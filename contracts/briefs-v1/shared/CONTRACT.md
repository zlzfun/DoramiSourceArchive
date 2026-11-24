# 共享业务契约 v1.0

本文件和 `openapi.yaml` 一起冻结。所有新增默认策略见根目录 README。下面的 MUST 含义为两仓必须满足，SHOULD 含义为推荐且偏离时需记录原因。

## HTTP 与参数

| 方法与路径 | 参数位置 | 行为 |
| --- | --- | --- |
| `GET /api/admin/reader/briefs/today` | query | 解析账号，取今日最大 revision，仅读取 |
| `POST /api/admin/reader/briefs/today/ensure` | query，body 为空 | 有版本直接返回，无版本原子创建并同步等待本次生成结果 |

公共必填参数 `employee_number` 为 1–128 字符的无空白字符串，例如 `l00976966`。只接受一次出现。缺失、空串、含空白、超长、重复出现返回 422。按原值精确查询，不改变大小写或删除空白。未知 query 参数在 v1 返回 422，以暴露拼写错误。POST 非空 body 返回 422。

鉴权采用 `Cookie: dorami_admin_session=<opaque value>`，统一校验有效会话且 `role == admin`。cookie 无效或缺失为 401，角色不符为 403。先鉴权，再校验参数，再解析目标账号。API 返回 JSON，不重定向到 HTML 登录页。请求 SHOULD 带 `Accept: application/json` 和 `X-Request-ID`。request id 是 1–128 字符的 `[A-Za-z0-9._-]` 字符串，无效时由服务端重建，不能影响业务结果。

现有 CSRF 机制适用时，bot 通过登录适配器获取并附带其要求的令牌或头。CSRF 拒绝统一为 403 / `CSRF_REJECTED`，bot 告警并按配置错误处理。具体头名必须由 Dorami 交接，两个日报端点不新增登录协议。

应用响应 MUST 包含 `Content-Type: application/json`、`Cache-Control: no-store`、`X-Request-ID`、`X-Briefs-Contract-Version: 1.0`。bot 允许 1.x 的向后兼容新增字段，发现主版本不匹配按协议错误处理。网关直接产生的错误不保证这些头。

## 日期、身份、版本

1. 每次请求开始固定 `report_date = now(Asia/Shanghai).date()`，本次查询、创建、生成和返回都使用这个日期。
2. `users.w3_uid == employee_number` 必须唯一映射到 username。不存在返回 404 / `ACCOUNT_NOT_FOUND`。存在重复映射返回 500 / `ACCOUNT_MAPPING_CONFLICT` 并告警，不能任取一个账号。
3. GET 和 ensure 的初始查询都选择 `(owner, report_date)` 下 revision 最大的 edition。若 rev 2 正在处理，不能回取 rev 1。缺版 ensure 创建后等待并返回它创建的 edition；等待期间若有管理流程另建更高 revision，后续 GET 可读到更高版本。bot 不发送低于或等于已发送最大 revision 的迟到响应。
4. 缺版 GET 返回 200 / missing。ensure 看到任何已存在版本都不创建、不重试生成、不改变该版本，返回读取时的快照。并发调用可能观察到不同生成进度，不要求响应字节相同。
5. ensure 新建首版 revision 从 1 开始。显式重生成创建更高 revision 的能力属于已有管理流程；恢复同一 edition 不增加 revision。
6. 终态 ready / failed 的 edition 内容、degraded_reason、items、generated_at 冻结。更改可推送内容必须新建 revision，才能让 bot 水位正确。

## 成功响应与字段

所有 200 响应固定包含 `employee_number`、`username`、`status`、`pushable`、`fallback_hint`、`edition`。username 是账号解析结果，允许与 employee_number 不同。employee_number 原样回显。

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| employee_number | string | 本次请求的目标工号 |
| username | string | Dorami owner 账号 |
| status | enum string | 下表中的推送业务态 |
| pushable | boolean | 当前返回的 edition 能否用于个人推送 |
| fallback_hint | public / retry / null | public 为本轮走公共；retry 为本轮走公共且在补查窗口内重查；null 表示无须回退 |
| edition | object / null | 仅 missing 时为 null，其余为 serializer 契约输出 |

| 顶层 status | edition.status | 有效条目 | pushable | fallback_hint |
| --- | --- | --- | --- | --- |
| missing | edition 为 null | 不适用 | false | retry |
| pending | pending | 尚未发布 | false | retry |
| processing | processing | 尚未发布 | false | retry |
| ready | ready | 至少 1 条，degraded_reason 为 null | true | null |
| degraded | ready | 至少 1 条，degraded_reason 非空 | true | null |
| empty | ready | 0 条 | false | public |
| failed | failed | 0 条 | false | public |

按 edition 处理状态先判断，再判断 ready 版本的条目数，再判断 degraded_reason。empty 的优先级高于 degraded。内部的 queued、running 等状态由 Dorami 映射到上述 wire 状态，bot 只认本契约。

`pushable=true` 不表示 bot 必须发送，bot 还需校验日期、目标工号、协议和已发送水位。未知顶层状态、类型错误、状态组合冲突均按协议错误处理，告警、本轮尝试公共回退，不能触发 ensure。

## edition 与渲染素材

完整类型定义在 OpenAPI。以下约定固定展示行为。

- `report_date` 为 YYYY-MM-DD；`revision` 为大于等于 1 的整数。
- `generated_at` 为带时区偏移或 Z 的 RFC 3339 字符串。pending / processing / failed 为 null，ready 必须非 null。
- `degraded_reason` 为非空字符串或 null。失败原因可通过额外字段提供，bot 不依赖其做分支。
- `selection_stats` 为 JSON object，可为空；内部各池统计是扩展元数据，bot 不依赖具体键。
- `items` 始终为数组。pending / processing / failed 返回 `[]`，暂存稿不对 bot 暴露。ready 可为空，非空时每条必须符合 Item Schema。
- position 是从 1 开始的连续整数且唯一，items 按 position 升序。渲染不重新排序。
- 有效条目要求 snapshot.title、source_name 为去空白后非空字符串，source_url 是有效的绝对 HTTPS 或 HTTP URL。其余缺失展示字段按 Schema 填空值。服务端过滤无效条目后重新编号，若因此部分降级则设置 degraded_reason，全部过滤则 empty。
- section 为非空分组名；selection_lane 固定 interest / quality / breaking。quality_score 为 number 或 null，不假设既有评分上限。
- snapshot.summary、score_reason、publish_date 可为 null。publish_date 为 YYYY-MM-DD 或带时区 RFC 3339 时间，bot 仅作展示。
- tags 和 display_tags 固定为字符串数组，空值为 `[]`。已有内部对象标签由 Dorami serializer/adapter 提取展示名称，bot 不推断对象结构。
- event_id 为 string 或 null，内部数字 ID 转为字符串。related_sources 固定为对象数组，每项包含 title、source_name、source_url，可附 publish_date。计数按数组长度，服务端先按 source_url 去重并排除主链接。
- 所有对象允许额外字段以兼容现有 serializer。额外字段不承载 bot 必须依赖的流程语义。bot 忽略不认识的字段。

若已有 serializer 的字段类型不同，Dorami 应复用现有序列化逻辑并集中适配本端点输出。原样透传只有在满足 Schema 时成立。不得让 bot 针对不同版本编写多套猜测性解析。

## 调用错误

```json
{
  "error": {
    "code": "ACCOUNT_NOT_FOUND",
    "message": "employee_number 未匹配到 Dorami 账号",
    "request_id": "briefs-example-001"
  }
}
```

| HTTP | error.code | bot 处理 |
| --- | --- | --- |
| 401 | UNAUTHENTICATED | 一个订阅本轮最多重登一次，并重放失败的请求一次；仍失败告警、公共回退 |
| 403 | FORBIDDEN / CSRF_REJECTED | 配置或权限错误，告警、公共回退，不在本轮重试 |
| 404 | ACCOUNT_NOT_FOUND | 当前无法映射该工号，本轮公共回退，不调用 ensure |
| 422 | INVALID_ARGUMENT | 调用缺陷，告警、公共回退，不重试 |
| 429 | RATE_LIMITED | 若部署限流，尊重 Retry-After，本轮公共回退，下轮先 GET |
| 500 | INTERNAL_ERROR / ACCOUNT_MAPPING_CONFLICT | 技术失败，告警、公共回退；下一轮允许 GET |
| 503 | SERVICE_UNAVAILABLE | 技术不可用，本轮公共回退，窗口内下轮 GET |
| 网关 502/504、网络异常、非 JSON | 不保证 error body | 本轮公共回退，窗口内下轮 GET |

只有确定属于 ACCOUNT_NOT_FOUND 的 404 才按缺账号处理。其他 404 按路由或协议错误告警。HTTP 4xx/5xx 的错误 body 与 200 业务 envelope 分开解析。禁止将未知异常伪装为 missing。可识别并持久化为 edition.failed 的生成失败使用 200 / failed。

## bot 调用算法

1. 以订阅目标和上海日期启动本轮，GET。先处理 HTTP 与协议验证。
2. 仅 GET 为 200 / missing 且 edition=null 时 ensure 一次；如果随后 401，允许已约定的一次鉴权重放。
3. ensure 返回后复用同一业务响应解析器。missing 不应从 ensure 正常返回，遇到则视为协议错误，公共回退，下一轮 GET。
4. pushable=true 时检查日期和个人版本发送记录。已发则结束，不因已发而回退公共。
5. pushable=false 时本轮尝试公共日报。retry 仅表示下一轮 GET，禁止紧密循环 ensure。
6. missing / pending / processing / 调用超时 / 可重试技术错误的自动补查间隔为 60 秒加 0–15 秒抖动，从本轮结束后计算。最多 20 次补查，且不晚于原订阅计划推送时间加 30 分钟，不跨上海日期。
7. failed / empty / ACCOUNT_NOT_FOUND 在当前计划推送窗口不自动补查，下一次正常计划任务仍可 GET。

若新请求已跨午夜，bot 不将返回的次日日报用于前一日计划。GET 和 ensure 之间跨日也按此规则处理，不新增日期参数。人工补发历史日报属于其他接口的职责。

## 超时、恢复与投递边界

bot 180 秒未收到 ensure 结果会回退。Dorami 已创建的 edition 和处理任务继续存在，恢复 worker 负责遗留 pending 和租约过期任务。所有重试共用 edition 身份。服务端未能启动任务也必须让已落库 pending 可被恢复 worker 扫描。

公共发送 key 为 `(channel, recipient, subscription_id, report_date, public)`。个人发送 key 为 `(channel, recipient, subscription_id, report_date, personal, revision)`。个人内容去重基于 Dorami 返回的日期和 revision，调用目标包含在 key 中。

发送器应通过数据库唯一约束或等效原子操作预占 key。只有渠道确认成功后记录 sent。发送超时导致接收状态不明时记 unknown，优先按渠道消息 ID 查询；没有幂等或查询能力时，不对 unknown 自动重发，并告警人工处理。该边界无法仅靠两个日报端点实现绝对一次投递。
