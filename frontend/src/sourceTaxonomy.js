/**
 * 来源谱系 (Source Taxonomy)
 *
 * 单一事实来源：为每个来源提供①品牌标识（真实 favicon logo + 字母徽标兜底 + 主题色）
 * 与②唯一的分类轴——「信息角色」（官方 / 媒体 / 个人 / 榜单，见文件末尾 SOURCE_ROLES）。
 *
 * 分类轴口径（2026-07-29 统一）：全站只有角色这一根轴。此前管理面另用「公司 → 板块」
 * 两级谱系分组，但板块表只登记了 17 家公司，现役 44 个 owner 里 27 家掉进「其他来源」
 * 兜底，占了半个目录；同一家的节点还会因 owner 写法不同而分家（Moonshot 的 Kimi
 * Research 与 X · Moonshot 落「其他」，X · OpenAI 却在「厂商官方」）。故板块轴退役，
 * 管理面与阅读器源栏、发现页共用同一套角色词汇，公司降为组内排序键。
 *
 * 采集侧界面（节点管理 / 知识台账 / 任务与运行）与读者侧共用本模块。
 * 品牌标识组件见 components/LogoMark.jsx。
 */

import { avatarHue, avatarInitial } from './utils/avatarColor';

/* ──────────────────────────────────────────────────────────
 * 1. owner 归一化：把同义/同司的 source_owner 收敛为单一 company key
 * ────────────────────────────────────────────────────────── */
const OWNER_ALIASES = {
  'deepseek-ai': 'deepseek',
  deepseek: 'deepseek',
  alibaba: 'alibaba',
  alibaba_cloud: 'alibaba',
  qwenlm: 'alibaba',
};

export const CUSTOM_COMPANY_KEY = '__custom__';

export function normalizeOwner(owner) {
  const key = String(owner || '').trim().toLowerCase();
  if (!key) return CUSTOM_COMPANY_KEY;
  return OWNER_ALIASES[key] || key;
}

/* ──────────────────────────────────────────────────────────
 * 2. 公司品牌注册表
 * ────────────────────────────────────────────────────────── */
export const COMPANY_REGISTRY = {
  openai: { name: 'OpenAI', en: 'OpenAI', accent: '#10A37F', domain: 'openai.com', monogram: 'AI', logoPath: '/source-logos/openai.png' },
  anthropic: { name: 'Anthropic', en: 'Claude', accent: '#CC785C', domain: 'anthropic.com', monogram: 'A', logoPath: '/source-logos/anthropic.png' },
  google: { name: 'Google', en: 'Gemini / Gemma', accent: '#1A73E8', domain: 'google.com', monogram: 'G', logoPath: '/source-logos/google.png' },
  xai: { name: 'xAI', en: 'Grok', accent: '#0B0B0F', domain: 'x.ai', monogram: 'xAI', logoPath: '/source-logos/xai.png' },
  deepseek: { name: 'DeepSeek', en: 'DeepSeek', accent: '#4D6BFE', domain: 'deepseek.com', monogram: 'DS', logoPath: '/source-logos/deepseek.png' },
  alibaba: { name: 'Alibaba', en: 'Qwen', accent: '#615CED', domain: 'qwen.ai', monogram: 'Q', logoPath: '/source-logos/qwen.png' },
  zai: { name: '智谱 AI', en: 'Z.ai / GLM', accent: '#3859FF', domain: 'z.ai', monogram: 'Z', logoPath: '/source-logos/zai.png' },
  bytedance_seed: { name: 'ByteDance', en: 'Seed', accent: '#1664FF', domain: 'seed.bytedance.com', monogram: 'BD', logoPath: '/source-logos/bytedance-seed.png' },
  cursor: { name: 'Cursor', en: 'Cursor', accent: '#0F172A', domain: 'cursor.com', monogram: 'C', logoPath: '/source-logos/cursor.png' },
  opencode: { name: 'OpenCode', en: 'OpenCode', accent: '#0EA5E9', domain: 'opencode.ai', monogram: 'OC', logoPath: '/source-logos/opencode.png' },
  openclaw: { name: 'OpenClaw', en: 'OpenClaw', accent: '#F59E0B', domain: '', monogram: 'CL', mark: 'openclaw' },
  nousresearch: { name: 'Nous Research', en: 'Hermes', accent: '#7C3AED', domain: 'nousresearch.com', monogram: 'N', logoPath: '/source-logos/nousresearch.png' },
  huggingface: { name: 'Hugging Face', en: 'Hub', accent: '#FF9D00', domain: 'huggingface.co', monogram: 'HF', logoPath: '/source-logos/huggingface.png' },
  qbitai: { name: '量子位', en: 'QbitAI', accent: '#E8392E', domain: 'qbitai.com', monogram: '量', logoPath: '/source-logos/qbitai.png' },
  ycombinator: { name: 'Hacker News', en: 'Y Combinator', accent: '#FF6600', domain: 'news.ycombinator.com', monogram: 'Y', logoPath: '/source-logos/ycombinator.png' },
  dorami: { name: '哆啦美', en: 'Dorami', accent: '#5B54E8', domain: '', monogram: '美', mark: 'dorami' },
  [CUSTOM_COMPANY_KEY]: { name: '通用能力节点', en: 'Custom', accent: '#64748B', domain: '', monogram: '⚙' },
};

/* ──────────────────────────────────────────────────────────
 * 3. 解析单个 fetcher 的公司身份
 * ────────────────────────────────────────────────────────── */
export function resolveCompany(fetcher) {
  // 用户自定源(v3.40):无策展 owner,按源名派生个性标识——feed 站点 favicon 优先
  // (domain 走 LogoMark 的 favicon 服务链),失败回退「首字母 + 名字稳定色相」
  // (avatarHue 黄金角散列,同名恒同色;与账户默认字母头像 v3.22.3 同一套语义)。
  if (fetcher?.user_source || String(fetcher?.source_id || '').startsWith('user_rss_')) {
    const name = fetcher?.name || fetcher?.source_id || '?';
    return {
      key: fetcher?.source_id || 'user',
      name,
      accent: `hsl(${avatarHue(name)} 52% 46%)`,
      domain: fetcher?.base_url || '',
      monogram: avatarInitial(name),
    };
  }
  const key = normalizeOwner(fetcher?.source_owner);
  const base = COMPANY_REGISTRY[key];
  if (base) return { key, ...base };
  // 未登记的 owner：用 owner 文本兜底，保留可视化能力
  const label = key.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return { key, name: label, en: label, accent: '#64748b', domain: fetcher?.base_url || '', monogram: label.slice(0, 2).toUpperCase() };
}

export function companyLogoUrl(company) {
  const domain = company?.domain || '';
  if (!domain) return '';
  const host = domain.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=128`;
}

/* ──────────────────────────────────────────────────────────
 * 4. 把（已过滤的）fetcher 列表按信息角色分组
 *
 * 唯一分类轴 = 角色（官方/媒体/个人/榜单，定义见末尾 SOURCE_ROLES）；节点在组内平铺。
 * 组内排序键是「公司标识 → 节点名」而非节点名——同一家的节点（Anthropic 新闻 /
 * Claude 博客 / Claude Code Changelog）借此天然相邻，30+ 个节点的「官方」组仍可扫读。
 * 用 company.key（归一化后的 owner，恒为拉丁标识）而非 company.name 作排序键：中英混排的
 * 显示名在 zh-Hans-CN 排序下会把中文名公司整体浮到组首（智谱/量子位），反而显得没有章法。
 * ────────────────────────────────────────────────────────── */
export function groupByRole(fetchers) {
  const byRole = new Map();
  fetchers.forEach(fetcher => {
    const key = sourceRoleOf(fetcher);
    if (!byRole.has(key)) byRole.set(key, []);
    byRole.get(key).push(fetcher);
  });

  return SOURCE_ROLES
    .filter(role => byRole.has(role.key))
    .map(role => ({
      id: role.key,
      label: role.label,
      accent: role.accent,
      blurb: role.blurb,
      fetchers: byRole.get(role.key).sort((a, b) => (
        resolveCompany(a).key.localeCompare(resolveCompany(b).key)
        || a.name.localeCompare(b.name, 'zh-Hans-CN')
      )),
    }));
}

/* ──────────────────────────────────────────────────────────
 * 6. 维度标签
 * ────────────────────────────────────────────────────────── */
export const TIER_META = {
  tier0_primary: { label: '官方一手', short: 'T0', tone: 'emerald' },
  tier1_curated: { label: '聚合筛选', short: 'T1', tone: 'sky' },
  tier2_commentary: { label: '评论观点', short: 'T2', tone: 'violet' },
};

export function tierMeta(tier) {
  return TIER_META[tier] || { label: '未分级', short: '—', tone: 'slate' };
}

export const SOURCE_SCOPE_LABELS = {
  company: '公司级',
  model_family: '模型族',
  open_model_family: '开放模型族',
  product_family: '产品族',
  api_platform: 'API 平台',
  developer_tool: '开发工具',
  research_lab: '研究团队',
  ai_media: 'AI 媒体',
  tech_media: '科技媒体',
  personal_commentary: '个人评论',
  expert_commentary: '专家评论',
  executive_commentary: '高管评论',
  expert_newsletter: '专家通讯',
  developer_community: '开发者社区',
  research_community: '研究社区',
};

export const SOURCE_CHANNEL_LABELS = {
  newsroom: '新闻页',
  newsroom_rss: '新闻 RSS',
  blog: '博客',
  blog_api: '博客 API',
  blog_category: '博客分类',
  changelog: 'Changelog',
  docs_changelog: '文档变更',
  docs_release_notes: 'Release Notes',
  docs_reference: '参考文档',
  docs_index: '文档索引',
  support_release_notes: '帮助中心',
  github_release: 'GitHub Release',
  github_repository_activity: 'GitHub 仓库',
  model_repository: '模型仓库',
  model_catalog: '模型目录',
  research_index: '研究目录',
  paper_ranking: '论文榜单',
  search_rss: '搜索 RSS',
  podcast_rss: 'Podcast RSS',
  website: '网站',
  website_or_feed: '网站/Feed',
  docs_console: '开放平台',
};

export const SIGNAL_LABELS = { high_signal: '高信号', medium_signal: '中信号', low_signal: '低信号' };
export const NOISE_LABELS = { low_noise: '低噪声', medium_noise: '中噪声', high_noise: '高噪声' };
export const RELIABILITY_LABELS = { stable_public: '稳定公开', fragile_js_api: 'JS/API 易变', blocked_or_fragile: '易阻断' };

export function labelFrom(map, value) {
  return map[value] || value || '';
}

/* 品牌标识尺寸表（供 LogoMark 组件使用） */
export const LOGO_SIZES = {
  /* s15/s17/s20:阅读器样页刻度(条目首行 15 / 阅读窗 crumb 17 / 源栏行 20) */
  s15: { box: 15, radius: 4.5, font: 7.5, img: 10 },
  s17: { box: 17, radius: 5.5, font: 8, img: 11 },
  s20: { box: 20, radius: 6, font: 9, img: 13 },
  /* s34:发现页源卡刻度 */
  s34: { box: 34, radius: 9, font: 13, img: 21 },
  xs: { box: 22, radius: 7, font: 9, img: 14 },
  sm: { box: 30, radius: 9, font: 11, img: 18 },
  md: { box: 44, radius: 13, font: 15, img: 26 },
  lg: { box: 56, radius: 16, font: 19, img: 34 },
};

/* ── 信息角色(单一分类轴,全站统一:阅读器源栏/发现页/管理面共用)──
   容器化之后,形态(文章/动态/社交)已由左侧视图轨容器 + 发现页过滤条承担,
   源栏组头不再重复形态,而是回答唯一一个问题:这个源是什么身份。四类角色:
   · 官方   —— 厂商/机构一手发布(tier0 company/model_family/product/developer_tool…)
   · 媒体   —— 有编辑的第三方报道(ai_media / tech_media)
   · 个人   —— 研究者/从业者/高管的个人视角(personal/expert/executive commentary、
              专家自营 newsletter、tier2)
   · 榜单   —— 无编辑立场的聚合排序/评测信号(HN、reddit、GitHub 趋势、HF 每日论文,
              以及 Arena / Artificial Analysis 这类基准评测榜)
   判定顺序 个人 → 榜单 → 媒体 → 官方(先具体后兜底)。
   accent 供分组标记条取色(管理面 board-group-marker);tone 供角色胶囊配色。 */
export const SOURCE_ROLES = [
  { key: 'official', label: '官方', tone: 'emerald', accent: '#059669', blurb: '厂商与研究机构的一手发布' },
  { key: 'media', label: '媒体', tone: 'sky', accent: '#0284c7', blurb: '有编辑立场的第三方报道' },
  { key: 'personal', label: '个人', tone: 'violet', accent: '#7c3aed', blurb: '研究者与从业者的个人视角' },
  { key: 'leaderboard', label: '榜单', tone: 'amber', accent: '#d97706', blurb: '社区热度与基准评测的聚合排序' },
  /* 自定源(v3.46):读者自助添加、未列入公共目录的 RSS 源——不属于策展角色轴,单列一组,
     防止无策展元数据的用户源回落进「官方」稀释组语义;仅订阅者的目录里出现。
     后端 source_role 无需镜像本档:用户源被日报机械排除,权威层吃不到。 */
  { key: 'custom', label: '自定源', tone: 'slate', accent: '#64748b', blurb: '你自己添加、目录未收录的来源' },
];

const ROLE_LABEL = Object.fromEntries(SOURCE_ROLES.map((r) => [r.key, r.label]));
const ROLE_TONE = Object.fromEntries(SOURCE_ROLES.map((r) => [r.key, r.tone]));

// ⚠️ 三个 scope 集合与 sourceRoleOf 的判定序在后端有镜像
// (src/services/source_naming.py source_role,v3.35 日报权威机械层),改一处必须同步另一处。
// expert_newsletter:专家自营 newsletter(Import AI = Jack Clark 个人执笔),
// 此前因 tier1_curated 被判成「媒体」,但它没有编辑部,归「个人」才对。
const PERSONAL_SCOPES = new Set([
  'personal_commentary', 'expert_commentary', 'executive_commentary', 'expert_newsletter',
]);
// 基准评测榜(Arena 排行榜更新 / Artificial Analysis):此前 Arena 因 tier0 被判「官方」、
// AA 因 tier1 被判「媒体」——两个同类源分居两组。它们产出的是排序信号,归「榜单」。
const LEADERBOARD_SCOPES = new Set([
  'community', 'developer_community', 'research_community', 'forum',
  'ai_benchmark_platform', 'ai_benchmark_analysis',
]);
const MEDIA_SCOPES = new Set(['ai_media', 'tech_media']);

/* 源 → 信息角色 key。仅看策展元数据(provenance_tier / source_scope),不看形态。
   用户自定源(user_rss_ 前缀/user_source 标记)最先短路进「自定」组——它们没有策展
   元数据,不判定会全部回落「官方」。 */
export const sourceRoleOf = (source) => {
  if (source?.user_source || String(source?.source_id || '').startsWith('user_rss_')) return 'custom';
  const scope = source?.source_scope || '';
  if (source?.provenance_tier === 'tier2_personal_social' || PERSONAL_SCOPES.has(scope)) return 'personal';
  if (LEADERBOARD_SCOPES.has(scope)) return 'leaderboard';
  if (MEDIA_SCOPES.has(scope) || source?.provenance_tier === 'tier1_curated') return 'media';
  return 'official';
};

export const roleLabelOf = (source) => ROLE_LABEL[sourceRoleOf(source)] || '官方';
export const roleToneOf = (source) => ROLE_TONE[sourceRoleOf(source)] || 'slate';

/* ── 社交平台标签(shape=social 的源)──
   平台是「源」的属性、不是每条内容的属性:社交卡片角标只在订阅了 >=2 个平台时才挂、
   发现页源卡标平台。源栏组头已回归纯角色,不再带平台前缀。 */
const PLATFORM_LABELS = { x: 'X', mastodon: 'Mastodon', bluesky: 'Bluesky' };

export const platformLabelOf = (platform) => PLATFORM_LABELS[platform] || platform || '社交';
