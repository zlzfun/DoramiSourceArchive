# 令牌交付计量:账户 · 令牌 · 接口调用量(issue #312)

> 状态:进行中(2026-11-19 立项;第一轮样页已出,**待拍板**,拍板后实现)。
> 样页:[`docs/design/issue-312-token-usage/`](../design/issue-312-token-usage/README.md)。实现方 Claude Code(Fable 5.2);检视方待定(按流程交 Codex)。

## 1. 来由

同事开始用令牌程序化取平台内容(issue #310 刚上线热点事件榜的令牌化交付口)。问到「这些调用现在能不能统计」,答案是不能:

- `dfeed_` / `dsub_` 两张令牌表只有创建、更新时间,没有使用痕迹。
- 交付路径——文章 feed(JSON / Markdown / Atom)、热点事件榜、单订阅接口、MCP 工具——鉴权通过后直接返回,不写计量。
- 现有四类计量(AI 用量、阅读、登录、游客)都不覆盖它。阅读计量只认登录读者在阅读器里打开文章。
- 唯一的痕迹是 nginx 访问日志:令牌走 `Authorization` 头,日志里没有,只能按路径数次数、按 IP 猜人;外网宿主机留 14 天,容器内那份每次部署清空。

对照:收录推送令牌(`ingest_tokens`)有 `last_used_at`,分享链接有 `view_count`,读取类令牌是空白。

用户要的效果(2026-11-19):**账户 → 它的令牌 → 接口调用与调用量** 能连起来。

## 2. 现状(代码事实,v3.69.0)

| 令牌 | 表 | 一个账号几枚 | 重新生成 | 界面 |
|---|---|---|---|---|
| `dfeed_` 个人聚合令牌 | `reader_feed_tokens`(主键 `owner_username`) | 一枚 | 原地换 `token_hash`,`created_at` 不变 | 设置 → 接入集成 → 聚合接口 |
| `dsub_` 单订阅令牌 | `reader_subscriptions.token_hash` | 每个订阅一枚(一键订阅时自动生成) | 原地换 | 没有界面,明文只在创建 / 轮换接口返回一次 |

带令牌的读取面:

| 接口 | 认哪种令牌 | 解析处 |
|---|---|---|
| `GET /api/public/feed/articles`、`.md`、`.xml` | `dfeed_` | `api.feed_service.resolve_feed_token_owner` |
| `GET /api/public/feed/events`、`/{event_id}` | `dfeed_` | 同上 |
| `GET /api/public/subscriptions/{id}/articles`、`POST …/vector/search` | `dsub_` | `resolve_subscription_by_token` |
| MCP `browse_articles` `get_article` `search_articles` `get_rag_context` | `dfeed_` 或 `dsub_` | `resolve_subscription_sources_by_token`(只回来源范围,不回是谁) |
| MCP `list_sources` | 不需要令牌 | — |

管理面「运维 → 账户」现有:页头五格 KPI、账户增长、游客、活跃用户 Top(按阅读 / 登录)、账户管理表(可排序列:最近登录 / 登录 / 阅读 / AI 调用 / 订阅)、单用户抽屉(阅读 / AI / 订阅三格、登录、各源阅读收藏、每日 AI 用量)、操作审计。账户名单与逐用户明细只给根管理员。

## 3. 做法(初稿,样页拍板后定稿)

### 3.1 记什么

一次调用 = 令牌有效、成功返回的一次请求(MCP 是带令牌的一次工具调用)。条数 = 这次返回了多少条(文章 / 事件 / 检索结果;取单篇与事件详情记 1)。

一行 = **日 × 账号 × 令牌 × 接口**,累加调用次数与返回条数,记最近一次时间。

不记:认不出的令牌(不知道是谁的)、MCP `list_sources`、阅读器里的阅读(已有阅读计量)。已作废的令牌再被调,记不记见 §5 第 4 条。

### 3.2 表(一张,形制同 `ai_usage` / `reader_reads`)

`token_delivery_usage`:

| 列 | 含义 |
|---|---|
| `day` | 本地日期 `YYYY-MM-DD` |
| `username` | 令牌归属账号 |
| `token_kind` | `feed` / `subscription` |
| `token_fp` | 令牌哈希的前 16 位。哈希本身是带站点密钥的 HMAC,前缀不可逆;用它区分同一账号先后几枚令牌 |
| `token_preview` | 尾号(签发时已存在令牌表里的那份),给界面显示 |
| `subscription_id` | 订阅令牌所属订阅,聚合令牌为空 |
| `channel` | 接口:`feed/articles`、`feed/articles.md`、`feed/articles.xml`、`feed/events`、`feed/events/{id}`、`subscriptions/{id}/articles`、`subscriptions/{id}/vector/search`、`mcp:<工具名>` |
| `calls` / `items` | 累计调用次数 / 返回条数 |
| `rejected` | 作废后仍被调的次数(§5 第 4 条定了才有) |
| `last_at` | 最近一次时间 |

唯一索引 `(day, username, token_fp, channel)`,写入走 SQLite `INSERT … ON CONFLICT DO UPDATE` 原子累加。一枚令牌是否仍是「现用」不落库:读的时候拿 `token_fp` 与令牌表当前哈希比对,对不上就是已作废。

留存进 `services/retention.py` 的逐表策略,365 天。

### 3.3 写入点

- 解析函数各加一个回「是哪枚令牌」的版本(账号、类型、哈希、尾号、订阅 id),原函数保留成薄壳,现有调用方不动。
- HTTP 端点在组好返回值之后记一笔;记账失败只打日志,不影响交付(同阅读计量的约定)。
- MCP:`build_mcp_app` 多收一个记账回调,工具返回前调用。
- 鉴权失败(401)默认不记;§5 第 4 条定了记的话,只在这枚哈希以前出现过(能对上账号)时,在它名下 `rejected` 加一。

### 3.4 读接口

- `GET /api/admin/accounts`:逐行加 `api_calls`,排序键加 `api`,`summary` 加 `api_calls`、`api_tokens_active`、`top_api`。
- `GET /api/admin/accounts/{username}/activity`:加 `delivery` 一块(窗口合计、令牌清单及各自的接口明细、逐日 × 接口组)。
- 新只读端点(板块用):逐日 × 接口组的全站走势,全体管理员可见;令牌台账,根管理员可见。
- 读者自己的令牌状态接口(设置卡用):加最近调用时间与窗口内次数。

### 3.5 前端

见样页。两个方向:A 并入现有(KPI 第六格、活跃榜「按接口」、账户表「接口」列、抽屉「接口调用」区);B 在 A 之上加板块「接口调用」(每日走势 + 令牌台账)。设置卡另有 现网 / A / B 三种。

## 4. 部署顺序与混版本

计量是各节点本机的事:一张新表、一条迁移,各记各的,不进 Archive Sync,不改任何同步流的字段、枚举、导出范围或删除语义。

- 新外网 + 旧内网:外网开始记外网的调用;内网照旧不记。互不影响。
- 旧外网 + 新内网:反过来,同样互不影响。
- 升级前的调用没有记录,也补不回来(访问日志里没有令牌)。
- 回滚到旧版:新表留在库里没人读写,无害;再升上来接着记,中间那段是空的。

## 5. 待拍板(样页总览页同一份清单,推荐项在前)

1. 账户页:**B 加令牌台账** / A 并入现有。
2. 接口的叫法:**真实路径**(`feed/articles`、`mcp · search_articles`)/ 起中文名。
3. 重新生成令牌之后:**旧令牌的记录留在它自己名下,标「已作废」** / 并进新令牌。
4. 已作废的令牌还被调:**在旧令牌名下记一个数** / 不记。
5. 订阅令牌:**只列调过的** / 全列(每个订阅都自带一枚,平时没人用)。
6. 谁能看:**KPI 与每日走势全体管理员,台账与抽屉根管理员** / 全部只给根管理员。
7. 留存:**365 天**。
8. 设置卡:不动 / **A 令牌下加一行** / B 逐接口计数并列出热点事件地址(顺带收掉 issue #310 留下的「设置页入口」)。
9. 首页磁贴:**本波不动** / 账户磁贴加一个数。

## 6. 本波不做

- 按 IP / UA 的来源分析、限流与配额:这波只记账,不拦人。
- 历史回填:访问日志里没有令牌,对不上账号。
- 计量跨节点汇总:各节点各看各的。
- 手机壳:手机设置不列「接入集成」(issue #230),运维面这波只做桌面。

## 7. 自测与检视

待实现后补。
