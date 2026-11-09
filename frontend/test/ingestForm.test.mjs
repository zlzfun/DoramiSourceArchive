import test from 'node:test';
import assert from 'node:assert/strict';
import { buildIngestItem, describeProvenance, describeReceipt, parseStatusLink } from '../src/utils/ingestForm.js';

test('来路一句:抓取条目无来路,收录条目带通道与收录者', () => {
  assert.equal(describeProvenance({}), '抓取');
  assert.equal(describeProvenance({ ingest_channel: '', ingest_channel_label: '抓取' }), '抓取');
  assert.equal(describeProvenance({ ingest_channel: 'push', ingest_actor: 'research-agent' }), '推送 · research-agent');
  assert.equal(describeProvenance({ ingest_channel: 'manual' }), '手工');
  // 读者看不到收录者,只剩通道。
  assert.equal(describeProvenance({ ingest_channel: 'push', ingest_channel_label: '推送' }), '推送');
});

const blank = {
  url: '', title: '', content: '', summary: '', source_id: '', publish_date: '', note: '',
  handle: '', post_id: '', feed_url: '', audio_url: '', guid: '',
};

test('帖子链接里解析出作者与帖子 id', () => {
  assert.deepEqual(parseStatusLink('https://x.com/OpenAI/status/1990000000000000001?s=20'), {
    handle: 'OpenAI', post_id: '1990000000000000001',
  });
  assert.deepEqual(parseStatusLink('https://twitter.com/karpathy/status/42'), { handle: 'karpathy', post_id: '42' });
  assert.equal(parseStatusLink('https://x.com/OpenAI'), null);
  assert.equal(parseStatusLink(''), null);
});

test('文章:来源留空即按链接自动认,填了就是显式 source_id', () => {
  const auto = buildIngestItem('article', { ...blank, url: ' https://a.test/p ', title: 'T', content: '正文' });
  assert.deepEqual(auto.source, {});
  assert.equal(auto.url, 'https://a.test/p');
  assert.equal(auto.kind, 'article');
  assert.equal(auto.publish_date, '');
  const explicit = buildIngestItem('article', { ...blank, url: 'https://a.test/p', source_id: 'rss_anil_dash' });
  assert.deepEqual(explicit.source, { source_id: 'rss_anil_dash' });
});

test('社交帖:手填优先,没填从帖子链接补', () => {
  const fromLink = buildIngestItem('social_post', { ...blank, url: 'https://x.com/OpenAI/status/7', content: 'hi' });
  assert.equal(fromLink.post_id, '7');
  assert.deepEqual(fromLink.author, { handle: 'OpenAI' });
  const manual = buildIngestItem('social_post', { ...blank, url: 'https://x.com/OpenAI/status/7', handle: 'sama', post_id: '8' });
  assert.equal(manual.post_id, '8');
  assert.deepEqual(manual.author, { handle: 'sama' });
});

test('播客单集:节目 feed 进 source,guid / 音频 / 简介原样带上', () => {
  const item = buildIngestItem('podcast_episode', {
    ...blank, feed_url: 'https://pod.test/feed.xml', title: 'EP1', audio_url: 'https://cdn.test/1.mp3', guid: 'ep-1',
    summary: '简介',
  });
  assert.deepEqual(item.source, { feed_url: 'https://pod.test/feed.xml' });
  assert.equal(item.guid, 'ep-1');
  assert.equal(item.audio_url, 'https://cdn.test/1.mp3');
  assert.equal(item.summary, '简介');
});

test('发布时间转成 ISO', () => {
  const item = buildIngestItem('article', { ...blank, url: 'https://a.test/p', publish_date: '2026-11-06T10:00' });
  assert.match(item.publish_date, /^2026-11-0[56]T\d{2}:00:00\.000Z$/);
});

test('回执:新建 / 新建临时源 / 已有三种 / 补全 / 拒收', () => {
  assert.deepEqual(describeReceipt({ status: 'created', source_id: 'rss_a' }), { tone: 'ok', stamp: '已新建', text: '收进 rss_a' });
  assert.equal(describeReceipt({ status: 'created', source_id: 'web_b', source_created: true }).text, '新建临时源 web_b');
  assert.equal(describeReceipt({ status: 'duplicate', source_id: 'rss_a' }).stamp, '已存在');
  assert.equal(describeReceipt({ status: 'duplicate_url', source_id: 'rss_a' }).text, '同一链接已在 rss_a');
  assert.equal(describeReceipt({ status: 'duplicate_content', source_id: 'rss_a' }).text, 'rss_a 里已有同一内容');
  assert.equal(describeReceipt({ status: 'refreshed', source_id: 'rss_a' }).stamp, '已更新');
  assert.deepEqual(describeReceipt({ status: 'rejected', reason: '该来源是社交源,不收文章' }), {
    tone: 'err', stamp: '未收录', text: '该来源是社交源,不收文章',
  });
  assert.equal(describeReceipt(null), null);
});
