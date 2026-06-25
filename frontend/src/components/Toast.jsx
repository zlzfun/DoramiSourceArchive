import { AlertCircle, CheckCircle2, Info } from 'lucide-react';
import { useModalTransition } from '../hooks/useModalTransition';

const STYLES = {
  error: { bg: 'bg-rose-500', Icon: AlertCircle },
  success: { bg: 'bg-emerald-600', Icon: CheckCircle2 },
  info: { bg: 'bg-slate-800', Icon: Info },
};

export default function Toast({ show, message, type = 'info' }) {
  // 退出时保持挂载 260ms 播放离场动画，避免「瞬间消失」的生硬感。
  const { mounted, closing } = useModalTransition(show, 260);
  if (!mounted) return null;

  const { bg, Icon } = STYLES[type] || STYLES.info;

  return (
    <div
      className={`toast-pop ${closing ? 'is-leaving' : ''} fixed bottom-8 left-1/2 z-[200] flex max-w-[90vw] items-center gap-3 rounded-[var(--r-card)] px-5 py-3.5 text-white shadow-2xl ${bg}`}
      role="status"
    >
      <Icon className="h-5 w-5 shrink-0" />
      <span className="text-sm font-semibold">{message}</span>
    </div>
  );
}
