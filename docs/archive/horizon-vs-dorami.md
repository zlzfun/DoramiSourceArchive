# Horizon vs DoramiSourceArchive — 抓取与日报生成原理对比报告

> 对比对象：[Thysrael/Horizon](https://github.com/Thysrael/Horizon)（commit 截至 2026-06，本地浅克隆阅读）
> 视角：抓取（fetch/scrape）与日报（daily briefing）生成两条主线。

---

## 一、Horizon 是什么 —— 一句话原理

Horizon 是一个**无状态的、配置驱动的「个人新闻雷达」CLI**：一条命令（`uv run horizon --hours 24`）跑完一个**线性单程流水线**，把过去 N 小时内多源内容抓回来 → 打分筛选 → 去重 → 联网补背景 → 渲染成中英双语 Markdown → 分发到 GitHub Pages / 邮件 / Webhook / MCP，然后进程退出。它**不建数据库、不做长期归档、不做向量检索**，每天的产物就是 `data/summaries/` 下的一份 Markdown。

它的核心流水线（`src/orchestrator.py::run`）：

```
fetch_all_sources (并发抓取)
  → merge_cross_source_duplicates (URL 归一跨源合并)
  → analyze (LLM 逐条打分 0-10 + 摘要 + 标签)
  → filter (score ≥ threshold)
  → merge_topic_duplicates (LLM 话题级语义去重)
  → expand_twitter_discussion (高分推文二次抓回复并重打分)
  → apply_balanced_digest (分类配额 + 总量上限)
  → enrich (概念提取 → DuckDuckGo 联网检索 → LLM 生成背景/社区讨论，双语)
  → summarize (纯程序化模板渲染 Markdown，无 LLM)
  → deliver (Pages / Email / Webhook / MCP)
```

---

## 二、抓取原理对比

### 2.1 Horizon 抓取层

| 维度 | Horizon 实现 |
|---|---|
| 抽象 | `BaseScraper`（`src/scrapers/base.py`，仅 47 行）：`async fetch(since) -> List[ContentItem]`，构造时注入**共享的** `httpx.AsyncClient` |
| 数据模型 | 统一 `ContentItem`（title/url/content/author/published_at/**metadata dict**/ai_score/ai_tags…），各源差异塞进 `metadata` |
| 源类型 | HackerNews、RSS/Atom、Reddit、Telegram、Twitter/X（Apify 或 Playwright+Cookie 双模式）、GitHub（用户事件+release）、OpenBB（金融新闻）、OSSInsight（trending repo） |
| 时间窗口 | 抓取以 `since`（now - hours）为硬过滤，**只要增量**，天然契合「日报」语义 |
| 并发 | `asyncio.gather(*tasks, return_exceptions=True)` 源级并发；HN 还做 story+comment 两级并发 |
| 评论/讨论 | HN/Reddit/Twitter 会抓 top-N 评论，拼进 `content` 的 `--- Top Comments ---` 段落，供后续打分与「社区讨论」摘要 |
| 配置 | 全部来自一份 `data/config.json`（源、阈值、模型、语言、交付）；支持 `${ENV_VAR}` 占位 |
| 选源向导 | `horizon-wizard`：用户用自然语言描述兴趣 → `ai_recommend.py` 让 LLM **推荐订阅源**，再叠加 preset 库 |

特点：**轻、瞬时、面向「本次运行」**。没有 fetcher 注册中心、没有健康度追踪、没有调度器（调度交给外部 GitHub Actions cron）。

### 2.2 本项目抓取层

| 维度 | DoramiSourceArchive 实现 |
|---|---|
| 抽象 | `BaseFetcher` + `BaseWebPageListFetcher` + `GenericGitHubReleasesFetcher` 三层；`FetcherRegistry` **import 时自动发现** `impl/` 下子类，`get_parameter_schema()` 驱动前端动态表单 |
| 数据模型 | 双维身份 `content_type`（数据形态）× `source_id`（渠道）；`BaseContent` 子类 + `extensions_json` 序列化；落 **SQLite（SQLModel）** 持久化归档 |
| 源类型 | RSS（23+ preset）、GitHub release/repo、HuggingFace model、网页列表抓取、curated 精选源、webhook 等，全部**插件化登记** |
| 持久化 | `DataPipeline → DatabaseStorage`，再**独立一步**向量化进 ChromaDB（admin 管控）；游标式增量 + 幂等 |
| 调度/可观测 | Collection Jobs（多节点可保存可调度）、`FetchRunRecord`/`SourceStateRecord` 运行与健康追踪、进度上报 |
| 反爬 | `PlaywrightRenderer` 按需启浏览器过 Cloudflare 挑战（仅 OpenAI 源用），未用则不启 |
| 角色 | 抓取/归档属 collector(admin) 面；reader(user) 只消费已归档内容 |

特点：**重、长期、面向「归档中枢 + 分发平台」**。是一个有状态的 CMS。

### 2.3 抓取对比小结

- **定位差异是根因**：Horizon 抓完即用即弃（stateless digest），本项目抓完入库长期沉淀（stateful archive + RAG）。
- Horizon 在**源覆盖广度**（Reddit/Telegram/Twitter/OpenBB/OSSInsight）和**评论/社区讨论抓取**上更强；本项目在**插件化注册、参数 schema 驱动 UI、健康追踪、调度、向量归档**上更工程化。
- Horizon 的 `since` 时间窗是抓取层的一等过滤；本项目时间窗在日报的游标里，抓取层更偏「尽量全量入库」。

---

## 三、日报生成原理对比（核心差异）

### 3.1 两者流水线对照

| 阶段 | Horizon | 本项目（`daily_brief.py`） |
|---|---|---|
| 候选来源 | **本次实时抓取**的全部条目 | 已归档库中，**游标 `daily_brief_cursor` 之后**新入库的文章（`max_total=120` 上限） |
| 打分 | `ContentAnalyzer` 逐条 LLM 打分 0-10（含评论/互动信号），并发 | `map_summarize` 逐条 LLM **概括+打分**（中文 schema：title_cn/classification/realm/summary/comment/score），并发 |
| 筛选 | `score ≥ threshold`（默认 7） | 不设硬阈值，`select_top` 按分排序 |
| 去重 | ① URL 归一**跨源合并** ② LLM **话题级语义去重**（评论并入主条目） | ① 确定性**游标**（防重复处理） ② reduce 时**注入近期 3 天日报正文**让 LLM 语义折叠同一事件 |
| 多样性 | `balanced_digest`：分类配额 `category_groups` + `max_items` 上限 | `select_top`：`per_source_cap=5` + `per_realm_cap=8`，配额不足用 overflow 补满 |
| 补充 | `enrich`：**概念提取 → DuckDuckGo 联网检索 → LLM 生成 background/社区讨论**（双语，带引用 sources） | 无独立 enrich 阶段；信息只来自已归档正文 |
| 汇总渲染 | **纯程序化模板渲染**（`DailySummarizer`，零 LLM、确定性、零幻觉） | `reduce_to_markdown`：**单次 LLM** 把择优条目汇编成 Markdown（灵活，但有成本/格式漂移/幻觉风险，靠 prompt 约束） |
| 语言 | **中英双语**各生成一版 | 仅中文 |
| 持久化 | 写文件 + 拷进 `docs/_posts` 供 Pages | 幂等写库为 `daily_brief` content 记录（可被订阅/检索/再分发） |
| 成本观测 | `get_usage_snapshot()` 统计每次运行 token（分 provider） | 无 token 统计 |

### 3.2 最关键的一处架构分歧：「LLM 放在哪一步」

- **Horizon**：把所有 LLM 智能**前置到 per-item**（打分、话题去重、逐条 enrich 出结构化双语字段），**最终 Markdown 完全是确定性模板拼接**。优点：输出格式 100% 可控、无 reduce 幻觉、可逐条缓存、便于多渠道复用同一份结构化数据。
- **本项目**：把「跨条目的编排/分类/事件级去重/篇幅取舍」交给**一次 reduce LLM**。优点：日报更像「主编手写」、能跨条目做叙事与合并；代价：reduce 可能漂移格式、引入未出现事实（已用强 prompt 约束「忠实于给定条目」缓解）、长输出 token 成本、难逐条缓存。

这不是谁对谁错，而是「**确定性渲染 vs 生成式编排**」的取舍。

---

## 四、本项目值得向 Horizon 学习的点（按性价比排序）

### ⭐ 高价值

1. **二段式 enrich + 外部/内部 grounding 补背景**
   Horizon 对高分条目做「概念提取 → 联网检索 → 基于检索结果生成背景知识」，让读者无需领域知识也能看懂。本项目 reduce 只吃已归档正文，缺「背景」维度。
   **落地建议**：在 map 与 reduce 之间加一个可选 `enrich` 阶段——但 grounding 来源**优先用本项目自有的 ChromaDB/RAG**（`/api/rag/context`）而非 DuckDuckGo，这正好把已有向量库变现为日报的「背景知识库」，是本项目独有优势。

2. **「确定性渲染」作为 reduce 的降级/可选路径**
   把每条的 `summary/comment/realm` 等结构化字段用**程序化模板**直接渲染成 Markdown（如同 `DailySummarizer`），仅在需要「跨条目叙事/事件合并」时才调 reduce LLM。
   **收益**：提供一条**零 LLM 成本、零幻觉、格式 100% 稳定**的日报生成路径（LLM 不可用或省钱时降级）；map 阶段已产出全部结构化字段，几乎零额外开发。

3. **生成前的批内显式去重（跨源 URL 归一 + 话题级语义）**
   本项目目前靠「游标 + 注入近期日报」做**跨天**去重，但**单次批内**同一事件多源并存未显式合并。可移植 Horizon 的 `merge_cross_source_duplicates`（URL 规范化）+ 一次轻量 LLM 话题去重，把重复条目在进 reduce 前合并，省 token 也更干净。

4. **社区讨论/评论维度纳入日报**
   Horizon 抓 HN/Reddit/Twitter 评论并由 LLM 总结「社区观点」。本项目已有 HackerNews fetcher（含热度），可在 map 的 `comment` 或新增字段里纳入「社区讨论摘要」，提升点评深度。

### ⭐ 中价值

5. **中英双语日报**：一套择优条目，reduce/渲染各出中英两版（Horizon 的 `languages` 配置 + per-lang 字段）。面向有海外/团队读者的场景。

6. **多交付渠道：Webhook 推送 + 邮件订阅**
   Horizon 支持飞书/钉钉/Slack/Discord webhook 与 SMTP/IMAP 邮件（自动处理订阅/退订）。本项目目前靠 feed token / MCP / skill 拉取，可补「主动推送」：日报生成后推一条飞书/Slack 卡片，或给订阅用户发邮件。

7. **运行 token 用量统计**：`get_usage_snapshot()` 按 provider 汇总每次 token 消耗。本项目日报无成本观测，加一个轻量统计便于调参与成本治理。

8. **「先粗筛后深挖」的两阶段成本控制**
   Horizon 对 Twitter 先粗抓打分，仅对**高分**条目二次抓回复并重打分（`max_tweets_to_expand` 兜底）。本项目 map 对全部有正文候选一律全量并发概括；可借鉴「先用标题/摘要轻量粗筛 → 仅高分条目再取全文/深度概括」，在大批量时显著省 token。

### ⭐ 锦上添花

9. **按兴趣 AI 推荐订阅源**（`horizon-wizard` + `ai_recommend.py`）：用户用自然语言描述兴趣，LLM 推荐应订阅的源。可作为 reader 端「发现更多来源」的智能版——让 LLM 基于用户已订阅源和一句兴趣描述，从 registry 里推荐候选 `source_id`。

10. **配置里的 `${ENV_VAR}` 占位**与 GitHub Pages 静态站交付：轻量、适合个人/开源展示场景。

---

## 五、反向：本项目相对 Horizon 的固有优势（不应丢失）

为避免「学习」时把自身长板削平，记录本项目领先处：

- **持久化归档 + 向量 RAG**：Horizon 无状态、不建库；本项目是可检索的内容中枢，日报只是其上一个消费者。
- **插件化 fetcher 自动发现 + 参数 schema 驱动 UI**：扩展源的工程化程度更高。
- **双轴角色权限 + 订阅分发 + 阅读器**：面向多用户/团队，Horizon 是单人 CLI。
- **调度（Collection Jobs）+ 健康追踪 + 运行记录**：可观测、可运维。
- **游标式增量 + 幂等写库 + collector→reader 归档同步**：分布式部署能力。

> **结论**：Horizon 的精华在「**per-item 智能前置 + 确定性渲染 + 联网 grounding + 多渠道交付**」这条**轻量生成式日报**链路上；本项目的精华在「**有状态归档 + RAG + 多用户分发**」的平台能力上。最高性价比的吸收方式，是把 Horizon 的 enrich/确定性渲染/批内去重/社区讨论这几个**日报质量增强点**，嫁接到本项目已有的 RAG 与归档底座上——用自家向量库做 grounding，而不是简单照搬其无状态形态。
