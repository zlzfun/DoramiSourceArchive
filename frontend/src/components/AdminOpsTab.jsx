import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Users,
  Database,
  Brain,
  UserPlus,
  KeyRound,
  Trash2,
  Power,
  Ban,
  Zap,
  Search,
  X,
  Loader2,
  Check,
} from 'lucide-react';
import {
  fetchAdminAccounts,
  fetchAccountActivity,
  fetchAdminContent,
  fetchMediaStats,
  getAiBetaGlobal,
  setAiBetaGlobal,
  fetchAiUsage,
  getLLMConfig,
  saveLLMConfig,
  testLLMConfig,
  createAccount,
  updateAccount,
  resetAccountPassword,
  deleteAccount,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import { MultiSeriesArea, RankBars, BarList } from './charts/DashboardCharts';
import MediaHeatmap from './admin/MediaHeatmap';
import { pivotDaily, C_READ, C_FAVORITE, C_OTHER } from './charts/chartUtils';
import { PURPOSE_LABELS, formatStamp, fmtNum, pct, truncLabel, vectorizedRateClass } from './admin/adminUtils';

// 账户列表分页大小：超过即翻页，避免成百上千账户一次性平铺。
const ACCOUNTS_PAGE_SIZE = 15;

// 媒体库占用空间可读化（去重后落盘字节）。
function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

// KPI 总账条单格（被动读数，数字全 ink；tone 只给需要语义色的异常指标）。
function Kpi({ num, label, sub, tone }) {
  return (
    <div className="kpi">
      <span className={`kpi-num${tone ? ` ${tone}` : ''}`}>{num}</span>
      <span className="kpi-lbl">{label}</span>
      {sub != null && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}

export default function AdminOpsTab({ showToast, pendingFocus = null, onPendingFocusApplied }) {
  const confirm = useConfirm();
  const [sub, setSub] = useState('user'); // 子页：user | content | ai

  // 跨页聚焦(pendingFocus 单通道):目前只解释 { sub } —— 集成页模型 chip 跳到 AI 子页。
  useEffect(() => {
    if (!pendingFocus) return;
    if (pendingFocus.sub) setSub(pendingFocus.sub);
    onPendingFocusApplied?.();
  }, [pendingFocus, onPendingFocusApplied]);
  const [accounts, setAccounts] = useState(null);
  const [globalAi, setGlobalAi] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [accountQuery, setAccountQuery] = useState('');
  const [accountPage, setAccountPage] = useState(1);
  // 单一时间窗（近 N 天）：页头统一驱动用户子页窗口指标 + AI 用量子页（内容子页为累计口径，不受影响）。
  const [days, setDays] = useState(30);
  // 活跃用户 Top 维度：阅读 | 登录。
  const [topMetric, setTopMetric] = useState('reads');

  // ── 单用户活动详情抽屉 ──
  const [detailUser, setDetailUser] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [loginListOpen, setLoginListOpen] = useState(false); // 抽屉「最近登录」展开列表

  // ── 模型配置（日报 + 阅读器 AI 共用的全局唯一配置，行内于 AI 子页总闸板）──
  const [llmStatus, setLlmStatus] = useState(null);
  const [llmForm, setLlmForm] = useState({ base_url: '', model: '', api_key: '', temperature: 0.3, max_tokens: 4096 });
  const [savingLlm, setSavingLlm] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);

  // ── AI 用量看板 ──
  const [usage, setUsage] = useState(null);

  // ── 内容看板（各源内容健康 + 收藏热度榜）──
  const [content, setContent] = useState(null);

  // ── 媒体库（图床）：缓存统计 ──
  // 全量回填按钮已撤（2026-07-20 拍板：生产只做「随抓预取」,突发回填易触发反爬且
  // 死链超时极慢;后端 /api/admin/media/backfill 端点保留作脚本化应急通道）。
  const [media, setMedia] = useState(null);

  // ── 新建账户弹窗 ──
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const createModal = useModalTransition(createModalOpen);

  // ── 重置密码弹窗（取代 window.prompt：不回显明文、与全站 Modal 体系一致）──
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetBusy, setResetBusy] = useState(false);
  const resetModal = useModalTransition(Boolean(resetTarget));

  // 弹窗/抽屉可访问性（Esc 关闭 / 焦点陷阱 / 焦点归还）：各挂一个 panelRef。
  const createPanelRef = useRef(null);
  const resetPanelRef = useRef(null);
  const detailPanelRef = useRef(null);
  useModalA11y(createModalOpen && createModal.mounted, () => setCreateModalOpen(false), createPanelRef);
  useModalA11y(Boolean(resetTarget) && resetModal.mounted, () => setResetTarget(null), resetPanelRef);
  useModalA11y(Boolean(detailUser), () => setDetailUser(null), detailPanelRef);

  const loadLlm = useCallback(() => getLLMConfig().then((d) => {
    setLlmStatus(d);
    setLlmForm((f) => ({ ...f, base_url: d.base_url || '', model: d.model || '', temperature: d.temperature ?? 0.3, max_tokens: d.max_tokens ?? 4096, api_key: '' }));
  }).catch(() => {}), []);

  const loadUsage = useCallback((d) => fetchAiUsage(d).then(setUsage).catch(() => {}), []);

  const loadContent = useCallback(() => fetchAdminContent().then(setContent).catch(() => {}), []);

  const loadMedia = useCallback(() => fetchMediaStats().then(setMedia).catch(() => {}), []);

  const loadGlobals = useCallback(async () => {
    try {
      const g = await getAiBetaGlobal();
      setGlobalAi(g.enabled);
    } catch (error) {
      showToast(error.message || '加载运维数据失败', 'error');
    }
  }, [showToast]);

  const reloadAccounts = useCallback(async () => {
    try {
      setAccounts(await fetchAdminAccounts(days));
    } catch (error) {
      showToast(error.message || '加载账户失败', 'error');
      setAccounts((prev) => prev ?? []);
    }
  }, [days, showToast]);

  useEffect(() => { loadGlobals(); loadLlm(); loadContent(); loadMedia(); }, [loadGlobals, loadLlm, loadContent, loadMedia]);
  // 账户列表随时间窗口变化重载（窗口指标按 days 聚合）。
  useEffect(() => { reloadAccounts(); }, [reloadAccounts]);
  useEffect(() => { loadUsage(days); }, [loadUsage, days]);

  // 新建 / 详情 / 重置密码打开时锁定页面滚动。
  useEffect(() => {
    if (!createModalOpen && !detailUser && !resetTarget) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [createModalOpen, detailUser, resetTarget]);

  const updateLlm = (key, value) => setLlmForm((f) => ({ ...f, [key]: value }));
  const canTestLlm = Boolean(llmForm.base_url.trim() && llmForm.model.trim() && (llmForm.api_key.trim() || llmStatus?.api_key_set));

  const persistLlm = async () => {
    const payload = {
      base_url: llmForm.base_url.trim(),
      model: llmForm.model.trim(),
      temperature: Number(llmForm.temperature),
      max_tokens: Number(llmForm.max_tokens),
    };
    if (llmForm.api_key.trim()) payload.api_key = llmForm.api_key.trim();
    await saveLLMConfig(payload);
  };

  const handleSaveLlm = async () => {
    setSavingLlm(true);
    try {
      await persistLlm();
      showToast('已保存模型配置', 'success');
      await loadLlm();
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    } finally {
      setSavingLlm(false);
    }
  };

  const handleTestLlm = async () => {
    setTestingLlm(true);
    try {
      await persistLlm();
      const r = await testLLMConfig();
      await loadLlm();
      showToast(`已连接 · ${r.model} · ${r.latency_ms}ms`, 'success');
    } catch (error) {
      showToast(error.message || '连接失败', 'error');
    } finally {
      setTestingLlm(false);
    }
  };

  const handleToggleGlobalAi = async () => {
    const next = !globalAi;
    try {
      const res = await setAiBetaGlobal(next);
      setGlobalAi(res.enabled);
      showToast(res.enabled ? '已开启用户 AI 功能' : '已关闭用户 AI 功能（全员暂停）', 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新 AI 全局开关失败', 'error');
    }
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!newUsername.trim() || !newPassword) {
      showToast('请填写用户名与密码', 'error');
      return;
    }
    if (newPassword.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    setBusy(true);
    try {
      await createAccount({ username: newUsername.trim(), password: newPassword, role: 'user' });
      showToast(`已创建读者账户 ${newUsername.trim()}`, 'success');
      setNewUsername('');
      setNewPassword('');
      setCreateModalOpen(false);
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '创建账户失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleToggleActive = async (acc) => {
    try {
      await updateAccount(acc.username, { is_active: !acc.is_active });
      showToast(acc.is_active ? `已停用 ${acc.username}` : `已启用 ${acc.username}`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleAiBeta = async (acc) => {
    try {
      await updateAccount(acc.username, { ai_beta_enabled: !acc.ai_beta_enabled });
      showToast(acc.ai_beta_enabled ? `已为 ${acc.username} 关闭 AI` : `已为 ${acc.username} 开启 AI`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleResetPassword = (acc) => {
    setResetPassword('');
    setResetTarget(acc);
  };

  const handleResetSubmit = async (event) => {
    event.preventDefault();
    if (resetPassword.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    setResetBusy(true);
    try {
      await resetAccountPassword(resetTarget.username, resetPassword);
      showToast(`已重置 ${resetTarget.username} 的密码`, 'success');
      setResetTarget(null);
    } catch (error) {
      showToast(error.message || '重置密码失败', 'error');
    } finally {
      setResetBusy(false);
    }
  };

  const handleDelete = async (acc) => {
    if (!(await confirm(`确认删除账户「${acc.username}」？其订阅与个人接口令牌会一并清除，且不可恢复。`))) return;
    try {
      await deleteAccount(acc.username);
      showToast(`已删除 ${acc.username}`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '删除账户失败', 'error');
    }
  };

  // ── AI 用量图表数据：每日图按用途/用户拆多系列（透视 + 零填充）──
  const dayPurpose = useMemo(
    () => (usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [usage],
  );
  const dayUser = useMemo(() => usage?.by_day_user ?? [], [usage]);
  const callsDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, days, 'purpose', 'calls'),
    user: pivotDaily(dayUser, days, 'username', 'calls'),
  }), [dayPurpose, dayUser, days]);
  const tokensDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, days, 'purpose', 'total_tokens'),
    user: pivotDaily(dayUser, days, 'username', 'total_tokens'),
  }), [dayPurpose, dayUser, days]);

  // ── 内容看板图表数据 ──
  const contentSourceRows = useMemo(
    () => [...(content?.sources ?? [])]
      .filter((s) => (s.read_count || 0) + (s.favorite_count || 0) + (s.subscription_count || 0) > 0)
      .sort((a, b) => (b.favorite_count - a.favorite_count) || (b.read_count - a.read_count) || (b.subscription_count - a.subscription_count))
      .map((s) => ({ name: s.name, reads: s.read_count || 0, favorites: s.favorite_count || 0, subs: s.subscription_count || 0 })),
    [content],
  );
  const topArticleRows = useMemo(
    () => (content?.top_articles ?? []).slice(0, 10).map((a) => ({
      title: a.title || '无标题',
      src: a.source_name || a.source_id || '',
      fav: a.favorite_count,
    })),
    [content],
  );

  // 账户搜索 + 分页（前端裁切，承载成百上千读者账户而不撑爆页面）。
  const filteredAccounts = useMemo(() => {
    const list = accounts ?? [];
    const q = accountQuery.trim().toLowerCase();
    return q ? list.filter((a) => a.username.toLowerCase().includes(q)) : list;
  }, [accounts, accountQuery]);
  const accountTotalPages = Math.max(1, Math.ceil(filteredAccounts.length / ACCOUNTS_PAGE_SIZE));
  const accountSafePage = Math.min(accountPage, accountTotalPages);
  const pagedAccounts = useMemo(
    () => filteredAccounts.slice((accountSafePage - 1) * ACCOUNTS_PAGE_SIZE, accountSafePage * ACCOUNTS_PAGE_SIZE),
    [filteredAccounts, accountSafePage],
  );

  // ── 用户子页总览 KPI（窗口指标，源自账户列表的窗口字段）──
  const userKpis = useMemo(() => {
    const list = accounts ?? [];
    return {
      readers: list.length,
      disabled: list.filter((a) => !a.is_active).length,
      loggedIn: list.filter((a) => a.logged_in_window).length,
      logins: list.reduce((s, a) => s + (a.logins || 0), 0),
      reads: list.reduce((s, a) => s + (a.reads || 0), 0),
      aiCalls: list.reduce((s, a) => s + (a.ai_calls || 0), 0),
      aiTokens: list.reduce((s, a) => s + (a.ai_tokens || 0), 0),
    };
  }, [accounts]);
  const perDay = (n) => (days > 0 ? (n / days).toFixed(1) : '0');
  // 活跃用户 Top：按所选维度（阅读 / 登录）排行。
  const activeUserRows = useMemo(
    () => [...(accounts ?? [])]
      .filter((a) => (a[topMetric] || 0) > 0)
      .sort((a, b) => (b[topMetric] || 0) - (a[topMetric] || 0))
      .slice(0, 8)
      .map((a) => ({ name: a.username, value: a[topMetric] || 0 })),
    [accounts, topMetric],
  );

  // ── 单用户详情：打开抽屉并拉取窗口活动 ──
  const openDetail = useCallback(async (username) => {
    setDetailUser(username);
    setDetailData(null);
    setLoginListOpen(false);
    try {
      setDetailData(await fetchAccountActivity(username, days));
    } catch (error) {
      showToast(error.message || '获取用户详情失败', 'error');
    }
  }, [days, showToast]);

  // 详情抽屉图表数据：每日 AI 用量（按用途堆叠，calls / tokens 两套）+ 各源阅读/收藏。
  const detailDayPurpose = useMemo(
    () => (detailData?.usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [detailData],
  );
  const detailWindow = detailData?.usage?.window_days ?? days;
  const detailDatasets = useMemo(() => ({
    calls: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'calls'),
    tokens: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'total_tokens'),
  }), [detailDayPurpose, detailWindow]);
  const detailEngagementRows = useMemo(
    () => (detailData?.source_engagement ?? []).map((s) => ({ name: s.name || s.source_id, reads: s.reads, favorites: s.favorites })),
    [detailData],
  );

  return (
    <div className="admin-page">
      <div className="page-head">
        <h1 className="page-title">运维管理</h1>
        <div className="page-head-actions">
          <span className="win-label">时间窗</span>
          <div className="mini-seg" role="group" aria-label="时间窗">
            {[7, 14, 30, 90].map((d) => (
              <button key={d} type="button" onClick={() => setDays(d)} className={`mini-seg-btn ${days === d ? 'is-on' : ''}`}>{d} 天</button>
            ))}
          </div>
          <div className="segmented-control">
            <button onClick={() => setSub('user')} className={`segmented-option ${sub === 'user' ? 'segmented-option-active' : ''}`}><Users /> 用户</button>
            <button onClick={() => setSub('content')} className={`segmented-option ${sub === 'content' ? 'segmented-option-active' : ''}`}><Database /> 内容</button>
            <button onClick={() => setSub('ai')} className={`segmented-option ${sub === 'ai' ? 'segmented-option-active' : ''}`}><Brain /> AI</button>
          </div>
        </div>
      </div>

      {/* ══ 用户子页 ══════════════════════════════════════════════ */}
      {sub === 'user' && (
        <div>
          <section className="surface-card kpi-strip" aria-label="窗口活跃概览">
            <Kpi num={fmtNum(userKpis.readers)} label="读者账户" sub={userKpis.disabled ? `停用 ${userKpis.disabled}` : '全部启用'} />
            <Kpi num={fmtNum(userKpis.loggedIn)} label={`近 ${days} 天活跃`} sub="登录过 ≥1 次" />
            <Kpi num={fmtNum(userKpis.logins)} label="登录次数" sub={`日均 ${perDay(userKpis.logins)}`} />
            <Kpi num={fmtNum(userKpis.reads)} label="阅读次数" sub={`日均 ${perDay(userKpis.reads)}`} />
            <Kpi num={fmtNum(userKpis.aiCalls)} label="AI 调用" sub={`tokens ${fmtNum(userKpis.aiTokens)}`} />
          </section>

          {(userKpis.reads + userKpis.logins) > 0 && (
            <>
              <div className="zone-head">
                <span className="zone-title">活跃用户 Top</span>
                <span className="zone-hint">近 {days} 天</span>
                <span className="zone-acts mini-seg" role="group" aria-label="排序维度">
                  {[['reads', '按阅读'], ['logins', '按登录']].map(([k, lbl]) => (
                    <button key={k} type="button" onClick={() => setTopMetric(k)} className={`mini-seg-btn ${topMetric === k ? 'is-on' : ''}`}>{lbl}</button>
                  ))}
                </span>
              </div>
              <section className="surface-card card-pad rounded-[var(--r-card)]">
                <RankBars
                  rows={activeUserRows}
                  labelKey="name"
                  valueKey="value"
                  name={topMetric === 'reads' ? '阅读' : '登录'}
                  height={Math.max(120, activeUserRows.length * 30)}
                  tickFormatter={truncLabel}
                  emptyHint={topMetric === 'reads' ? '窗口内还没有阅读记录' : '窗口内还没有登录记录'}
                />
              </section>
            </>
          )}

          <div className="zone-head">
            <span className="zone-title">账户管理</span>
            <span className="zone-hint">停用 / 删除会立即让对应账户的会话失效；点行查看活动详情</span>
            <span className="zone-acts flex items-center gap-2">
              {accounts && accounts.length > 0 && (
                <span className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                  <input
                    value={accountQuery}
                    onChange={(e) => { setAccountQuery(e.target.value); setAccountPage(1); }}
                    placeholder="搜索用户名"
                    className="form-input form-input-inline w-44 pl-8"
                  />
                </span>
              )}
              <button onClick={() => setCreateModalOpen(true)} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                <UserPlus className="h-4 w-4" /> 新建读者
              </button>
            </span>
          </div>

          <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
            {accounts === null ? (
              <p className="p-6 tiny-meta">加载中…</p>
            ) : accounts.length === 0 ? (
              <p className="p-6 text-center tiny-meta">还没有读者账户，用右上角「新建读者」创建第一个。</p>
            ) : filteredAccounts.length === 0 ? (
              <p className="p-6 text-center tiny-meta">没有匹配「{accountQuery.trim()}」的账户。</p>
            ) : (
              <>
                <div className="acct-scroll">
                  <table className="acct-table">
                    <thead>
                      <tr>
                        <th className="acct-th">用户</th>
                        <th className="acct-th">状态</th>
                        <th className="acct-th">最近登录</th>
                        <th className="acct-th is-num">登录</th>
                        <th className="acct-th is-num">阅读</th>
                        <th className="acct-th is-num">AI 调用</th>
                        <th className="acct-th is-num">订阅</th>
                        <th className="acct-th" aria-label="操作" style={{ width: 130 }} />
                      </tr>
                    </thead>
                    <tbody>
                      {pagedAccounts.map((account) => (
                        <tr
                          key={account.username}
                          className={`acct-row ${detailUser === account.username ? 'is-sel' : ''}`}
                          role="button"
                          tabIndex={0}
                          aria-label={`${account.username} 活动详情`}
                          onClick={() => openDetail(account.username)}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(account.username); } }}
                        >
                          <td>
                            <span className="acct-user">
                              <span className="acct-avatar">{account.username.charAt(0).toUpperCase()}</span>
                              <span className="acct-name">{account.username}</span>
                            </span>
                          </td>
                          <td>{account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>}</td>
                          <td><span className="acct-mono">{formatStamp(account.last_login_at)}</span></td>
                          <td className={`acct-n ${(account.logins || 0) ? '' : 'is-zero'}`}>{account.logins || '–'}</td>
                          <td className={`acct-n is-main ${(account.reads || 0) ? '' : 'is-zero'}`}>{account.reads || '–'}</td>
                          <td className={`acct-n ${(account.ai_calls || 0) ? '' : 'is-zero'}`}>{account.ai_calls || '–'}</td>
                          <td className="acct-n">{account.subscription_count ?? 0}</td>
                          <td>
                            <span className="rowacts" onClick={(e) => e.stopPropagation()}>
                              {globalAi === false ? (
                                <span className="rowact-btn" title="AI 功能总闸已关闭（AI 子页），单账户开关暂不可用" aria-disabled="true"><Ban /></span>
                              ) : (
                                <button
                                  type="button"
                                  className={`rowact-btn ${account.ai_beta_enabled ? 'is-on' : ''}`}
                                  title={account.ai_beta_enabled ? `关闭 ${account.username} 的 AI` : `开启 ${account.username} 的 AI`}
                                  onClick={() => handleToggleAiBeta(account)}
                                >
                                  <Zap />
                                </button>
                              )}
                              <button type="button" className="rowact-btn" title="重置密码" onClick={() => handleResetPassword(account)}><KeyRound /></button>
                              <button type="button" className="rowact-btn" title={account.is_active ? '停用账户' : '启用账户'} onClick={() => handleToggleActive(account)}><Power /></button>
                              <button type="button" className="rowact-btn is-danger" title="删除账户" onClick={() => handleDelete(account)}><Trash2 /></button>
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {accountTotalPages > 1 && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
                    <span className="tiny-meta">
                      共 {filteredAccounts.length} 个 · 第 {(accountSafePage - 1) * ACCOUNTS_PAGE_SIZE + 1}–{Math.min(accountSafePage * ACCOUNTS_PAGE_SIZE, filteredAccounts.length)} 个
                    </span>
                    <div className="pager">
                      <button className="pager-btn" disabled={accountSafePage <= 1} onClick={() => setAccountPage((p) => Math.max(1, p - 1))}>‹</button>
                      {Array.from({ length: accountTotalPages }, (_, i) => (
                        <button key={i} className={`pager-btn ${i + 1 === accountSafePage ? 'is-on' : ''}`} onClick={() => setAccountPage(i + 1)}>{i + 1}</button>
                      ))}
                      <button className="pager-btn" disabled={accountSafePage >= accountTotalPages} onClick={() => setAccountPage((p) => Math.min(accountTotalPages, p + 1))}>›</button>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}

      {/* ══ 内容子页 ══════════════════════════════════════════════ */}
      {sub === 'content' && (
        <div>
          {!content ? (
            <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
              <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载内容统计…
            </p>
          ) : (
            <>
              <section className="surface-card kpi-strip" aria-label="内容概览">
                <Kpi num={fmtNum(content.totals.sources)} label="内容源" sub="累计" />
                <Kpi num={fmtNum(content.totals.articles)} label="归档文章" sub="累计" />
                <Kpi num={fmtNum(content.totals.reads)} label="阅读总数" sub="累计" />
                <Kpi num={fmtNum(content.totals.favorites)} label="收藏总数" sub="累计" />
                <Kpi num={pct(content.totals.vectorized_rate)} label="向量化率" tone={vectorizedRateClass(content.totals.vectorized_rate)} />
              </section>

              <div className="zone-head">
                <span className="zone-title">各源热度</span>
                <span className="zone-hint">阅读 / 收藏 / 订阅为累计口径，不随时间窗变化</span>
              </div>
              <div className="admin-grid">
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <div className="card-head">
                    <span className="card-title">各源 · 文章阅读 / 文章收藏 / 源订阅{contentSourceRows.length ? ` · ${contentSourceRows.length} 个源` : ''}</span>
                  </div>
                  <BarList
                    rows={contentSourceRows}
                    nameKey="name"
                    metrics={[
                      { key: 'reads', name: '文章阅读', color: C_READ },
                      { key: 'favorites', name: '文章收藏', color: C_FAVORITE },
                      { key: 'subs', name: '源订阅', color: C_OTHER },
                    ]}
                    emptyHint="还没有阅读 / 收藏 / 订阅记录"
                  />
                </section>
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <div className="card-head">
                    <span className="card-title">文章 · 收藏 TOP{topArticleRows.length ? ` ${topArticleRows.length}` : ''}</span>
                  </div>
                  {topArticleRows.length === 0 ? (
                    <div className="flex items-center justify-center rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] tiny-meta" style={{ minHeight: 120 }}>
                      还没有任何收藏记录，读者在阅读器收藏文章后这里会出现热度榜。
                    </div>
                  ) : (
                    <div className="toplist">
                      {topArticleRows.map((a, i) => (
                        <div key={`${a.title}-${i}`} className="toplist-row">
                          <span className="toplist-rank">{i + 1}</span>
                          <span className="toplist-title" title={a.title}>{a.title}</span>
                          {a.src && <span className="toplist-src">{a.src}</span>}
                          <span className="toplist-n">{a.fav}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>

              {/* ── 媒体库（图床）：正文外链图片本地缓存 ── */}
              <div className="zone-head">
                <span className="zone-title">媒体库</span>
                <span className="zone-hint">正文外链图片的本地缓存：抓取入库时随文预取，逐日覆盖见下方热点图</span>
              </div>
              {media?.enabled === false ? (
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <p className="tiny-meta">媒体库未启用（[media] enabled = false），正文图片走外链直连。</p>
                </section>
              ) : (
                <>
                  <section className="surface-card kpi-strip" aria-label="媒体库概览">
                    {!media ? (
                      <Kpi num="—" label="图片缓存" sub="加载中…" />
                    ) : (
                      <>
                        <Kpi num={fmtNum(media.cached_count)} label="已缓存图片" sub="按 URL 计" />
                        <Kpi num={fmtNum(media.distinct_files)} label="去重文件" sub="按内容计" />
                        <Kpi num={fmtBytes(media.disk_bytes)} label="占用空间" sub="去重后落盘" />
                        <Kpi num={fmtNum(media.failed_count)} label="下载失败" sub="多为签名过期 / 防盗链" tone={media.failed_count > 0 ? 'is-warn' : undefined} />
                      </>
                    )}
                  </section>
                  <MediaHeatmap showToast={showToast} />
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* ══ AI 子页 ══════════════════════════════════════════════ */}
      {sub === 'ai' && (
        <div>
          <section className="surface-card ai-switchboard rounded-[var(--r-card)]">
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
            <div className="model-fields">
              <label className="model-field">base_url
                <input value={llmForm.base_url} onChange={(e) => updateLlm('base_url', e.target.value)} placeholder="https://api.deepseek.com/v1" className="model-input-grow" />
              </label>
              <label className="model-field">model
                <input value={llmForm.model} onChange={(e) => updateLlm('model', e.target.value)} placeholder="deepseek-chat" size={12} />
              </label>
              <label className="model-field">api_key
                <input type="password" value={llmForm.api_key} onChange={(e) => updateLlm('api_key', e.target.value)} placeholder={llmStatus?.api_key_set ? '留空不改' : 'sk-...'} size={12} />
              </label>
              <label className="model-field">temp
                <input type="number" step="0.1" min="0" max="2" value={llmForm.temperature} onChange={(e) => updateLlm('temperature', e.target.value)} style={{ width: 52 }} />
              </label>
              <label className="model-field">max_tokens
                <input type="number" step="256" min="256" value={llmForm.max_tokens} onChange={(e) => updateLlm('max_tokens', e.target.value)} style={{ width: 68 }} />
              </label>
              <button type="button" className="model-btn" onClick={handleTestLlm} disabled={testingLlm || savingLlm || !canTestLlm}>
                {testingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />} 测试连通
              </button>
              <button type="button" className="model-btn" onClick={handleSaveLlm} disabled={savingLlm}>
                {savingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 保存
              </button>
            </div>
          </section>

          {!usage || usage.totals.calls === 0 ? (
            <>
              <div className="zone-head"><span className="zone-title">每日用量</span><span className="zone-hint">近 {days} 天</span></div>
              <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
                近 {days} 天还没有 AI 调用记录，触发一次翻译 / 问答或日报生成后这里会出现统计。
              </p>
            </>
          ) : (
            <>
              <section className="surface-card kpi-strip" style={{ marginTop: 16 }} aria-label="AI 用量概览">
                <Kpi num={fmtNum(usage.totals.calls)} label="总调用" sub={`近 ${days} 天`} />
                <Kpi num={fmtNum(usage.totals.prompt_tokens)} label="输入 tokens" sub={`日均 ${fmtNum(Math.round(usage.totals.prompt_tokens / Math.max(1, days)))}`} />
                <Kpi num={fmtNum(usage.totals.completion_tokens)} label="输出 tokens" sub={`日均 ${fmtNum(Math.round(usage.totals.completion_tokens / Math.max(1, days)))}`} />
              </section>
              <div className="zone-head"><span className="zone-title">每日用量</span><span className="zone-hint">悬停看当日明细；系列色恒随实体，不随排位</span></div>
              <div className="admin-grid">
                <MultiSeriesArea title="每日调用次数" datasets={callsDatasets} namespace="ai-calls" />
                <MultiSeriesArea title="每日 tokens" datasets={tokensDatasets} namespace="ai-tokens" />
              </div>
            </>
          )}
        </div>
      )}

      {/* ── 单用户活动详情抽屉（右缘滑入，ledger-drawer 语法） ── */}
      <div className={`ledger-scrim ${detailUser ? 'is-open' : ''}`} onClick={() => setDetailUser(null)} aria-hidden="true" />
      <aside
        ref={detailPanelRef}
        className={`ledger-drawer ${detailUser ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={detailUser ? `${detailUser} · 活动详情` : '用户活动详情'}
        aria-hidden={!detailUser}
      >
        <div className="ledger-drawer-head">
          <span className="acct-avatar">{detailUser ? detailUser.charAt(0).toUpperCase() : ''}</span>
          <span className="ledger-drawer-title">{detailUser}</span>
          {detailData && (detailData.account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>)}
          <button type="button" className="icon-button shrink-0" onClick={() => setDetailUser(null)} aria-label="关闭详情"><X className="h-5 w-5" /></button>
        </div>
        <div className="ledger-drawer-body">
          {!detailData ? (
            <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center tiny-meta">
              <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载活动详情…
            </p>
          ) : (
            <>
              <div className="tiles">
                <div className="tile"><div className="tile-num">{fmtNum(detailData.reads.total)}</div><div className="tile-lbl">近 {detailWindow} 天阅读</div></div>
                <div className="tile"><div className="tile-num">{fmtNum(detailData.usage.totals.calls)}</div><div className="tile-lbl">近 {detailWindow} 天 AI 调用</div></div>
                <div className="tile"><div className="tile-num">{fmtNum(detailData.account.subscription_count)}</div><div className="tile-lbl">订阅来源</div></div>
              </div>

              {detailData.logins.recent.length > 0 ? (
                <details className="login-card" open={loginListOpen} onToggle={(e) => setLoginListOpen(e.currentTarget.open)}>
                  <summary>
                    <span className="tile-num" style={{ fontSize: '18px', lineHeight: '22px' }}>{fmtNum(detailData.logins.count)}</span>
                    <span className="tile-lbl" style={{ alignSelf: 'center' }}>近 {detailWindow} 天登录 · 最近 {formatStamp(detailData.account.last_login_at)}</span>
                    <span className="login-toggle">{loginListOpen ? '收起' : `展开近 ${detailData.logins.recent.length} 次`}</span>
                  </summary>
                  <ul className="login-list">
                    {detailData.logins.recent.map((at, i) => <li key={`${at}-${i}`}>{formatStamp(at)}</li>)}
                  </ul>
                </details>
              ) : (
                <div className="login-card">
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span className="tile-num" style={{ fontSize: '18px', lineHeight: '22px' }}>{fmtNum(detailData.logins.count)}</span>
                    <span className="tile-lbl" style={{ alignSelf: 'center' }}>近 {detailWindow} 天登录</span>
                  </div>
                </div>
              )}

              {(detailData.usage.totals.calls === 0 && detailData.reads.total === 0 && detailData.favorites_total === 0) ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                  近 {detailWindow} 天该用户没有阅读 / 收藏 / AI 调用记录。
                </p>
              ) : (
                <>
                  <div>
                    <div className="drawer-sec-title">各源 · 文章阅读 / 文章收藏{detailEngagementRows.length ? ` · ${detailEngagementRows.length} 个源` : ''}</div>
                    <BarList
                      rows={detailEngagementRows}
                      nameKey="name"
                      metrics={[{ key: 'reads', name: '文章阅读', color: C_READ }, { key: 'favorites', name: '文章收藏', color: C_FAVORITE }]}
                      emptyHint="窗口内无阅读、且无收藏记录"
                    />
                  </div>
                  <MultiSeriesArea
                    title="每日 AI 用量"
                    datasets={detailDatasets}
                    dims={[['calls', '调用'], ['tokens', 'tokens']]}
                    namespace="user-detail"
                  />
                </>
              )}
            </>
          )}
        </div>
      </aside>

      {/* ── 新建账户弹窗（Portal 到 body，避开变换祖先造成的 fixed 错位） ── */}
      {createModal.mounted && createPortal(
        <div className={`modal-overlay ${createModal.closing ? 'is-closing' : ''}`} onClick={() => setCreateModalOpen(false)}>
          <form ref={createPanelRef} role="dialog" aria-modal="true" aria-label="新建读者账户" tabIndex={-1} className="modal-panel max-w-md form-sheet" onClick={(e) => e.stopPropagation()} onSubmit={handleCreate}>
            <div className="form-sheet-head">
              <h3 className="card-title">新建读者账户</h3>
              <button type="button" onClick={() => setCreateModalOpen(false)} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
            </div>
            <div className="form-sheet-body">
              <div className="form-sheet-field">
                <label className="form-label" htmlFor="acct-new-name">用户名</label>
                <input id="acct-new-name" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="用户名" autoComplete="off" className="form-input w-full" />
              </div>
              <div className="form-sheet-field">
                <label className="form-label" htmlFor="acct-new-pw">初始密码</label>
                <input id="acct-new-pw" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="至少 6 位" autoComplete="new-password" className="form-input w-full" />
              </div>
            </div>
            <div className="form-sheet-foot">
              <button type="button" onClick={() => setCreateModalOpen(false)} className="action-button action-button-quiet">取消</button>
              <button type="submit" disabled={busy} className="action-button action-button-primary">
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UserPlus className="h-3.5 w-3.5" />} 创建读者账户
              </button>
            </div>
          </form>
        </div>,
        document.body,
      )}

      {/* ── 重置密码弹窗（Portal 到 body） ── */}
      {resetModal.mounted && createPortal(
        <div className={`modal-overlay ${resetModal.closing ? 'is-closing' : ''}`} onClick={() => setResetTarget(null)}>
          <form ref={resetPanelRef} role="dialog" aria-modal="true" aria-label="重置密码" tabIndex={-1} className="modal-panel max-w-md form-sheet" onClick={(e) => e.stopPropagation()} onSubmit={handleResetSubmit}>
            <div className="form-sheet-head">
              <h3 className="card-title">重置密码</h3>
              <button type="button" onClick={() => setResetTarget(null)} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
            </div>
            <div className="form-sheet-body">
              <p className="tiny-meta">为账户「{resetTarget?.username}」设置新密码，设置后该账户需用新密码登录。</p>
              <div className="form-sheet-field">
                <label className="form-label" htmlFor="acct-reset-pw">新密码</label>
                <input
                  id="acct-reset-pw"
                  type="password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  placeholder="至少 6 位"
                  autoComplete="new-password"
                  autoFocus
                  className="form-input w-full"
                />
              </div>
            </div>
            <div className="form-sheet-foot">
              <button type="button" onClick={() => setResetTarget(null)} className="action-button action-button-quiet">取消</button>
              <button type="submit" disabled={resetBusy} className="action-button action-button-primary">
                {resetBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />} 保存新密码
              </button>
            </div>
          </form>
        </div>,
        document.body,
      )}
    </div>
  );
}
