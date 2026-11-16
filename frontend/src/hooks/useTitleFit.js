import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { bondAfter, pickBreaks, stopAfter } from '../utils/titleFit';

// 标题挑折行点的显示层一半(issue #279 第五批补):量宽度、定行宽、行宽或字号变了重算。算法在 utils/titleFit。
//
// 量法:把标题那一截(.ts-kw)克隆一份,绝对定位、不折行地排成一行,读每一块(.ts-w)的左右边——
// 这样标点挤压的负边距、中西文间隙、字距都按浏览器实际排出来的算。克隆在同一次同步代码里插入又移除,不会画出来。
// 行宽:标题所在的块级盒(最近的非行内祖先)的内容宽。盒子若是「随内容收缩」的(行内块、flex 子项),指定折点之后它会
// 缩到最长一行那么宽——所以读行宽之前先把标题临时放回自动折行(.is-probe),读到的才是真正能用的宽度;
// 之后盒宽只要还落在「最长一行 ~ 上次的行宽」之间就不重算,不会自己把自己越算越窄。

/** 最近的非行内祖先:标题文字就是在它的行盒里折行的。 */
function lineBoxOf(node) {
  let el = node.parentElement;
  while (el) {
    const display = getComputedStyle(el).display;
    if (display !== 'inline' && display !== 'contents') return el;
    el = el.parentElement;
  }
  return null;
}

function contentWidth(box) {
  const style = getComputedStyle(box);
  return box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
}

/** 这一行最多能排多宽:已经指定折点的标题先临时放开再读。 */
function availableWidth(kw, box) {
  const fitted = kw.classList.contains('is-fit');
  if (fitted) kw.classList.replace('is-fit', 'is-probe');
  const width = contentWidth(box);
  if (fitted) kw.classList.replace('is-probe', 'is-fit');
  return width;
}

function measure(kw, box) {
  const ruler = kw.cloneNode(true);
  ruler.className = 'ts-kw ts-ruler';
  ruler.setAttribute('aria-hidden', 'true');
  kw.parentNode.insertBefore(ruler, kw.nextSibling);
  // 祖先带缩放变换时(入场动效),getBoundingClientRect 是变换后的尺寸:按盒子的缩放比折回排版尺寸
  const laidOut = box.offsetWidth;
  const scale = (laidOut && box.getBoundingClientRect().width / laidOut) || 1;
  const starts = [];
  const ends = [];
  ruler.querySelectorAll(':scope > .ts-w').forEach((word) => {
    const rect = word.getBoundingClientRect();
    starts.push(rect.left / scale);
    ends.push(rect.right / scale);
  });
  ruler.remove();
  return { starts, ends };
}

/** 按 heads 分行后最长一行的宽度。 */
function longestLine(starts, ends, heads) {
  let longest = 0;
  const edges = [0, ...heads, starts.length];
  for (let k = 0; k + 1 < edges.length; k += 1) longest = Math.max(longest, ends[edges[k + 1] - 1] - starts[edges[k]]);
  return longest;
}

/**
 * @param ref 指向 .ts-kw
 * @param units utils/titleFit 的 titleUnits(同一标题同一数组);.ts-kw 里每片一个 .ts-w,顺序一致
 * @returns 各行(第一行除外)的起始片下标;null = 不指定,由浏览器折
 */
export function useTitleFit(ref, units) {
  const [fit, setFit] = useState(null);          // { units, heads }
  const seen = useRef({ units: null, font: '', limit: 0, longest: 0 });
  const marks = useMemo(() => ({
    stops: units.map((unit, index) => stopAfter(unit.text, units[index + 1]?.text)),
    bonds: units.map((unit, index) => bondAfter(unit.text, units[index + 1]?.text)),
    glued: units.map((unit) => unit.glued),
  }), [units]);

  const refit = useCallback((force = false) => {
    const kw = ref.current;
    const box = kw && lineBoxOf(kw);
    if (!box) return;
    const font = getComputedStyle(kw).fontSize;
    const last = seen.current;
    if (!force && last.units === units && last.font === font) {
      // 没变宽、现有的几行也还排得下:不动(随内容收缩的盒子在指定折点之后就落在这个区间里)
      const width = contentWidth(box);
      if (width <= last.limit + 0.5 && width >= last.longest - 1) return;
    }
    const limit = availableWidth(kw, box);
    const em = parseFloat(font);
    if (!(limit > 0) || !(em > 0)) return;       // 还没排版(藏着的面板):等它有了宽度再算
    const { starts, ends } = measure(kw, box);
    if (starts.length !== units.length) return;
    // 行宽的取法沿用标题原来的折行风格(均衡 / 铺满):这里只改折在哪儿
    const boxStyle = getComputedStyle(box);
    const balance = (boxStyle.textWrapStyle || boxStyle.textWrap || '').includes('balance');
    // 留 1px 余量:量出来的宽度与真正排版之间差个亚像素,不值得为它多折一行(指定折点之后行内不再折,差这一点也只是贴边)
    const heads = pickBreaks({ starts, ends, ...marks, limit: limit + 1, em, balance });
    seen.current = { units, font, limit, longest: heads ? longestLine(starts, ends, heads) : 0 };
    setFit((prev) => {
      const same = prev && prev.units === units && (prev.heads === null) === (heads === null) && String(prev.heads) === String(heads);
      return same ? prev : { units, heads };
    });
  }, [ref, units, marks]);

  // 换标题:画出来之前就定好
  useLayoutEffect(() => { refit(); }, [refit]);

  // 行宽、字号变了(窗口、栏宽、阅读字号),以及字体到位之后:重算
  useEffect(() => {
    const kw = ref.current;
    const box = kw && lineBoxOf(kw);
    if (!box) return undefined;
    let alive = true;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { if (alive) refit(); });
    observer?.observe(box);
    const onFonts = () => { if (alive) refit(true); };
    const fonts = document.fonts;
    fonts?.addEventListener?.('loadingdone', onFonts);
    if (fonts?.status === 'loading') fonts.ready.then(onFonts).catch(() => {});   // 挂上来时字体还在路上:到位后按真字宽再算一遍
    return () => {
      alive = false;
      observer?.disconnect();
      fonts?.removeEventListener?.('loadingdone', onFonts);
    };
  }, [ref, refit]);

  return fit && fit.units === units ? fit.heads : null;
}
