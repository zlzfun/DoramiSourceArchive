/**
 * 运维 · 读者功能页(issue #114,自 AdminOpsTab 的 AI 档与内容档的读者策略迁出,逻辑原样):
 * 每日用量(观测)→ 开关与默认(用户 AI 总闸 / 新账号默认开 / 模型 / 日预算 · 公开分享 · 读者点播 · 新账号默认订阅)→ 早报与兴趣。
 * 「读者能用什么、默认是什么、用了多少」归这一页(方案 §2.1 归属规则)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import {
  getAiBetaGlobal,
  setAiBetaGlobal,
  setAiBetaNewUserDefault,
  setAiDailyTokenBudget,
  fetchPublicShareGlobal,
  updatePublicShareGlobal,
  fetchReaderOndemandGlobal,
  updateReaderOndemandGlobal,
  fetchReaderDefaults,
  updateReaderDefaults,
  fetchAiUsage,
  getLLMConfig,
} from '../../../api';
import { MultiSeriesBars } from '../../charts/DashboardCharts';
import BriefInterestZone from '../BriefInterestZone';
import { Kpi } from '../Kpi';
import { pivotDaily } from '../../charts/chartUtils';
import { PURPOSE_LABELS, fmtNum } from '../adminUtils';
import { opsBlockId } from '../../../utils/opsNav';

// AI 用量「按用户」系列名:哨兵键('other:' / 'guest:')→ 显示名;恰好同名的真实账号加「（用户）」
const SERIES_SENTINELS = { 'other:': '其它', 'guest:': '游客' };
const seriesUserLabel = (name) => SERIES_SENTINELS[name]
  || (Object.values(SERIES_SENTINELS).includes(name) ? `${name}（用户）` : name);

export default function ReaderPage({ showToast, days, refreshTick = 0, rootAdmin = false, onOpenCredentials }) {
  const [globalAi, setGlobalAi] = useState(null);
  const [aiBudget, setAiBudget] = useState(null);  // {daily_token_budget, tokens_used_today}(读者面 AI 日预算,0=不限)
  const [newUserAiDefault, setNewUserAiDefault] = useState(null); // 新账号 AI 默认值(只影响此后新建账户)
  const [budgetDraft, setBudgetDraft] = useState(''); // 预算输入框草稿(失焦/回车提交)
  const [publicShare, setPublicShare] = useState(null);   // 公开分享总闸 + 存活链接盘点
  const [ondemand, setOndemand] = useState(null);         // 读者点播总闸(issue #137):{enabled, podcast_available, article_available, blockers}
  const [readerDefaults, setReaderDefaults] = useState(null); // 新账号默认订阅名单(issue #56):{source_ids, overridden, sources, code_default, candidates}
  const [defaultsPick, setDefaultsPick] = useState('');
  const [defaultsBusy, setDefaultsBusy] = useState(false); // 整集写请求在途:卡内全部控件禁用(快速增删会互相覆盖——codex 检视 P2)
  const defaultsGenRef = useRef(0);                         // 迟到响应不覆盖新快照
  const [llmStatus, setLlmStatus] = useState(null);
  // ── AI 用量看板 ──
  const [usage, setUsage] = useState(null);
  // 每组 loader 的请求代次守卫(v3.43.1 codex 交叉检视):M15 加了多个刷新时机后,
  // 同组请求可能并发在途——旧响应后到会覆盖新快照。发起时领代次,异步返回后校验
  // 自己仍是该组最新才落 state/弹 Toast(不真正 abort,只丢弃旧响应);keyed map
  // 让不同 loader 互不作废,实例级 useRef 不跨实例共享。
  const loadGen = useRef({});
  const claimGen = useCallback((key) => {
    const gen = (loadGen.current[key] || 0) + 1;
    loadGen.current[key] = gen;
    return () => loadGen.current[key] === gen;
  }, []);

  const loadLlm = useCallback(() => {
    const fresh = claimGen('llm');
    return getLLMConfig().then((v) => { if (fresh()) setLlmStatus(v); }).catch(() => {});
  }, [claimGen]);

  const loadUsage = useCallback((d) => {
    const fresh = claimGen('usage');
    return fetchAiUsage(d).then((v) => { if (fresh()) setUsage(v); }).catch(() => {});
  }, [claimGen]);
  const loadGlobals = useCallback(async () => {
    const fresh = claimGen('globals');
    try {
      const g = await getAiBetaGlobal();
      if (fresh()) {
        setGlobalAi(g.enabled);
        setAiBudget({ daily_token_budget: g.daily_token_budget ?? 0, tokens_used_today: g.tokens_used_today ?? 0 });
        setBudgetDraft(String(g.daily_token_budget ?? 0));
        setNewUserAiDefault(g.new_user_default ?? true);
      }
    } catch (error) {
      if (fresh()) showToast(error.message || '加载运维数据失败', 'error');
    }
    // 分享总闸单独 try:它读不到不该连带把 AI 总闸的错误提示也吞掉/重复弹。
    try {
      const share = await fetchPublicShareGlobal();
      if (fresh()) setPublicShare(share);
    } catch { /* 读不到就保持 null,卡片显示「正在读取…」且开关禁用 */ }
    try {
      const defaults = await fetchReaderDefaults();
      if (fresh()) setReaderDefaults(defaults);
    } catch { /* 同上:名单卡显示读取中 */ }
    try {
      const ondemandGlobal = await fetchReaderOndemandGlobal();
      if (fresh()) setOndemand(ondemandGlobal);
    } catch { /* 同上:点播卡显示读取中且开关禁用 */ }
  }, [claimGen, showToast]);

  // 新账号默认订阅名单(issue #56):增删即写,null = 恢复代码缺省;只影响此后新建账号
  const saveReaderDefaults = async (sourceIds, okMsg) => {
    if (defaultsBusy) return; // 一次只允许一项操作:整集替换语义下并发写会互相带回旧集合
    const gen = ++defaultsGenRef.current;
    setDefaultsBusy(true);
    try {
      const res = await updateReaderDefaults(sourceIds);
      if (gen !== defaultsGenRef.current) return;
      setReaderDefaults(res);
      showToast(okMsg, 'success');
    } catch (error) {
      if (gen === defaultsGenRef.current) showToast(error.message || '更新默认订阅名单失败', 'error');
    } finally {
      if (gen === defaultsGenRef.current) { setDefaultsBusy(false); setDefaultsPick(''); } // 失败也复位 select,重选同项才会触发 change
    }
  };
  const handleDefaultsRemove = (sourceId) => saveReaderDefaults(
    (readerDefaults?.source_ids || []).filter((id) => id !== sourceId), '已从默认订阅名单移除',
  );
  const handleDefaultsAdd = (sourceId) => {
    if (!sourceId || (readerDefaults?.source_ids || []).includes(sourceId)) return;
    saveReaderDefaults([...(readerDefaults?.source_ids || []), sourceId], '已加入默认订阅名单');
  };
  const handleDefaultsReset = () => saveReaderDefaults(null, '已恢复代码缺省名单');

  const handleToggleOndemand = async () => {
    const next = !ondemand?.enabled;
    try {
      const res = await updateReaderOndemandGlobal(next);
      setOndemand(res);
      showToast(res.enabled ? '已开启读者点播' : '已关闭读者点播', 'success');
    } catch (error) {
      showToast(error.message || '更新点播总闸失败', 'error');
    }
  };

  const handleTogglePublicShare = async () => {
    const next = !publicShare?.enabled;
    try {
      const res = await updatePublicShareGlobal(next);
      setPublicShare((prev) => ({ ...(prev || { live_count: 0, total_count: 0 }), ...res }));
      showToast(
        res.enabled ? '已开启公开分享链接' : '已关闭公开分享链接（已发出的链接立即失效）',
        'success',
      );
    } catch (error) {
      showToast(error.message || '更新分享总闸失败', 'error');
    }
  };

  useEffect(() => { loadGlobals(); loadLlm(); }, [loadGlobals, loadLlm, refreshTick]);
  useEffect(() => { loadUsage(days); }, [loadUsage, days, refreshTick]);

  const handleToggleGlobalAi = async () => {
    const next = !globalAi;
    try {
      const res = await setAiBetaGlobal(next);
      setGlobalAi(res.enabled);
      showToast(res.enabled ? '已开启用户 AI 功能' : '已关闭用户 AI 功能（全员暂停）', 'success');
    } catch (error) {
      showToast(error.message || '更新 AI 全局开关失败', 'error');
    }
  };
  // 新账号 AI 默认值:只改「创建时刻」的播种初值,存量账户与总闸互不牵动。
  const handleToggleNewUserDefault = async () => {
    const next = !newUserAiDefault;
    try {
      const res = await setAiBetaNewUserDefault(next);
      setNewUserAiDefault(res.new_user_default ?? next);
      showToast(next ? '此后新建的账号将默认开启 AI 功能' : '此后新建的账号将默认关闭 AI 功能', 'success');
    } catch (error) {
      showToast(error.message || '更新新账号 AI 默认值失败', 'error');
    }
  };

  // 日预算提交(失焦/回车):空或非法回落当前值;0 = 不限。
  const handleCommitBudget = async () => {
    const parsed = Math.max(0, Math.floor(Number(budgetDraft)));
    if (!Number.isFinite(parsed) || String(parsed) === String(aiBudget?.daily_token_budget ?? 0)) {
      setBudgetDraft(String(aiBudget?.daily_token_budget ?? 0));
      return;
    }
    try {
      const res = await setAiDailyTokenBudget(parsed);
      setAiBudget({ daily_token_budget: res.daily_token_budget ?? parsed, tokens_used_today: res.tokens_used_today ?? 0 });
      setBudgetDraft(String(res.daily_token_budget ?? parsed));
      showToast(parsed ? `已设置日预算 ${parsed.toLocaleString()} tokens` : '已取消日预算限制', 'success');
    } catch (error) {
      showToast(error.message || '更新 AI 日预算失败', 'error');
      setBudgetDraft(String(aiBudget?.daily_token_budget ?? 0));
    }
  };
  // ── AI 用量图表数据：每日图按用途/用户拆多系列（透视 + 零填充）──
  const dayPurpose = useMemo(
    () => (usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [usage],
  );
  // 后端聚合桶用带冒号 sentinel('other:',用户名禁冒号故不可撞),渲染层映射回
  // 「其它」;真实用户名恰为「其它」时改显「其它（用户）」——两者永不合并,
  // colorForEntity 的中性槽判定只认映射后的「其它」。游客(issue #213)同理:全体访客
  // 合成一条 'guest:' 系列,显示「游客」,真有叫「游客」的账号改显「游客（用户）」。
  const dayUser = useMemo(() => (usage?.by_day_user ?? []).map((r) => ({
    ...r,
    username: seriesUserLabel(r.username),
  })), [usage]);
  // 用户维度已由后端裁到 Top-6+「其它」共 ≤7 系,前端 pivot 不再二次裁剪
  // (topN=Infinity;否则按 calls 重排可能再挤掉一个真实用户并回「其它」,
  // 甚至产出重复的「其它」key——codex 交叉检视终审指出)。用途维度枚举有界,
  // 保持默认裁剪。
  const callsDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, days, 'purpose', 'calls'),
    user: pivotDaily(dayUser, days, 'username', 'calls', Infinity),
  }), [dayPurpose, dayUser, days]);
  const tokensDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, days, 'purpose', 'total_tokens'),
    user: pivotDaily(dayUser, days, 'username', 'total_tokens', Infinity),
  }), [dayPurpose, dayUser, days]);
  // AI 用量「按用户」维度是逐用户明细:非根管理员端点已剥空,前端同步收起该档。
  const aiDims = useMemo(() => (rootAdmin ? [['purpose', '按用途'], ['user', '按用户']] : [['purpose', '按用途']]), [rootAdmin]);

  return (
    <div className="ops-page-body">
      <section id={opsBlockId('reader', 'usage')} className="ops-block" tabIndex={-1} aria-label="每日用量">
        <div className="zone-head zone-head-first"><span className="zone-title">每日用量</span><span className="zone-hint">近 {days} 天</span></div>
        {!usage || usage.totals.calls === 0 ? (
          <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
            近 {days} 天还没有 AI 调用记录，触发一次翻译 / 问答或日报生成后这里会出现统计。
          </p>
        ) : (
          <>
            <section className="surface-card kpi-strip" aria-label="AI 用量概览">
              <Kpi num={fmtNum(usage.totals.calls)} label="总调用" sub={`近 ${days} 天`} />
              <Kpi num={fmtNum(usage.totals.prompt_tokens)} label="输入 tokens" sub={`日均 ${fmtNum(Math.round(usage.totals.prompt_tokens / Math.max(1, days)))}`} />
              <Kpi num={fmtNum(usage.totals.completion_tokens)} label="输出 tokens" sub={`日均 ${fmtNum(Math.round(usage.totals.completion_tokens / Math.max(1, days)))}`} />
            </section>
            <div className="admin-grid">
              <MultiSeriesBars title="每日调用次数" datasets={callsDatasets} namespace="ai-calls" dims={aiDims} />
              <MultiSeriesBars title="每日 tokens" datasets={tokensDatasets} namespace="ai-tokens" dims={aiDims} />
            </div>
          </>
        )}
      </section>

      <section id={opsBlockId('reader', 'switches')} className="ops-block" tabIndex={-1} aria-label="开关与默认">
        <div className="zone-head"><span className="zone-title">开关与默认</span></div>
          <section className="surface-card ai-switchboard rounded-[var(--r-card)] mb-4">
            <span className={`ai-light ${globalAi ? '' : 'is-off'}`} />
            <div className="ai-switch-lbl" title="总闸:关闭立即暂停全员翻译 / 问答,不影响单账户开关记忆">用户 AI 功能</div>
            <button
              type="button"
              role="switch"
              aria-checked={!!globalAi}
              aria-label="用户 AI 功能总闸"
              disabled={globalAi === null}
              onClick={handleToggleGlobalAi}
              className={`ledger-switch ${globalAi ? 'is-on' : ''}`}
            />
            <span className="ai-divider" />
            {/* 新账号 AI 默认值:只播种「创建时刻」的逐账户开关初值,存量账户不动 */}
            <div className="ai-switch-lbl" title="此后新建的账号,逐账户 AI 开关按此初值播种;不影响已有账户,也不影响总闸">新账号默认开</div>
            <button
              type="button"
              role="switch"
              aria-checked={!!newUserAiDefault}
              aria-label="新账号 AI 功能默认开启"
              disabled={newUserAiDefault === null}
              onClick={handleToggleNewUserDefault}
              className={`ledger-switch ${newUserAiDefault ? 'is-on' : ''}`}
            />
            <span className="ai-divider" />
            {/* 模型编辑已收敛到 设置 → 凭据(v3.27);此处只留状态 chip 回指 */}
            <button
              type="button"
              className="model-chip"
              title="前往设置编辑模型凭据"
              onClick={() => onOpenCredentials?.()}
            >
              <i className={llmStatus?.configured ? '' : 'is-off'} />模型{' '}
              <b>{llmStatus?.configured ? (llmStatus.model || '已配置') : '未配置'}</b>
            </button>
            <span className="ai-divider" />
            {/* 读者面 AI 全站日 token 预算(v3.34):0=不限;超限当日全员 429,次日自复。
                与逐用户日调用限额互补——护总成本(多账户/IM bot 代答渠道的放大器)。 */}
            <div className="ai-switch-lbl" title="读者面 AI(翻译/问答/速读)全站每日 token 预算;0 = 不限。超限当日全员暂停,次日自动恢复;失焦或回车保存">日预算</div>
            <input
              className="form-input font-mono"
              style={{ width: 120, minHeight: 32 }}
              type="number"
              min="0"
              step="100000"
              value={budgetDraft}
              disabled={aiBudget === null}
              onChange={(e) => setBudgetDraft(e.target.value)}
              onBlur={handleCommitBudget}
              onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
              aria-label="读者面 AI 每日 token 预算(0 为不限)"
            />
            {aiBudget !== null && (
              <span className="tiny-meta" title="今日 翻译/问答/速读 全账户合计消耗">
                今日已用 {fmtNum(aiBudget.tokens_used_today)}
                {aiBudget.daily_token_budget ? ` / ${fmtNum(aiBudget.daily_token_budget)}` : ' · 不限'}
              </span>
            )}
          </section>
          <section className="surface-card ai-switchboard rounded-[var(--r-card)] mb-4">
            <span className={`ai-light ${publicShare?.enabled ? '' : 'is-off'}`} />
            <div className="ai-switch-lbl" title="总闸:关闭后已发出的公开链接立即失效,签发记录保留,重新开启即回归">
              公开分享链接
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={!!publicShare?.enabled}
              aria-label="公开分享链接总闸"
              disabled={publicShare === null}
              onClick={handleTogglePublicShare}
              className={`ledger-switch ${publicShare?.enabled ? 'is-on' : ''}`}
            />
            <span className="ai-divider" />
            <span className="tiny-meta">
              {publicShare === null
                ? '正在读取…'
                : `当前有效 ${publicShare.live_count} 条 · 累计签发 ${publicShare.total_count} 条`}
              {' · '}读者可为单篇内容生成免登录只读链接，可设有效期并随时撤销
            </span>
          </section>
          <section className="surface-card ai-switchboard is-wrap rounded-[var(--r-card)] mb-4">
            <span className={`ai-light ${ondemand?.enabled ? '' : 'is-off'}`} />
            <div className="ai-switch-lbl" title="总闸:关闭后读者面不再出现点播入口,端点一并谢绝;已生成的音频与排队中的任务不动,重开即回归。管理面的强制生成不受影响">
              读者点播
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={!!ondemand?.enabled}
              aria-label="读者点播总闸"
              disabled={ondemand === null}
              onClick={handleToggleOndemand}
              className={`ledger-switch ${ondemand?.enabled ? 'is-on' : ''}`}
            />
            <span className="ai-divider" />
            <span className="tiny-meta">
              {ondemand === null
                ? '正在读取…'
                : !ondemand.enabled
                  ? '已关闭：读者不再看到点播入口，已生成的音频照常播放'
                  : (ondemand.blockers || []).length === 0
                    ? '读者可按需生成播客精品导读与文章精简旁白（两者共用每日额度池）'
                    : `${ondemand.article_available ? '仅文章精简旁白可点播' : '本部署当前跑不了点播'}，入口不对读者露出 · 缺：${ondemand.blockers.join('；')}`}
            </span>
          </section>
      </section>

      <section id={opsBlockId('reader', 'defaults')} className="ops-block" tabIndex={-1} aria-label="新账号默认订阅">
          <section className="surface-card ai-switchboard rdef-card rounded-[var(--r-card)] mb-4" aria-label="新账号默认订阅">
            <div className="ai-switch-lbl" title="新读者账号登录时自动订阅的来源;只影响此后新建账号,存量账号不回填、退订过的不复活">
              新账号默认订阅
            </div>
            <span className="ai-divider" />
            {readerDefaults === null ? (
              <span className="tiny-meta">正在读取…</span>
            ) : (
              <div className="rdef-body">
                <div className="rdef-chips">
                  {readerDefaults.sources.map((src) => (
                    <span key={src.source_id} className={`interest-chip ${src.unknown ? 'is-bad' : ''}`} title={src.unknown ? `${src.source_id} · 系统不认识的来源` : src.hidden ? `${src.source_id} · 已在读者面隐藏` : src.source_id}>
                      <span className="interest-chip-dot" aria-hidden="true" />
                      <span className="interest-chip-name">{src.name}{src.hidden ? '（已隐藏）' : ''}</span>
                      <button type="button" className="interest-chip-x" aria-label={`移出 ${src.name}`} title="移出" disabled={defaultsBusy} onClick={() => handleDefaultsRemove(src.source_id)}>×</button>
                    </span>
                  ))}
                  {readerDefaults.sources.length === 0 && <span className="tiny-meta">名单为空：新账号不播种任何订阅</span>}
                </div>
                <div className="rdef-acts">
                  <select
                    className="rdef-select"
                    value={defaultsPick}
                    aria-label="加入默认订阅"
                    disabled={defaultsBusy}
                    onChange={(e) => { setDefaultsPick(e.target.value); handleDefaultsAdd(e.target.value); }}
                  >
                    <option value="">加入来源…</option>
                    {/* 候选与 POST 合法域同源(后端 candidates),不再借用读者目录(codex 检视 P3) */}
                    {(readerDefaults.candidates || [])
                      .filter((src) => !readerDefaults.source_ids.includes(src.source_id))
                      .map((src) => <option key={src.source_id} value={src.source_id}>{src.name}</option>)}
                  </select>
                  {readerDefaults.overridden && (
                    <button type="button" className="action-button action-button-secondary min-h-[28px] px-2.5 text-xs" disabled={defaultsBusy} onClick={handleDefaultsReset} title={`代码缺省:${readerDefaults.code_default.join(', ')}`}>恢复缺省</button>
                  )}
                  {defaultsBusy && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" aria-label="保存中" />}
                </div>
              </div>
            )}
          </section>
      </section>

      <section id={opsBlockId('reader', 'brief')} className="ops-block" tabIndex={-1} aria-label="早报与兴趣">
        <BriefInterestZone showToast={showToast} refreshTick={refreshTick} />
      </section>
    </div>
  );
}
