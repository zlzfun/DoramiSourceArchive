import { useEffect, useRef, useState } from 'react';
import { Power } from 'lucide-react';
import { avatarInitial, avatarHue } from '../utils/avatarColor';

// 轨底用户滑出菜单(2026-07-24 拍板:头像与设置钮功能重复——改为「常态只见头像,
// hover/聚焦时工具钮向上滑出、头像同时变为关机样式的退出钮」)。
// 管理面应用导轨与阅读器视图轨(standalone)共用;children = 滑出的工具钮
// (主题/设置/界面切换等,沿用 .reader-vrail-btn 语法)。键盘可达:focus-within 同样展开。
//
// 退出防呆(2026-07-25):首次点击不退出——只进入 4 秒「待确认」态(头像红圈脉动,
// 调用方经 onLogoutHint 弹底部 Toast 提示),窗口内再点一次才真正退出;
// 光标移出滑出菜单或超时即解除。实测首次用户会因好奇点头像而误退,故加此闸。
const ARM_WINDOW_MS = 4000;

export default function RailUserFlyout({ avatar, username, roleLabel, onLogout, onLogoutHint, notify = false, children }) {
  const [armed, setArmed] = useState(false);
  const armTimerRef = useRef(null);

  const disarm = () => {
    if (armTimerRef.current) { clearTimeout(armTimerRef.current); armTimerRef.current = null; }
    setArmed(false);
  };
  useEffect(() => () => { if (armTimerRef.current) clearTimeout(armTimerRef.current); }, []);

  const handleAvatarClick = () => {
    if (armed) { disarm(); onLogout?.(); return; }
    setArmed(true);
    onLogoutHint?.();
    armTimerRef.current = setTimeout(() => { armTimerRef.current = null; setArmed(false); }, ARM_WINDOW_MS);
  };

  return (
    <div className="rail-flyout" onMouseLeave={disarm}>
      <div className="rail-flyout-items">
        {children}
      </div>
      <button
        type="button"
        className={`reader-vrail-avatar rail-flyout-avatar ${armed ? 'is-armed' : ''}`}
        onClick={handleAvatarClick}
        aria-label={armed ? '再次点击以退出登录' : '退出登录(点击两次确认)'}
        title={armed
          ? '再次点击以退出登录'
          : `${username || '账号'}${roleLabel ? ` · ${roleLabel}` : ''} · 点击两次退出登录`}
      >
        {/* 轻通知点(如:反馈有新回复):挂头像因 flyout 常态收起只见头像 */}
        {notify && <span className="rail-avatar-dot" aria-hidden="true" />}
        <span
          className={`rail-avatar-face${avatar ? '' : ' avatar-letter'}`}
          style={avatar ? undefined : { '--avatar-h': avatarHue(username) }}
          aria-hidden="true"
        >
          {avatar ? <img src={avatar} alt="" /> : <span>{avatarInitial(username)}</span>}
        </span>
        <span className="rail-avatar-power" aria-hidden="true">
          <Power />
        </span>
      </button>
    </div>
  );
}
