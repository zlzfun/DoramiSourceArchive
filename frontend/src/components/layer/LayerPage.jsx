import { useRef } from 'react';
import { useLayer } from './useLayer';

// 整屏推入页(page):正文页 / 发现页 / 设置栈 / 游客叠层登录。面板自己就是整层,焦点落在面板容器上
// (读屏念出页面名,不在 Safari 上给返回钮画焦点环),下层整片 inert(缺省保留页面层之上的播放条;
// 盖住播放条的页传 inert="all")。出口:页里的返回钮(调用方放)/ Esc / 系统返回。
// 只在 open 时渲染时可以不传 open(缺省 true)。
export default function LayerPage({
  open = true,
  onClose,
  onEscape = null,
  label,
  role = 'region',
  modal = false,
  history = 'compact',
  inert,
  className = '',
  children,
  ...rest
}) {
  const panelRef = useRef(null);
  useLayer({ open, onClose, onEscape, kind: 'page', panelRef, history, inert });

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
