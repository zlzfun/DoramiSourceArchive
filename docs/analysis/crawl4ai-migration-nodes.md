# crawl4ai 待切换节点清单（阶段二/三/四 跟踪表）

> 配套文档：[`crawl4ai-feasibility.md`](./crawl4ai-feasibility.md)（可行性与路线图）。
> 本表盘点 `src/fetchers/impl/` 全部抓取节点，给出**当前实现、迁移分类、切换注意事项**，作为逐节点切换前的总账。
> 状态列：⬜ 未开始 / 🟡 调优中 / ✅ 已切换。本表随阶段二推进逐行更新。

## 进度快照（截至 2026-06-24）

**阶段一 · 旁路接入** ✅ 完成：`src/fetchers/web_content/`（`WebContentBackend` 接口 + `LegacyArticleExtractorBackend` + `Crawl4AIContentBackend` + `profiles.py` + `compare.py`）；`scripts/compare_web_backends.py` 旁路对比；crawl4ai 设为 `pyproject` 可选 extra；`tests/test_web_content_backend.py`。

**阶段二 · B 类详情页迁移** ✅ 5/6 完成（已 e2e 确认走 `crawl4ai:*`）：
- `web_anthropic_news`、`web_ithome_ai`、`web_qbitai`、`web_claude_blog`、`web_aiera`
- `web_cursor_changelog` 🟡 暂留 legacy（单页多条，类 C 类，留阶段四）

**集成方式**：`BaseFetcher.web_backend_enabled` 开关（默认 False）→ `fetch()` 按需起停浏览器后端 → `_detail_for_url` 经 `_web_backend_detail()` 路由，未装 crawl4ai/未命中 Profile/失败均回退 legacy。crawl4ai 是可选依赖，默认环境（无它）行为不变，194 项测试全过。

**两轮人工测试修复的关键问题**（详见文末"配置陷阱"）：
1. markdown 改由项目自有 `node_to_markdown` 在 crawl4ai `cleaned_html` 上生成（修复懒加载占位图 / 图片被 `fit_markdown` 裁掉）；
2. 关闭 `remove_overlay_elements`/`remove_consent_popups`（误删正文题图）；
3. 多顶层 `<article>` 取最长（修复 Anthropic SSR 正文重复）；
4. 容器尾部噪声用稳定特征排除（`[class*="LinkGrid"]` / Webflow `w-condition-invisible`）。

**阶段四 · C 类单页拆条** ✅ 完成（结论：**不按原计划迁移，改加渲染兜底**）：
- **诊断结论**：逐个跑 9 个 C 类节点的 httpx 路径，**全部产出 6–12 条结构完整、正文充实的条目**——它们都是服务端渲染，httpx 已最优。按「httpx 优先」原则，这些节点没有要解决的 JS/Cloudflare 渲染问题，不应上浏览器。
- **原计划被推翻**：原设想「crawl4ai 取 `cleaned_html` + 保留 Segmenter」对 C 类是**净倒退**——这些 Segmenter 吃的是原始 HTML 的结构锚点（Codex `<li data-product>`+`id`、Claude Code Mintlify `data-component-part`、Devsite `<h2 id>` 日期标题、HF 论文卡片），而 `cleaned_html` 会剥属性/重构，直接打断拆条。
- **改为加「渲染兜底」**：给 `SinglePageDocumentFetcher` 加 `_segment_with_render_fallback()`——httpx 文本拆出 0 条（页面将来改成 SPA、httpx 拿到空壳）且 crawl4ai 可用时，**惰性**起浏览器取 `render_html()` 返回的**原始** `result.html`（保留全部锚点，**非** cleaned_html），喂回**完全不动的同一 Segmenter**。日常 httpx 即可，浏览器**不常驻**（零快路径成本），仅失败时按需启停。
- **e2e 已验证**：强制 httpx 空壳后，DeepSeek/Claude Code 经渲染兜底拆出的条目与 httpx 路径**首条字节一致**（Mintlify 的 `data-component-part` 锚点在原始 `result.html` 中完整保留，印证用 `result.html` 而非 cleaned_html 的必要性）。
- `web_cursor_changelog` 仍留 legacy（它是 `BaseWebPageListFetcher`，遗留问题是**详情页**单页多版本，属 legacy httpx 解析优化，与 crawl4ai 无关）。

**遗留（非迁移引入，待单独立项）**：量子位/新智元图床防盗链（阅读器侧需图片代理）；阅读器对 `.svg` 题图的渲染过滤；`web_cursor_changelog` 详情页按日期切分细化。

**阶段三 · OpenAI News（Cloudflare）** ✅ 完成：`rss_openai_news` 详情渲染**优先 crawl4ai**，**Playwright 保留为 fallback**，httpx→summary 为最终降级。
- **三级渲染链**（`OpenAINewsRssFetcher._detail_for_url`）：① crawl4ai `render_html()` 带 CF 等待条件（`js:` 等正文文本 >1200，与 PlaywrightRenderer 同口径）→ ② Playwright 自渲染（懒启动、一次/run 复用）→ ③ httpx 详情 → 通用 RSS 逻辑降级 summary。每层渲染产出的 HTML 都喂给**同一** `extract_detail_from_html` + `_strip_render_placeholders`，故 fallback 与主路提取完全等价。
- **浏览器开销**：crawl4ai 主路常驻一次/run；Playwright 仅在 crawl4ai 不可用或逐篇失败时**懒启动**，不双开浏览器。
- **e2e 实测**：crawl4ai **成功过 CF**——2/2 真实 OpenAI 文章拿到完整正文（6450c / 7712c），method=`crawl4ai_html_selector`，`Loading…` 占位行被剔除。
- **尾部噪声修复（人工测试发现）**：OpenAI 文章 `<article>` 内，正文块之后还有作者署名 `<section>`（“… GPT Author OpenAI”）与“Keep reading / View all”相关推荐块，会被 `extract_detail_from_html` 一并抓入。类名全是 Tailwind 哈希、无语义选择器；版式稳定为「正文是 `<article>` 直接子节点中文本最长者，其后同级子节点皆尾部噪声」→ `_strip_openai_trailers()` 删除正文块之后的全部同级子节点（两条渲染路共用此清洗）。实测正文 7712→7132c，末句正确收于「…privileged to do that.”」。
- **待补**：成功率/封禁率的**长期统计**（单次 2/2 通过，仍需上线后观察 CF 在 headless 下的稳定性；fallback 已就位以兜底）。

**全部阶段（一~四）已落地。** 剩余仅为运营观察（OpenAI CF 成功率长期统计）与中/高级目标（Profile Registry + AI Loop，对应路线图阶段五）。

## 集成接缝（切换的统一入口）

所有网页详情提取最终都收敛到一个方法，这就是阶段二的改造点——**不重写各 fetcher，只把这个方法路由到 `WebContentBackend`**：

- `BaseWebPageListFetcher._detail_for_url(client, url, max_chars)` （`webpage_fetcher.py:369`）及其子类覆盖
- `GenericRssFetcher._detail_for_url(...)`（`rss_fetcher.py:193/397`）
- `SinglePageDocumentFetcher` 取页 → 各 `_release_entries()/_paper_entries()` Segmenter（C 类）

切换方式：`_detail_for_url` 内优先调用注入的 backend；backend 不可用 / 正文不达标时**回退现有逻辑**（旁路保护）。
`_content_id(url)` 与发现逻辑（listing/分页/RSC/JSON API）**完全不变** → 稳定 ID、去重、回填语义不受影响。

## 跨节点通用注意事项

1. **只迁详情、不迁发现**：列表 URL/标题/日期发现（含 Anthropic 的 RSC、Qwen 的 JSON API、量子位/新智元分页）留在 Dorami；backend 只接收一个 URL 返回正文。
2. **C 类的关键风险（阶段四已定论）**：Segmenter（`_release_entries`/`_paper_entries`）吃的是 **httpx 原始 HTML 的结构锚点**（日期标题、版本卡片、论文卡片）。crawl4ai 的 `cleaned_html` 会剥掉这些锚点 → **结论：C 类不走 cleaned_html**。诊断证明 9 个 C 类 httpx 已最优，保留 httpx，仅在 httpx 空壳时用浏览器渲染的**原始** `result.html`（锚点完整）兜底重跑 Segmenter。
3. **过滤过度**：`fit_markdown`（`PruningContentFilter`）可能误删短规格/日期/代码。backend 已用 `min(profile, caller)` 阈值在 fit 偏短时回退 `raw_markdown`；对 changelog 类优先靠 `target_elements` 精确圈定而非全靠启发式。
4. **验收门槛**：切换前用 `scripts/compare_web_backends.py` 跑旁路对比，要求 **相似度 ≥ 0.8** 或人工核对正文实质一致 + 无噪声泄漏。当前 IT之家仅 0.62 → 必须先调 profile。
5. **Profile 覆盖**：现有 Profile 仅 3 个（anthropic/ithome/deepseek，`profiles.py`）。B/C 类每个域名都需补 Profile（`target_elements`/`excluded_selector`/`wait_for`）。
6. **`detail_extraction_method` 字段**：生产 `raw_data` 记录该字段，切换后变为 `crawl4ai:<profile>`。审计/下游若按此字段筛选需知悉（非破坏，仅取值变化）。
7. **空正文语义**：`drop_empty_content=True` 的节点，若 crawl4ai 误判空会丢条目 → 回退保护必须保留。
8. **JS/SPA**：需要渲染的节点要配 `wait_for`（+ 必要时 `scan_full_page`）；纯静态页按"httpx 优先"原则可暂不迁移。
9. **成本**：浏览器抓取重于 httpx，逐节点迁移而非一刀切；A 类协议源严禁绕道浏览器。

---

## A 类 · 协议/API 型 —— 保留现状（不迁移）

发现与正文都走 API/feed，crawl4ai 不介入（仅个别"缺正文补抓详情页"可选接入）。

| 状态 | source_id | 类 | 当前实现 | 备注 |
|---|---|---|---|---|
| — | `generic_rss` / `rss_google_gemini_models` / `rss_hn_ai` | RSS | feedparser，正文取 feed summary/content | 保留；缺正文补抓详情页可选走 backend |
| — | `generic_github_releases` + `github_opencode/openclaw/hermes_agent_releases` | GitHub API | Releases API | 保留 |
| — | `generic_github_repositories` + `github_deepseek_repositories` | GitHub API | Repo API + README 回填 | 保留 |
| — | `generic_huggingface_models` + `hf_deepseek_models` | HF API | Models API | 保留 |
| — | `web_qwen_blog` | JSON API | `qwen.ai/api/v2/article/retrieval`，正文多由 API 内联 HTML（`qwen_article_retrieval_html`），仅罕见兜底才抓详情页 | **归 A**：内容来自 API，crawl4ai 基本无关；仅兜底分支可选接入 |
| ✅ | `rss_openai_news` | RSS + **crawl4ai（主）/ Playwright（fallback）** | 发现走 RSS；正文渲染优先 crawl4ai 过 Cloudflare，失败退回 Playwright，再退回 summary | **阶段三完成**：发现保留 RSS，详情渲染优先级 crawl4ai→Playwright→httpx/summary；crawl4ai e2e 实测过 CF（2/2，6–7KB 正文）。仍需上线后做成功率/封禁率长期统计 |

---

## B 类 · 列表页 + 详情页 —— 高度适合迁移（详情走 crawl4ai Profile）

发现逻辑保留，仅 `_detail_for_url` 路由到 backend。`default_fetch_detail=True`。

| 状态 | source_id | 当前详情实现 | Profile | 切换注意 / 验收 |
|---|---|---|---|---|
| ✅ | `web_anthropic_news` | 通用 `extract_article_detail`（`html_selector`） | ✅ `anthropic-article` | 相似度 **0.97**；e2e 已确认走 `crawl4ai:anthropic-article`。**列表 RSC 解析（`_anthropic_news_entries`）保留**，crawl4ai 不保证发现 RSC 中未渲染的历史文章 |
| ✅ | `web_ithome_ai` | 专用 `_extract_ithome_detail`（`#paragraph.post_content`）+ `_detail_for_url` 覆盖 | ✅ `ithome-article` | 关 `PruningContentFilter` + 选择器加 `.post_content` 回退后相似度 **0.95**；e2e 已确认 |
| ✅ | `web_claude_blog` | 通用 `extract_article_detail` | ✅ `claude-blog` | Webflow 页**无 `<article>`**，正文在唯一 `<main>`；去 `wait_for=article`、改 `css:main` + locale=en 后相似度 **0.855** |
| 🟡 | `web_cursor_changelog` | `BaseWebPageListFetcher` 通用详情，列表分页 `_next_listing_page_url` | 🟡 `cursor-changelog`（未启用） | **暂留 legacy**：`locale=en` 修了 `/cn/` 翻译跳转，但 `/changelog/<date>` 是**单页多条**，`main` 会把整页多个版本都抓进来（相似度 0.43、1.83x）。本质类 C 类，需按日期切分或更细选择器，待阶段四处理 |
| ✅ | `web_qbitai`（量子位） | 专用 `_extract_qbitai_detail`（`.content .article` + 细致噪声排除） | ✅ `qbitai-article` | Profile `excluded_selector` 照搬专用规则（`.wx_img/.tags/.person_box/.xiangguan` 等）+ 关过滤；相似度 **0.978** |
| ✅ | `web_aiera`（新智元） | `BaseWebPageListFetcher` 通用详情 | ✅ `aiera-article` | WordPress 页有 9 个 `<article>`（含相关文章），精确到 `article .entry-content` 单容器、去 `wait_for`、关过滤后正文洁净（~1.1x，开头逐字相符） |

> **跨节点经验（批次 1+2 实证）**：① 浏览器默认 `text_mode`/系统 locale 会改变结果——已统一 `text_mode=False`(保图) + `locale=en-US`(防翻译跳转，`CrawlerRunConfig.locale`，非 BrowserConfig)；② 选错容器（如 `article` 命中多个、Webflow 的 `main` 含相关文章/CTA）会把噪声混入，须落到最细的正文容器（如新智元 `article .entry-content`、Claude `.blog_post_section_wrap`）；③ `wait_for` 选错选择器会 15s 超时报错，服务端渲染页可不设；④ **不要并发跑多个 crawl4ai 进程**（共享 `CRAWL4_AI_BASE_DIRECTORY` 会争用导致随机失败）。
>
> **关键架构修正——markdown 由 `node_to_markdown` 生成，而非 crawl4ai 自带 markdown**：crawl4ai 的 `raw/fit_markdown` 在图片上有两个硬伤——(a) 取 `<img src>`，对懒加载站点（IT之家 src=占位图 `t.png`、真图在 `data-original`）会得到占位图/坏图；(b) `fit_markdown`(PruningContentFilter) 会把低文字密度的图片整张裁掉（Anthropic 的题图）。修复：后端改为取 crawl4ai 的 `cleaned_html`（已按 target/excluded 圈定去噪、**且保留 `data-original`**）→ 喂给项目自有的 `node_to_markdown`（其 `_pick_image_src` 优先 `data-original`、识别占位图）。这样**两条路径(legacy/crawl4ai)图片与 markdown 处理完全一致**，旁路相似度随之跳到 IT之家 1.0 / 量子位 0.999 / Anthropic 0.974。`use_content_filter`/`PruningContentFilter` 因此不再用于正文，仅 `target_elements`+`excluded_selector` 负责圈定。
>
> **非迁移引入的遗留问题（阅读器渲染侧）**：量子位/新智元的图床对 `/wp-content/uploads/*.webp` 等启用**防盗链**（按 Referer 拒绝，返回 AccessDenied / 503）。抓取拿到的 URL 正确，legacy 路径取到的是**同一批 URL**（同走 `node_to_markdown`），故这与 crawl4ai 迁移无关，是阅读器直接 `<img src>` 跨域加载被图床拦截。正解是阅读器侧加**图片代理/缓存**（带源站 Referer 回源或本地缓存），单独立项。
>
> **第二轮人工测试发现的两个 crawl4ai 配置陷阱（已修）**：
> ① **`remove_overlay_elements`/`remove_consent_popups` 会误删正文题图**——crawl4ai 的遮罩启发式把 Anthropic 的 1000×1000 svg 题图当弹窗删掉了。因正文已用 `target_elements` 精确圈定（弹窗本就在范围外），这两个开关冗余且有害，**统一关闭**。
> ② **SSR+水合站点会在 cleaned_html 产生多个顶层 `<article>`（同一正文的重复副本）**，`target_elements=("article",)` 全拼会导致正文重复出现。后端改为：cleaned_html 里有多个顶层 `<article>` 时**只取正文最长的那个**（Anthropic 由此去重，且保留含题图的主 article）。
> ③ 容器尾部的"相关内容/CTA/空 CMS 控件"用稳定特征排除：Anthropic 相关推荐带 `LinkGrid-*`（`[class*="LinkGrid"]`）、Claude 的 testimonials/FAQ 带 Webflow `w-condition-invisible`/`w-dyn-empty`。

---

## C 类 · 单页文档拆多条 —— **保留 httpx + 渲染兜底**（不迁 crawl4ai）

`SinglePageDocumentFetcher` 取整页 → 各自 `_release_entries()/_paper_entries()` 拆成多条 `BaseContent`。

**最终决策（阶段四诊断后修正）**：9 个节点 httpx 均已完美拆条（服务端渲染），**保留 httpx 取页**。
原计划的「crawl4ai 取 `cleaned_html` 替代 httpx」被推翻——`cleaned_html` 会剥掉这些 Segmenter 依赖的结构锚点，是净倒退。
改为统一加**渲染兜底**：httpx 拆出 0 条且 crawl4ai 可用时，惰性起浏览器取**原始** `result.html`（锚点完整）重跑同一 Segmenter（`_segment_with_render_fallback`）。Segmenter 本身一行未改。

| 状态 | source_id | Segmenter | 拆条依据 | httpx 实测 / 兜底 |
|---|---|---|---|---|
| ✅ httpx+兜底 | `docs_deepseek_api_changelog` | `_release_entries`（经 `DevsiteReleaseNotesFetcher`） | 日期标题 | httpx 拆 17 条；e2e 验证渲染兜底首条字节一致 |
| ✅ httpx+兜底 | `docs_openai_codex_changelog` | `_release_entries` | `<li data-product>`+`id` | httpx 12 条；锚点仅存于原始 HTML，故兜底用 `result.html` 非 cleaned_html |
| ✅ httpx+兜底 | `docs_claude_code_changelog` | `_release_entries` | Mintlify `data-component-part` | httpx 12 条；e2e 验证渲染兜底首条字节一致（锚点完整） |
| ✅ httpx+兜底 | `docs_gemma_release_notes` | `_release_entries`（经 `DevsiteReleaseNotesFetcher`） | Devsite `<h2 id>` 日期 | httpx 12 条（正文本就是单行 release note，非缺失） |
| ✅ httpx+兜底 | `docs_xai_release_notes` | `_release_entries` | 日期/版本 | httpx 12 条 |
| ✅ httpx+兜底 | `docs_zai_new_released` | `_release_entries` | 版本卡片 | httpx 12 条 |
| ✅ httpx+兜底 | `web_bytedance_seed_research` | `_release_entries` | 研究卡片 | httpx 6 条 |
| ✅ httpx+兜底 | `web_huggingface_daily_papers` | `_paper_entries` | 论文卡片 | httpx 12 条（卡片含 arxiv 链接/作者，原始 HTML 结构完整） |

---

## 推荐切换批次（对应路线图阶段）

1. **阶段二·批次 1（最低风险，先建立信心）**：`web_anthropic_news`（0.97 已达标）→ `web_ithome_ai`（调 profile 到达标）。
2. **阶段二·批次 2**：补 Profile 后切 `web_claude_blog` / `web_cursor_changelog` / `web_qbitai` / `web_aiera`，每个先过旁路验收门槛。
3. **阶段三（已完成）**：`rss_openai_news` 详情渲染优先 crawl4ai 过 CF，Playwright 保留为 fallback，summary 最终降级；e2e 实测 crawl4ai 过 CF 2/2。仍需上线后做成功率/封禁率长期统计。
4. **阶段四（已完成，结论修正）**：诊断证明 9 个 C 类 httpx 已最优 → **不迁 cleaned_html**（会破坏结构锚点），改为统一加「httpx 空壳时用原始 `result.html` 重跑 Segmenter」的渲染兜底。

每行切换的标准动作：补/调 Profile → `scripts/compare_web_backends.py` 旁路验收（相似度 ≥ 0.8 或人工确认）→ 把该节点 `_detail_for_url` 路由到 backend（保留回退）→ 跑该源一次真实抓取核对入库正文 → 更新本表状态列。
