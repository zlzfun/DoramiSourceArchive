import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';
import { useModalTransition } from '../hooks/useModalTransition';

const STYLES = {
  error: { bg: 'bg-rose-500', Icon: AlertCircle },
  success: { bg: 'bg-emerald-600', Icon: CheckCircle2 },
  info: { bg: 'bg-slate-800 dark:bg-slate-900', Icon: Info },
};

// action(可选,issue #213):行内一个次动作,如游客入场提示里的「登录」;点了即执行并收起。
export default function Toast({ show, message, type = 'info', onClose, action = null }) {
  // 退出时保持挂载 260ms 播放离场动画，避免「瞬间消失」的生硬感。
  const { mounted, closing } = useModalTransition(show, 260);
  if (!mounted) return null;

  const { bg, Icon } = STYLES[type] || STYLES.info;

  // 底边距在 .toast-pop 上(index.css):播放条出现时整体抬到条之上(issue #199)
  // w-max:fixed + left-1/2 的收缩宽度只有半个视口,手机上短句会被挤成竖排;按内容取宽、由 90vw 封顶(issue #213)
  return (
    <div
      className={`toast-pop ${closing ? 'is-leaving' : ''} fixed left-1/2 z-[200] flex w-max max-w-[90vw] items-center gap-3 rounded-[var(--r-card)] py-3.5 pl-5 pr-3 text-white shadow-2xl ${bg}`}
      role="status"
    >
      <Icon className="h-5 w-5 shrink-0" />
      <span className="text-sm font-semibold">{message}</span>
      {action && (
        <button
          type="button"
          onClick={() => { action.onClick?.(); onClose?.(); }}
          className="shrink-0 rounded-[var(--r-sm)] bg-white/15 px-2.5 py-1 text-sm font-semibold text-white transition-colors hover:bg-white/25"
        >
          {action.label}
        </button>
      )}
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭提示"
          className="shrink-0 rounded-[var(--r-sm)] p-1 text-white/70 transition-colors hover:bg-white/15 hover:text-white"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
