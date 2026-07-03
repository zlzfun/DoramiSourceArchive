# 后端重构 · 逐项进度追踪

> 本文件是 [`backend-refactor-plan.md`](backend-refactor-plan.md)（重构路线）的**活文档配套**：
> 逐项记录每个计划条目的状态、对应提交与备注。每完成一个增量即更新此表。
> 诊断出处见 [`backend-architecture-review.md`](backend-architecture-review.md)。

**最近更新**：2026-07-03 · 当前分支 `refactor/phase0-concurrency` · 测试基线 **304 passed**
**总状态**：✅ **重构收官** —— 五阶段全部高/中价值项完成；低价值/单节点无需求项已明确决策暂缓或舍弃（见文末《收官小结》）。

## 状态图例
- ✅ 已完成
- 🟡 部分完成（子项拆分见备注）
- ⬜ 未开始
- ⏸️ 有意暂缓（含原因）

---

## 阶段 0 · 并发止血 —— ✅ 完成（本会话之前，phase0 分支）

| 项 | 状态 | 依据 / 提交 |
|---|---|---|
| D1 executor 卸载（向量 save/search/rerank、reindex、vectorize 同步重操作） | ✅ | `vector_storage.py` 多处 `asyncio.to_thread`；reindex 走线程池 |
| D3 生产关 reload | ✅ | `config.py:37` `ServerConfig.reload=False` |
| D7(部分) SQLite WAL + busy_timeout + check_same_thread | ✅ | `db_storage.py:_enable_sqlite_concurrency`（connect 事件挂 PRAGMA） |
| D2 长任务后台化（过渡版） | ✅→ 升级 | 原 `background_jobs`（内存），已在阶段3 升级为持久化 jobs |

---

## 阶段 1 · 结构拆分（应用工厂 + Router + DI + 声明式权限）—— ✅ 收官（工厂/权限迁移已决策暂缓）

| 项 | 状态 | 依据 / 提交 | 备注 |
|---|---|---|---|
| D4 应用工厂 `create_app(settings, deps)`，单例收进 app.state | ⏸️ 舍弃 | — | 决策：模块级 `app` + 单例与测试大量 `monkeypatch.setattr(api.app, ...)` 深绑，工厂化需重写全部夹具；唯一实质收益（多实例/隔离）在单节点 `all` 部署无需求。lazy `_app()` 已足够解 Router 解耦 |
| 按域拆 `APIRouter`（15 域） | ✅ | 阶段1 系列（截至 `084e0a4`/`8b36e67`/…） | app.py 5354→1677 行；路径全不变 |
| 抽 dependencies（get_session/get_db_sink/get_vector_sink[_optional] 等） | ✅ | `api/deps.py` | D5 的 Session 手抄在 router 内基本消除 |
| `read_auth_token` 每请求查库缓存 | ⏸️ 不做 | — | 决策：每请求校验 DB **正是**「禁用/删除账户即时吊销 cookie」的安全特性；缓存会削弱它。单条主键查询开销可忽略，当前即正确 |
| D6 声明式权限：前缀表 → route dependency（AccessPolicy） | ✅ 以审计护栏收口 | 审计测试 `b90483e` | 决策：安全实质（新增端点漏审防护）已由「遍历 `/api/*` 校验鉴权归类」审计测试兜底；中间件 + `COLLECTOR/READER_API_PREFIXES` 前缀表是集中、经测试的有效模型。全量迁 15 router 为纯风格性改动、有真实回归风险 → 暂缓 |

---

## 阶段 2 · 数据层固化（Alembic + 约束 + 跨存储一致性）—— ✅ 收官（迁移/索引/对账/枚举完成；FK/JSON提列/唯一约束决策舍弃）

| 项 | 状态 | 依据 / 提交 | 备注 |
|---|---|---|---|
| D7 引入 Alembic + 基线；之后只走 migration | ✅ | `bfe3e07` | `alembic/`、基线 `5ee31a7c5393`、`ensure_migrated`（老库 stamp+upgrade）、deploy 钩子、漂移守卫测试 |
| 废弃 `_ensure_compatible_schema` | ⏸️ | — | 暂留作 create_all 共存的向后兼容；漂移守卫保证 create_all==迁移链，无漂移风险。可后续删 |
| 补索引（修复旧库缺失的声明索引） | ✅ | `d61f9be` | 迁移 `ccae184ca0a1` 幂等补齐 19 个缺失单列索引 |
| 补外键（article→fetch_run/job_run、subscription→user） | ⏸️ 舍弃 | — | 决策：SQLite 默认不强制 FK（需逐连接 PRAGMA），加约束是装饰性；开启强制对历史孤儿有破坏风险。收益低 |
| 高频 JSON 字段提列 | ⏸️ 舍弃 | — | 决策：现有单列索引已覆盖高频过滤（content_type/source_id/publish_date/index_status）；JSON 仅承载低频扩展，提列收益低 |
| 唯一约束（聚合表 ai_usage/reader_reads 的 upsert 元组） | ⏸️ 舍弃 | — | 决策：`record_usage`/`record_read` 已在应用层 find-or-create 保证唯一；SQLite 加唯一约束需 batch 重建表、收益低 |
| D8 跨存储一致性：SQLite↔Chroma 对账 | ✅ | `27d0292` | `services/vector_reconcile.py` + `GET/POST /api/vector/reconcile`（三类漂移分类+修复） |
| D8 `is_vectorized` 升级状态枚举 pending/indexing/indexed/failed/stale | ✅ | `7a177a4` | 附加式 index_status 列（is_vectorized 保留为派生位）+ 迁移 `8bba6f81b240` 回填；存储态转移 + save/编辑 stale + 单篇 indexing/failed + 对账 stale；`?index_status=` 过滤/载荷；知识台账状态徽标（失败/陈旧语义色 + 按状态筛选）已上前端 |
| 对账定为巡检任务（定时） | ✅ | `execute_vector_reconcile_job` | RAG 开启时注册每日 04:00 APScheduler 巡检（只报告、发现漂移告警；修复仍走管理员端点） |

---

## 阶段 3 · 任务化与拆 worker —— ✅ 收官（长任务已任务化；进度落库/worker 拆分决策暂缓，多实例才需）

| 项 | 状态 | 依据 / 提交 | 备注 |
|---|---|---|---|
| D2/D9 统一 jobs 状态机表 | ✅ | `a6997f8` | `JobRecord` + `services/jobs.py`（queued/running/succeeded/failed/cancelled）；进度节流落库；取代内存 background_jobs |
| 迁移：vectorize/all-pending、reindex-all | ✅ | `a6997f8` | 走 `jobs.launch`，前端 pollJob 不变 |
| 迁移：每日日报（手动生成） | ✅ | `6aa1382` | POST 返回 job_id；定时路径保持直连（非请求，已有自身 run 记录） |
| 迁移：批量 fetch active sources、collection job run | ✅ | `4f88b0b` | collection-jobs/{id}/run + fetch-active-rss/web 走 jobs.launch，前端 pollJob；fetch/batch 与单节点 fetch/{id} 暂留同步 🟡 |
| D9 进度持久化（pipeline/progress.py + 日报 _PROGRESS → DB） | ⏸️ 暂缓 | — | 决策：进度落库的实际消费方是「API 与 worker 分进程后 API 读 worker 写的进度」；单进程下仅写放大、无新消费方。任务**终态**已由 jobs 表持久化。待确有多实例/worker 拆分需求时与之一并做 |
| D9 scheduler/worker 与 API 进程分离 + DB 锁防重复调度 | ⏸️ 暂缓 | — | 决策：为多实例部署铺路；当前单节点 `all` 部署无此需求，改动大、风险高。jobs 表已为将来拆分预留持久化地基 |

---

## 阶段 4 · 编排下沉 + 安全硬化 —— ✅ 收官（D10/D11 完成；metrics/trace 暂缓）

| 项 | 状态 | 备注 |
|---|---|---|
| D11 ReaderContext 下沉三档上下文 | ✅ | `reader_ai.assemble_reader_context`（注入 rag_fetch/recent_fetch 解耦 request，三档 graceful degrade 可独立单测）；reader.py 端点委托之。RagService 未单独抽（rag_context 仍在 app.py，无第二消费方，暂不过度设计） |
| D10 auth.secret 非默认/不从口令派生 + 启动安全校验 | ✅ | `api/security_checks.py`：AUTH_SECRET 回退去掉口令（仅 database_url 派生）；`enforce_security_config` 在生产姿态（cookie_secure）下对 secret 未设/占位、CORS *+credentials 拒绝启动，dev 仅告警 |
| D10 CORS 域名白名单（启动校验） | ✅ | *+credentials 组合在生产姿态被拒；production.example 加白名单指引 |
| D10 生产默认 CA 校验 / 密钥迁 env 或 secret manager | 🟡 收口 | CA 禁用在生产姿态启动告警；LLM key/base_url/model 已支持 `DORAMI_LLM_*` env 覆盖。图床/代理密钥迁 secret manager 暂缓（低频、非核心链路） |
| 可观测性：结构化日志替代 print、任务/队列/向量滞后指标 | 🟡 部分 | 新增模块（jobs/security/对账巡检）均用 `logging`；全量替换散落 `print` 与接入 metrics/trace 属低优先运维项，暂缓 |

---

## 收官小结

**已完成（本轮 refactor 全部高/中价值项）**：阶段 0 并发止血；阶段 1 API 巨石按域拆 15 Router + DI（app.py 5354→1677 行）+ 鉴权审计护栏；阶段 2 Alembic 版本化迁移 + 索引修复 + SQLite↔Chroma 对账（按需端点 + 每日巡检）+ `index_status` 状态枚举（后端 + 前端）；阶段 3 持久化 jobs 状态机 + 五类长任务全部「提交+查状态」化（vectorize all-pending / reindex / 日报 / 批量抓取 / 采集运行）；阶段 4 启动安全校验 + AUTH_SECRET 去口令派生 + reader 三档上下文下沉。测试 249 → **304**，全程全绿。

**已决策暂缓/舍弃（低价值或单节点无需求，见各表备注）**：应用工厂化、权限 route-dependency 全量迁移（安全实质已由审计护栏收口）、`read_auth_token` 缓存（会削弱即时吊销）、FK/JSON提列/唯一约束（SQLite 下装饰性或需重建表）、进度落库 + scheduler/worker 拆分（多实例才需，jobs 表已预留地基）、full metrics/trace 与 fetcher manifest 化（低优先运维项）。均非阻塞项，待出现多实例部署或具体需求时再按需推进。

---

## 提交映射（本轮 refactor 时间线，倒序）

| 提交 | 阶段 | 摘要 |
|---|---|---|
| `7a177a4` | 2/3 | is_vectorized 升级 index_status 枚举（附加式 + 回填迁移） |
| `4f88b0b` | 3 | 批量 fetch active + collection job run 迁上 jobs |
| `d4e94e5` | docs | 逐项进度追踪活文档 + 计划交叉链接 |
| `6aa1382` | 3 | 手动日报生成迁上持久化 jobs |
| `a6997f8` | 3 | 持久化 jobs 状态机替代内存 background_jobs |
| `27d0292` | 2 | SQLite↔Chroma 向量索引对账服务 + 端点 |
| `d61f9be` | 2 | 对账迁移补齐旧库缺失声明索引 |
| `bfe3e07` | 2 | 引入 Alembic 版本化迁移 + 基线 |
| `b90483e` | 1 | 路由鉴权覆盖审计测试（Router 化护栏） |
| `084e0a4` 及更早 | 1 | 按域拆 Router 系列（迁出 fetchers/采集调度/archive-sync/feed/articles…） |
