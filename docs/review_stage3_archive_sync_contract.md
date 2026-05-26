# Stage 3 检视报告：Archive Sync Contract

- 分支：`codex/archive-sync-contract`
- 被检视提交：
  - `c9e22d5 fix: normalize legacy collection scope labels`（落实 Stage 2 检视意见）
  - `5a5f6eb feat: add archive JSONL sync contract`（Stage 3 主体）
- 检视日期：2026-05-25
- 检视者：Claude (Opus 4.7)

## 结论

实现完整、契约清晰，达成 Stage 3 目标，可以合并。

- 新增首个 collector→reader 的 JSONL 归档同步契约：导出 `GET /api/archive/export/articles.jsonl`、导入 `POST /api/archive/import/articles.jsonl`。
- 导入按 `article.id` 幂等；保留全部身份与血缘字段；SHA-256 校验和保障完整性；reader 导入不触发公网抓取。
- 契约文档 `docs/archive_sync_contract.md` 齐备。

已运行 `tests/test_archive_sync.py` + `tests/test_runtime_role.py` —— **6 passed**。并额外**手动验证了校验和经 JSON 序列化→解析的线上往返稳定性**（含中文与乱序嵌套 extensions），结果一致。

---

## ✅ 附带确认：Stage 2 检视意见已落实（`c9e22d5`）

上一轮 Stage 2 报告第 1 项（历史运行名混合术语）已按建议处理：`FetchRunsTab.jsx` 新增 `normalizeCollectorDisplayName`，在展示层把历史 `run.name` 里的「节点组」替换为「采集范围」，不改动已落库数据。方案与建议一致，干净。

---

## ✅ Stage 3 已验证正确

- **契约符合计划要求**：JSONL bundle ✓；导入幂等 ✓；保留身份/血缘字段（`id` `source_id` `content_type` `publish_date` `fetched_date` `fetch_run_id` `job_id` `job_run_id` `source_group_id` `run_scope`）✓；校验和完整性 ✓；reader 导入仅写库、不抓取 ✓；面向外部自动化的文档 ✓。
- **导出复用 `apply_article_query_filters`**：过滤语义与 `/api/articles`、`/api/dify` 等保持一致，已确认该 helper 支持导出传入的全部 kwargs。
- **角色门禁正确**：`/api/archive/export` 归 collector、`/api/archive/import` 归 reader；测试验证了 reader 调导出 403、collector 调导入 403。
- **校验和稳健**：`_canonical_json`（`sort_keys=True` + `ensure_ascii=False` + 紧凑分隔符）使校验和对键顺序/中文/嵌套结构稳定，线上往返实测一致。
- **回填不留孤儿向量块**：回填路径将 `is_vectorized` 置 `false`；后续重向量化在 `vector_storage.py:221` 会先按 `parent_id` 删除旧块再重建，故原 header-only 块不会残留。
- **增量游标安全**：导出按 `fetched_date asc, id asc` 排序，`fetched_date_start` 用 `>=`，与上次游标边界重叠的记录会被再次导出，但导入幂等（skip）使其无副作用。
- **导入语义与文档一致**：新 id 插入、已存在跳过、已存在且无正文而来件有正文则回填并重置向量标志。

---

## ⚠️ 待办项

### 1.（中）校验和的「规范化 JSON」规则未对外部生产者写明

- 现象：`docs/archive_sync_contract.md` 只说校验和是 "SHA-256 hash of the canonical JSON representation of the `article` object"，但未写出外部自动化要复现的确切规则：键排序（`sort_keys=True`）、紧凑分隔符（`,`/`:` 无空格）、`ensure_ascii=False`（保留 UTF-8）、以及字段默认值约定（导出时 `content` 取 `record.content or ""`、`extensions` 为解析后的对象、`None`→`null`、`bool`→`true/false`、整数不带引号）。
- 影响：计划对本阶段的 review focus 明确要求「documented enough for external automation」。缺这些规则，外部生产者无法算出可通过校验的 checksum，只能逆向源码。
- 建议：在契约文档加一节「Checksum canonicalization」，逐条列出上述规则与字段默认值。属本阶段范畴，建议补齐。

### 2.（低）导出全量缓冲在内存，未流式输出

- 现象：`lines = [...]; body = "\n".join(...)`，单次最多 5000 条且每条含完整正文，响应体可能达数十 MB。
- 性质：v1 可接受（文档已注明 5000 上限）。
- 建议：后续大规模同步时可改为 `StreamingResponse` 逐行产出。非本阶段必须。

### 3.（低）最终 `session.commit()` 在逐行 try/except 之外

- 现象：逐行异常被捕获计数，但若 `commit()` 自身在 flush 时抛错（如未在处理期暴露的完整性错误），整个请求 500，而非返回 partial-success 汇总。
- 性质：因有逐记录校验，风险低。
- 建议：给 `commit()` 包一层防御性 try/except，或改为逐记录 flush，使错误也并入 `errors` 汇总。

### 4.（低 / 确认）两端共用同一 admin 会话，无 collector/reader 凭证区分

- 现象：导出与导入都走现有 admin session。跨主机同步代理需在两端都持有 admin 凭证。
- 性质：文档已将「consumer tokens」明确推迟到 Stage 4 reader 订阅层，属有意限制。仅作确认，无需本阶段处理。

### 5.（nit / 测试盲点）现有测试未跨越真实的 JSON 序列化→解析边界

- 现象：测试在进程内直接把 `archive_sync_line(...)` 喂给 `import_archive_sync_jsonl(...)`，校验和恒匹配，无法捕捉只在「上线序列化」时才出现的规范化回归。
- 我已手动验证往返一致；建议补一个 `json.loads(_canonical_json(line))` 后再导入的用例，把往返稳定性固化进测试。低优先级。

---

## 修改建议优先级

- 无强制阻塞项；Stage 3 可合并。
- 建议本阶段补齐：第 1 项（文档化校验和规范化规则，直接对应 stage review focus）。
- 可选优化（非本阶段）：第 2、3、5 项。
- 仅需确认：第 4 项（凭证模型留待 Stage 4）。
