import { createContext, useContext } from 'react';

// 确认弹窗的 Context 与 hook，单独成文件以满足 react-refresh「组件文件只导出组件」约束。
// Provider 在 components/ConfirmDialog.jsx 中实现。
export const ConfirmContext = createContext(null);

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm 必须在 ConfirmProvider 内使用');
  return ctx;
}
