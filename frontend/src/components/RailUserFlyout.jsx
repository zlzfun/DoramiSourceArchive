import { Power } from 'lucide-react';

// 轨底用户滑出菜单(2026-07-24 拍板:头像与设置钮功能重复——改为「常态只见头像,
// hover/聚焦时工具钮向上滑出、头像同时变为关机样式的退出钮,点击即退」)。
// 管理面应用导轨与阅读器视图轨(standalone)共用;children = 滑出的工具钮
// (主题/设置/界面切换等,沿用 .reader-vrail-btn 语法)。键盘可达:focus-within 同样展开。
export default function RailUserFlyout({ avatar, avatarText, username, roleLabel, onLogout, children }) {
  return (
    <div className="rail-flyout">
      <div className="rail-flyout-items">
        {children}
      </div>
      <button
        type="button"
        className="reader-vrail-avatar rail-flyout-avatar"
        onClick={onLogout}
        aria-label="退出登录"
        title={`${username || '账号'}${roleLabel ? ` · ${roleLabel}` : ''} · 点击退出登录`}
      >
        <span className="rail-avatar-face" aria-hidden="true">
          {avatar
            ? <img src={avatar} alt="" />
            : <span>{avatarText || (username || '?').slice(0, 2).toUpperCase()}</span>}
        </span>
        <span className="rail-avatar-power" aria-hidden="true">
          <Power />
        </span>
      </button>
    </div>
  );
}
