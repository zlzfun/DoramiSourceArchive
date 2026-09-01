# 文档总索引(L1)

> **分层导航机制**(本仓文档主要供 Agent 使用):
> **L0** `CLAUDE.md`(架构简报+开发命令,Claude Code 自动加载)与 `AGENTS.md`(通用 Agent 入口)
> 的「文档地图」→ **L1** 本文(全量一行摘要)→ **L2** 子目录 README
> (`archive/README.md` 按故事分组、`sources/candidates/README.md` 按板块+状态)。
> 每层给出下一层的内容概括,**无需逐篇 grep/read 即可定位**。
> 状态签:◉ 活跃(会随代码演进更新)/ ◇ 耐久参考(稳定,少改)/ ⊘ 归档(只增不改,勿据其判断现状)。

## 顶层(活跃)

- ◉ [backlog.md](./backlog.md) — **跨波次待办总账**(进行中/排队中/展望三档 + 近期已完结索引)。
  找「下一步做什么/哪些方向被搁置及原因」看这里。
- ◉ [admin-usability-audit.md](./admin-usability-audit.md) — **管理面/运维面可用性审计与共识清单(v3.40.3,
  150 人上量场景)**:Claude+codex 双独立审计交叉核验的 M01–M25 分层清单(P0 安全/成本护栏/身份 →
  P1 账户治理规模化[批量+过滤排序两点名痛点] → P2 运维可靠性 → P3 优化/文档),含三项待负责人决策与整改分层。
  **P0(M01–M04)v3.40.4、账户管理 V2(M05/M06/M07/M19)v3.41.0、运维列表规模化(M08/M09/M11/M17)
  v3.42.0 已落地**,整改记录见文首;余项与裁剪注记见 backlog。
- ◉ [configuration.md](./configuration.md) — `config/*.ini` 配置项逐节说明(runtime 角色/auth/RAG/LLM/
  网络代理),含生产 production.ini 与环境变量覆盖。
- ◉ [deploy-docker.md](./deploy-docker.md) — **Docker 部署(推荐路径)**:compose 双容器形态/
  用法与运维/ini 容器内语义差异/HTTPS/全新服务器部署与迁移/受限网络镜像源。
- ◉ [deploy-baremetal.md](./deploy-baremetal.md) — **裸机部署(第二条官方路径,v3.39.0 扶正)**:
  `deploy.sh` = uv + PM2 + 宿主 Nginx;两条路径选型对照/前置软件/脚本七步与六道护栏/
  ini 两节/HTTPS 两趟部署(certbot certonly 而非 --nginx)/全新服务器与迁移。
- ◉ [engage-sync-wave-plan.md](./engage-sync-wave-plan.md) — v3.18 互通波设计:读者反馈收件箱/
  管理员公告横幅(逐用户一次性 dismiss)/远程内容同步(接收方拉取,复用归档同步契约)。
- ◉ [source-collections-wave-plan.md](./source-collections-wave-plan.md) — 源合集波(v3.38.0 已实现,
  含实施记录):发现页策展合集(「源 ⇄ 合集」seg + 合集卡/详情 + 一键批量订阅);定调=目录呈现层
  批量动作而非订阅实体,代码注册表策展,交付链路零感知。首发五枚:HN 博客 2025/前沿实验室官方/
  国产开源模型动态/AI 编程工具动态/AI 深度写作(构思纪律与被毙候选见实施记录)。
- ◉ [user-custom-rss-wave-plan.md](./user-custom-rss-wave-plan.md) — 用户自定 RSS 源波(v3.40.0
  已实现):读者贴 URL 自助添加私有 RSS 源(参照 Folo);方案 B 自助+隔离(不进公共目录/
  all 检索域/日报/archive sync),最简正文(feed 给什么存什么,preview 仅守门不分型),
  入库存储+媒体不预取+删源即清;技术底座复用 generic_rss + SourceConfigRecord(加 owner 列)。
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

## frontend/ —— 前端纪律(◉ 活跃)

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

## archive/ —— 已完结方案与执行记录(⊘ 归档)

计划已落地或被取代的文档,按「故事」分组:阅读器演进五轮(v3.6–3.10)、源扩容 wave1–3、
静默仪器重构、前/后端结构重构、实体简化、crawl4ai 选型、竞品对照。
**查决策来龙去脉才来这里;判断现状请看 CLAUDE.md 与代码。**
→ 分组索引:[archive/README.md](./archive/README.md)
