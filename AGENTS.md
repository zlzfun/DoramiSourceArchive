# AGENTS.md — Agent 入口(L0)

> ## ⚠️ 你在 `intranet` 分支(内网特殊适配分支)
> 一切改动只提交本分支、**绝不合并/cherry-pick 回 main**;同步单向 `git merge main`;
> 开工前先核对与 origin/main 的差异。完整纪律与本分支独有内容清单见
> [`CLAUDE.md`](./CLAUDE.md) 顶部「intranet 分支须知」块(该块仅存在于本分支)。

> 面向所有在本仓工作的 Agent。**权威的架构简报 + 开发命令 + 全部工程约定在
> [`CLAUDE.md`](./CLAUDE.md)**(与本文件同层,内容以它为准,此处不重复);
> 本文件只提供最小定向 + 文档分层索引入口。

## 一分钟定向

- **项目**:DoramiSourceArchive(哆啦美·归档中枢)——AI 内容聚合 CMS + RAG。
  采集/归档侧(fetcher → SQLite → 可选 ChromaDB 向量化)+ 读者分发侧
  (订阅制阅读器、令牌化 feed/MCP 交付、LLM 日报),按登录角色 admin/user 分面。
- **技术栈**:Python FastAPI + SQLModel + Alembic(后端,`uv` 管依赖,入口 `python src/main.py`);
  React + Vite + Tailwind v4(前端,`frontend/`,`npm run dev|build|lint`);测试 `pytest tests/`。
- **三条铁律**:① 每次模型改动配套 Alembic 迁移(drift 测试强制);② 改前端先读
  `docs/frontend/conventions.md`(token/角色类单一事实来源 = `frontend/src/index.css`);
  ③ 版本单一事实来源 = `src/version.py`(与 `pyproject.toml` 同步,合 main 打 tag)。
- **勿提交** `uv.lock` 的本地镜像改动(开发者本机换源所致,长期停留在工作区)。

## 文档分层索引(每层概括下一层,无需逐篇 grep)

| 层 | 文档 | 内容 |
|---|---|---|
| L0 | `CLAUDE.md`(本层) | 架构简报/开发命令/关键设计决策/端点清单 + 文档地图 |
| L1 | [`docs/README.md`](./docs/README.md) | 全量文档一行摘要 + 状态签(活跃/耐久参考/归档) |
| L2 | [`docs/archive/README.md`](./docs/archive/README.md) | 已完结方案按「故事」分组(查决策来龙去脉;勿据其判断现状) |
| L2 | [`docs/sources/candidates/README.md`](./docs/sources/candidates/README.md) | 候选源 13 册的覆盖与消化状态 |

高频直达:待办总账 [`docs/backlog.md`](./docs/backlog.md) · 前端纪律
[`docs/frontend/conventions.md`](./docs/frontend/conventions.md) · 对外契约 `docs/contracts/*` ·
源策展 `docs/sources/*` · 配置 [`docs/configuration.md`](./docs/configuration.md)。
