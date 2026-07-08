import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart2,
  FileText,
  Info,
  Palette,
  Plug2,
  Settings as SettingsIcon,
  User,
  X,
} from 'lucide-react';
import { fetchMcpStatus } from '../api';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import AccountSection from './settings/AccountSection';
import AppearanceSection from './settings/AppearanceSection';
import VectorSection from './settings/VectorSection';
import IntegrationSection from './settings/IntegrationSection';
import DataSyncSection from './settings/DataSyncSection';
import AboutSection from './settings/AboutSection';

export default function SettingsModal({ open, onClose, theme, onThemeChange, runtimeInfo, username, avatar, onUserUpdated, onLogout, showToast, onArticlesChanged }) {
  const { mounted, closing } = useModalTransition(open);
  const collectorEnabled = Boolean(runtimeInfo?.collector_enabled);
  const readerEnabled = Boolean(runtimeInfo?.reader_enabled);
  const ragEnabled = Boolean(runtimeInfo?.rag_enabled);
  const accountRole = runtimeInfo?.account_role;
  const isAdmin = accountRole === 'admin';

  const accountRoleLabel = useMemo(() => {
    if (accountRole === 'admin') return '管理员';
    if (accountRole === 'user') return '读者';
    return '—';
  }, [accountRole]);

  const sections = useMemo(() => [
    { id: 'account', label: '账户', icon: User, show: true },
    { id: 'appearance', label: '外观', icon: Palette, show: true },
    { id: 'vector', label: '向量雷达', icon: BarChart2, show: collectorEnabled && ragEnabled },
    { id: 'sync', label: '数据同步', icon: FileText, show: isAdmin && (collectorEnabled || readerEnabled) },
    { id: 'integration', label: '接入集成', icon: Plug2, show: readerEnabled },
    { id: 'about', label: '关于', icon: Info, show: true },
  ].filter(s => s.show), [collectorEnabled, isAdmin, ragEnabled, readerEnabled]);

  const [active, setActive] = useState('account');
  const [mcpStatus, setMcpStatus] = useState(null);
  const panelRef = useRef(null);
  useModalA11y(open && mounted, onClose, panelRef);

  useEffect(() => {
    if (!open) return undefined;
    setActive('account');
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);

  useEffect(() => {
    if (!open || !readerEnabled) return;
    fetchMcpStatus().then(setMcpStatus).catch(() => setMcpStatus({ enabled: false, url: null }));
  }, [open, readerEnabled]);

  if (!mounted) return null;

  return (
    <div className={`modal-overlay ${closing ? 'is-closing' : ''}`} onMouseDown={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        tabIndex={-1}
        className="modal-panel max-w-3xl"
        onMouseDown={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[var(--dorami-border)] bg-[var(--dorami-well)] px-6 py-4">
          <div className="flex items-center gap-3">
            <SettingsIcon className="h-5 w-5 text-indigo-500" />
            <h3 className="text-lg font-black text-[var(--dorami-ink)]">设置</h3>
          </div>
          <button onClick={onClose} className="icon-button" aria-label="关闭">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav className="w-40 shrink-0 space-y-1 border-r border-[var(--dorami-border)] bg-[var(--dorami-soft)] p-3">
            {sections.map(section => (
              <button
                key={section.id}
                onClick={() => setActive(section.id)}
                className={`flex w-full items-center gap-2 rounded-[var(--r-control)] px-3 py-2 text-sm font-bold transition-colors ${
                  active === section.id ? 'bg-[var(--dorami-surface)] text-indigo-600 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                <section.icon className="h-4 w-4" /> {section.label}
              </button>
            ))}
          </nav>

          <div className="flex-1 overflow-y-auto p-6">
            {active === 'account' && (
              <AccountSection username={username} avatar={avatar} accountRoleLabel={accountRoleLabel} onUserUpdated={onUserUpdated} onLogout={onLogout} showToast={showToast} />
            )}
            {active === 'appearance' && (
              <AppearanceSection theme={theme} onThemeChange={onThemeChange} />
            )}
            {active === 'vector' && collectorEnabled && ragEnabled && (
              <VectorSection showToast={showToast} />
            )}
            {active === 'sync' && isAdmin && (collectorEnabled || readerEnabled) && (
              <DataSyncSection
                showToast={showToast}
                canExport={collectorEnabled}
                canImport={readerEnabled}
                onArticlesChanged={onArticlesChanged}
              />
            )}
            {active === 'integration' && readerEnabled && (
              <IntegrationSection
                showToast={showToast}
                mcpStatus={mcpStatus}
                canToggle={collectorEnabled}
                onMcpToggled={enabled => setMcpStatus(prev => ({ ...(prev || {}), enabled }))}
              />
            )}
            {active === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} isAdmin={isAdmin} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
