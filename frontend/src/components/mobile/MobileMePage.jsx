import {
  ChevronRight,
  Compass,
  LogOut,
  MessageSquare,
  Settings,
  Star,
  SunMoon,
} from 'lucide-react';
import { avatarInitial, avatarHue } from '../../utils/avatarColor';
import { useConfirm } from '../../hooks/useConfirm';

// 「我的」页(移动波 Wave2,样页画面③):承接桌面 RailUserFlyout 的全部职责
// (主题/设置/反馈/退出)+ 收藏与发现入口。退出改确认对话框——触屏上 hover 展开的
// 两击防呆会吞首击,防呆语义换壳不换心。「发现」从这里进:低频目的地不占 Tab。
export default function MobileMePage({
  account,
  subscribedCount,
  favoriteCount,
  feedbackUnread = 0,
  themePref,
  onSetTheme,
  onShowFavorites,
  onOpenDiscover,
  onOpenSettings,
  onLogout,
}) {
  const confirm = useConfirm();
  const isAdmin = account?.role === 'admin';

  const handleLogout = async () => {
    if (await confirm('确定退出登录？')) onLogout?.();
  };

  return (
    <div className="m-me" role="region" aria-label="我的">
      <div className="m-me-card">
        {account?.avatar ? (
          <img src={account.avatar} alt="头像" className="m-avatar-img" />
        ) : (
          <div className="m-avatar avatar-letter" style={{ '--avatar-h': avatarHue(account?.username) }}>
            {avatarInitial(account?.username)}
          </div>
        )}
        <div className="min-w-0">
          <div className="m-me-name">{account?.username || '—'}</div>
          <div className="m-me-sub">已订阅 {subscribedCount} 个来源</div>
        </div>
      </div>

      <div className="m-group">
        <button type="button" className="m-row" onClick={onShowFavorites}>
          <Star aria-hidden="true" />
          <span className="m-row-label">收藏</span>
          {favoriteCount > 0 && <span className="m-row-meta">{favoriteCount}</span>}
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
        <button type="button" className="m-row" onClick={onOpenDiscover}>
          <Compass aria-hidden="true" />
          <span className="m-row-label">发现更多来源</span>
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
      </div>

      <div className="m-group">
        {/* 反馈是「读者→管理员」通道,admin 会话无此入口(与桌面设置柜同口径) */}
        {!isAdmin && (
          <button type="button" className="m-row" onClick={() => onOpenSettings?.('feedback')}>
            <MessageSquare aria-hidden="true" />
            <span className="m-row-label">反馈与建议</span>
            {feedbackUnread > 0 && <span className="m-row-dot" aria-hidden="true" />}
            <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
          </button>
        )}
        <div className="m-row">
          <SunMoon aria-hidden="true" />
          <span className="m-row-label">外观</span>
          <span className="m-seg3" role="radiogroup" aria-label="外观">
            {[['light', '亮'], ['dark', '暗'], ['system', '跟随']].map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={themePref === value}
                className={themePref === value ? 'is-on' : ''}
                onClick={() => onSetTheme?.(value)}
              >
                {label}
              </button>
            ))}
          </span>
        </div>
        <button type="button" className="m-row" onClick={() => onOpenSettings?.()}>
          <Settings aria-hidden="true" />
          <span className="m-row-label">设置</span>
          <span className="m-row-meta">账户 · 接入集成</span>
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
      </div>

      <div className="m-group">
        <button type="button" className="m-row is-danger" onClick={handleLogout}>
          <LogOut aria-hidden="true" />
          <span className="m-row-label">退出登录</span>
        </button>
      </div>
    </div>
  );
}
