# Stage 1 检视报告：Runtime Role Boundary

- 分支：`codex/runtime-role-boundary`
- 被检视提交：`9fa2407 feat: add runtime role boundary`
- 检视日期：2026-05-25
- 检视者：Claude (Opus 4.7)

## 结论

实现质量良好，达成 Stage 1 目标，可以合并。核心要求满足：

- 默认 `role = all` 时无任何行为变化（无回归）。
- 角色禁用 API 时返回清晰的 403。

已运行 `tests/test_runtime_role.py` —— **3 passed**（双向 gating + `/mcp` + 角色规范化）。

下面的待办项按严重程度排序，供 codex 修改参考。

---

## ✅ 已验证正确

- 默认 `all` 下 `collector_enabled` 与 `reader_enabled` 均为 `True`，`disabled_runtime_surface()` 恒返回 `None`，中间件直接放行 → 本地开发零回归。
- `DORAMI_RUNTIME_ROLE` 环境变量覆盖 + `[runtime] role` 文件配置；`_runtime_role()` 做大小写/空白规范化并拒绝非法值。
- collector 才启动 APScheduler；reader 才构建/运行 FastMCP，否则 `_mcp_gate._app = None`、`_mcp_enabled = False`。
- 示例配置 `config/*.example.ini` 与 `docs/configuration.md` 同步更新。
- 前端：`/api/runtime` 驱动 tab 过滤；`switchTab` 为稳定 `useCallback`，重定向 effect 不会造成渲染循环。

---

## ⚠️ 待办项

### 1.（中 / 后续阶段）collector 角色仍加载 embedding 模型，与"精简采集端"目标相悖

- 现象：`src/api/app.py:190` 的 `vector_sink = ChromaVectorStorage(...)` 在 import 期无条件实例化，而 `src/storage/impl/vector_storage.py:180` 在 `__init__` 里立即加载 `BAAI/bge-m3`。collector 主机（外网）启动时会加载纯 reader 用途的向量模型。fetcher registry 在 reader 角色下也会全量 import（含 playwright 等）。
- 性质：**不是 Stage 1 回归**（`all` 行为不变，Stage 1 只界定 API 边界），但削弱拆分收益。
- 建议：记入 plan 的后续阶段，做按角色的惰性 / 条件初始化（reader 才建 `vector_sink`，collector 才扫描 fetcher impl）。Stage 1 不必处理。

### 2.（低）中间件顺序：runtime 闸门跑在 OPTIONS 放行和鉴权之前

- 位置：`src/api/app.py:284`（闸门）先于 `:296`（OPTIONS / public / `/mcp` 放行）与 `:298`（鉴权）。
- 副作用 A：CORS 预检 `OPTIONS` 打到被禁用面会返回 403 且不带 CORS 头（早返回绕过内层 `CORSMiddleware`），跨域浏览器消费方无法读取响应。实际影响低（前端隐藏禁用 tab，Dify 服务端调用）。
- 副作用 B：403（含完整 `runtime_capabilities`）会返回给未鉴权调用方，属轻微角色信息泄露。
- 建议：让 `OPTIONS` 先于 runtime 闸门放行；如在意泄露，可把闸门移到鉴权判断之后。

### 3.（低 / nit）路径匹配不一致

- 位置：`src/api/app.py:130` 的 `/mcp` 用裸 `path.startswith("/mcp")`（会误匹配 `/mcpfoo`），而 API 前缀走精确的 `_path_matches`。
- 建议：统一为 `path == "/mcp" or path.startswith("/mcp/")`，或复用 `_path_matches`。当前无此类路由，无害。

### 4.（低）分类选择需确认是否符合预期

- `/api/articles`（浏览 / CRUD / 手动新增）不在任何列表 → 两角色都可用。单仓阶段共享 DB 合理，但 reader 也能增删改归档记录，请确认有意为之。
- `/api/vectorize` + `/api/vector` 归 reader → collector 无法建向量索引。符合计划；若本地有"采集端跑向量化"工作流，显式切 `collector` 会失效（`all` 不受影响，非默认回归）。
- `/api/import/social-posts` 归 collector：与计划里 reader 的"archive import"（指 Stage 3 collector→reader 同步导入）不是一回事，作为入站采集归 collector 合理。

### 5.（nit）`disabled_runtime_surface(method, path)` 的 `method` 参数未使用

- 位置：`src/api/app.py:127`。为未来按方法细分预留，但目前是 dead param。
- 建议：加注释说明意图，或先去掉。

---

## 修改建议优先级

- 立即可改（几分钟）：第 2、3、5 项。
- 记入 plan 后续阶段，本阶段不动：第 1 项。
- 仅需确认无需改代码：第 4 项。
