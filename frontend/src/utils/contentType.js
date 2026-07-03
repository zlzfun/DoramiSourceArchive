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
