// issue #218 R2 样页 fixture:全部为虚构示例数据,不来自也不写入任何后台。
// 正文按「片」切开(content/*.md 用 <!-- r2:seg --> 分隔,中英各一份、片数一致);
// 样页把片界放在完整顶层块的边界上,所以连续前缀可以逐片渲染。真实接口片约 3,500 字符、可能切在段落 /
// 列表 / 表格中间——实现须把连续已完成前缀还原成一篇 Markdown,只显示到最后一个完整顶层块,
// 跨界的那一块整体留到下一片完成;不能把接口片当作渲染边界。
import speculativeMd from './content/speculative.md?raw';
import paperMd from './content/paper.md?raw';
import proxyMd from './content/proxy.md?raw';
import longctxMd from './content/longctx.md?raw';
import sqliteMd from './content/sqlite.md?raw';
import releaseMd from './content/release.md?raw';
import qbitaiMd from './content/qbitai.md?raw';
import podcastStackMd from './content/podcast-stack.md?raw';
import podcastAgentsMd from './content/podcast-agents.md?raw';
import { parseBlocks } from './slotBlocks';

const SEG = '<!-- r2:seg -->';

function splitSegments(text) {
  return text.split(SEG).map((part) => part.trim()).filter(Boolean);
}

// 外文正文:{ lang: 'en', segments: [{ en, zh, blocks }] };中文原文:{ lang: 'zh', text }
// blocks = 这一片原文的顶层块(逐段占位据此画骨架,只看原文,不看译文)
function bilingual(raw) {
  const [enPart, zhPart = ''] = raw.split('<!-- r2:zh -->');
  const en = splitSegments(enPart.replace('<!-- r2:en -->', ''));
  const zh = splitSegments(zhPart);
  if (en.length !== zh.length) {
    console.warn('[r2-preview] 中英片段数不一致', en.length, zh.length);
  }
  return {
    lang: 'en',
    segments: en.map((text, index) => ({ en: text, zh: zh[index] || text, blocks: parseBlocks(text) })),
  };
}

function chineseOnly(raw) {
  return { lang: 'zh', text: raw.trim() };
}

const now = Date.now();
const ago = (minutes) => new Date(now - minutes * 60 * 1000).toISOString();

// 来源:只用本地已有品牌图标(public/source-logos)的 owner,其余落字母徽标——不请求任何外部图标服务
export const SOURCES = [
  { source_id: 'hf_blog', name: 'Hugging Face 博客', source_owner: 'huggingface', role: 'official', shape: 'article' },
  { source_id: 'google_research_blog', name: 'Google Research', source_owner: 'google', role: 'official', shape: 'article' },
  { source_id: 'tinyserve_releases', name: 'tinyserve 版本发布', source_owner: 'tinyserve', role: 'official', shape: 'article' },
  { source_id: 'qbitai', name: '量子位', source_owner: 'qbitai', role: 'media', shape: 'article' },
  { source_id: 'tomrivera_blog', name: 'Tom Rivera', source_owner: 'tomrivera', role: 'personal', shape: 'article' },
  { source_id: 'web_huggingface_daily_papers', name: 'HF 每日论文', source_owner: 'huggingface', role: 'leaderboard', shape: 'article' },
  { source_id: 'hn_front', name: 'Hacker News', source_owner: 'ycombinator', role: 'leaderboard', shape: 'article' },
  { source_id: 'inference_hour', name: 'The Inference Hour', source_owner: 'inferencehour', role: 'media', shape: 'podcast' },
  { source_id: 'applied_ai_weekly', name: 'Applied AI Weekly', source_owner: 'appliedaiweekly', role: 'media', shape: 'podcast' },
  { source_id: 'x_mirachen', name: 'Mira Chen', source_owner: 'mirachen', role: 'personal', shape: 'social', platform: 'x' },
  { source_id: 'x_linzhou', name: '林舟', source_owner: 'linzhou', role: 'personal', shape: 'social', platform: 'x' },
  { source_id: 'x_devpatel', name: 'Dev Patel', source_owner: 'devpatel', role: 'personal', shape: 'social', platform: 'x' },
];

export const SOURCE_MAP = Object.fromEntries(SOURCES.map((source) => [source.source_id, source]));
export const SOURCE_NAME_MAP = Object.fromEntries(SOURCES.map((source) => [source.source_id, source.name]));

export const ROLE_GROUPS = [
  { key: 'official', label: '官方' },
  { key: 'media', label: '媒体' },
  { key: 'personal', label: '个人' },
  { key: 'leaderboard', label: '榜单' },
];

const tag = (id, name) => ({ type: 'canonical', id, name_zh: name });
const flexTag = (label) => ({ type: 'extracted', label });

// title_state:ready = 已有有效中文译名(随列表 / 详情一起返回);pending = 后台标题待办尚未完成;
// not_needed = 中文原题或版本号这类无需翻译的标识。title_zh 只在 ready 时生效。
export const ARTICLES = [
  {
    id: 'a-proxy',
    source_id: 'tomrivera_blog',
    title: 'Running local models behind a reverse proxy: notes from a weekend',
    title_zh: '周末笔记：把本地模型放到反向代理后面',
    title_state: 'pending',
    publish_date: ago(12),
    analysis_status: 'running',
    content_preview: 'I wanted the local models on my laptop to be reachable from my phone without opening a port to the whole internet.',
    read_count: 3,
    read_minutes: 2,
    body: bilingual(proxyMd),
  },
  {
    id: 'a-spec',
    source_id: 'hf_blog',
    title: 'Speculative Decoding in Practice: What Actually Speeds Up Inference',
    title_zh: '推测解码实战：究竟是什么让推理变快',
    title_state: 'ready',
    kind: 'tutorial',
    kind_label: '教程',
    publish_date: ago(35),
    quality_score: 8.4,
    score_reason: '给出可复现的配置与分并发档的实测数据，对部署选型有直接参考价值。',
    summary_zh: '文章拆解推测解码在真实服务中的收益来源：接受率决定上限，批量越大收益越小；附 vLLM 配置示例与单卡分并发档的延迟对比，建议先在低并发的交互场景启用。',
    display_tags: [tag(101, '推理优化'), tag(102, '开源模型'), flexTag('推测解码')],
    read_count: 128,
    read_minutes: 4,
    body: bilingual(speculativeMd),
  },
  {
    id: 'a-paper',
    source_id: 'web_huggingface_daily_papers',
    title: 'Block-Sparse Routing for Long-Context Attention',
    title_zh: '面向长上下文注意力的分块稀疏路由',
    title_state: 'ready',
    kind: 'paper',
    kind_label: '论文',
    extensions_json: JSON.stringify({ paper: { content_level: 'fulltext' } }),
    publish_date: ago(130),
    quality_score: 7.6,
    score_reason: '方法简单、开销可控，长上下文质量损失小；仍需更多任务验证。',
    summary_zh: '提出按块路由的稀疏注意力：每个查询块只与少量键块交互，计算量从平方级降到约 n√n；在 128K 上下文的检索与问答任务上，与稠密注意力的差距在 1 个百分点以内。',
    display_tags: [tag(103, '长上下文'), tag(104, '注意力机制'), flexTag('稀疏注意力')],
    read_count: 57,
    read_minutes: 5,
    body: bilingual(paperMd),
  },
  {
    id: 'a-qbit',
    source_id: 'qbitai',
    title: '长视频理解有了新考卷：十类任务难住多数多模态模型',
    title_state: 'not_needed',
    event_label: '研究进展',
    publish_date: ago(190),
    quality_score: 6.9,
    score_reason: '新基准覆盖面较全，但缺少与已有评测的系统对比。',
    summary_zh: '新的长视频评测基准覆盖十类任务、平均时长超 40 分钟；多数多模态模型在跨片段推理上得分不到一半，瓶颈在长时记忆。',
    display_tags: [tag(105, '多模态'), tag(106, '评测基准')],
    read_count: 342,
    read_minutes: 1,
    body: chineseOnly(qbitaiMd),
  },
  {
    id: 'a-release',
    source_id: 'tinyserve_releases',
    title: 'tinyserve v0.9.0',
    title_state: 'not_needed',
    kind: 'release',
    kind_label: '版本更新',
    publish_date: ago(300),
    read_count: 21,
    read_minutes: 1,
    body: bilingual(releaseMd),
  },
  {
    id: 'a-longctx',
    source_id: 'google_research_blog',
    title: 'A practical guide to evaluating long-context retrieval',
    title_zh: '长上下文检索评测实用指南',
    title_state: 'ready',
    kind: 'tutorial',
    kind_label: '教程',
    publish_date: ago(60 * 26),
    quality_score: 6.8,
    score_reason: '方法论清晰，数据来自作者自测，可作为评测设计的检查清单。',
    summary_zh: '只看“大海捞针”分数会高估长上下文能力；建议同时报告检索、聚合、弃答三项，并按证据间距分桶。',
    display_tags: [tag(103, '长上下文'), tag(106, '评测基准')],
    read_count: 410,
    read_minutes: 2,
    body: bilingual(longctxMd),
  },
  {
    id: 'a-sqlite',
    source_id: 'hn_front',
    title: 'Show HN: A 300-line SQLite extension for vector search',
    title_zh: 'Show HN：300 行代码的 SQLite 向量检索扩展',
    title_state: 'ready',
    publish_date: ago(60 * 30),
    quality_score: 5.4,
    score_reason: '个人小工具，思路清楚，但适用范围有限。',
    summary_zh: '作者用约 300 行 C 实现了 SQLite 向量距离函数与暴力 top-k 扫描，适合原型与个人工具，不替代专门的向量索引。',
    display_tags: [tag(107, '向量检索')],
    read_count: 88,
    read_minutes: 1,
    body: bilingual(sqliteMd),
  },
];

export const PODCASTS = [
  {
    id: 'p-stack',
    source_id: 'inference_hour',
    content_type: 'podcast_episode',
    title: 'The inference stack in 2026: caching, batching and what comes next',
    title_zh: '2026 年的推理栈：缓存、批处理与下一步',
    title_state: 'ready',
    publish_date: ago(95),
    quality_score: 7.9,
    score_reason: '两位一线工程师逐项拆解延迟来源，信息密度高。',
    summary_zh: '两位推理集群工程师从尾延迟谈起，依次讨论前缀缓存、连续批处理的失效场景和推测解码的适用边界。',
    podcast: { show_title: 'The Inference Hour', duration_seconds: 4320 },
    read_count: 64,
    read_minutes: 1,
    body: bilingual(podcastStackMd),
  },
  {
    id: 'p-agents',
    source_id: 'applied_ai_weekly',
    content_type: 'podcast_episode',
    title: 'Evaluating agents without fooling yourself',
    title_zh: '评估智能体时，如何不自欺欺人',
    title_state: 'ready',
    publish_date: ago(60 * 28),
    quality_score: 7.1,
    score_reason: '讨论聚焦评测方法，实践建议具体。',
    summary_zh: '聊智能体评测里的过拟合陷阱：留出任务集、记录每次工具调用，以及如何区分偶然的好结果与真实改进。',
    podcast: { show_title: 'Applied AI Weekly', duration_seconds: 2760 },
    read_count: 19,
    read_minutes: 1,
    body: bilingual(podcastAgentsMd),
  },
];

const social = (id, sourceId, minutes, content, ext, zh) => ({
  id,
  source_id: sourceId,
  content_type: 'social_post',
  title: content.slice(0, 60),
  content,
  publish_date: ago(minutes),
  extensions_json: JSON.stringify({ platform: 'x', ...ext }),
  r2_translation: zh,
});

// 社交卡:没有独立标题区,正文与引用推照旧逐卡手动翻译(r2_translation 是样页里的模拟译文)
export const SOCIAL_POSTS = [
  social(
    's-mira',
    'x_mirachen',
    48,
    'Spent the week measuring speculative decoding on real traffic. Acceptance rate is everything: 0.8 gave us 2x, 0.5 gave us almost nothing. Measure on your own prompts before you trust anyone\'s numbers.',
    { author_name: 'Mira Chen', author_handle: 'mirachen_ml', lang: 'en' },
    { text: '这周在真实流量上测了推测解码。接受率决定一切：0.8 带来 2 倍加速，0.5 几乎没有收益。先用你自己的提示词测一测，再相信别人的数字。' },
  ),
  social(
    's-lin',
    'x_linzhou',
    150,
    '长上下文评测别只看“大海捞针”。我们内部现在固定报三项：检索、聚合、弃答，并且按证据之间的距离分桶报告。',
    { author_name: '林舟', author_handle: 'linzhou_ai', lang: 'zh' },
    null,
  ),
  social(
    's-dev',
    'x_devpatel',
    240,
    'This matches what we saw. Prefix caching was the biggest single win for chat workloads, bigger than any kernel tweak.',
    {
      author_name: 'Dev Patel',
      author_handle: 'devpatel',
      lang: 'en',
      quoted: {
        author_name: 'Mira Chen',
        author_handle: 'mirachen_ml',
        lang: 'en',
        text: 'What was the single biggest latency win in your serving stack this year?',
      },
    },
    {
      text: '和我们看到的一致。对聊天类负载来说，前缀缓存是最大的单项收益，比任何算子优化都大。',
      quoted: '今年你们的推理服务里，延迟方面最大的单项收益来自哪里？',
    },
  ),
];

export const CONTAINER_ITEMS = { article: ARTICLES, podcast: PODCASTS, social: SOCIAL_POSTS };
export const ALL_READABLE = [...ARTICLES, ...PODCASTS];
export const READABLE_MAP = Object.fromEntries(ALL_READABLE.map((item) => [item.id, item]));

// 初始未读:新近三条未读,其余已读
export const INITIAL_UNREAD = ['a-proxy', 'a-spec', 'a-paper', 'a-qbit', 'p-stack', 's-mira', 's-dev'];

// 初始任务(模拟服务端持久任务):论文有一个上次中断的暂停任务,第 1、3 片已完成——中文视图只显示第 1 片,
// 第 3 片缓存待接上;点「继续翻译」后只补第 2 片,补齐即 2、3 一起出现。
// 检索指南有一份完整译文缓存(切到中文直接是全文)。
export const INITIAL_JOBS = {
  'a-paper': { status: 'paused', reason: 'interrupted', done: [0, 2], total: 3, live: true, failNext: false },
  'a-longctx': { status: 'succeeded', reason: null, done: [0, 1], total: 2, live: false, failNext: false },
};

// 样页默认落在这一篇(桌面列表 + 详情;手机直接是正文页,返回即列表)
export const DEFAULT_ARTICLE_ID = 'a-spec';
