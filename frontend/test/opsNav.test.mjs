import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  OPS_PAGES, formatOpsPath, normalizeOpsTarget, opsBlockId, opsBlocks, parseOpsPath,
} from '../src/utils/opsNav.js';
import { PURPOSE_LABELS } from '../src/components/admin/adminUtils.js';

test('运维 8 页按重要度排序:账户 > 阅读与榜单 > 其它,页 id 与板块 id 不重复', () => {
  assert.deepEqual(OPS_PAGES.map((p) => p.id), ['accounts', 'reading', 'reader', 'podcast', 'analysis', 'external', 'user-sources', 'inbox']);
  for (const page of OPS_PAGES) {
    const ids = page.blocks.map(([id]) => id);
    assert.equal(new Set(ids).size, ids.length, page.id);
  }
});

test('hash 路径往返:首页、页、页 + 板块;未知页回首页,未知板块只留页', () => {
  assert.deepEqual(parseOpsPath(''), { page: null, block: null });
  assert.deepEqual(parseOpsPath('podcast'), { page: 'podcast', block: null });
  assert.deepEqual(parseOpsPath('podcast/asr'), { page: 'podcast', block: 'asr' });
  assert.deepEqual(parseOpsPath('nope/asr'), { page: null, block: null });
  assert.deepEqual(parseOpsPath('podcast/nope'), { page: 'podcast', block: null });
  assert.equal(formatOpsPath({ page: 'accounts', block: 'table' }), 'accounts/table');
  assert.equal(formatOpsPath({ page: 'accounts' }), 'accounts');
  assert.equal(formatOpsPath({}), '');
  for (const page of OPS_PAGES) {
    for (const [block] of page.blocks) {
      assert.deepEqual(parseOpsPath(formatOpsPath({ page: page.id, block })), { page: page.id, block });
    }
  }
});

test('旧五档载荷 { sub, zone } 落到新页 / 板块(设置柜「ASR 配额」回指等)', () => {
  assert.deepEqual(normalizeOpsTarget({ sub: 'content', zone: 'podcast-asr-quota' }), { page: 'podcast', block: 'asr' });
  assert.deepEqual(normalizeOpsTarget({ sub: 'user' }), { page: 'accounts', block: null });
  assert.deepEqual(normalizeOpsTarget({ sub: 'ai' }), { page: 'reader', block: null });
  assert.deepEqual(normalizeOpsTarget({ sub: 'taxonomy' }), { page: 'analysis', block: null });
  assert.deepEqual(normalizeOpsTarget({ sub: 'engage' }), { page: 'inbox', block: null });
  assert.deepEqual(normalizeOpsTarget({ page: 'podcast', block: 'asr' }), { page: 'podcast', block: 'asr' });
  assert.deepEqual(normalizeOpsTarget(null), { page: null, block: null });
});

test('根专属板块只对根管理员列出', () => {
  const root = opsBlocks('accounts', { rootAdmin: true }).map((b) => b.id);
  const admin = opsBlocks('accounts', { rootAdmin: false }).map((b) => b.id);
  assert.ok(root.includes('table') && root.includes('top'));
  assert.ok(!admin.includes('table') && !admin.includes('top'));
  assert.equal(opsBlockId('podcast', 'asr'), 'ops-podcast-asr');
});

test('每个注册的板块在源码里都有落位锚点(opsBlockId 调用)', () => {
  const files = [
    'components/AdminOpsTab.jsx', 'components/admin/pages/AccountsPage.jsx', 'components/admin/pages/ReaderPage.jsx',
    'components/admin/pages/ReadingPage.jsx', 'components/admin/pages/ExternalPage.jsx',
    'components/admin/PodcastZone.jsx', 'components/admin/AdminTaxonomyPanel.jsx',
  ].map((f) => readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8')).join('\n');
  for (const page of OPS_PAGES) {
    for (const [block] of page.blocks) {
      assert.match(files, new RegExp(`opsBlockId\\('${page.id}', '${block}'\\)`), `${page.id}/${block}`);
    }
  }
});

test('App 把运维页登记为带子视图的 tab,并用 opsNav 解析', () => {
  const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
  assert.match(app, /const SUBVIEW_TABS = new Set\(\['fetch', 'admin'\]\)/);
  assert.match(app, /if \(tab === 'admin'\) view = formatOpsPath\(parseOpsPath\(/);
});

test('AI 用途标签覆盖后端登记的全部用途(含事件归并)', () => {
  const src = readFileSync(new URL('../../src/services/ai_usage.py', import.meta.url), 'utf8');
  const start = src.indexOf('VALID_PURPOSES = (');
  const block = src.slice(start, src.indexOf('\n)\n', start));
  // 只认每行开头的字符串字面量(注释里的引号不算)
  const purposes = [...block.matchAll(/^\s+"([a-z_]+)",/gm)].map((m) => m[1]);
  assert.ok(purposes.length > 10);
  for (const purpose of purposes) assert.ok(PURPOSE_LABELS[purpose], purpose);
});
