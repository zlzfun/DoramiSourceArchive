# Podcast 专栏与「长播客精华」Wave 设计

> 对应需求：[Issue #7](https://github.com/zlzfun/DoramiSourceArchive/issues/7)
> 文档状态：Proposed，待产品与版权策略确认
> 调研日期：2026-09-02（Asia/Shanghai）
> 适用仓库：DoramiSourceArchive（FastAPI + SQLModel + Alembic + React/Vite）

> 2026-09-03 更新：Issue #7 的可执行规格、技术计划、数据模型、API 契约、多 Agent 任务和
> 主会话验收手册已收敛到 [`specs/007-podcast-intelligence/`](../specs/007-podcast-intelligence/)。
> 本文保留完整竞品与方案背景；后续实施优先级以 SpecKit 产物为准：P1 源/单集准入，P2
> 领域模型与摘要，P3 源语言 ASR/完整中文转录/中文精华，P4 单旁白 `<=15min` TTS。
> 2026-09-03 的语音平台价格、能力和正式选型以
> [`voice-stack-decision.md`](../specs/007-podcast-intelligence/voice-stack-decision.md) 为准。

## 1. 结论先行

建议把这项能力拆成两个相互关联、但边界清晰的产品：

1. **Podcast 专栏**：订阅与发现优质播客，保留原节目、原音频、原 show notes、章节和官方逐字稿，提供连续播放和时间戳导航。
2. **长播客精华**：仅对 `duration > 30 分钟`、质量合格且拥有衍生使用权限的单集，产出保留源说话人/时间码映射的完整中文转录和带原文引用的中文精华博客，再用一个固定中文旁白合成目标 12–14 分钟、硬上限 15 分钟的精华音频。

首版不应把“超过 30 分钟”直接等同于“自动转制”。真正的门控条件应是：

```text
超过 30 分钟
  ∧ 质量/相关性达标
  ∧ 来源与音频可稳定访问
  ∧ 权利策略允许转录
  ∧ 权利策略允许生成并展示衍生文字
  ∧ 权利策略允许生成并分发衍生音频
```

高性价比实施路径：

- M0/M1 优先使用源站提供的 `podcast:transcript`，没有才调用托管 ASR；先做文字精华，TTS 放在同一流水线的可选末端。
- ASR 默认采用低价托管模型以减少运维：国内首测阿里 Qwen-Audio 3.0，全球中英混合首测 AssemblyAI Universal-3.5 Pro + diarization，并以 OpenAI、自托管 faster-whisper/WhisperX 和 SenseVoice/FunASR作质量基准；达到稳定规模后再决定自托管。
- 摘要沿用项目现有 OpenAI-compatible LLM 配置，但新增“带时间戳证据的结构化脚本”协议，不能直接复用普通文章摘要作为播音稿。
- TTS 首版使用一个固定平台中文音色，按语义段落合成，禁止克隆主持人声音；全球首测 Azure Neural，国内并行盲测腾讯、阿里与自托管 CosyVoice。
- 原始音频默认由客户端读取发布者 enclosure，不做永久镜像；ASR 临时下载有短保留期，生成音频进入对象存储。这样同时降低带宽、存储和版权风险。
- Podcast Index 用于开放播客发现和 RSS 定位，直接 RSS 永远是入库后的事实源；Listen Notes 只作为可插拔的商业备选。

目前没有查到已核实竞品把“任意外部长播客 RSS → 完整转录 → 独立中文博客 → ≤15 分钟合成音频 → 新 RSS”完整串起来。最接近的是 Snipd 的原声精华、Podwise 的结构化知识、NotebookLM 的生成式 Audio Overview，以及 BestBlogs 曾上线但当前状态存在文档冲突的音频简报。Dorami 的差异化机会是：**可审计的时间戳引用、明确授权、博客与音频双产物、成本可控的异步流水线**。

## 2. 背景、范围与术语

Issue #7 的标题要求“新增播客专栏，收录优质播客；将外部优质播客转入为内部播客，精简成文字博客，同时提供精简版中文博客”。本轮进一步明确：超过 30 分钟的播客期望转录、提炼为博客，再生成不超过 15 分钟的音频。

为避免“内部播客”被误解为取得内容所有权，本文使用以下术语：

- **原节目 / original**：发布者 RSS 中的节目与音频，Dorami 只是索引、播放入口和阅读工具。
- **精华博客 / digest blog**：Dorami 基于原内容生成的中文衍生文本，必须附 AI 标识、来源、原单集链接和时间戳证据。
- **精华音频 / digest audio**：由精华博客的播音稿合成的 AI 音频，不冒充主持人，也不混用原主持人音色。
- **内部源 / internal curated source**：由团队从内网清单导入并治理的优质源；“内部”描述管理方式，不改变版权归属。
- **发布 / publish**：对目标受众可见。内网可见、登录可见和公开互联网可见是三个不同的授权层级。

### 2.1 本 Wave 范围

- 直接 RSS/Podcasting 2.0 播客源接入、校验、去重、更新与健康监控。
- 播客发现页、节目页、单集页、持久播放器、订阅与播放进度。
- 官方逐字稿优先；无逐字稿时的 ASR、章节、精华博客、中文播音稿与 TTS。
- 对内网导入的优质博客 RSS 与播客 RSS 分开治理。
- 权利门控、审核、下架、版本和成本追踪。
- 兼容现有文章检索、订阅、AI 分析、Job 与 Archive Sync 的演进方案。

### 2.2 首版非目标

- 不做 Spotify/Apple Podcasts/小宇宙页面抓取，不绕过登录、DRM、付费墙或私有 feed 认证。
- 不提供原主持人的声音克隆，不把合成音频伪装成原节目。
- 不默认永久缓存或重新托管原始音频。
- 不承诺实时转录、直播播客或视频播客处理。
- 不在未确认权利时公开完整逐字稿、精华博客或精华音频。
- 不一次性把用户私人 Podcast OPML、公开目录和内部精选库混成一个权限模型。

### 2.3 本次已实现切片与未实现边界（2026-09-02）

本轮实际落地的是一个**无数据库迁移的 metadata + 播放器 MVP**，目的是先验证“Podcast 能否作为 Reader 的第四种内容形态成立”，不是把本文后续的完整领域模型和 AI 流水线宣称为已完成。

已实现：

- 后端注册 `GenericPodcastRssFetcher`：配置仍落现有 `SourceConfigRecord`，当前契约为 `source_type = podcast`、`fetcher_id = generic_podcast_rss`；没有新增表或 Alembic migration。
- 新增 `PodcastEpisodeContent(content_type="podcast_episode")`，抓取 RSS/Atom 的 show title、作者、标签、GUID、show notes、enclosure URL/MIME/字节数、iTunes duration/episode/season/explicit、封面，以及 Podcasting 2.0 transcript 资源列表和 chapters URL/MIME。
- 抓取阶段**只保存节目单与音频定位元数据**，不下载音频、不读取逐字稿正文、不运行 ASR/LLM/TTS。
- 单集仍写入现有 `ArticleRecord`；Podcast 字段通过 `extensions_json` 持久化。文章列表和详情 API 投影同一个轻量 `podcast` 对象，不向客户端透出 `raw_data`。
- 轻量投影提供 `processing_eligible = duration_seconds > 1800` 和 `transcript_available`。前者目前只是时长提示，不是本文 §10 所述包含权利、质量、预算的完整资格判定。
- 后端 source shape 已扩展为 `article | bulletin | social | podcast`，支持 Podcast 容器过滤与按容器全部标读；`podcast_episode` 也进入内容类型标签。
- 桌面端与移动端 Reader 增加 Podcast 入口、源过滤、封面/节目名/时长/状态卡片和详情音频区；使用浏览器原生 `<audio controls preload="metadata">` 播放原 enclosure。若现有扩展数据已人工提供 `condensed_audio_url`，同一区域可以显示带“AI 生成”标识的精简版，但系统不会生成它。
- 已有后端测试覆盖 RSS 元数据解析、duration 边界、source 路由/shape、轻量投影、列表/详情一致性和 `1800/1801` 秒边界。

明确未实现：

- 本文 §8.3 的 `PodcastEpisodeRecord`、rights、processing、artifact、segment、playback state 等新表与迁移。
- 专用 `/api/podcasts/*`、节目页/单集页、Podcast Index/Listen Notes 搜索、Podcast OPML 和专用导入/审核后台。
- 官方 transcript/chapters 文件的下载、解析、分页、时间轴联动；当前仅保留 URL 和 MIME 元数据。
- 音频探测、持久缓存/对象存储、Range 代理、签名 URL、跨页面 mini-player、服务端播放进度与双时间轴。
- `>30min` 的 rights/质量/预算门控、持久状态机、Job、ASR、摘要/精华博客、播音稿、TTS、15 分钟 QA、发布/下架和成本计量。
- 当前播放器读取上游原音频 URL；尚未完成本文 §13 的所有媒体代理、授权与衍生发布控制。因此只能作为受信任源的 MVP，不代表公开转制能力已经具备。

后续 Phase 1 新表是把已验证的兼容投影升级为可审计的领域事实源；迁移完成前，`extensions_json` 中的 `processing_status`、`condensed_audio_url`、`condensed_duration_seconds` 只是前向兼容展示字段，不能被当作真实处理系统。

## 3. 事实与设计推断的标记

本文竞争分析中的“事实”只来自截至调研日可访问的官方页面、官方帮助中心或项目官方仓库；未找到官方证据时明确写“未见官方资料”。“建议/推断”是针对 Dorami 的产品判断，不代表竞品事实。

价格是调研日的公开标价，不含税、汇率、批量折扣、区域加价、存储、出网和失败重试；上线时必须由配置和账单监控读取，不能硬编码。

## 4. 竞品调研

### 4.1 能力矩阵

| 产品 | 官方定位与内容源（事实） | 播客/音频能力（事实） | AI 阅读/知识化（事实） | 导入与订阅（事实） | Dorami 可借鉴 |
| --- | --- | --- | --- | --- | --- |
| BestBlogs | 600+ 人工筛选源；文章、播客、视频、推文、Newsletter；AI 筛选后专家复核 | 播客详情流为 player → chapters → transcript → overview → show notes；曾有简报音频与后台播放器，但当前状态冲突，见下节 | 结构化总结、章节跳转、基于内容的问答和段落引用、双语 | RSS/网页 URL、OPML；私人源只进入个人池 | 同一内容详情下切换听/读；章节与证据定位；精选库与私人源隔离 |
| AIHOT | AI 信息源聚合与降噪；主页含精选/全部/热榜/日报/专题/收藏 | 未见官方一等播客播放器、逐字稿或再生成音频资料 | 卡片含分数、摘要、推荐理由、关联报告；支持聚类、分类、翻译 | 提供 RSS/API/MCP/Skill 输出协议 | 先筛选再花 ASR 钱；对外输出契约与 ETag/增量设计；版权提示要显式 |
| Feedly | 可添加网站、博客、出版物、研究源、Newsletter、RSS、YouTube、Podcast RSS、社交源 | 官方资料确认 Podcast RSS 订阅；未见逐字稿/精华再音频能力 | 单篇 AI Summary；最多 50 份报告的 Ask AI 综合并附引用；多篇 AI Overview | OPML 导入/导出；Feeds/Folders/Boards/AI Feeds/Dashboards | 统一订阅体验、分文件夹、跨内容综合；同时保持媒体类型专属详情页 |
| Snipd | AI podcast app，官方称覆盖约 400 万播客 | 原音频播放、章节、逐字稿、原句时间戳；AI DJ 将原声“最佳片段”加短 AI 串场，目标约原时长 25%，示例 60→15 分钟，初期仅英文 | 摘要、可对话逐字稿、snip/highlight、说话人 | Podcast 搜索使用 Listen Notes；支持公开/私有 RSS、OPML 和其他 App 导入 | 15 分钟目标已有用户心智；优先原声证据比全合成更可信，但公开再剪辑原声的权利风险更高 |
| Podwise | Podcast 知识管理与学习 | 支持 Apple/Spotify/YouTube/RSS/私有源/音频；未见生成精华音频官方能力 | 摘要、takeaways、mind map、逐字稿、章节、问答、关键词；处理结果 API 含时间戳 | 导出 Notion/Readwise/Obsidian/Logseq/Markdown/PDF；提供 CLI/Skill/MCP | 把流水线产物建模为结构化对象，利于导出、搜索和 agent 消费 |
| Readwise Reader | 全内容阅读与稍后读；明确说是 podcast app 的“companion”而非替代品 | 可从 Spotify/Overcast/Apple 链接加入，订阅 RSS，触发逐字稿；Podcast 是独立 library type | 逐字稿高亮、搜索、标签、导出与聊天 | RSS/OPML；可订阅 podcast feed | 采用 companion 边界：原节目负责完整收听，Dorami 强在理解、回顾与精华 |
| Listen Notes / PodcastAPI | 商业播客搜索、目录与元数据 API；官方称约 380 万播客、1.9 亿+单集 | 返回 feed/episode/audio 等元数据；无逐字稿时建议把 audio URL 交给第三方 STT | 不是主要摘要产品 | 搜索 API；免费层 300 次/月，付费含更多字段/转录能力 | 适合快速补齐公开搜索，但不应成为已订阅源的事实源或唯一标识 |
| NotebookLM | 以用户提供来源为基础的研究/学习工具；支持音频源导入并转录 | Audio Overview 有 Deep Dive、Brief、Critique、Debate，可选择语言、重点和长度并下载/分享 | 基于来源的问答与音频概览；官方提示可能出现错误或音频故障 | 单文件上限 200 MB 或 50 万词；免费层每 notebook 50 个来源 | 借鉴“生成前选重点/长度/语言”和显式 AI 风险；未见稳定批处理 RSS API，不应作为生产依赖 |

### 4.2 BestBlogs 文档状态冲突（必须在决策中保留）

调研日的官方材料同时存在三组互相矛盾的信息：

- [2026-04-11 v2.0.0](https://www.bestblogs.dev/en/changelog) 写 My Brief 有文字 + 音频。
- [2026-04-13 v2.0.2](https://www.bestblogs.dev/en/changelog) 写后台播放、文字/Podcast 双视图、带音频元数据的 RSS，并提到改进播客脚本生成。
- [2026-05-04 v2.2.0](https://www.bestblogs.dev/en/changelog) 明确写 Pro Brief 改为 **text-only**，以前生成的播客仍可播放。
- 然而调研日的 [Public Brief 文档](https://www.bestblogs.dev/en/docs/public/brief) 与 [FAQ](https://www.bestblogs.dev/en/docs/faq) 仍把“Brief podcast/audio edition”列为 Pro 能力。

因此本报告只认定：**BestBlogs 曾实现生成式简报音频；其在 2026-09-02 是否仍对新简报持续生成音频，官方资料无法一致证明。** 不应把该功能当成已经验证的长期产品成功案例。反而应借鉴它暴露的风险：音频生成的时延、成本、可靠性与真实使用率很可能使产品回退到文字，因此 Dorami 必须先有使用门控、成本预算和可关闭的功能旗标。

### 4.3 逐项事实依据

#### BestBlogs

- [How It Works](https://www.bestblogs.dev/en/docs/how-it-works)：多类型内容、AI Screening + Expert Review、精选逻辑。
- [Sources](https://www.bestblogs.dev/en/docs/public/sources)：公开源目录及类型。
- [Add Sources](https://www.bestblogs.dev/en/docs/personal/add-sources)：RSS/网页/OPML 和私人源边界。
- [Explore](https://www.bestblogs.dev/en/docs/public/explore)：内容类型、主题、时间、语言、分数与排序筛选。
- [AI Reading Assistant](https://www.bestblogs.dev/en/docs/personal/ai-reading)：摘要、问答、引用、章节跳转和双语阅读。
- [Changelog](https://www.bestblogs.dev/en/changelog)：2026-06-25 记录当前播客详情顺序与章节交互；同时是音频简报状态冲突的来源。

#### AIHOT

- [About](https://aihot.virxact.com/about)：聚合、AI 降噪与内容筛选定位。
- [Homepage](https://aihot.virxact.com/)：当前信息架构、卡片字段和榜单/专题入口。
- [Changelog](https://aihot.virxact.com/changelog)：一手源优先、翻译、聚类与分类演进。
- [Agent / API](https://aihot.virxact.com/agent)：RSS、API、MCP、Skill、ETag/快照与增量契约；也明确提示 RSS 可访问不代表获得公开或商业再分发权。

#### Feedly

- [The 10 types of sources](https://feedly.com/new-features/posts/the-10-types-of-sources-you-can-add-on-feedly)：含 podcast RSS 的多源订阅。
- [OPML import](https://docs.feedly.com/article/51-how-to-import-opml-into-feedly) / [OPML export](https://docs.feedly.com/article/52-how-can-i-export-my-sources-and-feeds-through-opml)：迁移能力。
- [Sidebar](https://feedly.com/new-features/posts/meet-your-new-sidebar-less-clutter-more-personalization)：Feeds、Folders、Boards、AI Feeds、Dashboards。
- [Summarization](https://feedly.com/new-features/posts/feedly-ai-and-summarization)：列表摘要与重点句。
- [Ask AI](https://docs.feedly.com/article/810-how-to-use-ask-ai)：多报告综合、跨语言与引用。
- [AI Summary vs AI Overview](https://docs.feedly.com/article/811-how-to-use-ai-in-automated-newsletter)：单篇和多篇产物边界。

#### Snipd

- [AI podcast summaries you can chat with](https://www.snipd.com/blog/ai-podcast-summaries-you-can-chat-with)：逐字稿、摘要、章节、说话人、准确引文与时间戳。
- [AI DJ](https://www.snipd.com/blog/ai-dj-listen-to-best-parts-of-any-podcast)：原声精华 + AI 串场，约原时长 25%，示例 60→15 分钟。
- [Pricing](https://www.snipd.com/pricing)：免费每周 2 集 AI，Premium 月度处理额度 900 分钟等公开限额。
- [Private RSS](https://support.snipd.com/en/articles/10226052-adding-private-rss-feeds) / [Imports](https://support.snipd.com/en/collections/11061889-import-podcasts)：私有 RSS、OPML 与迁移。
- [Submitting podcasts](https://support.snipd.com/en/articles/11137020-submitting-podcats)：其搜索使用 Listen Notes。

#### Podwise

- [Product and pricing](https://podwise.ai/)：支持来源、结构化产物、导出目标和公开套餐。
- [Documentation](https://docs.podwise.ai/)：使用与集成入口。
- [AI Agent](https://docs.podwise.ai/ai-agent)：CLI、Skills、MCP。
- [Processing result API](https://docs.podwise.ai/ent-api-v1/processing/get-processing-result)：摘要、章节/时间戳、问答、思维导图、takeaways、关键词字段。

#### Readwise Reader

- [RSS FAQ](https://docs.readwise.io/reader/docs/faqs/feed)：RSS 与 OPML。
- [Adding new content](https://docs.readwise.io/reader/docs/faqs/adding-new-content)：Podcast 链接、RSS 与转录入口。
- [December 2025 update](https://readwise.io/reader/update-dec2025)：Podcast companion 定位和逐字稿阅读能力。

#### Listen Notes / PodcastAPI

- [Listen API](https://www.listennotes.com/en/api/)：目录规模、搜索与脏数据整理说明。
- [API FAQ](https://www.listennotes.com/api/faq/)：数据来源、提交和更新频率。
- [Podcast search API](https://www.listennotes.help/article/38-use-podcast-search-apis)：搜索端点用途。
- [PodcastAPI](https://www.podcastapi.com/)：没有逐字稿时使用音频 URL + 第三方 STT 的建议。
- [Pricing](https://www.listennotes.com/api/pricing/)：请求额度与付费字段。

#### NotebookLM

- [Supported source types and limits](https://support.google.com/notebooklm/answer/16215270?co=GENIE.Platform%3DDesktop&hl=en-GB)：音频导入、转录、大小/字数/数量限制。
- [Audio Overviews](https://support.google.com/notebooklm/answer/16212820?hl=en)：格式、语言、长度、下载/分享和错误提示。

### 4.4 借鉴与反模式

建议借鉴：

- BestBlogs 的统一详情框架，但要让播放器、章节、逐字稿和精华博客共享同一时间轴。
- AIHOT 的“先降噪、后深加工”；没有质量门控就不要付 ASR/TTS 成本。
- Feedly 的统一订阅与 OPML 迁移、同时保持内容类型的专属工作区。
- Snipd 的时间戳证据、持续播放器和 60→15 的明确价值承诺。
- Podwise 的结构化产物与导出契约，而不是只存一段 Markdown。
- Readwise 的 companion 定位，避免用一个首版网页播放器硬碰成熟播客 App。
- NotebookLM 的“重点/语言/长度”控制和显式 AI 标签。

不要照抄：

- 不复制 Snipd 的原声剪辑再发布，除非逐节目取得剪辑与再分发授权；全合成平台声更容易标识来源和控制许可边界。
- 不把 BestBlogs 的音频简报当作持续有效的商业验证；官方状态冲突本身就是成本/使用率风险信号。
- 不照搬 Feedly 的企业级全域 AI Feed；首版只对精选播客做昂贵处理。
- 不把 Listen Notes 的外部 ID 作为主键，也不让目录元数据覆盖直接 RSS。
- 不依赖 NotebookLM 完成生产批处理；官方资料未给出稳定的 RSS 批处理产品契约。
- 不把“AI 摘要”与“可播音脚本”视为同一个产物。前者追求信息密度，后者还需要口语节奏、数字读法、引用处理和时长验证。

## 5. 内容源与治理策略

### 5.1 内网博客 RSS 与 Podcast RSS 必须分轨

用户会从内网导入一批优质博客 RSS。它们应进入现有文章管线，不应因为同样使用 RSS 就与播客共用处理策略。

| 维度 | 内网优质博客 RSS | Podcast RSS |
| --- | --- | --- |
| 当前 `source_type` | `rss`（沿用） | `podcast`（MVP 约定） |
| 当前 `fetcher_id` | 现有 generic RSS fetcher | `generic_podcast_rss` |
| 建议 `source_family` | `blog` | `podcast` |
| 主内容类型 | `rss_article` | `podcast_episode` |
| 主要载荷 | HTML/正文/摘要 | enclosure 音频、show notes、章节、逐字稿 |
| 抓取成本 | 文本请求 | feed 请求 + 可选大音频下载 |
| 深加工默认值 | 可按现有 `ai_analysis_enabled` | 默认关闭，需权利与预算门控 |
| 权利默认值 | 引用/链接；是否存全文沿现有政策 | `link_only`；衍生文字/音频均默认禁止 |
| 更新节奏 | 现有 RSS 调度 | `podcast:updateFrequency` 优先，缺失则自适应 |
| 归档 | Archive Sync v1 可带文章正文/扩展 | 元数据可兼容；二进制音频需新资产清单 |
| 展示 | Reader/搜索/Daily Brief | Podcast 专栏/节目页/单集页，可被统一搜索召回 |

具体约束：

- 内网导入的团队精选博客源保持 `owner_username = ""`，这是团队治理源，不复用 `user_rss_` 私人源语义。
- 私人 RSS 继续遵循 `docs/user-custom-rss-wave-plan.md`：只在拥有者空间可见，不进入全局目录、全局检索、Daily Brief 或 Archive Sync。
- 内部精选并不等于自动公开。建议新增 `audience = internal | authenticated | public` 与 `catalog_visibility`，避免用 `owner_username` 承担可见性全部含义。
- 博客源批量导入支持 OPML/CSV/JSON 预览，但入库前输出：合法 URL、重复项、重定向后 canonical URL、最近更新时间、内容语言、抓取错误与推荐分类。确认后再写库。
- 同一域名可同时有博客 feed 与 podcast feed，但必须形成不同 source_id，避免订阅/抓取/健康状态串线。
- `source_type = podcast` 是当前后端/前端共同契约；若未来为兼容外部导入接受 `podcast_rss`，只在 API 输入适配层把它规范化为 `podcast`，数据库和响应不得同时存在两种枚举值。

### 5.2 Podcast 来源优先级

1. **团队直接提供的 canonical RSS**：最高可信；验证后成为事实源。
2. **Podcast Index**：用于搜索发现、由 Apple/Spotify 链接反查 RSS、补充 Podcasting 2.0 字段。官方 [API 文档](https://podcastindex-org.github.io/docs-api/) 提供 feed/episode 搜索及 GUID 等端点。
3. **Listen Notes**：当 Podcast Index 搜索质量不足时的商业备选，不是运行时硬依赖。
4. **用户私有 RSS/OPML**：后续阶段，沿私人源边界，不进入公共精华流水线。
5. **平台 URL**：Apple/Spotify/小宇宙链接只能用于解析或人工找到官方 RSS；禁止页面抓取和绕过平台限制。

直接 RSS 一旦确认，外部目录只能提供候选补充信息，不能自动覆盖标题、enclosure、许可或 canonical feed URL。

### 5.3 去重与身份

- 节目主身份：优先 `podcast:guid`；否则 `canonical_feed_url` 的 SHA-256。
- 单集主身份：`podcast:guid`/RSS `guid` + 节目身份；缺失时使用规范化 enclosure URL + 发布时间 + 标题的组合哈希。
- enclosure URL 会因 CDN 签名或动态广告改变，不能单独作为单集主键。
- 保存 `audio_etag`、`last_modified`、`content_length`、首尾抽样哈希；发现内容变化时生成新输入版本，不原地污染旧产物。
- 对动态广告插入音频，官方时间戳可能与实际下载版本偏移；首版若时间戳校验失败，应降级为段落级引用并标记“时间可能有偏差”。

### 5.4 抓取与新鲜度

- 支持 `ETag`、`Last-Modified`、条件请求、重定向后的 canonical URL。
- 优先读取 `podcast:updateFrequency`；没有时按历史发刊间隔自适应，默认 60 分钟，稳定后放宽，上游错误指数退避并加 jitter。
- Feed 预览只下载 XML 与少量元数据，不下载 enclosure。
- 单源连续失败沿用 `SourceStateRecord` 健康度；但音频下载/ASR 失败属于 episode processing，不应把健康 feed 自动判死。
- Podping 可在后续用作低延迟提示，轮询仍是正确性兜底。

## 6. Podcasting 2.0 兼容策略

[Podcast Namespace 官方仓库](https://github.com/Podcastindex-org/podcast-namespace) 的目标是为开放播客扩展 RSS；[Podcasting 2.0 概览](https://github.com/Podcastindex-org/podcast-namespace/blob/main/podcasting2.0.md) 说明了 transcript、chapters、license、alternate enclosure 等能力。

### 6.1 读取优先级

| 标签 | Dorami 用法 | 首版要求 |
| --- | --- | --- |
| `podcast:guid` | 稳定节目身份 | 读取并保留；缺失才使用 feed URL 哈希 |
| `podcast:transcript` | 官方逐字稿 URL、MIME、语言、rel | 优先于 ASR；支持 text/plain、HTML、SRT、VTT、JSON，保存原文件与规范化副本 |
| `podcast:chapters` | 外部 JSON 章节 | 优先展示；验证 URL、MIME 与时间范围，可与 ASR 章节并存但标来源 |
| `podcast:person` | 主持人/嘉宾与角色 | 用于节目页与搜索，不自动人脸/身份推断 |
| `podcast:license` | 节目/单集许可线索 | 写入 rights 记录，不能只靠标签自动判定所有衍生用途 |
| `podcast:locked` | 发布者的导入意愿 | `yes` 时不得自动导入到新托管平台；Dorami 仍可按政策仅链接，但需法务确认 |
| `podcast:block` | 发布者要求目录屏蔽 | 命中 Dorami/全局 block 时不展示并停止后续处理 |
| `podcast:alternateEnclosure` | 其他编码/码率/媒体版本 | 选择兼容且成本最低的音频输入；保存 source/integrity，不擅自发布为 Dorami 版本 |
| `podcast:soundbite` | 发布者提供的短预览 | 可作节目卡试听，不能当作 Dorami 生成精华 |
| `podcast:funding` | 支持创作者 | 原样展示链接，提高归因和创作者收益入口 |
| `podcast:updateFrequency` | 自适应轮询 | 支持并保留调度建议 |

官方 [XSD](https://github.com/Podcastindex-org/podcast-namespace/blob/main/podcast.xsd) 明确 `podcast:transcript` 可以出现多次并携带 URL、MIME 与语言。实现时应保存所有候选，按 `目标语言匹配 > VTT/SRT/JSON 时间轴 > plain text` 选择，不覆盖原始清单。

### 6.2 Dorami 自有 feed 的发布

只有 `derivative_audio_allowed = true` 且审核通过的精华音频，才可进入 Dorami 自有的、按受众鉴权的 Podcast RSS：

- `<enclosure>` 指向 Dorami 生成音频；title/description 明确“AI 中文精华，不是原节目”。
- `<podcast:transcript>` 指向播音稿或 VTT，而不是未经许可公开完整原逐字稿。
- `<podcast:chapters>` 指向精华音频自己的时间轴，不能错误复用原节目时间戳。
- show notes 必须含原节目、原单集、创作者、许可/授权依据与“可能有 AI 错误”的说明。
- 若只有站内播放授权，feed 必须使用不可枚举、可撤销 token；若没有分发授权，仅站内展示文字或链接原节目。

## 7. 页面与信息架构

### 7.1 一级导航

建议 Reader 左侧新增 **Podcast** 一级入口，与文章流并列；统一搜索仍能跨 `rss_article` 和 `podcast_episode`，但结果用明确的内容类型 badge。不要把 Podcast 仅做成普通文章筛选项，否则播放器、节目层级、播放进度和章节都会被文章卡片稀释。

```text
Podcast
├── 为你推荐        精选 + 订阅 + 使用反馈重排
├── 最新单集        时间流；可筛选语言/时长/是否有精华
├── 我的订阅        节目分组和未听数量
├── 精华 ≤15 min    只展示已发布精华音频/博客
└── 浏览节目        官方目录 + 团队精选集合
```

“为你推荐”首版不用复杂向量推荐：以订阅、分类、质量分、发布时间和来源去重做确定性配额，借鉴现有 Daily Brief 的候选/去重/选择分层。

### 7.2 列表卡片

单集卡片至少展示：节目封面、单集标题、节目名、发布时间、原时长、语言、质量分/推荐理由、`原节目`/`AI 精华` 可用性、精华时长、播放进度、权利/AI 标签。

交互：

- 主按钮随状态变化：`播放原节目`、`播放 13 分钟精华`、`阅读精华`、`精华处理中`。
- 卡片内不自动播放；鼠标/键盘触发试听时必须可停止。
- 筛选：已订阅、未听/继续听、`>30min`、有逐字稿、有精华、语言、分类、来源。
- URL 保留筛选条件，便于刷新与分享；内部内容分享仍需鉴权。

### 7.3 节目页 `/podcasts/shows/:sourceId`

- 头部：封面、名称、作者、语言、分类、简介、官网/RSS、订阅按钮、更新频率、显式内容提示、创作者 funding。
- 内容：`单集`、`关于`；管理员额外看到 `来源健康`、`权利策略`、`处理预算`。
- 单集列表默认最新；支持“仅看有精华”“最长/最短”“未听”。
- 权利为 link-only 时正常提供原节目入口，但隐藏所有衍生处理按钮。

### 7.4 单集页 `/podcasts/episodes/:episodeId`

桌面端建议结构：

```text
┌──────────────────────────────────────────────────────────┐
│ 标题 / 节目 / 来源 / AI 与授权标识 / 分享                 │
├───────────────────────────┬──────────────────────────────┤
│ 固定播放器                │ 章节                         │
│ [原节目 | AI 中文精华]    │ 00:00 ...                    │
│ 进度 / 倍速 / 30s 跳转    │ 点击联动播放器与正文         │
├───────────────────────────┴──────────────────────────────┤
│ [精华博客] [逐字稿] [Show notes] [产物信息]              │
│ 标题、要点、正文、[12:34] 时间戳证据、免责声明           │
└──────────────────────────────────────────────────────────┘
```

要求：

- 切换原节目/精华音频时各自保存播放位置；切换不应把时间戳互相套用。
- 原文时间戳链接始终跳到原节目；精华章节跳到精华音频。
- 逐字稿采用虚拟列表/分段分页，不能一次把数万字渲染进 DOM。
- 移动端播放器吸底；章节和正文上下排列；正文滚动不能被“自动跟随字幕”劫持。
- 播放器支持键盘、屏幕阅读器标签、焦点态、速度 0.75–2x、前后 15/30 秒。
- 生成中展示阶段与合理 ETA，不显示虚假的百分比；失败展示“继续听原节目”，管理员可看错误码。

### 7.5 管理页面

`/admin/podcast-sources`：导入/预览、重复合并、来源健康、rights policy、单源成本上限。
`/admin/podcast-pipeline`：按阶段/失败原因/供应商/成本筛选，支持单集 retry、从某阶段重跑、publish/unpublish 和紧急下架。

## 8. 系统架构

### 8.1 与现有能力的关系

- `SourceConfigRecord` 继续承担“如何抓这个节目”的配置，MVP 使用 `source_type = podcast` 与 `fetcher_id = generic_podcast_rss`；团队源 `owner_username` 为空。
- `SourceStateRecord` 继续承担 feed 级游标与健康度。
- 每个单集建立 `ArticleRecord(content_type="podcast_episode")`，让统一搜索、订阅流、标签、质量分和 Daily Brief 能复用现有基础设施；`content` 保存 show notes 或已发布精华博客的可检索投影，不保存大音频。
- `ArticleAnalysisRecord` 可继续存统一质量分、简短摘要和分类，但它“一篇文章一个状态”的结构不足以表达多阶段、多产物、可独立重试的媒体流水线。
- 顶层 `JobRecord` 继续承担持久异步任务和用户可见进度；另建 episode processing 记录保存每一阶段的事实状态、版本、成本和失败原因。
- `extensions_json` 只保存小型兼容投影/外部元数据，不塞完整逐字稿、分段数组或音频二进制。

### 8.2 数据流

```text
直接 RSS / Podcast Index 候选
        │
        ▼
Feed 预览、SSRF 校验、规范化、去重 ──► SourceConfig + SourceState
        │
        ▼
Episode 元数据入库 ──► ArticleRecord(podcast_episode) + PodcastEpisode
        │
        ▼
资格/权利/预算门控 ──不通过──► link-only / skipped / blocked
        │通过
        ▼
官方 transcript 优先 ──无──► 临时下载音频 ──► ASR/对齐/说话人
        │
        ▼
规范化逐字稿 ──► 分层摘要 ──► 结构化精华博客 + 时间戳证据
        │
        ▼
事实/长度/安全 QA ──► 中文播音稿 ──► TTS ──► ffprobe 时长校验
        │
        ▼
人工抽检/自动发布策略 ──► 对象存储 + API + 可选鉴权 RSS
```

### 8.3 建议新增表

#### `PodcastEpisodeRecord`

- `id`, `article_id`（唯一 FK）、`source_id`
- `episode_guid`, `podcast_guid`, `canonical_episode_url`
- `enclosure_url`, `enclosure_mime`, `enclosure_bytes`, `audio_etag`, `audio_last_modified`
- `duration_seconds`, `duration_source = rss | probe | directory`
- `language`, `explicit`, `season_number`, `episode_number`, `episode_type`
- `image_url`, `authors_json`, `persons_json`
- `source_transcripts_json`, `source_chapters_url`, `license_url`, `funding_json`
- `input_fingerprint`, `published_at`, `updated_at`

#### `PodcastRightsRecord`

- `source_id`，可选 `episode_id` 覆盖节目默认值
- `policy = link_only | transcribe_private | derivative_text | derivative_audio`
- `transcript_allowed`, `derivative_text_allowed`, `derivative_audio_allowed`, `public_distribution_allowed`
- `license_name`, `license_url`, `evidence_url`, `evidence_note`
- `reviewed_by`, `reviewed_at`, `expires_at`, `policy_version`

布尔字段用于运行时快速门控，`policy` 用于后台展示；episode 级拒绝永远优先于 source 级允许。

#### `PodcastProcessingRecord`

- `id`, `episode_id`, `job_id`, `status`, `stage`, `attempt`
- `input_fingerprint`, `policy_version`, `pipeline_version`
- `asr_provider/model/version`, `llm_provider/model/prompt_version`, `tts_provider/model/voice/version`
- `audio_minutes`, `input_tokens`, `output_tokens`, `tts_characters`
- `estimated_cost_usd`, `actual_cost_usd`, `cost_breakdown_json`
- `lease_owner`, `lease_expires_at`, `next_retry_at`
- `error_code`, `error_message_redacted`, `started_at`, `finished_at`

唯一约束建议为 `(episode_id, input_fingerprint, pipeline_version)`；同一输入与版本重复请求返回已有 run，人工 `force=true` 才新开 attempt。

#### `PodcastArtifactRecord`

- `id`, `episode_id`, `processing_id`
- `kind = source_transcript | normalized_transcript | digest_blog_zh | narration_script_zh | digest_audio_zh | source_chapters | digest_chapters`
- `language`, `status`, `version`, `content_hash`
- 小文本 `inline_text`；大文本/音频 `storage_uri`, `mime_type`, `bytes`
- `duration_seconds`, `word_or_char_count`, `is_public`, `published_at`, `expires_at`
- `provenance_json`（源 artifact、模型、提示版本、时间戳映射）

#### `PodcastTranscriptSegmentRecord`

- `artifact_id`, `ordinal`, `start_ms`, `end_ms`, `speaker_label`, `text`, `confidence`
- 对 `(artifact_id, ordinal)` 唯一；对时间范围索引。
- 搜索可把分段文本聚合进现有 FTS 投影，详情按 cursor 分页读取。

#### `PodcastPlaybackStateRecord`

- `username`, `episode_id`, `variant = original | digest`, `position_ms`, `duration_ms`, `completed`, `updated_at`
- 唯一 `(username, episode_id, variant)`；客户端 10–15 秒节流写入，页面卸载时 best-effort flush。

### 8.4 对象存储与 Archive Sync

- 开发环境：本地 artifact store；生产：S3-compatible/MinIO provider 接口，数据库只存 URI、哈希、字节数和 MIME。
- 原音频临时文件在 ASR 完成后按策略删除，默认 24 小时，失败排障最多 72 小时且仅管理员可访问。
- 生成音频、VTT、章节 JSON 可长期保存，但支持权利撤销后的级联下架与物理删除任务。
- Archive Sync v1 是 Article JSONL，不搬运二进制。M1 可只同步 `podcast_episode` 的公开元数据/精华文字投影；若 Reader 要离线播放，必须设计 v2 `asset` manifest（URI、hash、bytes、mime、audience、expiry）或让目标环境从对象存储读取，不能假设 `extensions` 已经带走音频。

## 9. API 草案

所有列表端点沿用现有 cursor/limit、统一错误格式与鉴权；URL 中使用内部稳定 ID，不暴露对象存储路径。

### 9.1 用户端

```http
GET  /api/podcasts/home?tab=for_you&language=zh&digest=available&cursor=...
GET  /api/podcasts/shows?query=&category=&subscribed_scope=only&cursor=...
GET  /api/podcasts/shows/{source_id}
GET  /api/podcasts/shows/{source_id}/episodes?digest=&unplayed=&cursor=...
GET  /api/podcasts/episodes/{episode_id}
GET  /api/podcasts/episodes/{episode_id}/transcript?variant=source&cursor=...
GET  /api/podcasts/episodes/{episode_id}/artifacts
POST /api/podcasts/episodes/{episode_id}/playback-state
GET  /api/podcasts/episodes/{episode_id}/audio?variant=original|digest
```

- 节目订阅复用现有 Reader subscription 的 `source_id`，避免出现两套订阅事实源。
- `audio?variant=original` 返回经过安全校验的短时重定向/代理策略；默认不隐藏原 enclosure 的发布者归属。
- `audio?variant=digest` 在鉴权和 audience 校验后返回签名 URL，支持 Range；禁止把永久对象 URL 暴露给前端。
- episode 响应显式给出 `available_variants`、`processing_status`、`rights_display`、`ai_disclosure` 和两个独立时间轴。

示例：

```json
{
  "id": "ep_...",
  "content_type": "podcast_episode",
  "show": {"source_id": "podcast_...", "name": "..."},
  "title": "...",
  "duration_seconds": 3720,
  "available_variants": [
    {"kind": "original", "duration_seconds": 3720, "timeline": "original"},
    {"kind": "digest", "duration_seconds": 782, "language": "zh", "timeline": "digest", "ai_generated": true}
  ],
  "digest": {
    "status": "ready",
    "blog_artifact_id": "pa_...",
    "evidence_timeline": "original",
    "disclaimer": "AI 生成的中文精华，请以原节目为准"
  }
}
```

### 9.2 管理端

```http
POST /api/admin/podcast-sources/preview
POST /api/admin/podcast-sources/import
PATCH /api/admin/podcast-sources/{source_id}/rights
POST /api/admin/podcast-episodes/{episode_id}/process
POST /api/admin/podcast-processings/{processing_id}/retry
POST /api/admin/podcast-artifacts/{artifact_id}/publish
POST /api/admin/podcast-artifacts/{artifact_id}/unpublish
POST /api/admin/podcast-episodes/{episode_id}/takedown
GET  /api/admin/podcast-pipeline?stage=&status=&provider=&error_code=
```

- Preview 输入支持单 RSS URL、OPML、CSV/JSON；返回结果不可直接持久化，confirm 请求需带服务端签名的 `preview_token` 与过期时间。
- `process` 支持 `target = transcript | digest_blog | digest_audio`，默认继续缺失阶段；`force` 仅管理员可用。
- publish 使用乐观锁/expected artifact version，避免旧审核覆盖新产物。
- 所有 rights、publish、unpublish、takedown 写审计日志。

## 10. `>30 分钟` 门控与处理状态机

### 10.1 资格门控

按以下顺序执行，越早越便宜：

1. **结构门控**：有效节目与单集身份；可用 audio enclosure 或官方逐字稿。
2. **时长门控**：权威 `duration_seconds > 1800`。RSS 缺失或明显异常时只做轻量 HEAD/range/ffprobe；无法确认则 `needs_duration_probe`，不自动下昂贵任务。
3. **重复门控**：同一 `input_fingerprint + pipeline_version` 已有 ready/running 则复用。
4. **权利门控**：根据目标产物分别检查 transcript/text/audio 权限，不能以“RSS 公开”为充分条件。
5. **内容门控**：显式内容、音乐占比过高、语言不支持、纯广告/预告、低质量或与产品主题无关则跳过或人工审查。
6. **预算门控**：单集预计费用、来源日/月额度、全局日/月额度均有余量。
7. **资源门控**：供应商并发与队列水位可接受。

边界规则：正好 `1800` 秒不触发；管理员可以针对重要单集 override，但必须记录理由。建议设置 `max_duration = 180 分钟`、`max_download_bytes = 500 MB` 的自动处理上限，超过转人工。

### 10.2 状态机

顶层状态：

```text
discovered
  → metadata_ready
  → eligible | skipped_short | blocked_rights | rejected_content | over_budget
  → queued
  → running
  → awaiting_review | ready
  → published

任一运行态 → retry_wait → queued
任一运行态 → failed_terminal
awaiting_review/ready/published → superseded（输入或 pipeline 版本变化）
published → unpublished → deleted（撤权/下架）
```

`running.stage`：

```text
acquire_transcript
→ download_audio（仅无可用官方逐字稿）
→ transcribe
→ align_and_diarize（可选）
→ normalize_transcript
→ translate_transcript_zh
→ validate_translation_alignment
→ generate_digest
→ validate_digest
→ generate_narration_script
→ synthesize_audio
→ package_audio
→ validate_audio
```

关键语义：

- 阶段产物不可变；重跑创建新 version，成功后原子切换 active artifact。
- 网络超时、429、5xx 进入指数退避；输入损坏、无权限、超硬上限直接终止，不无意义重试。
- lease 到期可被其他 worker 接管；外部调用带幂等键，避免双倍计费。
- `cancelled` 停在安全阶段，已完成且仍合规的 artifact 可复用。
- `published` 不是处理成功的同义词；自动 QA 通过后仍可能需要人工/抽样审核。

### 10.3 结构化中间产物

摘要模型首先输出 JSON，不直接输出最终 Markdown：

```json
{
  "title_zh": "...",
  "one_sentence_summary_zh": "...",
  "audience": "...",
  "key_takeaways": [
    {"claim": "...", "evidence_segment_ids": [12, 13], "start_ms": 745000}
  ],
  "sections": [
    {"heading": "...", "body": "...", "evidence_segment_ids": [20, 21]}
  ],
  "caveats": ["..."],
  "named_entities": ["..."],
  "suggested_duration_seconds": 780
}
```

服务端验证 segment ID、时间范围、字段长度和引用覆盖率，再确定性渲染精华博客。完整中文转录是单独的 `transcript_zh` 产物，逐段保留源 segment、时间码和 speaker 映射。单旁白播音稿从摘要 JSON 独立生成，围绕核心观点、论据、事实、分歧和结论重组，去掉 Markdown、URL、表格和难读符号，但网页仍保留可点击证据。

### 10.4 15 分钟硬上限

- 生成目标设为 12–14 分钟，为模型语速与停顿留余量；15 分钟只是验收硬上限。
- TTS 前按选定 voice 的基准语速估算字符数；中文初始可用约 3,000–3,600 汉字作为经验区间，但必须用实际样本校准，不能只依赖字数。
- TTS 后使用 ffprobe 读取真实 `duration_seconds`：`<= 900` 通过；`901–960` 优先让 LLM删减冗余并重合成；不得仅靠高倍速压到 15 分钟；`>960` 必须重写脚本。
- 最多自动重写/合成 2 次，防止成本失控；之后 `awaiting_review`。
- 音频 QA 还要检查静音比例、峰值、响度、开头结尾截断、TTS 文本覆盖和敏感词误读。建议归一到约 -16 LUFS（立体声）或团队最终选定标准。

## 11. ASR、LLM、TTS 选型与成本

### 11.1 总成本公式

```text
episode_cost =
  audio_minutes × asr_price_per_minute
  + transcript_input_tokens / 1,000,000 × llm_input_price
  + llm_output_tokens / 1,000,000 × llm_output_price
  + tts_characters / 1,000 × tts_price_per_1k_chars
  + object_storage + egress + retry_cost + worker_cost
```

后台必须记录“预计/实际、按阶段、按供应商、按 source”的成本；价格配置带 `effective_at`，历史账单不随新价格重算。

### 11.2 ASR 方案

> 本节保留初轮背景。2026-09-03 复核后的国内/全球主候选、说话人能力、限制与成本见
> [`voice-stack-decision.md`](../specs/007-podcast-intelligence/voice-stack-decision.md)，其结论优先。

| 方案 | 调研日公开成本/属性 | 优点 | 风险 | 建议 |
| --- | --- | --- | --- | --- |
| 源站 `podcast:transcript` | 获取成本近似网络/存储 | 最便宜、最尊重创作者、常带时间轴 | 格式/质量不一；展示与衍生权仍需许可 | 永远优先 |
| OpenAI transcription API | 官方 [Pricing](https://developers.openai.com/api/docs/pricing) 将三个模型 ID 分开列价：`gpt-4o-mini-transcribe` 估算 `$0.003/min`、`gpt-transcribe` `$0.0045/min`、`gpt-4o-transcribe` `$0.006/min` | API 简单、无需 GPU、多语言；项目已有 OpenAI-compatible 配置经验 | 数据出境/保留需评审；说话人/词级时间轴能力要实测 | M0 默认测试 `gpt-4o-mini-transcribe`，低置信片段再评估另外两个，计费键必须保存完整模型 ID |
| AssemblyAI Universal-2 / 3.5 Pro | 官方 [Pricing](https://www.assemblyai.com/pricing) 的预录音价为 `$0.15/$0.21 per hour`（约 `$0.0025/$0.0035 per min`）；说话人分离加 `$0.02/hour` | 单价低，Universal-2 宣称支持 99 种语言，Pro 支持 code switching 与更强说话人能力 | 中文与专名效果仍需基准；附加能力单独计费 | 成本和 diarization 很有竞争力，纳入 M0 A/B 而不是只比一家 |
| Deepgram Nova-3 | 官方 [Pricing](https://deepgram.com/pricing) 预录音 Monolingual/Multilingual 公开档约 `$0.0077/$0.0092 per min`，页面同时展示流式与 Growth 价，结算前需核对选项 | 时间戳、说话人及音频特性成熟 | 比 `$0.003/min` 的 `gpt-4o-mini-transcribe` 贵；中文/中英混合需样本验证 | 对说话人/时间戳要求高时 A/B 备选 |
| 自托管 faster-whisper/WhisperX | 软件开源，成本是 GPU/CPU、队列与运维 | 可控、可复用、可做词级对齐/diarization | 闲置 GPU、模型下载、显存、扩缩容、升级成本 | 月音频量稳定后评估，不作为 M0 前置条件 |
| 自托管 SenseVoice/FunASR | 官方仓库强调中文、粤语、英文、日/韩与长音频本地部署；2026 版本加入说话人方案 | 中文与中英混合潜力、低推理延迟、数据本地 | 模型/依赖/许可组合和准确率需企业法务与样本基准 | 中文量大或隐私要求高时优先 PoC |

本文后续 `$0.18/60 分钟` 与 `$180/1000 集` 的例子**只指** `gpt-4o-mini-transcribe`（`60 × $0.003`）；`gpt-transcribe` 是另一个模型 ID，按调研日公开估算同一 60 分钟约 `$0.27`，不得与前者混写或作为别名。以上均尚未计算重试和其他阶段。是否自托管不应按“单次 API 看起来贵”决定，而应比较：

```text
break_even_minutes = monthly_fixed_gpu_and_ops_cost / managed_asr_price_per_minute
```

并把 GPU 利用率、工程值班、模型热启动和峰值队列纳入固定成本。

### 11.3 LLM 方案

- 沿用项目 OpenAI-compatible provider，新增 `podcast_digest_model`、`podcast_digest_prompt_version`、输入/输出 token 上限和 JSON Schema。
- 长逐字稿分两层：按 5–10 分钟/章节做 map（保留 segment IDs）→ reduce（合并主题、去重、生成博客）→ narration rewrite；避免一次性塞满上下文导致成本和遗漏不可控。
- 默认使用低价小模型；仅在 schema 校验失败、证据覆盖不足或质量抽检失败时升级。不要让大模型处理所有单集。
- 可使用 provider 的 batch/缓存折扣处理非实时任务，但流水线状态必须允许数小时延迟。
- 示例预算用调研日 [OpenAI API Pricing](https://developers.openai.com/api/docs/pricing) 中的低价文本模型档位计算，不把模型名写死：假设 12k 输入 token、4k 输出 token，输入 `$0.10/M`、输出 `$0.60/M`，一次核心生成约 `$0.0036`；实际还要乘 map 次数、重试和 narration rewrite。价格变化时公式仍成立。
- 如果使用本地 Qwen 等模型，先用 30–50 集人工标注集评估事实保真、时间戳引用召回、中文可读性和 JSON 合规；“零 API 费”不等于零成本。

### 11.4 TTS 方案

> 本节保留开源许可证背景。2026-09-03 的托管平台横向对比与单旁白架构见
> [`voice-stack-decision.md`](../specs/007-podcast-intelligence/voice-stack-decision.md)，其价格和结论优先。

可进入技术 PoC 的方案：

| 方案 | 代码/权重许可与官方能力 | 适用性 | 建议 |
| --- | --- | --- | --- |
| 托管中文 TTS provider adapter | 按字符或音频时长；商用、音频再分发、数据留存取决于供应商合同 | 无需 GPU，声音和 SLA 稳定 | M1 默认。至少比较中文数字/英文术语、15 分钟稳定性、再分发条款、并发与数据保留；价格通过配置注入 |
| Deepgram Aura | 官方 [Pricing](https://deepgram.com/pricing) 为 Aura-1 `$0.015/1k chars`、Aura-2 `$0.030/1k chars` | 可作成本公式参照；目标中文 voice 尚未核实 | 先核对官方 voice catalog 和中文样本；未验证前不作为中文默认 |
| CosyVoice | [官方仓库](https://github.com/QwenAudio/CosyVoice) 标 Apache-2.0，覆盖中文/英文等 9 种语言、中文方言、流式与服务部署 | 中文质量与可控性强，GPU/依赖比轻量模型重 | 中文规模化候选；只用获准固定音色，不启用零样本克隆；模型权重、`ttsfrd` 等附属组件和训练声音逐项审计 |
| Kokoro-82M | [官方模型卡](https://huggingface.co/hexgrad/Kokoro-82M) 和 [官方推理库](https://github.com/hexgrad/kokoro) 标 Apache-2.0；82M 参数，推理库列出普通话 `lang_code=z` | 轻量、部署成本低，适合快速跑长文本；中文自然度、专名和长篇一致性须实测 | 纳入首轮本地 PoC；生成 SBOM，尤其核对 G2P/`espeak-ng` 等运行与分发依赖，不因主模型 Apache-2.0 就跳过依赖审计 |
| MeloTTS | [官方仓库](https://github.com/myshell-ai/MeloTTS) 标 MIT，并明确允许商业/非商业使用；支持中文中英混合和 CPU 实时推理 | 资源要求低、商用许可清晰，适合中文成本基线；表现和维护活跃度要实测 | 与 Kokoro/CosyVoice 同样跑 15 分钟稳定性、数字/术语、音色许可基准；不启用外部 voice cloning |

当前应排除或需要先解决许可的热门方案：

| 方案 | 官方许可证据 | 商用判断与排除理由 |
| --- | --- | --- |
| ChatTTS | [官方仓库](https://github.com/2noise/ChatTTS) 写明代码为 AGPLv3+、发布模型为 CC BY-NC 4.0，且只用于教育/研究 | 权重明确非商用；不进入 Dorami 生产候选，只可在隔离研究环境做非商用评测 |
| F5-TTS 官方预训练权重 | [官方仓库](https://github.com/SWivid/F5-TTS) 写明代码 MIT，但预训练模型因 Emilia 训练数据采用 CC-BY-NC | “代码 MIT”不覆盖官方 checkpoint；除非取得商业许可或用权利清晰数据从头训练并完成法务审计，否则排除 |
| Fish Speech | [官方 LICENSE](https://github.com/fishaudio/fish-speech/blob/main/LICENSE) 是 Fish Audio Research License，明确任何 commercial purpose 均需另签书面许可，并把企业内部运营也列入商业用途 | 免费开源权重不能直接用于 Dorami 业务；拿到书面商业许可前排除 |
| XTTS-v2 | [官方文档](https://docs.coqui.ai/en/stable/models/xtts.html) 指向 Coqui Public Model License；[官方权重 LICENSE](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt) 明确模型及输出仅限非商业用途 | 即使周边推理代码更宽松，模型和输出仍受限；生产排除，且其声音克隆定位也不符合首版中性平台声策略 |
| Piper | 旧 [rhasspy/piper](https://github.com/rhasspy/piper) 为 MIT 但已归档并指向当前 [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl)，当前引擎为 GPL-3.0；[piper-voices](https://huggingface.co/rhasspy/piper-voices) 中 voice/model card 的数据与许可逐声音不同 | GPL 不等于禁止商用，但会带来分发/组合的 copyleft 合规要求，voice 权利也不能由引擎许可推定；首版不作默认方案，只有法务批准部署边界并选定许可清晰的具体 voice 后才可进入 PoC |

判断规则是：**代码 license、模型权重 license、voice/训练数据条款、模型输出条款四项取最严格边界**。仓库首页写 MIT/Apache 不能替代具体 checkpoint 与 voice model card 的审计。

以 3,200 个中文字符、`$0.015/1k chars` 的公开字符价仅作演算，TTS 约 `$0.048`。加上 60 分钟 `gpt-4o-mini-transcribe` `$0.18` 和上述一次核心 LLM `$0.0036`，理想路径约 `$0.232/集`，不含 map/rewrite、存储、出网、供应商最低计费和失败重试。建议给单集默认软预算 `$0.35`、硬预算 `$0.60`，上线前用真实中文供应商报价重算。

### 11.5 推荐的成本阶梯

- **Tier 0**：已有官方逐字稿，只生成精华博客；最优先、最低成本。
- **Tier 1**：无逐字稿但高质量，低价 ASR（首测 `gpt-4o-mini-transcribe` 与 AssemblyAI Universal）+ 小模型博客；默认长播客路径。
- **Tier 2**：用户点击“想听精华”或编辑精选后才 TTS；避免为无人播放的单集预生成。
- **Tier 3**：置信度/抽检失败才升级 ASR/LLM 或人工修订。
- 每个 source 设每月分钟数和美元额度；超额仍可展示原节目，不影响订阅基本功能。

## 12. 可复用的开源能力

| 能力 | 项目与官方链接 | 成熟度/适用性 | 集成注意 |
| --- | --- | --- | --- |
| RSS/Atom 解析 | [feedparser](https://github.com/kurtmckee/feedparser) | Python 生态成熟；可复用现有 RSS fetcher 经验 | 仍需自己解析 iTunes/Podcast Namespace、SSRF、防大包和 canonical 规则 |
| Podcasting 2.0 | [podcast-namespace](https://github.com/Podcastindex-org/podcast-namespace) | 规范与 XSD 是实现依据 | namespace 演进快，保存未知 tag 原始数据以便前向兼容 |
| 播客目录 | [Podcast Index API](https://podcastindex-org.github.io/docs-api/) | 开放发现与元数据，支持 feed/episode/GUID 搜索 | 目录是候选，不覆盖直接 RSS；遵守凭据、速率与条款 |
| 音频探测/转码 | [FFmpeg](https://ffmpeg.org/) / ffprobe | 行业标准，能做时长、编码、响度、VTT/章节处理 | 放隔离 worker，参数白名单，限制 CPU/内存/时长；构建许可证要审计 |
| 通用 ASR | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | CTranslate2 批处理与量化，社区成熟 | 下载模型、GPU/CPU 基准、VAD 与长音频分块要固化版本 |
| 词级对齐/说话人 | [WhisperX](https://github.com/m-bain/whisperX) | BSD-2-Clause；官方仓库提供词级时间戳、VAD、diarization | 依赖 pyannote 和各语言 align model；模型条款与 HF token 单独审计 |
| 中文 ASR | [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [FunASR](https://github.com/modelscope/FunASR) | 中文/粤语/中英混合与本地部署有吸引力 | 仓库代码 license、模型权重 license、依赖分别确认；用真实播客 benchmark |
| 中文 TTS | [CosyVoice](https://github.com/QwenAudio/CosyVoice) | Apache-2.0 代码，中文、多语、流式、部署路径完整 | 禁止未经同意的声音克隆；权重、声音来源、附属二进制与商用条款单独审计 |
| 轻量 TTS | [Kokoro-82M model card](https://huggingface.co/hexgrad/Kokoro-82M) / [kokoro](https://github.com/hexgrad/kokoro) | Apache-2.0、82M 参数、含普通话路径，适合低成本 PoC | G2P、espeak-ng 和实际 voice/训练数据仍纳入 SBOM 与法务检查 |
| 轻量中文 TTS | [MeloTTS](https://github.com/myshell-ai/MeloTTS) | 官方 MIT，支持中文中英混合与 CPU 实时推理 | 以真实长博客验证自然度、专名和 15 分钟稳定性；固定合法音色 |
| 受限 TTS 参考 | [ChatTTS](https://github.com/2noise/ChatTTS)、[F5-TTS](https://github.com/SWivid/F5-TTS)、[Fish Speech](https://github.com/fishaudio/fish-speech)、[XTTS-v2](https://huggingface.co/coqui/XTTS-v2)、[Piper](https://github.com/OHF-Voice/piper1-gpl) | 技术上成熟度各异，但权重非商用、需另签商业许可或 GPL/逐 voice 合规风险明显 | 不进入首版生产默认；具体证据与条件见 §11.4 |

不建议首版引入一体化“Podcast 下载器”作为核心域模型：解析容易，真正复杂的是身份、权限、多产物版本、成本和下架。开源组件应藏在 provider adapter 后，领域状态由 Dorami 自己掌握。

## 13. 版权、隐私与安全

### 13.1 权利原则

- **RSS 可访问 ≠ 获得复制、改编、公开传播或商业使用授权。** AIHOT 的官方 agent 页也明确提示 RSS 访问不授予公开/商业再分发权。
- 默认 `link_only`：可索引必要元数据、链接/播放原 enclosure，衍生处理全部关闭。
- 许可标签只是证据之一；Creative Commons 的 ND（禁止演绎）、NC（非商业）及地域/期限都要按实际使用方式判断。
- 官方逐字稿也可能只授权辅助访问，不代表可以公开全文、翻译或生成音频。
- 获取邮件/合同授权时记录用途：内部处理、登录用户展示、公开网页、公开 RSS、商业化、训练用途分别勾选。
- 每个生成产物包含创作者归因、原链接、生成时间、pipeline/model 版本、AI 提示和纠错/投诉入口。
- 收到下架请求时先 `unpublish` 和吊销签名 URL，再停止作业并排队物理删除；保留最少审计元数据，不保留被撤权正文/音频。
- 法务未确认前，本文不是法律意见；公开衍生内容功能旗标默认关闭。

### 13.2 音色与人格权

- 使用 Dorami 自有或供应商明确允许商用/分发的中性音色。
- 不从原节目抽取主持人 voice embedding，不让用户上传名人声音，不在文案中暗示原主持人录制了精华。
- 音频头尾和播放器显示“AI 中文精华”；保留原节目入口。

### 13.3 抓取与媒体安全

- Feed/enclosure/transcript/chapters URL 全部经过 SSRF 防护：只允许 http/https；DNS 解析后拒绝 loopback、link-local、private、metadata IP；每次重定向重新校验。
- 限制 feed、transcript、image、audio 的字节数、连接/读取超时、重定向次数和压缩比；Content-Type 与魔数同时检查。
- HEAD 不可信，实际流式下载仍逐块累计并在超限时终止。
- FFmpeg 在无网络、低权限、临时目录、CPU/内存/墙钟配额的隔离 worker 运行；禁止拼接用户参数。
- 清洗 show notes/HTML，禁止脚本、事件属性、危险 URL；图片代理/白名单策略沿现有 Reader。
- 对象存储默认私有、服务端加密、短时签名 URL、Range 权限校验；日志不记录完整签名 URL。

### 13.4 AI 与数据安全

- 逐字稿和 show notes 是不可信输入；系统 prompt 明确忽略其中的指令，生成任务无工具/网络权限，只能引用提供的 segment IDs。
- JSON Schema 校验、长度限制和 allowlist 字段；Markdown 渲染继续做 XSS sanitize。
- 向外部 ASR/LLM/TTS 发送前按策略移除 feed 中的 email、token query 和非必要账户信息；私有 feed 凭据永不进入模型 prompt。
- 供应商需配置数据保留/训练选项；敏感/内部源可强制走本地 provider。
- 管理员错误信息脱敏；用户只见稳定错误码和可继续使用的原节目入口。

## 14. 质量门与评测

建立 30–50 集基准集，覆盖：普通话访谈、中英混合、多人抢话、口音、远场、音乐/广告、60–180 分钟、已有/没有官方逐字稿。

| 阶段 | 指标 | 初始验收建议 |
| --- | --- | --- |
| ASR | 中文 CER、英文 WER、专名准确率、时间戳偏差、说话人 DER | 以供应商 A/B 相对最优 + 人工可读为先；上线阈值在基准后冻结 |
| 摘要 | 关键观点召回、无证据陈述率、引用可达率、重复率 | 所有事实性段落至少一个有效 segment；引用可达率 ≥99% |
| 博客 | 中文可读性、压缩比、归因、禁用内容 | 编辑 1–5 分均值 ≥4；重大事实错误为 0 才可自动发布 |
| 脚本 | 字数、口语自然度、数字/专名读法 | 不读 URL/Markdown；关键 takeaway 覆盖 ≥90% |
| 音频 | 时长、截断、静音、响度、播放兼容 | `duration <= 900s` 100%；主流桌面/移动浏览器可 Range 播放 |

每次 prompt/model/pipeline 版本升级先跑离线回归；指标下降或成本上升超过阈值不得自动切流。自动发布只开放给稳定 source allowlist，其余进入 `awaiting_review`。

## 15. 可观测性与产品指标

### 15.1 运行指标

- Feed：成功率、304 比例、更新延迟、解析错误、重复率、连续失败源数。
- Queue：各 stage backlog、最老任务年龄、p50/p95 端到端时延、lease 回收数。
- ASR：分钟数、real-time factor、供应商错误/429、平均置信度、升级率。
- LLM：input/output token、schema 失败、证据校验失败、重试/升级率。
- TTS：字符数、真实时长/估算偏差、合成重试、静音/截断失败。
- Cost：每单集/每分钟/每 source/每日每月实际成本，预算拒绝数，缓存复用节省。
- Rights：link-only 数、可衍生数、待复核、即将到期授权、下架 SLA。

建议 SLO：

- Podcast feed 抓取成功率 ≥99%（排除明确 4xx/已停更）。
- 已授权且门控通过的 60 分钟单集，文字精华 p95 在 30 分钟内；精华音频 p95 在 60 分钟内。M0 可放宽，以实测定最终 SLO。
- `published` artifact 的无效/越权访问为 0。
- 下架请求确认后 15 分钟内不可访问，24 小时内完成对象删除或进入可审计重试队列。

### 15.2 产品指标

- Podcast 订阅转化、7/30 日留存、继续听完成率。
- 原节目播放与精华播放的选择比例、精华播放完成率、从精华跳回原时间戳率。
- 精华博客阅读完成/收藏/纠错率。
- `requested_digest / eligible_long_episode`，用于判断 TTS 是按需还是预生成。
- 每个“完整收听等价节省分钟”的成本：`processing_cost / max(original_duration - digest_duration, 0)`。
- 来源多样性和单一节目占比，防止推荐被高频节目淹没。

## 16. 分期开发计划

以下计划以 §2.3 的“metadata + 播放器 MVP”作为已完成基线；凡涉及新表、权限、处理和生成的条目均为后续工作。

### Baseline：Podcast metadata + 播放器 MVP（本次已实现，无迁移）

- 已用 `PodcastEpisodeContent → ArticleRecord.extensions_json` 验证现有文章存储可承载轻量 Podcast 元数据。
- 已接入 `generic_podcast_rss`、第四种 source shape、列表/详情 `podcast` 投影，以及桌面/移动原音频播放器。
- 已完成后端解析、路由、shape、序列化和 30 分钟边界测试；当前 `processing_eligible` 只是 UI/未来流水线提示。

验收边界：能配置可信 Podcast RSS、抓到单集、在 Reader 的 Podcast 容器播放原音频；不把“精简版状态/URL 可展示”误报为已经具备生成能力。

### Phase 0：决策与 Spike（3–5 天）

- 确认首批 10–20 个播客 RSS、受众、权利策略和是否允许衍生文字/音频。
- 选 10 集基准，验证 RSS/iTunes/Podcasting 2.0 解析、duration、enclosure Range。
- 对 3 集中文/中英混合长播客跑 AssemblyAI Universal、`gpt-4o-mini-transcribe` 与 SenseVoice/faster-whisper；人工记录 CER/专名/时间戳/耗时/成本。
- 用现有 LLM 生成结构化摘要、博客和播音稿；只生成内部测试音频，不发布。
- 输出 go/no-go：默认 provider、单集预算、质量阈值、数据保留和法务边界。

验收：一条命令/管理动作可对固定样本产生可复现 artifacts、成本明细和 QA 报告；删除样本后无残留公开 URL。

### Phase 1：Podcast 领域化与完整专栏（未来，1–2 周）

- Alembic 新增 `PodcastEpisodeRecord`、`PodcastRightsRecord`、`PodcastPlaybackStateRecord`；把兼容投影迁移为领域事实源，保留旧 `extensions_json` 读取过渡期。
- 增加 RSS preview/import、canonical 去重、节目/单集专用 API 与节目层级；复用既有 SourceState 健康度。
- 在已有 Reader 播放器之上增加 Podcast 首页、节目页、单集页、跨页播放、服务端播放进度与原/精华双时间轴基础。
- 下载并解析官方 transcript/chapters/person/license，提供分段分页与时间轴跳转；仍不自动执行 ASR/TTS。
- 统一搜索继续使用已加入的 `podcast_episode` 类型，并补节目维度搜索与权限过滤。

验收：导入 10 个源，重复导入幂等；migration 前后的元数据无损；新增单集自动出现；桌面/移动连续播放和恢复进度；恶意/内网 URL 被拒；link-only 源没有生成入口。

### Phase 2：长播客文字精华（1–2 周）

- `PodcastProcessingRecord`、artifact、segment、状态机和 Job 集成。
- `>30min`、rights、质量、预算门控；官方 transcript 优先、托管 ASR fallback。
- 分层摘要、证据验证、精华博客渲染、管理员 review/publish/unpublish。
- 运行/成本 dashboard、重试/断点续跑、撤权下架。

验收：短于等于 30 分钟绝不自动 ASR；官方逐字稿不重复计费；断网/429 后可恢复且不产生重复 artifact；每个事实段落能跳转原音频；未授权内容不可发布。

### Phase 3：≤15 分钟精华音频（约 1 周）

- TTS adapter、固定音色、脚本规范化、ffmpeg packaging、duration/响度 QA。
- 原节目/精华双时间轴、持久播放器、按需生成按钮和预算额度。
- 对授权 source allowlist 开放站内发布；可选鉴权 RSS。

验收：全部测试音频 `<=900s`；播放器明确 AI 标识且不冒充主持人；Range/seek/倍速可用；超长脚本最多重试 2 次；撤权后签名 URL 立即失效。

### Phase 4：发现、导入与规模化（1–2 周）

- Podcast Index 搜索、外部平台 URL → canonical RSS 辅助解析。
- 内网博客 RSS 的 OPML/CSV/JSON 批量 preview/confirm，与 Podcast 导入分轨。
- 推荐配额、精华需求信号、source 月预算、provider 路由与自托管评估。
- Archive Sync v2 asset 方案只在确有跨环境音频需求时实施。

验收：目录不可用时已订阅 RSS 仍正常更新；内部博客批量导入不会触发 Podcast 管线；预算耗尽优雅降级为原节目；成本和使用率能回答是否值得预生成 TTS。

## 17. 端到端验收清单

### 17.1 功能 Happy Path

1. 管理员 preview 一个带 `podcast:transcript`、chapters、license 的 60 分钟 feed。
2. 确认导入后节目与单集入库，重复确认不增加记录。
3. rights 设为允许内部衍生文字/音频，触发 processing。
4. 流水线跳过 ASR，规范化官方逐字稿，产出带证据的中文博客。
5. 按需生成精华音频，ffprobe 结果 ≤900 秒，发布到 authenticated audience。
6. 普通用户订阅节目、播放原节目、从博客 `[12:34]` 跳转、切换精华并恢复各自进度。
7. 管理员 unpublish，旧签名 URL 立即不可用；审计日志完整。

### 17.2 Fallback Path

- 无官方逐字稿的 61 分钟单集：只在 rights + budget 通过后下载，ASR 成功后删除临时原音频。
- 29:59、30:00：不自动生成；30:01：进入后续门控。
- duration 缺失：probe 后再判断；probe 失败不盲目下载全文件。
- enclosure 302 到私网/超大文件/错误 MIME：拒绝并记录稳定错误码。
- ASR 429/超时：退避恢复；重复投递只产生一次实际账单调用或被幂等保护。
- LLM 伪造 segment ID：schema/evidence validator 阻止发布。
- TTS 生成 15:20：删减脚本重试，不用粗暴 1.2x 加速冒充通过。
- Podcast Index/Listen Notes 不可用：现有订阅正常，只有目录搜索降级。
- rights 从 allow 改为 deny：停止队列、取消发布、吊销 URL、排队删除。

### 17.3 回归

- 现有 RSS article、GitHub、arXiv、WeChat 抓取不受 `source_type = podcast` / `generic_podcast_rss` 影响。
- `shape=article|bulletin|social` 旧 API 行为保持；新增 podcast 值需显式扩展 schema 与前端类型，不静默归为 article。
- 用户私人 `user_rss_` 仍不进入公共目录/Daily Brief/Archive Sync。
- Article FTS、订阅 only/prioritize、read state、Daily Brief 对 podcast 的加入均由 feature flag 控制。
- Migration upgrade/downgrade、SQLite 约束、并发 job lease 与大 transcript 分页测试通过。

## 18. 上线开关与决策点

建议功能旗标：

```text
podcast_catalog_enabled
podcast_directory_search_enabled
podcast_transcription_enabled
podcast_digest_text_enabled
podcast_digest_audio_enabled
podcast_digest_public_distribution_enabled
podcast_self_hosted_asr_enabled
podcast_self_hosted_tts_enabled
```

开始实现前需要产品/法务明确：

1. “内部播客”的目标受众是内网、登录用户，还是公开互联网？三者不能共用默认授权。
2. 首批播客是否有明确的转录、翻译、改编与音频分发授权？若没有，MVP 只能做 link-only + 官方 transcript。
3. 精华音频采用全合成平台声，还是原声剪辑？本文强烈建议首版全合成平台声；原声剪辑需单独授权。
4. TTS 是编辑精选/用户请求后生成，还是所有 eligible 单集预生成？建议先按需，数据证明需求后再预生成。
5. 精华博客是否进入公共搜索、Daily Brief 和 Archive Sync？建议按 artifact audience 显式控制，不能随 ArticleRecord 自动泄露。

## 19. 推荐最终方案

如果只选择一条最稳、性价比最高的路线：

1. 先上线 Podcast RSS 专栏和原音频播放，Podcast Index 只负责发现。
2. 首批只处理团队精选且有授权的节目；`>30min` 后再经过质量/预算门控。
3. 优先用官方逐字稿，否则在 AssemblyAI Universal（公开价低至 `$0.15/hour`）与 `gpt-4o-mini-transcribe`（公开估算 `$0.003/min`）中以中文实测选默认；不要与 `$0.0045/min` 的 `gpt-transcribe` 混写，质量不足的片段再升级或转 SenseVoice PoC。
4. 用现有 OpenAI-compatible LLM 做“分段 map → 带证据 reduce → 播音稿 rewrite”，每个结论绑定原节目 segment/timecode。
5. 文字精华先发布；只有编辑精选或用户明确请求时 TTS。
6. TTS 使用固定、获授权的中性中文声音，目标 12–14 分钟，实际时长 >15 分钟必须重写。
7. 所有处理均可幂等、可重跑、可审计、可撤权；原音频不永久镜像，生成资产存私有对象存储。

这条路线把昂贵和高风险的步骤推迟到确有价值的单集，同时保留未来自托管 ASR/TTS、公开精华 RSS 和 agent API 的扩展空间。

## 20. 参考资料索引

除竞品逐项链接外，本设计直接依赖：

- Dorami [Issue #7](https://github.com/zlzfun/DoramiSourceArchive/issues/7)
- [Podcast Namespace specification repository](https://github.com/Podcastindex-org/podcast-namespace)
- [Podcasting 2.0 overview](https://github.com/Podcastindex-org/podcast-namespace/blob/main/podcasting2.0.md)
- [Podcast Namespace XSD](https://github.com/Podcastindex-org/podcast-namespace/blob/main/podcast.xsd)
- [Podcast Index API docs](https://podcastindex-org.github.io/docs-api/)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [Deepgram pricing](https://deepgram.com/pricing)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [WhisperX](https://github.com/m-bain/whisperX)
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)
- [FunASR](https://github.com/modelscope/FunASR)
- [CosyVoice](https://github.com/QwenAudio/CosyVoice)
- [Kokoro-82M model card](https://huggingface.co/hexgrad/Kokoro-82M) / [Kokoro inference library](https://github.com/hexgrad/kokoro)
- [MeloTTS](https://github.com/myshell-ai/MeloTTS)
- [ChatTTS](https://github.com/2noise/ChatTTS)
- [F5-TTS](https://github.com/SWivid/F5-TTS)
- [Fish Speech license](https://github.com/fishaudio/fish-speech/blob/main/LICENSE)
- [XTTS-v2 docs](https://docs.coqui.ai/en/stable/models/xtts.html) / [XTTS-v2 model license](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)
- [Piper current engine](https://github.com/OHF-Voice/piper1-gpl) / [Piper voices](https://huggingface.co/rhasspy/piper-voices)
- [FFmpeg](https://ffmpeg.org/)

所有外部事实与价格最后核验日期均为 **2026-09-02**。产品上线前应再次核对价格、供应商数据条款、模型/权重许可证和 Podcast Namespace 最新版本。
