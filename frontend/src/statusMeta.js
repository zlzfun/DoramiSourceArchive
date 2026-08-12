// 状态徽标的单一事实来源：tone→配色的映射只在此处一份，
// 各页面的状态语义（运行状态 / 来源健康 / 检索相关性）都归一为 { label, tone, icon? }。
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Loader2,
  XCircle,
} from 'lucide-react';

// 语义中性色 tone（emerald/amber/red/slate 为 conventions §4 许可的语义中性色；
// indigo/blue 已在 @theme 折叠为同一 accent）。一处定义，全站复用。
// 来源健康的小圆点（dept-dot 用）。
export const TONE_DOT = {
  emerald: 'bg-emerald-500',
  amber: 'bg-amber-400',
  red: 'bg-red-500',
  accent: 'bg-indigo-500',
  slate: 'bg-slate-300',
};

export function runStatusMeta(status) {
  switch (status) {
    case 'success':
      return { label: '成功', tone: 'emerald', icon: CheckCircle2 };
    case 'failed':
      return { label: '失败', tone: 'red', icon: XCircle };
    case 'partial_failed':
      return { label: '部分失败', tone: 'amber', icon: AlertTriangle };
    case 'running':
      // 运行中 = accent 家族(产品语义):icon 旋转 + 徽标呼吸;不再叠 indigo ring(R1/R4 去描边)。
      return { label: '运行中', tone: 'accent', icon: Loader2, iconClassName: 'animate-spin', extraClassName: 'animate-pulse' };
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

// 抓取失败原因分类（后端 classify_error → latest_error_type）的中文标签。
// 已知枚举值：configuration_error / network_error / http_error / parse_error / runtime_error，
// 其余为异常类名（如 KeyError、ConnectTimeout）——未知类型原样显示。
const ERROR_TYPE_LABELS = {
  configuration_error: '配置错误',
  network_error: '网络错误',
  http_error: 'HTTP 错误',
  parse_error: '解析错误',
  runtime_error: '运行错误',
};

export function errorTypeLabel(type) {
  if (!type) return '抓取失败';
  return ERROR_TYPE_LABELS[type] || type;
}
