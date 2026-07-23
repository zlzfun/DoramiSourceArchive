# DoramiSourceArchive · 哆啦美·归档中枢

一个带 RAG 能力的 **AI 内容聚合 CMS**：从多源抓取内容 → 存入 SQLite → 在 ChromaDB 建向量索引做语义检索，并按用户订阅做分发（Feed / MCP / 每日日报）。系统分为**采集/归档**（collector）与**阅读/分发**（reader）两层，由运行时角色与登录账号角色共同门控。

> 本文件是**全仓导航地图**（鸟瞰 + 路径索引）。需要深入时按「渐进式披露」逐层下钻 ↓

## 文档分层（按需下钻）

| 层级 | 文件 | 看什么 |
|---|---|---|
| ① 鸟瞰 / 导航 | **本 README** | 项目是什么、怎么跑、文件都在哪 |
| ② 架构详解 | [`CLAUDE.md`](./CLAUDE.md) | 数据流、关键设计决策、各模块职责、端点清单（最权威、最详尽） |
| ③ 契约与标准 | [`docs/`](./docs/README.md) | 下游集成契约、来源治理标准、配置说明 |
| ④ 实现 | `src/` · `frontend/` · `tests/` | 源码 |

> 三层之间**不重复**：架构性的「为什么」只写在 `CLAUDE.md`，本 README 只做「在哪里」的索引。

## 快速上手

```bash
# 后端（http://127.0.0.1:8088，热重载；API 文档 /docs）
uv sync
python src/main.py

# 前端（端口 5173，/api 代理到后端）
cd frontend && npm install && npm run dev

# 测试（整套，排除 tests/rag/ 自有 harness）
.venv/bin/python -m pytest tests/

# 生产部署（Docker:构建镜像 + 起容器 + 健康验证,一键;详见 docs/deploy-docker.md）
./deploy-docker.sh
```

数据落在 `data/`（SQLite `cms_data.db` + ChromaDB `chroma_db/`，已 gitignore）。

## 仓库地图

### 后端 `src/`
| 路径 | 概述 |
|---|---|
| `main.py` | 入口：启动 uvicorn（reload=True），设置 HF 镜像 |
| `config.py` | `load_config()` → settings 单例；读 `DORAMI_CONFIG_FILE`（否则 `config/backend.ini`） |
| `api/app.py` | FastAPI 主体：全部 REST 端点 + APScheduler 初始化 + 双轴角色门控 |
| `api/skill_router.py` | `GET /api/skill/daily-brief`：实时打包可下载的 Claude skill zip |
| `llm/` | OpenAI 兼容 LLM 客户端（`client.py`，绝不记录 api_key）+ 日报 map/reduce 提示词（`prompts.py`） |
| `services/daily_brief.py` | 每日 AI 资讯日报：对归档做 LLM map-reduce + 游标去重 + 内存进度 |
| `models/content.py` · `models/db.py` | 内容数据类（`BaseContent` 及子类）/ SQLModel ORM 表 |
| `fetchers/` | 插件式抓取器：`registry.py` 启动时自动扫描 `impl/`；基类见 `base.py`、`webpage_fetcher.py`、`github_release_fetcher.py` |
| `fetchers/impl/` | 各来源实现（RSS / GitHub Releases / repo+model / 网页列表 / curated / Playwright 渲染等） |
| `pipeline/` | `core.py` DataPipeline（fetcher → storages 广播）+ `progress.py` 内存进度 |
| `storage/impl/` | `db_storage.py`（SQLite）/ `vector_storage.py`（ChromaDB + 分块 + sentence-transformers） |
| `mcp_server.py` | `build_mcp_app()`：FastMCP streamable-HTTP，挂到 `/mcp` |
| `skill_templates/` | 可下载 Claude skill 模板源（被 `skill_router` 打包） |

> 各模块的设计决策（双维内容身份、`extensions_json` 序列化、向量分块、RAG 懒加载、双轴访问控制、Collection Jobs、Archive Sync 等）详见 [`CLAUDE.md`](./CLAUDE.md)。

### 前端 `frontend/src/`
| 路径 | 概述 |
|---|---|
| `api.js` | 所有后端 `fetch()` 的唯一出口 |
| `App.jsx` | 根：登录门控 + tab 路由（按运行时能力与账号角色过滤） |
| `components/` | 各 tab 与弹窗：`ReaderTab`（阅读器，user 唯一主界面）+`DiscoverPage`（发现页）、`DataTab`（知识台账）、`FetchTab`/`FetchRunsTab`（采集）、`VectorTab`（向量雷达）、`DailyBriefTab`（AI 日报）、`SettingsModal`（设置柜，含接入集成分区）、`AdminOpsTab`（运维管理）等 |
| `hooks/` · `utils/` · `config.js` · `sourceTaxonomy.js` | 复用 hook、工具函数、单点配置、来源分类表 |

### 文档 `docs/` — 见 [`docs/README.md`](./docs/README.md)
| 路径 | 概述 |
|---|---|
| `configuration.md` | `config/backend.ini`、运行时角色、两层部署 |
| `contracts/` | 下游集成契约：`feed_delivery` / `archive_sync` / `reader_subscription` |
| `sources/` | 来源治理：分类标准、收录策略、准入流程、节点审计 playbook、节点目录与风险、`candidates/` 各厂商候选源 |
| `backlog.md` | 跨波次待办总账（进行中/排队/展望） |
| `archive/` | 已完结方案与执行记录（按故事分组，见其 README） |
| `archive/` | 历史/已落地的计划文档（如 `frontend-optimization-plan.md`） |

### 脚本 `scripts/` — 见 [`scripts/README.md`](./scripts/README.md)
独立于后端运行时的运维/导出脚本（每日采集 job 幂等创建、shendeng 日报导出）。

### 测试 `tests/`
单测直接放在 `tests/test_*.py`（每个文件自举 `sys.path` 到 `src/`）。`tests/rag/` 是独立的离线 RAG 评测 harness（见 [`CLAUDE.md`](./CLAUDE.md) 的 *RAG Evaluation*）。

### 根目录配置 / 部署
| 路径 | 概述 |
|---|---|
| `CLAUDE.md` | 架构详解 + 开发命令（最权威） |
| `pyproject.toml` · `uv.lock` · `requirements.txt` | Python 依赖 |
| `config/*.example.ini` | 配置模板（真实 `backend.ini`/`production.ini` 不入库） |
| `deploy-docker.sh` · `docker-compose.yml` · `docker/` | 一键生产部署(Docker 双容器) |
