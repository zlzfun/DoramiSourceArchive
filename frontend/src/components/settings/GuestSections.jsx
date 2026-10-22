import { Lock, LogIn, UserRound } from 'lucide-react';
import { GUEST_LOCKED_SECTIONS } from '../../utils/guestCopy';

// 游客模式(issue #213):设置柜里游客看到的两种分区。
// - GuestAccountSection:「账户」区换成游客卡 + 登录,下面两行说清游客能做什么、登录后还能做什么。
// - GuestGate:需要账号的分区(OPML / 反馈 / 接入集成)不渲染原组件、不发请求,只放登录入口。
// 「外观」「关于」游客照常可用(外观本就只存本机)。

export function GuestAccountSection({ onLogin }) {
  return (
    <div>
      <div className="sett-row">
        <span className="sett-avatar guest-avatar" aria-hidden="true"><UserRound /></span>
        <span className="sett-id">
          <span className="sett-acct-name">游客</span>
          <div className="sett-sub"><span className="sett-role-chip">未登录</span></div>
        </span>
        <span className="sett-ctl">
          <button type="button" onClick={onLogin} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            <LogIn className="h-3.5 w-3.5" /> 登录
          </button>
        </span>
      </div>
      <div className="sett-row is-block">
        <span className="sett-id">
          <span className="sett-lbl">游客可以</span>
          <div className="sett-sub">阅读全部内容，使用 AI 翻译、问答与速读，浏览发现页与榜单</div>
        </span>
      </div>
      <div className="sett-row is-block">
        <span className="sett-id">
          <span className="sett-lbl">登录后还能</span>
          <div className="sett-sub">收藏、订阅、设置兴趣，早报按你的订阅与兴趣编排；接入聚合接口与 MCP</div>
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
