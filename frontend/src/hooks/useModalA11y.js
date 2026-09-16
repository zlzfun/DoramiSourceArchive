import { useEffect, useRef } from 'react';

// 模态可访问性：Esc 关闭 + 打开时焦点移入面板 + Tab 焦点陷阱 + 关闭后焦点归还触发者。
// 用法：给面板元素挂 ref（面板需可聚焦，建议 tabIndex={-1}），active 为弹窗是否打开。
//   const panelRef = useRef(null);
//   useModalA11y(active, onClose, panelRef);
//   <div ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1}>…</div>
// 说明：
// - 尊重 React 的 autoFocus——若 commit 阶段已把焦点放进面板内，则不再抢焦点。
// - keydown 用捕获阶段监听，保证 Esc 在冒泡被 stopPropagation 前先被拦到。
// - 层栈(issue #104 codex R1):多层浮层同时激活(抽屉上开确认框 / 表单弹窗、总账上开新建标签 sheet)时,
//   每层都在 document 捕获阶段挂了监听,同节点的 stopPropagation 拦不住彼此——一次 Esc 会两层一起关,
//   且同一 commit 里下层先把焦点还给它的触发者、上层再把焦点还给下层里已 aria-hidden 的按钮。
//   故激活时入栈、只有栈顶消费 Esc / Tab:上层关闭后焦点回到下层触发控件、下层保持打开,再按 Esc 才关下层。
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const layers = [];

export function useModalA11y(active, onClose, panelRef) {
  // onClose ref 化:调用方常传内联箭头(每次渲染新引用),若进 effect 依赖,任何重渲染
  // (如编辑器每秒走字)都会让 effect 重跑——cleanup 把焦点归还给遮罩下的触发者、再抢回
  // 面板首元素,表现为「输入框打字 1-2 秒后被夺焦」。依赖只留 active/panelRef。
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });
  // 触发者要在 commit 之前抓:面板里若有 autoFocus,React 在 commit 阶段就把焦点搬进面板,
  // 等到 effect 再读 activeElement 拿到的是面板内的输入框——关闭时「归还」给一个正在卸载的元素,
  // 焦点最终掉到 body(重置密码 / 确认框都有 autoFocus,改前即如此)。故在 active 由假变真的
  // 这次渲染里记下当时的 activeElement;渲染期写 ref 是有意为之。
  const openerRef = useRef(null);
  const wasActiveRef = useRef(false);
  // 上一次 setup 时面板内的焦点元素:StrictMode(开发态)会 setup → cleanup → setup 重跑一遍,
  // cleanup 把焦点还给触发者后,第二次 setup 若只认「首个可聚焦元素」会把 autoFocus 的结果覆盖掉
  // (确认框:取消钮 → 关闭 X)。记住它,重跑时优先还给它,setup / cleanup 才幂等。
  const panelFocusRef = useRef(null);
  /* eslint-disable react-hooks/refs -- 渲染期读写 ref 是有意为之:触发者必须在 commit(autoFocus)之前抓,
     wasActiveRef 只用来识别 active 由假变真;这两个 ref 不参与渲染输出。 */
  if (active && !wasActiveRef.current) {
    openerRef.current = document.activeElement;
    panelFocusRef.current = null; // 新一次打开,上次记住的面板内焦点作废
  }
  wasActiveRef.current = active;
  /* eslint-enable react-hooks/refs */
  useEffect(() => {
    if (!active) return undefined;
    const panel = panelRef.current;
    const opener = openerRef.current;
    const previouslyFocused = (opener && !(panel && panel.contains(opener))) ? opener : document.activeElement;
    const layer = {};
    layers.push(layer);
    const isTop = () => layers[layers.length - 1] === layer;

    const focusables = () => (panel
      ? Array.from(panel.querySelectorAll(FOCUSABLE)).filter((el) => el.offsetParent !== null)
      : []);

    // 焦点移入：React autoFocus 已把焦点落进面板内则不打扰；否则优先还给上次 setup 记住的面板内元素
    // (StrictMode 重跑),再退到首个可聚焦元素（兜底面板本身）。
    if (!(panel && panel.contains(document.activeElement))) {
      const remembered = panelFocusRef.current;
      const target = (remembered && panel && panel.contains(remembered) && remembered.offsetParent !== null)
        ? remembered
        : (focusables()[0] || panel);
      target?.focus?.();
    }
    panelFocusRef.current = (panel && panel.contains(document.activeElement)) ? document.activeElement : null;

    const onKeyDown = (e) => {
      if (!isTop()) return;
      if (e.key === 'Escape') { e.stopPropagation(); onCloseRef.current?.(); return; }
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) { e.preventDefault(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      const idx = layers.lastIndexOf(layer);
      if (idx !== -1) layers.splice(idx, 1);
      document.removeEventListener('keydown', onKeyDown, true);
      // 焦点归还给触发者（若它仍在文档内且不在正在关闭的面板里）。
      if (previouslyFocused && previouslyFocused.focus && document.contains(previouslyFocused)
        && !(panel && panel.contains(previouslyFocused))) {
        previouslyFocused.focus();
      }
    };
  }, [active, panelRef]);
}
