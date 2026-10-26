import { stripMarkdown } from '../../utils/readerText';

// 「逐段占位」的骨架来源(样页专用):把一片的**原文** Markdown 拆成顶层块,按块类型与篇幅画占位——
// 段落 / 标题 / 列表 / 引文 / 代码围栏 / 表格 / 块级公式 / 独立图片。只读原文,不读译文。
// 只覆盖 fixture 用到的语法;样页 fixture 的片界都落在完整块之间,所以「一片 = 若干完整块」。
// 真实接口片约 3,500 字符、可能切在段落 / 列表 / 表格中间:生产实现需要服务端给出与译文对齐的
// 安全块边界(哪些块属于哪一片、跨片的块何时算完成),不能把接口片当作段落,也不能照搬这里的切法。

const LIST_RE = /^\s*(?:[-*+]|\d+\.)\s+/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const TABLE_RULE_RE = /^\s*\|?[\s:|-]+\|?\s*$/;
const IMAGE_RE = /^!\[[^\]]*\]\([^)]*\)$/;

const isFence = (line) => line.trimStart().startsWith('```');
const isMathFence = (line) => line.trim() === '$$';
const isTableRow = (line) => line.trimStart().startsWith('|');
const isQuote = (line) => line.trimStart().startsWith('>');
const startsBlock = (line) => isFence(line) || isMathFence(line) || HEADING_RE.test(line)
  || isTableRow(line) || isQuote(line) || LIST_RE.test(line);

const cellsOf = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((cell) => stripMarkdown(cell).length);

export function parseBlocks(markdown) {
  const lines = String(markdown || '').split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (isFence(line)) {
      const body = [];
      for (i += 1; i < lines.length && !isFence(lines[i]); i += 1) body.push(lines[i].replace(/\s+$/, '').length);
      i += 1;
      blocks.push({ type: 'code', lines: body });
      continue;
    }
    if (isMathFence(line)) {
      const body = [];
      for (i += 1; i < lines.length && !isMathFence(lines[i]); i += 1) body.push(lines[i].trim());
      i += 1;
      blocks.push({ type: 'math', chars: body.join(' ').length });
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, chars: stripMarkdown(heading[2]).length });
      i += 1;
      continue;
    }
    if (isTableRow(line)) {
      const rows = [];
      for (; i < lines.length && isTableRow(lines[i]); i += 1) {
        if (!TABLE_RULE_RE.test(lines[i])) rows.push(cellsOf(lines[i]));
      }
      blocks.push({ type: 'datatable', rows });
      continue;
    }
    if (isQuote(line)) {
      const text = [];
      for (; i < lines.length && isQuote(lines[i]); i += 1) text.push(lines[i].replace(/^\s*>\s?/, ''));
      blocks.push({ type: 'quote', chars: stripMarkdown(text.join(' ')).length });
      continue;
    }
    if (LIST_RE.test(line)) {
      const items = [];
      for (; i < lines.length && LIST_RE.test(lines[i]); i += 1) items.push(stripMarkdown(lines[i].replace(LIST_RE, '')).length);
      blocks.push({ type: 'list', items });
      continue;
    }
    const text = [];
    for (; i < lines.length && lines[i].trim() && !startsBlock(lines[i]); i += 1) text.push(lines[i].trim());
    const joined = text.join(' ');
    blocks.push(IMAGE_RE.test(joined) ? { type: 'image' } : { type: 'paragraph', chars: stripMarkdown(joined).length });
  }
  return blocks;
}

// 行宽估算:西文正文平均每字符约 0.5em(含空格);一行能放「栏宽 ÷ 字号」个 em。
// 中文译文每字约 1em、字数约为原文字符的一半,所以按原文估出的行数与译文行数大致相当——不必像素级预测。
const EM_PER_CHAR = 0.5;
const FULL_LINES = [100, 97, 100, 95, 99];

export function lineWidths(chars, lineEm) {
  const units = Math.max(1, chars) * EM_PER_CHAR;
  const perLine = Math.max(8, lineEm);
  const count = Math.max(1, Math.ceil(units / perLine));
  const last = units / perLine - (count - 1);
  const widths = [];
  for (let index = 0; index < count - 1; index += 1) widths.push(FULL_LINES[index % FULL_LINES.length]);
  widths.push(Math.round(Math.min(0.94, Math.max(0.28, last)) * 100));
  return widths;
}

export const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));
