# MCP Server 设计文档

**日期**：2026-05-12  
**状态**：已批准，待实现  
**范围**：在现有 DoramiSourceArchive 后端中集成 Streamable HTTP MCP Server，并在前端新增管理面板

---

## 1. 背景与目标

DoramiSourceArchive 是一个 AI 资讯聚合 CMS，内置语义搜索（ChromaDB）和 RAG 能力。本需求在此基础上对外暴露一个标准 MCP（Model Context Protocol）端点，使用户可以在任意 AI Agent（如 Claude Desktop、Dify 等）中通过配置 MCP URL 直接获取平台归档的 AI 资讯，无需额外开发。

典型使用场景：
1. 用户询问 Agent："最近的具身智能资讯有哪些？"
2. 用户询问 Agent："Anthropic 最近有什么动态？"
3. 用户让 Agent 生成今日 AI 资讯日报

---

## 2. 架构决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 传输协议 | Streamable HTTP（HTTP + SSE） | MCP 官方推荐的 remote server 方式 |
| 集成方式 | 挂载在同一 FastAPI 实例 `/mcp` 路径 | 零额外进程，MCP URL 固定为 `http://host:8088/mcp` |
| 实现库 | 官方 `mcp` Python SDK（`FastMCP`） | 协议维护交给官方，代码量最少 |
| 访问控制 | 当前阶段完全开放 | 后续可加 API Key Header 认证 |
| 软开关 | `mcp_enabled` 标志持久化到 SQLite | 前端可随时切换，重启后状态保留 |

---

## 3. 后端设计

### 3.1 新增文件：`src/mcp_server.py`

职责：构建 `FastMCP` 实例，注册 5 个工具，返回可供挂载的 ASGI app。

```
mcp_server.py
└── build_mcp_app(db_sink, vector_sink) -> ASGIApp
    ├── FastMCP("dorami-archive")
    ├── @mcp.tool: search_articles
    ├── @mcp.tool: browse_articles
    ├── @mcp.tool: get_article
    ├── @mcp.tool: list_sources
    └── @mcp.tool: get_rag_context
```

工具实现直接调用传入的 `db_sink`（SQLModel/SQLite）和 `vector_sink`（ChromaDB），不另建连接。

### 3.2 修改文件：`src/api/app.py`

新增内容：

1. **`AppSettingRecord` SQLModel 表**（若不存在则建表）
   - `key: str` (PK), `value: str`
   - 启动时读取 `mcp_enabled`（默认 `"true"`）到内存标志 `_mcp_enabled: bool`

2. **`MCPGateApp`（内联 ASGI 包装类）**
   - 每次请求检查 `_mcp_enabled`，若为 `False` 返回 `503 {"detail": "MCP server is disabled"}`
   - 否则转发给真实 MCP ASGI app

3. **挂载**：`app.mount("/mcp", MCPGateApp(build_mcp_app(db_sink, vector_sink)))`

4. **新增 REST 端点**：

   | 方法 | 路径 | 说明 |
   |------|------|------|
   | `GET` | `/api/mcp/status` | 返回 `{enabled, url, tools[]}` |
   | `POST` | `/api/mcp/toggle` | 切换 `_mcp_enabled`，写 DB，返回新状态 |

### 3.3 新增依赖

```
mcp[cli]>=1.0.0
```

加入 `pyproject.toml` 的 `dependencies`。

### 3.4 `AppSettingRecord` 表结构

```python
class AppSettingRecord(SQLModel, table=True):
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: str
```

---

## 4. MCP 工具规范

### 4.1 `search_articles`

**用途**：按语义查询检索文章，适用于"最近的 XXX 资讯有哪些"类问题。

**参数**：
- `query: str` — 搜索语句（支持中英文）
- `top_k: int = 10` — 返回数量
- `content_type: str | None` — 内容类型过滤
- `source_id: str | None` — 来源过滤
- `publish_date_gte: str | None` — 发布日期下限（`YYYY-MM-DD`）

**返回**：去重后的文章列表，每项含 `id`, `title`, `source_id`, `content_type`, `publish_date`, `url`, `summary`（前 200 字），`score`。

---

### 4.2 `browse_articles`

**用途**：按条件过滤浏览文章，适用于"Anthropic 最新动态"或日报生成场景。

**参数**：
- `source_id: str | None`
- `content_type: str | None`
- `publish_date_start: str | None` — `YYYY-MM-DD`
- `publish_date_end: str | None` — `YYYY-MM-DD`
- `has_content: bool | None` — 是否有正文
- `limit: int = 20`（上限 100）
- `skip: int = 0`

**返回**：文章列表，每项含 `id`, `title`, `source_id`, `content_type`, `publish_date`, `url`, `is_vectorized`。

---

### 4.3 `get_article`

**用途**：获取单篇文章完整内容。

**参数**：
- `article_id: int`

**返回**：`id`, `title`, `content`, `source_id`, `content_type`, `publish_date`, `url`, `extensions`（对象，非原始 JSON 字符串）。文章不存在时返回错误说明字符串。

---

### 4.4 `list_sources`

**用途**：列出平台中所有已知数据来源，帮助 Agent 了解可过滤的 `source_id` 和 `content_type`。

**参数**：无（暂不分页，来源数量有限）

**返回**：来源列表，每项含 `source_id`, `name`, `icon`, `content_type`, `category`, `last_fetch_time`（来自 `SourceStateRecord`）。

---

### 4.5 `get_rag_context`

**用途**：语义检索后组装成格式化上下文字符串，可直接拼入 LLM System Prompt，适用于问答场景。

**参数**：
- `query: str`
- `top_k: int = 8`
- `max_chars: int = 4000`
- `score_threshold: float = 0.3`
- `content_type: str | None`
- `source_id: str | None`
- `publish_date_gte: str | None` — `YYYY-MM-DD`

**返回**：格式化好的上下文字符串（含来源名、日期、标题、正文摘要），无结果时返回空字符串。

---

## 5. 前端设计

### 5.1 新增 Tab

在 `App.jsx` 导航栏末尾添加：
- **Tab ID**：`mcp`
- **图标**：`lucide-react` 的 `Plug2`（或 `Cpu`）
- **标签**：`MCP 接入`
- **组件**：`MCPTab.jsx`

### 5.2 `MCPTab.jsx` 布局

**区块 ①：状态与控制**
- 状态指示灯：绿色脉冲动画（运行中）/ 红色（已停止）
- 状态文字：`MCP Server 运行中` / `MCP Server 已停止`
- 切换按钮：`停止 MCP` / `启动 MCP`，点击调用 `POST /api/mcp/toggle`，结果通过 `showToast` 反馈

**区块 ②：接入地址**
- 显示完整 URL：`http://127.0.0.1:8088/mcp`
- 一键复制按钮（Clipboard API），复制后 2 秒内显示"已复制"
- MCP 停止时：URL 区块变灰，tooltip 提示"MCP 当前未运行"

**区块 ③：可用工具**
- 5 张工具卡片，每张含工具名、一句话说明、关键参数列表
- 数据来自 `GET /api/mcp/status` 返回的 `tools` 字段（静态，无需额外请求）

### 5.3 新增 API 函数（`api.js`）

```js
export const fetchMcpStatus = () => fetch('/api/mcp/status').then(r => r.json());
export const toggleMcp = () => fetch('/api/mcp/toggle', { method: 'POST' }).then(r => r.json());
```

---

## 6. 数据流

```
Agent (Claude / Dify / etc.)
  │ HTTP POST /mcp  (MCP Streamable HTTP)
  ▼
MCPGateApp (检查 mcp_enabled)
  │ enabled → 转发
  ▼
FastMCP ASGI App
  │ 解析 tool call
  ▼
Tool 实现函数
  ├── db_sink (SQLite) ← browse_articles, get_article, list_sources
  └── vector_sink (ChromaDB) ← search_articles, get_rag_context
```

---

## 7. 不在本次范围内

- MCP 认证（API Key）— 后续迭代
- MCP 调用日志 / 统计面板 — 后续迭代
- 每日日报生成 Skill — 后续迭代（依赖本 MCP）
- MCP Resources / Prompts 原语 — 暂只实现 Tools
