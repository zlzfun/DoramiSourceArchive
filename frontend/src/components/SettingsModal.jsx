import { useEffect, useMemo, useRef, useState } from 'react';
import { FileText, Info, Package, Palette, Plug2, Rss, User, X } from 'lucide-react';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';
import AccountSection from './settings/AccountSection';
import AppearanceSection from './settings/AppearanceSection';
import FeedTokenSection from './settings/FeedTokenSection';
import McpAccessSection from './settings/McpAccessSection';
import SkillSection from './settings/SkillSection';
import DataSyncSection from './settings/DataSyncSection';
import AboutSection from './settings/AboutSection';

// 设置面板(弹窗波→并入设置波):控制柜——左导航(分组灰签 + wash 块选中,全站轨语言)+ 右内容。
// 「接入集成」组(聚合接口/MCP 接入/技能包)自原接入集成页签并入,两种角色同享(admin 文案分支
// 在各分区组件内);「管理」组(数据同步)admin-only——原「服务」区随 MCP 开关并入「MCP 接入」
// 退役(用户拍板,向量统计行同迁);柜体随内容扩容 880×640。
// initialSection 供深链(读者轨底/头像入口直落对应分区)。
const HINTS = {
  account: '身份、头像与登录凭据',
  appearance: '亮暗主题偏好',
  feed: '一枚 dfeed_ 令牌与聚合拉取接口', // 角色差异(订阅范围/全库)在分区文案内分支
  mcp: '把内容接进你的 Agent 工具',
  skill: '装进 Agent 的每日资讯技能',
  sync: '部署端之间搬运文章归档',
  about: '产品与账户信息',
};

export default function SettingsModal({ open, initialSection, onClose, theme, onThemeChange, runtimeInfo, username, avatar, onUserUpdated, onLogout, showToast, onArticlesChanged }) {
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

  // 导航分组(与源栏编辑分层同语法);「关于」不入组,沉底在 spacer 之后。
  const navGroups = useMemo(() => [
    {
      label: '通用',
      items: [
        { id: 'account', label: '账户', icon: User },
        { id: 'appearance', label: '外观', icon: Palette },
      ],
    },
    {
      label: '接入集成',
      items: [
        { id: 'feed', label: '聚合接口', icon: Rss },
        { id: 'mcp', label: 'MCP 接入', icon: Plug2 },
        { id: 'skill', label: 'Agent 技能包', icon: Package },
      ],
    },
    {
      label: '管理',
      items: [
        { id: 'sync', label: '数据同步', icon: FileText, show: isAdmin && (collectorEnabled || readerEnabled) },
      ],
    },
  ].map(group => ({ ...group, items: group.items.filter(item => item.show !== false) }))
    .filter(group => group.items.length > 0), [collectorEnabled, isAdmin, readerEnabled]);

  const aboutItem = useMemo(() => ({ id: 'about', label: '关于', icon: Info }), []);
  const sections = useMemo(
    () => [...navGroups.flatMap(group => group.items), aboutItem],
    [navGroups, aboutItem],
  );

  const [active, setActive] = useState('account');
  const panelRef = useRef(null);
  useModalA11y(open && mounted, onClose, panelRef);

  useEffect(() => {
    if (!open) return undefined;
    setActive(initialSection || 'account');
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open, initialSection]);

  if (!mounted) return null;

  const activeSection = sections.find(s => s.id === active) || sections[0];

  const navButton = (section) => (
    <button
      key={section.id}
      type="button"
      onClick={() => setActive(section.id)}
      className={`sett-nav-btn ${activeSection.id === section.id ? 'is-on' : ''}`}
    >
      <section.icon /> {section.label}
    </button>
  );

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
          {navGroups.map(group => (
            <div key={group.label} className="sett-nav-sec">
              <div className="sett-nav-group">{group.label}</div>
              {group.items.map(navButton)}
            </div>
          ))}
          <div className="sett-nav-spacer" />
          {navButton(aboutItem)}
          <div className="sett-nav-foot">{isAdmin ? '哆啦美 · 归档中枢' : '哆啦美 · AI 资讯阅读器'}</div>
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
            {activeSection.id === 'feed' && (
              <FeedTokenSection showToast={showToast} isAdmin={isAdmin} />
            )}
            {activeSection.id === 'mcp' && (
              <McpAccessSection
                showToast={showToast}
                ragEnabled={ragEnabled}
                canManage={isAdmin && collectorEnabled}
                onClose={onClose}
              />
            )}
            {activeSection.id === 'skill' && (
              <SkillSection />
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
