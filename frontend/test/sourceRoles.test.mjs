import test from 'node:test';
import assert from 'node:assert/strict';
import { sourceRoleOf } from '../src/utils/sourceRole.js';

test('没有策展元数据的源归「媒体」,与后端 DEFAULT_SOURCE_ROLE 一致(issue #262 B7)', () => {
  assert.equal(sourceRoleOf({ source_id: 'manual' }), 'media');
  assert.equal(sourceRoleOf({ source_id: 'web_lab', source_scope: 'frontier_ai_lab' }), 'official');
  assert.equal(sourceRoleOf({ source_id: 'rss_x', provenance_tier: 'tier1_curated' }), 'media');
});

test('自定源与收录临时源各自成组,不混进策展角色轴', () => {
  assert.equal(sourceRoleOf({ source_id: 'user_rss_abc' }), 'custom');
  assert.equal(sourceRoleOf({ source_id: 'web_pushed', listing: 'unlisted' }), 'collected');
});

test('系统源(公共日报)单独成组,只在源管理里出现', () => {
  assert.equal(sourceRoleOf({ source_id: 'dorami_daily_brief', origin: 'system' }), 'system');
  assert.equal(sourceRoleOf({ source_id: 'web_legacy', origin: 'legacy' }), 'media');
});
