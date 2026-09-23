# 文档总索引(L1)

> **分层导航机制**(本仓文档主要供 Agent 使用):
> **L0** `CLAUDE.md`(架构简报+开发命令,Claude Code 自动加载)与 `AGENTS.md`(通用 Agent 入口)
> 的「文档地图」→ **L1** 本文(全量一行摘要)→ **L2** 子目录 README
> (`archive/README.md` 按故事分组、`sources/candidates/README.md` 按板块+状态)。
> 每层给出下一层的内容概括,**无需逐篇 grep/read 即可定位**。
> 状态签:◉ 活跃(会随代码演进更新)/ ◇ 耐久参考(稳定,少改)/ ⊘ 归档(只增不改,勿据其判断现状)。

## 顶层(活跃)

- ◉ [news-coverage-reliability-plan.md](./news-coverage-reliability-plan.md) — **新闻漏采修复(issue #127)**：IT之家有界分页、公共日报候选持久积压、HN 备用发现入口；真实源验证、日间采集配置预览与回滚。

- ◉ [backlog.md](./backlog.md) — **跨波次待办总账**(进行中/排队中/展望三档 + 近期已完结索引)。
  找「下一步做什么/哪些方向被搁置及原因」看这里。
- ◉ [admin-usability-audit.md](./admin-usability-audit.md) — **管理面/运维面可用性审计与共识清单(v3.40.3,
  150 人上量场景)**:Claude+codex 双独立审计交叉核验的 M01–M25 分层清单(P0 安全/成本护栏/身份 →
  P1 账户治理规模化[批量+过滤排序两点名痛点] → P2 运维可靠性 → P3 优化/文档),含三项待负责人决策与整改分层。
  **P0(M01–M04)v3.40.4、账户管理 V2(M05/M06/M07/M19)v3.41.0、运维列表规模化(M08/M09/M11/M17)
  v3.42.0 已落地**,整改记录见文首;余项与裁剪注记见 backlog。
- ◉ [admin-surface-refactor-plan.md](./admin-surface-refactor-plan.md) — **运维面「播客」与「分析与标签」重构(issue #76)**:
  两波 codex 管理面拉齐到管理面纪律——拍板记录(信息架构 / 表格+抽屉 / 表单卡 / 回填默认档 / 内容页隐藏时间窗 /
  单集抽屉)、前端落地面(PodcastZone / TaxonomyLedger / 归一层 / 共享 KPI+TableFoot)、后端接口卫生(任务三轴筛选 /
  音频分页 / 标签统一总账 / 单集详情)、隔离栈 Playwright 验收与有意边界。
- ◉ [configuration.md](./configuration.md) — `config/*.ini` 配置项逐节说明(runtime 角色/auth/RAG/LLM/
  网络代理),含生产 production.ini 与环境变量覆盖。
- ◉ [oss-storage.md](./oss-storage.md) — **OSS 媒体存储（issue #92，开发中）**：同地域内网读取与 Archive Sync 路径、
  OSS/ECS/内网副本的数据分工、自动缓存回收与读写租约、迁移恢复和验收；尚未部署。
- ◉ [storage-backups.md](./storage-backups.md) — **自动备份与离线恢复（默认关闭）**：SQLite 一致性快照、
  付费回执与本地媒体打包、OSS 依赖清单、独立备份权限、本地保留及 SHA-256 恢复演练。
- ◉ [deploy-docker.md](./deploy-docker.md) — **Docker 部署(推荐路径)**:compose 双容器形态/
  用法与运维/ini 容器内语义差异/HTTPS/全新服务器部署与迁移/受限网络镜像源。
- ◉ [deploy-baremetal.md](./deploy-baremetal.md) — **裸机部署(第二条官方路径,v3.39.0 扶正;issue #126 起 release 形态)**:
  `deploy.sh` = uv + PM2 + 宿主 Nginx;两条路径选型对照/release 布局/前置软件/流程与护栏/退出码/**回滚**(`--rollback` /
  `--restore-db` / `--to` / `--code`)/**收养**/ini 两节与环境变量/HTTPS 两趟部署(certbot certonly 而非 --nginx)/全新服务器与迁移。
- ◉ [release-process.md](./release-process.md) — **发布流程:tag 即发布(2026-09-14)**:合入≠上线,
  annotated tag 是唯一发布单元;`scripts/release.sh` 发版(版本号只在发版时改,PR 不 bump)/
  两条部署脚本按 tag 部署(`--here` 显式例外)/CI 门禁与自动 Release/回滚=切 tag+恢复备份/分支保护清单。
- ◉ [intranet-release-guide.md](./intranet-release-guide.md) — **外网发版到内网发布与部署**(仅 master):
  上游验收/内网同步与独立 tag/精简文案/源码与前端双包/按 tag 部署与回滚/社区发布。
- ◉ [auto-deploy-plan.md](./auto-deploy-plan.md) — **自动部署流水线(issue #102,2026-09-16)**:tag → Release → Environment 批准 → SSH forced command → 仓库外 launcher / worker(事务 / 护栏 / 首装门)→ 目标 tag 的 deploy-docker.sh;§7 codex 检视记录。
- ◉ [baremetal-rollback-plan.md](./baremetal-rollback-plan.md) — **裸机部署回滚方案(issue #126,2026-09-21,R3 拍板后已实现)**:两级健康门失败告警(不自动回滚)+ `./deploy.sh --rollback` 不 checkout / 不出网 / 不构建回到上一 release;**运行副本版本化**(release = 代码副本 + venv 指针 + dist + nginx 配置集合 + 固化回滚执行体,PM2 从 release 实路径起)/ 事务阶段 / DB 按迁移计划分流(`--restore-db` 显式恢复)/ 收养 / `--code`;§8 codex 设计检视记录(R1 25 条 + 改形答复 13 条 + 复检 8 条全部采纳)、§9 实现记录(与方案的偏差 / 测试矩阵覆盖)。运维手册见 [`deploy-baremetal.md`](./deploy-baremetal.md)。
- ◉ [version-history.md](./version-history.md) — **波次史(1.x→3.57.x 逐波详细记录)**:2026-09-15 自 CLAUDE.md
  `## Versioning` 节整体迁出(L0 超 150k 字符上限);每波的设计取舍/目检返修/codex 检视返修/被否方案全文在此,
  CLAUDE.md 年表每波只留一行。**新波次的详细记录追加于此**。
- ◉ [engage-sync-wave-plan.md](./engage-sync-wave-plan.md) — v3.18 互通波设计:读者反馈收件箱/
  管理员公告横幅(逐用户一次性 dismiss)/远程内容同步(接收方拉取,复用归档同步契约)。
- ◉ [interest-lens-wave-plan.md](./interest-lens-wave-plan.md) — 兴趣即透镜波(v3.52.0 已实现,issue #27
  第一波,含四稿设计取舍与宏观拍板):左栏改三谓词过滤面板(订阅 / 兴趣 / 收藏 多选 AND,全关 = 全站),
  列头回显激活集,命中胶囊 / 屏蔽折叠行(v3.56 随屏蔽退役)/ 订阅外就地订阅,兴趣编辑面并入发现页第三段;后续候选清单。
- ◉ [source-collections-wave-plan.md](./source-collections-wave-plan.md) — 源合集波(v3.38.0 已实现,
  含实施记录):发现页策展合集(「源 ⇄ 合集」seg + 合集卡/详情 + 一键批量订阅);定调=目录呈现层
  批量动作而非订阅实体,代码注册表策展,交付链路零感知。首发五枚:HN 博客 2025/前沿实验室官方/
  国产开源模型动态/AI 编程工具动态/AI 深度写作(构思纪律与被毙候选见实施记录)。
- ◉ [user-custom-rss-wave-plan.md](./user-custom-rss-wave-plan.md) — 用户自定 RSS 源波(v3.40.0
  已实现):读者贴 URL 自助添加私有 RSS 源(参照 Folo);方案 B 自助+隔离(不进公共目录/
  all 检索域/日报/archive sync),最简正文(feed 给什么存什么,preview 仅守门不分型),
  入库存储+媒体不预取+删源即清;技术底座复用 generic_rss + SourceConfigRecord(加 owner 列)。
- ◉ [podcast-wave-plan.md](./podcast-wave-plan.md) — Podcast 专栏与长播客精华设计:博客/播客 RSS
  分轨治理、竞品与 Podcasting 2.0 调研、`>30min` 权利/质量/预算门控、带时间戳证据的中文博客、
  `≤15min` AI 精华音频、数据/API/状态机/成本/安全以及分期与端到端验收。
- ◉ [podcast-transcript-dedup.md](./podcast-transcript-dedup.md) — Provider 无关的逐段逐字稿
  去重策略：同源片段时间覆盖与连续 token 双判定、部分镜像保留独有内容、真实多声道验收矩阵。
- ◉ [Issue #7 可执行规格](../specs/007-podcast-intelligence/spec.md) — Podcast Intelligence 的
  用户故事、成功指标、研究决策、数据模型、OpenAPI 契约、多 Agent 任务和主会话验收入口。
- ◉ [sources/podcast_catalog.md](./sources/podcast_catalog.md) — 内部「欧研观澜」36 个节目样本的
  RSS 可接入性验证、精选目录、幂等导入命令/API、观察期与权利边界。
- ◉ [taxonomy-v1-deployment.md](./taxonomy-v1-deployment.md) — 已批准 Taxonomy v1 的发布资产与上线手册：
  外网 authority 启动自动 reconcile → 人工发布，内网 replica 通过 Archive Sync
  `taxonomy.jsonl` 原子导入最新已发布版本；无额外 taxonomy 安装脚本。
- ◉ [bailian-singapore-deployment.md](./bailian-singapore-deployment.md) — 新加坡百炼 Fun-ASR / Qwen3-TTS 适配、预算与独立样本验证。
- ◉ [aliyun-dual-node-deployment.md](./aliyun-dual-node-deployment.md) — 阿里云外网/内网双节点生产上线：
  验收 INI 替换清单、双端配置、Secret/RAM/NLS/OSS/安全组、发布顺序和 E2E 验收。
- ◉ [unified-news-scoring-plan.md](./unified-news-scoring-plan.md) — **统一新闻价值评分波(issue #22,v3.48.0)**:
  取缔阅读价值维度,文章级评分只剩一把「新闻价值」尺子(入库分析提示词以日报 MAP 锚点为底改写);
  公共日报复用该评分、缺分就地补评不写回(软依赖,分析全无也能出报)、门槛 pass、只为入选者写点评;
  个人早报门槛 5.0 + 去等待。拍板/取舍/明确不做见 §0–§5。
- ◉ [personal-brief-breaking-lane-plan.md](./personal-brief-breaking-lane-plan.md) — **个人早报「重大事件」通道(issue #33 §2,v3.50.0)**:
  跨订阅范围的头条位——官方一手 ≥T 或多源印证双准入、entity 标签连通归并、代表优先级官方>非社交>分数、
  24h 窗、同实体前两期抑制、额外于精选之上;生产 7 天实证(Fable 5.1 官博不在库)与不做清单。
- ◉ [personal-brief-title-localization.md](./personal-brief-title-localization.md) — **个人早报标题中文化(issue #33 §4,v3.52.1)**:
  条目快照补 `title_zh`,来源按成本 译名缓存 → 公共日报 title_cn → 编排后批量翻译(aux、20s 预算、写回缓存);
  中文不译不画副标题、失败回退原标题、不并入分析调用;卡片中文主标题 + 英文原题副行。
- ◉ [personal-brief-interest-union.md](./personal-brief-interest-union.md) — **个人早报「订阅 ∪ 兴趣」+ 页面体现个性化(issue #33 §3,v3.54.0)**:
  兴趣半候选池扩到全站可见源(订阅外门槛 6.0 / 每源硬上限 2,管理面旋钮;订阅内命中排前;订阅空但有兴趣只出兴趣半);
  页面加编排说明行(哆啦美身份 + 「从 N 个来源和 K 个兴趣 · M 篇里选出 P 篇」,§5 三条提示并入尾句)、
  选篇理由四词进卡头右侧、订阅外卡挂「未订阅」;`selection_stats_json` 入库。
- ◉ [admin-root-admin-and-account-growth.md](./admin-root-admin-and-account-growth.md) — **管理面:用户明细仅根管理员可见 + 账户增长曲线(issue #31,v3.55.0)**:
  根管理员判据(活跃管理员里的 admin,缺则最早活跃管理员)与不可降级/停用/删除守卫;`/api/accounts`、`/api/admin/accounts` 仅根管理员,
  ai-usage/overview 按会话剥用户维度;`/api/admin/account-growth` + 日/周/月累计与新增两图;有意保留给全体管理员的面。
- ◉ [image-understanding-wave-plan.md](./image-understanding-wave-plan.md) — **图片理解波:可配置多模态能力 + 文章配图识别(issue #69)**:
  `[llm] vision_model` 第三档(DeepSeek `deepseek-flash`,显式关思考)、客户端多模态分片、`image_insights` 表按图片字节哈希缓存;
  配图 → 结构化文字说明并入入库分析 / 公共日报补评与编辑 / 问答显式档(缺则识)与检索档(cached-only)/ 速读兜底;
  未配置视觉模型各链路与既有逐字一致;选图护栏(播客/不可外送源/小图/上限 4)、负缓存退避、预算超时转后台;真机验证记录。
- ◉ [personal-brief-rebuild-entrypoints.md](./personal-brief-rebuild-entrypoints.md) — **个人早报重编入口收口(issue #33 §5,v3.51.1)**:
  兴趣/订阅变更只记录不触发重编,重编只剩手动与次日定时;今日端点 `interest_stale`/`scope_stale`
  驱动「下次编排生效 · 立即重编」提示;系统侧保留管理员下架的维护性触发(公共日报就绪追加自 issue #74 退役)。
- ◉ [personal-brief-grid-and-sections.md](./personal-brief-grid-and-sections.md) — **个人早报板块顺序 / 分值驱动网格 / 公共日报排除(issue #74)**:
  日报剔出早报范围并退役 daily_brief_ready 触发链;板块按 SECTION_ORDER 固定、板块内分数降序;网格 6 等分单元,宽度跟分数走
  (≥ 9 通栏、并排分差 ≥ 1 用 ⅔ + ⅓、余 1 头卡够高才通栏否则 2 + 2),样页 `design/dorami-brief-grid-quiet.html`。
- ◇ [node-watchdog-layer1-plan.md](./node-watchdog-layer1-plan.md) — **节点看护第一层:抓取失败判定升级(issue #82 L1,R2 方案稿,codex 检视一致,2026-09-19 拍板)**:
  执行轴不动、新增独立**产出轴** `yield_status`(S1 两个不同日显式零候选 / S2 严格新增数 + 源自身历史最长间隔 ×2 + 观察世代与指纹,产出日不足即「未分类」);
  抓取器上报 `discovered_count` + `discovery_mode` 默认 unknown 显式 opt-in;正文偏薄按源级 `body_expectation` 显式声明;调度「收据先行 + 对账」(`guarded_schedule` / `schedule_receipts`)、`watch_events` occurrence 模型;
  日报运行史改表 + 50% 基线;运维「看护」子页;生产库回放实证(issue 原案误报 74 源 → 护栏版只命中 3 源且比人工早 3–9 天;OpenAI 正文 7 月中起即退化);
  检视中发现现役 bug:编辑采集任务会把留存清理 / 远程同步 / 用户源刷新 / 播客 ASR worker 从调度器删掉(PR-0 先修)。
- ◉ [analysis-brief-review-plan.md](./analysis-brief-review-plan.md) — **入库分析与日报链路审视收口**(v3.48 同 PR):
  补评喂同一闭集、无正文候选按标题走同一把尺子、worker 轻列扫描 + 版本重跑慢滴、编辑喂分析事实、
  分数直方图与补评撞车读数、同事件机械预聚类、跨天对照物带要点;审视结论与明确不做见 §0/§5。
- ◉ [full-analysis-backfill.md](./full-analysis-backfill.md) — 历史文章语义回填：
  `full_analysis` 与 `retag_only` 边界、管理面操作、低优先级调度、恢复语义及上线顺序。
- ◇ [reader-search-architecture.md](./reader-search-architecture.md) — 检索问答(类 RAG)模块
  技术栈与原理报告(快照 v3.36.0,含 mermaid 架构图):FTS5 索引层/四阶段管线/降级链与
  机械化诚实层/消费方范围/关键参数;权威事实来源仍是 reader_search.py 等代码。
- ◇ [rag-retirement-plan.md](./rag-retirement-plan.md) — **RAG 退役与问答检索重构(已完成,
  v3.30+v3.31 执行记录)**:审视结论存档(生产 0 向量化/双存储复杂度税/两个实质 bug)+
  「LLM 计划检索 + FTS5」目标架构 + 两波执行清单与差异注记 + **重新引入触发器(§4,
  未来考虑向量层时先读)**。
- ◇ [im-bot-architecture.md](./im-bot-architecture.md) — **内网 IM 机器人后端架构(intranet 独有,
  给内网编码助理的开工指示)**:入站管道→按会话串行调度→处理器→出站管道四段式、
  会话生命周期(懒创建+双重回收+内存态)、asyncio 三层并发控制、网关哑管道纪律、
  模块骨架与已拍板取舍表;§8 平台化演进预案(已被 §9 关闭,留作决策考古)、
  §9 定位终判(2026-08-14):welink-bot=哆啦美专属平台,通用性约束解除、分层纪律保留、
  多发送者设施冻结保留。
  契约面见 contracts/im_bot_integration.md。

## contracts/ —— 对外契约(◇ 耐久)

下游消费方(LLM/RAG/RSS 工具/对端部署)依赖的接口契约,字段级描述:

- ◇ [contracts/feed_delivery.md](./contracts/feed_delivery.md) — `/api/feed/*` JSON+Markdown 批量交付
  (过滤参数/记录形状/extensions 展开)。
- ◇ [contracts/reader_subscription.md](./contracts/reader_subscription.md) — 读者订阅体系:一键订阅、
  dsub_/dfeed_ 令牌签发与轮换、`/api/public/*` 令牌拉取端点。
- ◇ [contracts/archive_sync.md](./contracts/archive_sync.md) — collector→reader 的 JSONL 导出/导入契约
  (身份/血缘/校验和)。
- ◇ [contracts/im_bot_integration.md](./contracts/im_bot_integration.md) — 内网 IM 机器人接入契约:
  dorami-bot 服务账号程序化登录 + ask(scope=all,2026-08-14 自 subscription 改判)问答/引用渲染/多轮 history +
  dfeed_ 日报拉取;责任分界与已拍板决策(哆啦美侧零改动)。
- ◇ [contracts/podcast_provider_ports.md](./contracts/podcast_provider_ports.md) — Podcast ASR/TTS
  provider-neutral ports、供应商 adapter 责任边界、未知提交规则和新增供应商验收清单。

## frontend/ —— 前端纪律(◉ 活跃)

- ◉ [frontend/e2e.md](./frontend/e2e.md) — #90 移动读者与 #85 PWA 真实 E2E：一条命令、自建 FastAPI/SQLite 沙箱、阅读交互与 SW 生命周期。
- ◉ [frontend/pwa.md](./frontend/pwa.md) — #85 主屏幕安装入口、最小离线恢复、更新边界与 cloudflared 隔离真机预览。
- ◉ [frontend/mobile-navigation.md](./frontend/mobile-navigation.md) — Issue #86：响应式阅读器、贴底导航、共享阅读状态与浏览器回归入口；含验证边界。

- ◉ [frontend/conventions.md](./frontend/conventions.md) — **改前端必读**:文案/可访问性/排版刻度/
  颜色令牌四套/圆角/描边预算/动效/选中语法(轨=wash 块、工作区列表=accent 竖条)/暗色,
  含 `button|input{font:inherit}` 压层陷阱档案。token 单一事实来源 = `frontend/src/index.css`。

## sources/ —— 源策展与节点运维

- ◉ [sources/curation_policy.md](./sources/curation_policy.md) — 默认可见性(`ESSENTIAL_FETCHER_IDS`)
  与 **incubating 观察期/转正机制**(新源批次流程)。
- ◇ [sources/classification_standard.md](./sources/classification_standard.md) — 每个源携带的
  身份+分类元数据规范 v1.1(owner/scope/channel/provenance_tier/信噪评级)。
- ◇ [sources/admission_workflow.md](./sources/admission_workflow.md) — 新源提案→验证→准入的 add-only 流程。
- ◇ [sources/node_audit_playbook.md](./sources/node_audit_playbook.md) — 节点体检与修复手册
  (检查步骤/质量核对/故障模式目录/删类标准;v3.22.4 增《Content-quality proofing》——
  正文质量三层诊断、共享转换层保证清单、站点镶边三修法与新源上线抽查清单)。
- ◉ [sources/node_catalog_and_risks.md](./sources/node_catalog_and_risks.md) — 内置节点逐个的
  适配手法与稳定性风险评级(**快照 2026-06-16**,wave1–3 新节点待补,现势以注册表为准)。
- ◇ [sources/candidates/](./sources/candidates/README.md) — 候选源证据库(13 册,按厂商/板块;
  推荐名单+Parking Lot+验证笔记)。**看它的 README 即可知各册覆盖与消化状态**。

## design/ —— 设计刻度快照(◇ 参考)

静默仪器各工作区改造时的 HTML 设计样页(`dorami-*-quiet.html`),`index.css` 注释以
「刻度 1:1 取自」引用之;阅读器/设置柜/发现页的后续样页在 Claude Artifact(见各波记录)。
`dorami-score-tiers-quiet.html`(v3.54 阅读面小特性波,issue #54):新闻价值分按分值分档着色——三处触点 × 亮暗,
页顶控件可切灰线(6.0/5.0/不设)、分档 vs 连续插值、档间跨度;拍板 6.0 / 分档 / 陡峭。
`dorami-interest-axis-quiet.html`(v3.52 兴趣即透镜波,issue #27;**五稿,现行**):左栏一根轴「订阅 | 兴趣」/
标签行 / 列头收藏星 / 单标签视图 / 空态 / 移动抽屉,顶部一张与四稿的差异表。
`dorami-interest-lens-quiet.html`(同波四稿,含一稿「关注动态 + 主题流」、二稿「源栏兴趣组」、三稿「作用域推翻」
三次被否的注记;四稿三谓词面板实现后因「选源后订阅开关空转」被五稿取代):列头回显 / 折叠行 / 发现页兴趣段 / 移动壳。
`dorami-onboarding-quiet.html`(v3.45 阅读器可发现性波,issue #9):视图轨微标签 / 轨底改制 /
标题下动作行三提案并排(A 现状 / B 微标签 / C 完整态 / D 管理台导轨),可点试两击退出与译文二段。
`dorami-brief-quiet.html`(issue #23 第一项,我的早报卡片式日报):日期栏 + 报纸面(报头 / 分节卡片网格 /
衬线渐变评分)/ 同日多版胶囊条 / 移动日期条三画面,页头注释含一稿否决记录与二稿实现后记。

## archive/ —— 已完结方案与执行记录(⊘ 归档)

计划已落地或被取代的文档,按「故事」分组:阅读器演进五轮(v3.6–3.10)、源扩容 wave1–3、
静默仪器重构、前/后端结构重构、实体简化、crawl4ai 选型、竞品对照。
**查决策来龙去脉才来这里;判断现状请看 CLAUDE.md 与代码。**
→ 分组索引:[archive/README.md](./archive/README.md)
