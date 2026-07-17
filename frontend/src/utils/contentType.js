// 内容类型枚举 → 中文标签映射。
// content_type 是后端下发的数据形态枚举（models/content.py 的 ClassVar），
// 直接展示会泄漏内部术语（web_article/hf_model…）。此处收敛为面向人的中文标签，
// 台账类型胶囊、检索结果、文章详情、阅读器共用。未命中回落原值（对 admin 仍是有效信息）。
const CONTENT_TYPE_LABELS = {
  ai_community: 'AI 社区',
  ai_company_blog: '厂商博客',
  ai_tools: 'AI 工具',
  arxiv: '学术论文',
  daily_brief: '每日日报',
  github_release: '版本发布',
  github_repository: '代码仓库',
  github_trending: 'GitHub 趋势',
  hf_model: '模型',
  huggingface_model: '模型',
  rss_article: '资讯文章',
  social_post: '社交动态',
  tech_conference: '技术会议',
  web_article: '资讯文章',
  wechat_article: '公众号文章',
};

export function contentTypeLabel(type, fallback = '未知') {
  if (!type) return fallback;
  return CONTENT_TYPE_LABELS[type] || type;
}

// 内容类型归组 = 台账「内容类型」分面的产品分类学，本表是其单一事实来源。
// content_type 是后端的数据形态枚举（细粒度、含同义别名如 hf_model/huggingface_model）；
// 面向人的台账分面按「组」呈现：一组 = 一个中文分类名 + 该分类涵盖的 content_type 数组。
//   · 分面项 = 组（右侧计数 = 组内各 content_type 计数求和；count=0 的组不显示）。
//   · 点击组 → 以组内 content_type 的 CSV 走 GET /api/articles?content_types= 多类型筛选
//     （单类型组也走 CSV，统一一条路径，不再用单值 content_type）。
//   · 未列入任何组的 content_type 由消费方（DataTab）自动归入「其他」组。
// 顺序即分面展示顺序；改动分类学只改这里。
export const CONTENT_TYPE_GROUPS = [
  { label: '资讯文章', types: ['rss_article', 'web_article'] },
  { label: '公众号文章', types: ['wechat_article'] },
  { label: '学术论文', types: ['arxiv'] },
  { label: '版本发布', types: ['github_release'] },
  { label: '代码仓库', types: ['github_repository'] },
  { label: 'GitHub 趋势', types: ['github_trending'] },
  { label: '模型', types: ['hf_model', 'huggingface_model'] },
  { label: '社交动态', types: ['social_post'] },
  { label: '技术会议', types: ['tech_conference'] },
  { label: '厂商博客', types: ['ai_company_blog'] },
  { label: 'AI 社区', types: ['ai_community'] },
  { label: 'AI 工具', types: ['ai_tools'] },
];
