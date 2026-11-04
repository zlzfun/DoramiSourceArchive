import { useLayoutEffect, useRef } from 'react';
import { useLayer } from './useLayer';
import { playPushExitAfterUnmount } from '../../utils/layerExit';
import { compactLayoutMatches } from '../../hooks/useCompactLayout';

// 整屏推入页(page):正文页 / 发现页 / 设置栈 / 游客叠层登录。面板自己就是整层,焦点落在面板容器上
// (读屏念出页面名,不在 Safari 上给返回钮画焦点环),下层整片 inert(缺省保留页面层之上的播放条;
// 盖住播放条的页传 inert="all")。出口:页里的返回钮(调用方放)/ Esc / 系统返回。
// 只在 open 时渲染时可以不传 open(缺省 true)。
// exit="push"(issue #230 批 4a,审计 M-33):推入的页(设置页、订阅 Tab 的推入页)退场时反向推出——卸载的当下留一个替身往右退
// 并淡出(utils/layerExit);调用方把整层卸掉(条件渲染)才播,版式切到桌面四栏时不播。
export default function LayerPage({
  open = true,
  onClose,
  onEscape = null,
  label,
  role = 'region',
  modal = false,
  history = 'compact',
  inert,
  exit = null,
  className = '',
  children,
  ...rest
}) {
  const panelRef = useRef(null);
  useLayer({ open, onClose, onEscape, kind: 'page', panelRef, history, inert });
  // 卸载时(层整个被拿掉)React 先跑这里的清理、再摘 DOM:此刻面板还在原位,克隆得到当前画面
  useLayoutEffect(() => {
    if (!open || exit !== 'push') return undefined;
    const panel = panelRef.current;
    return () => {
      if (panel?.isConnected && compactLayoutMatches()) playPushExitAfterUnmount(panel);
    };
  }, [open, exit]);

  if (!open) return null;
  return (
    <div
      ref={panelRef}
      className={className}
      role={role}
      aria-modal={modal ? 'true' : undefined}
      aria-label={label}
      tabIndex={-1}
      data-layer-panel=""
      {...rest}
    >
      {children}
    </div>
  );
}
