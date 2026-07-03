import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Users,
  Database,
  Bookmark,
  Sparkles,
  Zap,
  KeyRound,
  Trash2,
  UserPlus,
  Loader2,
  ServerCog,
  Brain,
  Check,
  X,
  BarChart3,
  Rss,
  Heart,
  Search,
  Activity,
  Clock,
  CalendarClock,
  BookOpen,
  ChevronDown,
  Ban,
} from 'lucide-react';
import {
  fetchAdminAccounts,
  fetchAccountActivity,
  fetchAdminContent,
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
import { MultiSeriesArea, RankBars, BarList } from './charts/DashboardCharts';
import { pivotDaily, CATEGORICAL, C_READ, C_FAVORITE } from './charts/chartUtils';

const INPUT_CLS = 'w-full rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-2.5 text-sm text-[var(--dorami-ink)] placeholder:text-[var(--dorami-faint)]';

// 账户列表分页大小：超过即翻页，避免成百上千账户一次性平铺。
const ACCOUNTS_PAGE_SIZE = 15;

// KPI 数字的语义配色：登录/AI 拉开色相（蓝 vs 紫），详情页与外层同语义同色。
const KPI_COLOR = {
  active: 'text-emerald-600',
  login: 'text-sky-600',
  read: 'text-amber-600',
  ai: 'text-violet-600',
  subscription: 'text-teal-600',
};

// 用途标签：与后端 AiUsageRecord.purpose 对齐。
const PURPOSE_LABELS = {
  translate: '阅读器翻译',
  ask: '阅读器问答',
  daily_brief_map: '日报·概括',
  daily_brief_dedup: '日报·去重',
  daily_brief_reduce: '日报·汇编',
  source_config: '节点·配置',
  detail_profile: '节点·详情',
};

// 时间戳格式化：把 ISO 时间显示为「MM-DD HH:mm」，无值回落到占位符。
function formatStamp(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 大数字缩写：1234 → 1.2k，1234567 → 1.2M。
function fmtNum(n) {
  const v = Number(n || 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(v);
}

// 比率（0~1）→ 百分比整数。
function pct(x) {
  return `${Math.round(Number(x || 0) * 100)}%`;
}

// Y 轴类目截断：避免长名撑爆图表（仅 RankBars 的活跃用户 Top 仍用）。
const truncLabel = (s) => (typeof s === 'string' && s.length > 7 ? `${s.slice(0, 6)}…` : s);

// 图表小面板：统一的标题 + 图表容器。
function ChartPanel({ title, action, children }) {
  return (
    <div className="flex flex-col rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
      <div className="mb-3 flex items-center gap-3">
        <p className="micro-label text-slate-500">{title}</p>
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {/* flex-1 + 居中：面板被同行更高的图表撑高时，本图表在垂直方向居中而非顶贴 */}
      <div className="flex flex-1 flex-col justify-center">{children}</div>
    </div>
  );
}

function StatCard({ icon: Icon, label, value, sub, valueClass = 'text-slate-800' }) {
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
      <div className="flex items-center gap-2 text-slate-500">
        <Icon className="h-4 w-4" />
        <span className="micro-label">{label}</span>
      </div>
      <p className={`stat-number mt-2 ${valueClass}`}>{value}</p>
      {sub && <p className="tiny-meta mt-0.5">{sub}</p>}
    </div>
  );
}

export default function AdminOpsTab({ showToast }) {
  const confirm = useConfirm();
  const [sub, setSub] = useState('user'); // 子页：user | content | ai
  const [accounts, setAccounts] = useState(null);
  const [globalAi, setGlobalAi] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [accountQuery, setAccountQuery] = useState('');
  const [accountPage, setAccountPage] = useState(1);
  // 用户子页时间窗口（近 N 天）：驱动账户列表窗口指标与详情面板。
  const [userDays, setUserDays] = useState(30);
  // 活跃用户 Top 维度：阅读 | 登录。
  const [topMetric, setTopMetric] = useState('reads');

  // ── 单用户活动详情面板 ──
  const [detailUser, setDetailUser] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [loginListOpen, setLoginListOpen] = useState(false); // 详情页「最近登录」展开列表
  const detailModal = useModalTransition(Boolean(detailUser));

  // ── 模型配置（日报 + 阅读器 AI 共用的全局唯一配置）──
  const [llmStatus, setLlmStatus] = useState(null);
  const [llmForm, setLlmForm] = useState({ base_url: '', model: '', api_key: '', temperature: 0.3, max_tokens: 4096 });
  const [savingLlm, setSavingLlm] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);
  const [llmModalOpen, setLlmModalOpen] = useState(false);
  const llmModal = useModalTransition(llmModalOpen);

  // ── AI 用量看板 ──
  const [usage, setUsage] = useState(null);
  const [usageDays, setUsageDays] = useState(7);

  // ── 内容看板（各源内容健康 + 收藏热度榜）──
  const [content, setContent] = useState(null);

  // ── 新建账户弹窗 ──
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const createModal = useModalTransition(createModalOpen);

  // ── 重置密码弹窗（取代 window.prompt：不回显明文、与全站 Modal 体系一致）──
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetBusy, setResetBusy] = useState(false);
  const resetModal = useModalTransition(Boolean(resetTarget));

  const loadLlm = useCallback(() => getLLMConfig().then((d) => {
    setLlmStatus(d);
    setLlmForm((f) => ({ ...f, base_url: d.base_url || '', model: d.model || '', temperature: d.temperature ?? 0.3, max_tokens: d.max_tokens ?? 4096, api_key: '' }));
  }).catch(() => {}), []);

  const loadUsage = useCallback((days) => fetchAiUsage(days).then(setUsage).catch(() => {}), []);

  const loadContent = useCallback(() => fetchAdminContent().then(setContent).catch(() => {}), []);

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
      setAccounts(await fetchAdminAccounts(userDays));
    } catch (error) {
      showToast(error.message || '加载账户失败', 'error');
      setAccounts((prev) => prev ?? []);
    }
  }, [userDays, showToast]);

  useEffect(() => { loadGlobals(); loadLlm(); loadContent(); }, [loadGlobals, loadLlm, loadContent]);
  // 账户列表随时间窗口变化重载（窗口指标按 userDays 聚合）。
  useEffect(() => { reloadAccounts(); }, [reloadAccounts]);
  useEffect(() => { loadUsage(usageDays); }, [loadUsage, usageDays]);

  // 配置 / 新建 / 详情 / 重置密码弹窗打开时锁定页面滚动。
  useEffect(() => {
    if (!llmModalOpen && !createModalOpen && !detailUser && !resetTarget) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [llmModalOpen, createModalOpen, detailUser, resetTarget]);

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
      setLlmModalOpen(false);
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
  // 用途键预先映射为中文标签，作为系列名直接进图例/Tooltip。
  const dayPurpose = useMemo(
    () => (usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [usage],
  );
  const dayUser = useMemo(() => usage?.by_day_user ?? [], [usage]);
  const callsDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, usageDays, 'purpose', 'calls'),
    user: pivotDaily(dayUser, usageDays, 'username', 'calls'),
  }), [dayPurpose, dayUser, usageDays]);
  const tokensDatasets = useMemo(() => ({
    purpose: pivotDaily(dayPurpose, usageDays, 'purpose', 'total_tokens'),
    user: pivotDaily(dayUser, usageDays, 'username', 'total_tokens'),
  }), [dayPurpose, dayUser, usageDays]);

  // ── 内容看板图表数据 ──
  // 各源热度：阅读 / 收藏 / 订阅三指标合一，全量（有任一互动即入榜），可滚动条形列表承载（全名 + 多色）。
  const contentSourceRows = useMemo(
    () => [...(content?.sources ?? [])]
      .filter((s) => (s.read_count || 0) + (s.favorite_count || 0) + (s.subscription_count || 0) > 0)
      .sort((a, b) => (b.favorite_count - a.favorite_count) || (b.read_count - a.read_count) || (b.subscription_count - a.subscription_count))
      .map((s) => ({ name: s.name, reads: s.read_count || 0, favorites: s.favorite_count || 0, subs: s.subscription_count || 0 })),
    [content],
  );
  const topArticleRows = useMemo(
    () => (content?.top_articles ?? []).slice(0, 10).map((a) => ({ title: a.title || '无标题', fav: a.favorite_count })),
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
      loggedIn: list.filter((a) => a.logged_in_window).length,
      logins: list.reduce((s, a) => s + (a.logins || 0), 0),
      reads: list.reduce((s, a) => s + (a.reads || 0), 0),
      aiCalls: list.reduce((s, a) => s + (a.ai_calls || 0), 0),
    };
  }, [accounts]);
  // 活跃用户 Top：按所选维度（阅读 / 登录）排行。
  const activeUserRows = useMemo(
    () => [...(accounts ?? [])]
      .filter((a) => (a[topMetric] || 0) > 0)
      .sort((a, b) => (b[topMetric] || 0) - (a[topMetric] || 0))
      .slice(0, 8)
      .map((a) => ({ name: a.username, value: a[topMetric] || 0 })),
    [accounts, topMetric],
  );

  // ── 单用户详情：打开面板并拉取窗口活动 ──
  const openDetail = useCallback(async (username) => {
    setDetailUser(username);
    setDetailData(null);
    setLoginListOpen(false);
    try {
      setDetailData(await fetchAccountActivity(username, userDays));
    } catch (error) {
      showToast(error.message || '获取用户详情失败', 'error');
    }
  }, [userDays, showToast]);

  // 详情面板图表数据：每日 AI 用量（按用途堆叠，calls / tokens 两套）+ 用途构成。
  const detailDayPurpose = useMemo(
    () => (detailData?.usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [detailData],
  );
  const detailWindow = detailData?.usage?.window_days ?? userDays;
  const detailDatasets = useMemo(() => ({
    calls: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'calls'),
    tokens: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'total_tokens'),
  }), [detailDayPurpose, detailWindow]);
  // 各源互动：阅读 + 收藏两维度（替代原「使用方式构成」——后者已被每日 AI 用量图涵盖）。
  // 全量（后端已按 reads 降序），由可滚动条形列表承载，源多也不撑高。
  const detailEngagementRows = useMemo(
    () => (detailData?.source_engagement ?? []).map((s) => ({ name: s.name || s.source_id, reads: s.reads, favorites: s.favorites })),
    [detailData],
  );

  return (
    <div>
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h1 className="page-title">运维管理</h1>
          <p className="page-subtitle mt-3 max-w-3xl">
            {sub === 'user' && '管理读者账号：创建、停用、AI 功能授权、重置密码。'}
            {sub === 'content' && '各源与文章的受欢迎度看板：订阅与收藏热度。'}
            {sub === 'ai' && 'AI 总开关、模型配置与用量监控（日报与阅读器 AI 共用一套模型）。'}
          </p>
        </div>
        <div className="page-actions">
          <div className="segmented-control">
            <button onClick={() => setSub('user')} className={`segmented-option ${sub === 'user' ? 'segmented-option-active' : ''}`}><Users /> 用户</button>
            <button onClick={() => setSub('content')} className={`segmented-option ${sub === 'content' ? 'segmented-option-active' : ''}`}><Database /> 内容</button>
            <button onClick={() => setSub('ai')} className={`segmented-option ${sub === 'ai' ? 'segmented-option-active' : ''}`}><Brain /> AI</button>
          </div>
        </div>
      </div>

      <div className="mt-6">
        {/* ══ AI 子页 ══════════════════════════════════════════════ */}
        {sub === 'ai' && (
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden animate-in fade-in">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-violet-500" />
            <h3 className="section-title">AI 配置与用量</h3>
            <span className="tiny-meta">日报与阅读器 AI 共用一套模型</span>
            {/* 用户 AI 功能：状态灯 + 开关（取代原「总闸」整块） */}
            <div className="ml-auto flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${globalAi ? 'bg-emerald-500' : 'bg-amber-500'}`} title={globalAi ? '用户 AI 功能：开启' : '用户 AI 功能：关闭（全员暂停）'} />
              <span className="micro-label text-slate-500">用户 AI 功能</span>
              <button
                onClick={handleToggleGlobalAi}
                disabled={globalAi === null}
                role="switch"
                aria-checked={!!globalAi}
                aria-label="用户 AI 功能开关"
                className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${globalAi ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-[var(--dorami-raised)]'}`}
              >
                <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${globalAi ? 'translate-x-4' : 'translate-x-0.5'}`} />
              </button>
            </div>
          </div>
          <div className="p-6">
            {/* 模型概览行 */}
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-bold text-slate-700">
                  <Brain className="h-4 w-4 text-slate-500" /> 大模型
                  <span className={`h-1.5 w-1.5 rounded-full ${llmStatus?.api_key_set ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                </p>
                <p className="tiny-meta mt-1 truncate font-mono">
                  {llmStatus?.api_key_set
                    ? `${llmStatus.base_url || '—'} · ${llmStatus.model || '—'}`
                    : '尚未配置 · 配置后日报与阅读器 AI 方可使用'}
                </p>
              </div>
              <button onClick={() => setLlmModalOpen(true)} className="action-button action-button-secondary shrink-0">
                <ServerCog className="h-4 w-4" /> 配置模型
              </button>
            </div>

            {/* 用量子区 */}
            <div className="mt-6 border-t border-[var(--dorami-border)] pt-6">
              <div className="mb-4 flex items-center gap-3">
                <p className="flex items-center gap-2 text-sm font-bold text-slate-700">
                  <BarChart3 className="h-4 w-4 text-slate-500" /> AI 用量
                </p>
                <select
                  value={usageDays}
                  onChange={(e) => setUsageDays(Number(e.target.value))}
                  className="form-input ml-auto w-auto py-1.5 text-xs"
                >
                  <option value={7}>近 7 天</option>
                  <option value={14}>近 14 天</option>
                  <option value={30}>近 30 天</option>
                  <option value={90}>近 90 天</option>
                </select>
              </div>
              {!usage || usage.totals.calls === 0 ? (
              <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                <BarChart3 className="mx-auto mb-1 h-4 w-4 text-slate-500" />
                近 {usageDays} 天还没有 AI 调用记录，触发一次翻译/问答或日报生成后这里会出现统计。
              </p>
            ) : (
              <>
                {/* 头条 KPI（数字明示）*/}
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <StatCard icon={Sparkles} label="总调用" value={fmtNum(usage.totals.calls)} valueClass={KPI_COLOR.ai} />
                  <StatCard icon={BarChart3} label="总 tokens" value={fmtNum(usage.totals.total_tokens)} valueClass="text-sky-600" />
                  <StatCard icon={BarChart3} label="输入 tokens" value={fmtNum(usage.totals.prompt_tokens)} valueClass={KPI_COLOR.read} />
                  <StatCard icon={BarChart3} label="输出 tokens" value={fmtNum(usage.totals.completion_tokens)} valueClass={KPI_COLOR.active} />
                </div>

                {/* 时间序列主图：每日调用 / 每日 tokens，各自可切「按用途 / 按用户」拆系列 */}
                <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
                  <MultiSeriesArea title="每日调用次数" datasets={callsDatasets} />
                  <MultiSeriesArea title="每日 tokens" datasets={tokensDatasets} defaultDim="user" />
                </div>
                </>
              )}
            </div>
          </div>
        </div>
        )}

        {/* ══ 用户子页 ════════════════════════════════════════════ */}
        {sub === 'user' && (
        <div className="space-y-6 animate-in fade-in">
        {/* ── 上半：活跃概览（数据看板）── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-sky-500" />
            <h3 className="section-title">活跃概览</h3>
            <span className="tiny-meta">读者登录 / 阅读 / AI 使用一览</span>
            <select
              value={userDays}
              onChange={(e) => setUserDays(Number(e.target.value))}
              className="action-button action-button-secondary ml-auto shrink-0 cursor-pointer"
              aria-label="活跃度统计时间窗口"
            >
              <option value={7}>近 7 天</option>
              <option value={14}>近 14 天</option>
              <option value={30}>近 30 天</option>
              <option value={90}>近 90 天</option>
            </select>
          </div>
          <div className="p-6 space-y-5">
            {/* 总览 KPI（窗口活跃度）：人数（活跃用户）与次数（登录/阅读/AI）分列、彩色数字 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <StatCard icon={Users} label="读者账户" value={fmtNum(userKpis.readers)} />
              <StatCard icon={Activity} label={`近 ${userDays} 天活跃`} value={fmtNum(userKpis.loggedIn)} valueClass={KPI_COLOR.active} />
              <StatCard icon={CalendarClock} label={`近 ${userDays} 天登录`} value={fmtNum(userKpis.logins)} valueClass={KPI_COLOR.login} />
              <StatCard icon={BookOpen} label={`近 ${userDays} 天阅读`} value={fmtNum(userKpis.reads)} valueClass={KPI_COLOR.read} />
              <StatCard icon={Sparkles} label={`近 ${userDays} 天 AI 调用`} value={fmtNum(userKpis.aiCalls)} valueClass={KPI_COLOR.ai} />
            </div>
            {(userKpis.reads + userKpis.logins) > 0 && (
              <ChartPanel
                title={`活跃用户 Top · 近 ${userDays} 天`}
                action={(
                  <div className="inline-flex rounded-[var(--r-control)] border border-[var(--dorami-border)] p-0.5">
                    {[['reads', '阅读'], ['logins', '登录']].map(([k, lbl]) => (
                      <button
                        key={k}
                        onClick={() => setTopMetric(k)}
                        className={`rounded-[var(--r-sm)] px-2 py-0.5 micro-label transition-colors ${topMetric === k ? 'bg-[var(--dorami-wash)] text-indigo-600' : 'text-slate-500 hover:text-slate-700'}`}
                      >
                        {lbl}
                      </button>
                    ))}
                  </div>
                )}
              >
                <RankBars
                  rows={activeUserRows}
                  labelKey="name"
                  valueKey="value"
                  name={topMetric === 'reads' ? '阅读' : '登录'}
                  height={Math.max(120, activeUserRows.length * 28)}
                  tickFormatter={truncLabel}
                  colorByIndex
                  emptyHint={topMetric === 'reads' ? '窗口内还没有阅读记录' : '窗口内还没有登录记录'}
                />
              </ChartPanel>
            )}
          </div>
        </div>

        {/* ── 下半：账户管理（操作）── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-indigo-500" />
            <h3 className="section-title">账户管理</h3>
            <span className="tiny-meta">停用 / 删除会立即让对应账户的会话失效</span>
            <button onClick={() => setCreateModalOpen(true)} className="action-button action-button-primary ml-auto shrink-0">
              <UserPlus className="h-4 w-4" /> 新建账户
            </button>
          </div>
          <div className="p-6">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm font-bold text-slate-700">
                  现有账户
                  {accounts ? <span className="ml-1.5 font-bold text-slate-500">{accounts.length}</span> : null}
                </p>
                {accounts && accounts.length > 0 && (
                  <div className="relative w-full sm:w-64">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                    <input
                      value={accountQuery}
                      onChange={(e) => { setAccountQuery(e.target.value); setAccountPage(1); }}
                      placeholder="搜索用户名"
                      className="form-input w-full pl-9"
                    />
                  </div>
                )}
              </div>
              {accounts === null ? (
                <p className="tiny-meta">加载中…</p>
              ) : accounts.length === 0 ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                  还没有账户，用上方「新建账户」创建第一个。
                </p>
              ) : filteredAccounts.length === 0 ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                  没有匹配「{accountQuery.trim()}」的账户。
                </p>
              ) : (
                <>
                <div className="space-y-2">
                  {pagedAccounts.map((account) => (
                    <div
                      key={account.username}
                      role="button"
                      tabIndex={0}
                      onClick={() => openDetail(account.username)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(account.username); } }}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] px-3 py-2.5 cursor-pointer transition-colors hover:border-[var(--dorami-blue)]"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-bold text-slate-800">{account.username}</span>
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 micro-label text-slate-500">读者</span>
                          {!account.is_active && <span className="rounded bg-rose-50 px-1.5 py-0.5 micro-label text-rose-500">已停用</span>}
                          {account.ai_beta_enabled && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 micro-label text-indigo-500">
                              <Zap className="h-3 w-3" /> AI Beta
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 tiny-meta text-slate-500">
                          <span className="inline-flex items-center gap-1">
                            <KeyRound className="h-3 w-3" /> 登录 {formatStamp(account.last_login_at)}
                            {(account.logins ?? 0) > 0 && <span className="text-slate-700">· {account.logins} 次</span>}
                          </span>
                          <span className="inline-flex items-center gap-1"><BookOpen className="h-3 w-3" /> 阅读 {account.reads ?? 0} 次</span>
                          <span className="inline-flex items-center gap-1"><Sparkles className="h-3 w-3" /> AI {account.ai_calls ?? 0} 次</span>
                          <span className="inline-flex items-center gap-1"><Bookmark className="h-3 w-3" /> 订阅 {account.subscription_count ?? 0}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                        <button onClick={() => handleToggleActive(account)} className="action-button action-button-secondary text-xs">
                          {account.is_active ? '停用' : '启用'}
                        </button>
                        {globalAi === false ? (
                          // 总闸关闭：明确的失效态（去按钮外观 + 灰化 + 禁用光标 + 禁止标），一眼可辨不可操作
                          <span
                            title="AI 功能总闸已关闭（运维管理 · AI 子页），单账户开关暂不可用"
                            className="action-button text-xs cursor-not-allowed select-none border-dashed border-[var(--dorami-border)] bg-[var(--dorami-well)] text-[var(--dorami-faint)]"
                          >
                            <Ban className="h-3.5 w-3.5" /> AI 总闸已关
                          </span>
                        ) : (
                          <button
                            onClick={() => handleToggleAiBeta(account)}
                            className="action-button action-button-secondary text-xs"
                          >
                            <Zap className="h-3.5 w-3.5" /> {account.ai_beta_enabled ? '关闭 AI' : '开启 AI'}
                          </button>
                        )}
                        <button onClick={() => handleResetPassword(account)} className="action-button action-button-secondary text-xs">
                          <KeyRound className="h-3.5 w-3.5" /> 重置密码
                        </button>
                        <button onClick={() => handleDelete(account)} className="action-button action-button-danger text-xs">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {accountTotalPages > 1 && (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                    <span className="tiny-meta">
                      共 {filteredAccounts.length} 个 · 第 {(accountSafePage - 1) * ACCOUNTS_PAGE_SIZE + 1}–{Math.min(accountSafePage * ACCOUNTS_PAGE_SIZE, filteredAccounts.length)} 个
                    </span>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => setAccountPage((p) => Math.max(1, p - 1))}
                        disabled={accountSafePage <= 1}
                        className="action-button action-button-secondary text-xs"
                      >
                        上一页
                      </button>
                      <span className="rounded-[var(--r-control)] border border-[var(--dorami-border)] px-2.5 py-1 tiny-meta font-bold text-slate-500">
                        {accountSafePage} / {accountTotalPages}
                      </span>
                      <button
                        type="button"
                        onClick={() => setAccountPage((p) => Math.min(accountTotalPages, p + 1))}
                        disabled={accountSafePage >= accountTotalPages}
                        className="action-button action-button-secondary text-xs"
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                )}
                </>
              )}
            </div>
          </div>
        </div>
        )}

        {/* ══ 内容子页 ════════════════════════════════════════════ */}
        {sub === 'content' && (
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden animate-in fade-in">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-rose-500" />
            <h3 className="section-title">内容看板</h3>
            <span className="tiny-meta">哪些源、哪些内容受欢迎</span>
          </div>
          <div className="p-6">
            {!content ? (
              <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" />
                正在加载内容统计…
              </p>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                  <StatCard icon={Rss} label="内容源" value={fmtNum(content.totals.sources)} />
                  <StatCard icon={Database} label="归档文章" value={fmtNum(content.totals.articles)} valueClass="text-sky-600" />
                  <StatCard icon={BookOpen} label="阅读总数" value={fmtNum(content.totals.reads)} valueClass={KPI_COLOR.read} />
                  <StatCard icon={Heart} label="收藏总数" value={fmtNum(content.totals.favorites)} valueClass="text-rose-600" />
                  <StatCard icon={BarChart3} label="向量化率" value={pct(content.totals.vectorized_rate)} valueClass={KPI_COLOR.active} />
                </div>

                {/* 各源热度（阅读/收藏/订阅三指标）+ 文章收藏榜：全名可滚动列表，多色 */}
                <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
                  <ChartPanel title={`各源 · 文章阅读 / 文章收藏 / 源订阅${contentSourceRows.length ? ` · ${contentSourceRows.length} 个源` : ''}`}>
                    <BarList
                      rows={contentSourceRows}
                      nameKey="name"
                      metrics={[
                        { key: 'reads', name: '文章阅读', color: C_READ },
                        { key: 'favorites', name: '文章收藏', color: C_FAVORITE },
                        { key: 'subs', name: '源订阅', color: CATEGORICAL[2] },
                      ]}
                      emptyHint="还没有阅读 / 收藏 / 订阅记录"
                    />
                  </ChartPanel>
                  <ChartPanel title={`文章 · 收藏${topArticleRows.length ? ` · TOP ${topArticleRows.length}` : ''}`}>
                    <BarList
                      rows={topArticleRows}
                      nameKey="title"
                      metrics={[{ key: 'fav', name: '收藏', color: C_FAVORITE }]}
                      colorByIndex
                      emptyHint="还没有任何收藏记录，读者在阅读器收藏文章后这里会出现热度榜。"
                    />
                  </ChartPanel>
                </div>
              </>
            )}
          </div>
        </div>
        )}
      </div>

      {/* ── 模型配置弹窗（Portal 到 body，避开变换祖先造成的 fixed 错位） ── */}
      {llmModal.mounted && createPortal(
        <div className={`modal-overlay ${llmModal.closing ? 'is-closing' : ''}`} onClick={() => setLlmModalOpen(false)}>
          <div className="modal-panel max-w-xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex items-center justify-between bg-[var(--dorami-well)]">
              <h3 className="card-title flex items-center gap-2">
                <Brain className="w-5 h-5 text-indigo-500" /> 模型配置
              </h3>
              <button onClick={() => setLlmModalOpen(false)} className="text-slate-500 hover:text-slate-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6">
              <p className="tiny-meta mb-4">日报与阅读器 AI 共用 · OpenAI 兼容协议（/chat/completions）· API Key 不回显</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <p className="form-label">Base URL</p>
                  <input value={llmForm.base_url} onChange={(e) => updateLlm('base_url', e.target.value)} placeholder="https://api.deepseek.com/v1" className={`${INPUT_CLS} font-mono`} />
                </div>
                <div>
                  <p className="form-label">模型</p>
                  <input value={llmForm.model} onChange={(e) => updateLlm('model', e.target.value)} placeholder="deepseek-chat" className={`${INPUT_CLS} font-mono`} />
                </div>
              </div>
              <div className="mt-3">
                <p className="form-label">
                  API Key
                  {llmStatus?.api_key_set ? <span className="ml-1 text-emerald-500 normal-case">已配置（{llmStatus.api_key_preview}）</span> : <span className="ml-1 text-amber-500 normal-case">未配置</span>}
                </p>
                <input type="password" value={llmForm.api_key} onChange={(e) => updateLlm('api_key', e.target.value)} placeholder={llmStatus?.api_key_set ? '留空表示不修改' : 'sk-...'} className={`${INPUT_CLS} font-mono`} />
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="form-label">Temperature</p>
                  <input type="number" step="0.1" min="0" max="2" value={llmForm.temperature} onChange={(e) => updateLlm('temperature', e.target.value)} className={INPUT_CLS} />
                </div>
                <div>
                  <p className="form-label">Max Tokens</p>
                  <input type="number" step="256" min="256" value={llmForm.max_tokens} onChange={(e) => updateLlm('max_tokens', e.target.value)} className={INPUT_CLS} />
                </div>
              </div>
            </div>
            <div className="px-6 py-4 bg-[var(--dorami-soft)] border-t border-[var(--dorami-border)] flex items-center gap-3">
              <button onClick={handleSaveLlm} disabled={savingLlm} className="action-button action-button-primary text-xs">
                {savingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 保存
              </button>
              <button onClick={handleTestLlm} disabled={testingLlm || savingLlm || !canTestLlm} className="action-button action-button-secondary text-xs">
                {testingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />} 测试连接
              </button>
              <span className="tiny-meta">{canTestLlm ? '测试会自动保存当前配置' : '填写 Base URL / 模型 / API Key 后可测试'}</span>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* ── 新建账户弹窗（Portal 到 body，避开变换祖先造成的 fixed 错位） ── */}
      {createModal.mounted && createPortal(
        <div className={`modal-overlay ${createModal.closing ? 'is-closing' : ''}`} onClick={() => setCreateModalOpen(false)}>
          <form className="modal-panel max-w-md" onClick={(e) => e.stopPropagation()} onSubmit={handleCreate}>
            <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex items-center justify-between bg-[var(--dorami-well)]">
              <h3 className="card-title flex items-center gap-2">
                <UserPlus className="w-5 h-5 text-indigo-500" /> 新建账户
              </h3>
              <button type="button" onClick={() => setCreateModalOpen(false)} className="text-slate-500 hover:text-slate-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-3">
              <div>
                <p className="form-label">用户名</p>
                <input value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="用户名" autoComplete="off" className="form-input w-full" />
              </div>
              <div>
                <p className="form-label">初始密码</p>
                <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="至少 6 位" autoComplete="new-password" className="form-input w-full" />
              </div>
            </div>
            <div className="px-6 py-4 bg-[var(--dorami-soft)] border-t border-[var(--dorami-border)] flex items-center justify-end">
              <button type="submit" disabled={busy} className="action-button action-button-primary text-xs">
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
          <form className="modal-panel max-w-md" onClick={(e) => e.stopPropagation()} onSubmit={handleResetSubmit}>
            <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex items-center justify-between bg-[var(--dorami-well)]">
              <h3 className="card-title flex items-center gap-2">
                <KeyRound className="w-5 h-5 text-indigo-500" /> 重置密码
              </h3>
              <button type="button" onClick={() => setResetTarget(null)} className="text-slate-500 hover:text-slate-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-3">
              <p className="tiny-meta">为账户「{resetTarget?.username}」设置新密码，设置后该账户需用新密码登录。</p>
              <div>
                <p className="form-label">新密码</p>
                <input
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
            <div className="px-6 py-4 bg-[var(--dorami-soft)] border-t border-[var(--dorami-border)] flex items-center justify-end">
              <button type="submit" disabled={resetBusy} className="action-button action-button-primary text-xs">
                {resetBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />} 保存新密码
              </button>
            </div>
          </form>
        </div>,
        document.body,
      )}

      {/* ── 单用户活动详情面板（Portal 到 body） ── */}
      {detailModal.mounted && createPortal(
        <div className={`modal-overlay ${detailModal.closing ? 'is-closing' : ''}`} onClick={() => setDetailUser(null)}>
          <div className="modal-panel max-w-3xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex items-center justify-between bg-[var(--dorami-well)]">
              <h3 className="card-title flex items-center gap-2">
                <Activity className="w-5 h-5 text-indigo-500" />
                {detailUser}
                <span className="rounded bg-slate-100 px-1.5 py-0.5 micro-label text-slate-500">读者</span>
                {detailData && !detailData.account.is_active && <span className="rounded bg-rose-50 px-1.5 py-0.5 micro-label text-rose-500">已停用</span>}
                {detailData?.account.ai_beta_enabled && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 micro-label text-indigo-500"><Zap className="h-3 w-3" /> AI Beta</span>
                )}
              </h3>
              <button onClick={() => setDetailUser(null)} className="text-slate-500 hover:text-slate-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 max-h-[70vh] overflow-y-auto">
              {!detailData ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center tiny-meta">
                  <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载活动详情…
                </p>
              ) : (
                <>
                  {/* 账户与订阅概况 */}
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {/* 登录：点击展开最近若干次登录时间 */}
                    <button
                      type="button"
                      onClick={() => detailData.logins.recent.length > 0 && setLoginListOpen((o) => !o)}
                      className={`rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4 text-left ${detailData.logins.recent.length > 0 ? 'cursor-pointer transition-colors hover:border-[var(--dorami-blue)]' : 'cursor-default'}`}
                    >
                      <div className="flex items-center gap-2 text-slate-500">
                        <Clock className="h-4 w-4" />
                        <span className="micro-label">近 {detailWindow} 天登录</span>
                        {detailData.logins.recent.length > 0 && (
                          <ChevronDown className={`ml-auto h-3.5 w-3.5 transition-transform ${loginListOpen ? 'rotate-180' : ''}`} />
                        )}
                      </div>
                      <p className={`stat-number mt-2 ${KPI_COLOR.login}`}>{fmtNum(detailData.logins.count)}</p>
                      <p className="tiny-meta mt-0.5">最近 {formatStamp(detailData.account.last_login_at)}</p>
                    </button>
                    <StatCard icon={BookOpen} label={`近 ${detailWindow} 天阅读`} value={fmtNum(detailData.reads.total)} sub={`${detailData.reads.by_source.length} 个源`} valueClass={KPI_COLOR.read} />
                    <StatCard icon={Sparkles} label={`近 ${detailWindow} 天 AI`} value={fmtNum(detailData.usage.totals.calls)} sub={`累计 ${(detailData.account.ai_translate_count || 0) + (detailData.account.ai_ask_count || 0)} 次`} valueClass={KPI_COLOR.ai} />
                    <StatCard icon={Bookmark} label="订阅来源" value={fmtNum(detailData.account.subscription_count)} sub={`创建 ${formatStamp(detailData.account.created_at)}`} valueClass={KPI_COLOR.subscription} />
                  </div>

                  {/* 最近登录时间列表（展开） */}
                  {loginListOpen && detailData.logins.recent.length > 0 && (
                    <div className="mt-3 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-4">
                      <p className="micro-label mb-2 text-slate-500">最近 {detailData.logins.recent.length} 次登录</p>
                      <div className="flex flex-wrap gap-2">
                        {detailData.logins.recent.map((at, i) => (
                          <span key={`${at}-${i}`} className="inline-flex items-center gap-1 rounded bg-white dark:bg-[var(--dorami-surface)] px-2 py-1 tiny-meta text-slate-700">
                            <Clock className="h-3 w-3 text-slate-500" /> {formatStamp(at)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {(detailData.usage.totals.calls === 0 && detailData.reads.total === 0 && detailData.favorites_total === 0) ? (
                    <p className="mt-5 rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                      <Activity className="mx-auto mb-1 h-4 w-4 text-slate-500" />
                      近 {detailWindow} 天该用户没有阅读 / 收藏 / AI 调用记录。
                    </p>
                  ) : (
                    <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
                      <ChartPanel title={`各源 · 文章阅读 / 文章收藏${detailEngagementRows.length ? ` · ${detailEngagementRows.length} 个源` : ''}`}>
                        <BarList
                          rows={detailEngagementRows}
                          nameKey="name"
                          metrics={[{ key: 'reads', name: '文章阅读', color: C_READ }, { key: 'favorites', name: '文章收藏', color: C_FAVORITE }]}
                          emptyHint="窗口内无阅读、且无收藏记录"
                        />
                      </ChartPanel>
                      <MultiSeriesArea
                        title="每日 AI 用量"
                        datasets={detailDatasets}
                        dims={[['calls', '调用'], ['tokens', 'tokens']]}
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
