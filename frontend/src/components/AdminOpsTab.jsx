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
  ZapOff,
  X,
  Loader2,
  MessageSquare,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react';
import {
  fetchAdminAccounts,
  fetchAccountActivity,
  fetchAdminContent,
  fetchMediaStats,
  getXApiConfig,
  getXApiQuota,
  getAiBetaGlobal,
  setAiBetaGlobal,
  setAiBetaNewUserDefault,
  setAiDailyTokenBudget,
  fetchPublicShareGlobal,
  updatePublicShareGlobal,
  fetchAiUsage,
  getLLMConfig,
  createAccount,
  updateAccount,
  resetAccountPassword,
  deleteAccount,
  batchUpdateAccounts,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';
import { ThFilter, ThSearch, ThSort } from './admin/TableTh';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import { MultiSeriesArea, RankBars, BarList } from './charts/DashboardCharts';
import MediaHeatmap from './admin/MediaHeatmap';
import UserSourcesPanel from './admin/UserSourcesPanel';
import FeedbackInboxPanel from './admin/FeedbackInboxPanel';
import AnnouncementsPanel from './admin/AnnouncementsPanel';
import AdminAuditPanel from './admin/AdminAuditPanel';
import Pager from './admin/Pager';
import { pivotDaily, C_READ, C_FAVORITE, C_SUBSCRIBE } from './charts/chartUtils';
import { PURPOSE_LABELS, formatStamp, fmtNum, truncLabel } from './admin/adminUtils';
import { avatarInitial, avatarHue } from '../utils/avatarColor';

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

export default function AdminOpsTab({ showToast, currentUsername = '', pendingFocus = null, onPendingFocusApplied, onOpenCredentials }) {
  const confirm = useConfirm();
  const [sub, setSub] = useState('user'); // 子页：user | content | ai

  // 跨页聚焦(pendingFocus 单通道):目前只解释 { sub } —— 集成页模型 chip 跳到 AI 子页。
  useEffect(() => {
    if (!pendingFocus) return;
    if (pendingFocus.sub) setSub(pendingFocus.sub);
    onPendingFocusApplied?.();
  }, [pendingFocus, onPendingFocusApplied]);
  // 账户列表(规模化波):服务端分页 + 搜索,前端只持有当前页;summary 聚合全量供 KPI/排行。
  const [acctData, setAcctData] = useState(null); // {items,total,summary} | null = 加载中
  const [globalAi, setGlobalAi] = useState(null);
  const [aiBudget, setAiBudget] = useState(null);  // {daily_token_budget, tokens_used_today}(读者面 AI 日预算,0=不限)
  const [newUserAiDefault, setNewUserAiDefault] = useState(null); // 新账号 AI 默认值(只影响此后新建账户)
  const [budgetDraft, setBudgetDraft] = useState(''); // 预算输入框草稿(失焦/回车提交)
  const [publicShare, setPublicShare] = useState(null);   // 公开分享总闸 + 存活链接盘点
  const [busy, setBusy] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('user'); // 新建账户角色(v3.19 多管理员:可直建管理员,默认读者防误触)
  const [accountQuery, setAccountQuery] = useState(''); // 输入框即时值(「用户」列头就地搜索)
  const [acctQ, setAcctQ] = useState(''); // 防抖后生效的搜索词(300ms)
  const [accountPage, setAccountPage] = useState(1);
  // 账户管理 V2(v3.41,审计 M05/M06):服务端组合过滤 × 排序 + 勾选批量。
  const [acctRole, setAcctRole] = useState('');       // '' | 'admin' | 'user'
  const [acctStatus, setAcctStatus] = useState('');   // '' | 'active' | 'disabled'
  const [acctAi, setAcctAi] = useState('');           // '' | 'on' | 'off'
  const [acctSort, setAcctSort] = useState('username');
  const [acctOrder, setAcctOrder] = useState('asc');
  const [selectedAccounts, setSelectedAccounts] = useState(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  // 单一时间窗（近 N 天）：页头统一驱动用户子页窗口指标 + AI 用量子页（内容子页为累计口径，不受影响）。
  const [days, setDays] = useState(30);
  // 活跃用户 Top 维度：阅读 | 登录。
  const [topMetric, setTopMetric] = useState('reads');

  // ── 单用户活动详情抽屉 ──
  const [detailUser, setDetailUser] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [loginListOpen, setLoginListOpen] = useState(false); // 抽屉「最近登录」展开列表

  // ── 模型/X API 状态(只读 chip;编辑已收敛到 设置 → 凭据,v3.27 凭据整合台)──
  const [llmStatus, setLlmStatus] = useState(null);
  const [xStatus, setXStatus] = useState(null);
  const [xQuota, setXQuota] = useState(null);

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

  const loadLlm = useCallback(() => getLLMConfig().then(setLlmStatus).catch(() => {}), []);

  const loadUsage = useCallback((d) => fetchAiUsage(d).then(setUsage).catch(() => {}), []);

  const loadContent = useCallback(() => fetchAdminContent().then(setContent).catch(() => {}), []);

  const loadMedia = useCallback(() => fetchMediaStats().then(setMedia).catch(() => {}), []);

  const loadX = useCallback(() => Promise.all([
    getXApiConfig().then(setXStatus).catch(() => {}),
    getXApiQuota().then(setXQuota).catch(() => {}),
  ]), []);

  const loadGlobals = useCallback(async () => {
    try {
      const g = await getAiBetaGlobal();
      setGlobalAi(g.enabled);
      setAiBudget({ daily_token_budget: g.daily_token_budget ?? 0, tokens_used_today: g.tokens_used_today ?? 0 });
      setBudgetDraft(String(g.daily_token_budget ?? 0));
      setNewUserAiDefault(g.new_user_default ?? true);
    } catch (error) {
      showToast(error.message || '加载运维数据失败', 'error');
    }
    // 分享总闸单独 try:它读不到不该连带把 AI 总闸的错误提示也吞掉/重复弹。
    try {
      setPublicShare(await fetchPublicShareGlobal());
    } catch { /* 读不到就保持 null,卡片显示「正在读取…」且开关禁用 */ }
  }, [showToast]);

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

  // 搜索防抖:停键 300ms 后生效并归位第一页(服务端过滤)。
  const debouncedAccountQuery = useDebouncedValue(accountQuery, 300);
  useEffect(() => {
    setAcctQ(debouncedAccountQuery.trim());
    setAccountPage(1);
  }, [debouncedAccountQuery]);

  const reloadAccounts = useCallback(async () => {
    try {
      setAcctData(await fetchAdminAccounts(days, {
        skip: (accountPage - 1) * ACCOUNTS_PAGE_SIZE,
        limit: ACCOUNTS_PAGE_SIZE,
        q: acctQ,
        role: acctRole,
        status: acctStatus,
        ai: acctAi,
        sort: acctSort,
        order: acctOrder,
      }));
    } catch (error) {
      showToast(error.message || '加载账户失败', 'error');
      setAcctData((prev) => prev ?? { items: [], total: 0, summary: null });
    }
  }, [days, accountPage, acctQ, acctRole, acctStatus, acctAi, acctSort, acctOrder, showToast]);

  // 过滤/排序/搜索/时间窗变化:归位第一页并清空勾选(勾选集是当前结果集语境的)。
  useEffect(() => {
    setAccountPage(1);
    setSelectedAccounts(new Set());
  }, [acctRole, acctStatus, acctAi, acctSort, acctOrder, acctQ, days]);

  useEffect(() => { loadGlobals(); loadLlm(); loadContent(); loadMedia(); loadX(); }, [loadGlobals, loadLlm, loadContent, loadMedia, loadX]);
  // 账户列表随时间窗口/页码/搜索词变化重载（窗口指标按 days 聚合）。
  useEffect(() => { reloadAccounts(); }, [reloadAccounts]);
  useEffect(() => { loadUsage(days); }, [loadUsage, days]);

  // 新建 / 详情 / 重置密码打开时锁定页面滚动。
  useEffect(() => {
    if (!createModalOpen && !detailUser && !resetTarget) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [createModalOpen, detailUser, resetTarget]);

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
      await createAccount({ username: newUsername.trim(), password: newPassword, role: newRole });
      showToast(`已创建${newRole === 'admin' ? '管理员' : '读者'}账户 ${newUsername.trim()}`, 'success');
      setNewUsername('');
      setNewPassword('');
      setNewRole('user');
      setCreateModalOpen(false);
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '创建账户失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  // 单账户操作若发生在详情抽屉打开的这个人身上,顺手刷新抽屉快照(M19 就地管理闭环)。
  const refreshDetailIfOpen = async (username) => {
    if (detailUser !== username) return;
    try { setDetailData(await fetchAccountActivity(username, days)); } catch { /* 详情刷新失败不打断主操作 */ }
  };

  // 角色变更(v3.19 多管理员):提升/降级都需确认;降级自己额外预警——生效后当前
  // 会话在下一次请求即被吊销(read_auth_token 回查),表现为被登出,不预警会像 bug。
  const handleToggleRole = async (acc) => {
    const toAdmin = acc.role !== 'admin';
    const selfNote = !toAdmin && acc.username === currentUsername
      ? '这是你当前登录的账号,取消后将立即被登出管理台。'
      : '';
    const message = toAdmin
      ? `确认将「${acc.username}」设为管理员?管理员拥有采集、归档、账户与系统配置的全部权限。`
      : `确认取消「${acc.username}」的管理员身份?该账户将变为读者,仅保留阅读与订阅能力。${selfNote}`;
    if (!(await confirm(message))) return;
    try {
      await updateAccount(acc.username, { role: toAdmin ? 'admin' : 'user' });
      showToast(toAdmin ? `已将 ${acc.username} 设为管理员` : `已取消 ${acc.username} 的管理员身份`, 'success');
      await reloadAccounts();
      await refreshDetailIfOpen(acc.username);
    } catch (error) {
      // 末位管理员保护等后端裁决文案直接透传
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleActive = async (acc) => {
    try {
      await updateAccount(acc.username, { is_active: !acc.is_active });
      showToast(acc.is_active ? `已停用 ${acc.username}` : `已启用 ${acc.username}`, 'success');
      await reloadAccounts();
      await refreshDetailIfOpen(acc.username);
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleAiBeta = async (acc) => {
    try {
      await updateAccount(acc.username, { ai_beta_enabled: !acc.ai_beta_enabled });
      showToast(acc.ai_beta_enabled ? `已为 ${acc.username} 关闭 AI` : `已为 ${acc.username} 开启 AI`, 'success');
      await reloadAccounts();
      await refreshDetailIfOpen(acc.username);
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
    if (!(await confirm(`确认删除账户「${acc.username}」？其订阅、收藏、分享链接与个人接口令牌会一并清除，且不可恢复。`))) return;
    try {
      await deleteAccount(acc.username);
      showToast(`已删除 ${acc.username}`, 'success');
      if (detailUser === acc.username) setDetailUser(null);
      setSelectedAccounts((prev) => {
        if (!prev.has(acc.username)) return prev;
        const next = new Set(prev); next.delete(acc.username); return next;
      });
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '删除账户失败', 'error');
    }
  };

  // ── 排序表头(v3.41 M06):点击同列翻转方向,换列取该列的惯用初始方向 ──
  const handleSort = (key) => {
    if (acctSort === key) {
      setAcctOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setAcctSort(key);
      setAcctOrder(key === 'username' ? 'asc' : 'desc');
    }
  };

  // ── 勾选与批量(v3.41 M05):页级全选,批量走原子端点 ──
  const toggleAccountSelection = (username) => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(username)) next.delete(username); else next.add(username);
      return next;
    });
  };

  const handleBatch = async (updates, { confirmMsg, done } = {}) => {
    const names = Array.from(selectedAccounts);
    if (!names.length) return;
    if (confirmMsg && !(await confirm(confirmMsg))) return;
    setBatchBusy(true);
    try {
      const res = await batchUpdateAccounts(names, updates);
      showToast(
        `${done(res.updated)}${res.unchanged ? `（${res.unchanged} 个本已如此）` : ''}`,
        'success',
      );
      setSelectedAccounts(new Set());
      await reloadAccounts();
    } catch (error) {
      // 原子语义:任一账户不存在/末位管理员保护 → 整批未生效,后端文案直接透传。
      showToast(error.message || '批量更新失败', 'error');
    } finally {
      setBatchBusy(false);
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

  // 账户分页派生(服务端分页:items 即当前页,total 为过滤后总数)。
  const pagedAccounts = acctData?.items ?? [];
  const acctTotal = acctData?.total ?? 0;
  const acctSummary = acctData?.summary || null;
  const accountTotalPages = Math.max(1, Math.ceil(acctTotal / ACCOUNTS_PAGE_SIZE));
  const acctFiltersActive = Boolean(acctQ || acctRole || acctStatus || acctAi);
  // 页级全选(150 人规模在 15/页 下最多十页;勾选集跨页累积,过滤变化即清)。
  const pageUsernames = pagedAccounts.map((a) => a.username);
  const allPageSelected = pageUsernames.length > 0 && pageUsernames.every((n) => selectedAccounts.has(n));
  const toggleAllPage = () => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (allPageSelected) pageUsernames.forEach((n) => next.delete(n));
      else pageUsernames.forEach((n) => next.add(n));
      return next;
    });
  };
  // 列头即操作:排序/筛选/搜索三种列头统一走共享组件(components/admin/TableTh,
  // 账户/审计/自定源三表同一套操作语言与样式)。
  // 数据收缩(删号/改窗)后当前页越界时回落到末页。
  useEffect(() => {
    if (acctData && accountPage > accountTotalPages) setAccountPage(accountTotalPages);
  }, [acctData, accountPage, accountTotalPages]);

  // ── 用户子页总览 KPI（窗口指标，服务端 summary 聚合全量,不受分页/搜索影响）──
  const userKpis = useMemo(() => ({
    accounts: acctSummary?.accounts ?? 0,
    admins: acctSummary?.admins ?? 0,
    disabled: acctSummary?.disabled ?? 0,
    loggedIn: acctSummary?.logged_in_window ?? 0,
    logins: acctSummary?.logins ?? 0,
    reads: acctSummary?.reads ?? 0,
    aiCalls: acctSummary?.ai_calls ?? 0,
    aiTokens: acctSummary?.ai_tokens ?? 0,
  }), [acctSummary]);
  const perDay = (n) => (days > 0 ? (n / days).toFixed(1) : '0');
  // 活跃用户 Top：按所选维度（阅读 / 登录）排行,服务端全量聚合。
  const activeUserRows = useMemo(() => {
    const rows = topMetric === 'reads' ? acctSummary?.top_reads : acctSummary?.top_logins;
    return (rows ?? []).map((r) => ({ name: r.username, value: r.value }));
  }, [acctSummary, topMetric]);

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
            <button onClick={() => setSub('engage')} className={`segmented-option ${sub === 'engage' ? 'segmented-option-active' : ''}`}><MessageSquare /> 消息</button>
          </div>
        </div>
      </div>

      {/* ══ 消息子页(v3.18 互通波:反馈收件箱 + 公告管理)════════════ */}
      {sub === 'engage' && (
        <div className="grid gap-4">
          <FeedbackInboxPanel showToast={showToast} />
          <AnnouncementsPanel showToast={showToast} />
        </div>
      )}

      {/* ══ 用户子页 ══════════════════════════════════════════════ */}
      {sub === 'user' && (
        <div>
          <section className="surface-card kpi-strip" aria-label="窗口活跃概览">
            <Kpi
              num={fmtNum(userKpis.accounts)}
              label="账户"
              sub={(
                <>
                  <button
                    type="button"
                    className={`kpi-sub-link ${acctRole === 'admin' ? 'is-on' : ''}`}
                    title={acctRole === 'admin' ? '取消管理员筛选' : '筛选管理员账户'}
                    onClick={() => setAcctRole((r) => (r === 'admin' ? '' : 'admin'))}
                  >
                    管理员 {userKpis.admins}
                  </button>
                  {userKpis.disabled > 0 && (
                    <>
                      {' · '}
                      <button
                        type="button"
                        className={`kpi-sub-link ${acctStatus === 'disabled' ? 'is-on' : ''}`}
                        title={acctStatus === 'disabled' ? '取消停用筛选' : '筛选停用账户'}
                        onClick={() => setAcctStatus((s) => (s === 'disabled' ? '' : 'disabled'))}
                      >
                        停用 {userKpis.disabled}
                      </button>
                    </>
                  )}
                </>
              )}
            />
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
                  height={Math.max(112, activeUserRows.length * 26)}
                  tickFormatter={truncLabel}
                  emptyHint={topMetric === 'reads' ? '窗口内还没有阅读记录' : '窗口内还没有登录记录'}
                />
              </section>
            </>
          )}

          <div className="zone-head">
            <span className="zone-title">账户管理</span>
            <span className="zone-acts">
              <button onClick={() => setCreateModalOpen(true)} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                <UserPlus className="h-4 w-4" /> 新建账户
              </button>
            </span>
          </div>

          <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
            {acctData === null ? (
              <p className="p-6 tiny-meta">加载中…</p>
            ) : (
              <>
                {acctTotal === 0 ? (
                  <p className="p-6 text-center tiny-meta">
                    {acctFiltersActive ? (
                      <>
                        没有匹配当前筛选的账户。
                        <button
                          type="button"
                          className="kpi-sub-link"
                          onClick={() => { setAcctRole(''); setAcctStatus(''); setAcctAi(''); setAccountQuery(''); }}
                        >
                          清除筛选
                        </button>
                      </>
                    ) : '还没有账户，用右上角「新建账户」创建第一个。'}
                  </p>
                ) : (
                  <>
                <div className="acct-scroll">
                  <table className="acct-table is-fixed">
                    <thead>
                      <tr>
                        <th className="acct-th" style={{ width: 40 }}>
                          <input
                            type="checkbox"
                            aria-label="全选本页账户"
                            checked={allPageSelected}
                            onChange={toggleAllPage}
                            className="h-4 w-4 cursor-pointer rounded align-middle"
                          />
                        </th>
                        <ThSearch label="用户" value={accountQuery} onChange={setAccountQuery} placeholder="搜索用户名" active={Boolean(acctQ)} />
                        <ThFilter label="角色" value={acctRole} onChange={setAcctRole} options={[['', '全部'], ['admin', '管理员'], ['user', '读者']]} width={96} />
                        <ThFilter label="状态" value={acctStatus} onChange={setAcctStatus} options={[['', '全部'], ['active', '启用'], ['disabled', '停用']]} width={80} />
                        <ThFilter label="AI" value={acctAi} onChange={setAcctAi} options={[['', '全部'], ['on', 'AI 已开'], ['off', 'AI 未开']]} width={88} />
                        <ThSort label="最近登录" k="last_login" sort={acctSort} order={acctOrder} onSort={handleSort} width={124} />
                        <ThSort label="登录" k="logins" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
                        <ThSort label="阅读" k="reads" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
                        <ThSort label="AI 调用" k="ai_calls" sort={acctSort} order={acctOrder} onSort={handleSort} num width={96} />
                        <ThSort label="订阅" k="subscriptions" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
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
                          <td onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              aria-label={`选择账户 ${account.username}`}
                              checked={selectedAccounts.has(account.username)}
                              onChange={() => toggleAccountSelection(account.username)}
                              className="h-4 w-4 cursor-pointer rounded align-middle"
                            />
                          </td>
                          <td>
                            <span className="acct-user">
                              <span className="acct-avatar avatar-letter" style={{ '--avatar-h': avatarHue(account.username) }}>{avatarInitial(account.username)}</span>
                              <span className="acct-name">{account.username}</span>
                            </span>
                          </td>
                          <td>
                            {account.role === 'admin'
                              ? <span className="sett-role-chip" style={{ color: 'var(--dorami-accent-ink)' }}><ShieldCheck className="h-3 w-3" /> 管理员</span>
                              : <span className="sett-role-chip">读者</span>}
                          </td>
                          <td>{account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>}</td>
                          <td>
                            {account.ai_beta_enabled
                              ? <span className="acct-ai-flag is-on" title="AI 已开启"><Zap /></span>
                              : <span className="acct-ai-flag" title="AI 未开启"><ZapOff /></span>}
                          </td>
                          <td><span className="acct-mono">{formatStamp(account.last_login_at)}</span></td>
                          <td className={`acct-n ${(account.logins || 0) ? '' : 'is-zero'}`}>{account.logins || '–'}</td>
                          <td className={`acct-n is-main ${(account.reads || 0) ? '' : 'is-zero'}`}>{account.reads || '–'}</td>
                          <td className={`acct-n ${(account.ai_calls || 0) ? '' : 'is-zero'}`}>{account.ai_calls || '–'}</td>
                          <td className="acct-n">{account.subscription_count ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* 批量操作条(v3.41 M05,返修改小图标组):勾选即现,动作走原子批量端点;
                    语义全靠图标 + title,与详情抽屉的动作图标同一套词汇。 */}
                {selectedAccounts.size > 0 && (
                  <div className="ledger-batchbar">
                    <span className="ledger-batch-n">{selectedAccounts.size} 个已选</span>
                    <span className="batch-acts">
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy || globalAi === false}
                        title={globalAi === false ? 'AI 功能总闸已关闭（AI 子页）' : '为已选账户开启 AI'}
                        onClick={() => handleBatch({ ai_beta_enabled: true }, { done: (n) => `已为 ${n} 个账户开启 AI` })}
                      >
                        <Zap />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy || globalAi === false}
                        title={globalAi === false ? 'AI 功能总闸已关闭（AI 子页）' : '为已选账户关闭 AI'}
                        onClick={() => handleBatch({ ai_beta_enabled: false }, { done: (n) => `已为 ${n} 个账户关闭 AI` })}
                      >
                        <ZapOff />
                      </button>
                      <span className="ai-divider" />
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="启用已选账户"
                        onClick={() => handleBatch({ is_active: true }, { done: (n) => `已启用 ${n} 个账户` })}
                      >
                        <Power />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="停用已选账户"
                        onClick={() => handleBatch(
                          { is_active: false },
                          {
                            confirmMsg: `确认停用选中的 ${selectedAccounts.size} 个账户？其登录会话将立即失效。`,
                            done: (n) => `已停用 ${n} 个账户`,
                          },
                        )}
                      >
                        <Ban />
                      </button>
                      <span className="ai-divider" />
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="将已选账户设为管理员"
                        onClick={() => handleBatch(
                          { role: 'admin' },
                          {
                            confirmMsg: `确认将选中的 ${selectedAccounts.size} 个账户设为管理员？管理员拥有采集、归档、账户与系统配置的全部权限。`,
                            done: (n) => `已将 ${n} 个账户设为管理员`,
                          },
                        )}
                      >
                        <ShieldCheck />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="将已选账户设为读者"
                        onClick={() => handleBatch(
                          { role: 'user' },
                          {
                            confirmMsg: `确认将选中的 ${selectedAccounts.size} 个账户设为读者？${selectedAccounts.has(currentUsername) ? '选中包含你自己，生效后你将立即被登出管理台。' : ''}`,
                            done: (n) => `已将 ${n} 个账户设为读者`,
                          },
                        )}
                      >
                        <ShieldOff />
                      </button>
                    </span>
                    <span className="flex-1" />
                    <button
                      type="button"
                      className="rowact-btn"
                      title="取消选择"
                      aria-label="取消选择"
                      onClick={() => setSelectedAccounts(new Set())}
                    >
                      <X />
                    </button>
                  </div>
                )}
                {accountTotalPages > 1 && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
                    <span className="tiny-meta">
                      共 {acctTotal} 个 · 第 {(accountPage - 1) * ACCOUNTS_PAGE_SIZE + 1}–{Math.min(accountPage * ACCOUNTS_PAGE_SIZE, acctTotal)} 个
                    </span>
                    <Pager page={accountPage} totalPages={accountTotalPages} onPage={setAccountPage} />
                  </div>
                )}
                  </>
                )}
              </>
            )}
          </section>

          {/* ── 操作审计(v3.19 多管理员):管理面写操作逐条落行,多管理员互相可查 ── */}
          <AdminAuditPanel days={days} showToast={showToast} />
        </div>
      )}

      {/* ══ 内容子页 ══════════════════════════════════════════════ */}
      {sub === 'content' && (
        <div>
          {/* 公开分享总闸:与 AI 总闸同形制。放「内容」而非「用户」——它管的是内容能否
              被摊到登录之外,和媒体库、X 接入同类(对外的内容出口)。 */}
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
                      { key: 'subs', name: '源订阅', color: C_SUBSCRIBE },
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

              {/* ── X API(社交源采集):按量付费开销观测面;凭据编辑已收敛到 设置 → 凭据 ── */}
              <div className="zone-head">
                <span className="zone-title">X API</span>
                <span className="zone-hint">社交源采集的按量付费开销(按返回资源计费,非请求次数)</span>
                {xQuota?.blocked && <span className="stamp stamp-bad">已达预算上限 · 停止抓取</span>}
                <div className="zone-acts">
                  <button
                    type="button"
                    className="model-chip"
                    title="前往设置编辑 X API 凭据"
                    onClick={() => onOpenCredentials?.()}
                  >
                    <i className={xStatus?.configured ? '' : 'is-off'} />凭据{' '}
                    <b>{xStatus?.bearer_token_set ? (xStatus.bearer_token_preview || '已配置') : '未配置'}</b>
                  </button>
                </div>
              </div>

              {xQuota && (
                <section className="surface-card kpi-strip" style={{ marginTop: 12 }} aria-label="X API 用量">
                  <Kpi
                    num={`$${Number(xQuota.estimated_cost_usd || 0).toFixed(2)}`}
                    label="本月开销"
                    sub={xQuota.month}
                    tone={xQuota.blocked ? 'bad' : undefined}
                  />
                  <Kpi num={`$${Number(xQuota.remaining_usd || 0).toFixed(2)}`} label="剩余额度" sub={`预算 $${xQuota.monthly_budget_usd}`} />
                  <Kpi num={fmtNum(xQuota.post_reads)} label="推文" sub="$0.005 / 条" />
                  <Kpi num={fmtNum(xQuota.media_reads)} label="图片 / 视频" sub="$0.005 / 个" />
                  <Kpi num={fmtNum(xQuota.note_reads)} label="长文" sub="$0.005 / 条" />
                  <Kpi num={fmtNum(xQuota.user_reads)} label="账号解析" sub="$0.010 / 次" />
                </section>
              )}

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

              {/* ── 用户自定源(v3.40):读者自助 RSS 源的治理与观测 ── */}
              <UserSourcesPanel showToast={showToast} />
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
        <div className="ledger-drawer-head acct-drawer-head">
          <span className="acct-avatar avatar-letter" style={{ '--avatar-h': avatarHue(detailUser) }}>{detailUser ? avatarInitial(detailUser) : ''}</span>
          <span className="ledger-drawer-title">{detailUser}</span>
          {detailData && (detailData.account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>)}
          {/* 就地管理动作(v3.41 M19,小图标形态):与批量条同一套图标词汇,语义挂 title */}
          {detailData && (
            <span className="drawer-acts">
              {globalAi !== false && (
                <button
                  type="button"
                  className={`rowact-btn ${detailData.account.ai_beta_enabled ? 'is-on' : ''}`}
                  title={detailData.account.ai_beta_enabled ? `关闭 ${detailUser} 的 AI` : `开启 ${detailUser} 的 AI`}
                  onClick={() => handleToggleAiBeta(detailData.account)}
                >
                  {detailData.account.ai_beta_enabled ? <Zap /> : <ZapOff />}
                </button>
              )}
              <button
                type="button"
                className={`rowact-btn ${detailData.account.role === 'admin' ? 'is-on' : ''}`}
                title={detailData.account.role === 'admin' ? `取消 ${detailUser} 的管理员身份` : `将 ${detailUser} 设为管理员`}
                onClick={() => handleToggleRole(detailData.account)}
              >
                {detailData.account.role === 'admin' ? <ShieldOff /> : <ShieldCheck />}
              </button>
              <button
                type="button"
                className="rowact-btn"
                title="重置密码"
                onClick={() => handleResetPassword(detailData.account)}
              >
                <KeyRound />
              </button>
              <button
                type="button"
                className="rowact-btn"
                title={detailData.account.is_active ? '停用账户' : '启用账户'}
                onClick={() => handleToggleActive(detailData.account)}
              >
                {detailData.account.is_active ? <Ban /> : <Power />}
              </button>
              <button
                type="button"
                className="rowact-btn is-danger"
                title="删除账户"
                onClick={() => handleDelete(detailData.account)}
              >
                <Trash2 />
              </button>
              <span className="ai-divider" />
            </span>
          )}
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
          <form ref={createPanelRef} role="dialog" aria-modal="true" aria-label="新建账户" tabIndex={-1} className="modal-panel max-w-md form-sheet" onClick={(e) => e.stopPropagation()} onSubmit={handleCreate}>
            <div className="form-sheet-head">
              <h3 className="card-title">新建账户</h3>
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
              <div className="form-sheet-field">
                <span className="form-label">角色</span>
                <div className="mini-seg" role="group" aria-label="账户角色">
                  {[['user', '读者'], ['admin', '管理员']].map(([role, label]) => (
                    <button
                      key={role}
                      type="button"
                      onClick={() => setNewRole(role)}
                      className={`mini-seg-btn ${newRole === role ? 'is-on' : ''}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {newRole === 'admin' && (
                  <p className="tiny-meta" style={{ marginTop: 6 }}>管理员拥有采集、归档、账户与系统配置的全部权限。</p>
                )}
              </div>
            </div>
            <div className="form-sheet-foot">
              <button type="button" onClick={() => setCreateModalOpen(false)} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
              <button type="submit" disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UserPlus className="h-3.5 w-3.5" />} 创建{newRole === 'admin' ? '管理员' : '读者'}账户
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
              <button type="button" onClick={() => setResetTarget(null)} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
              <button type="submit" disabled={resetBusy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
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
