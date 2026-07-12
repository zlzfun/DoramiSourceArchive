import { useEffect, useMemo, useRef, useState } from 'react';
import { FileText, Info, Palette, Plug2, User, X } from 'lucide-react';
import { fetchMcpStatus } from '../api';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import AccountSection from './settings/AccountSection';
import AppearanceSection from './settings/AppearanceSection';
import ServiceSection from './settings/ServiceSection';
import DataSyncSection from './settings/DataSyncSection';
import AboutSection from './settings/AboutSection';

// 设置面板(弹窗波):控制柜——左导航(竖条选中)+ 右内容,五区共用设置行范式。
// 分区头随切换给一句话提示;「向量雷达」独立区已退役为服务区只读统计行(用户拍板);
// 原「接入集成」区更名「服务」且 admin-only(MCP 地址已移除,reader 无可操作项)。
const HINTS = {
  account: '身份、头像与登录凭据',
  appearance: '亮暗主题偏好',
  service: 'MCP 与向量索引的运行状态',
  sync: '部署端之间搬运文章归档',
  about: '产品与账户信息',
};

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
    { id: 'service', label: '服务', icon: Plug2, show: isAdmin && collectorEnabled },
    { id: 'sync', label: '数据同步', icon: FileText, show: isAdmin && (collectorEnabled || readerEnabled) },
    { id: 'about', label: '关于', icon: Info, show: true },
  ].filter(s => s.show), [collectorEnabled, isAdmin, readerEnabled]);

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
    if (!open || !(isAdmin && collectorEnabled)) return;
    fetchMcpStatus().then(setMcpStatus).catch(() => setMcpStatus({ enabled: false, url: null }));
  }, [open, isAdmin, collectorEnabled]);

  if (!mounted) return null;

  const activeSection = sections.find(s => s.id === active) || sections[0];

  return (
    <div className={`modal-overlay ${closing ? 'is-closing' : ''}`} onMouseDown={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        tabIndex={-1}
        className="modal-panel sett-cab"
        onMouseDown={e => e.stopPropagation()}
      >
        <nav className="sett-nav" aria-label="设置分区">
          <div className="sett-nav-title">设置</div>
          {sections.map(section => (
            <button
              key={section.id}
              type="button"
              onClick={() => setActive(section.id)}
              className={`sett-nav-btn ${activeSection.id === section.id ? 'is-on' : ''}`}
            >
              <section.icon /> {section.label}
            </button>
          ))}
          <div className="sett-nav-spacer" />
          <div className="sett-nav-foot">哆啦美 · 归档中枢</div>
        </nav>

        <div className="sett-body">
          <div className="sett-head">
            <span className="sett-head-title">{activeSection.label}</span>
            <span className="sett-head-hint">{HINTS[activeSection.id]}</span>
            <button onClick={onClose} className="icon-button ml-auto h-8 w-8" aria-label="关闭设置">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="sett-scroll">
            {activeSection.id === 'account' && (
              <AccountSection username={username} avatar={avatar} accountRoleLabel={accountRoleLabel} onUserUpdated={onUserUpdated} onLogout={onLogout} showToast={showToast} />
            )}
            {activeSection.id === 'appearance' && (
              <AppearanceSection theme={theme} onThemeChange={onThemeChange} />
            )}
            {activeSection.id === 'service' && isAdmin && collectorEnabled && (
              <ServiceSection
                showToast={showToast}
                mcpStatus={mcpStatus}
                onMcpToggled={enabled => setMcpStatus(prev => ({ ...(prev || {}), enabled }))}
                ragEnabled={ragEnabled}
                onClose={onClose}
              />
            )}
            {activeSection.id === 'sync' && isAdmin && (collectorEnabled || readerEnabled) && (
              <DataSyncSection
                showToast={showToast}
                canExport={collectorEnabled}
                canImport={readerEnabled}
                onArticlesChanged={onArticlesChanged}
              />
            )}
            {activeSection.id === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} isAdmin={isAdmin} version={runtimeInfo?.version} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
