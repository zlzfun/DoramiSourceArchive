/**
 * 来源谱系 (Source Taxonomy)
 *
 * 单一事实来源：把后端返回的扁平 fetcher 元数据，归并为「公司 → 板块」两级谱系，
 * 并为每个公司提供品牌标识（真实 favicon logo + 字母徽标兜底 + 主题色）。
 *
 * 三个采集侧界面（节点管理 / 知识台账 / 任务与运行）共用本模块，保证视觉与分类一致。
 * 品牌标识组件见 components/LogoMark.jsx。
 */

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
  openai: { name: 'OpenAI', en: 'OpenAI', accent: '#10A37F', domain: 'openai.com', monogram: 'AI' },
  anthropic: { name: 'Anthropic', en: 'Claude', accent: '#CC785C', domain: '', monogram: 'A', mark: 'anthropic' },
  google: { name: 'Google', en: 'Gemini / Gemma', accent: '#1A73E8', domain: '', monogram: 'G', mark: 'google' },
  xai: { name: 'xAI', en: 'Grok', accent: '#0B0B0F', domain: 'x.ai', monogram: 'xAI' },
  deepseek: { name: 'DeepSeek', en: 'DeepSeek', accent: '#4D6BFE', domain: 'deepseek.com', monogram: 'DS' },
  alibaba: { name: 'Alibaba', en: 'Qwen', accent: '#615CED', domain: 'qwen.ai', monogram: 'Q' },
  zai: { name: '智谱 AI', en: 'Z.ai / GLM', accent: '#3859FF', domain: 'z.ai', monogram: 'Z' },
  bytedance_seed: { name: 'ByteDance', en: 'Seed', accent: '#1664FF', domain: 'seed.bytedance.com', monogram: 'BD' },
  cursor: { name: 'Cursor', en: 'Cursor', accent: '#0F172A', domain: 'cursor.com', monogram: 'C' },
  opencode: { name: 'OpenCode', en: 'OpenCode', accent: '#0EA5E9', domain: 'opencode.ai', monogram: 'OC' },
  openclaw: { name: 'OpenClaw', en: 'OpenClaw', accent: '#F59E0B', domain: '', monogram: 'CL', mark: 'openclaw' },
  nousresearch: { name: 'Nous Research', en: 'Hermes', accent: '#7C3AED', domain: 'nousresearch.com', monogram: 'N' },
  huggingface: { name: 'Hugging Face', en: 'Hub', accent: '#FF9D00', domain: 'huggingface.co', monogram: 'HF' },
  qbitai: { name: '量子位', en: 'QbitAI', accent: '#E8392E', domain: 'qbitai.com', monogram: '量' },
  ycombinator: { name: 'Hacker News', en: 'Y Combinator', accent: '#FF6600', domain: 'news.ycombinator.com', monogram: 'Y' },
  [CUSTOM_COMPANY_KEY]: { name: '通用能力节点', en: 'Custom', accent: '#64748B', domain: '', monogram: '⚙' },
};

/* ──────────────────────────────────────────────────────────
 * 3. 板块（section）：把公司聚成「站在用户视角」的内容阵营
 * ────────────────────────────────────────────────────────── */
export const SECTIONS = [
  {
    id: 'model_vendor',
    label: '厂商官方',
    en: 'Official Vendors',
    blurb: '国内外 AI 厂商的一手发布、模型更新、应用产品与平台动态',
    accent: '#4f46e5',
    companies: ['anthropic', 'google', 'openai', 'xai', 'alibaba', 'deepseek', 'bytedance_seed', 'zai'],
  },
  {
    id: 'agent',
    label: 'Agent 与编程工具',
    en: 'Agent & Coding',
    blurb: 'AI 编程、Agent 框架与开发者工具版本流',
    accent: '#7c3aed',
    companies: ['cursor', 'opencode', 'openclaw', 'nousresearch'],
  },
  {
    id: 'signal',
    label: '社区 · 媒体 · 论文信号',
    en: 'Community Signal',
    blurb: '聚合筛选层：热门论文、行业媒体与社区热度',
    accent: '#e8392e',
    companies: ['huggingface', 'qbitai', 'ycombinator'],
  },
  {
    id: 'custom',
    label: '通用能力节点',
    en: 'Custom Nodes',
    blurb: '通用 RSS / GitHub / HuggingFace 等可自配置能力',
    accent: '#64748b',
    companies: [CUSTOM_COMPANY_KEY],
  },
];

const SECTION_BY_COMPANY = (() => {
  const map = {};
  SECTIONS.forEach(section => section.companies.forEach(company => { map[company] = section.id; }));
  return map;
})();

const OTHER_SECTION = { id: 'other', label: '其他来源', en: 'Other', blurb: '尚未归类的来源', accent: '#94a3b8', companies: [] };

/* ──────────────────────────────────────────────────────────
 * 4. 解析单个 fetcher 的公司身份
 * ────────────────────────────────────────────────────────── */
export function resolveCompany(fetcher) {
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
 * 5. 把（已过滤的）fetcher 列表组织成 section → company → fetchers
 * ────────────────────────────────────────────────────────── */
export function groupBySection(fetchers) {
  const byCompany = new Map();
  fetchers.forEach(fetcher => {
    const company = resolveCompany(fetcher);
    if (!byCompany.has(company.key)) byCompany.set(company.key, { company, fetchers: [] });
    byCompany.get(company.key).fetchers.push(fetcher);
  });

  const sectionMap = new Map();
  const ensureSection = (def) => {
    if (!sectionMap.has(def.id)) sectionMap.set(def.id, { ...def, companies: [] });
    return sectionMap.get(def.id);
  };

  // 按 SECTIONS 的固定顺序优先填充
  SECTIONS.forEach(sectionDef => {
    sectionDef.companies.forEach(companyKey => {
      const bucket = byCompany.get(companyKey);
      if (bucket) {
        ensureSection(sectionDef).companies.push(bucket);
        byCompany.delete(companyKey);
      }
    });
  });

  // 剩余未归类的公司 → 其他
  byCompany.forEach(bucket => {
    const sectionId = SECTION_BY_COMPANY[bucket.company.key] || 'other';
    const def = SECTIONS.find(s => s.id === sectionId) || OTHER_SECTION;
    ensureSection(def).companies.push(bucket);
  });

  // 输出按 SECTIONS 顺序，其他垫底
  const ordered = [];
  SECTIONS.forEach(def => { if (sectionMap.has(def.id)) ordered.push(sectionMap.get(def.id)); });
  if (sectionMap.has('other')) ordered.push(sectionMap.get('other'));
  // 每个 section 内公司保持 SECTIONS 中人工策展顺序；只排序未登记的剩余公司。
  ordered.forEach(section => {
    section.companies.forEach(bucket => bucket.fetchers.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN')));
  });
  return ordered;
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
  xs: { box: 22, radius: 7, font: 9, img: 14 },
  sm: { box: 30, radius: 9, font: 11, img: 18 },
  md: { box: 44, radius: 13, font: 15, img: 26 },
  lg: { box: 56, radius: 16, font: 19, img: 34 },
};
