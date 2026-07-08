// 状态徽标的单一事实来源：tone→配色的映射只在此处一份，
// 各页面的状态语义（运行状态 / 来源健康 / 检索相关性）都归一为 { label, tone, icon? }。
// 配合 <StatusBadge>（复用 .status-badge 角色类）使用。
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Loader2,
  XCircle,
} from 'lucide-react';

// 语义中性色 tone → status-badge 配色（emerald/amber/red/slate 为 conventions §4 许可的语义中性色；
// indigo/blue 已在 @theme 折叠为同一 accent）。一处定义，全站复用。
export const TONE_CLASS = {
  emerald: 'text-emerald-700 bg-emerald-50 border-emerald-100',
  amber: 'text-amber-700 bg-amber-50 border-amber-100',
  red: 'text-red-700 bg-red-50 border-red-100',
  accent: 'text-indigo-700 bg-[var(--dorami-wash)] border-[var(--dorami-accent)]/25',
  slate: 'text-slate-500 bg-[var(--dorami-soft)] border-[var(--dorami-border)]',
};

// 来源健康的小圆点（dept-dot 用），tone 对齐上表。
export const TONE_DOT = {
  emerald: 'bg-emerald-500',
  amber: 'bg-amber-400',
  red: 'bg-red-500',
  accent: 'bg-indigo-500',
  slate: 'bg-slate-300',
};

// 运行状态（FetchRunRecord / CollectionJobRunRecord）。
export function runStatusMeta(status) {
  switch (status) {
    case 'success':
      return { label: '成功', tone: 'emerald', icon: CheckCircle2 };
    case 'failed':
      return { label: '失败', tone: 'red', icon: XCircle };
    case 'partial_failed':
      return { label: '部分失败', tone: 'amber', icon: AlertTriangle };
    case 'running':
      // 运行中额外叠加脉冲/光环以强调“进行中”，仍走统一 tone 配色。
      return { label: '运行中', tone: 'accent', icon: Loader2, iconClassName: 'animate-spin', extraClassName: 'ring-2 ring-indigo-200/60 animate-pulse' };
    default:
      return { label: '运行中', tone: 'accent', icon: Clock3 };
  }
}

// 来源健康状态（SourceStateRecord 派生）。
export function healthMeta(status) {
  switch (status) {
    case 'healthy':
      return { label: '健康', tone: 'emerald', dot: TONE_DOT.emerald };
    case 'failing':
      return { label: '失败', tone: 'red', dot: TONE_DOT.red };
    case 'running':
      return { label: '运行中', tone: 'amber', dot: TONE_DOT.amber };
    case 'never_run':
      return { label: '未运行', tone: 'slate', dot: TONE_DOT.slate };
    default:
      return { label: '未知', tone: 'slate', dot: TONE_DOT.slate };
  }
}

// 向量检索相关性（distance 越小越相关）。
export function distanceMeta(distance) {
  if (distance < 0.3) return { label: '极高', tone: 'emerald' };
  if (distance < 0.5) return { label: '高', tone: 'accent' };
  if (distance < 0.7) return { label: '中', tone: 'amber' };
  return { label: '低', tone: 'slate' };
}
