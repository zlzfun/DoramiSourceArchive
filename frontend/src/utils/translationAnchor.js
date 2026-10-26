// 正文渐进翻译(issue #218)的阅读锚点:只读写 DOM 几何,不碰 React,便于假 DOM 自测。
//
// 浏览器原生滚动锚定保持开启(真实图片加载变高等由它处理);未完成块挂 overflow-anchor:none,原生锚点永远
// 不会落在骨架上。这里只在「译文内容变化 / 切视图」这类由本组件引起的变化前后补一次对位,目标是绝对位置
// (某块的片内比例或相对视口的偏移),与原生锚定已做过的调整互不叠加:
//   · 视口顶部在已译块或原文里 → 该块原地不动;
//   · 视口顶部落在骨架里、而视口内已能看到已译块 → 锚定那段可见的真实内容(骨架里没有可读的东西,
//     它的高度还会随原因行挪进来、估行变化而变,按骨架比例恢复会把下方正在读的译文推走);
//   · 视口里只有骨架 → 按所在块的片内比例落位;
//   · 分块 → 整篇缓存(终态没收到最后的增量):整篇是一份完整 Markdown,没有块包层,按「第 k 块 ↔ 第 k 个顶层元素」
//     对应(两边结构数一致且文本对得上);对不上时按实际文本找同一段(重复段落取离预期序号最近的);
//   · 切视图:原文 → 中文对齐同序号的块(两边块数不一致时按全文比例);读者在中文里没动过,切回原文回到离开时的位置。
export const SCROLLER_SELECTOR = '.reader-pane, .m-read-scroll';

// 中文视图有块包层就按块;整篇缓存 / 原文按正文的顶层元素(读屏 status 区除外)
export function anchorList(copy, view) {
  if (!copy) return [];
  if (view === 'zh') {
    const blocks = [...copy.querySelectorAll('[data-tr-block]')];
    if (blocks.length) return blocks;
  }
  return [...copy.children].filter((el) => !el.hasAttribute('data-tr-status'));
}

// 实际文本签名:块包层取它渲染出的第一个元素,顶层元素取自身;骨架没有文本
const SIGNATURE_CHARS = 80;
function signatureOf(el) {
  if (!el || el.hasAttribute('data-tr-pending')) return '';
  const first = el.querySelector?.('.reader-tr-block-body > *') || el;
  return String(first.textContent || '').replace(/\s+/g, ' ').trim().slice(0, SIGNATURE_CHARS);
}

function anchorOf(el, rect, top, position, count, progress) {
  return {
    position,
    count,
    index: el.getAttribute('data-tr-block'),
    text: signatureOf(el),
    offset: rect.top - top,
    fraction: rect.top < top && rect.height > 0 ? (top - rect.top) / rect.height : null,
    progress,
  };
}

/** 内容变化前记下视口顶部的阅读位置;视口顶部还在标题区(或未滚动)时返回 null——正文怎么变都不影响眼前。 */
export function captureAnchor(scroller, copy, view) {
  if (!scroller || !copy || scroller.scrollTop <= 0) return null;
  const box = scroller.getBoundingClientRect();
  const copyRect = copy.getBoundingClientRect();
  if (copyRect.top >= box.top) return null;
  const progress = copyRect.height > 0 ? Math.min(1, (box.top - copyRect.top) / copyRect.height) : null;
  const list = anchorList(copy, view);
  const first = list.findIndex((el) => el.getBoundingClientRect().bottom > box.top + 1);
  if (first < 0) return { position: null, count: list.length, index: null, text: '', offset: 0, fraction: null, progress };
  let pick = first;
  if (list[first].hasAttribute('data-tr-pending')) {
    for (let k = first + 1; k < list.length; k += 1) {
      const rect = list[k].getBoundingClientRect();
      if (rect.top >= box.bottom) break;
      if (!list[k].hasAttribute('data-tr-pending')) {
        pick = k;
        break;
      }
    }
  }
  const el = list[pick];
  return anchorOf(el, el.getBoundingClientRect(), box.top, pick, list.length, progress);
}

function placeElement(scroller, el, anchor) {
  const top = scroller.getBoundingClientRect().top;
  const rect = el.getBoundingClientRect();
  const wanted = anchor.fraction == null ? top + anchor.offset : top - anchor.fraction * rect.height;
  const delta = rect.top - wanted;
  if (Math.abs(delta) >= 1) scroller.scrollTop += delta;
}

function placeProgress(scroller, copy, progress) {
  const box = scroller.getBoundingClientRect();
  const rect = copy.getBoundingClientRect();
  const delta = rect.top + progress * rect.height - box.top;
  if (Math.abs(delta) >= 1) scroller.scrollTop += delta;
}

// 同签名的候选里取离预期序号(按两边元素数等比换算)最近的一个
function findByText(list, anchor) {
  if (!anchor.text) return null;
  const expected = anchor.position != null && anchor.count ? (anchor.position * list.length) / anchor.count : 0;
  let best = null;
  let bestGap = Infinity;
  list.forEach((el, index) => {
    if (signatureOf(el) !== anchor.text) return;
    const gap = Math.abs(index - expected);
    if (gap < bestGap) {
      best = el;
      bestGap = gap;
    }
  });
  return best;
}

/**
 * 同一视图内的内容变化之后:把记下的那一块放回原处。依次尝试:同一块(id)→ 结构一致时的同序号元素(文本也对得上)
 * → 实际文本相同的那一段 → 同序号元素 → 全文比例。
 */
export function restoreAnchor(scroller, copy, view, anchor) {
  if (!scroller || !copy || !anchor) return false;
  const list = anchorList(copy, view);
  if (anchor.index != null) {
    const el = list.find((node) => node.getAttribute('data-tr-block') === anchor.index);
    if (el) {
      placeElement(scroller, el, anchor);
      return true;
    }
  }
  const sameShape = anchor.position != null && list.length === anchor.count ? list[anchor.position] : null;
  const target = (sameShape && (!anchor.text || signatureOf(sameShape) === anchor.text) ? sameShape : null)
    || findByText(list, anchor)
    || sameShape;
  if (target) {
    placeElement(scroller, target, anchor);
    return true;
  }
  if (anchor.progress == null) return false;
  placeProgress(scroller, copy, anchor.progress);
  return true;
}

/** 切视图之后:按序号对齐另一视图的同一块(两边块数相同才按序号,否则按全文比例;两种语言的文本对不上,不比文本)。 */
export function restoreAcross(scroller, copy, view, anchor) {
  if (!scroller || !copy || !anchor) return false;
  const list = anchorList(copy, view);
  if (anchor.position != null && list.length === anchor.count && list[anchor.position]) {
    placeElement(scroller, list[anchor.position], anchor);
    return true;
  }
  if (anchor.progress == null) return false;
  placeProgress(scroller, copy, anchor.progress);
  return true;
}
