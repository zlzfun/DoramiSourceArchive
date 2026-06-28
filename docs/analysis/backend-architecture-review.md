# 后端架构检视与优化建议

> 评估对象：DoramiSourceArchive 后端（`src/`）
> 性质：静态架构审查，未改动主代码。
> 日期：2026-06-28

## 1. 当前架构概览

后端是一个 Python 单体应用，主体由以下部分组成：

- API 层：`src/api/app.py`，FastAPI 应用、REST 端点、鉴权中间件、APScheduler 初始化、MCP 挂载。
- 抓取层：`src/fetchers/`，基于 `BaseFetcher` 的插件式抓取器注册中心。
- Pipeline 层：`src/pipeline/core.py`，驱动 fetcher 并广播到 storage。
- 存储层：`src/storage/impl/db_storage.py` 使用 SQLModel + SQLite；`src/storage/impl/vector_storage.py` 使用 ChromaDB + sentence-transformers。
- 服务层：`src/services/`，包含日报、账户、读者 AI、用量统计、内容分析、来源构建等业务逻辑。
- 分发层：Reader 订阅、公开 feed、MCP、RAG context。

核心数据流：

```text
Fetcher -> DataPipeline -> DatabaseStorage(SQLite)
                          -> ChromaVectorStorage(显式/自动向量化步骤)
```

整体上，项目已经具备一些清晰抽象：

- Fetcher 插件注册机制，新增来源可以通过实现 `BaseFetcher` 接入。
- `SourceConfigRecord` + `generic_rss` / `generic_web` 方向，正在把来源接入从写代码迁移到写配置。
- RAG 默认关闭、向量模型懒加载，降低启动成本。
- collector / reader runtime role 与 admin / user account role 的双轴访问控制。
- 日报、账户、AI 用量等部分业务已经下沉到 `services/`。

主要架构问题不在于“没有架构”，而在于这些抽象还没有充分隔离 API 巨石、进程内状态、持久化一致性和权限边界。

## 2. 主要问题

### 2.1 API 层职责过度集中

`src/api/app.py` 超过 5,300 行，承担了 FastAPI 应用创建、路由、鉴权、账号、Reader、订阅、Feed、抓取任务、调度器、日报、向量化、RAG、MCP、运维管理等职责。

典型位置：

- `src/api/app.py:283` 创建 FastAPI app。
- `src/api/app.py:294` 创建全局 `db_sink`。
- `src/api/app.py:323` 创建全局 `vector_sink`。
- `src/api/app.py:327` 创建全局 `pipeline`。
- `src/api/app.py:341` 创建全局 `scheduler`。

风险：

- 任意领域改动都容易触碰同一个大文件，代码审查成本高。
- 测试需要 monkeypatch 模块级单例，隔离性差。
- 新增功能容易绕过既有边界，导致服务层与 API 层继续纠缠。
- 后续拆 worker、拆 reader/collector 服务、替换存储都困难。

优化方向：

- 引入 `create_app(settings, dependencies)` 应用工厂。
- 按领域拆 router：`auth_router`、`articles_router`、`collector_router`、`reader_router`、`vector_router`、`admin_router`、`mcp_router`。
- 把 `db_sink`、`vector_sink`、`pipeline`、`scheduler` 放入 app state 或 dependency provider。
- 将可复用业务函数从 `app.py` 迁入 `services/` 或 `repositories/`。

### 2.2 权限控制依赖路径前缀，新增端点容易漏审

当前权限主要由集中式中间件和路径前缀表控制：

- `COLLECTOR_API_PREFIXES`：`src/api/app.py:164`
- `READER_API_PREFIXES`：`src/api/app.py:186`
- `require_admin_session` 中间件：`src/api/app.py:472`

该方案对当前代码有效，但存在明显维护风险：

- 新增 `/api/*` 端点时，必须手动加入前缀表。
- reader/collector 前缀重叠依赖短路顺序，认知负担高。
- 端点自身代码无法直观看出权限要求。
- 自动化审计困难，容易出现“端点存在但权限表缺失”的情况。

优化方向：

- 使用 FastAPI dependency 声明权限，例如 `Depends(require_admin)`、`Depends(require_reader)`、`Depends(require_collector)`。
- 将 runtime role 与 account role 封装成 `AccessPolicy`。
- 每个 router 挂默认权限依赖，例外端点显式声明。
- 增加测试扫描所有 `/api/*` routes，校验每个端点都有权限元数据。

### 2.3 进程内调度和进度状态限制扩展性

调度器在 API 进程中启动：

- lifespan 启动调度器：`src/api/app.py:266`
- `load_tasks_to_scheduler()`：`src/api/app.py:1967`
- `scheduler.remove_all_jobs()` 后重建所有 jobs：`src/api/app.py:1968`

进度状态也在进程内：

- 抓取进度：`src/pipeline/progress.py`
- 日报进度：`src/services/daily_brief.py:45`

当前 PM2 配置明确为单进程：

- `ecosystem.config.js:10` `instances: 1`
- `ecosystem.config.js:11` `exec_mode: fork`

风险：

- 多进程部署会重复调度，同一个 cron 可能被多个进程执行。
- 进度重启即丢失，只能通过启动自愈把 running 标记为 failed。
- 长任务和 API 请求共享进程，抓取、向量化、日报可能影响交互请求延迟。
- `remove_all_jobs()` 热重载调度配置时，对并发修改不够友好。

优化方向：

- 将 scheduler/worker 从 API 进程拆出。
- 持久化 job 状态，使用 `queued/running/succeeded/failed/cancelled` 状态机。
- 用数据库锁或分布式锁避免重复调度。
- 进度写入 DB 或 Redis，而不是模块级 dict。
- API 只负责任务提交、状态查询和结果展示。

### 2.4 同步 DB 与重计算任务直接跑在 async 请求路径中

大量 `Session(db_sink.engine)` 直接在请求处理里使用，同时部分 async 端点内部执行同步数据库、ChromaDB、embedding、reranker 等重操作。

典型位置：

- 批量向量化：`src/api/app.py:4203`
- 单篇向量化：`src/api/app.py:4531`
- 向量检索：`src/api/app.py:4598`
- RAG context：`src/api/app.py:4717`
- 全量 reindex：`src/api/app.py:4877`
- ChromaDB 写入：`src/storage/impl/vector_storage.py:228`
- reranker 懒加载与推理：`src/storage/impl/vector_storage.py:364`

风险：

- CPU/IO 密集任务阻塞事件循环或占满 API worker。
- 全量 reindex、批量向量化、日报生成等请求耗时不可控。
- API 超时、中断后任务状态难以准确恢复。

优化方向：

- 短期：重任务使用 `run_in_threadpool` 或后台任务隔离。
- 中期：引入持久化任务队列，向量化、reindex、日报、批量抓取都异步执行。
- 长期：将向量化 worker 与 API 进程分离，API 只查询任务状态。

### 2.5 数据库迁移体系和关系约束偏弱

当前数据库初始化方式：

- `SQLModel.metadata.create_all()`：`src/storage/impl/db_storage.py:20`
- 手写轻量 additive migration：`src/storage/impl/db_storage.py:24`

模型关系主要通过裸字段和 JSON 字符串表达：

- `CollectionJobRecord.fetcher_ids_json`：`src/models/db.py:69`
- `ReaderSubscriptionRecord.filters_json`：`src/models/db.py:203`
- `AppSettingRecord` 泛 KV：`src/models/db.py:237`

风险：

- 缺少正式 migration 版本，生产 schema 演进不可审计、不可回滚。
- 缺少外键、唯一约束、关系定义，数据一致性主要依赖应用代码。
- JSON 字符串字段难以索引、难以校验、难以做复杂查询。
- 后续迁到 PostgreSQL 或多实例部署时风险会放大。

优化方向：

- 引入 Alembic，先生成当前 schema baseline。
- 为关键关系补外键和索引，例如 article -> fetch_run/job_run、subscription -> user。
- 为高频过滤字段提升为列，JSON 只保留低频扩展元数据。
- 将 `AppSettingRecord` 中重要配置逐步迁到 typed setting 或专表。

### 2.6 SQLite 与 ChromaDB 之间缺少一致性机制

文章与向量索引分属 SQLite 与 ChromaDB，当前靠顺序调用保持一致：

- 删除文章时先删向量再删 DB：`src/api/app.py:4046`
- 写向量成功后标记 `is_vectorized`：`src/api/app.py:4203`
- 全量 reindex 先重建 Chroma collection，再重置 DB 标记并逐条写入：`src/api/app.py:4877`

风险：

- Chroma 成功、DB 更新失败，或 DB 成功、Chroma 失败，都会造成状态漂移。
- 全量 reindex 中途失败后，可能一部分文章已写入、一部分未写入。
- `is_vectorized` 是布尔值，无法表达 indexing/failed/stale 等状态。

优化方向：

- 引入 `vector_index_jobs` 或 outbox 表。
- 将 `is_vectorized` 扩展为状态字段：`pending/indexing/indexed/failed/stale`。
- 增加 reconciliation 任务，定期扫描 SQLite 与 Chroma 的差异。
- 删除、重建、自动向量化都走可重试任务。

### 2.7 Fetcher 插件发现存在导入副作用和运行时身份混用

注册中心 import 时扫描并导入 `fetchers.impl` 下所有模块：

- `fetcher_registry.discover()`：`src/fetchers/registry.py:195`

配置驱动 fetcher 会在运行时修改实例属性：

- `GenericRssFetcher` 修改 `self.source_id`：`src/fetchers/impl/rss_fetcher.py:230`
- `ConfigurableWebFetcher` 修改 `self.source_id` 与抓取配置：`src/fetchers/impl/configurable_web_fetcher.py:102`

风险：

- 模块导入副作用会影响启动和测试稳定性。
- 单个 fetcher 模块依赖异常会在启动阶段暴露。
- class 级元数据和运行时配置混用，长期容易出现身份、状态和并发认知问题。

优化方向：

- 引入显式 fetcher manifest 或 entrypoint。
- 将 `FetcherDefinition` 与 `FetcherRunContext` 分离。
- `source_id`、`source_config`、`params` 由 run context 承载，fetcher 实例保持无状态。
- 继续推进 `generic_rss/generic_web`，减少为单个来源写 Python 子类。

### 2.8 Reader AI 与 RAG 编排仍有 API 层耦合

`reader_ai_service` 已经封装了翻译和问答 LLM 调用，但上下文组装仍在 API 层，且 `reader_ai_ask` 直接调用 `rag_context()`：

- `reader_ai_ask`：`src/api/app.py:3155`
- 直接调用 `rag_context()`：`src/api/app.py:3177`

风险：

- API endpoint 变成业务编排函数，后续 MCP、worker、CLI 想复用同样逻辑会困难。
- RAG 的检索、权限范围、上下文组装与 FastAPI request 绑定较重。

优化方向：

- 新增 `ReaderContextService` 或 `RagService`。
- 将“当前文章 / 订阅 RAG / 最近订阅文章 fallback”三档上下文组装下沉到 service。
- API 层只做参数校验、权限提取和响应转换。

### 2.9 配置与安全默认值需要生产硬化

配置中存在面向开发便利的默认项：

- `disable_ca_bundle = true`：`config/backend.example.ini:20`
- CORS `allow_origins = *` 且 `allow_credentials = true`：`config/backend.example.ini:48`
- 默认 secret 示例：`config/backend.example.ini:37`

LLM API key 运行期覆盖也存入 `AppSettingRecord`：

- `KEY_LLM_API_KEY`：`src/services/daily_brief.py:76`

风险：

- 生产环境如果沿用宽松默认，会扩大安全面。
- secret 存 DB KV 没有额外加密或轮换机制。
- CORS + credentials 的组合需要按实际域名收紧。

优化方向：

- 生产启动时校验 `auth.secret` 不能是默认值。
- 生产默认启用 CA 校验。
- CORS 明确配置域名白名单。
- LLM key、代理凭证、图床 secret 迁到环境变量或 secret manager；DB 只保存引用或非敏感覆盖项。

## 3. 建议的优化路线

### 阶段一：降低 API 巨石风险

目标是不改变外部行为，先改善结构。

1. 新增 `create_app()` 应用工厂。
2. 抽出 router：auth、articles、reader、subscriptions、collector、vector、daily_brief、admin。
3. 抽出 dependencies：`get_db_sink`、`get_vector_sink`、`current_session`、`require_admin`、`require_reader`。
4. 保持现有端点路径不变，迁移后跑现有测试。

### 阶段二：引入正式 schema 管理

1. 引入 Alembic。
2. 以当前线上 schema 生成 baseline migration。
3. 后续所有 schema 变更只走 migration。
4. 补充关键索引和唯一约束。

### 阶段三：长任务任务化

优先迁移以下任务：

- `/api/vectorize/all-pending`
- `/api/vector/reindex-all`
- 每日日报生成
- 批量 fetch active sources
- collection job run

建议新增：

```text
jobs
- id
- type
- status
- payload_json
- result_json
- error_message
- created_by
- created_at
- started_at
- ended_at
```

API 返回 `job_id`，前端轮询 job 状态。

### 阶段四：权限声明化

1. 将权限从前缀表迁移到 route dependency。
2. 每个 router 设置默认权限。
3. 对公开 token 接口单独建 public router。
4. 增加 route 权限审计测试，防止新增端点未声明权限。

### 阶段五：存储一致性与可观测性

1. 为向量索引增加状态机和重试。
2. 增加 SQLite/Chroma reconciliation。
3. 结构化日志替代大量 `print`。
4. 增加运行指标：任务耗时、失败类型、队列长度、向量索引滞后量。

## 4. 优先级排序

高优先级：

- 拆 `app.py` 与应用工厂。
- 权限 dependency 化。
- 正式 migration。
- 向量化/reindex 任务化。

中优先级：

- 调度器拆为 worker。
- 进度持久化。
- DB/Chroma reconciliation。
- Reader AI/RAG 编排下沉到 service。

低优先级：

- fetcher manifest 化。
- JSON 字段逐步结构化。
- 更完整的 metrics/trace。

## 5. 总结

当前后端适合单实例、低到中等并发、个人或小团队使用场景，功能完整度较高，抽象方向基本正确。真正制约后续演进的是 API 层聚合过重、模块级全局状态多、长任务在请求路径执行、数据库迁移和跨存储一致性不足。

建议优先做结构性拆分和任务化，而不是急于替换框架或数据库。只要先把应用工厂、router/dependency、migration、job 状态机落地，现有功能可以较平滑地演进到更稳的 collector/reader/worker 架构。
