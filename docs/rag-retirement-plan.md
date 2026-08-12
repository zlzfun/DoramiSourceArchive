# RAG 退役与问答检索重构方案

> 状态:**已完成**——Wave A 已实施(v3.30,2026-08-11);Wave B 已实施(v3.31,2026-08-12,
> 用户拍板跳过观察期连做,论证见下注*)。本文档转入执行记录归档态。 | 起草 2026-08-11 |
> 源起:RAG 层全面审视(本文 §1 即审视结论存档)
> 结论:**取缔向量 RAG,问答检索改「LLM 计划检索 + FTS5」两段式**;分两波实施(检索扶正 → 退役清仓),
> 波间留观察期。向量层保留**显式的重新引入触发器**(§4),届时按轻形态重建,不回滚现架构。

---

## §0 结论(TL;DR)

RAG 子系统是「早期实现、后期架空」:代码质量尚可,但**从未有过真实消费者**——生产库 4842 篇
文章向量化数为 **0**,`[rag] enabled` 默认关,日报完全不走 RAG,读者问答唯一的 RAG 路径
(ask scope=subscription)在 RAG 关闭时静默降级且无人察觉。与此同时它背着全项目最大的一份
复杂度税(双状态机、对账服务+巡检、torch extra、TEI/chroma compose profile、专属前端面)。

取缔的论证**不是「语料小到可以塞上下文」**(生产密度 ~100 篇/天已否决纯堆料),而是:

1. **检索必须保留,但不需要向量形态**——FTS5 trigram 已就绪(trigger 同步、结构性零漂移、
   随规模自动扩展),配 LLM 查询规划(中英关键词改写,恰好覆盖 bge-m3 被选中的唯一理由
   「跨语言召回」)+ 两段式选篇,成本恒定、不随语料涨。
2. **生产硬件从未支持过向量路径**——1.6GB 内存 / 2 核的生产机物理上跑不起 bge-m3 进程内推理
   或 TEI+chroma 双容器;「RAG 长期没开」不只是产品取舍,部署上它从来不可行。纯 LLM 方案的
   算力全在外部 API 侧,与本机资源解耦。
3. **增长曲线下向量形态的运维成本涨得比收益快**——reindex-all(改名漂移/换模型都要全量重建)、
   Chroma 内存、对账巡检的漂移面,全部随 10 万篇量级线性上涨;FTS 无此项。
4. **三层现成资产恰好构成替代检索栈**——FTS5(召回)+ 标题/摘要缓存(选篇)+
   **日报即月度压缩索引**(跨期问题直接检索 `dorami_daily_brief` 正文,向量方案从未利用)。

---

## §1 审视结论存档(2026-08-11)

### 1.1 消费面事实

- 生产(www.dorami.cloud)与本地开发库 `is_vectorized=1` 计数**均为 0**;`[rag] enabled` 两侧均为默认 false。
- 日报(daily_brief)全程不经 RAG;读者问答三档中 scope=article 零 RAG;scope=subscription 的
  RAG 档从未在生产走通,一直由第三档(订阅源最近 25 篇时序拼接)顶班,无用户投诉记录。
- MCP 的 `search_articles` 是其**唯一**搜索工具且强依赖向量层 → 当前生产形态下 MCP 消费者
  (含分发的 dorami-daily-brief skill)实际没有任何搜索能力,只能 browse。FTS5 落地后从未接入 MCP。

### 1.2 生产规模与密度(2026-08-11 实测)

- 存量 4842 篇 / 1560 万字符,均长 ~3.2k 字符,53 个源。
- 密度:2026-06 月 1183 篇 → 07 月 2637 篇 → 08 月前 11 天 1022 篇;**当前 ~90–100 篇/天、
  ~3000 篇/月、~1000 万字符/月**,逐月上翘(wave4 扩容、X 社交波入列)。近 30 天头部:
  IT之家AI 509 / HF Daily Papers 401 / HN AI 329 / 爱范儿 300 / 量子位 277。
- 外推:现势一年 +3.5 万篇;考虑源持续拓展,两年内 10 万篇量级是合理预期。
- **推论**:订阅 10 源的读者「最近一周」即 200–350 篇 / 70–120 万字符,任何「拉最近 N 篇直接
  塞上下文」的方案不可行——**现第三档降级(25 篇窗)按早期密度设计,当前覆盖不到一天,
  对「本周有什么」类问题已名不副实**,无论是否退役 RAG 都必须升级。

### 1.3 架构层问题

- **双存储一致性是自找的债**:SQLite→Chroma 靠调用顺序保一致,衍生出 `index_status` 五态、
  `vector_reconcile` 三桶对账、04:00 巡检、reconcile 端点整套擦屁股机器。对比 FTS5
  external-content + trigger 方案:结构性不可能漂移,零对账零状态列。
- **检索管线一式四份**:`run_vector_search` / `rag_context`(routers/vector.py)与 MCP 的
  `_search_articles_impl` / `_get_rag_context_impl` 四段近乎相同的「召回→parent 去重→阈值→组装」。
  已实际漂移:MCP 版不做 rerank、search 不从 DB 回填标题(chunk 元数据存旧名,v3.22.5 改名后失真)。
- **唯一读者路径没用上向量层的招牌能力**:reader ask 的 `_rag_fetch` 固定 top_k=6/max_chars=12000,
  不传 rerank、不传 expand_context、不传日期过滤——T12 重排 / T13 相邻扩展在唯一用户消费点全部闲置;
  新闻提问天然带时效性而纯语义召回对旧文零偏置,降级档反而在时效性上是对的。
- **社交波未与向量层对齐**:`social_post` 照常向量化,但转推顶层 content 是 `RT @xxx: …` 截断文本
  (展示契约要求用 `reposted.text`)→ 向量库索引的是截断错文;短推 chunk 头部占比极高,是检索噪声。

### 1.4 实现细节问题(两个实质 bug + 失效旋钮)

- **Bug 1(钉死在「永远待索引」)**:`db_storage.save` 权威元数据刷新分支(标题/日期变更,
  v3.23.2 引入)置 stale 但不清 Chroma chunks;`_save_blocking` 幂等检查见既有 chunks 带
  `has_body=True` 即 `return False` 拒绝重建 → 该文永远 `is_vectorized=False`,all-pending
  每轮重试失败,单篇端点误标 `failed`,chunk 头部与元数据永远留旧标题。写入层缺「先删后建」强制更新语义。
- **Bug 2(`/api/rag/similar` 越过订阅域)**:挂 `/api/rag` 前缀归 reader 门控,但 handler 无
  `resolve_scoped_search_args` 接线 → 受限读者可对任意 article_id 拉全库相似文章标题/URL,
  含未订阅源与隐藏源(v3.24 可见性口径失守)。**当前因 RAG 关闭(vector_sink=None → 503)而潜伏**;
  前端恰好也从未消费(`api.js ragSimilar` 是死函数)。⚠️ 护栏:Wave B 前若有人开启 RAG,须先修此洞。
- **失效旋钮**:`score_threshold=1.5` 对 cosine distance(值域 [0,2],bge-m3 相关命中 ~0.4–0.8)
  几乎什么都滤不掉,T4 过滤从未真正生效;`top_k*4` 召回再按 parent 去重,长文多 chunk 挤占候选槽,
  极端时凑不满 top_k 篇。
- 其余:TEI httpx.Client 不关闭、无重试;chromadb HttpClient 忽略 URL path/无鉴权;
  `list_parent_ids` 整集合 metadata 进内存;`ChromaVectorStorage.update()` 恒 False 死契约;
  `auto_vectorize_after_fetch` 用 print 非 logger;检索循环内逐条 `db_sink.get`(N+1);
  chunk 头部烤入友好源名(改名即漂移,v3.22.5 已知)。
- **评测资产已腐烂**:`tests/rag/testset_v1.json` 停在 2026-05-12,早于源改名与几乎全部现役源;
  evaluate.py 依赖已灌满的 live Chroma,而向量数为 0——harness 实际不可运行。

### 1.5 前端脱节(停在 v3.20,此后九波全部绕开)

- **产品层**:向量雷达是 admin-only 桌面工程师页签,全站零入口链向它;读者语义搜索(backlog ◇F)
  立项未做;移动壳连「问哆啦美」都没有;`rag/similar`(Folo 类标配「相似文章」)后端就绪 UI 从未落地。
- **实现层**:`VectorTab.jsx:13` 的 `accountRole==='user'` 分支因页签 `hideForReader:true` 永不可达
  (死码,但 `fetchSubscribedVectorStats` 每次挂载仍白发请求);`App.jsx` vector 面板守卫漏
  `rag_enabled`(与 nav 条件失配,RAG 关时深链 `#/vector` 挂载即打两个 503);reconcile 端点无前端入口;
  `ReaderAiPanel`「基于我的订阅」在 RAG 关闭时静默降级为时序拼接,UI 无提示(McpAccessSection 有
  「RAG 未启用」stamp,口径不一);`McpAccessSection.jsx` 把可配置的 `BAAI/bge-m3` 写死进文案。
- **样式层**:`index.css` ~85 行 `.vector-status-*` 死 CSS(零消费者,含硬编码色值)+ 暗色覆盖;
  `conventions.md:210` 仍把该死类指定为全站状态章「准绳」(应为 `.stamp-*`);
  `EmptyState.jsx` / `StatusBadge.jsx` 两个共享原语全仓只剩 VectorTab 一个消费者。

### 1.6 复杂度税盘点(退役收益即此清单)

chromadb 核心依赖 + rag-embedded extra(torch ~1GB,镜像 -1GB 已靠 extra 拆分但机器仍需装得起)+
TEI/chroma compose `--profile rag` + `is_vectorized`/`index_status` 双状态机 + reconcile 服务与
04:00 巡检 + auto_vectorize 抓取钩子 + jobs 两类长任务(all-pending/reindex-all)+ 向量雷达页签 +
台账向量列 + MCP 两工具 + 评测 harness + 内网分支 2 处 TLS 接线面。

---

## §2 目标架构:两段式 LLM 计划检索

### 2.1 检索管线(scope=subscription 的新主路径)

```
用户问题
  │
  ├─(0) 意图分流:时效型(「今天/本周有什么」)→ 直接走时序窗口档(§2.3),不检索
  │
  ├─(1) 查询规划(1 次轻量 LLM 调用,JSON mode):
  │      问题 → {keywords: [中文组, 英文组…], date_gte?, date_lte?, source_hint?, use_brief?}
  │      中英双语改写覆盖跨语言召回(bge-m3 的唯一不可替代点在此消解)
  │
  ├─(2) FTS5 召回(fts_search_ids,已就绪):
  │      多组关键词分别查询取并集 ∩ 订阅域(resolve_subscribed_source_ids,天然含隐藏源排除)
  │      ∩ 日期窗;候选上限 ~100
  │
  ├─(3) 选篇(1 次轻量 LLM 调用):
  │      候选表示 = 标题 + 来源 + 日期 + 引子(summary_zh 命中用摘要,未命中用正文首 ~200 字符;
  │      不强依赖摘要缓存——它是读者按需生成的,覆盖率低)
  │      → LLM 挑 5–8 篇最相关(~15k 字符输入,恒定,与总库规模无关)
  │
  └─(4) 注入回答:选中篇目全文(截断至 per-article 上限)→ 既有 answer_question
         sources 返回形状不变(title/source_id/source_url),前端零改动
```

- **日报即索引**:规划器可置 `use_brief=true`(跨期/盘点类问题),第 (2) 步改查
  `source_id=dorami_daily_brief` 的日报正文——每日 ~100 篇 → 一篇精选的现成压缩层,比检索原文更准更省。
- **成本**:每次 ask 多 1–2 次小调用(规划 ~几百 token,选篇输入 ~15k 字符),在现有 AI 配额
  体系内可忽略;计费归因并入 `ask`(不拆新 purpose,避免面板噪声)。
- **降级链**(graceful degrade,沿项目惯例):LLM 规划失败 → 用户原词直接 FTS(fts 不可用再回
  标题 LIKE,`fts_search_ids` 已带此契约)→ 零命中 → 时序窗口档。任何一级失败都不 5xx。

### 2.2 scope=article 与翻译/摘要

原样不动(本就零 RAG 依赖)。

### 2.3 时序窗口档升级

25 篇窗按当前密度(~100 篇/天)已名不副实。升级为:订阅域最近 **3 天**(上限 ~100 篇)的
「标题+引子」先经选篇调用压缩,再注入——即时效型问题与检索型问题共用第 (3)(4) 步,只是候选来源
不同(时序窗 vs FTS 命中)。

### 2.4 MCP 与 skill 兼容

`search_articles` / `get_rag_context` **保留工具名、换 FTS 实现**(skill 模板
`dorami-daily-brief/SKILL.md` 引用这两个名字,同名换芯零破坏):

- `search_articles`:FTS 召回 + 订阅域 + 元数据过滤,返回形状不变(distance 字段退役,
  改返回 FTS bm25 rank 或省略)。**顺手修复「RAG 关 = MCP 无搜索」的现状缺口**。
- `get_rag_context`:FTS 召回 + 与 §2.1 (3)(4) 同款组装(标题/来源/日期/链接头 + 正文块),
  输出契约(拼好的 context 字符串)不变。

---

## §3 分波实施

### Wave A:检索扶正波(✅ 已实施 v3.30,2026-08-11;RAG 开关此后无读者面语义)

后端:

- ☑ 新建 `src/services/reader_search.py`:查询规划(prompt 入 `llm/prompts.py`:SEARCH_PLAN/
  SEARCH_SELECT 两对)+ FTS 召回 + 选篇 + 编排;`assemble_reader_context` 收敛为 search_fetch
  闭包注入(D11 形态保留)。实现细节:候选 ≤8 时选篇短路省一跳;选篇 LLM 判「无一相关」时
  诚实返回空(问答侧如实说资料不足);FTS 全部不可用时 LIKE 标题回退;rowid IN 防御上限 10000。
- ☑ `reader.py /ai/ask`:scope=subscription 改走新管线(`_search_fetch` 闭包注入订阅域与
  UsageMeta,两次检索调用计费并入 ask);`_recent_subscribed_articles` 移入
  reader_search.fetch_recent_window,窗口升至 100 篇 + 选篇压缩(§2.3)。
- ☑ MCP 两工具同名换 FTS 实现(§2.4;distance 参数与字段退役,`_MCP_TOOLS_MANIFEST` 文案同步);
  `SKILL.md` 措辞同步。`build_mcp_app` 的 vector_sink 参数保留签名兼容,Wave B 删。
- ☑ 测试:新增 `tests/test_reader_search.py`(16 用例:规划解析清洗/失败降级、FTS 并集×订阅域×
  日期窗、选篇短路/索引校验/诚实空选/失败回退、编排降级链、日报即索引作用域含未订阅忽略);
  `test_reader_context` 改 search_fetch 委托;`test_reader_ai` 订阅域两用例改版(管线委托 +
  桩输出非 JSON 走降级链落窗口);`test_mcp` 检索工具七用例换芯。全套 561 passed。

前端(轻):

- ☑ `ReaderAiPanel` 范围选项加 hint(title):「在你订阅的内容里检索相关文章后作答」。
- ☑ `McpAccessSection`:工具描述改 FTS 文案,撤 requiresRag 灰显/「RAG 未启用」章、
  撤向量索引统计行(含 `BAAI/bge-m3` 硬编码与 fetchVectorStats 调用),ragEnabled prop 退役
  (SettingsModal/MobileSettings 透传同步删除)。

**观察期**:生产跑一个完整问答周期(建议 1–2 周),用 `AiUsageRecord` 的 ask 用量与实际问答质量
目检验收;期间 RAG 代码原样留守(它默认关,不碍事)。

### Wave B:退役清仓波(✅ 已实施 v3.31,2026-08-12;用户拍板跳过观察期连做)

> *跳过观察期的论证(2026-08-12 复核后拍板):观察期护错了对象——向量层不构成
> Wave A 新管线的回退路径(生产 0 向量化、从未运行;新管线的兜底是自身降级链或
> git revert),留着不增加任何安全冗余;「回头开 RAG」的退路因生产硬件(1.6GB 内存)
> 而本就不存在;删列零信息损失(两列全 False/pending)、无 Chroma 数据可丢;
> archive sync 线格式不携带 is_vectorized(import 侧本地生成),跨部署同步互通无破坏。

后端:

- ☑ `friendly_source_name` + `SOURCE_FRIENDLY_NAMES` **先迁出**至 `src/services/source_naming.py`
  (8 个模块引用:mcp_server / api/sources / app / routers/{subscriptions,admin,reader,vector,share}),
  再动存储层。
- ☑ 删 `src/api/routers/vector.py` 整册(/api/vectorize*、/api/vector*、/api/rag* 全部端点)、
  `src/storage/impl/vector_storage.py`、`src/services/vector_reconcile.py`、04:00 巡检 job、
  `auto_vectorize_after_fetch` 抓取钩子、`deps.get_vector_sink*`、app.py 前缀表两处、
  runtime `rag_enabled` 字段(前端消费点同波清)。
- ☑ `models/db.py`:删 `is_vectorized`/`index_status` 列与 `INDEX_STATUS_*` 常量;
  `db_storage` 删 mark_as_* / set_index_status 与 stale 触点;Alembic 删列迁移(SQLite batch,
  历史迁移与 backfill 迁移保留可跑);`?is_vectorized=`/`?index_status=` 查询参数退役。
- ☑ 依赖与部署:pyproject 删 chromadb 核心依赖与 rag-embedded extra → `uv lock` + 双导出清单;
  compose 删 `--profile rag`(chroma/TEI 服务)与 `WITH_RAG` build-arg、`docker/requirements-rag.txt`;
  config 删 `[rag]` 节 / RagConfig / `DORAMI_RAG_*` / `[models]` embedding/reranker 两键;
  本地 `data/chroma_db/` 目录清理(生产无向量数据,无需动)。
- ☑ 测试:删 test_rag_disabled / test_vector_reconcile / test_vector_remote / test_index_status /
  tests/rag/ harness;test_jobs、test_migrations、test_mcp 中相关用例改写。

前端:

- ☑ 删 `VectorTab.jsx` + 页签 + 孤儿原语(`EmptyState.jsx`/`StatusBadge.jsx`/`statusMeta.distanceMeta`);
  `DataTab` 删向量列/auto-vectorize 开关/批量向量化/reindex(RAG-off 三格看板形态转正);
  `api.js` 删 10 个 vector/rag 函数(含死函数 `ragSimilar`);`App.jsx` 删 vector 挂载与
  `runtimeInfo.rag_enabled` 消费点。
- ☑ `index.css` 删 `.vector-status-*` 死类(~85 行)与暗色覆盖三处;
  `conventions.md:210` 状态章准绳改指 `.stamp-*`。

文档:

- ☑ CLAUDE.md 重写 RAG 相关段(双形态/opt-in/vectorization admin-managed/reconcile/embedding 模型节
  → 换为「问答检索:LLM 计划检索 + FTS」一节);configuration.md 删 [rag];deploy-docker.md 删
  rag profile 节;docs/README.md 索引更新;本方案完结后按惯例归档至 `docs/archive/`。

版本节奏建议:Wave A = 下一个 MINOR,Wave B = 再下一个 MINOR(各一提交一 tag,沿一波一提交惯例)。

---

## §4 重新引入触发器(显式,非保险)

同时满足以下两条时重启向量层评估,否则不回头:

1. 语料 ≫ 10 万篇,**且**
2. ask 用量数据(`AiUsageRecord` + 问答抽查)显示「关键词完全不重叠的模糊语义查询」成为真实使用模式
   (LLM 关键词改写覆盖不到的残余)。

届时按**轻形态**重建:派生数据、外部推理(API embedding 或独立推理机)、可随时全量重建、
无状态列无对账——不恢复本次退役的架构。

## §5 风险与缓解

| 风险 | 缓解 |
|---|---|
| FTS trigram 对 <3 字符词失效(如「AI」单独成词) | 规划器 prompt 要求关键词 ≥3 字符/含上下文词;`fts_search_ids` 已有过短判定 → LIKE 回退 |
| LLM 规划/选篇质量不稳 | JSON mode + `parse_json_object` 既有健壮解析;降级链兜底(§2.1);观察期目检 |
| 选篇调用增加 ask 延迟 | 规划与选篇用低档模型即可(与翻译同档);两次调用均为小 payload |
| MCP 换芯后外部消费者语义变化(vector→keyword) | 工具名/出入参形状不变;docstring 明示 keyword 语义;skill 同步更新 |
| 删列迁移在存量生产库上的风险 | SQLite batch 迁移 + 迁移前 DB 热备(生产已有 /root/backups 惯例);drift 守卫测试兜底 |

## §6 波前护栏(已随 Wave B 完成而失效,留档)

- Wave B 落地前**不要开启** `[rag] enabled`(Bug 2 的 `/api/rag/similar` 越域会随之暴露);
  若观察期内确需临时开启,先给该端点接 `resolve_scoped_search_args`。

---

## §7 Wave B 执行注记(2026-08-12,实施与清单的差异点)

- Alembic 迁移 `f2c9d4e07a11`:SQLite 两个特有陷阱的处置——索引列不能原地 DROP
  (先显式删两索引再 batch 删列);batch 重建表会连带删掉 articles 上的 FTS 同步
  triggers(收尾 `ensure_fts` 幂等补回,数据未变无需 rebuild)。历史迁移
  `8bba6f81b240` 的回填 UPDATE 加了列守卫——新库经现行 metadata create_all 后走
  「stamp 基线 + upgrade head」收养路径时已无 is_vectorized 列,真旧库回填照常。
- `POST /api/public/subscriptions/{id}/vector/search`(方案清单遗漏项):路径留存、
  换 FTS 芯(与 MCP `search_articles` 同源复用 `_search_articles_impl`),
  body 的 score_threshold/rerank 字段保留兼容旧调用方、忽略。
- `content_analytics`/`/api/admin/content`:向量化聚合字段(vectorized_count/
  vectorized_rate)随列删除一并退役,运维内容板「向量化率」KPI 移除。
- 保留未动:`NetworkConfig.hf_endpoint`(通用网络配置,且属 intranet 分支
  merge 冲突敏感面);`storage.chroma_path` 配置键删除但本地 `data/chroma_db/`
  目录(空)由使用者自行清理;Bug 2(rag/similar 越域)随端点删除自然消亡。
- 验收:后端全套 529 passed;前端 eslint 零告警、vite build 通过。
