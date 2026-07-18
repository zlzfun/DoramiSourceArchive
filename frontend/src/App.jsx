import { useCallback, useState, useEffect, useMemo, useRef, lazy, Suspense } from 'react';
import {
  BarChart2,
  BookOpen,
  Bot,
  CloudDownload,
  Database,
  History,
  Loader2,
  LogOut,
  Moon,
  Plug2,
  Settings,
  ShieldCheck,
  Sun,
  X,
} from 'lucide-react';
import Toast from './components/Toast';
import SettingsModal from './components/SettingsModal';
import LoginScreen from './components/LoginScreen';
import BrandLogoImage from './components/BrandLogoImage';
import { useTheme } from './theme';
import { fetchAuthSession, fetchFetchers, fetchRuntimeInfo, loginAdmin, logoutAdmin } from './api';
import RunningWidget from './components/RunningWidget';
import { useRunningProgress } from './hooks/useRunningProgress';

// Tab 组件按路由惰性加载：各自独立 chunk，登录页与读者态不再下载用不到的重依赖
// （AdminOpsTab→recharts、ReaderTab/MCPTab→react-markdown）。每个 Tab 挂在独立
// Suspense 边界内，故加载新 Tab 不会波及已挂载 Tab（避免切换时整屏闪 fallback）。
const DataTab = lazy(() => import('./components/DataTab'));
const FetchTab = lazy(() => import('./components/FetchTab'));
const VectorTab = lazy(() => import('./components/VectorTab'));
const FetchRunsTab = lazy(() => import('./components/FetchRunsTab'));
const MCPTab = lazy(() => import('./components/MCPTab'));
const AdminOpsTab = lazy(() => import('./components/AdminOpsTab'));
const ReaderTab = lazy(() => import('./components/ReaderTab'));

// Tab chunk 加载期的占位（与顶层 checking-state 同语汇，克制不喧宾夺主）。
function TabFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
    </div>
  );
}

// ── 导航 / 历史锚点 ──
// 把「标签 + 子视图」镜像到 URL hash（#/runs/history），跨页跳转的聚焦上下文存在 history.state 里。
// 让浏览器「返回」能逐级退回：子视图切换 → 标签切换 → 跨页跳转的原位。
const ALL_TABS = ['reader', 'data', 'fetch', 'runs', 'vector', 'mcp', 'admin'];
// runs 的双视图已随运行波(调度台)退役:旧书签 #/runs/history、#/runs/jobs 在
// hashToRoute 里因 runs 不再是 SUBVIEW 而自然归一到 #/runs,无需特判。
const TAB_DEFAULT_VIEW = { fetch: 'catalog' };
const SUBVIEW_TABS = new Set(['fetch']);

// 运行能力的非乐观初始/重置值：能力未知（未登录 / 登出 / 会话过期）时按「都未启用」，
// 避免在 fetchRuntimeInfo 返回前把 collector 界面闪给读者。初始化与两处重置共用同一形状，
// 防字段遗漏（如 ai_beta_enabled/llm_configured 曾在重置时丢失）。
const INITIAL_RUNTIME = {
  role: 'all',
  collector_enabled: false,
  reader_enabled: false,
  rag_enabled: false,
  ai_beta_enabled: false,
  llm_configured: false,
};

function defaultViews() {
  return { fetch: 'catalog' };
}

function routeToHash(tab, views) {
  if (SUBVIEW_TABS.has(tab) && views[tab]) return `#/${tab}/${views[tab]}`;
  return `#/${tab}`;
}

function hashToRoute(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean);
  const tab = ALL_TABS.includes(parts[0]) ? parts[0] : 'data';
  let view = SUBVIEW_TABS.has(tab) ? (parts[1] || TAB_DEFAULT_VIEW[tab]) : null;
  // 已下线的子视图归一到默认视图，兼容陈旧书签：#/fetch/groups（「采集范围」，实体简化阶段 1 移除）。
  if (tab === 'fetch' && view === 'groups') view = TAB_DEFAULT_VIEW.fetch;
  return { tab, view };
}

function navFromHash() {
  const r = typeof window !== 'undefined' && window.location.hash ? hashToRoute(window.location.hash) : { tab: 'data', view: null };
  const views = defaultViews();
  if (SUBVIEW_TABS.has(r.tab) && r.view) views[r.tab] = r.view;
  return { tab: r.tab, views, focus: null };
}

function BrandLogo({ logoError, onLogoError, rail = false }) {
  // rail 变体:32px 品牌位,与阅读器视图轨同规格(导轨风格向视图轨靠拢)
  if (rail) {
    return !logoError ? (
      <BrandLogoImage
        displaySize={32}
        alt="哆啦美"
        className="reader-vrail-brand-img"
        onError={onLogoError}
      />
    ) : (
      <div className="reader-vrail-brand" aria-hidden="true">
        <Bot />
      </div>
    );
  }
  return !logoError ? (
    <BrandLogoImage
      displaySize={48}
      alt="哆啦美"
      className="h-12 w-12 rounded-[var(--r-card)] object-contain shadow-sm"
      onError={onLogoError}
    />
  ) : (
    <div className="brand-mark flex h-12 w-12 items-center justify-center rounded-[var(--r-card)]">
      <Bot className="h-6 w-6 text-white" />
    </div>
  );
}

export default function App() {
  const [nav, setNav] = useState(navFromHash);
  const navRef = useRef(nav);
  const activeTab = nav.tab;
  const { theme, setTheme, toggleTheme, effective } = useTheme();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mountedTabs, setMountedTabs] = useState(() => new Set([nav.tab]));
  const [toast, setToast] = useState({ show: false, message: '', type: 'info' });
  const [logoError, setLogoError] = useState(false);
  const [availableFetchers, setAvailableFetchers] = useState([]);
  const [runtimeInfo, setRuntimeInfo] = useState(INITIAL_RUNTIME);
  const [runtimeLoaded, setRuntimeLoaded] = useState(false);
  const [authState, setAuthState] = useState({ status: 'checking', user: null });
  const [articlesDirty, setArticlesDirty] = useState(false);
  const [runsDirty, setRunsDirty] = useState(false);
  // 跨页跳转的聚焦上下文：单一通道 { tab, payload }。目标 Tab 自行解释 payload，
  // 消费后调 clearPendingFocus 归零。取代原先每种跳转一套 state + clear 回调 + applyFocus 分支。
  const [pendingFocus, setPendingFocus] = useState(null);
  const clearPendingFocus = useCallback(() => setPendingFocus(null), []);
  // 「保存为采集任务」的一次性 handoff：节点管理发起 → 切到任务与运行 → 预填新建编辑器后回执清空。
  // 独立于 pendingFocus 通道，避免与运行历史的 pendingFilter（同走 tab==='runs'）相撞。
  const [pendingJobDraft, setPendingJobDraft] = useState(null);
  const clearPendingJobDraft = useCallback(() => setPendingJobDraft(null), []);

  // history.state 回放时重新点燃这一次性聚焦，让目标页重新定位/筛选。
  const applyFocus = useCallback((focus) => {
    setPendingFocus(focus || null);
  }, []);

  // 写入历史 + 应用路由的单一出口；replace 用于初始播种和角色重定向（不留多余历史条目）。
  const commitNav = useCallback((next, { replace = false } = {}) => {
    const url = routeToHash(next.tab, next.views);
    const histState = { tab: next.tab, views: next.views, focus: next.focus || null };
    try {
      if (replace) window.history.replaceState(histState, '', url);
      else window.history.pushState(histState, '', url);
    } catch { /* 某些沙箱环境禁用 history，忽略即可 */ }
    navRef.current = next;
    setNav(next);
    setMountedTabs(prev => (prev.has(next.tab) ? prev : new Set(prev).add(next.tab)));
    applyFocus(next.focus);
  }, [applyFocus]);

  const goTab = useCallback((tab, options = {}) => {
    const cur = navRef.current;
    if (cur.tab === tab && !options.replace) return; // 点击当前标签：不重复入栈
    commitNav({ tab, views: cur.views, focus: null }, options);
  }, [commitNav]);

  const goView = useCallback((tab, view) => {
    const cur = navRef.current;
    if (cur.tab === tab && cur.views[tab] === view) return; // 视图未变：no-op（拦掉子组件的冗余同步）
    commitNav({ tab, views: { ...cur.views, [tab]: view }, focus: null });
  }, [commitNav]);

  const jumpWithFocus = useCallback((tab, view, focus) => {
    const cur = navRef.current;
    const views = view ? { ...cur.views, [tab]: view } : cur.views;
    commitNav({ tab, views, focus });
  }, [commitNav]);

  const setFetchView = useCallback((v) => goView('fetch', v), [goView]);

  const markArticlesDirty = useCallback(() => setArticlesDirty(true), []);
  const clearArticlesDirty = useCallback(() => setArticlesDirty(false), []);

  // 全局运行进度:跨页浮窗数据源。kick 通道复用 onRunsChanged(两页发起运行都会经过);
  // 节点页自身有行内进度+信号灯条,浮窗在该页隐藏(去重),其余页面获得跨页感知。
  const {
    progress: globalRunProgress,
    runningIds: globalRunningIds,
    kick: kickRunningProgress,
  } = useRunningProgress(authState.status === 'authenticated' && runtimeInfo.collector_enabled);

  const markRunsDirty = useCallback(() => { setRunsDirty(true); kickRunningProgress(); }, [kickRunningProgress]);
  const clearRunsDirty = useCallback(() => setRunsDirty(false), []);

  const viewArticlesForSource = useCallback((sourceId) => {
    jumpWithFocus('data', null, { tab: 'data', payload: { source_id: sourceId } });
  }, [jumpWithFocus]);

  const viewRunsForSource = useCallback((fetcherId, options = {}) => {
    jumpWithFocus('runs', null, { tab: 'runs', payload: { fetcher_id: fetcherId, status: options.status || '' } });
  }, [jumpWithFocus]);
  const viewRunningTasks = useCallback(() => {
    jumpWithFocus('runs', null, { tab: 'runs', payload: { fetcher_id: '', status: 'running' } });
  }, [jumpWithFocus]);

  // 节点管理「保存为采集任务」→ 切到任务与运行，本地打开新建编辑器（草稿）。
  const saveSelectionAsJob = useCallback((draft) => {
    setPendingJobDraft(draft);
    jumpWithFocus('runs', null, null);
  }, [jumpWithFocus]);

  // 知识台账「数据来源」列点击 → 定位并展开节点管理（采集端）里对应来源。
  const focusSourceNode = useCallback((sourceId) => {
    if (!sourceId) return;
    if (runtimeInfo.collector_enabled) {
      jumpWithFocus('fetch', 'catalog', { tab: 'fetch', payload: { source_id: sourceId } });
    }
  }, [runtimeInfo.collector_enabled, jumpWithFocus]);

  // 历史锚点：初始播种 + 监听浏览器返回/前进。
  useEffect(() => {
    commitNav(navRef.current, { replace: true });
    const onPop = (event) => {
      let route = event.state && event.state.tab ? event.state : null;
      if (!route) {
        const h = hashToRoute(window.location.hash);
        const views = { ...navRef.current.views };
        if (SUBVIEW_TABS.has(h.tab) && h.view) views[h.tab] = h.view;
        route = { tab: h.tab, views, focus: null };
      }
      navRef.current = route;
      setNav(route);
      setMountedTabs(prev => (prev.has(route.tab) ? prev : new Set(prev).add(route.tab)));
      applyFocus(route.focus);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toastTimerRef = useRef(null);
  const hideToast = useCallback(() => {
    if (toastTimerRef.current) { clearTimeout(toastTimerRef.current); toastTimerRef.current = null; }
    // 仅翻转 show=false，保留 message/type 以便离场动画期间内容不闪空。
    setToast(t => ({ ...t, show: false }));
  }, []);
  const showToast = useCallback((message, type = 'info') => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ show: true, message: typeof message === 'string' ? message : JSON.stringify(message), type });
    // error 更可能需要阅读/复制，停留更久（6s）；info/success 维持 3s。
    const duration = type === 'error' ? 6000 : 3000;
    toastTimerRef.current = setTimeout(() => setToast(t => ({ ...t, show: false })), duration);
  }, []);

  const loadRuntimeAndFetchers = useCallback(async () => {
    try {
      const runtime = await fetchRuntimeInfo();
      setRuntimeInfo(runtime);
      if (runtime.collector_enabled) {
        // 模板节点(is_template:generic_* 参数驱动通用抓取器)只在后端保留——
        // 作 source-configs/source_builder 执行底座与新节点开发模板;目录一律不显现
        // (新增源的正道 = 写代码固化质量有保障的 preset)。
        const fetchers = await fetchFetchers();
        setAvailableFetchers(fetchers.filter(f => !f.is_template));
      } else {
        setAvailableFetchers([]);
      }
    } catch (error) {
      showToast(error.message || `网络连接异常，无法获取后端数据。`, 'error');
    } finally {
      // 能力已就绪（成功或失败都放行渲染，失败时退回非乐观默认）
      setRuntimeLoaded(true);
    }
  }, [showToast]);

  useEffect(() => {
    let mounted = true;
    const checkSession = async () => {
      try {
        const session = await fetchAuthSession();
        if (!mounted) return;
        if (session.authenticated) {
          setAuthState({ status: 'authenticated', user: session.user });
        } else {
          setAuthState({ status: 'anonymous', user: null });
        }
      } catch {
        if (mounted) setAuthState({ status: 'anonymous', user: null });
      }
    };
    checkSession();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (authState.status !== 'authenticated') return;
    loadRuntimeAndFetchers();
  }, [authState.status, loadRuntimeAndFetchers]);

  useEffect(() => {
    const handleAuthExpired = () => {
      setAuthState({ status: 'anonymous', user: null });
      setAvailableFetchers([]);
      setRuntimeInfo(INITIAL_RUNTIME);
      setRuntimeLoaded(false);
      showToast('登录已过期，请重新登录。', 'error');
    };
    window.addEventListener('dorami-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('dorami-auth-expired', handleAuthExpired);
  }, [showToast]);

  const handleLogin = async (username, password) => {
    const session = await loginAdmin(username, password);
    setAuthState({ status: 'authenticated', user: session.user });
    showToast(session.user?.username ? `已登录 · ${session.user.username}` : '已登录', 'success');
  };

  // 头像/账户字段就地更新（如改头像），让顶栏与设置面板即时同步，无需重登。
  const handleUserUpdated = useCallback((patch) => {
    setAuthState(prev => (
      prev.status === 'authenticated'
        ? { ...prev, user: { ...prev.user, ...patch } }
        : prev
    ));
  }, []);

  const handleLogout = async () => {
    try {
      await logoutAdmin();
    } finally {
      setAuthState({ status: 'anonymous', user: null });
      setAvailableFetchers([]);
      setRuntimeInfo(INITIAL_RUNTIME);
      setRuntimeLoaded(false);
    }
  };

  // 受限读者（user 账号）只看到「阅读器 + 订阅分发 + 接入集成」；admin（采集+阅读超级用户）保持现有全部 tab。
  const readerOnly = runtimeInfo.account_role === 'user';
  const tabs = useMemo(() => [
    { id: 'reader', icon: BookOpen, label: '阅读器', onlyReader: true },
    { id: 'data', icon: Database, label: '知识台账', hideForReader: true },
    { id: 'fetch', icon: CloudDownload, label: '节点管理', surface: 'collector' },
    { id: 'runs', icon: History, label: '任务与运行', surface: 'collector' },
    { id: 'vector', icon: BarChart2, label: '向量雷达', surface: 'reader', requiresRag: true, hideForReader: true },
    { id: 'mcp', icon: Plug2, label: '接入集成', surface: 'reader', hideForReader: true },
    { id: 'admin', icon: ShieldCheck, label: '运维管理', adminOnly: true },
  ].filter(tab => {
    if (tab.onlyReader && !readerOnly) return false;
    if (tab.hideForReader && readerOnly) return false;
    if (tab.adminOnly && runtimeInfo.account_role !== 'admin') return false;
    if (tab.surface && !runtimeInfo[`${tab.surface}_enabled`]) return false;
    if (tab.requiresRag && !runtimeInfo.rag_enabled) return false;
    return true;
  }), [runtimeInfo, readerOnly]);

  const fetchersById = useMemo(
    () => Object.fromEntries(availableFetchers.map(f => [f.id, f])),
    [availableFetchers],
  );

  const avatarInitials = useMemo(() => {
    const name = authState.user?.username?.trim();
    return name ? name.slice(0, 2).toUpperCase() : 'AD';
  }, [authState.user?.username]);

  // 账号身份标签：读者不感知部署「层」概念，只显示自己的角色。
  const roleLabel = readerOnly ? '读者' : '管理员';

  // ── 读者账号:应用导轨隐藏(阅读器视图轨独占,轨底头像菜单承接 设置/主题/退出),
  //    「接入集成」从页签降为头像菜单里的全屏浮层 ──
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  useEffect(() => {
    // 无导轨即无页签切换;历史 hash(#mcp 等)一律归位到阅读器
    if (readerOnly && activeTab !== 'reader') goTab('reader', { replace: true });
  }, [readerOnly, activeTab, goTab]);

  // ── 管理面导轨:轨底 主题/设置/头像/退出 四钮并入单一头像菜单(与读者视图轨同语言)──
  const [railMenuOpen, setRailMenuOpen] = useState(false);
  const railMenuRef = useRef(null);
  useEffect(() => {
    if (!railMenuOpen) return undefined;
    const onDown = (e) => {
      if (railMenuRef.current && !railMenuRef.current.contains(e.target)) setRailMenuOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setRailMenuOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [railMenuOpen]);

  // 品牌标题/副标题按账号视角区分：读者侧只讲「AI 资讯阅读」，不暴露归档/采集/分发。
  const brandTitle = readerOnly ? '哆啦美' : '哆啦美·归档中枢';

  const brandSubtitle = useMemo(() => {
    if (readerOnly) return 'AI 资讯阅读器';
    if (runtimeInfo.collector_enabled && !runtimeInfo.reader_enabled) return 'External Collector Archive';
    if (runtimeInfo.reader_enabled && !runtimeInfo.collector_enabled) return 'Reader Subscription Layer';
    return 'Dorami Agent Archive';
  }, [readerOnly, runtimeInfo.collector_enabled, runtimeInfo.reader_enabled]);

  useEffect(() => {
    // 能力未载入前 tabs 为空,此时重定向会把深链(#/fetch 等书签)误弹回默认页——等载入后再判。
    if (!runtimeLoaded) return;
    if (!tabs.some(tab => tab.id === activeTab)) {
      goTab(tabs[0]?.id || 'data', { replace: true });
    }
  }, [activeTab, goTab, tabs, runtimeLoaded]);

  if (authState.status === 'checking') {
    return (
      <div className="checking-state app-shell flex min-h-screen items-center justify-center font-sans">
        <Loader2 className="mr-3 h-5 w-5 animate-spin text-blue-600" />
        <span className="text-sm font-bold">正在检查登录状态</span>
      </div>
    );
  }

  if (authState.status !== 'authenticated') {
    return <LoginScreen logoError={logoError} onLogoError={() => setLogoError(true)} onLogin={handleLogin} />;
  }

  // 运行能力就绪前不渲染主界面，避免读者态下 collector 界面/请求闪现（403）。
  if (!runtimeLoaded) {
    return (
      <div className="checking-state app-shell flex min-h-screen items-center justify-center font-sans">
        <Loader2 className="mr-3 h-5 w-5 animate-spin text-blue-600" />
        <span className="text-sm font-bold">正在载入工作台</span>
      </div>
    );
  }

  return (
    <div className="app-shell font-sans">
      {/* ── lg+:左侧固定导轨(管理面),形制向阅读器视图轨靠拢:
             56px 带宽 / 32px 品牌位 / 38px icon-only 钮 + 右侧墨底 tooltip /
             轨底单一头像菜单(设置·主题·退出)。复用 reader-vrail-* 类族(已是全站轨语言)。 ── */}
      {!readerOnly && (
      <aside className="app-rail hidden lg:flex" aria-label="主导航">
        <div className="rail-brand" title={`${brandTitle} · ${brandSubtitle}`}>
          <BrandLogo rail logoError={logoError} onLogoError={() => setLogoError(true)} />
        </div>
        <nav className="flex w-full flex-col items-center gap-1" aria-label="页面">
          {tabs.map(tab => (
            <button
              key={tab.id}
              type="button"
              onClick={() => goTab(tab.id)}
              className={`reader-vrail-btn ${activeTab === tab.id ? 'is-on' : ''}`}
              aria-current={activeTab === tab.id ? 'page' : undefined}
              aria-label={tab.label}
            >
              <tab.icon className="h-[18px] w-[18px]" />
              <span className="reader-vrail-tip">{tab.label}</span>
            </button>
          ))}
        </nav>
        <div className="reader-vrail-spring" />
        <div className="reader-vrail-user" ref={railMenuRef}>
          <button
            type="button"
            className="reader-vrail-avatar"
            aria-haspopup="menu"
            aria-expanded={railMenuOpen}
            title={`${authState.user?.username || 'admin'} · ${roleLabel}`}
            onClick={() => setRailMenuOpen((o) => !o)}
          >
            {authState.user?.avatar
              ? <img src={authState.user.avatar} alt="" />
              : <span>{avatarInitials}</span>}
          </button>
          {railMenuOpen && (
            <div className="reader-user-menu" role="menu" aria-label="账号菜单">
              <div className="reader-user-menu-head">
                {authState.user?.username || 'admin'}
                <span>{roleLabel}</span>
              </div>
              <button type="button" role="menuitem" onClick={() => { setRailMenuOpen(false); setSettingsOpen(true); }}>
                <Settings className="h-[15px] w-[15px]" /> 设置
              </button>
              <button type="button" role="menuitem" onClick={() => toggleTheme()}>
                {effective === 'dark' ? <Sun className="h-[15px] w-[15px]" /> : <Moon className="h-[15px] w-[15px]" />}
                {effective === 'dark' ? '切换亮色' : '切换暗色'}
              </button>
              <div className="reader-user-menu-sep" aria-hidden="true" />
              <button type="button" role="menuitem" className="is-danger" onClick={() => { setRailMenuOpen(false); handleLogout(); }}>
                <LogOut className="h-[15px] w-[15px]" /> 退出登录
              </button>
            </div>
          )}
        </div>
      </aside>
      )}

      {/* ── lg 以下:保留移动顶栏 ── */}
      <header className="app-header flex items-center justify-between gap-4 px-5 sm:px-8 lg:hidden">
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo logoError={logoError} onLogoError={() => setLogoError(true)} />
        </div>

        <nav className="mobile-tabs flex max-w-full flex-1 items-center gap-1 overflow-x-auto">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => goTab(tab.id)}
              className={`nav-pill flex shrink-0 items-center gap-2 px-3 py-2 text-xs font-extrabold ${activeTab === tab.id ? 'nav-pill-active' : 'text-slate-500'}`}
            >
              <tab.icon className="h-4 w-4" /> {tab.label}
            </button>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={toggleTheme}
            className="icon-button"
            title={effective === 'dark' ? '切换到亮色' : '切换到暗色'}
            aria-label={effective === 'dark' ? '切换到亮色' : '切换到暗色'}
          >
            {effective === 'dark' ? <Sun className="h-4.5 w-4.5" /> : <Moon className="h-4.5 w-4.5" />}
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="icon-button"
            title="设置"
            aria-label="设置"
          >
            <Settings className="h-4.5 w-4.5" />
          </button>
          {authState.user?.avatar ? (
            <img
              src={authState.user.avatar}
              alt="头像"
              className="h-9 w-9 rounded-full object-cover shadow-sm ring-1 ring-black/5"
            />
          ) : (
            <div className="avatar-badge flex h-9 w-9 items-center justify-center rounded-full text-xs font-bold text-white">{avatarInitials}</div>
          )}
          <button
            type="button"
            onClick={handleLogout}
            className="icon-button"
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut className="h-4.5 w-4.5" />
          </button>
        </div>
      </header>

      <Toast show={toast.show} message={toast.message} type={toast.type} onClose={hideToast} />

      {globalRunningIds.size > 0 && (
        <RunningWidget
          variant="floating"
          runningIds={globalRunningIds}
          fetchProgress={globalRunProgress}
          fetchersById={fetchersById}
          onViewRunning={viewRunningTasks}
        />
      )}

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        theme={theme}
        onThemeChange={setTheme}
        runtimeInfo={runtimeInfo}
        username={authState.user?.username}
        avatar={authState.user?.avatar}
        onUserUpdated={handleUserUpdated}
        onLogout={() => { setSettingsOpen(false); handleLogout(); }}
        showToast={showToast}
        onArticlesChanged={markArticlesDirty}
      />

      {/* 读者账号的「接入集成」:从页签降为全屏浮层(入口在阅读器轨底头像菜单) */}
      {readerOnly && integrationsOpen && (
        <div className="reader-integrations-overlay" role="dialog" aria-modal="true" aria-label="接入集成">
          <div className="reader-integrations-bar">
            <span className="reader-integrations-title">接入集成</span>
            <button
              type="button"
              className="reader-pane-iconbtn"
              onClick={() => setIntegrationsOpen(false)}
              aria-label="关闭接入集成"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="reader-integrations-body">
            <div className="page-shell">
              <Suspense fallback={<TabFallback />}>
                <MCPTab
                  showToast={showToast}
                  ragEnabled={runtimeInfo.rag_enabled}
                  collectorEnabled={runtimeInfo.collector_enabled}
                  isAdmin={false}
                  onOpenModelConfig={() => {}}
                />
              </Suspense>
            </div>
          </div>
        </div>
      )}

      <main
        className={`${readerOnly ? '' : 'ml-[var(--rail-w)] '}px-5 pt-[22px] pb-9 sm:px-7`}
        style={readerOnly ? { '--rail-w': '0px' } : undefined}
      >
        <div className="page-shell">
          {readerOnly && mountedTabs.has('reader') && (
            <div className={`tab-panel${activeTab === 'reader' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <ReaderTab
                  showToast={showToast}
                  aiEnabled={runtimeInfo.ai_beta_enabled && runtimeInfo.llm_configured}
                  standalone
                  account={authState.user}
                  avatarText={avatarInitials}
                  themeDark={effective === 'dark'}
                  onToggleTheme={toggleTheme}
                  onOpenSettings={() => setSettingsOpen(true)}
                  onOpenIntegrations={() => setIntegrationsOpen(true)}
                  onLogout={handleLogout}
                />
              </Suspense>
            </div>
          )}
          {!readerOnly && mountedTabs.has('data') && (
            <div className={`tab-panel${activeTab === 'data' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <DataTab
                  availableFetchers={availableFetchers}
                  showToast={showToast}
                  isActive={activeTab === 'data'}
                  canManageArticles={runtimeInfo.collector_enabled}
                  isReader={runtimeInfo.reader_enabled}
                  ragEnabled={runtimeInfo.rag_enabled}
                  articlesDirty={articlesDirty}
                  onArticlesRefreshed={clearArticlesDirty}
                  pendingFilter={pendingFocus?.tab === 'data' ? pendingFocus.payload : null}
                  onPendingFilterApplied={clearPendingFocus}
                  onFocusSource={focusSourceNode}
                />
              </Suspense>
            </div>
          )}
          {mountedTabs.has('fetch') && runtimeInfo.collector_enabled && (
            <div className={`tab-panel${activeTab === 'fetch' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <FetchTab
                  availableFetchers={availableFetchers}
                  showToast={showToast}
                  view={nav.views.fetch}
                  setView={setFetchView}
                  onArticlesChanged={markArticlesDirty}
                  onRunsChanged={markRunsDirty}
                  onViewArticles={viewArticlesForSource}
                  onViewRuns={viewRunsForSource}
                  onViewRunning={viewRunningTasks}
                  onSaveAsJob={saveSelectionAsJob}
                  pendingFocus={pendingFocus?.tab === 'fetch' ? pendingFocus.payload : null}
                  onPendingFocusApplied={clearPendingFocus}
                />
              </Suspense>
            </div>
          )}
          {mountedTabs.has('runs') && runtimeInfo.collector_enabled && (
            <div className={`tab-panel${activeTab === 'runs' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <FetchRunsTab
                  availableFetchers={availableFetchers}
                  showToast={showToast}
                  onArticlesChanged={markArticlesDirty}
                  onRunsChanged={markRunsDirty}
                  isActive={activeTab === 'runs'}
                  runsDirty={runsDirty}
                  onRunsRefreshed={clearRunsDirty}
                  pendingFilter={pendingFocus?.tab === 'runs' ? pendingFocus.payload : null}
                  onPendingFilterApplied={clearPendingFocus}
                  pendingJobDraft={pendingJobDraft}
                  onPendingJobDraftApplied={clearPendingJobDraft}
                />
              </Suspense>
            </div>
          )}
          {mountedTabs.has('vector') && runtimeInfo.reader_enabled && (
            <div className={`tab-panel${activeTab === 'vector' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <VectorTab availableFetchers={availableFetchers} showToast={showToast} accountRole={runtimeInfo.account_role} />
              </Suspense>
            </div>
          )}
          {mountedTabs.has('mcp') && runtimeInfo.reader_enabled && (
            <div className={`tab-panel${activeTab === 'mcp' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <MCPTab
                  showToast={showToast}
                  ragEnabled={runtimeInfo.rag_enabled}
                  collectorEnabled={runtimeInfo.collector_enabled}
                  isAdmin={runtimeInfo.account_role === 'admin'}
                  onOpenModelConfig={() => jumpWithFocus('admin', null, { tab: 'admin', payload: { sub: 'ai' } })}
                />
              </Suspense>
            </div>
          )}
          {mountedTabs.has('admin') && runtimeInfo.account_role === 'admin' && (
            <div className={`tab-panel${activeTab === 'admin' ? '' : ' is-off'}`}>
              <Suspense fallback={<TabFallback />}>
                <AdminOpsTab
                  showToast={showToast}
                  pendingFocus={pendingFocus?.tab === 'admin' ? pendingFocus.payload : null}
                  onPendingFocusApplied={clearPendingFocus}
                />
              </Suspense>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
