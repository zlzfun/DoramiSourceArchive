import { useCallback, useState, useEffect, useMemo } from 'react';
import {
  BarChart2,
  Bot,
  CloudDownload,
  Database,
  History,
  KeyRound,
  Loader2,
  LogOut,
  Plug2,
} from 'lucide-react';
import Toast from './components/Toast';
import DataTab from './components/DataTab';
import FetchTab from './components/FetchTab';
import VectorTab from './components/VectorTab';
import FetchRunsTab from './components/FetchRunsTab';
import MCPTab from './components/MCPTab';
import SubscriptionTab from './components/SubscriptionTab';
import LoginScreen from './components/LoginScreen';
import { fetchAuthSession, fetchFetchers, fetchRuntimeInfo, loginAdmin, logoutAdmin } from './api';
import { LOGO_PATH } from './config';

function BrandLogo({ logoError, onLogoError }) {
  return !logoError ? (
    <img src={LOGO_PATH} alt="Logo" className="h-12 w-12 rounded-[12px] object-contain shadow-sm" onError={onLogoError} />
  ) : (
    <div className="brand-mark flex h-12 w-12 items-center justify-center rounded-[12px]">
      <Bot className="h-6 w-6 text-white" />
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState('data');
  const [mountedTabs, setMountedTabs] = useState(() => new Set(['data']));
  const [toast, setToast] = useState({ show: false, message: '', type: 'info' });
  const [logoError, setLogoError] = useState(false);
  const [availableFetchers, setAvailableFetchers] = useState([]);
  const [runtimeInfo, setRuntimeInfo] = useState({ role: 'all', collector_enabled: true, reader_enabled: true });
  const [authState, setAuthState] = useState({ status: 'checking', user: null });
  const [articlesDirty, setArticlesDirty] = useState(false);
  const [runsDirty, setRunsDirty] = useState(false);
  const [pendingDataFilter, setPendingDataFilter] = useState(null);
  const [pendingRunsFilter, setPendingRunsFilter] = useState(null);

  const switchTab = useCallback((id) => {
    setActiveTab(id);
    setMountedTabs(prev => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  const markArticlesDirty = useCallback(() => setArticlesDirty(true), []);
  const clearArticlesDirty = useCallback(() => setArticlesDirty(false), []);

  const markRunsDirty = useCallback(() => setRunsDirty(true), []);
  const clearRunsDirty = useCallback(() => setRunsDirty(false), []);

  const viewArticlesForSource = useCallback((sourceId) => {
    setPendingDataFilter({ source_id: sourceId });
    switchTab('data');
  }, [switchTab]);
  const clearPendingDataFilter = useCallback(() => setPendingDataFilter(null), []);

  const viewRunsForSource = useCallback((fetcherId, options = {}) => {
    setPendingRunsFilter({ fetcher_id: fetcherId, status: options.status || '' });
    switchTab('runs');
  }, [switchTab]);
  const viewRunningTasks = useCallback(() => {
    setPendingRunsFilter({ fetcher_id: '', status: '' });
    switchTab('runs');
  }, [switchTab]);
  const clearPendingRunsFilter = useCallback(() => setPendingRunsFilter(null), []);

  const showToast = useCallback((message, type = 'info') => {
    setToast({ show: true, message: typeof message === 'string' ? message : JSON.stringify(message), type });
    setTimeout(() => setToast({ show: false, message: '', type: 'info' }), 3000);
  }, []);

  const loadRuntimeAndFetchers = useCallback(async () => {
    try {
      const runtime = await fetchRuntimeInfo();
      setRuntimeInfo(runtime);
      if (runtime.collector_enabled) {
        setAvailableFetchers(await fetchFetchers());
      } else {
        setAvailableFetchers([]);
      }
    } catch (error) {
      showToast(error.message || `网络连接异常，无法获取后端数据。`, 'error');
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
      showToast('登录已过期，请重新登录。', 'error');
    };
    window.addEventListener('dorami-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('dorami-auth-expired', handleAuthExpired);
  }, [showToast]);

  const handleLogin = async (username, password) => {
    const session = await loginAdmin(username, password);
    setAuthState({ status: 'authenticated', user: session.user });
    showToast('登录成功', 'success');
  };

  const handleLogout = async () => {
    try {
      await logoutAdmin();
    } finally {
      setAuthState({ status: 'anonymous', user: null });
      setAvailableFetchers([]);
    }
  };

  const tabs = useMemo(() => [
    { id: 'data', icon: Database, label: '知识台账' },
    { id: 'fetch', icon: CloudDownload, label: '节点管理', surface: 'collector' },
    { id: 'runs', icon: History, label: '任务与运行', surface: 'collector' },
    { id: 'subscriptions', icon: KeyRound, label: '订阅分发', surface: 'reader' },
    { id: 'vector', icon: BarChart2, label: '向量雷达', surface: 'reader' },
    { id: 'mcp', icon: Plug2, label: '接入集成', surface: 'reader' },
  ].filter(tab => !tab.surface || runtimeInfo[`${tab.surface}_enabled`]), [runtimeInfo]);

  const roleLabel = useMemo(() => {
    if (runtimeInfo.collector_enabled && !runtimeInfo.reader_enabled) return '采集归档层';
    if (runtimeInfo.reader_enabled && !runtimeInfo.collector_enabled) return '分发订阅层';
    if (runtimeInfo.collector_enabled && runtimeInfo.reader_enabled) return '双层一体';
    return '无可用层';
  }, [runtimeInfo.collector_enabled, runtimeInfo.reader_enabled]);

  const brandSubtitle = useMemo(() => {
    if (runtimeInfo.collector_enabled && !runtimeInfo.reader_enabled) return 'External Collector Archive';
    if (runtimeInfo.reader_enabled && !runtimeInfo.collector_enabled) return 'Reader Subscription Layer';
    return 'Dorami Agent Archive';
  }, [runtimeInfo.collector_enabled, runtimeInfo.reader_enabled]);

  useEffect(() => {
    if (!tabs.some(tab => tab.id === activeTab)) {
      switchTab(tabs[0]?.id || 'data');
    }
  }, [activeTab, switchTab, tabs]);

  if (authState.status === 'checking') {
    return (
      <div className="app-shell flex min-h-screen items-center justify-center font-sans text-slate-500">
        <Loader2 className="mr-3 h-5 w-5 animate-spin text-indigo-500" />
        <span className="text-sm font-bold">正在检查登录状态</span>
      </div>
    );
  }

  if (authState.status !== 'authenticated') {
    return <LoginScreen logoError={logoError} onLogoError={() => setLogoError(true)} onLogin={handleLogin} />;
  }

  return (
    <div className="app-shell font-sans">
      <header className="app-header flex items-center justify-between gap-4 px-5 sm:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo logoError={logoError} onLogoError={() => setLogoError(true)} />
          <div className="hidden min-w-0 sm:block">
            <h1 className="truncate text-[20px] font-black leading-tight text-slate-950">哆啦美·归档中枢</h1>
            <p className="mt-1 text-xs font-bold text-slate-500">{brandSubtitle}</p>
          </div>
        </div>

        <nav className="hidden flex-1 items-center justify-center gap-6 lg:flex">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => switchTab(tab.id)}
              className={`top-tab relative flex items-center gap-2 whitespace-nowrap px-6 py-3 text-sm font-extrabold transition-colors ${activeTab === tab.id ? 'top-tab-active' : 'text-slate-600 hover:text-slate-950'}`}
            >
              <tab.icon className="h-4.5 w-4.5" /> {tab.label}
            </button>
          ))}
        </nav>

        <nav className="mobile-tabs flex max-w-full flex-1 items-center gap-1 overflow-x-auto lg:hidden">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => switchTab(tab.id)}
              className={`nav-pill flex shrink-0 items-center gap-2 px-3 py-2 text-xs font-extrabold ${activeTab === tab.id ? 'nav-pill-active' : 'text-slate-600'}`}
            >
              <tab.icon className="h-4 w-4" /> {tab.label}
            </button>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-black text-slate-800">{authState.user?.username || 'admin'}</p>
              <p className="text-[11px] font-bold text-slate-400">{roleLabel}</p>
            </div>
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[#4f46e5] to-[#7c3aed] text-xs font-black text-white shadow-lg shadow-indigo-500/25">AD</div>
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

      <main className="mx-auto max-w-[1540px] px-5 py-9 sm:px-8 xl:px-10">
        <div className="page-shell">
          {mountedTabs.has('data') && (
            <div style={{ display: activeTab === 'data' ? 'block' : 'none' }}>
              <DataTab
                availableFetchers={availableFetchers}
                showToast={showToast}
                isActive={activeTab === 'data'}
                canManageArticles={runtimeInfo.collector_enabled}
                isReader={runtimeInfo.reader_enabled}
                articlesDirty={articlesDirty}
                onArticlesRefreshed={clearArticlesDirty}
                pendingFilter={pendingDataFilter}
                onPendingFilterApplied={clearPendingDataFilter}
              />
            </div>
          )}
          {mountedTabs.has('fetch') && (
            <div style={{ display: activeTab === 'fetch' && runtimeInfo.collector_enabled ? 'block' : 'none' }}>
              <FetchTab
                availableFetchers={availableFetchers}
                showToast={showToast}
                onArticlesChanged={markArticlesDirty}
                onRunsChanged={markRunsDirty}
                onViewArticles={viewArticlesForSource}
                onViewRuns={viewRunsForSource}
                onViewRunning={viewRunningTasks}
              />
            </div>
          )}
          {mountedTabs.has('runs') && (
            <div style={{ display: activeTab === 'runs' && runtimeInfo.collector_enabled ? 'block' : 'none' }}>
              <FetchRunsTab
                availableFetchers={availableFetchers}
                showToast={showToast}
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
          {mountedTabs.has('vector') && (
            <div style={{ display: activeTab === 'vector' && runtimeInfo.reader_enabled ? 'block' : 'none' }}>
              <VectorTab availableFetchers={availableFetchers} showToast={showToast} accountRole={runtimeInfo.account_role} />
            </div>
          )}
          {mountedTabs.has('subscriptions') && (
            <div style={{ display: activeTab === 'subscriptions' && runtimeInfo.reader_enabled ? 'block' : 'none' }}>
              <SubscriptionTab showToast={showToast} onViewArticles={viewArticlesForSource} />
            </div>
          )}
          {mountedTabs.has('mcp') && (
            <div style={{ display: activeTab === 'mcp' && runtimeInfo.reader_enabled ? 'block' : 'none' }}>
              <MCPTab showToast={showToast} />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
