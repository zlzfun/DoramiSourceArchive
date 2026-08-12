import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Info, MessageSquare, Package, Palette, Plug2, Rss, User, X } from 'lucide-react';
import { markFeedbackSeen } from '../../api';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import AccountSection from '../settings/AccountSection';
import AppearanceSection from '../settings/AppearanceSection';
import FeedTokenSection from '../settings/FeedTokenSection';
import McpAccessSection from '../settings/McpAccessSection';
import SkillSection from '../settings/SkillSection';
import FeedbackSection from '../settings/FeedbackSection';
import AboutSection from '../settings/AboutSection';

// 移动设置栈(移动波 Wave3):桌面 880×576 设置柜的页面栈翻译——一级=分组清单页,
// 二级=分区页(back 出栈)。分区组件与桌面设置柜完全同源(components/settings/),
// 门控按移动面口径:移动壳恒为阅读器界面(readerSurface),管理组(数据同步/凭据)
// 不出现——admin 手机上与读者同观感,管理面回桌面操作;反馈仅读者账号(同柜)。
const HINTS = {
  account: '身份、头像与登录凭据',
  appearance: '亮暗主题偏好',
  feed: '一枚 dfeed_ 令牌与聚合拉取接口',
  mcp: '把内容接进你的 Agent 工具',
  skill: '装进 Agent 的每日资讯技能',
  feedback: '把想法与问题告诉管理员',
  about: '产品与账户信息',
};

export default function MobileSettings({
  open,
  initialSection = 'account',
  onClose,
  theme,
  onThemeChange,
  runtimeInfo,
  username,
  avatar,
  onUserUpdated,
  onLogout,
  showToast,
  feedbackUnread = 0,
  onFeedbackSeen,
}) {
  const accountRole = runtimeInfo?.account_role;
  const isAdmin = accountRole === 'admin';
  const accountRoleLabel = isAdmin ? '管理员' : accountRole === 'user' ? '读者' : '—';

  const navGroups = useMemo(() => [
    {
      label: '通用',
      items: [
        { id: 'account', label: '账户', icon: User },
        { id: 'appearance', label: '外观', icon: Palette },
        { id: 'feedback', label: '反馈与建议', icon: MessageSquare, show: !isAdmin },
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
      label: '关于',
      items: [
        { id: 'about', label: '关于', icon: Info },
      ],
    },
  ].map((group) => ({ ...group, items: group.items.filter((item) => item.show !== false) }))
    .filter((group) => group.items.length > 0), [isAdmin]);

  const sections = useMemo(() => navGroups.flatMap((g) => g.items), [navGroups]);

  // null = 一级清单页;非空 = 二级分区页。深链(我的页「反馈与建议」)直落二级。
  const [sectionId, setSectionId] = useState(null);
  useEffect(() => {
    if (!open) return;
    setSectionId(initialSection && sections.some((s) => s.id === initialSection) ? initialSection : null);
    // 深链默认落地 'account' 属常规打开:进清单页而非直落账户分区
    if (!initialSection || initialSection === 'account') setSectionId(null);
  }, [open, initialSection, sections]);

  // 返回键握手:设置根一层 + 分区页一层(back 先回清单,再关设置)
  useLayerHistory(open, onClose);
  useLayerHistory(open && Boolean(sectionId), () => setSectionId(null));

  // 打开反馈分区即视为已读(与桌面设置柜同约定)
  useEffect(() => {
    if (open && sectionId === 'feedback' && feedbackUnread > 0) {
      markFeedbackSeen();
      onFeedbackSeen?.();
    }
  }, [open, sectionId, feedbackUnread, onFeedbackSeen]);

  if (!open) return null;

  const active = sections.find((s) => s.id === sectionId) || null;

  return (
    <div className="m-page m-sett" role="dialog" aria-modal="true" aria-label="设置">
      {active ? (
        <>
          <div className="m-topbar on-pane">
            <button type="button" className="m-iconbtn" onClick={() => setSectionId(null)} aria-label="返回设置">
              <ChevronLeft />
            </button>
            <span className="m-title">{active.label}</span>
          </div>
          <div className="m-page-scroll m-sett-body">
            {active.id === 'account' && (
              <AccountSection
                username={username}
                avatar={avatar}
                accountRoleLabel={accountRoleLabel}
                isAdmin={isAdmin}
                defaultSurface={runtimeInfo?.default_surface}
                onUserUpdated={onUserUpdated}
                onLogout={onLogout}
                showToast={showToast}
              />
            )}
            {active.id === 'appearance' && (
              <AppearanceSection theme={theme} onThemeChange={onThemeChange} />
            )}
            {/* 移动壳恒为阅读器观感:isAdmin/canManage 一律走读者口径(adminConsole=false) */}
            {active.id === 'feed' && (
              <FeedTokenSection showToast={showToast} isAdmin={false} />
            )}
            {active.id === 'mcp' && (
              <McpAccessSection showToast={showToast} canManage={false} />
            )}
            {active.id === 'skill' && <SkillSection />}
            {active.id === 'feedback' && <FeedbackSection showToast={showToast} />}
            {active.id === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} isAdmin={false} version={runtimeInfo?.version} />
            )}
          </div>
        </>
      ) : (
        <>
          <div className="m-topbar on-pane">
            <button type="button" className="m-iconbtn" onClick={onClose} aria-label="关闭设置">
              <X />
            </button>
            <span className="m-title">设置</span>
          </div>
          <div className="m-page-scroll m-sett-list">
            {navGroups.map((group) => (
              <section key={group.label}>
                <div className="m-sett-group">{group.label}</div>
                <div className="m-group">
                  {group.items.map((item) => (
                    <button key={item.id} type="button" className="m-row" onClick={() => setSectionId(item.id)}>
                      <item.icon aria-hidden="true" />
                      <span className="m-row-label">{item.label}</span>
                      {item.id === 'feedback' && feedbackUnread > 0 && (
                        <span className="m-row-badge">{feedbackUnread > 99 ? '99+' : feedbackUnread}</span>
                      )}
                      <span className="m-row-meta m-sett-hint">{HINTS[item.id]}</span>
                      <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
