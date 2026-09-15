import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Plus, RefreshCw } from 'lucide-react';
import {
  backfillCmsTagAliases,
  fetchAnalysisConfig,
  fetchAnalysisMetrics,
  fetchTaxonomyState,
  publishTaxonomyV1,
  updateAnalysisConfig,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { TAG_KIND_LABELS } from '../../utils/taxonomyLabels';
import FullAnalysisBackfillCard from './FullAnalysisBackfillCard';
import { Kpi, KpiState } from './Kpi';
import StaleNotice from './StaleNotice';
import TaxonomyLedger from './TaxonomyLedger';
import { formatStamp } from './adminUtils';

const FLAG_META = {
  article_analysis_enabled: ['文章分析', '关闭只停止创建与消费分析任务，不删已有结果'],
  taxonomy_candidate_enabled: ['候选证据', '关闭后不再记录公共内容里的未知概念'],
  taxonomy_auto_activation_enabled: ['候选自动激活', '开启前需目录 v1 已发布并结束引导期；开启时确认'],
  personal_digest_enabled: ['个人早报', '关闭只停止早报 API 与调度，不删已有版本'],
};
const pct = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

// 新闻价值分整数档分布(v3.48 收口):手写 grid 不引图表库;数字只在 title 里。
function ScoreHistogram({ histogram, windowDays, total }) {
  const buckets = Array.from({ length: 10 }, (_, i) => [String(i + 1), Number(histogram?.[String(i + 1)] || 0)]);
  const max = Math.max(1, ...buckets.map(([, n]) => n));
  return (
    <section className="surface-card card-pad rounded-[var(--r-card)]">
      <div className="card-head">
        <span className="card-title">新闻价值分布</span>
        <span className="tiny-meta ml-auto">近 {windowDays} 天 · {total.toLocaleString()} 篇</span>
      </div>
      {total === 0 ? (
        <p className="tiny-meta">窗口内还没有分析结果</p>
      ) : (
        <div className="score-hist" role="img" aria-label={buckets.map(([b, n]) => `${b} 分 ${n} 篇`).join('，')}>
          {buckets.map(([b, n]) => (
            <div key={b} className="score-hist-col" title={`${b} 分：${n} 篇（${Math.round((n / total) * 100)}%）`}>
              <span className="score-hist-bar" style={{ height: `${Math.max(2, Math.round((n / max) * 100))}%` }} />
              <span className="score-hist-lbl">{b}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function useTracked(fetcher) {
  const genRef = useRef(0);
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  const load = useCallback(async (...args) => {
    const gen = ++genRef.current;
    setState((prev) => ({ status: 'loading', data: prev.data, error: '' }));
    try {
      const data = await fetcher(...args);
      if (gen === genRef.current) setState({ status: 'ok', data, error: '' });
      return data;
    } catch (error) {
      if (gen === genRef.current) {
        setState((prev) => ({ status: error.status === 404 ? 'unavailable' : 'error', data: prev.data, error: error.message }));
      }
      return undefined;
    }
  }, [fetcher]);
  return [state, load, setState];
}

function StateCard({ label, state, onRetry }) {
  if (state.status === 'unavailable') {
    return <section className="surface-card card-pad rounded-[var(--r-card)]"><span className="stamp stamp-warn">未接入</span><span className="tiny-meta ml-2">当前后端版本没有{label}端点</span></section>;
  }
  if (state.status === 'error') {
    return <section className="surface-card card-pad rounded-[var(--r-card)]" role="alert"><span className="stamp stamp-bad">{label}读取失败</span><span className="tiny-meta ml-2">{state.error} · <button type="button" className="kpi-sub-link" onClick={onRetry}>重试</button></span></section>;
  }
  return <section className="surface-card card-pad rounded-[var(--r-card)]" aria-busy="true"><span className="stamp stamp-idle">加载中…</span></section>;
}

/**
 * 运维管理 → 「分析与标签」子页(issue #76 拍板①):两区——
 *   「分析链路」:发布开关板(4 枚 ledger-switch)→ 分析指标 KPI → 新闻价值分布 → 历史文章完整分析;
 *   「标签治理」:区头动作(同步别名 / 新建标签 / 刷新)→ 目录版本 KPI → 规范标签 ∪ 候选 统一总账 + 抽屉。
 * 早报两旋钮与兴趣目录 Top N 已迁至「内容」子页「早报与兴趣」区。四组数据各自
 * loading / error / data,只有 404 才显示「未接入」,其余失败可重试并保留上次快照(P1 #7)。
 */
export default function AdminTaxonomyPanel({ showToast, days = 7, refreshTick = 0 }) {
  const confirm = useConfirm();
  const [config, loadConfig, setConfig] = useTracked(fetchAnalysisConfig);
  const [metrics, loadMetrics] = useTracked(fetchAnalysisMetrics);
  const [taxonomyState, loadTaxonomyState, setTaxonomyState] = useTracked(fetchTaxonomyState);
  const [flagBusy, setFlagBusy] = useState('');
  const [govBusy, setGovBusy] = useState('');
  const [ledgerTick, setLedgerTick] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => { loadConfig(); loadTaxonomyState(); }, [loadConfig, loadTaxonomyState]);
  useEffect(() => { loadMetrics(days); }, [loadMetrics, days]);
  useEffect(() => {
    if (refreshTick > 0) { loadConfig(); loadMetrics(days); loadTaxonomyState(); setLedgerTick((t) => t + 1); }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应「切回 Tab / 切子页」刷新脉冲
  }, [refreshTick]);

  const flags = config.data?.feature_flags;
  const toggleFlag = async (key, enabled) => {
    if (key === 'taxonomy_auto_activation_enabled' && enabled
      && !(await confirm({ title: '开启候选自动激活', message: '开启前请确认目录 v1 已审核并结束引导期；满足组合阈值的低风险候选会自动成为规范标签。', confirmText: '开启', tone: 'primary' }))) return;
    setFlagBusy(key);
    try {
      const data = await updateAnalysisConfig({ [key]: enabled });
      setConfig({ status: 'ok', data, error: '' });
      window.dispatchEvent(new CustomEvent('dorami-analysis-config-changed', { detail: data.feature_flags || {} }));
      showToast(`已${enabled ? '开启' : '关闭'}${FLAG_META[key][0]}`, 'success');
    } catch (error) { showToast(error.message, 'error'); }
    finally { setFlagBusy(''); }
  };

  const governanceChanged = () => { loadTaxonomyState(); loadMetrics(days); };
  const publishV1 = async () => {
    if (!(await confirm({ title: '发布目录 v1', message: '发布会激活目录 v1，并创建最近 7 天文章的闭集重标任务。', confirmText: '发布', tone: 'primary' }))) return;
    setGovBusy('publish');
    try {
      const result = await publishTaxonomyV1('产品审核通过 taxonomy-bootstrap-v1');
      setTaxonomyState({ status: 'ok', data: result.state, error: '' });
      showToast(`已发布目录 v${result.taxonomy_version}`, 'success');
      setLedgerTick((t) => t + 1);
    } catch (error) { showToast(error.message, 'error'); }
    finally { setGovBusy(''); }
  };
  const syncAliases = async () => {
    setGovBusy('alias');
    try {
      const result = await backfillCmsTagAliases('管理台同步规范中英文解析入口');
      setTaxonomyState({ status: 'ok', data: result.state, error: '' });
      showToast(`已补齐 ${result.created} 个规范名别名`, 'success');
      setLedgerTick((t) => t + 1);
    } catch (error) { showToast(error.message, 'error'); }
    finally { setGovBusy(''); }
  };

  const analysis = metrics.data?.article_analysis;
  const taxonomyMetrics = metrics.data?.taxonomy;
  const pending = Number(analysis?.status_counts?.pending || 0);
  const running = Number(analysis?.status_counts?.running || 0);
  const histogramTotal = Object.values(analysis?.score_histogram || {}).reduce((acc, n) => acc + Number(n || 0), 0);
  const versionStale = Number(analysis?.version_stale || 0);
  const gov = taxonomyState.data;
  const activeVersion = gov?.versions?.find((item) => item.status === 'active');
  const publishable = gov && gov.publish_ready && !(gov.active_version > 0);
  const govLoading = taxonomyState.status === 'loading';

  return (
    <div>
      {/* ══ 分析链路 ══ */}
      <div className="zone-head zone-head-first">
        <span className="zone-title">分析链路</span>
        <span className="zone-hint">近 {days} 天</span>
        {config.data && <StaleNotice status={config.status} error={config.error} onRetry={loadConfig} label="分析配置" />}
        {metrics.data && <StaleNotice status={metrics.status} error={metrics.error} onRetry={() => loadMetrics(days)} label="指标" />}
        <span className="zone-acts">
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={() => { loadConfig(); loadMetrics(days); }} disabled={config.status === 'loading' || metrics.status === 'loading'}>
            {config.status === 'loading' || metrics.status === 'loading' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 刷新
          </button>
        </span>
      </div>
      <div className="grid gap-3">
        {flags ? (
          <section className="surface-card ai-switchboard is-wrap rounded-[var(--r-card)]" aria-label="分析发布开关">
            {Object.entries(FLAG_META).map(([key, [label, hint]], index) => {
              const enabled = Boolean(flags[key]);
              return (
                <span key={key} className="sw-group">
                  {index > 0 && <span className="ai-divider" />}
                  <span className="sw">
                    <span className={`ai-light ${enabled ? '' : 'is-off'}`} />
                    <span className="ai-switch-lbl">{label}</span>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={enabled}
                      aria-label={label}
                      title={hint}
                      disabled={flagBusy === key}
                      onClick={() => toggleFlag(key, !enabled)}
                      className={`ledger-switch ${enabled ? 'is-on' : ''}`}
                    />
                  </span>
                </span>
              );
            })}
          </section>
        ) : <StateCard label="分析配置" state={config} onRetry={loadConfig} />}

        <section className="surface-card kpi-strip" aria-label="分析指标">
          {metrics.data ? (
            <>
              <Kpi num={pct(analysis?.success_rate)} label="分析成功率" sub={`待处理 ${pending.toLocaleString()} · 运行中 ${running.toLocaleString()}`} />
              <Kpi num={analysis?.score_p50 ?? '—'} label="评分 P50" sub={`P90 ${analysis?.score_p90 ?? '—'}`} />
              <Kpi num={pct(analysis?.score_threshold_rates?.['7.0'])} label="7+ 占比" sub={`9+ ${pct(analysis?.score_threshold_rates?.['9.0'])}`} />
              <Kpi num={pct(taxonomyMetrics?.tagged_article_rate)} label="标签覆盖" sub={`缺主标签 ${pct(taxonomyMetrics?.primary_missing_rate)}`} />
              <Kpi num={versionStale.toLocaleString()} label="旧尺子待重跑" sub="每轮 16 篇慢滴" tone={versionStale > 0 ? 'is-warn' : undefined} />
            </>
          ) : (
            <KpiState label="分析指标" error={metrics.status === 'error' ? metrics.error : metrics.status === 'unavailable' ? '当前后端版本没有指标端点' : ''} onRetry={() => loadMetrics(days)} />
          )}
        </section>

        {analysis?.score_histogram && (
          <ScoreHistogram histogram={analysis.score_histogram} windowDays={metrics.data.window_days} total={histogramTotal} />
        )}

        <FullAnalysisBackfillCard showToast={showToast} refreshTick={refreshTick} />
      </div>

      {/* ══ 标签治理 ══ */}
      <div className="zone-head">
        <span className="zone-title">标签治理</span>
        {gov && <StaleNotice status={taxonomyState.status} error={taxonomyState.error} onRetry={loadTaxonomyState} label="目录版本" />}
        {gov?.publish_blockers?.length > 0 && !(gov.active_version > 0) && (
          <span className="stamp stamp-warn" title={gov.publish_blockers.join('；')}>发布前 {gov.publish_blockers.length} 项待处理</span>
        )}
        <span className="zone-acts">
          {gov?.canonical_alias_gap_count > 0 && (
            <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" disabled={Boolean(govBusy)} onClick={syncAliases} title="补齐规范中英文名的别名解析入口">
              {govBusy === 'alias' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}同步别名 <span className="acct-mono">{gov.canonical_alias_gap_count}</span>
            </button>
          )}
          {publishable && (
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={Boolean(govBusy)} onClick={publishV1}>
              {govBusy === 'publish' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}发布目录 v1
            </button>
          )}
          <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> 新建标签
          </button>
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={() => { loadTaxonomyState(); setLedgerTick((t) => t + 1); }} disabled={govLoading}>
            {govLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 刷新
          </button>
        </span>
      </div>
      <div className="grid gap-3">
        <section className="surface-card kpi-strip" aria-label="目录版本">
          {gov ? (
            <>
              <Kpi
                num={gov.active_version > 0 ? `v${gov.active_version}` : '—'}
                label="目录版本"
                sub={gov.active_version > 0
                  ? <><span className="stamp stamp-ok kpi-stamp">已发布</span>{activeVersion?.activated_at ? ` ${formatStamp(activeVersion.activated_at)}` : ''}</>
                  : <span className={`stamp ${gov.publish_ready ? 'stamp-ok' : 'stamp-warn'} kpi-stamp`}>{gov.publish_ready ? '可发布' : '待治理'}</span>}
              />
              <Kpi
                num={Number(gov.tag_count ?? 0).toLocaleString()}
                label="规范标签"
                sub={Object.entries(TAG_KIND_LABELS).map(([key, label]) => `${label} ${gov.active_tags_by_kind?.[key] ?? 0}`).join(' · ')}
              />
              <Kpi num={pct(gov.coverage_7d?.coverage_rate)} label="近 7 天覆盖" sub={`缺主标签 ${pct(gov.coverage_7d?.primary_missing_rate)}`} />
              <Kpi
                num={Number(gov.unresolved_candidate_count ?? 0).toLocaleString()}
                label="未归并候选"
                tone={Number(gov.unresolved_candidate_count) > 0 ? 'is-warn' : undefined}
                sub={`候选记录 ${Number(gov.candidate_count ?? 0).toLocaleString()}`}
              />
              <Kpi num={Number(taxonomyMetrics?.alias_count ?? 0).toLocaleString()} label="别名" sub={`自动激活 ${Number(taxonomyMetrics?.active_automatic_count ?? 0).toLocaleString()}`} />
            </>
          ) : (
            <KpiState label="目录版本" error={taxonomyState.status === 'error' ? taxonomyState.error : taxonomyState.status === 'unavailable' ? '当前后端版本没有目录状态端点' : ''} onRetry={loadTaxonomyState} />
          )}
        </section>

        <TaxonomyLedger
          showToast={showToast}
          refreshTick={ledgerTick}
          onChanged={governanceChanged}
          createOpen={createOpen}
          onCreateClose={() => setCreateOpen(false)}
        />
      </div>
    </div>
  );
}
