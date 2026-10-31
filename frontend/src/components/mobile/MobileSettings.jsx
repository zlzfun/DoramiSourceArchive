import { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpenText, ChevronRight, Info, Lock, LogOut, MessageSquare, Palette, User, UserRound } from 'lucide-react';
import { markFeedbackSeen } from '../../api';
import { useLayerHistory } from '../../hooks/useLayerHistory';
import { useConfirm } from '../../hooks/useConfirm';
import { avatarHue, avatarInitial } from '../../utils/avatarColor';
import LayerPage from '../layer/LayerPage';
import { playPushExit } from '../../utils/layerExit';
import InstallApp from '../InstallApp';
import AccountSection from '../settings/AccountSection';
import AppearanceSection from '../settings/AppearanceSection';
import ReadingSection from '../settings/ReadingSection';
import FeedbackSection from '../settings/FeedbackSection';
import AboutSection from '../settings/AboutSection';
import { GuestAccountSection, GuestGate } from '../settings/GuestSections';
import { GUEST_LOCKED_SECTIONS } from '../../utils/guestCopy';
import { HeadBack, HeadTitle, MobileHeader } from './MobileHeader';

// 手机设置页(issue #230 第 2 批;样页「设置(从头像进入)」,拍板 20 / 27 / 29 / 35):
// 从四个 Tab 页头的头像推入的整页层,盖住底栏与播放坞;返回钮落在头像原位,标题「设置」在标题位(页头一行 52px)。
// 一级页 = 原「我的」(资料卡、添加到主屏幕、退出登录)+ 原设置栈一级页;二级页(分区)照旧往里推,
// 分区组件与桌面设置柜完全同源(components/settings/)。手机不列「订阅源 OPML」与「接入集成」(拍板 35),
// 桌面设置柜不动;guestCopy.GUEST_LOCKED_SECTIONS 是两边共用的,键不删(手机里只是没有入口)。
// 门控按移动面口径:移动壳恒为阅读器界面,管理组(数据同步 / 凭据)不出现;反馈仅读者账号(同柜)。
// 游客(issue #213)同桌面柜:资料卡换游客卡 + 登录,需要账号的分区挂小锁、进去是登录入口。
// 反馈回复不外露(拍板 29):数字角标只在「反馈与建议」这一行,头像不挂点。
const HINTS = {
  account: '身份、头像与登录凭据',
  reading: '标题语言等阅读偏好',
  feedback: '把想法与问题告诉管理员',
  about: '产品与账户信息',
};
const THEME_LABEL = { light: '亮色', dark: '暗色', system: '跟随系统' };

export default function MobileSettings({
  open,
  initialSection = 'account',
  onClose,
  theme,
  onThemeChange,
  runtimeInfo,
  username,
  avatar,
  subscribedCount = null,
  onUserUpdated,
  onLogout,
  showToast,
  feedbackUnread = 0,
  onFeedbackSeen,
  onGuestLogin,
}) {
  const confirm = useConfirm();
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
        { id: 'feedback', label: '反馈与建议', icon: MessageSquare, show: !isAdmin },
      ],
    },
    {
      label: '关于',
      install: true,   // 「添加到主屏幕」排在本组最前(InstallApp 自己判断能不能装,不能装不渲染)
      items: [
        { id: 'about', label: '关于', icon: Info },
      ],
    },
  ].map((group) => ({ ...group, items: group.items.filter((item) => item.show !== false) }))
    .filter((group) => group.items.length > 0), [isAdmin]);

  const sections = useMemo(() => navGroups.flatMap((g) => g.items), [navGroups]);

  // null = 一级页;非空 = 二级分区页。深链(如反馈)直落二级;手机没有的分区(OPML / 接入集成)落一级页。
  const [sectionId, setSectionId] = useState(null);
  useEffect(() => {
    if (!open) return;
    setSectionId(initialSection && sections.some((s) => s.id === initialSection) ? initialSection : null);
    // 深链默认落地 'account' 属常规打开:进一级页而非直落账户分区
    if (!initialSection || initialSection === 'account') setSectionId(null);
  }, [open, initialSection, sections]);

  // 返回键握手:设置根一层(层原语 LayerPage 登记)+ 分区页一层(子页,本组件自己登记;根先开、子页后开,返回由内向外逐级退)。
  // Esc 与返回同序:先退分区、再关设置。
  // 退回一级页时分区页反向推出(批 4a,审计 M-33 / 批 2 遗留「设置页只有推入、无反向退场」):换掉之前留一个替身往右退并淡出
  const paneRef = useRef(null);
  const backToRoot = () => {
    playPushExit(paneRef.current, { absolute: true });
    setSectionId(null);
  };
  useLayerHistory(open && Boolean(sectionId), backToRoot);
  const stepBack = () => {
    if (sectionId) backToRoot();
    else onClose?.();
  };

  // 滚过 4px 页头换镀层底(与 Tab 页同一规则);换页回到未滚动
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => { setScrolled(false); }, [sectionId, open]);
  const onScroll = (event) => setScrolled(event.currentTarget.scrollTop > 4);

  // 打开反馈分区即视为已读(与桌面设置柜同约定)
  useEffect(() => {
    if (open && !isGuest && sectionId === 'feedback' && feedbackUnread > 0) {
      markFeedbackSeen();
      onFeedbackSeen?.();
    }
  }, [open, isGuest, sectionId, feedbackUnread, onFeedbackSeen]);

  if (!open) return null;

  const active = sections.find((s) => s.id === sectionId) || null;
  const handleLogout = async () => {
    if (await confirm('确定退出登录？')) onLogout?.();
  };

  const hintOf = (item) => {
    if (item.id === 'appearance') return THEME_LABEL[theme] || '';
    if (isGuest && item.id === 'account') return '当前以游客身份浏览';
    return HINTS[item.id] || '';
  };

  const profile = isGuest ? (
    <div className="m-profile">
      <span className="m-profile-av is-guest" aria-hidden="true"><UserRound /></span>
      <div className="m-profile-txt">
        <p className="m-profile-name">游客</p>
        <p className="m-profile-sub">登录后可收藏、订阅、设置兴趣</p>
      </div>
      <button type="button" className="action-button action-button-primary m-note-btn guest-login-btn" onClick={onGuestLogin}>
        登录
      </button>
    </div>
  ) : (
    <div className="m-profile">
      {avatar ? (
        <img src={avatar} alt="" className="m-profile-av" draggable={false} />
      ) : (
        <span className="m-profile-av avatar-letter" style={{ '--avatar-h': avatarHue(username) }} aria-hidden="true">
          {avatarInitial(username)}
        </span>
      )}
      <div className="m-profile-txt">
        <p className="m-profile-name">{username || '—'}</p>
        <p className="m-profile-sub">
          {subscribedCount != null ? `已订阅 ${subscribedCount} 个来源` : accountRoleLabel}
        </p>
      </div>
    </div>
  );

  // 整页层(LayerPage):dialog 语义、焦点落在面板、下层(含播放坞——设置盖住它)整片 inert。
  // 两级共用同一个根元素,焦点范围与 inert 不随级别换节点。
  return (
    <LayerPage
      className={`m-page m-sett ${scrolled ? 'is-scrolled' : ''}`}
      role="dialog"
      modal
      inert="all"
      label="设置"
      onClose={onClose}
      onEscape={stepBack}
      exit="push"
    >
      {active ? (
        <>
          <MobileHeader
            lead={<HeadBack label="返回设置" onClick={backToRoot} />}
            title={<HeadTitle>{active.label}</HeadTitle>}
          />
          <div key={active.id} ref={paneRef} className="m-page-scroll m-sett-body m-sett-pane" onScroll={onScroll}>
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
            {active.id === 'feedback' && !isGuest && <FeedbackSection showToast={showToast} />}
            {active.id === 'about' && (
              <AboutSection accountRoleLabel={accountRoleLabel} isAdmin={false} version={runtimeInfo?.version} build={runtimeInfo?.build} />
            )}
          </div>
        </>
      ) : (
        <>
          <MobileHeader
            lead={<HeadBack label="返回" onClick={onClose} />}
            title={<HeadTitle>设置</HeadTitle>}
          />
          <div className="m-page-scroll m-sett-list" onScroll={onScroll}>
            {profile}
            {navGroups.map((group) => (
              <section key={group.label} aria-label={group.label}>
                <p className="m-group-label">{group.label}</p>
                <div className="m-group">
                  {group.install && <InstallApp />}
                  {group.items.map((item) => (
                    <button key={item.id} type="button" className="m-row" onClick={() => setSectionId(item.id)}>
                      <item.icon aria-hidden="true" />
                      <span className="m-row-label">{item.label}</span>
                      {item.id === 'feedback' && !isGuest && feedbackUnread > 0 && (
                        <span className="m-row-badge" aria-label={`${feedbackUnread} 条新回复`}>
                          {feedbackUnread > 99 ? '99+' : feedbackUnread}
                        </span>
                      )}
                      <span className="m-row-meta m-sett-hint">{hintOf(item)}</span>
                      {guestLocked(item.id) && <Lock className="m-row-lock" aria-label="需要登录" />}
                      <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
                    </button>
                  ))}
                </div>
              </section>
            ))}
            {!isGuest && (
              <div className="m-group">
                <button type="button" className="m-row is-danger" onClick={handleLogout}>
                  <LogOut aria-hidden="true" />
                  <span className="m-row-label">退出登录</span>
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </LayerPage>
  );
}
