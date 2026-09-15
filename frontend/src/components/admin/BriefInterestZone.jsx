import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';

import {
  fetchAnalysisConfig,
  fetchInterestCatalogPolicy,
  updateAnalysisConfig,
  updateInterestCatalogPolicy,
} from '../../api';
import { TAG_KIND_LABELS } from '../../utils/taxonomyLabels';

// 表单卡(拍板③):零说明句、字段名即语义、一枚 secondary 保存;数值取自后端,保存后回填。
function KnobCard({ title, stamp, fields, form, onChange, meta, onSave, busy, dirty, disabled }) {
  return (
    <section className="surface-card card-pad rounded-[var(--r-card)]">
      <div className="card-head">
        <span className="card-title">{title}</span>
        {stamp && <span className={`stamp stamp-${stamp.tone} ml-auto`}>{stamp.label}</span>}
      </div>
      <div className="knob-grid" style={fields.length === 3 ? { gridTemplateColumns: 'repeat(3, 1fr)' } : undefined}>
        {fields.map((field) => (
          <label key={field.key} className="knob-field">
            <span>{field.label}</span>
            <input
              className="form-input"
              type="number"
              min={field.min}
              max={field.max}
              step={field.step}
              value={form[field.key] ?? ''}
              onChange={(e) => onChange(field.key, e.target.value)}
              disabled={disabled}
              aria-label={`${title} · ${field.label}`}
            />
          </label>
        ))}
      </div>
      <div className="card-foot">
        {meta && <span className="tiny-meta">{meta}</span>}
        <button
          type="button"
          className="action-button action-button-secondary min-h-[32px] px-3 text-xs ml-auto"
          onClick={onSave}
          disabled={busy || disabled || !dirty}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}保存
        </button>
      </div>
    </section>
  );
}

function ZoneState({ status, error, onRetry }) {
  if (status === 'error') {
    return (
      <section className="surface-card card-pad rounded-[var(--r-card)]" role="alert">
        <span className="stamp stamp-bad">配置读取失败</span>
        <span className="tiny-meta ml-2">{error} · <button type="button" className="kpi-sub-link" onClick={onRetry}>重试</button></span>
      </section>
    );
  }
  if (status === 'unavailable') {
    return (
      <section className="surface-card card-pad rounded-[var(--r-card)]">
        <span className="stamp stamp-warn">未接入</span>
        <span className="tiny-meta ml-2">当前后端版本没有早报策略端点</span>
      </section>
    );
  }
  return (
    <section className="surface-card card-pad rounded-[var(--r-card)]" aria-busy="true">
      <span className="stamp stamp-idle">加载中…</span>
    </section>
  );
}

const num = (value) => Number(value);

/**
 * 运维管理 → 内容 → 「早报与兴趣」区(issue #76 拍板①③):个人早报的两枚读者分发策略旋钮
 * (重大事件通道 / 订阅外兴趣)+ 兴趣目录 Top N,自「标签」子页迁入——它们是 personal_digest_*
 * 的分发策略,不是分析 worker 或目录的生产状态。三张表单卡各一枚 secondary 保存。
 * 分析配置与目录策略两组数据各自 loading / error / data:404 才显示「未接入」,其它失败可重试并
 * 保留上次快照(归并稿 P1 #7)。
 */
export default function BriefInterestZone({ showToast, refreshTick = 0 }) {
  const genRef = useRef({ config: 0, policy: 0 });
  const [config, setConfig] = useState({ status: 'loading', data: null, error: '' });
  const [policy, setPolicy] = useState({ status: 'loading', data: null, error: '' });
  const [breakingForm, setBreakingForm] = useState({ min_score: '', max_items: '' });
  const [unionForm, setUnionForm] = useState({ external_min_score: '', external_per_source_max: '' });
  const [limitsForm, setLimitsForm] = useState({ topic: '', industry: '', entity: '' });
  const [busy, setBusy] = useState('');

  const applyConfig = useCallback((data) => {
    const breaking = data?.personal_digest_breaking || {};
    const selection = data?.personal_digest_selection || {};
    setBreakingForm({ min_score: String(breaking.min_score ?? ''), max_items: String(breaking.max_items ?? '') });
    setUnionForm({ external_min_score: String(selection.external_min_score ?? ''), external_per_source_max: String(selection.external_per_source_max ?? '') });
  }, []);
  const applyPolicy = useCallback((data) => {
    const limits = data?.policy?.limits || {};
    setLimitsForm({ topic: String(limits.topic ?? ''), industry: String(limits.industry ?? ''), entity: String(limits.entity ?? '') });
  }, []);

  const loadConfig = useCallback(async () => {
    const gen = ++genRef.current.config;
    setConfig((prev) => ({ status: 'loading', data: prev.data, error: '' }));
    try {
      const data = await fetchAnalysisConfig();
      if (gen !== genRef.current.config) return;
      setConfig({ status: 'ok', data, error: '' });
      applyConfig(data);
    } catch (error) {
      if (gen !== genRef.current.config) return;
      setConfig((prev) => ({ status: error.status === 404 ? 'unavailable' : 'error', data: prev.data, error: error.message }));
    }
  }, [applyConfig]);
  const loadPolicy = useCallback(async () => {
    const gen = ++genRef.current.policy;
    setPolicy((prev) => ({ status: 'loading', data: prev.data, error: '' }));
    try {
      const data = await fetchInterestCatalogPolicy();
      if (gen !== genRef.current.policy) return;
      setPolicy({ status: 'ok', data, error: '' });
      applyPolicy(data);
    } catch (error) {
      if (gen !== genRef.current.policy) return;
      setPolicy((prev) => ({ status: error.status === 404 ? 'unavailable' : 'error', data: prev.data, error: error.message }));
    }
  }, [applyPolicy]);

  useEffect(() => { loadConfig(); loadPolicy(); }, [loadConfig, loadPolicy]);
  useEffect(() => {
    if (refreshTick > 0) { loadConfig(); loadPolicy(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应刷新脉冲
  }, [refreshTick]);

  const breaking = config.data?.personal_digest_breaking;
  const selection = config.data?.personal_digest_selection;
  const facetStats = policy.data?.facet_stats || {};
  const limits = policy.data?.policy?.limits || {};

  const saveConfig = async (key, payload, done) => {
    setBusy(key);
    try {
      const data = await updateAnalysisConfig(payload);
      setConfig({ status: 'ok', data, error: '' });
      applyConfig(data);
      showToast(done, 'success');
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setBusy(''); }
  };
  const savePolicy = async () => {
    setBusy('policy');
    try {
      const data = await updateInterestCatalogPolicy({
        topic: num(limitsForm.topic), industry: num(limitsForm.industry), entity: num(limitsForm.entity),
        reason: '管理台调整兴趣目录 Top N',
      });
      setPolicy({ status: 'ok', data, error: '' });
      applyPolicy(data);
      showToast('已更新兴趣目录 Top N', 'success');
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setBusy(''); }
  };

  const breakingDirty = Boolean(breaking) && (num(breakingForm.min_score) !== num(breaking.min_score) || num(breakingForm.max_items) !== num(breaking.max_items));
  const unionDirty = Boolean(selection) && (num(unionForm.external_min_score) !== num(selection.external_min_score) || num(unionForm.external_per_source_max) !== num(selection.external_per_source_max));
  const limitsDirty = Boolean(policy.data) && ['topic', 'industry', 'entity'].some((k) => num(limitsForm[k]) !== num(limits[k]));
  const loading = config.status === 'loading' || policy.status === 'loading';

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">早报与兴趣</span>
        <span className="zone-acts">
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={() => { loadConfig(); loadPolicy(); }} disabled={loading}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 刷新
          </button>
        </span>
      </div>
      <div className="knob-cards">
        {config.data ? (
          <>
            <KnobCard
              title="重大事件通道"
              stamp={num(breaking?.max_items) > 0 ? { label: '开启', tone: 'ok' } : { label: '关闭', tone: 'idle' }}
              fields={[
                { key: 'min_score', label: '新闻价值分阈值', min: 0, max: 10, step: 0.1 },
                { key: 'max_items', label: '每期条数上限', min: 0, max: breaking?.max_items_limit ?? 5, step: 1 },
              ]}
              form={breakingForm}
              onChange={(k, v) => setBreakingForm((f) => ({ ...f, [k]: v }))}
              meta="0 = 关闭"
              onSave={() => saveConfig('breaking', { personal_digest_breaking_min_score: num(breakingForm.min_score), personal_digest_breaking_max_items: num(breakingForm.max_items) }, '已更新重大事件通道')}
              busy={busy === 'breaking'}
              dirty={breakingDirty}
            />
            <KnobCard
              title="订阅外兴趣"
              fields={[
                { key: 'external_min_score', label: '订阅外门槛', min: 0, max: 10, step: 0.1 },
                { key: 'external_per_source_max', label: '每源每期上限', min: 1, max: selection?.external_per_source_max_limit ?? 5, step: 1 },
              ]}
              form={unionForm}
              onChange={(k, v) => setUnionForm((f) => ({ ...f, [k]: v }))}
              meta={selection ? `订阅内门槛 ${Number(selection.min_score).toFixed(1)}` : ''}
              onSave={() => saveConfig('union', { personal_digest_external_min_score: num(unionForm.external_min_score), personal_digest_external_per_source_max: num(unionForm.external_per_source_max) }, '已更新订阅外兴趣')}
              busy={busy === 'union'}
              dirty={unionDirty}
            />
          </>
        ) : (
          <div className="knob-span-2"><ZoneState status={config.status} error={config.error} onRetry={loadConfig} /></div>
        )}
        {policy.data ? (
          <KnobCard
            title="兴趣目录 Top N"
            fields={['topic', 'industry', 'entity'].map((key) => ({
              key,
              label: `${TAG_KIND_LABELS[key]} / ${facetStats[key]?.eligible_count ?? '—'}`,
              min: 0, max: 200, step: 1,
            }))}
            form={limitsForm}
            onChange={(k, v) => setLimitsForm((f) => ({ ...f, [k]: v }))}
            onSave={savePolicy}
            busy={busy === 'policy'}
            dirty={limitsDirty}
          />
        ) : (
          <ZoneState status={policy.status} error={policy.error} onRetry={loadPolicy} />
        )}
      </div>
    </>
  );
}
