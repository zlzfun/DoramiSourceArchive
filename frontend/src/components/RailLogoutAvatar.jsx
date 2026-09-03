import { useEffect, useRef, useState } from 'react';
import { Power } from 'lucide-react';
import { avatarInitial, avatarHue } from '../utils/avatarColor';

// 轨底头像 = 退出钮(可发现性波 v3.45,取代 hover 滑出的 RailUserFlyout)。
// 2026-07-24 曾把主题/设置/界面切换收进头像的 hover 滑出菜单(常态只见头像);
// 上量后新用户反馈「反馈/设置常态不可见」,且 hover 触发对触控板外接屏、触屏本、
// 键盘用户都是坏的——工具钮回到轨上常态可见(带标签),头像只剩一件事:退出。
//
// 退出防呆(v3.22 两击机制原样,只去掉 hover 前置):首次点击不退出——翻面为关机钮
// 进入 4 秒「待确认」态(红圈脉动,调用方经 onLogoutHint 弹 Toast 提示),
// 窗口内再点一次才真正退出;超时 / Esc / 点击别处即回落。
const ARM_WINDOW_MS = 4000;

export default function RailLogoutAvatar({ avatar, username, roleLabel, onLogout, onLogoutHint }) {
  const [armed, setArmed] = useState(false);
  const armTimerRef = useRef(null);
  const btnRef = useRef(null);

  const disarm = () => {
    if (armTimerRef.current) { clearTimeout(armTimerRef.current); armTimerRef.current = null; }
    setArmed(false);
  };
  useEffect(() => () => { if (armTimerRef.current) clearTimeout(armTimerRef.current); }, []);

  // 待确认态下点击别处 / Esc 回落:头像不再有 hover 容器承接「移出即解除」,改监听文档
  useEffect(() => {
    if (!armed) return undefined;
    const onDown = (e) => { if (!btnRef.current?.contains(e.target)) disarm(); };
    const onKey = (e) => { if (e.key === 'Escape') disarm(); };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [armed]);

  const handleClick = () => {
    if (armed) { disarm(); onLogout?.(); return; }
    setArmed(true);
    onLogoutHint?.();
    armTimerRef.current = setTimeout(() => { armTimerRef.current = null; setArmed(false); }, ARM_WINDOW_MS);
  };

  return (
    <button
      ref={btnRef}
      type="button"
      className={`reader-vrail-avatar rail-logout${armed ? ' is-armed' : ''}`}
      onClick={handleClick}
      aria-label={armed ? '再次点击以退出登录' : '退出登录(点击两次确认)'}
      title={armed
        ? '再次点击以退出登录'
        : `${username || '账号'}${roleLabel ? ` · ${roleLabel}` : ''} · 点击两次退出登录`}
    >
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
  );
}
