import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { analysisNeedsPolling, podcastAssessmentMeta, podcastReaderBasisText } from '../src/utils/analysis.js';
import { podcastListenEntries } from '../src/utils/listenEntries.js';
import { podcastListAvailabilityMeta } from '../src/utils/podcast.js';
import { buildListFilters } from '../src/utils/articleList.js';
import { polledMembershipChanged } from '../src/utils/contentKind.js';

const episode = (podcast = {}, other = {}) => ({
  id: 'episode', content_type: 'podcast_episode',
  quality_score: 7.6, content_value: 7.6, primary_dimension: 'content',
  score_reason: '讨论提供了具体案例', content_value_reason: '讨论提供了具体案例',
  podcast, ...other,
});

test('读者评分依据认公开完成标记；原稿、导读或任务状态不能冒充完成分析', () => {
  assert.equal(podcastReaderBasisText(episode({ full_analysis_ready: true })), '基于整期内容');
  for (const podcast of [
    {}, { full_analysis_ready: false }, { transcript_available: true },
    { premium_guide: { blog_ready: true, audio_ready: true } },
    { processing_status: 'ready' }, { processing_status: 'running', stage: 'analyze' },
  ]) {
    assert.equal(podcastReaderBasisText(episode(podcast)), '仅基于节目简介');
  }
  for (const basis of ['publisher_transcript', 'asr_transcript']) {
    assert.equal(podcastReaderBasisText(episode({}, { analysis_basis: basis })), '基于整期内容');
    assert.equal(podcastReaderBasisText(episode({ analysis_basis: basis })), '基于整期内容');
  }
  assert.equal(podcastReaderBasisText({ content_type: 'rss_article' }), '');
});

test('刷新中仍解释已有分数；管理台保留详细标签', () => {
  const full = episode({ full_analysis_ready: true, processing_status: 'failed' }, { analysis_status: 'running' });
  assert.equal(podcastReaderBasisText(full), '基于整期内容');
  assert.equal(podcastAssessmentMeta(full).label, '全文深度分析');
  assert.equal(podcastAssessmentMeta(episode()).label, '简介初评');
});

test('等待重试保留生成反馈并继续轮询；已发布文字仍可读', () => {
  for (const blog_ready of [false, true]) {
    const article = episode({ premium_guide: { status: 'retry_wait', blog_ready } });
    assert.equal(analysisNeedsPolling(article), true);
    assert.equal(podcastListAvailabilityMeta(article.podcast).label, blog_ready ? '中文导读' : '导读生成中…');
    const entries = podcastListenEntries(article, { aiEnabled: true, ondemandEnabled: true });
    assert.equal(entries[0].kind, 'busy');
    assert.equal(entries[0].label, blog_ready ? '导读音频生成中…' : '导读生成中…');
  }
});

test('导读筛选与关键词、来源、未读、收藏相交；不污染文章查询', () => {
  const query = { activeSourceId: 'podcast-source', mode: 'podcast', scope: { axis: 'subscribed', favorite: true },
    searchQuery: '产品', unreadOnly: true };
  for (const podcastGuideFilter of ['any', 'readable', 'listenable']) {
    const filters = buildListFilters({ ...query, podcastGuideFilter });
    assert.equal(filters.podcast_guide, podcastGuideFilter);
    assert.equal(filters.source_id, query.activeSourceId);
    assert.equal(filters.search, '产品');
    assert.equal(filters.unread_only, 'true');
    assert.equal(filters.favorite_scope, 'only');
    assert.equal(buildListFilters({ ...query, mode: 'article', podcastGuideFilter }).podcast_guide, undefined);
  }
  assert.equal(buildListFilters(query).podcast_guide, undefined);
  assert.equal(polledMembershipChanged([{ id: 'old' }], new Set(['old']), new Set(), { podcastGuideFilter: 'readable' }), true);
});

test('列表真实组件：优质与导读能力正交，书本/耳机分别表达可看/可听', async (t) => {
  const cacheDir = mkdtempSync(join(tmpdir(), 'dorami-podcast-list-'));
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), cacheDir,
    logLevel: 'silent', server: { middlewareMode: true }, appType: 'custom' });
  try {
    const { ArticleRow } = await server.ssrLoadModule('/src/components/ReaderTab.jsx');
    const cases = [
      ['无导读', false, false, '', ''], ['排队', false, false, 'queued', '导读生成中…'],
      ['等待重试', false, false, 'retry_wait', '导读生成中…'], ['失败无产物', false, false, 'failed', ''],
      ['仅文字', true, false, 'ready', '中文导读'], ['文字已有音频生成中', true, false, 'synthesizing', '中文导读'],
      ['文字已有音频失败', true, false, 'failed', '中文导读'], ['仅音频', false, true, 'ready', '有声导读'],
      ['文字及音频', true, true, 'ready', '有声导读'], ['状态完成无产物', false, false, 'ready', ''],
    ];
    for (const [name, blog_ready, audio, status, label] of cases) {
      for (const premium of [false, true]) await t.test(`${name} / 优质=${premium}`, () => {
        const article = episode({ full_analysis_ready: true, condensed_audio_url: audio ? '/audio' : '',
          premium_guide: { blog_ready, status } }, { title: '测试节目', is_premium_podcast: premium });
        const html = renderToStaticMarkup(createElement(ArticleRow, { article, entryPodcast: true, sourceName: '节目' }));
        assert.equal(html.includes('podcast-premium-badge'), premium);
        assert.equal(html.includes('reader-podcast-availability'), Boolean(label));
        if (label) assert.ok(html.includes(label));
        assert.equal(html.includes('lucide-book-open-text'), blog_ready);
        assert.equal(html.includes('lucide-headphones'), audio);
        assert.doesNotMatch(html, /暂无中文导读|仅提供原节目|简介初评|全文深度分析/);
        if (audio && blog_ready) assert.match(html, /也可阅读中文导读/);
      });
    }
  } finally {
    await server.close();
    rmSync(cacheDir, { recursive: true, force: true });
  }
});

test('真实速读卡渲染：读者/游客缺诊断字段也不误标，摘要与评分理由不混用', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const cacheDir = mkdtempSync(join(tmpdir(), 'dorami-podcast-render-'));
  const server = await createServer({ root, cacheDir, logLevel: 'silent', server: { middlewareMode: true }, appType: 'custom' });
  try {
    const { default: Card } = await server.ssrLoadModule('/src/components/AiReadingCard.jsx');
    for (const full_analysis_ready of [false, true]) {
      const article = episode({ full_analysis_ready });
      const html = renderToStaticMarkup(createElement(Card, { article, podcast: true, summary: '实际摘要' }));
      assert.match(html, /reader-ai-layer-title">AI 速读<\/span>/);
      assert.match(html, /实际摘要/);
      assert.match(html, full_analysis_ready ? /基于整期内容/ : /仅基于节目简介/);
      assert.doesNotMatch(html, /简介导读|全文导读|简介初评|全文深度分析|尚未分析完整音频/);
    }
    const onlyReason = renderToStaticMarkup(createElement(Card, { article: episode(), podcast: true }));
    assert.match(onlyReason, /reader-ai-layer-title">评分参考<\/span>/);
    assert.match(onlyReason, /讨论提供了具体案例/);
    const empty = renderToStaticMarkup(createElement(Card, {
      article: episode({}, { quality_score: null, content_value: null, score_reason: '', content_value_reason: '' }),
      podcast: true, canGenerate: true,
    }));
    assert.match(empty, /生成本期要点速读/);
    const article = renderToStaticMarkup(createElement(Card, {
      article: { id: 'article', quality_score: 7 }, summary: '普通文章摘要',
    }));
    assert.match(article, /AI 速读/);
    assert.doesNotMatch(article, /节目简介|整期内容/);
  } finally {
    await server.close();
    rmSync(cacheDir, { recursive: true, force: true });
  }
});
