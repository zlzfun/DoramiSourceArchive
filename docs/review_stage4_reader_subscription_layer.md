# Stage 4 检视报告：Reader Subscription Layer

- 分支：`codex/reader-subscription-layer`
- 被检视提交：
  - `99596d3 docs: clarify archive sync checksum rules`（落实 Stage 3 检视意见）
  - `c955585 feat: add reader subscription delivery`（Stage 4 主体）
- 检视日期：2026-05-25
- 检视者：Claude (Opus 4.7)

## 结论

实现完整、令牌模型稳健，达成 Stage 4 目标，可以合并。

- 新增 reader 侧订阅源 `ReaderSubscriptionRecord`（JSON 过滤条件 + 交付策略 + 启停 + 独立令牌）。
- admin CRUD：`/api/subscriptions`（含 rotate-token）。
- 令牌化消费端：`GET /api/public/subscriptions/{id}/dify/articles`，Bearer 或 `?token=`。
- 令牌独立于 admin 会话，仅存 HMAC-SHA256 哈希；契约文档 `docs/reader_subscription_contract.md` 齐备。

已运行 `tests/test_subscriptions.py` + `test_archive_sync.py` + `test_runtime_role.py` —— **11 passed**。

---

## ✅ 附带确认：Stage 3 检视意见已落实（`99596d3`）

上一轮 Stage 3 报告第 1 与第 5 项均已处理：

- 第 1 项：契约文档新增「Checksum Canonicalization」节，写明键排序、紧凑分隔符、`ensure_ascii=False`、标量类型与字段默认值，并附 Python 参考实现。
- 第 5 项：新增 `test_archive_sync_checksum_survives_json_roundtrip`，跨真实 `json.loads(_canonical_json(...))` 边界（含中文与乱序嵌套 extensions）锁定往返稳定。

---

## ✅ Stage 4 已验证正确

- **令牌安全**：`secrets.token_urlsafe(32)`（256 位熵），仅存 `hmac.new(AUTH_SECRET, token, sha256)` 哈希，校验用 `hmac.compare_digest` 常量时间比较；轮换覆盖旧哈希使旧令牌失效；停用订阅 `is_active=False` 返回 404。
- **消费端鉴权路径正确**：中间件对 `/api/public/subscriptions/*` 跳过 admin 会话校验，但仍走 reader 角色门禁；令牌校验在端点内 `resolve_subscription_by_token` 完成。测试验证 collector 角色下消费端 403。
- **交付策略有上限**：`normalize_delivery_policy` 将 `max_limit` 钳制 ≤500、`default_limit` ≤ `max_limit`，消费端 `limit` 受 `max_limit` 限制，防止超大分页。
- **复用既有过滤与序列化**：`apply_article_query_filters` + `serialize_dify_article`，与现有 `/api/dify` 交付面语义一致。
- **不回泄令牌**：admin 读取仅返回 `token_preview`；明文 `token` 仅在 create 与 rotate-token 响应各出现一次；消费响应只含 `{id, name}`。
- **测试覆盖**：令牌化交付+过滤、缺令牌/错令牌 401、轮换失效、collector 角色 403。表 `reader_subscriptions` 随 DatabaseStorage 自动建表（测试用全新文件库通过）。
- **文档一致**：plan、roadmap、dify_delivery 均已串联；reader UI 明确推迟到 Stage 5。

---

## ⚠️ 待办项

### 1.（低 / 安全）订阅存在性可被枚举：404 vs 401

- 现象：`resolve_subscription_by_token` 对「不存在/已停用」返回 404，对「存在但令牌错误」返回 401。持任意令牌串的攻击者可借小整数 ID 枚举出哪些订阅 ID 存在。
- 性质：ID 非机密、令牌仍是唯一闸门，严重度低。
- 建议：如需加固，对两种情况统一返回 401（或统一 404），避免区分存在性。

### 2.（低 / 安全运维）`?token=` 查询参数令牌会进日志

- 现象：URL 查询串中的令牌可能落入访问日志、代理日志、浏览器历史、Referer。
- 性质：已作为「无法设置 header 的工具」的便利回退记录在文档，属可接受权衡。
- 建议：在契约文档明确「优先 Bearer header，`?token=` 为较低安全级别回退」，并对此类令牌更积极地轮换。

### 3.（nit / 安全）令牌预览暴露首尾两端

- 现象：`token[:10]...token[-6:]` 暴露 `dsub_` 前缀 + 5 个随机字符 + 末尾 6 个随机字符（约 43 个随机字符中露出 11 个）。
- 性质：剩余熵仍 ~190+ 位，不可暴力破解，纯属观感。
- 建议：仅显示末尾 4–6 位是更常规、略稳的做法。

### 4.（低 / 范围）`is_public_subscription_path` 对该前缀下所有方法都跳过 admin 鉴权

- 现象：免 admin 鉴权是按前缀放行（非按具体只读路由/方法）。当前安全，因为该前缀下只有一个 GET 路由，未知路由在放行后会 404/405。
- 风险：若将来在此前缀下新增写路由，会默认变成未鉴权可达。
- 建议：保持该前缀仅承载只读路由，或将放行收敛到具体路由/方法。给后续维护者留个提示。

### 5.（低 / 耦合确认）订阅过滤词汇借用了 collector 血缘字段

- 现象：过滤项含 `job_id` / `job_run_id` / `fetch_run_id` / `run_scope` 等 collector 血缘标识。
- 与计划 review focus「Reader concepts do not expose collector implementation details unnecessarily」相关：这些是 admin 侧配置（不暴露给消费者），用于范围限定，尚属可辩护；但确实把订阅配置耦合到了 collector 血缘 ID。
- 提示：物理分离后的 reader 若未同步这些血缘 ID，相关过滤会静默匹配为空（契约文档「Current Limits」已有暗示）。确认这是预期即可。

### 6.（观察）订阅默认 `has_content=True`

- `SubscriptionFilters.has_content` 默认 True，`_model_to_clean_dict` 会保留它；故「空过滤」订阅默认只交付有正文记录，与 `query_subscription_articles` 的 `filters.get("has_content", True)` 一致。
- 属合理的 Dify 交付默认，仅提示这是有意行为（空过滤订阅不会交付无正文记录）。

### 7.（观察）暂无 reader 订阅管理 UI

- 当前订阅只能通过原始 API 管理，contract 文档与 plan 已明确把 reader UI 推迟到 Stage 5，符合预期，非缺口。

---

## 修改建议优先级

- 无强制阻塞项；Stage 4 可合并。
- 建议小修：第 1、2 项（枚举与查询令牌日志，均为低危安全项，第 2 项可仅文档化）。
- 可选/观感：第 3 项。
- 仅需确认 / 留待后续：第 4、5、6、7 项。
