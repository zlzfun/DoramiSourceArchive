# 热点事件榜的令牌化交付口(issue #310)

> **归档于 2026-11-19(issue #310,PR #311,开发版 3.69.0-alpha,纯后端 + 契约文档,Codex 两轮检视通过,待发布)**:现状以 [`contracts/reader_subscription.md`](../contracts/reader_subscription.md)「Hot Events Board」与 `src/api/routers/events.py` 为准,文中路径与状态为当时记录。
> 状态:归档(已落地待发布;2026-11-19 开工;分支 `feat/issue-310-events-feed`;没有可目检界面,按 2026-09-19 例外口径「检视通过 + 全量套件绿 + CI 绿后直接合入」,用户同日授权执行者推进到合入)。

> 实现方:Claude Code(Fable 5.2);检视方:Codex(见 §7)。

## 1. 来由

榜单页「热点事件」的数据只有会话接口 `GET /api/reader/events`(登录或游客 cookie)。用户的同事想用脚本长期拉这份榜单,cookie 会话不适合程序化调用:要先登录、会过期;走游客会话还会记进游客访问统计(`guest_access.record_entry`)。

站内已有令牌化交付——`dfeed_` 个人聚合令牌拉 `/api/public/feed/articles[.md|.xml]`、MCP 工具带同一枚令牌——但事件榜不在其中(`src/api/feed_service.py`、`src/mcp_server.py` 都不碰 `event_board`)。

## 2. 做法

| 端点 | 作用 |
|---|---|
| `GET /api/public/feed/events` | 热点事件榜;`window_hours`(默认 168,1–336)、`limit`(1–10)、`include_members`(每件事带成员报道) |
| `GET /api/public/feed/events/{event_id}` | 单个事件:榜单字段 + 成员 + 后续事件 + `merged_into` |

- **鉴权**:`Authorization: Bearer dfeed_…` 或 `?token=`,与 `/api/public/feed/articles` 走同一个解析口(`api.tokens.read_bearer_or_query_token` → `api.feed_service.resolve_feed_token_owner`)。无效 401,文案沿用「个人聚合接口令牌无效」。
- **数据**:直接调 `services/event_board.read_board` / `read_event`,读者面与对外面同一段代码,不会各算各的。列表外面包 feed 家族的 `status` / `count`,详情是 `{status, event}`。
- **服务层只加了一个参数**:`read_board(include_members=False)`。成员视图在算榜时已经载入(`_board_entries` → `_load_member_views`),写出来不多查库;成员清单的排序与形状抽成 `_members_payload`,详情与带成员的榜单共用。读者面不传该参数,返回字段不变。
- **路由放在 `src/api/routers/events.py`**(事件域),不放 `subscriptions.py`:窗口 / 条数校验与读者面共用一份(`_check_window` / 新抽的 `_check_limit`)。
- **运行面门控零改动**:`/api/public/feed` 已在 `READER_API_PREFIXES`,`[runtime] role=collector` 时中间件回 403;`/api/public/*` 免会话,`tests/test_route_authz_audit.py` 的分类已覆盖。无迁移。

## 3. 取舍

1. **沿用 `dfeed_`,不新开令牌族。** 新令牌族要配签发 / 轮换 / 展示 / 账号删除时的清理,而这份数据读者面对所有读者乃至游客都开放,没有需要单独圈定的范围。`dfeed_` 在设置里就能自助签发。
2. **全站口径,不按订阅范围裁剪。** 事件榜回答的是「全站这几天什么事被多家报道」,读者面也不按订阅裁;按订阅裁会让同一件事在不同令牌下主体数不同、名次漂移,且与页面对不上。令牌只决定能不能拿。管理员令牌与读者令牌拿到的是同一份(读取侧恒 `reader_scope=True`:隐藏 / 私有 / 凭据源成员剔除)。
3. **不接受 `dsub_`。** 单订阅令牌绑一个订阅、端点路径带订阅 id,语义是「这个订阅的内容」;榜单与任何单个订阅无关。
4. **登录会话不顶替令牌。** `/api/public/*` 这一族只认令牌;带 cookie 的浏览器请求有 `/api/reader/events`。
5. **读者面载荷原样下发,用键集测试钉住。** 另写一层交付视图能把对外契约与界面字段解耦,但今天两者内容完全相同,多一层只多一处要同步的地方。代价是读者面载荷的键自此成了对外契约——`tests/test_events_api.py` 的键集断言(读者面与对外面共用同一组常量)在有人增删改键时会红,`_item_payload` 上加了注释指到契约文档,`feed_delivery.md` 的消费方清单也登记了。口径:只加不改不删。
6. **`include_members` 而不是让脚本逐件请求。** 详情端点每次要重算一遍榜单来取名次;脚本要全部报道时 1 + 10 次请求就是 11 次算榜。带成员的列表一次算完。详情端点仍然保留:按 id 回访一件已掉榜的事、合并后旧 id 指向新事件,都只有它能做。
7. **令牌校验先于参数取值校验。** 没有有效令牌的调用方只看到 401,不回窗口 / 条数范围类的 400(类型不合的 422 由框架在进入处理函数前给出,不含数据)。

## 4. 部署顺序与混版本

- 不碰 Archive Sync 的任何流、字段、枚举或删除语义;纯增端点。外网与内网谁先上都行。
- 开着读者面的旧版本节点上这两条路径不存在,返回 404(`{"detail":"Not Found"}`),消费方按「该站点尚未提供」处理;`[runtime] role=collector` 的节点不论版本都回 403(读者面整段关着)。
- **两个节点的榜不保证一致**:事件随同步到内网,但名次按各节点自己的可见源现算、`previous_rank` 比的是各节点自己 07:00 的快照(`event_rank_snapshots` 不同步)。契约里写明了。
- 同事若在内网取数,要等内网升到含本波的版本(外网按 tag 发版 → `master` 同步 → 内网手工部署)。

## 5. 本波不做

- **设置页「聚合接口」列出这条地址。** 该卡片的范围签写的是「已订阅来源 / 全站公开内容」,而事件榜对谁都是全站口径,放进去要重新想这张卡怎么表达范围,属于要出样页、要目检的界面改动;本波保持纯后端。用户手册(`docs-site/features/integrations.md`)先写了用法。
- **MCP 工具**(让 AI 助手直接问「这几天有什么热点」):有人要时再加,服务层已经能直接复用。
- **Markdown / Atom 变体**:事件榜是结构化数据,目前没有 RSS 阅读器或文本工具的消费方。
- **按来源 / 标签筛选、翻页**:榜单最多 10 件,读者面也没有这些维度。

## 6. 自测

- `tests/test_events_api.py` 新增四条:
  - `test_public_feed_events_deliver_the_reader_board`:只凭令牌(清掉 cookie)拉到的条目与同一时刻读者面逐字段相等;`?token=` 兜底;`window_hours` / `limit`;`include_members` 的成员与详情一致、其余字段不变;详情与读者面详情相等;合并旧 id、单主体事件、404 / 422 / 400;管理员令牌同一份榜;不入审计。
  - `test_public_feed_events_require_a_valid_feed_token`:无令牌 / 错令牌 / 仅登录会话 / `dsub_` 令牌 / 轮换后的旧令牌一律 401;无效令牌配越界参数仍是 401。
  - `test_public_feed_events_disabled_in_collector_role`:403。
  - `test_public_feed_events_never_deliver_hidden_sources`:隐藏一个源后,成员、主体数、名次都按可见成员重算,响应里不出现该源。
- `tests/test_event_board.py` 新增 `test_board_with_members_matches_detail_and_adds_no_queries`:带成员与不带成员的 SQL 条数相等(`before_cursor_execute` 计数),成员与详情相同,默认不带。
- **反向对照**:把令牌校验改成恒通过,`…require_a_valid_feed_token` 红;恢复后绿。
- **规模自查(合成数据,脚本未入库)**:7 天窗内 400 件事件、1559 篇成员、40 个源——不带成员 43 ms / 27 KB,带成员 44 ms / 225 KB(10 件事 245 篇成员);近 2 天窗 16 ms。本机开发库与历次沙箱副本里都没有事件数据(事件层 10 月下旬才上线,本机库停在 09-17),没法用真实数据量。
- 没有前端改动,沙箱 Playwright 不适用。
- 本地按 #288 的分工只跑受影响的测试文件(事件 / 订阅 / MCP / 来源可见性 / 游客 / 路由鉴权审计,共 160 条)与 `scripts/check_docs.py`(0 个问题),全量由 CI 跑。

## 7. 交叉检视

实现方 Claude Code(Fable 5.2);检视方 Codex(`gpt-6-astra`,effort ultra,本地 `codex exec` 只读沙箱)。任务书、报告、表态与修复清单在 worktree `.review/`(不入库)。

**第 1 轮(修后可合入,0 P1 / 2 P2)**——鉴权、数据外流、读者面回归、混版本判断与 §3 七条取舍均无异议;两条 P2 都是契约文档与实现对不上,它用纯内存库各复现了一遍:

| 条目 | 意见 | 表态与修法 |
|---|---|---|
| F1(P2) | 契约写「不在榜的事件 `rank` / `previous_rank` 都为空」,实际两者各查各的:今早上过榜、此刻掉榜的事件详情里 `rank` 为空而 `previous_rank` 仍在 | 接受。实现不动(读者面事件页靠它显示上次名次),契约把两个字段的空值条件分开写;`test_is_new_and_previous_rank_…` 补一件「快照里第 2、现在已出窗口」的事件 |
| F2(P2) | 契约写成员「newest first」,实际按 `report_at`(归组时间,发布时间钳在首见前 72 小时内)倒序;八天前发布、两小时前入库的稿会排在四天前发布的前面,消费方拿第一篇当最新发表会错 | 接受。排序保持读者面现状,契约写明排序键与同值规则,加三个时间字段的表,并提示用 `latest` 或按 `published_at` 排;`test_first_and_latest_report_…` 补成员顺序断言 |
| §4 备注 | 「旧节点回 404」只适用于开着读者面的节点,collector 仍是 403 | 接受,方案 §4、波次史、契约各补一句 |
| 格式 | `tests/test_event_board.py` 文件末尾多一个空行 | 接受,删掉 |

修复后反向对照:把 `read_event` 改成「掉榜即清空 `previous_rank`」、把 `_members_payload` 改按发布时间排,两条补了断言的用例各自变红;还原后绿。

它的沙箱没有可写临时目录,pytest 在收集前失败,它没有跑成测试(报告里按环境失败注明,不算发现);测试由实现方本机与 CI 跑。

**第 2 轮(复检,只对照修复清单)**——结论「可合入」:F1 / F2 / §4 / 空行四项逐条核对为已修,两处新增断言它在纯内存库里执行通过、反向对照能检出错误;无新引入的 P1。观察一条(不作为合入条件):契约没写 `report_at` 存值不可解析时排序回退到 `publish_date` 的容错分支——顺手在契约的 `report_at` 行补了半句,代码未动。

两轮封顶,无遗留分歧。
