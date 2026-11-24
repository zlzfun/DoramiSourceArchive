# Admin Reader Briefs M2M 契约与对接说明 (v1.0)

本文档对应 Dorami × `dorami-bot` 个人日报机对机（M2M）接口契约 v1.0 及 2026-11-18 对齐说明（`A01–A08`、`R01–R04`、`DEV01–DEV10`）。冻结共享基线存放于 `contracts/briefs-v1/shared/`（含 `CONTRACT.md`、`openapi.yaml`、`SHA256SUMS` 与 18 个示例响应）。

## 1. 业务端点与请求语义（R01）

```http
GET  /api/admin/reader/briefs/today?employee_number=l00976966
POST /api/admin/reader/briefs/today/ensure?employee_number=l00976966
```

- **入口日期锁定（C09）**：请求进入中间件第一时间（鉴权之前）捕获 `Asia/Shanghai` 当日日期 `report_date`，后续查版、创建与跨午夜完成均沿用该日期。
- **参数与空 Body（A03 / C02 / C03）**：
  - `employee_number` 位于 query，必填、仅允许出现一次、长度 `1–128` 且不含任何空白字符；按原值精确匹配 `users.w3_uid`（不 trim、不改大小写、不剥离首字母，严禁匹配纯数字列 `w3_employee_number`，也严禁回退匹配 `users.username`，见 A02 / C01）。
  - 未知 query 参数一律返回 `422 / INVALID_ARGUMENT`。
  - `POST /today/ensure` 实际请求体必须为空字节（`{}`、`null`、空格、换行均为 `422 / INVALID_ARGUMENT`）；服务端不因缺少 `Content-Length` 请求头额外拒绝请求。

### 1.1 `users` 表三列与请求参数格式对齐表（严禁混用）

| 字段 / 参数 | 真实值示例 | 格式特征 | 在本接口中的角色 |
| --- | --- | --- | --- |
| Query 参数 `employee_number` | `l00976966` | 带首字母的 W3 账号标识（来自 bot `subscriptions.user_id`） | **GET 与 POST ensure 唯一合法 query 参数名** |
| `users.w3_uid` | `l00976966` | 带首字母的 W3 账号标识 | **唯一查询匹配列**（`WHERE users.w3_uid = :employee_number`） |
| `users.username` | `l00976966`（或本地账号名） | 登录主键（SSO 账号常与 `w3_uid` 同值，但本地/测试账号可不同） | **仅作为解析结果用于查早报并回显至响应 `username`**，绝不作入参匹配或兜底 |
| `users.w3_employee_number` | `00976966` | 无首字母的纯数字工号（内网 SSO 表列） | **本接口完全不使用**；传入 `00976966` 不会命中，合入内网分支时切勿改绑到此列 |
- **只读与首版原子创建（DEV04 / D01–D10 / C11）**：
  - `GET` 仅查询 `(owner, report_date)` 下最大 `revision`，缺版返回 `200 / missing`（`edition: null`），无任何生成副作用。
  - `POST /today/ensure` 在已有任意状态版本时原样返回最大 `revision`，不重新生成、不接管既有 `pending`；仅在缺版时经共享创建门 `ensure_first_personal_digest_edition`（进程内互斥 + SQLite `BEGIN IMMEDIATE` + 唯一约束兜底）原子创建 `revision=1, status=pending`，交由 `BriefRuntime` 受监督执行并同步等待本次创建的 `edition` 返回（正常响应不返回 `missing`）。

## 2. 鉴权、响应头与错误契约（R02 / A01 / A04 / A05）

- **应用响应固定头**：`Content-Type: application/json`、`Cache-Control: no-store`、`X-Request-ID`（请求头合法匹配 `^[A-Za-z0-9._-]{1,128}$` 时回显，缺失或非法时服务端生成新 ID，绝不因此返回 `422`）、`X-Briefs-Contract-Version: 1.0`。
- **统一错误体**（仅作用于上述两个端点及其应用中间件）：
  `{ "error": { "code": "...", "message": "...", "request_id": "..." } }`

| HTTP | `error.code` | 触发条件 |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 缺失、签名无效、过期或已吊销的 `dorami_admin_session` Cookie（忽略 `Bearer`） |
| 403 | `FORBIDDEN` | 会话有效但 `role != "admin"` 或当前运行角色关闭 reader 面 |
| 403 | `CSRF_REJECTED` | 部署启用 CSRF 校验钩子且未通过 |
| 404 | `ACCOUNT_NOT_FOUND` | 合法工号未匹配到活跃账号 |
| 422 | `INVALID_ARGUMENT` | query 参数缺失/空白/超长/重复/未知，或 `POST` 非空 body |
| 429 | `RATE_LIMITED` | 应用限流拒绝（透传 `Retry-After`） |
| 500 | `INTERNAL_ERROR` | 未知异常或历史脏数据核心字段缺失（脱敏输出） |
| 500 | `ACCOUNT_MAPPING_CONFLICT` | 存量库同一 `w3_uid` 命中多条账户记录 |
| 503 | `SERVICE_UNAVAILABLE` | 数据库不可写/锁超时或运行器停机中等暂时不可用（C08） |

## 3. 7 种顶层业务态与快照适配（R03 / R04 / DEV07）

| 顶层 `status` | `edition.status` | `edition.items` | `generated_at` | `degraded_reason` | `pushable` | `fallback_hint` |
| --- | --- | --- | --- | --- | --- | --- |
| `missing` | `edition=null` | N/A | N/A | N/A | `false` | `retry` |
| `pending` | `pending` | `[]` | `null` | `null` 或非空串 | `false` | `retry` |
| `processing` | `processing` | `[]` | `null` | `null` 或非空串 | `false` | `retry` |
| `ready` | `ready` | ≥ 1 项 | 带时区 RFC 3339 | `null` | `true` | `null` |
| `degraded` | `ready` | ≥ 1 项 | 带时区 RFC 3339 | 非空字符串 | `true` | `null` |
| `empty` | `ready` | `[]` | 带时区 RFC 3339 | `null` 或非空串 | `false` | `public` |
| `failed` | `failed` | `[]` | `null` | `null` 或非空串 | `false` | `public` |

- `src/services/briefs_contract.py` 在发布前过滤无效主条目并按 `1..N` 连续编号、将内部对象标签转为字符串数组、`read`/`listen` 映射为 `quality`、`related_sources` 按 URL 去重并排除主链接；发布时将规范化结果固化至 `briefs_payload_json`，保证终态同 `revision` 输出恒定（D20）。

## 4. 租约、恢复预算与跨午夜规则（DEV05 / DEV06 / DEV09 / A06）

- **预算参数**（`src/services/briefs_runtime.py`）：单次尝试 `240s`、租约 `300s`、心跳续租 `60s`、恢复扫描间隔 `30s`（APScheduler `personal_digest_pending`）、遗留 `pending` 宽限 `30s`、最大领取次数 `2` 次（含初次）。
- **原子提交与令牌围栏**：每次领取分配新 `generation_token`（fencing token）并递增 `generation_attempt_count`；条目构建与标题中文化均在暂存态完成，最终凭有效 token + 租约 + 截止时间在同一事务内原子写入 `ready`/`degraded` 状态、`briefs_payload_json` 与条目表，旧 worker 提交一律拒绝（D13）。
- **A06 跨午夜截止修正方案（待与 bot 仓双端签字闭环）**：
  - `recovery_deadline = created_at + 20 分钟`，移除原实施说明中“当日结束截断”的冲突限制。
  - `23:59` 创建的任务可在有效租约与 20 分钟期限内于 `00:01` 完成或由恢复 worker 跨日扫描接管，落库并返回起始上海 `report_date`（D17 / C09 / C10）。

## 5. 真实代码交接信息（DEV01 / DEV10）

| 交接项 | Dorami 代码核查结果 |
| --- | --- |
| 登录接口（A01） | `POST /api/auth/login`（`src/api/app.py`），`Content-Type: application/json`，请求体 `{"username": "<str>", "password": "<str>"}`；成功返回 `200 {"authenticated": true, "user": {...}}` 并下发 `Set-Cookie`；失败返回 `401 {"detail": "账号或密码错误"}` 或 `403` |
| 会话 Cookie | Cookie 名默认 `dorami_admin_session`（`src/config.py` `[auth] cookie_name`），`Path=/`，`HttpOnly`，`SameSite=lax`，`Secure` 由 `[auth] cookie_secure` 控制，默认有效期 `604800` 秒（7 天，`[auth] session_seconds`）；每请求实时校验账户启用态、角色 `admin` 及 `users.session_epoch`（改密/停用立即失效） |
| CSRF 规则（A05） | 主线默认采用 `HttpOnly + SameSite=Lax` 签名会话 Cookie，不要求额外 CSRF 请求头（bot 当前空 `csrf_header`/`csrf_value` 可直接工作）；已预留 `app.state.briefs_csrf_verifier` 钩子，若特定环境启用则拒绝时返回 `403 / CSRF_REJECTED` |
| 身份映射（A02） | `users.w3_uid` 唯一索引 `uq_users_w3_uid`（迁移 `alembic/versions/9b7e2c4d1f60_add_w3_uid_identity_binding.py`），精确解析函数 `accounts.resolve_w3_user`（`src/services/accounts.py`） |
| 恢复部署 | 应用内 `BriefRuntime`（`asyncio.shield` 防客户端 180s 断连取消）+ APScheduler `personal_digest_pending` 每 30 秒扫描数据库恢复（`alembic/versions/b8d4107c2f61_briefs_generation_recovery.py`） |
| 部署环境项 | 联调/生产 HTTPS Base URL、反向代理超时配置（建议 ≥ 330s）、网关限流策略及专用 admin 账号凭据属于现场运维配置项（待部署环境提供） |

## 6. curl 联调示例

```bash
curl -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<secret>"}' \
  'http://127.0.0.1:8088/api/auth/login'

curl -b cookies.txt -H 'Accept: application/json' -H 'X-Request-ID: bot-check-001' \
  'http://127.0.0.1:8088/api/admin/reader/briefs/today?employee_number=l00976966'

curl -X POST -b cookies.txt -H 'Accept: application/json' -H 'X-Request-ID: bot-ensure-001' \
  -H 'Content-Length: 0' \
  'http://127.0.0.1:8088/api/admin/reader/briefs/today/ensure?employee_number=l00976966'
```
