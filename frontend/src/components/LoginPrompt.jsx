import { LogIn, MessageSquare, Plus, Rss, Sparkles, Star, Tags, X } from 'lucide-react';
import Modal from './Modal';
import { loginPromptCopy } from '../utils/loginPrompt';

const ICONS = {
  star: Star,
  plus: Plus,
  rss: Rss,
  tags: Tags,
  message: MessageSquare,
  sparkles: Sparkles,
  login: LogIn,
};

// 游客登录提示(issue #213):外形照抄 ConfirmDialog(图标圆 + 标题 + 一句说明;脚部「继续逛逛 | 登录」),
// 遮罩关闭走 Modal 外壳。request = { action, name } 由 utils/loginPrompt.requestLogin 或后端错误码触发;
// 文案载荷在退场动画期间仍需渲染,故父组件关闭时只翻 open、不清 request。
export default function LoginPrompt({ open, request, onClose, onLogin }) {
  if (!request) return null;
  const copy = loginPromptCopy(request.action, request);
  const Icon = ICONS[copy.icon] || LogIn;
  return (
    <Modal
      open={open} onClose={onClose} closeOnOverlay centered size="md"
      ariaLabel={copy.title} overlayClassName="z-[100]"
    >
      <div className="flex items-start gap-4 p-6">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--dorami-wash)] text-indigo-500">
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold text-[var(--dorami-ink)]">{copy.title}</h3>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-500">{copy.message}</p>
        </div>
        <button type="button" onClick={onClose} className="p-1 text-slate-300 hover:text-slate-500" aria-label="关闭">
          <X className="h-5 w-5" />
        </button>
      </div>
      <div className="flex justify-end gap-3 border-t border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-6 py-3">
        <button type="button" onClick={onClose} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">
          继续逛逛
        </button>
        <button type="button" onClick={onLogin} className="action-button action-button-primary min-h-[32px] px-3 text-xs" autoFocus>
          登录
        </button>
      </div>
    </Modal>
  );
}
