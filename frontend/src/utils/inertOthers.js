// 下层 inert(issue #230 层原语):模态层打开时,把层以外的页面整片设成 inert——读屏读不到、Tab 走不进、
// 点不到;层关闭时还原。做法同 aria-hidden 的「hideOthers」:从 body 往下走,含有「保留元素」的节点
// 往里递归,其余兄弟节点整棵设 inert;保留元素(层面板自身、Toast、页面层之上的播放条)及其祖先不动。
//
// 多层叠放时同一个节点可能被几层各设一次,按引用计数:最后一层放手才去掉 inert,原本就带 inert 的节点
// 不碰。新挂载的节点(之后打开的上层、portal)不在任何一次计算里,天然不受影响。
// 纯 DOM 操作、不依赖 React;只用 children / contains / tagName / get|set|removeAttribute,
// test/layerPrimitives.test.mjs 用假节点树核对。
const counts = new Map();   // 节点 → 被设 inert 的次数
const preset = new Set();   // 打开前就带 inert 的节点(不归我们管)
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE', 'META', 'NOSCRIPT']);

function acquire(node) {
  const n = counts.get(node) || 0;
  if (n === 0) {
    if (node.hasAttribute('inert')) { preset.add(node); return false; }
    node.setAttribute('inert', '');
  }
  counts.set(node, n + 1);
  return true;
}

function release(node) {
  const n = counts.get(node) || 0;
  if (n <= 1) {
    counts.delete(node);
    if (!preset.has(node)) node.removeAttribute('inert');
    return;
  }
  counts.set(node, n - 1);
}

// keep:要保留可交互的元素数组(层面板必在其中)。root 缺省为 document.body。
// 返回还原函数(幂等)。
export function inertOthers(keep, root = (typeof document !== 'undefined' ? document.body : null)) {
  const kept = keep.filter(Boolean);
  if (!root || kept.length === 0) return () => {};
  const touched = [];
  const walk = (parent) => {
    for (const child of Array.from(parent.children)) {
      if (SKIP_TAGS.has(child.tagName)) continue;
      if (kept.includes(child)) continue;
      if (kept.some((el) => child.contains(el))) { walk(child); continue; }
      if (acquire(child)) touched.push(child);
    }
  };
  walk(root);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    touched.forEach(release);
  };
}

// 测试用:清空模块级计数(只在单测里调)
export function resetInertState() {
  counts.clear();
  preset.clear();
}
