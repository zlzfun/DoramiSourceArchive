import React, { useState, useEffect } from 'react';
import { Database, CloudDownload, BarChart2, Activity, Bot } from 'lucide-react';
import Toast from './components/Toast';
import DataTab from './components/DataTab';
import FetchTab from './components/FetchTab';
import VectorTab from './components/VectorTab';
import { fetchFetchers } from './api';

const CUSTOM_LOGO_PATH = '/logo.png';

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
      } catch (e) {
        showToast(`网络连接异常，无法获取后端数据。`, 'error');
      }
    };
    loadFetchers();
  }, []);

  const tabs = [
    { id: 'data', icon: Database, label: '知识台账' },
    { id: 'fetch', icon: CloudDownload, label: '节点与调度' },
    { id: 'vector', icon: BarChart2, label: '向量雷达' },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/40 to-indigo-50/60 text-slate-800 font-sans pb-32">
      <header className="bg-white/80 backdrop-blur-lg border-b border-slate-200/60 shadow-sm px-6 py-4 flex items-center justify-between sticky top-0 z-40">
        <div className="flex items-center space-x-4">
          {!logoError ? (
            <img src={CUSTOM_LOGO_PATH} alt="Logo" className="h-10 w-auto object-contain" onError={() => setLogoError(true)} />
          ) : (
            <div className="bg-blue-600 p-1.5 rounded-xl shadow flex items-center justify-center w-11 h-11"><Bot className="text-white w-6 h-6" /></div>
          )}
          <div>
            <h1 className="text-xl font-extrabold tracking-tight">哆啦美<span className="text-blue-600">·</span>归档中枢</h1>
            <p className="text-[11px] font-medium text-slate-500 flex items-center mt-0.5"><Activity className="w-3 h-3 mr-1 text-emerald-500" /> Dorami Agent Archive</p>
          </div>
        </div>
        <nav className="flex space-x-1 bg-slate-100/80 p-1.5 rounded-xl border border-slate-200/50">
          {tabs.map(tab => (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)} className={`px-4 py-2 rounded-lg text-sm font-bold transition-all flex items-center ${activeTab === tab.id ? 'bg-white shadow text-blue-700' : 'text-slate-500 hover:text-slate-800'}`}>
              <tab.icon className="w-4 h-4 mr-2" /> {tab.label}
            </button>
          ))}
        </nav>
      </header>

      <Toast show={toast.show} message={toast.message} type={toast.type} />

      <main className="max-w-[1400px] mx-auto px-4 py-8 relative">
        {activeTab === 'data' && <DataTab availableFetchers={availableFetchers} showToast={showToast} />}
        {activeTab === 'fetch' && <FetchTab availableFetchers={availableFetchers} showToast={showToast} />}
        {activeTab === 'vector' && <VectorTab availableFetchers={availableFetchers} showToast={showToast} />}
      </main>
    </div>
  );
}
