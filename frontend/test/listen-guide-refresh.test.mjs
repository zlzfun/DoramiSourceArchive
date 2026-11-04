import test from 'node:test';
import assert from 'node:assert/strict';
import { withFreshAnalysis } from '../src/utils/analysisProjection.js';
import { articleListenActive, articleListenEntries, podcastListenEntries } from '../src/utils/listenEntries.js';
import { articleNarrationItem, podcastTrackItem } from '../src/utils/playerCore.js';

const opts = { aiEnabled: true, ondemandEnabled: true };

test('详情回读驱动排队、编写、合成、可播,并保留本地阅读信息', () => {
  let article = { id: 'a', title: 'Article', read_count: 9 };
  assert.equal(articleListenEntries(article, opts)[0].kind, 'request');
  for (const [status, label] of [['queued', '导读排队中…'], ['narrating', '正在编写导读…'], ['synthesizing', '导读音频生成中…']]) {
    article = withFreshAnalysis(article, { listen_guide: { status, audio_ready: false } });
    assert.equal(articleListenActive(article), true);
    assert.equal(articleListenEntries(article, opts)[0].label, label);
    assert.equal(article.read_count, 9);
  }
  article = withFreshAnalysis(article, { listen_guide: { status: 'ready', audio_ready: true, audio_url: '/api/reader/articles/a/listen-audio', duration_seconds: 300 } });
  assert.equal(articleListenActive(article), false);
  assert.equal(articleListenEntries(article)[0].kind, 'play');
  assert.equal(articleNarrationItem(article).src, '/api/reader/articles/a/listen-audio');
});

test('失败停止轮询,提示与重试权限分开;已生成但被撤销的音频不会残留', () => {
  const failed = withFreshAnalysis({ id: 'a', listen_guide: { audio_ready: true, audio_url: '/old.mp3' } }, { listen_guide: { status: 'failed', audio_ready: false } });
  assert.equal(articleListenActive(failed), false);
  assert.equal(articleNarrationItem(failed), null);
  assert.equal(articleListenEntries(failed, opts)[0].label, '重试导读');
  assert.equal(articleListenEntries(failed)[0].kind, 'notice');
});

test('#242 已有播客音频不受 AI/点播总闸、失败状态影响;文字就绪不冒充音频', () => {
  for (const url of ['/api/reader/podcast-artifacts/x/audio', 'https://media.example/guide.mp3']) {
    const article = { id: 'p', podcast: { condensed_audio_url: url, premium_guide: { status: 'failed', blog_ready: true } } };
    assert.equal(podcastListenEntries(article)[0].track, 'digest');
    assert.equal(podcastTrackItem(article, 'digest').src, url);
  }
  for (const status of ['ready', 'failed', 'queued']) {
    const article = { id: 'p', podcast: { premium_guide: { status, blog_ready: true }, condensed_audio_url: '' } };
    assert.equal(podcastListenEntries(article, opts).some((e) => e.track === 'digest'), false);
    assert.equal(podcastTrackItem(article, 'digest'), null);
  }
});
