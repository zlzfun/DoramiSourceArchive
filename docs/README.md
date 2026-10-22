# 文档总索引(L1)

> **分层导航**(本仓文档主要供 Agent 使用):**L0** `AGENTS.md`(每会话必载:定向 / 命令 / 硬规则 / 流程 / 不变量 / 地图;`CLAUDE.md` 经 `@AGENTS.md` 引入它)→ **L1** 本文(全量一行摘要)→ **L2** 子目录 README(`architecture/` 无 README,各文件自述;`ops/`、`archive/`、`design/`、`sources/candidates/`、`archive/podcast-intelligence/` 各有一张表)。每层概括下一层,**无需逐篇 grep 即可定位**。
> **状态签**:◉ 现行(随代码演进更新)/ ◇ 耐久参考(稳定少改,文首标快照日期)/ ☐ 活跃方案(进行中 / 排队中)/ ⊘ 归档(只增不改,勿据其判断现状)。每篇文首 `> 状态:` 行用词表 `进行中 | 排队中 | 已落地 vX.Y.Z | 耐久参考 | 归档`;守卫 `scripts/check_docs.py`(索引覆盖 / 断链 / 词表 / L0 体量 / `plans/` 文件名 `issue-<N>-<slug>.md`)。
> 本索引最后一次整体重排:2026-10-21(文档整理波 [issue #210](https://github.com/zlzfun/DoramiSourceArchive/issues/210),方案 [`plans/issue-210-docs-tidy.md`](./plans/issue-210-docs-tidy.md))。

## 总账与决策

- ◉ [backlog.md](./backlog.md) — **跨波次待办总账**(进行中 / 排队中 / 展望三档 + 近期已完结指针)。找「下一步做什么 / 哪些方向被搁置及原因」看这里。
- ◉ [version-history.md](./version-history.md) — **波次史**:顶部「年表」一行一波(新波次在此加一行),其后逐波详细记录(设计取舍 / 目检返修 / 检视返修 / 被否方案)。L0 不再逐波改。
- ◇ [decisions.md](./decisions.md) — **决策总表(ADR-lite)**:一行一决策(拍板 / 明确不做 / 再评估触发)+ 出处链接;问「为什么不做 X」先查这里。

## architecture/ —— 设计说明(◇ 耐久参考,2026-10-21 自 CLAUDE.md 原文迁出)

- [architecture/overview.md](./architecture/overview.md) — **整体设计**(回应 issue #89):两侧一库、数据流、分析与两条早报产品线、播客与媒体、角色轴、部署拓扑与同步、调度、运维面;每节链到分域文档。
- [architecture/content-model.md](./architecture/content-model.md) — 双维身份(`content_type` × `source_id`)、内容分类(kind / event 读取时推导,`admission_status`,双评分主分规则,分发三波)、`extensions_json` 序列化。
- [architecture/fetchers-and-sources.md](./architecture/fetchers-and-sources.md) — `FetcherRegistry` 自动发现、三类基类、crawl4ai 可选后端、`generic_web` 配置源、`source_builder`、Playwright 渲染、运行跟踪。
- [architecture/analysis-and-briefs.md](./architecture/analysis-and-briefs.md) — 文章分析与 Taxonomy、个人早报选篇(重大事件 / 订阅 ∪ 兴趣 / 重编意图)、公共日报管线、分析可见性与 worker drain。
- [architecture/podcast.md](./architecture/podcast.md) — 媒体边界(不下载音频 / 无公网回源)、成本门控(#97)、全文处理自动入队三层防线。[architecture/podcast-transcript-dedup.md](./architecture/podcast-transcript-dedup.md) — 逐字稿去重算法。
- [architecture/media-and-storage.md](./architecture/media-and-storage.md) — 媒体库(原链不改写 / 内容哈希去重 / Referer / SSRF / 负缓存)、OSS 持久媒体、自动备份、存量策略(只随文预取)。
- [architecture/search-and-qa.md](./architecture/search-and-qa.md) — FTS5 唯一检索基础设施、LLM 计划检索 + FTS5、RAG 退役结论;附 v3.36 技术栈与原理报告快照。
- [architecture/social-x.md](./architecture/social-x.md) — 社交媒体流(X 社交波):X API v2 按量付费采集与配额守卫、一账号一源、引用 / 转推扁平化、作者头像、SocialFlow 卡片流与不展示互动数字。
- [architecture/accounts-and-access.md](./architecture/accounts-and-access.md) — 账号(DB 管理 / 根管理员 / 末位守卫)、审计、双界面、角色双轴、外部凭据统一保管层。
- [architecture/reader-distribution.md](./architecture/reader-distribution.md) — 读者订阅与交付(dsub_/dfeed_ 令牌、聚合 feed)、可下载技能包。
- [architecture/llm-and-ai.md](./architecture/llm-and-ai.md) — OpenAI 兼容客户端(三档模型 / 思考模式 / 计量回调)、读者面 AI Beta(翻译 / 问答四档 / 引用联动)、后台翻译、图片理解。
- [architecture/jobs-and-scheduling.md](./architecture/jobs-and-scheduling.md) — Collection Jobs 唯一调度实体、热重载契约、持久后台 Job 状态机。
- [architecture/archive-sync.md](./architecture/archive-sync.md) — 外网 authority → 内网 replica 六流同步协议摘要(契约全文在 contracts/)。
- [architecture/admin-ops.md](./architecture/admin-ops.md) — 运维管理 Tab 三子页、播客与分析标签区、AI 用量 / 阅读 / 登录三类计量。
- [architecture/code-map.md](./architecture/code-map.md) — 带注释的目录树(快照;以 `ls` 为准,价值在注释)。

## reference/ 与 dev/ —— 参考与开发流程

- ◇ [reference/endpoints.md](./reference/endpoints.md) — 端点清单与门控语义(权威 = 运行中的 `/docs` 与 `src/api/routers/`;含互通波、分享波、Admin Ops 端点注记)。
- ◇ [dev/workflow.md](./dev/workflow.md) — **协作流程全文**:节奏与原则、开工(worktree)、自测、沙箱、文档同波、提交、交叉检视协议、PR 与合入(含难验收例外)、发版、master 分支、纯文档轻量路径。
- ◇ [dev/testing.md](./dev/testing.md) — 测试布局与覆盖面、会话级沙箱、xdist 划分、陷阱档案(滚动窗 / fake-ip / git 深度 / 管道吞退出码 / E2E 基线)。
- ◇ [dev/migrations.md](./dev/migrations.md) — Alembic 完整语义:create_all ≡ upgrade heads、收养、多头口径、FK 策略、Taxonomy 启动姿态。
- ◇ [dev/dependencies.md](./dev/dependencies.md) — uv.lock 入库流程、避开镜像源、导出 docker/requirements、worktree 下的 uv 注意。
- ◇ [dev/sandbox-acceptance.md](./dev/sandbox-acceptance.md) — 隔离栈起服、真实库副本、播种约束、Playwright 选择器与像素断言、交付验收步骤。

## ops/ —— 部署与运维(◉ 现行;总览与表 [ops/README.md](./ops/README.md))

- [ops/deploy-docker.md](./ops/deploy-docker.md) — Docker 双容器部署(推荐):compose 形态 / 用法与运维 / ini 容器内语义 / HTTPS / 全新服务器与迁移 / 受限网络镜像源。
- [ops/deploy-baremetal.md](./ops/deploy-baremetal.md) — 裸机 PM2 路径(issue #126 起 release 形态):选型对照 / release 布局 / 流程与护栏 / 退出码 / `--rollback` / 收养 / HTTPS。
- [ops/release-process.md](./ops/release-process.md) — tag 即发布:`scripts/release.sh`、CI 门禁与自动 Release、流水线审批部署、回滚、分支保护清单。
- [ops/configuration.md](./ops/configuration.md) — `config/*.ini` **唯一配置参考**(操作视角 + 配置节语义总表 + 环境变量)。
- [ops/oss-storage.md](./ops/oss-storage.md) / [ops/oss-local-fallback.md](./ops/oss-local-fallback.md) — OSS 持久媒体与 Archive Sync 路径、自动缓存回收与租约、迁移恢复 / 从 OSS 回退本地;策略样例 `ops/oss-*.example.json`。
- [ops/storage-backups.md](./ops/storage-backups.md) — 自动备份与离线恢复(默认关闭)。
- [ops/taxonomy-v1-deployment.md](./ops/taxonomy-v1-deployment.md) — Taxonomy v1 发布资产与 authority / replica 启动姿态。
- [ops/aliyun-dual-node-deployment.md](./ops/aliyun-dual-node-deployment.md) — 阿里云外网 / 内网双节点上线:INI 替换清单、Secret / RAM / NLS / OSS、发布顺序、E2E 验收。
- [ops/bailian-singapore-deployment.md](./ops/bailian-singapore-deployment.md) — 新加坡百炼 Fun-ASR / Qwen3-TTS 接入、预算与样本验证。
- [ops/full-analysis-backfill.md](./ops/full-analysis-backfill.md) — 历史文章 `full_analysis` 回填:边界、管理面操作、低优先级调度、恢复语义。
- [ops/runbook.md](./ops/runbook.md) — 排查手法沉淀:日报漏报 / cron 缺席 / 分析回填 / 只读聚合统计 / 部署前后核对 / 源静默停产修补(不含主机信息)。

## contracts/ —— 对外契约(◇ 耐久)

下游消费方(LLM / RSS 工具 / 对端部署)依赖的接口契约,字段级描述:

- [contracts/feed_delivery.md](./contracts/feed_delivery.md) — `/api/feed/*` JSON + Markdown 批量交付(过滤参数 / 记录形状 / extensions 展开)。
- [contracts/reader_subscription.md](./contracts/reader_subscription.md) — 读者订阅体系:一键订阅、dsub_/dfeed_ 令牌签发与轮换、`/api/public/*` 拉取端点。
- [contracts/archive_sync.md](./contracts/archive_sync.md) — collector → reader 的六流同步协议与合并规则(身份 / 血缘 / 校验和 / 墓碑)。
- [contracts/podcast_provider_ports.md](./contracts/podcast_provider_ports.md) — Podcast ASR / TTS provider-neutral ports、adapter 责任边界、新增供应商验收清单。

## frontend/ —— 前端纪律(◉ 现行)

- [frontend/conventions.md](./frontend/conventions.md) — **改前端必读**:文案 / 可访问性 / 排版刻度 / 颜色令牌四套 / 圆角 / 描边预算 / 动效 / 选中语法 / 暗色;token 单一事实来源 = `frontend/src/index.css`;含 `font: inherit` 压层陷阱档案。
- [frontend/e2e.md](./frontend/e2e.md) — #90 移动读者与 #85 PWA 真实 E2E:一条命令、自建 FastAPI / SQLite 沙箱、`--flows` 子集。
- [frontend/pwa.md](./frontend/pwa.md) — #85 主屏幕安装、最小离线恢复、更新边界、cloudflared 真机预览。
- [frontend/mobile-navigation.md](./frontend/mobile-navigation.md) — Issue #86 响应式阅读器、贴底导航、共享阅读状态与浏览器回归入口。

## sources/ —— 源策展与节点运维

- ◉ [sources/curation_policy.md](./sources/curation_policy.md) — 默认可见性与 **incubating 观察期 / 转正机制**(新源批次流程)。
- ◇ [sources/classification_standard.md](./sources/classification_standard.md) — 源携带的身份 + 分类元数据规范 v1.1。
- ◇ [sources/admission_workflow.md](./sources/admission_workflow.md) — 新源提案 → 验证 → 准入的 add-only 流程。
- ◇ [sources/node_audit_playbook.md](./sources/node_audit_playbook.md) — 节点体检与修复手册,**补新源必读**(正文质量三层诊断 / 站点镶边三修法 / 上线抽查清单)。
- ◉ [sources/node_catalog_and_risks.md](./sources/node_catalog_and_risks.md) — 内置节点逐个适配手法与稳定性风险(快照 2026-06-16,现势以注册表为准)。
- ◉ [sources/podcast_catalog.md](./sources/podcast_catalog.md) — 37 个精选播客节目、逐源验证日期、幂等导入、观察期与权利边界。
- ◇ [sources/candidates/](./sources/candidates/README.md) — 候选源证据库(13 册,按厂商 / 板块)。**看它的 README 即可知各册覆盖与消化状态**。

## plans/ —— 活跃方案(☐ 进行中 / 排队中;落地后归档)

- ☐ [plans/issue-178-content-classification.md](./plans/issue-178-content-classification.md) — **内容分类与双评分(issue #178)**:第一波 / 第一波补 / 第二波 / 3a 已上线(v3.64),3b 播客提示词拆分与 3c 新闻尺子收紧排队,分区空板率与论文名额观察期。
- ☐ [plans/issue-82-node-watchdog-layer1.md](./plans/issue-82-node-watchdog-layer1.md) — **节点看护第一层:抓取失败判定升级(issue #82)**:R2 稿已拍板,PR-0 已修调度重载 bug,PR-A / B / C(产出轴 `yield_status`、收据先行、看护页)未动工;生产回放校准结论在此。
- ☐ [plans/issue-97-podcast-cost-and-scoring.md](./plans/issue-97-podcast-cost-and-scoring.md) — **Podcast 成本门控与评分标定(issue #97)**:P0 门控已上线;P1 六组 32 输入黄金集与提示词调整未启动。
- ☐ [plans/issue-132-intelligent-butler.md](./plans/issue-132-intelligent-butler.md) — **智能管家(issue #132,Clowder AI,R1 设计稿)**:Mac mini 常驻 agent、运行日报 / 主动预警 / 投递回执 / 反馈归并 / 自愈;排队中,未部署。
- ☐ [plans/issue-210-docs-tidy.md](./plans/issue-210-docs-tidy.md) — **文档整理波(issue #210)**:盘点数字、三层分发模型、CLAUDE.md 节级去向、逐文件与记忆逐条裁决、拍板 D1–D10、执行清单(文中路径为迁移前)。

## design/ —— 设计样页(◇ 参考;索引 [design/README.md](./design/README.md))

静默仪器各工作区的 HTML 设计样页(`dorami-*-quiet.html`)与多页样页目录(`podcast-player/`、`social-translate/`、`visual-refine-final/`),`index.css` 注释以「刻度 1:1 取自」引用之;逐页对应的波次 / issue / 现行与否见 README。历史样页(登录探索、凭据柜、入口分析)在 `archive/design/`。`pr-assets/` 是 PR 配图。

## archive/ —— 已完结方案与执行记录(⊘ 归档;索引 [archive/README.md](./archive/README.md))

计划已落地或被取代的文档,按「故事」分组:2026-07 的阅读器演进五轮 / 源扩容 / 静默仪器重构 / 前后端重构 / 实体简化 / crawl4ai 选型 / 竞品对照,以及 2026-10-21 归档的 28 篇 2026-07 → 2026-10 波次方案(评分与早报 / 检索与翻译 / 播客 / 来源 / 管理面 / 部署运维 / 视觉),Podcast Intelligence 的 spec-kit 规格与报告在 `archive/podcast-intelligence/`。**查决策来龙去脉才来这里;判断现状看 `docs/architecture/` 与代码。**

#41 于 2026-10-22 按首期范围收口，见 [论文全文阅读归档](./archive/issue-41-paper-fulltext.md)：HF/arXiv HTML 阅读、摘要分析、回填、FTS 优化与后续翻译修复均已落地；PDF/其它源/复杂排版等后续边界保留在待办展望。

## 仓外 / 其它

- `../docs-site/` — **读者使用手册**(VitePress,`cd docs-site && npm ci && npm run dev`;PR #162,GitHub Pages 待开);与本目录的开发者文档分工:它面向用户,不记设计取舍。
- `../README.md` — 面向用户的项目介绍;`../AGENTS.md` / `../CLAUDE.md` — Agent 入口;`../scripts/README.md` — 运维 / 治理 / 导出脚本目录。
