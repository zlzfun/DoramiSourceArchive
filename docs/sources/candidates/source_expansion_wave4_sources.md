# Source Expansion Wave 4 Candidates

> 2026-07-26 横向调研与用户拍板记录。7 个源均已进入统一开发批次，初始状态
> 为 `incubating`；本档保存准入依据、实测通路和观察期风险。

# Recommended Review Sources

## Source: Microsoft AI 模型

- status: `implemented_incubating`
- source_owner: `microsoft`
- source_brand: `Microsoft AI`
- source_scope: `ai_lab`
- source_channel: `models_rss`
- source_url: `https://microsoft.ai/news-categories/models/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_rss_custom_user_agent`

### Target Coverage

Microsoft AI 自研 MAI 模型的发布、能力评测、模型卡与平台可用性。

### Inclusion Reasons

现有目录没有 Microsoft AI 的第一方模型发布面；Models 分类比 Microsoft 全站新闻更窄，
能补齐 MAI 模型线而不引入 Copilot/公司新闻大盘。

### Risks / Open Questions

Cloudflare 会拦截浏览器形态 UA；官方 WordPress RSS reader UA 可稳定返回全文。观察期需留意
规则变化以及 WordPress 正文 `.wysiwyg` 容器改版。

### Known Overlap

模型发布可能被 Arena、Artificial Analysis 与 Tier1 媒体二次报道；本源作为第一方事实锚点。

### Validation Notes

2026-07-26 live probe: feed 返回 10 条、真实发布日期和 `content:encoded` 全文；浏览器 UA
得到 Cloudflare block page，`WordPress/6.0; https://microsoft.ai` 得到真实 RSS。正文由全部
`.wysiwyg` 块拼接，实测排除标题/分享、招聘、Related Stories 与 `appeared first` 尾注。

## Source: Artificial Analysis

- status: `implemented_incubating`
- source_owner: `artificial_analysis`
- source_brand: `Artificial Analysis`
- source_scope: `ai_benchmark_analysis`
- source_channel: `articles`
- source_url: `https://artificialanalysis.ai/articles`
- provenance_tier: `tier1_curated`
- content_tags: `benchmark`, `model_release`, `market_news`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_website`

### Target Coverage

前沿模型的智能、成本、速度与 Agent 工作基准，以及新模型的横向比较。

### Inclusion Reasons

它提供厂商自述之外的统一实测坐标，能直接回答“新模型实际处于什么位置”；Articles 栏目
颗粒度清晰、发布日期稳定，且不是高频榜单快照。

### Risks / Open Questions

正文依赖 Tailwind `.prose` 容器，站点重构时需复验；基准口径属于平台方法论，不等同于
普遍能力结论。

### Known Overlap

与官方模型发布、Arena 排行榜更新重叠事件，但增量是独立测量、成本和速度解释。

### Validation Notes

2026-07-26 live probe: 列表 12 条/页并有标准分页，日期倒序；两篇详情均能从
`.prose.prose-sm.max-w-none` 取得结构化正文、图表和链接，重复 H1 已剔除。

## Source: Meta AI 博客

- status: `implemented_incubating`
- source_owner: `meta`
- source_brand: `Meta AI`
- source_scope: `ai_lab`
- source_channel: `official_blog`
- source_url: `https://ai.meta.com/blog/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `open_source`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_website_obfuscated_css`

### Target Coverage

Meta 的模型、开源研究、多模态、智能体和研究应用发布。

### Inclusion Reasons

Meta 是当前目录中的显著第一方缺口；2026-07-26 复验表明旧有“httpx 全灭”结论已经失效，
列表与详情均能直接 HTTP 获取，因此应重新准入。

### Risks / Open Questions

页面 CSS 类名经过混淆；正文选择器可能随前端部署变化。观察期重点看 selector 命中率、
重复卡片和分页。

### Known Overlap

与 Hugging Face、Arena、AI 媒体对 Llama/Muse/SAM 的二次报道重叠；第一方技术细节优先。

### Validation Notes

2026-07-26 live probe: 列表页与两篇详情均 HTTP 200；详情正文稳定位于 `._amgj`。适配器
排除作者/分享、Learn More、newsletter 订阅尾巴；卡片 overlay 链接按单卡父容器取标题日期。

## Source: Kimi Research

- status: `implemented_incubating`
- source_owner: `moonshot_ai`
- source_brand: `Kimi`
- source_scope: `model_family`
- source_channel: `research_blog`
- source_url: `https://www.kimi.com/blog/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `agent`, `multimodal`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_website`

### Target Coverage

Kimi 模型、Agent Swarm、多模态基准、训练方法与研究系统。

### Inclusion Reasons

国内前沿模型的一手技术博客；覆盖内容比单纯发布公告更深，且页面集中列出历史研究文章。

### Risks / Open Questions

列表存在顶部导航与首卡重复，日期采用 `YYYY/MM/DD`；正文是 Next.js/Tailwind 页面，需对
类名漂移保持监控。

### Known Overlap

与 Kimi X 账号、Artificial Analysis 和国内媒体重叠发布事件；博客提供完整技术正文。

### Validation Notes

2026-07-26 live probe: 列表含 Kimi K3、PerceptionBench 等并带真实日期；K3 与 K2.6
详情均由 `.blog-v2-main .markdown` 取得 19k+ 字符结构化正文，导航/标题/CTA 不入正文。

## Source: MiniMax Research

- status: `implemented_incubating`
- source_owner: `minimax`
- source_brand: `MiniMax`
- source_scope: `ai_lab`
- source_channel: `research_blog`
- source_url: `https://www.minimax.io/blog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `agent`, `multimodal`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_website`

### Target Coverage

MiniMax 模型、Agent、长上下文、强化学习、语音和多模态研究。

### Inclusion Reasons

补齐 MiniMax 第一方技术源；研究页颗粒度统一、摘要和日期完整，详情含基准与方法说明。

### Risks / Open Questions

正文依赖 `article .prose` 容器；部分文章包含大型表格/嵌入 HTML，需持续抽查 markdown 可读性。

### Known Overlap

与国内媒体、Arena 和模型发布新闻有事件重叠；本源保留技术细节与评测方法。

### Validation Notes

2026-07-26 live probe: 列表 10 条，日期倒序；MaxProof 与 M3 两篇详情分别取得约 32k/
24k 字符正文，标题、日期、标签、站点导航和页脚均被正文容器自然排除。

## Source: Import AI

- status: `implemented_incubating`
- source_owner: `jack_clark`
- source_brand: `Import AI`
- source_scope: `expert_newsletter`
- source_channel: `newsletter_rss`
- source_url: `https://jack-clark.net/feed/`
- provenance_tier: `tier1_curated`
- content_tags: `research_paper`, `market_news`, `opinion`, `ai_policy`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_rss`

### Target Coverage

Jack Clark 对 AI 研究、算力、政策与产业信号的周度深度综述。

### Inclusion Reasons

稳定、长寿且高度策展的专家通讯，为当前偏发布/产品的目录补充研究与政策脉络。

### Risks / Open Questions

单篇较长且带固定订阅前言；观点性内容应与第一方事实源区分，观察阅读完成度和日报权重。

### Known Overlap

会引用论文、官方发布和政策新闻；增量来自跨事件综合与专家判断。

### Validation Notes

2026-07-26 live probe: RSS 10 条、`content:encoded` 全文，最近两篇清洗后约 14.7k/15.3k
字符。保留标题层级/链接/列表，删除缩略图、固定欢迎/Subscribe 与 Thanks for reading 尾注。

## Source: Arena 排行榜更新

- status: `implemented_incubating`
- source_owner: `arena_intelligence`
- source_brand: `Arena`
- source_scope: `ai_benchmark_platform`
- source_channel: `leaderboard_changelog`
- source_url: `https://arena.ai/blog/leaderboard-changelog`
- provenance_tier: `tier0_primary`
- content_tags: `benchmark`, `model_release`, `leaderboard_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_ssr_page`

### Target Coverage

Arena 排行榜新增模型、分数重算、方法更新和榜单扩展。

### Inclusion Reasons

它是模型真实进入公开对战/榜单的早期结构化信号，且更新页面本身就是官方事实来源。

### Risks / Open Questions

单页持续增长；必须按日期卡片拆条，不能把整页当一篇文章。SSR DOM 或日期卡结构变化时
需复验，旧条目数量增长也需留意解析成本。

### Known Overlap

与厂商发布、Artificial Analysis 的模型评测重叠；增量是榜单上线和评分方法变化。

### Validation Notes

2026-07-26 live probe: 页面直接 SSR 出 178 个日期卡片；适配器逐卡生成 `bulletin`，保留
模型/榜单链接、真实日期与同日多条更新，排除页面简介、导航、footer 和订阅内容。

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Microsoft AI 全站 News | `https://microsoft.ai/news/` | Models 分类已覆盖本轮目标；全站范围更宽、重复和公司新闻更多。 |
| Arena 全站 Blog | `https://arena.ai/blog` | 本轮只收高结构化的 Leaderboard Changelog，避免活动/营销文章扩大噪声。 |
