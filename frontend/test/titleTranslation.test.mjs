// issue #218 标题中文化与正文渐进翻译的纯逻辑:标题选择与偏好来源、预览块合并、骨架形状估算、
// 停止原因 / 读屏播报文案、阅读锚点(假 DOM 上的几何)。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  GUEST_TITLE_PREF_KEY,
  accountTitlePreference,
  chineseTitleOf,
  displayTitle,
  readGuestTitlePreference,
  titleView,
  titleZhFromResult,
  writeGuestTitlePreference,
} from '../src/utils/titleDisplay.js';
import {
  EMPTY_TRANSLATION,
  announcementOf,
  applyTranslationSnapshot,
  blockShapes,
  isActiveStatus,
  lineWidths,
  mergePreview,
  parseBlocks,
  stoppedLine,
} from '../src/utils/translationBlocks.js';
import { captureAnchor, restoreAcross, restoreAnchor } from '../src/utils/translationAnchor.js';
import { playerItemTitle, podcastTrackItem, sanitizeItem } from '../src/utils/playerCore.js';

const memoryStorage = () => {
  const map = new Map();
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    map,
  };
};

// ── 标题 ──

test('标题:偏好开且有有效译名 → 中文主标题 + 原题小字;偏好关 / 待译 / 同名 → 原题', () => {
  const article = { title: 'Speculative decoding, explained', title_zh: '推测解码详解' };
  assert.deepEqual(titleView(article, true), { title: '推测解码详解', orig: 'Speculative decoding, explained' });
  assert.deepEqual(titleView(article, false), { title: 'Speculative decoding, explained', orig: '' });
  assert.deepEqual(titleView({ title: 'Pending one', title_zh: null }, true), { title: 'Pending one', orig: '' });
  assert.deepEqual(titleView({ title: '量子位原题', title_zh: '量子位原题' }, true), { title: '量子位原题', orig: '' });
  assert.equal(chineseTitleOf({ title: 'A', title_zh: '   ' }), '');
  assert.equal(chineseTitleOf({ title: 'A' }), '');
  assert.equal(displayTitle({ title: '', title_zh: null }, true), '（无标题）');
  assert.equal(displayTitle({ title: '' }, true, ''), '');
  assert.equal(displayTitle(null, true), '（无标题）');
});

test('标题:正文任务带回的中文标题只在原题非中文、译名不同于原题时回填', () => {
  assert.equal(titleZhFromResult({ title: 'Release notes for v2' }, '第二版发布说明'), '第二版发布说明');
  assert.equal(titleZhFromResult({ title: '阶跃发布 StepAudio 3' }, '阶跃发布 StepAudio 3'), null);
  assert.equal(titleZhFromResult({ title: '阶跃发布新模型' }, '另一个标题'), null, '原题本是中文');
  assert.equal(titleZhFromResult({ title: 'Same' }, 'Same'), null);
  assert.equal(titleZhFromResult({ title: 'X' }, '  '), null);
  assert.equal(titleZhFromResult({ title: '' }, '标题'), null);
});

test('偏好来源:账号缺省视为开,只有显式 false 才关;游客只读写本机', () => {
  assert.equal(accountTitlePreference(null), true);
  assert.equal(accountTitlePreference({}), true);
  assert.equal(accountTitlePreference({ prefer_chinese_titles: true }), true);
  assert.equal(accountTitlePreference({ prefer_chinese_titles: false }), false);
  assert.equal(accountTitlePreference({ prefer_chinese_titles: null }), true, 'null 是缺失,不是关闭');

  const storage = memoryStorage();
  assert.equal(readGuestTitlePreference(storage), true, '未存过 = 默认开');
  writeGuestTitlePreference(storage, false);
  assert.equal(storage.map.get(GUEST_TITLE_PREF_KEY), 'off');
  assert.equal(readGuestTitlePreference(storage), false);
  writeGuestTitlePreference(storage, true);
  assert.equal(readGuestTitlePreference(storage), true);
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(readGuestTitlePreference(broken), true);
  assert.doesNotThrow(() => writeGuestTitlePreference(broken, false));
  assert.equal(readGuestTitlePreference(null), true);
});

test('播放条标题:条目记下译名,偏好开显示译名(不标 lang),会话恢复保留译名', () => {
  const episode = {
    id: 'ep-1', title: 'Meta shakes up the frontiers', title_zh: 'Meta 重组前沿实验室',
    podcast: { audio_url: 'https://cdn.example.com/a.mp3', show_title: 'Interconnects', duration_seconds: 60 },
  };
  const item = podcastTrackItem(episode, 'original');
  assert.equal(item.titleZh, 'Meta 重组前沿实验室');
  assert.deepEqual(playerItemTitle(item, true), { title: 'Meta 重组前沿实验室', lang: '' });
  assert.deepEqual(playerItemTitle(item, false), { title: 'Meta shakes up the frontiers', lang: 'en' });
  assert.equal(sanitizeItem(item).titleZh, 'Meta 重组前沿实验室');
  assert.equal(sanitizeItem({ ...item, titleZh: 42 }).titleZh, '');
  assert.deepEqual(playerItemTitle(null, true), { title: '', lang: '' });
});

// ── 预览块合并 ──

const reset = (over = {}) => ({
  job_id: 'j1', input_key: 'k1', cursor: 'j1:1', reset: true, body_complete: false, total_blocks: 3,
  blocks: [
    { id: 'b0', source: '# Title', kind: 'heading', translation: '# 标题' },
    { id: 'b1', source: 'First paragraph.', kind: 'paragraph', translation: null },
    { id: 'b2', source: '- a\n- b', kind: 'list', translation: null },
  ],
  ...over,
});

test('预览:reset 整份替换;增量按 id 合并、不重排、游标前进', () => {
  const base = mergePreview(null, reset()).preview;
  assert.equal(base.blocks.length, 3);
  assert.deepEqual(base.blocks[1], { id: 'b1', source: 'First paragraph.', kind: 'paragraph', translation: null });
  assert.equal(base.cursor, 'j1:1');
  const delta = mergePreview(base, {
    job_id: 'j1', input_key: 'k1', cursor: 'j1:5', reset: false, body_complete: false, blocks: [{ id: 'b2', translation: '- 甲\n- 乙' }],
  });
  assert.equal(delta.resync, false);
  assert.deepEqual(delta.preview.blocks.map((b) => b.id), ['b0', 'b1', 'b2']);
  assert.equal(delta.preview.blocks[2].translation, '- 甲\n- 乙');
  assert.equal(delta.preview.blocks[2].source, '- a\n- b', '增量不带 source,保留基线的原文');
  assert.equal(delta.preview.blocks[1].translation, null);
  assert.equal(delta.preview.cursor, 'j1:5');
  const empty = mergePreview(delta.preview, { job_id: 'j1', input_key: 'k1', cursor: 'j1:5', reset: false, blocks: [] });
  assert.equal(empty.preview.blocks, delta.preview.blocks, '空增量不换数组');
});

test('预览:换任务 / 换输入的增量立即清掉旧块,要求不带游标重取;坏载荷忽略', () => {
  const base = mergePreview(null, reset()).preview;
  const otherJob = mergePreview(base, { job_id: 'j2', input_key: 'k1', cursor: 'j2:1', reset: false, blocks: [{ id: 'b1', translation: 'X' }] });
  assert.deepEqual(otherJob, { preview: null, resync: true }, '换任务:重取期间不再显示旧正文');
  const otherInput = mergePreview(base, { job_id: 'j1', input_key: 'k2', cursor: 'j1:1', reset: false, blocks: [] });
  assert.deepEqual(otherInput, { preview: null, resync: true }, '换输入:同上');
  assert.equal(mergePreview(null, { job_id: 'j1', input_key: 'k1', reset: false, blocks: [] }).resync, true);
  assert.equal(mergePreview(base, { blocks: 'nope' }).preview, base);
  const dirty = mergePreview(null, reset({ blocks: [{ id: '', translation: 'x' }, { id: 'b5', translation: 7 }, null] })).preview;
  assert.deepEqual(dirty.blocks, [{ id: 'b5', translation: null, source: '', kind: 'other' }]);
});

test('快照:切文章清旧预览;快照不带预览时一律清掉本地块,改看权威整篇 result(不猜同一输入)', () => {
  let state = applyTranslationSnapshot(EMPTY_TRANSLATION, 'a1', { status: 'running', job: { job_id: 'j1' }, result: null, preview: reset() });
  assert.equal(state.preview.blocks.length, 3);
  const switched = applyTranslationSnapshot(state, 'a2', { status: 'idle', job: null, result: null });
  assert.equal(switched.preview, null);
  assert.equal(switched.id, 'a2');

  const incomplete = applyTranslationSnapshot(state, 'a1', { status: 'succeeded', job: null, result: { translation: '全文', title: '标题' } });
  assert.equal(incomplete.preview, null, '最后一片的增量没收到:改看整篇缓存');
  state = applyTranslationSnapshot(state, 'a1', {
    status: 'running', job: { job_id: 'j1' }, result: null,
    preview: { job_id: 'j1', input_key: 'k1', cursor: 'j1:7', reset: false, body_complete: true, blocks: [{ id: 'b1', translation: '第一段。' }, { id: 'b2', translation: '- 甲' }] },
  });
  assert.ok(state.preview.blocks.every((block) => typeof block.translation === 'string'), '本地块已整篇就绪');
  const done = applyTranslationSnapshot(state, 'a1', { status: 'succeeded', job: null, result: { translation: '# 标题\n\n第一段。\n\n- 甲', title: '标题' } });
  assert.equal(done.preview, null, '整篇已就绪也改看权威 result(切换由锚点保位)');
  assert.equal(done.result.translation, '# 标题\n\n第一段。\n\n- 甲');
  const titlePending = applyTranslationSnapshot(state, 'a1', { status: 'running', job: { job_id: 'j1' }, result: { translation: '全文', title: null }, preview: null });
  assert.equal(titlePending.preview, null, '正文已缓存、标题未就绪:看整篇缓存');
  const gone = applyTranslationSnapshot(state, 'a1', { status: 'idle', job: null, result: null });
  assert.equal(gone.preview, null, '任务没了(原文变了)且无缓存:清掉');
  assert.equal(isActiveStatus('queued') && isActiveStatus('running') && !isActiveStatus('failed'), true);
});

test('快照回归:多行块只改了后面几行(表格第三行 / 列表 / 代码)时,绝不保留旧块', () => {
  const oldTable = '| A | B |\n|---|---|\n|old|old|';
  const oldList = '- 第一项\n- 旧的第二项';
  const oldCode = '```\nprint(1)\nold()\n```';
  let state = applyTranslationSnapshot(EMPTY_TRANSLATION, 'a1', {
    status: 'running', job: { job_id: 'j1' }, result: null,
    preview: { job_id: 'j1', input_key: 'k1', cursor: 'j1:7', reset: true, body_complete: true, total_blocks: 3, blocks: [
      { id: 'b0', source: 'table', kind: 'table', translation: oldTable },
      { id: 'b1', source: 'list', kind: 'list', translation: oldList },
      { id: 'b2', source: 'code', kind: 'code', translation: oldCode },
    ] },
  });
  const fresh = '| A | B |\n|---|---|\n|new|new|\n\n- 第一项\n- 新的第二项\n\n```\nprint(1)\nnew()\n```';
  state = applyTranslationSnapshot(state, 'a1', { status: 'succeeded', job: null, result: { translation: fresh, title: '标题' } });
  assert.equal(state.preview, null, '首行相同不能证明同一输入');
  assert.equal(state.result.translation, fresh);
  assert.ok(!JSON.stringify(state).includes('old'), '旧表格行 / 列表项 / 代码都不留在状态里');
});

// ── 停止原因与读屏播报 ──

test('停止原因:失败给重试;额度用尽不给立即继续;取消给继续;原文更新给重新翻译;进行中 / 完成没有', () => {
  assert.deepEqual(stoppedLine('failed', { can_resume: true, retry_after: null }), { text: '翻译失败', action: 'resume', label: '重试' });
  assert.deepEqual(stoppedLine('failed', { can_resume: false, retry_after: 3600 }), { text: '今日 AI 翻译额度已用完，明天可继续', action: null, label: '' });
  assert.deepEqual(stoppedLine('cancelled', { can_resume: true }), { text: '翻译已取消', action: 'resume', label: '继续翻译' });
  assert.equal(stoppedLine('cancelled', { can_resume: false, retry_after: 10 }).action, null);
  assert.deepEqual(stoppedLine('stale', null), { text: '原文已更新', action: 'restart', label: '重新翻译' });
  assert.equal(stoppedLine('failed', { can_resume: false, retry_after: null }).action, null);
  assert.equal(stoppedLine('running', {}), null);
  assert.equal(stoppedLine('succeeded', null), null);
});

test('读屏播报:完成 / 失败 / 额度各一句;取消、原文更新不播', () => {
  assert.equal(announcementOf('succeeded', null), '中文译文已就绪');
  assert.equal(announcementOf('failed', { can_resume: true }), '翻译失败');
  assert.equal(announcementOf('failed', { can_resume: false, retry_after: 5 }), '今日 AI 翻译额度已用完');
  assert.equal(announcementOf('cancelled', {}), '');
  assert.equal(announcementOf('stale', null), '');
});

// ── 骨架形状 ──

test('骨架:块原文切顶层结构(标题 / 段落 / 列表续行 / 有序列表 / 代码围栏 / 公式 / 表格 / 引文 / 图片)', () => {
  const types = (md) => parseBlocks(md).map((b) => b.type).join(',');
  assert.equal(types('## Why it matters\n\nBody text here.'), 'heading,paragraph');
  const list = parseBlocks('- first item\n  continued line\n- second\n1) ordered');
  assert.equal(list.length, 1);
  assert.equal(list[0].items.length, 3);
  assert.ok(list[0].items[0] > 'first item'.length, '缩进续行计入上一项');
  const fence = parseBlocks('~~~python\nprint(1)\n\nx = 2\n~~~');
  assert.deepEqual(fence, [{ type: 'code', lines: [8, 0, 5] }]);
  assert.deepEqual(parseBlocks('```\n```'), [{ type: 'code', lines: [0] }]);
  assert.equal(types('$$\nE = mc^2\n$$'), 'math');
  assert.equal(parseBlocks('$$ a+b $$')[0].type, 'math');
  assert.equal(types('\\[\nx\n\\]'), 'math');
  const table = parseBlocks('| a | bb |\n|---|---|\n| 1 | 22 |');
  assert.deepEqual(table, [{ type: 'datatable', rows: [[1, 2], [1, 2]] }]);
  assert.equal(types('> quoted\n> more'), 'quote');
  assert.equal(types('![chart](https://x/y.png)'), 'image');
  assert.equal(parseBlocks('Title\n### Sub').map((b) => b.type).join(','), 'paragraph,heading');
});

test('骨架:服务端块类型兜底——图片按图片画;切不出结构的按篇幅画一段', () => {
  assert.deepEqual(blockShapes({ kind: 'image', source: '<img src="x">' }), [{ type: 'image' }]);
  assert.deepEqual(blockShapes({ kind: 'other', source: '' }), [{ type: 'paragraph', chars: 1 }]);
  assert.equal(blockShapes({ kind: 'list', source: '- a\n- b' })[0].items.length, 2);
  assert.equal(blockShapes({ kind: 'paragraph', source: 'A '.repeat(100).trim() })[0].chars, 199);
});

test('估行:300 字符 @640px/17px = 4 行且末行变短;窄屏行数更多;短标题单行有下限', () => {
  const desktop = lineWidths(300, 640 / 17);
  assert.equal(desktop.length, 4);
  assert.ok(desktop[3] < 100);
  assert.ok(lineWidths(300, 335 / 16).length > desktop.length);
  assert.deepEqual(lineWidths(10, 40), [28]);
  assert.deepEqual(lineWidths(0, 40), [28]);
});

// ── 阅读锚点(假 DOM:滚动容器顶 = 0,标题区 head 之后依次排块)──

// state.whole 非空 = 整篇缓存视图:没有块包层(querySelectorAll 为空),正文顶层元素就是 whole 里的各段
function layout({ head = 300, viewport = 800, blocks }) {
  const state = { scrollTop: 0, blocks: blocks.map((b) => ({ ...b })), whole: null };
  const items = () => state.whole || state.blocks;
  const offsetOf = (index) => head + items().slice(0, index).reduce((sum, b) => sum + b.height, 0);
  const node = (index, view) => {
    const item = items()[index];
    return {
      getBoundingClientRect() {
        const top = offsetOf(index) - state.scrollTop;
        return { top, bottom: top + item.height, height: item.height };
      },
      hasAttribute(name) {
        if (name === 'data-tr-pending') return Boolean(item.pending);
        if (name === 'data-tr-block') return view === 'zh';
        return false;
      },
      getAttribute(name) {
        return name === 'data-tr-block' && view === 'zh' ? item.id : null;
      },
      textContent: item.text || '',
      querySelector: () => null,
    };
  };
  const scroller = {
    get scrollTop() { return state.scrollTop; },
    set scrollTop(value) { state.scrollTop = Math.max(0, value); },
    getBoundingClientRect: () => ({ top: 0, bottom: viewport, height: viewport }),
  };
  const copy = {
    getBoundingClientRect() {
      const top = head - state.scrollTop;
      const height = items().reduce((sum, b) => sum + b.height, 0);
      return { top, bottom: top + height, height };
    },
    querySelectorAll: () => (state.whole ? [] : state.blocks.map((_, i) => node(i, 'zh'))),
    get children() { return items().map((_, i) => node(i, 'orig')); },
  };
  const topOf = (id) => offsetOf(items().findIndex((b) => b.id === id)) - state.scrollTop;
  return { state, scroller, copy, topOf };
}

test('锚点:视口顶部在骨架、下方能看到已译块时锁住已译块;前方骨架换成更短的译文后它不动', () => {
  const page = layout({ blocks: [
    { id: 'b0', height: 760, pending: true },
    { id: 'b1', height: 500, pending: true },
    { id: 'b2', height: 400 },
  ] });
  page.state.scrollTop = 1200; // 视口顶部落在 b1 骨架里,b2 顶部在视口内 360px 处
  const before = page.topOf('b2');
  const anchor = captureAnchor(page.scroller, page.copy, 'zh');
  assert.equal(anchor.index, 'b2');
  // b0 就绪:骨架 760 → 译文 600;停止态那一行原因(37px)随「第一个未完成块」挪进 b1,b1 骨架变高
  page.state.blocks[0] = { id: 'b0', height: 600 };
  page.state.blocks[1] = { id: 'b1', height: 537, pending: true };
  restoreAnchor(page.scroller, page.copy, 'zh', anchor);
  assert.equal(page.topOf('b2'), before);
});

test('锚点:视口里只有骨架时按所在块的片内比例落位;还在标题区时不动', () => {
  const page = layout({ blocks: [
    { id: 'b0', height: 400 },
    { id: 'b1', height: 2000, pending: true },
    { id: 'b2', height: 400, pending: true },
  ] });
  page.state.scrollTop = 1200; // b1 顶部 = 700 - 1200 = -500 → 片内 25%
  const anchor = captureAnchor(page.scroller, page.copy, 'zh');
  assert.equal(anchor.index, 'b1');
  assert.equal(anchor.fraction, 0.25);
  page.state.blocks[1] = { id: 'b1', height: 1600 }; // 所在骨架就绪,变矮
  restoreAnchor(page.scroller, page.copy, 'zh', anchor);
  assert.equal(page.topOf('b1'), -400, '仍在块内 25% 处');

  const top = layout({ blocks: [{ id: 'b0', height: 400, pending: true }] });
  top.state.scrollTop = 100; // 标题区仍在视口顶部
  assert.equal(captureAnchor(top.scroller, top.copy, 'zh'), null);
  top.state.scrollTop = 0;
  assert.equal(captureAnchor(top.scroller, top.copy, 'zh'), null);
});

test('锚点:切视图按序号对齐同一块;两边块数不同按全文比例', () => {
  const page = layout({ blocks: [
    { id: 'p0', height: 300 }, { id: 'p1', height: 900 }, { id: 'p2', height: 300 },
  ] });
  page.state.scrollTop = 900; // 原文:p1 顶部 = 600 - 900 = -300 → 片内 1/3
  const anchor = captureAnchor(page.scroller, page.copy, 'orig');
  assert.equal(anchor.position, 1);
  page.state.blocks = [{ id: 'b0', height: 240 }, { id: 'b1', height: 600, pending: true }, { id: 'b2', height: 240 }];
  restoreAcross(page.scroller, page.copy, 'zh', anchor);
  assert.ok(Math.abs(page.topOf('b1') + 200) < 0.5, '中文视图同一块的 1/3 处');

  page.state.blocks = [{ id: 'estimate', height: 3000, pending: true }];
  page.state.scrollTop = 900;
  restoreAcross(page.scroller, page.copy, 'zh', anchor);
  const rect = page.copy.getBoundingClientRect();
  assert.ok(Math.abs((0 - rect.top) / rect.height - anchor.progress) < 0.01, '块数不同:按全文比例');
});
