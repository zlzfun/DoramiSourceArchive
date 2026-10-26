// 正文渐进翻译(issue #218)的纯逻辑:预览块合并、骨架形状估算、停止原因文案。组件只做 DOM,这里便于 node --test。
//
// 服务端预览(preview)以「完整 Markdown 块」为单位:reset 时给全部块(原文 source + 类型 kind + 已就绪的译文),
// 之后只给新就绪块的 { id, translation },按 id 合并、不重排。接口的翻译片(约 3,500 字符)只是传输单位,
// 前端不当段落用;块的原文只用来估算骨架形状,不上屏、不与译文混排。
import { stripMarkdown } from './readerText.js';

export const ACTIVE_STATUSES = Object.freeze(['queued', 'running']);
export const isActiveStatus = (status) => ACTIVE_STATUSES.includes(status);

export const EMPTY_TRANSLATION = Object.freeze({
  id: null, status: 'idle', job: null, result: null, preview: null, resync: false,
});

function normalizeBlock(raw, withSource) {
  if (!raw || typeof raw.id !== 'string' || !raw.id) return null;
  const block = { id: raw.id, translation: typeof raw.translation === 'string' ? raw.translation : null };
  if (withSource) {
    block.source = typeof raw.source === 'string' ? raw.source : '';
    block.kind = typeof raw.kind === 'string' ? raw.kind : 'other';
  }
  return block;
}

function previewMeta(payload) {
  return {
    jobId: String(payload.job_id || ''),
    inputKey: String(payload.input_key || ''),
    cursor: typeof payload.cursor === 'string' && payload.cursor ? payload.cursor : null,
    bodyComplete: payload.body_complete === true,
  };
}

/**
 * 把一次快照里的 preview 合并进当前预览。返回 { preview, resync }:
 * reset → 整份替换;增量 → 同一任务(job_id + input_key)才按 id 合并;对不上(换了任务或输入)立即清掉旧块
 * (重取期间也不再显示旧正文),并要求不带 cursor 重取 reset。
 */
export function mergePreview(current, payload) {
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.blocks)) {
    return { preview: current, resync: false };
  }
  if (payload.reset) {
    const blocks = payload.blocks.map((raw) => normalizeBlock(raw, true)).filter(Boolean);
    return { preview: { ...previewMeta(payload), blocks }, resync: false };
  }
  const meta = previewMeta(payload);
  if (!current || current.jobId !== meta.jobId || current.inputKey !== meta.inputKey) {
    return { preview: null, resync: true };
  }
  const ready = new Map();
  for (const raw of payload.blocks) {
    const block = normalizeBlock(raw, false);
    if (block?.translation != null) ready.set(block.id, block.translation);
  }
  const blocks = ready.size
    ? current.blocks.map((block) => (ready.has(block.id) ? { ...block, translation: ready.get(block.id) } : block))
    : current.blocks;
  return { preview: { ...current, ...meta, blocks }, resync: false };
}

/**
 * 应用一次快照({status, job, result, preview?}),返回这篇的新状态。切文章、换任务 / 输入时旧预览作废。
 * 快照里没有 preview(任务完成后 job 已删除、正文已有缓存、或没有任务)时一律清掉本地预览、改看权威的整篇 result:
 * 终态快照不带 input 身份,无法证明本地块与这份缓存是同一输入,不猜;分块 → 整篇的切换由阅读锚点保位。
 */
export function applyTranslationSnapshot(prev, articleId, data) {
  const base = prev && prev.id === articleId ? prev : { ...EMPTY_TRANSLATION, id: articleId };
  const status = typeof data?.status === 'string' ? data.status : 'idle';
  const job = data?.job && typeof data.job === 'object' ? data.job : null;
  const result = data?.result && typeof data.result === 'object' ? data.result : null;
  let preview = null;
  let resync = false;
  if (data?.preview) ({ preview, resync } = mergePreview(base.preview, data.preview));
  return { id: articleId, status, job, result, preview, resync };
}

// ── 停止态的那一行(整篇只出现一次,跟在第一个未完成块之后):一句原因 + 可用的恢复动作 ──
// 额度用尽(服务端 can_resume=false 且给了 retry_after)不给立即继续;cancelled 只会来自旧客户端或其它入口。
export function isQuotaStop(job) {
  return Boolean(job) && job.can_resume === false && job.retry_after != null;
}

export function stoppedLine(status, job) {
  if (status === 'stale') return { text: '原文已更新', action: 'restart', label: '重新翻译' };
  if (status !== 'failed' && status !== 'cancelled') return null;
  if (isQuotaStop(job)) return { text: '今日 AI 翻译额度已用完，明天可继续', action: null, label: '' };
  const resumable = job?.can_resume === true;
  if (status === 'cancelled') return { text: '翻译已取消', action: resumable ? 'resume' : null, label: '继续翻译' };
  return { text: '翻译失败', action: resumable ? 'resume' : null, label: '重试' };
}

/** 读屏一次性播报:只在亲眼看着任务从进行中走到终态时播一句;打开时已是终态不补播。 */
export function announcementOf(status, job) {
  if (status === 'succeeded') return '中文译文已就绪';
  if (status === 'failed') return isQuotaStop(job) ? '今日 AI 翻译额度已用完' : '翻译失败';
  return '';
}

// ── 骨架形状:按块原文的顶层结构逐块估算(段落 / 标题 / 列表 / 引文 / 代码 / 表格 / 块级公式 / 独立图片)──
const LIST_RE = /^\s*(?:[-*+]|\d+[.)])\s+/;
const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*)$/;
const TABLE_RULE_RE = /^\s*\|?[\s:|-]+\|?\s*$/;
const IMAGE_RE = /^!\[[^\]]*\]\([^)]*\)$/;
const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})/;

const isMathOpen = (line) => /^\s*(\$\$|\\\[)/.test(line);
const isTableRow = (line) => line.trimStart().startsWith('|');
const isQuote = (line) => line.trimStart().startsWith('>');
const startsBlock = (line) => FENCE_RE.test(line) || isMathOpen(line) || HEADING_RE.test(line)
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
    const fence = FENCE_RE.exec(line);
    if (fence) {
      const marker = fence[1][0];
      const body = [];
      for (i += 1; i < lines.length && !lines[i].trimStart().startsWith(marker.repeat(3)); i += 1) {
        body.push(lines[i].replace(/\s+$/, '').length);
      }
      i += 1;
      blocks.push({ type: 'code', lines: body.length ? body : [0] });
      continue;
    }
    if (isMathOpen(line)) {
      const close = line.trimStart().startsWith('$$') ? '$$' : '\\]';
      const opener = line.trim();
      const body = [opener.slice(2)];
      // 单行 $$ … $$ / \[ … \] 自成一块
      if (!(opener.length > 2 && opener.endsWith(close))) {
        for (i += 1; i < lines.length && !lines[i].includes(close); i += 1) body.push(lines[i].trim());
        if (i < lines.length) body.push(lines[i].replace(close, '').trim());
      }
      i += 1;
      blocks.push({ type: 'math', chars: body.join(' ').replace(/\$\$|\\\]/g, '').trim().length });
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
      for (; i < lines.length && lines[i].trim(); i += 1) {
        if (LIST_RE.test(lines[i])) items.push(stripMarkdown(lines[i].replace(LIST_RE, '')).length);
        else if (/^\s+/.test(lines[i]) && items.length) items[items.length - 1] += stripMarkdown(lines[i]).length + 1;
        else break;
      }
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

/** 一个服务端预览块的骨架形状:按它的原文切顶层结构;切不出来时按篇幅画段落。 */
export function blockShapes(block) {
  const source = typeof block?.source === 'string' ? block.source : '';
  if (block?.kind === 'image') return [{ type: 'image' }];
  const shapes = parseBlocks(source);
  if (shapes.length) return shapes;
  return [{ type: 'paragraph', chars: Math.max(1, stripMarkdown(source).length) }];
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
