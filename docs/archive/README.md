# 归档:已完结的方案与执行记录

> 这里是**冷存**:计划已落地或被取代的文档,保留供追溯来龙去脉(为什么这样设计、当时否决了什么)。
> **不要依据本目录判断现状**——现势以 `CLAUDE.md` 架构简报与代码为准;耐久规范已回流至
> `docs/frontend/conventions.md` / `docs/sources/curation_policy.md` 等活跃文档。
> 按「故事」分组索引;文内常含 ☐/☑ 进度符,均为当时状态。

## 阅读器演进(2026-07,v3.2 → v3.10)

- [reader-reshell-plan.md](./reader-reshell-plan.md) — **主档**:按设计样页重构四带式阅读器的五轮记录
  (样页复刻/双轨合并 v3.6 → 容器化 v3.7 → 导轨靠拢·轨语言统一 v3.8 → 接入集成并入设置柜 v3.9 →
  发现页 v3.10),含每轮的用户目检修正与决策理由。
- [reader-usability-plan.md](./reader-usability-plan.md) — 前一波可用性迭代(未读体系/内容形态分流/AI 摘要,B→A→C)。
- [reader-polish-wave2-plan.md](./reader-polish-wave2-plan.md) — 体验二波小项(hover 预取/骨架等)。
- [folo-vs-dorami-reader.md](./folo-vs-dorami-reader.md) — Folo 竞品对照分析(差距定位:阅读循环机制/内容形态学)。

## 源扩容(2026-07,v3.2 → v3.5)

- [source-expansion-plan.md](./source-expansion-plan.md) — 扩容方法论 + wave1(8 文章形 RSS 源)记录;
  §1.1 读者画像校准(daily_brief 兴趣档 + Folo 中文订阅结构)仍被候选文档引用。
- [source-expansion-wave3-plan.md](./source-expansion-wave3-plan.md) — wave2/3 记录(Newsletter 二批、
  官方直连、GitHub Trending 日榜;H1/H2/M 轨道关闭或挂起的裁决);**Newsletter 三批候补名单在此**。

## 静默仪器全站重构(2026-07,v3.0.0)

- [quiet-instrument-restyle-plan.md](./quiet-instrument-restyle-plan.md) — 风格语法 R1–R8 与十四波执行记录
  (布局/台账/运行页/集成/运维/弹窗等)。R 规则的现行版在 `docs/frontend/conventions.md`
  (注意:选中语法在 v3.8 轨语言统一后已演进,以 conventions 为准)。
- [frontend-visual-polish-plan.md](./frontend-visual-polish-plan.md) — 更早的视觉打磨波(A–D/V4/F8-B)。
- [frontend-optimization-plan.md](./frontend-optimization-plan.md) — 最早期前端打磨快照。

## 前端结构重构(2026-07)

- [frontend-refactor-plan.md](./frontend-refactor-plan.md) + [frontend-refactor-progress.md](./frontend-refactor-progress.md)
  — 五阶段前端重构(F1–F8 债务锚点,api.js 拆分、桥接替换等)的计划与进度。

## 后端重构与实体简化(2026-06/07)

- [backend-architecture-review.md](./backend-architecture-review.md) — 触发重构的全面架构评审。
- [backend-refactor-plan.md](./backend-refactor-plan.md) + [backend-refactor-progress.md](./backend-refactor-progress.md)
  + [backend-refactor-report.md](./backend-refactor-report.md) — 五阶段后端重构(路由拆分/Alembic 落地/
  持久化 Job/阶段化)的计划、进度与收官报告。
- [entity-simplification-plan.md](./entity-simplification-plan.md) — 节点组(采集范围)与旧抓取任务的退役方案
  (迁移语义见 CLAUDE.md「Collection Jobs」节与 Alembic `8f6d93196258`)。

## 抓取技术选型(2026-06)

- [crawl4ai-feasibility.md](./crawl4ai-feasibility.md) — crawl4ai 可行性结论(httpx 优先、按需浏览器、可选依赖);
  被 `src/fetchers/web_content/__init__.py` 引为设计原则出处。
- [crawl4ai-migration-nodes.md](./crawl4ai-migration-nodes.md) — B/C 类节点迁移清单与逐节点验证记录。

## 竞品与原理对照

- [horizon-vs-dorami.md](./horizon-vs-dorami.md) — 与 Thysrael/Horizon 的抓取/日报原理对照。

## OpenAI 静态图片(2026-10-23,issue #217)

- [issue-217-decisions-images.md](./issue-217-decisions-images.md) — 相对图链修复、受限静态图片浏览器取图、交叉检视与历史回测记录。

## 游客模式(2026-10-23,issue #213)

- [issue-213-guest-mode.md](./issue-213-guest-mode.md) — 收到站内链接免登录先逛:访客会话 + 共享保留账号 `guest:shared`、白名单门控、共享游客早报、每访客已读与 AI 计次(与读者同额)、登录提示与叠层登录回原处、运维游客区;三批拍板、Codex 两轮检视(§7)与目检返修(分享菜单收口、小字「游客登录」)。

## 论文全文阅读（2026-10-22，issue #41 收口）

- [issue-41-paper-fulltext.md](./issue-41-paper-fulltext.md) — HF/arXiv HTML 全文阅读、摘要分析、默认 dry-run 回填、翻译完整性与 FTS 优化；§0 汇总 #201/#206/#207/#208 的完成证据及 PDF/其它源/复杂排版/修订跟踪/授权边界。原方案与检视过程保留供追溯。

## 2026-07 → 2026-10 波次方案(2026-10-21 文档整理波 issue #210 归档)

对应波次均已合入 / 发版;每篇文首有归档注,拍板与「明确不做」已摘入 [`../decisions.md`](../decisions.md)。按主题分组:

### 评分、分析与个人早报

- [unified-news-scoring-plan.md](./unified-news-scoring-plan.md) — 统一新闻价值评分(issue #22,v3.48):取缔阅读价值维度,文章级一把尺子;日报复用、缺分补评不写回;个人早报门槛 5.0 去等待。
- [analysis-brief-review-plan.md](./analysis-brief-review-plan.md) — 入库分析与日报链路审视(v3.48 收口):补评喂同一闭集、无正文按标题评、worker 轻列扫描、同事件机械预聚类。
- [image-understanding-wave-plan.md](./image-understanding-wave-plan.md) — 图片理解波(issue #69):`[llm] vision_model` 第三档、`image_insights` 按图片哈希缓存、配图说明并入分析 / 日报 / 问答。
- [personal-brief-breaking-lane-plan.md](./personal-brief-breaking-lane-plan.md) — 个人早报「重大事件」通道(issue #33 §2,v3.50.0):官方 ≥T 或多源印证、实体归并、每期 ≤N 额外于精选。
- [personal-brief-interest-union.md](./personal-brief-interest-union.md) — 「订阅 ∪ 兴趣」(issue #33 §3,v3.54.0):兴趣半取全站可见源,订阅外门槛 6.0 / 每源上限 2,编排说明行。
- [personal-brief-rebuild-entrypoints.md](./personal-brief-rebuild-entrypoints.md) — 重编入口收口(issue #33 §5,v3.51.1):兴趣 / 订阅变更只记录不重编,`interest_stale` / `scope_stale` 提示。
- [personal-brief-title-localization.md](./personal-brief-title-localization.md) — 标题中文化(issue #33 §4,v3.52.1):译名缓存 → 日报 title_cn → aux 批量译。
- [personal-brief-grid-and-sections.md](./personal-brief-grid-and-sections.md) — 板块顺序 / 分值驱动网格 / 公共日报排除(issue #74);网格部分已随 #184 退役。
- [interest-lens-wave-plan.md](./interest-lens-wave-plan.md) — 兴趣即透镜(issue #27,v3.52 / v3.56):左栏一根轴「订阅 | 兴趣」、五稿演进、屏蔽一极退役。

### 检索、翻译与读者 AI

- [rag-retirement-plan.md](./rag-retirement-plan.md) — RAG 退役与问答检索重构(v3.30 + v3.31):审视结论、「LLM 计划检索 + FTS5」目标架构、**§4 重新引入触发器**。
- [r1-translation-validation.md](./r1-translation-validation.md) — 全文翻译实测(#204 / #205):结构化单元协议修复、术语保护、质量边界。
- [background-translation-plan.md](./background-translation-plan.md) — 读者后台翻译与分段恢复(issue #202):持久任务、租约、预算预留、容量上限。
- [social-translate-plan.md](./social-translate-plan.md) — 社交流翻译(issue #84):卡片底栏「译为中文」、原文下追加译文、X API 无翻译能力的核查。
- [issue-93-tag-search-plan.md](./issue-93-tag-search-plan.md) — 标签检索整合(issue #93):单一 `tagSearch` 意图、正式 / 非正式标签统一检索。

### 播客

- [podcast-wave-plan.md](./podcast-wave-plan.md) — Podcast 专栏与精品播客总方案(issue #7):分轨治理、权利 / 质量 / 预算门控、中文精华、数据 / 状态机 / 成本 / 安全;旧成本与选型结论已被 `podcast-intelligence/` 取代。
- [podcast-intelligence/](./podcast-intelligence/README.md) — Issue #7 的 spec-kit 规格(spec / plan / tasks / data-model / research / voice-stack-decision / decisions)与 P0 验证报告、2026-09-03 设计复核。

### 来源与采集

- [social-x-wave-plan.md](./social-x-wave-plan.md) — X 社交波(v3.12):X API v2 按量付费、一账号一源、社交第三容器、白名单与不收理由、重叠率备案。
- [source-collections-wave-plan.md](./source-collections-wave-plan.md) — 源合集(v3.38.0):目录呈现层批量动作而非订阅实体,代码注册表策展,首发五枚。
- [user-custom-rss-wave-plan.md](./user-custom-rss-wave-plan.md) — 用户自定 RSS 源(v3.40.0):方案 B 自助 + 隔离,`user_rss_` 前缀结构性隔离,2026-09-04 现势修订。
- [news-coverage-reliability-plan.md](./news-coverage-reliability-plan.md) — 新闻覆盖可靠性(issue #127):IT之家有界分页、日报候选持久积压、HN Algolia 补充发现。

### 管理面、账号与互通

- [admin-usability-audit.md](./admin-usability-audit.md) — 管理面可用性审计 M01–M25(v3.40.3)与四波整改记录(P0 安全 / 账户 V2 / 列表规模化 / 数据生命周期);余项在 backlog。
- [admin-root-admin-and-account-growth.md](./admin-root-admin-and-account-growth.md) — 根管理员唯一例外 + 账户增长曲线(issue #31,v3.55.0)。
- [admin-surface-refactor-plan.md](./admin-surface-refactor-plan.md) — 运维面「播客」与「分析与标签」重构(issue #76):信息架构 / 表格 + 抽屉 / 归一层 / 接口卫生。
- [engage-sync-wave-plan.md](./engage-sync-wave-plan.md) — 互通波(v3.18):读者反馈收件箱、公告横幅、远程内容同步。

### 部署、运维与存储

- [auto-deploy-plan.md](./auto-deploy-plan.md) — GitHub Release 自动部署流水线(issue #102,v3.60.x):tag → Release → 批准 → 仓库外 launcher / worker;§7 检视记录。
- [baremetal-rollback-plan.md](./baremetal-rollback-plan.md) — 裸机部署回滚与运行副本版本化(issue #126):两级健康门告警、`--rollback`、收养;§8–§9 检视与实现记录。
- [backend-stall-fix-plan.md](./backend-stall-fix-plan.md) — 生产间歇停顿修复(issue #194,v3.65.2):逐字稿扫描投影、分析数据库线程隔离。
- [issue-101-media-reuse-plan.md](./issue-101-media-reuse-plan.md) — 接收端媒体文件复用与同步进度契约(issue #101)。

### 前端与视觉

- [visual-refine-plan.md](./visual-refine-plan.md) — 视觉精修波(issue #184,含 #176,v3.65):自托管思源宋体 / 西文衬线、三套中性色、夜航版式早报、字重与字号护栏。

## design/ —— 历史设计样页(2026-10-21 归档)

零引用的早期探索稿,现行样页在 [`../design/README.md`](../design/README.md):登录页六方向(`dorami-login-*.html`,2026-07-22,定稿是星座演出并入 `LoginScreen.jsx`)、凭据柜两稿(`dorami-credentials-*.html`,2026-08-04,v3.27 凭据整合台)、入口分析页(`dorami-entry-analysis-quiet.html`)。
