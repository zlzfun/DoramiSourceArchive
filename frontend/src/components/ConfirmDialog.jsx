import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { ConfirmContext } from '../hooks/useConfirm';

// 统一的确认弹窗 Provider：替代原生 window.confirm，复用既有 modal 视觉。
// 用法（消费侧）：const confirm = useConfirm(); if (!(await confirm('确定删除？'))) return;
//      或 confirm({ title, message, confirmText, cancelText, tone: 'danger' | 'primary' })
export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);
  const [closing, setClosing] = useState(false);
  const resolverRef = useRef(null);
  const closeTimerRef = useRef(null);

  // 立即 resolve（不阻塞调用方），随后播 200ms 退出动画再卸载弹窗。
  const settle = useCallback((result) => {
    resolverRef.current?.(result);
    resolverRef.current = null;
    setClosing(true);
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setState(null);
      setClosing(false);
      closeTimerRef.current = null;
    }, 320);
  }, []);

  const confirm = useCallback((options) => {
    const opts = typeof options === 'string' ? { message: options } : (options || {});
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
    setClosing(false);
    return new Promise((resolve) => {
      resolverRef.current = resolve;
      setState({
        title: opts.title || '请确认操作',
        message: opts.message || '',
        confirmText: opts.confirmText || '确认',
        cancelText: opts.cancelText || '取消',
        tone: opts.tone || 'danger',
      });
    });
  }, []);

  // 仅 Esc 取消（不绑定 Enter 确认，避免误触危险操作）；同时锁定背景滚动。
  useEffect(() => {
    if (!state) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') settle(false); };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [state, settle]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <div className={`modal-overlay items-center z-[100] ${closing ? 'is-closing' : ''}`} onClick={() => settle(false)}>
          <div
            className="modal-panel max-w-md"
            role="alertdialog"
            aria-modal="true"
            aria-label={state.title}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-4 p-6">
              <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${state.tone === 'danger' ? 'bg-red-50 text-red-500' : 'bg-[var(--dorami-wash)] text-indigo-500'}`}>
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-base font-bold text-[var(--dorami-ink)]">{state.title}</h3>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-500">{state.message}</p>
              </div>
              <button onClick={() => settle(false)} className="p-1 text-slate-300 hover:text-slate-500" aria-label="关闭">
                <X className="h-5 w-5" />
              </button>
            </div>
            {/* 脚部按钮取 M 档三件套(2026-07-24 拍板:模态脚部由 L 降 M,40px 在弹窗里过高) */}
            <div className="flex justify-end gap-3 border-t border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-6 py-3">
              <button type="button" onClick={() => settle(false)} className="action-button action-button-quiet min-h-[32px] px-3 text-xs" autoFocus>
                {state.cancelText}
              </button>
              <button
                type="button"
                onClick={() => settle(true)}
                className={`action-button min-h-[32px] px-3 text-xs ${state.tone === 'danger' ? 'action-button-danger' : 'action-button-primary'}`}
              >
                {state.confirmText}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
