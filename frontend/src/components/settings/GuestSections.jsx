import { Lock, LogIn, UserRound } from 'lucide-react';
import { GUEST_LOCKED_SECTIONS } from '../../utils/guestCopy';

// 游客模式(issue #213):设置柜里游客看到的两种分区。
// - GuestAccountSection:「账户」区换成游客卡 + 登录,名字下一句登录后多出什么(与手机壳资料卡同一句;
//   原先另有「游客可以 / 登录后还能」两段功能清单,issue #291 收成这一句)。
// - GuestGate:需要账号的分区(OPML / 反馈 / 接入集成)不渲染原组件、不发请求,只放登录入口。
// 「外观」「关于」游客照常可用(外观本就只存本机)。

export function GuestAccountSection({ onLogin }) {
  return (
    <div>
      <div className="sett-row">
        <span className="sett-avatar guest-avatar" aria-hidden="true"><UserRound /></span>
        <span className="sett-id">
          <span className="sett-acct-name">游客</span>
          <div className="sett-sub">登录后可收藏、订阅、设置兴趣</div>
        </span>
        <span className="sett-ctl">
          <button type="button" onClick={onLogin} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            <LogIn className="h-3.5 w-3.5" /> 登录
          </button>
        </span>
      </div>
    </div>
  );
}

export function GuestGate({ sectionId, onLogin }) {
  return (
    <div className="guest-gate">
      <Lock aria-hidden="true" />
      <p className="guest-gate-title">登录后可用</p>
      <p className="guest-gate-sub">{GUEST_LOCKED_SECTIONS[sectionId]}</p>
      <button type="button" onClick={onLogin} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
        登录
      </button>
    </div>
  );
}
