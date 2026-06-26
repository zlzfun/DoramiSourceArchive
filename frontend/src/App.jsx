import { useCallback, useState, useEffect, useMemo, useRef } from 'react';
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
  Sun,
} from 'lucide-react';
import Toast from './components/Toast';
import DataTab from './components/DataTab';
import FetchTab from './components/FetchTab';
import VectorTab from './components/VectorTab';
import FetchRunsTab from './components/FetchRunsTab';
import MCPTab from './components/MCPTab';
import ReaderTab from './components/ReaderTab';
import SettingsModal from './components/SettingsModal';
import LoginScreen from './components/LoginScreen';
import BrandLogoImage from './components/BrandLogoImage';
import { useTheme } from './theme';
import { fetchAuthSession, fetchFetchers, fetchRuntimeInfo, loginAdmin, logoutAdmin } from './api';

// ── 导航 / 历史锚点 ──
// 把「标签 + 子视图」镜像到 URL hash（#/fetch/groups），跨页跳转的聚焦上下文存在 history.state 里。
// 让浏览器「返回」能逐级退回：子视图切换 → 标签切换 → 跨页跳转的原位。
const ALL_TABS = ['reader', 'data', 'fetch', 'runs', 'vector', 'mcp'];
const TAB_DEFAULT_VIEW = { fetch: 'catalog', runs: 'jobs' };
const SUBVIEW_TABS = new Set(['fetch', 'runs']);

function defaultViews() {
  return { fetch: 'catalog', runs: 'jobs' };
}

function routeToHash(tab, views) {
  if (SUBVIEW_TABS.has(tab) && views[tab]) return `#/${tab}/${views[tab]}`;
  return `#/${tab}`;
}

function hashToRoute(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split('/').filter(Boolean);
  const tab = ALL_TABS.includes(parts[0]) ? parts[0] : 'data';
  const view = SUBVIEW_TABS.has(tab) ? (parts[1] || TAB_DEFAULT_VIEW[tab]) : null;
  return { tab, view };
}

function navFromHash() {
  const r = typeof window !== 'undefined' && window.location.hash ? hashToRoute(window.location.hash) : { tab: 'data', view: null };
  const views = defaultViews();
  if (SUBVIEW_TABS.has(r.tab) && r.view) views[r.tab] = r.view;
  return { tab: r.tab, views, focus: null };
}

function BrandLogo({ logoError, onLogoError }) {
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
  // 初始非乐观默认：能力未知时按「都未启用」，避免在 fetchRuntimeInfo 返回前把 collector 界面闪给读者。
  const [runtimeInfo, setRuntimeInfo] = useState({ role: 'all', collector_enabled: false, reader_enabled: false, rag_enabled: false, ai_beta_enabled: false, llm_configured: false });
  const [runtimeLoaded, setRuntimeLoaded] = useState(false);
  const [authState, setAuthState] = useState({ status: 'checking', user: null });
  const [articlesDirty, setArticlesDirty] = useState(false);
  const [runsDirty, setRunsDirty] = useState(false);
  const [pendingDataFilter, setPendingDataFilter] = useState(null);
  const [pendingRunsFilter, setPendingRunsFilter] = useState(null);
  const [pendingFetchFocus, setPendingFetchFocus] = useState(null);

  // 跨页跳转的聚焦上下文（存在 history.state 里）回放时，重新点燃对应的一次性 pending*，让目标页重新定位/筛选。
  const applyFocus = useCallback((focus) => {
    if (!focus) return;
    if (focus.kind === 'dataFilter') setPendingDataFilter(focus.payload);
    else if (focus.kind === 'runsFilter') setPendingRunsFilter(focus.payload);
    else if (focus.kind === 'fetchFocus') setPendingFetchFocus(focus.payload);
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
  const setRunsView = useCallback((v) => goView('runs', v), [goView]);

  const markArticlesDirty = useCallback(() => setArticlesDirty(true), []);
  const clearArticlesDirty = useCallback(() => setArticlesDirty(false), []);

  const markRunsDirty = useCallback(() => setRunsDirty(true), []);
  const clearRunsDirty = useCallback(() => setRunsDirty(false), []);

  const viewArticlesForSource = useCallback((sourceId) => {
    jumpWithFocus('data', null, { kind: 'dataFilter', payload: { source_id: sourceId } });
  }, [jumpWithFocus]);
  const clearPendingDataFilter = useCallback(() => setPendingDataFilter(null), []);

  const viewRunsForSource = useCallback((fetcherId, options = {}) => {
    jumpWithFocus('runs', 'history', { kind: 'runsFilter', payload: { fetcher_id: fetcherId, status: options.status || '' } });
  }, [jumpWithFocus]);
  const viewRunningTasks = useCallback(() => {
    jumpWithFocus('runs', 'history', { kind: 'runsFilter', payload: { fetcher_id: '', status: '' } });
  }, [jumpWithFocus]);
  const clearPendingRunsFilter = useCallback(() => setPendingRunsFilter(null), []);

  // 知识台账「数据来源」列点击 → 定位并展开节点管理（采集端）里对应来源。
  const focusSourceNode = useCallback((sourceId) => {
    if (!sourceId) return;
    if (runtimeInfo.collector_enabled) {
      jumpWithFocus('fetch', 'catalog', { kind: 'fetchFocus', payload: { source_id: sourceId } });
    }
  }, [runtimeInfo.collector_enabled, jumpWithFocus]);
  const clearPendingFetchFocus = useCallback(() => setPendingFetchFocus(null), []);

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
  const showToast = useCallback((message, type = 'info') => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ show: true, message: typeof message === 'string' ? message : JSON.stringify(message), type });
    // 仅翻转 show=false，保留 message/type 以便离场动画期间内容不闪空。
    toastTimerRef.current = setTimeout(() => setToast(t => ({ ...t, show: false })), 3000);
  }, []);

  const loadRuntimeAndFetchers = useCallback(async () => {
    try {
      const runtime = await fetchRuntimeInfo();
      setRuntimeInfo(runtime);
      if (runtime.collector_enabled) {
        // 配置驱动通用抓取器（generic_web，中级目标）暂不开放前端入口：后端保留，目录里隐藏。
        const fetchers = await fetchFetchers();
        setAvailableFetchers(fetchers.filter(f => f.id !== 'generic_web'));
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
      setRuntimeInfo({ role: 'all', collector_enabled: false, reader_enabled: false, rag_enabled: false });
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
      setRuntimeInfo({ role: 'all', collector_enabled: false, reader_enabled: false, rag_enabled: false });
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
    { id: 'mcp', icon: Plug2, label: '接入集成', surface: 'reader' },
  ].filter(tab => {
    if (tab.onlyReader && !readerOnly) return false;
    if (tab.hideForReader && readerOnly) return false;
    if (tab.surface && !runtimeInfo[`${tab.surface}_enabled`]) return false;
    if (tab.requiresRag && !runtimeInfo.rag_enabled) return false;
    return true;
  }), [runtimeInfo, readerOnly]);

  const avatarInitials = useMemo(() => {
    const name = authState.user?.username?.trim();
    return name ? name.slice(0, 2).toUpperCase() : 'AD';
  }, [authState.user?.username]);

  // 账号身份标签：读者不感知部署「层」概念，只显示自己的角色。
  const roleLabel = readerOnly ? '读者' : '管理员';

  // 品牌标题/副标题按账号视角区分：读者侧只讲「AI 资讯阅读」，不暴露归档/采集/分发。
  const brandTitle = readerOnly ? '哆啦美' : '哆啦美·归档中枢';

  const brandSubtitle = useMemo(() => {
    if (readerOnly) return 'AI 资讯阅读器';
    if (runtimeInfo.collector_enabled && !runtimeInfo.reader_enabled) return 'External Collector Archive';
    if (runtimeInfo.reader_enabled && !runtimeInfo.collector_enabled) return 'Reader Subscription Layer';
    return 'Dorami Agent Archive';
  }, [readerOnly, runtimeInfo.collector_enabled, runtimeInfo.reader_enabled]);

  useEffect(() => {
    if (!tabs.some(tab => tab.id === activeTab)) {
      goTab(tabs[0]?.id || 'data', { replace: true });
    }
  }, [activeTab, goTab, tabs]);

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
      <header className="app-header flex items-center justify-between gap-4 px-5 sm:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo logoError={logoError} onLogoError={() => setLogoError(true)} />
          <div className="hidden min-w-0 sm:block">
            <h1 className="brand-title truncate text-xl font-black leading-tight">{brandTitle}</h1>
            <p className="brand-subtitle mt-1 text-xs font-bold">{brandSubtitle}</p>
          </div>
        </div>

        <nav className="hidden flex-1 items-center justify-center gap-6 lg:flex">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => goTab(tab.id)}
              className={`top-tab relative flex items-center gap-2 whitespace-nowrap px-6 py-3 text-sm font-extrabold transition-colors ${activeTab === tab.id ? 'top-tab-active' : 'text-slate-500 hover:text-slate-950 dark:hover:text-slate-100'}`}
            >
              <tab.icon className="h-4.5 w-4.5" /> {tab.label}
            </button>
          ))}
        </nav>

        <nav className="mobile-tabs flex max-w-full flex-1 items-center gap-1 overflow-x-auto lg:hidden">
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

        <div className="flex shrink-0 items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-black text-slate-800">{authState.user?.username || 'admin'}</p>
              <p className="micro-label text-slate-500">{roleLabel}</p>
            </div>
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
              <div className="avatar-badge flex h-9 w-9 items-center justify-center rounded-full text-xs font-black text-white">{avatarInitials}</div>
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
        </div>
      </header>

      <Toast show={toast.show} message={toast.message} type={toast.type} />

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

      <main className="mx-auto max-w-[1540px] px-5 py-9 sm:px-8 xl:px-10">
        <div className="page-shell">
          {readerOnly && mountedTabs.has('reader') && (
            <div className="tab-panel" style={{ display: activeTab === 'reader' ? 'block' : 'none' }}>
              <ReaderTab showToast={showToast} aiEnabled={runtimeInfo.ai_beta_enabled && runtimeInfo.llm_configured} />
            </div>
          )}
          {!readerOnly && mountedTabs.has('data') && (
            <div className="tab-panel" style={{ display: activeTab === 'data' ? 'block' : 'none' }}>
              <DataTab
                availableFetchers={availableFetchers}
                showToast={showToast}
                isActive={activeTab === 'data'}
                canManageArticles={runtimeInfo.collector_enabled}
                isReader={runtimeInfo.reader_enabled}
                ragEnabled={runtimeInfo.rag_enabled}
                articlesDirty={articlesDirty}
                onArticlesRefreshed={clearArticlesDirty}
                pendingFilter={pendingDataFilter}
                onPendingFilterApplied={clearPendingDataFilter}
                onFocusSource={focusSourceNode}
              />
            </div>
          )}
          {mountedTabs.has('fetch') && runtimeInfo.collector_enabled && (
            <div className="tab-panel" style={{ display: activeTab === 'fetch' ? 'block' : 'none' }}>
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
                pendingFocus={pendingFetchFocus}
                onPendingFocusApplied={clearPendingFetchFocus}
              />
            </div>
          )}
          {mountedTabs.has('runs') && runtimeInfo.collector_enabled && (
            <div className="tab-panel" style={{ display: activeTab === 'runs' ? 'block' : 'none' }}>
              <FetchRunsTab
                availableFetchers={availableFetchers}
                showToast={showToast}
                view={nav.views.runs}
                setView={setRunsView}
                onArticlesChanged={markArticlesDirty}
                onRunsChanged={markRunsDirty}
                isActive={activeTab === 'runs'}
                runsDirty={runsDirty}
                onRunsRefreshed={clearRunsDirty}
                pendingFilter={pendingRunsFilter}
                onPendingFilterApplied={clearPendingRunsFilter}
              />
            </div>
          )}
          {mountedTabs.has('vector') && runtimeInfo.reader_enabled && (
            <div className="tab-panel" style={{ display: activeTab === 'vector' ? 'block' : 'none' }}>
              <VectorTab availableFetchers={availableFetchers} showToast={showToast} accountRole={runtimeInfo.account_role} />
            </div>
          )}
          {mountedTabs.has('mcp') && runtimeInfo.reader_enabled && (
            <div className="tab-panel" style={{ display: activeTab === 'mcp' ? 'block' : 'none' }}>
              <MCPTab showToast={showToast} ragEnabled={runtimeInfo.rag_enabled} collectorEnabled={runtimeInfo.collector_enabled} isAdmin={runtimeInfo.account_role === 'admin'} />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
