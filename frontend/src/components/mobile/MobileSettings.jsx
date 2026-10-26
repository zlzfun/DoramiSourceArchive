import { useEffect, useMemo, useState } from 'react';
import { ArrowDownUp, BookOpenText, ChevronLeft, ChevronRight, Info, Lock, MessageSquare, Package, Palette, Plug2, Rss, User, X } from 'lucide-react';
import { markFeedbackSeen } from '../../api';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import AccountSection from '../settings/AccountSection';
import AppearanceSection from '../settings/AppearanceSection';
import ReadingSection from '../settings/ReadingSection';
import FeedTokenSection from '../settings/FeedTokenSection';
import McpAccessSection from '../settings/McpAccessSection';
import SkillSection from '../settings/SkillSection';
import FeedbackSection from '../settings/FeedbackSection';
import AboutSection from '../settings/AboutSection';
import OpmlSection, { OpmlPreview } from '../settings/OpmlSection';
import { GuestAccountSection, GuestGate } from '../settings/GuestSections';
import { GUEST_LOCKED_SECTIONS } from '../../utils/guestCopy';

// 移动设置栈(移动波 Wave3):桌面 880×576 设置柜的页面栈翻译——一级=分组清单页,
// 二级=分区页(back 出栈)。分区组件与桌面设置柜完全同源(components/settings/),
// 门控按移动面口径:移动壳恒为阅读器界面(readerSurface),管理组(数据同步/凭据)
// 不出现——admin 手机上与读者同观感,管理面回桌面操作;反馈仅读者账号(同柜)。
// 游客(issue #213)同桌面柜:账户换游客卡 + 登录,需要账号的分区挂小锁、进去是登录入口。
const HINTS = {
  account: '身份、头像与登录凭据',
  appearance: '主题与动效',
  reading: '标题语言等阅读偏好',
  opml: 'OPML 迁移',
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
  onGuestLogin,
}) {
  const accountRole = runtimeInfo?.account_role;
  const isAdmin = accountRole === 'admin';
  const isGuest = accountRole === 'guest';
  const accountRoleLabel = isAdmin ? '管理员' : accountRole === 'user' ? '读者' : isGuest ? '游客' : '—';
  const guestLocked = (id) => isGuest && Boolean(GUEST_LOCKED_SECTIONS[id]);

  const navGroups = useMemo(() => [
    {
      label: '通用',
      items: [
        { id: 'account', label: '账户', icon: User },
        { id: 'appearance', label: '外观', icon: Palette },
        { id: 'reading', label: '阅读', icon: BookOpenText },
        { id: 'opml', label: '订阅源 OPML', icon: ArrowDownUp },
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

  // 导入预览是设置栈里单独一屏,返回先离开预览,再回清单,再关设置。
  const [opmlPreview, setOpmlPreview] = useState(null);
  useEffect(() => {
    if (!open || sectionId !== 'opml') setOpmlPreview(null);
  }, [open, sectionId]);

  // 返回键握手:设置根一层 + 分区页一层 + 预览一层
  useLayerHistory(open, onClose);
  useLayerHistory(open && Boolean(sectionId), () => setSectionId(null));
  useLayerHistory(open && Boolean(opmlPreview), () => setOpmlPreview(null));

  // 打开反馈分区即视为已读(与桌面设置柜同约定)
  useEffect(() => {
    if (open && !isGuest && sectionId === 'feedback' && feedbackUnread > 0) {
      markFeedbackSeen();
      onFeedbackSeen?.();
    }
  }, [open, isGuest, sectionId, feedbackUnread, onFeedbackSeen]);

  if (!open) return null;

  const active = sections.find((s) => s.id === sectionId) || null;

  if (opmlPreview) {
    return (
      <div className="m-page m-sett" role="dialog" aria-modal="true" aria-label="导入预览">
        <div className="m-topbar on-pane">
          <button type="button" className="m-iconbtn" onClick={() => setOpmlPreview(null)} aria-label="返回订阅源 OPML">
            <ChevronLeft />
          </button>
          <span className="m-title">导入预览</span>
        </div>
        <OpmlPreview
          layout="mobile"
          filename={opmlPreview.filename}
          opml={opmlPreview.opml}
          plan={opmlPreview.plan}
          showToast={showToast}
          onCancel={() => setOpmlPreview(null)}
          onDone={() => setOpmlPreview(null)}
        />
      </div>
    );
  }

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
            {isGuest && active.id === 'account' && <GuestAccountSection onLogin={onGuestLogin} />}
            {guestLocked(active.id) && <GuestGate sectionId={active.id} onLogin={onGuestLogin} />}
            {!isGuest && active.id === 'account' && (
              <AccountSection
                username={username}
                avatar={avatar}
                accountRoleLabel={accountRoleLabel}
                isAdmin={isAdmin}
                defaultSurface={runtimeInfo?.default_surface}
                passwordLoginEnabled={runtimeInfo?.password_login_enabled !== false}
                onUserUpdated={onUserUpdated}
                onLogout={onLogout}
                showToast={showToast}
              />
            )}
            {active.id === 'appearance' && (
              <AppearanceSection theme={theme} onThemeChange={onThemeChange} />
            )}
            {active.id === 'reading' && <ReadingSection />}
            {active.id === 'opml' && !isGuest && (
              <OpmlSection showToast={showToast} onPushPreview={setOpmlPreview} />
            )}
            {/* 移动壳恒为阅读器观感:isAdmin/canManage 一律走读者口径(adminConsole=false) */}
            {active.id === 'feed' && !isGuest && (
              <FeedTokenSection showToast={showToast} isAdmin={false} />
            )}
            {active.id === 'mcp' && !isGuest && (
              <McpAccessSection showToast={showToast} canManage={false} />
            )}
            {active.id === 'skill' && !isGuest && <SkillSection />}
            {active.id === 'feedback' && !isGuest && <FeedbackSection showToast={showToast} />}
            {active.id === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} isAdmin={false} version={runtimeInfo?.version} build={runtimeInfo?.build} />
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
                      <span className="m-row-meta m-sett-hint">{isGuest && item.id === 'account' ? '当前以游客身份浏览' : HINTS[item.id]}</span>
                      {guestLocked(item.id) && <Lock className="m-row-lock" aria-label="需要登录" />}
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
