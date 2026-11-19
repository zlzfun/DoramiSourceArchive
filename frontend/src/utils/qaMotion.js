// 问答的光(issue #301 第五批,conventions §7「光只给问答」):回答到达时的逐段对焦与引用依次亮。
// 面板自己的玻璃、由虚到实、极光沿边、等待时的流光都在样式表里(index.css「问答的光」一段);这里只有需要按内容编排的那一部分。
//
// 回答的到达不是哪一下点击的结果(提问多半是回车发出的),等待与逐段出现对应的是回答真实的到达过程——
// 所以不看输入方式,键盘发出的提问照常演;只按减少动效降级(去掉模糊,只留短淡入)。
import { motionReduced } from '../motion.js';
import { MOTION, fadeIn } from './readerMotion.js';

const PARA_STAGGER = 140;   // 段与段之间
const PARA_MAX = 8;         // 至多错开这么多段,其余与最后一段同时
const CITE_STAGGER = 150;   // 引用角标之间

const accent = (alpha) => {
  const rgb = getComputedStyle(document.documentElement).getPropertyValue('--dorami-accent-rgb').trim() || '63, 123, 246';
  return `rgba(${rgb}, ${alpha})`;
};

/**
 * 回答到达:各段依次由虚到实(220ms,模糊 6px → 0),随后来源条淡入,引用角标依次亮一下、对应的来源行跟着亮。
 * block 是这一轮回答的 .reader-ai-a。
 */
export function revealAnswer(block) {
  if (!block || typeof Element === 'undefined' || !Element.prototype.animate) return;
  const reduced = motionReduced();
  const body = block.querySelector('.reader-ai-a-body');
  // 段落在 Markdown 渲染出来的那一层:正文容器只有一个子元素时再往里取一层
  let parts = body ? [...body.children] : [];
  if (parts.length === 1 && parts[0].children.length > 1 && !parts[0].matches('p, ul, ol, pre, blockquote, table, h1, h2, h3, h4')) parts = [...parts[0].children];
  parts.forEach((part, i) => {
    const delay = Math.min(i, PARA_MAX) * (reduced ? 60 : PARA_STAGGER);
    if (reduced) fadeIn(part, 160, delay);
    else part.animate([{ opacity: 0, filter: 'blur(6px)' }, { opacity: 1, filter: 'blur(0px)' }], { duration: 220, delay, easing: MOTION.easeFocus, fill: 'backwards' });
  });
  const after = Math.min(parts.length, PARA_MAX + 1) * (reduced ? 60 : PARA_STAGGER) + 60;
  for (const el of block.querySelectorAll(':scope > :not(.reader-ai-a-id, .reader-ai-a-body)')) fadeIn(el, reduced ? 160 : 220, after);
  if (reduced) return;
  const dark = document.documentElement.dataset.theme === 'dark';
  const rows = [...block.querySelectorAll('.reader-ai-ref-row')];
  const rowOf = (num) => rows.find((row) => row.querySelector('.reader-ai-ref-num')?.textContent.trim() === num);
  const seen = new Set();
  let order = 0;
  for (const cite of block.querySelectorAll('.reader-ai-cite')) {
    const delay = after + 180 + order * CITE_STAGGER;
    order += 1;
    // 角标自己的底色在 0.3 处提亮一下,首尾不写——从它原来的底色出发、回到原来的底色
    cite.animate([
      { boxShadow: `0 0 0 0 ${accent(0)}`, offset: 0 },
      { boxShadow: `0 0 0 2px ${accent(0.4)}, 0 0 12px 2px ${accent(dark ? 0.7 : 0.4)}`, backgroundColor: accent(dark ? 0.42 : 0.24), offset: 0.3 },
      { boxShadow: `0 0 0 0 ${accent(0)}`, offset: 1 },
    ], { duration: 620, delay, easing: 'ease-out' });
    const num = cite.textContent.replace(/\D/g, '');
    if (!num || seen.has(num)) continue;
    seen.add(num);
    rowOf(num)?.animate([
      { backgroundColor: accent(0), offset: 0 },
      { backgroundColor: accent(dark ? 0.2 : 0.12), offset: 0.3 },
      { backgroundColor: accent(0), offset: 1 },
    ], { duration: 760, delay, easing: 'ease-out' });
  }
}

/** 面板由指针打开时,导轨上的问答钮泛一次光晕(600ms)——光唯一落在面板之外的地方,它是面板的来处。 */
export function haloQaButton() {
  if (typeof document === 'undefined' || motionReduced()) return;
  const icon = document.querySelector('.reader-shell .reader-vrail .reader-vrail-qa > svg');
  if (!icon || !icon.animate) return;
  icon.animate([
    { filter: `drop-shadow(0 0 0 ${accent(0)})`, offset: 0 },
    { filter: `drop-shadow(0 0 11px ${accent(document.documentElement.dataset.theme === 'dark' ? 0.95 : 0.6)})`, offset: 0.3 },
    { filter: `drop-shadow(0 0 0 ${accent(0)})`, offset: 1 },
  ], { duration: 600, easing: 'ease-out' });
}

// 只在回答刚到的那一次演:面板收起再打开、换范围重排时,同一轮回答重新挂载不重演
const revealed = new WeakSet();
const asked = new WeakSet();

/** 提问发出的那一刻:问句由虚到实(200ms,模糊 4px → 0)。turn 是等待中的那一轮,同一个对象只演一次。 */
export function focusQuestionOnce(turn, bubble) {
  if (!turn || asked.has(turn)) return;
  asked.add(turn);
  if (!bubble || !bubble.animate) return;
  if (motionReduced()) { fadeIn(bubble, 160); return; }
  bubble.animate([{ opacity: 0, filter: 'blur(4px)' }, { opacity: 1, filter: 'blur(0px)' }], { duration: 200, easing: MOTION.easeFocus });
}

/** turn 是这一轮的数据对象(回答到达时带 fresh 标记);同一个对象只演一次。 */
export function revealOnce(turn, block) {
  if (!turn || !turn.fresh || revealed.has(turn)) return;
  revealed.add(turn);
  revealAnswer(block);
}
