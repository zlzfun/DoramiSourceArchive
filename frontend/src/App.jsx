import { useState, useEffect } from 'react';
import {
  BarChart2,
  Bot,
  CloudDownload,
  Database,
  History,
  Plug2,
} from 'lucide-react';
import Toast from './components/Toast';
import DataTab from './components/DataTab';
import FetchTab from './components/FetchTab';
import VectorTab from './components/VectorTab';
import FetchRunsTab from './components/FetchRunsTab';
import MCPTab from './components/MCPTab';
import { fetchFetchers } from './api';

const CUSTOM_LOGO_PATH = '/logo.png';

function BrandLogo({ logoError, onLogoError }) {
  return !logoError ? (
    <img src={CUSTOM_LOGO_PATH} alt="Logo" className="h-12 w-12 rounded-[12px] object-contain shadow-sm" onError={onLogoError} />
  ) : (
    <div className="brand-mark flex h-12 w-12 items-center justify-center rounded-[12px]">
      <Bot className="h-6 w-6 text-white" />
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState('data');
  const [toast, setToast] = useState({ show: false, message: '', type: 'info' });
  const [logoError, setLogoError] = useState(false);
  const [availableFetchers, setAvailableFetchers] = useState([]);

  const showToast = (message, type = 'info') => {
    setToast({ show: true, message: typeof message === 'string' ? message : JSON.stringify(message), type });
    setTimeout(() => setToast({ show: false, message: '', type: 'info' }), 3000);
  };

  useEffect(() => {
    const loadFetchers = async () => {
      try {
        setAvailableFetchers(await fetchFetchers());
      } catch {
        showToast(`网络连接异常，无法获取后端数据。`, 'error');
      }
    };
    loadFetchers();
  }, []);

  const tabs = [
    { id: 'data', icon: Database, label: '知识台账' },
    { id: 'fetch', icon: CloudDownload, label: '节点管理' },
    { id: 'runs', icon: History, label: '任务与运行' },
    { id: 'vector', icon: BarChart2, label: '向量雷达' },
    { id: 'mcp', icon: Plug2, label: '接入集成' },
  ];

  return (
    <div className="app-shell font-sans">
      <header className="app-header flex items-center justify-between gap-4 px-5 sm:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo logoError={logoError} onLogoError={() => setLogoError(true)} />
          <div className="hidden min-w-0 sm:block">
            <h1 className="truncate text-[20px] font-black leading-tight text-slate-950">哆啦美·归档中枢</h1>
            <p className="mt-1 text-xs font-bold text-slate-500">Dorami Agent Archive</p>
          </div>
        </div>

        <nav className="hidden flex-1 items-center justify-center gap-6 lg:flex">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
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
              onClick={() => setActiveTab(tab.id)}
              className={`nav-pill flex shrink-0 items-center gap-2 px-3 py-2 text-xs font-extrabold ${activeTab === tab.id ? 'nav-pill-active' : 'text-slate-600'}`}
            >
              <tab.icon className="h-4 w-4" /> {tab.label}
            </button>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[#0f75ff] to-[#1554ff] text-xs font-black text-white shadow-lg shadow-blue-500/20">DA</div>
          </div>
        </div>
      </header>

      <Toast show={toast.show} message={toast.message} type={toast.type} />

      <main className="mx-auto max-w-[1540px] px-5 py-9 sm:px-8 xl:px-10">
        <div className="page-shell">
          {activeTab === 'data' && <DataTab availableFetchers={availableFetchers} showToast={showToast} />}
          {activeTab === 'fetch' && <FetchTab availableFetchers={availableFetchers} showToast={showToast} />}
          {activeTab === 'runs' && <FetchRunsTab availableFetchers={availableFetchers} showToast={showToast} />}
          {activeTab === 'vector' && <VectorTab availableFetchers={availableFetchers} showToast={showToast} />}
          {activeTab === 'mcp' && <MCPTab showToast={showToast} />}
        </div>
      </main>
    </div>
  );
}
