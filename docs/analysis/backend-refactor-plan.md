# 后端重构路线（书面化）

> 性质：**重构路线**（分阶段执行计划），非现状诊断。
> 配套文档：现状诊断见 [`backend-architecture-review.md`](./backend-architecture-review.md)。本文是其行动方案。
> 评估对象：`src/` 后端。日期：2026-06-28。
> 来源：两轮独立架构检视的合并结论（Claude 会话 + Codex），并经代码核实带 `file:line` 锚点。

## 0. 总原则

1. **先并发止血、再结构拆分、后数据层与任务化、最后编排下沉与安全硬化。** 操作稳定性优先于结构美观——最廉价、最立竿见影的修复（阶段 0）先做，再投入数周级的拆分。
2. **优先结构性拆分与任务化，不急于替换框架或数据库。** 只要应用工厂、router/dependency、migration、job 状态机落地，现有功能可平滑演进到更稳的 collector/reader/worker 架构。
3. **每阶段以"不改变外部行为"为先，迁移后跑现有测试**（`tests/`，见 CLAUDE.md 测试章）。各阶段独立可回退。

---

## 1. 现状债务摘要

四类结构性债务，均经代码核实：

| # | 债务 | 核实依据（`file:line`） | 共识 / 独有 |
|---|---|---|---|
| D1 | 同步重计算跑在 async 事件循环，零 executor 卸载 | 全库 `run_in_executor/to_thread` 用量为 0；向量 `save/search/rerank`（`src/storage/impl/vector_storage.py:228/305/373`）同步推理直接在 async 路径 | 共识（本会话强调严重度） |
| D2 | 长任务即请求，无后台化/续跑 | `vectorize/all-pending`（`src/api/app.py:4216`）、`reindex-all`（`src/api/app.py:4877`）串行 for 循环 | 共识 |
| D3 | 单进程单事件循环 + 生产 `reload=True` | `ecosystem.config.js` `instances:1, exec_mode:fork`；`src/main.py:21` `reload=settings.server.reload`，默认 True（`src/config.py:35`） | 本会话独有 |
| D4 | API 巨石 + 模块级全局单例 | `src/api/app.py` 5354 行 / 109 路由；`db_sink/vector_sink/pipeline/scheduler`（`src/api/app.py:294/323/327/341`） | 共识 |
| D5 | 零依赖注入；Session 手抄；热点 N+1 | `Depends` 用量 0；`Session(db_sink.engine)` 手开 109 次；`read_auth_token` 每请求多查库（`src/api/app.py:427`）；`run_vector_search` N+1（`src/api/app.py:4640`） | 共识（本会话量化） |
| D6 | 权限靠路径前缀表，新增端点易漏审 | `COLLECTOR_API_PREFIXES`/`READER_API_PREFIXES`（`src/api/app.py:164/186`）+ 中间件短路特判（`src/api/app.py:472`） | 共识 |
| D7 | 迁移手写、约束弱、引擎未调优 | `_ensure_compatible_schema` 一串 `ALTER TABLE`（`src/storage/impl/db_storage.py:24-120`）；裸 `create_engine` 无 WAL/busy_timeout（`src/storage/impl/db_storage.py:19`） | 共识 |
| D8 | SQLite↔Chroma 无一致性机制 | `is_vectorized` 为布尔，无 `indexing/failed/stale`；删除/标记/reindex 靠顺序调用（`src/api/app.py:4046/4203/4877`） | Codex 独有（本会话接纳） |
| D9 | 进度纯内存，重启即丢、无法多实例 | `src/pipeline/progress.py` 模块 dict；日报进度在 `src/services/daily_brief.py` 内存 | 共识 |
| D10 | 安全默认偏开发便利 | `AUTH_SECRET` 缺省从明文口令派生（`src/api/app.py:351`）；CORS `*`+credentials、`disable_ca_bundle`（`config/backend.example.ini`）；LLM key 存 KV（`src/services/daily_brief.py`） | 共识（本会话点出口令派生） |
| D11 | 编排耦合在 API 层 | `reader_ai_ask` 在端点内拼三档上下文、直调 `rag_context()`（`src/api/app.py:3155/3177`） | Codex 独有（接纳） |
| D12 | Fetcher 插件导入副作用 | `fetcher_registry.discover()` import 时扫描全 `impl/`（`src/fetchers/registry.py:195`） | Codex 独有 |

---

## 阶段 0 · 并发止血

**目标**：消除"单事件循环被同步推理冻结"这一最直接的稳定性风险。改动局部、风险低、当天见效。

1. **executor 卸载**（D1）：向量 `save/search/rerank`、RAG context、`reindex`、`vectorize/*` 中的同步重操作改用 `fastapi.concurrency.run_in_threadpool` 卸载到线程池，避免冻结事件循环。
2. **生产关 reload**（D3）：`src/config.py` 的 `ServerConfig.reload` 默认改 `False`，并在 `config/production.ini` 显式 `reload = false`。
3. **SQLite 调优**（D7 部分）：`src/storage/impl/db_storage.py` 的 `create_engine` 加 `connect_args={"check_same_thread": False}`，并在连接建立时执行 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`（用 `event.listens_for(engine, "connect")`）。
4. **长任务后台化（过渡版）**（D2）：`vectorize/all-pending`、`reindex-all` 改为提交后台任务并立即返回 `job_id`；过渡期可用 `BackgroundTasks` 或 `asyncio.create_task`，正式状态机见阶段 3。

**验收**：检索/向量化期间并发请求（登录、心跳、文章列表）不再被阻塞；长任务请求立即返回 `job_id`。

---

## 阶段 1 · 结构拆分（应用工厂 + Router + DI + 声明式权限）

**目标**：拆解 API 巨石，把权限从隐式前缀表变为显式声明。**保持所有端点路径不变。**

1. **应用工厂**（D4）：新增 `create_app(settings, deps)`；`db_sink/vector_sink/pipeline/scheduler` 收进 `app.state` 或 provider，去掉模块级全局。
2. **按域拆 `APIRouter`**：auth / accounts / admin / articles / fetchers·collection / vector·rag / reader·subscriptions / daily_brief / mcp。逐 router 分批合入，每批迁移后跑 `tests/` 全套。
3. **抽 dependencies**：`get_session`、`current_session`、`require_admin`、`require_reader`、`require_collector`、`get_vector_sink`（替代 `require_vector_sink`，`src/api/app.py:330`）。顺带消除 D5 的 Session 手抄与 `read_auth_token` 每请求查库（可缓存/合并）。
4. **声明式权限**（D6）：把 runtime role × account role 封装为 `AccessPolicy`，权限从前缀表迁到 route dependency；新增"遍历所有 `/api/*` 路由、校验每个端点都带权限元数据"的审计测试，防止新增端点漏审。

**验收**：现有测试全绿；权限审计测试通过；端点权限可在路由声明处直读。

---

## 阶段 2 · 数据层固化（Alembic + 约束 + 跨存储一致性）

1. **正式迁移**（D7）：引入 Alembic，以当前线上 schema 生成 baseline migration；之后所有 schema 变更只走 migration，废弃 `_ensure_compatible_schema`。
2. **补约束与索引**：article→fetch_run/job_run、subscription→user 等关键外键；高频过滤字段（`content_type`/`source_id`/`is_vectorized`/`publish_date`）建索引并由 JSON 提列，JSON 仅保留低频扩展元数据（`fetcher_ids_json`/`filters_json` 等）。
3. **跨存储一致性**（D8）：`is_vectorized` 升级为状态枚举 `pending/indexing/indexed/failed/stale`；新增 SQLite↔Chroma reconciliation 巡检任务，定期扫描并修复漂移；删除/重建/自动向量化都走可重试任务。

**验收**：`alembic upgrade head` 在干净库与现网库上均幂等可回放；对账任务能报告并修复差异。

---

## 阶段 3 · 任务化与拆 worker

1. **统一 jobs 状态机表**（D2/D9）：

   ```text
   jobs
   - id / type / status            # queued|running|succeeded|failed|cancelled
   - payload_json / result_json / error_message
   - created_by / created_at / started_at / ended_at
   ```

2. **迁移长任务**：`vectorize/all-pending`、`reindex-all`、每日日报、批量 fetch active sources、collection job run 全部走 jobs；API 只负责提交 + 查状态，前端轮询 `job_id`。
3. **进度持久化 + 拆 worker**（D9）：进度写 DB（替代 `src/pipeline/progress.py` 与日报内存进度）；scheduler/worker 与 API 进程分离，用 DB 锁或分布式锁避免重复调度（为多实例铺路）。

**验收**：长任务重启后可续/可查；多实例部署下同一 cron 不被重复执行。

---

## 阶段 4 · 编排下沉 + 安全硬化

1. **编排下沉**（D11）：新增 `RagService`/`ReaderContextService`，把"当前文章 / 订阅 RAG / 最近订阅文章 fallback"三档上下文组装从 `src/api/app.py:3155` 下沉到 service，供 API/MCP/worker/CLI 复用；API 层只做参数校验、权限提取与响应转换。
2. **安全硬化**（D10）：启动时校验 `auth.secret` 非默认值且**不从口令派生**（移除 `src/api/app.py:351` 的回退）；CORS 改为域名白名单；生产默认启用 CA 校验；LLM key / 代理凭证 / 图床 secret 迁到环境变量或 secret manager，DB 只存引用或非敏感覆盖项。
3. **可观测性**：结构化日志替代散落的 `print`（如 lifespan 内）；新增运行指标——任务耗时、失败类型、队列长度、向量索引滞后量。

---

## 2. 优先级矩阵

| 优先级 | 事项 |
|---|---|
| **高** | 阶段 0 全部；阶段 1（拆分 + 权限 dependency 化）；阶段 2 的 Alembic baseline；阶段 3 的向量化任务化 |
| **中** | scheduler 拆 worker；进度持久化；SQLite/Chroma 对账；RAG/ReaderAI 编排下沉 |
| **低** | fetcher manifest 化（D12）；JSON 字段渐进结构化；完整 metrics/trace |

---

## 3. 风险与回退

- 每阶段"不改外部行为优先、迁移后跑现有测试"；阶段 1 按 router 分批合入，单批可回滚；阶段 2/3 的 schema 迁移与任务化各自独立、互不阻塞。
- **两处对 Codex 原结论的修正**：
  1. **Fetcher 运行时改 `self.source_id` 的并发风险被高估**——`run_fetcher_with_tracking` 每次运行 `fetcher_class()` 新建实例（`src/api/app.py:2515`，registry 只返回类不缓存实例），并发运行各持独立实例，不存在共享可变状态。D12 的"导入副作用"部分成立，"运行时改 self 的并发隐患"可降级。
  2. **优先级上"并发止血"应前置于"拆 app.py"**——故本路线在 Codex 原五阶段前插入"阶段 0 并发止血"，用最小改动先解决当前最直接的稳定性风险。

---

## 4. 与现状诊断的映射

| 本文阶段 | 对应 `backend-architecture-review.md` 章节 |
|---|---|
| 阶段 0 | §2.4（同步任务在 async 路径）+ 本会话补充（reload/executor/WAL） |
| 阶段 1 | §2.1（API 巨石）+ §2.2（权限前缀表）|
| 阶段 2 | §2.5（迁移/约束）+ §2.6（跨存储一致性）|
| 阶段 3 | §2.3（进程内调度与进度）|
| 阶段 4 | §2.8（Reader AI/RAG 编排）+ §2.9（安全默认）|
| 低优先 | §2.7（Fetcher 插件）|
