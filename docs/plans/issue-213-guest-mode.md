# 游客模式:收到站内链接免登录先逛(issue #213)方案

> 状态:进行中(方案 R1 + 样页待拍板;实现未动工)
> 实现方:Claude Code(Opus 5.5);检视方:Codex(待定)。issue [#213](https://github.com/zlzfun/DoramiSourceArchive/issues/213) · 样页 [`docs/design/guest-mode/`](../design/guest-mode/README.md)

## 0. 拍板记录

**2026-10-22 第一批(用户)**

1. 游客碰到需要账号的操作**只提示登录,不做注册**。
2. AI 额度按用户名统计、**全体游客合为一个口径**;默认值**尽可能大**;**各用途全开**(翻译 / 问答 / 速读 / 社交翻译 / 逐字稿翻译 / 长文后台翻译 / 播客与文章点播)。
3. 个人早报**全体游客共享一版**:早报本就按默认订阅编排,游客改不了订阅,想改就登录。

**待拍板(本稿推荐,随样页确认)**

| # | 事项 | 推荐 | 备选 |
|---|---|---|---|
| D1 | 游客总闸默认值 | **关**,管理员在运维面一键开(内网按需开) | 默认开 |
| D2 | 入口 | 未登录打开**站内深链直接以游客进入**并打开该篇;根地址仍是登录页,加「先逛逛」 | 深链也先给「登录 / 先逛逛」二选一 |
| D3 | 公开分享页 | 页尾加「在哆啦美里继续逛逛」,落到早报 | 不加 |
| D4 | 游客能逛的范围 | 同正式读者:「我的订阅」= 默认名单,发现页 / 榜单 / 全文检索照常 | 只限默认订阅 + 深链篇目 |
| D5 | 已读状态 | **每位访客各记各的**(服务端按访客 id 记,30 天无访问即回收);未读、全部标已读照常可用 | 游客不进未读体系(全部按已读样式) |
| D6 | AI 额度默认值 | 各用途**不限**(0 = 不限),游客另有日 token 预算(默认不限);管理员设了上限后,超限弹登录提示 | 给一组很大的有限数 |
| D7 | 游客统计口径 | 单列「游客」区,不进读者数 / 活跃榜 / 账户增长 | 混入 |
| D8 | 反馈入口 | 照常显示,点击弹登录提示 | 对游客隐藏 |
| D9 | 登录后是否替游客补做刚才的操作(如收藏) | 不做,只回到原处 | 记下意图,登录后自动完成 |
| D10 | 公告 | 游客可见;关闭只记在本机 | 游客不显示公告 |

## 1. 目标与边界

- 收到站内链接的人不登录也能第一时间看到那篇,并像正式读者一样逛:早报、文章 / 播客 / 社交流、发现页、榜单、检索、AI。
- 需要「属于自己」的操作(收藏、订阅、兴趣、公开分享、反馈、接入集成、账户设置)统一弹登录提示;登录后回到原处。
- 管理面看得到游客数据:访客、访问、阅读、AI 用量、入口来源、转化。
- **不做**:注册 / 申请开通;游客数据迁移到登录后的账号;对外公开的匿名内容端点(游客照样要先拿到游客会话)。

## 2. 现状要点(决定方案的事实)

- **鉴权**:`src/api/app.py` 中间件 `require_admin_session` 对 `/api/*` 一律要会话(登录 / 会话 / 健康 / `/api/public/*` 除外);`read_auth_token` 每请求查 `users`(行存在、启用、角色一致、会话世代一致);角色只有 admin / user,`reader_role_enabled` 只放行这两者。
- **前端 401 即踢回登录页**:`frontend/src/api.js` 的 `apiFetch` 对任何非 `/auth/` 的 401 派发 `dorami-auth-expired`,`App.jsx` 收到即回登录页。**所以游客被拒的端点不能回 401**。
- **个人早报版面外键到 `users`**:`personal_digest_editions.owner_username → users.username`(SQLite 开 `foreign_keys`);兴趣表同。没有账号行的虚拟身份建版面会撞外键,`start_personal_digest_edition` 把 IntegrityError 当并发重试,会无限递归。
- **早报的范围只读订阅行**:`resolve_personal_digest_source_ids` 只读 owner 的 `reader_subscriptions` 行;默认名单(`services/reader_defaults.py`)只在账号首次播种时用一次。
- **AI 闸都按账号查**:`_require_reader_ai` 查账号行的 `ai_beta_enabled`;`translation_jobs.authorize` 要求账号行存在且启用;逐用户日额度(translate 50 / ask 100 / summarize 50 / 点播共池 5)按 `AiUsageRecord.username` 计;长文翻译每账号同时最多 3 篇(`MAX_USER_JOBS`)。
- **已读状态两张表**(逐篇状态 + 按源水位)按 `owner_username` 存,**无外键**;列表的 `unread` 标记、未读数、各种「全部标已读」都经 `reader_state` 按用户名读写。
- **媒体代理** `/api/media/proxy` 对任意 http(s) 图片地址缓存未命中时会去源站下载——对游客放开就成了公网可用的取图代理。
- **用户名不允许含冒号**(`create_user`),删号墓碑已用 `deleted:` 前缀;冒号是系统保留身份的天然命名空间。

## 3. 方案

### 3.1 游客身份:一个共享保留账号 + 每访客 id

- `users` 表加一行**保留账号** `guest:shared`(`role = "guest"`,不可用的密码哈希,`interest_onboarding_completed_at` 置值免引导,`ai_beta_enabled` 默认真)。冒号让它不可能被管理员建出同名账号,也不可能用密码登录。
- 为什么不用「无账号行的虚拟身份」:早报外键、AI 两道闸、长文翻译授权都要求账号行;拍板 2、3 本就要求全体游客共享一个口径。用一行保留账号,这些链路**零改动**即可服务游客。
- **每访客 id**(`vid`,随机 16 字节):放进游客会话令牌,另写一个长效 Cookie 让同一浏览器回来时沿用;只用于三件事:访客统计、已读状态(§3.5)、转化归因。
- 游客会话令牌沿用现有登录 Cookie(同名、同签名),载荷 `{"sub": "guest:shared", "role": "guest", "gen": <保留账号会话世代>, "vid": …, "exp": …}`,有效期同账号会话(7 天)。游客点「登录」成功后,账号令牌直接覆盖它。
- `read_auth_token` 对 `role == "guest"` 额外校验:总闸开、保留账号行启用。**总闸关 = 保留账号行停用**,既有游客会话下一次请求即失效(回 401,前端回登录页,符合「关了就关」)。
- 保留账号排除在所有账户管理面之外:账户名单、批量操作、改角色 / 启停 / 删号 / 重置密码一律拒绝或不可见;`seed_root_admin_if_empty` 的「空表」判定忽略它;账户增长、读者数不计它。

### 3.2 门控:游客白名单(fail-closed)

- 新模块 `src/api/guest_policy.py`(待建):游客可达路由的**唯一白名单**,按 (方法, 路由模板) 登记;中间件对游客会话先查白名单,不在其中的一律 **403 `{"code": "login_required"}`**(不回 401,见 §2)。`/mcp` 一并拒绝。
- 白名单内的请求按读者面处理:`reader_role_enabled` 增收 guest;collector / admin 判定对游客恒假。
- 守卫:`tests/test_route_authz_audit.py` 增一条——遍历全部注册路由,白名单条目必须真实存在、其余路由对游客会话一律 403 `login_required`。新端点默认不对游客开放。
- 新公开端点 `POST /api/auth/guest`(`{entry}`):总闸开时签发游客会话并记一次访问;总闸关回 403 `guest_disabled`;已登录账号调用回 409,不降级。
- `GET /api/auth/session` 匿名时多给 `guest_access_enabled`(登录页据此显示「先逛逛」,深链据此决定是否自动进入);游客会话时 `user.role = "guest"`,展示名「游客」。`/api/runtime` 对游客给 `account_role: "guest"`。

**游客白名单(初稿,实现时以路由表为准)**

| 类别 | 路由 |
|---|---|
| 会话 | `GET /api/runtime`;`/api/auth/session`、`/api/auth/logout`、`/api/auth/guest` 本就公开 |
| 读内容 | `GET /api/articles`、`/api/articles/facets`、`/api/articles/{id}`、`/api/articles/{id}/analysis`;`GET /api/reader/sources`、`/collections`、`/rankings*`、`/announcements`、`/favorites`(恒空)、`/interests`(恒空)、`/interests/catalog`;`GET /api/podcasts/episodes/{id}/texts`、`/api/reader/articles/{id}/listen-audio`、`/api/reader/podcast-artifacts/{id}/audio`;`GET /api/media/proxy`(仅命中缓存,§3.7) |
| 早报 | `GET /api/reader/briefs`、`/today`、`/{date}`;`POST /api/reader/briefs/today/ensure` |
| 已读(按访客) | `POST /api/reader/articles/{id}/read`、`/mark-read`、`/mark-unread`;`GET /api/reader/unread-counts`;`POST /api/reader/mark-all-read`、`/mark-scope-read`、`/sources/{id}/mark-all-read` |
| AI | `/api/reader/ai/*` 全部(翻译、社交翻译、长文任务及续译 / 取消、逐字稿翻译、播客 / 文章点播、速读、问答及进度) |

其余全部拒绝,包括订阅 / 退订 / 合集 / 批量订阅、自定源、OPML、收藏写、公开分享、聚合令牌、`/api/subscriptions*`、反馈、公告关闭、兴趣写与引导、早报重编、改密 / 头像 / 偏好、`/api/feed*`、`/api/skill*`、`/api/mcp*` 以及全部管理与采集面。

### 3.3 默认订阅同步

- 保留账号的订阅行**镜像默认名单**(`reader_defaults.default_source_ids`):开总闸、改默认名单(`set_default_source_ids`)、启动时各同步一次,增缺删多;不写水位(已读状态按访客另记)。
- 名单有变化时,系统替游客早报发起一次重编(原因 `subscription_changed`,与下架源扇出同路),当天的游客版就跟上新名单。
- 游客的订阅行会被几处全站统计误算,一律排除:发现页「N 人订阅」(`routers/reader.py` 订阅人数)、运维总订阅数与各源订阅数(`routers/admin.py` overview / content)。`GET /api/reader/sources` 对游客跳过首次播种。

### 3.4 游客早报:共享一版

- 版面归保留账号,所有游客读同一份;08:30 定时本就遍历全部启用账号,自然包含游客;当天第一位游客打开时若尚未生成,`ensure` 现场生成。
- 生成本身是确定性的,唯一的模型调用是标题中文化(有缓存、有预算),一天一版成本可忽略。
- 游客不能重编(白名单不含);页面不显示「重新编排」、兴趣引导横幅、「下次编排生效」提示;页头标「游客版」并给登录入口。
- 当天版面生成失败时,游客打开会触发一次系统重试(10 分钟内不重复),避免失败版挂一整天。
- 运维「分析 → 指标」的早报版面数排除游客版。

### 3.5 已读状态:每访客独立

- 已读两张表的 owner 对游客改用 `guest:v:<vid>`,订阅范围仍取保留账号。阅读器的未读加粗、未读数、「全部 / 未读」、各种「全部标已读」、「载入 N 篇新文章」**前端一行不改**就对游客成立;同一浏览器在会话期内持续有效,不同访客互不影响。
- 改动面:`/api/articles` 的未读标注与只看未读、`/api/reader` 下的读 / 标读 / 标未读 / 未读数 / 三种全部标读,共约 8 处,统一经一个「已读 owner」取值函数。
- `POST /read` 对游客:逐篇已读写访客 owner;文章阅读量照常 +1;阅读计量记在保留账号名下(全体游客合计),并给当日访问行 `reads` +1。
- 回收:访客 30 天无访问,其已读行随每日 04:30 留存清理删除。

### 3.6 AI 试用

- 授权沿用 `_require_reader_ai`:全局 AI 总闸 ∧ 保留账号 `ai_beta_enabled`(运维游客区的「游客 AI」开关)∧ LLM 已配置。
- 额度:`_enforce_ai_daily_quota` 对保留账号改读 KV `guest_ai_daily_limits`(按 translate / ask / summarize / 点播池四个键,**缺省全 0 = 不限**);另加游客日 token 预算 KV `guest_ai_daily_token_budget`(缺省 0 = 不限),护住正式读者的全站预算。全站日 token 预算照旧对所有人生效。
- 长文后台翻译:游客的每账号并发上限改用全站任务上限(`MAX_JOBS`),日调用额度走上面的游客额度。翻译结果照旧写文章缓存,正式读者也受益。
- 超出游客额度回 **429 `{"code": "guest_quota_exhausted"}`**(带 Retry-After),前端弹「登录后继续使用」;全站预算耗尽仍是原文案「今日 AI 用量已达全站上限」,不引导登录(登录也解决不了)。
- 已知取舍:游客共用一个身份,长文翻译任务对所有游客可见、可取消;影响面小,不为此加访客维度。

### 3.7 媒体代理

- 游客会话访问 `/api/media/proxy` 只返回**已缓存**的图片,未命中一律 302 回源站,不在服务端下载。正文图随文预取,绝大多数已在库里。

### 3.8 计量与统计

- 新表 `guest_visits`(待建,配迁移):`(day, visitor_id)` 唯一;`entry`(当天首个入口:`deep_link` / `share_page` / `login_page` / `return`)、`visits`、`reads`、`ai_requests`、`first_seen_at`、`last_seen_at`、`converted_username`、`converted_at`。
- 写入点:`POST /api/auth/guest`(新访客或新的一天建行、记入口);游客打开应用(`/api/runtime`)`visits` +1;`/read` `reads` +1;AI 请求放行时 `ai_requests` +1;游客在本浏览器登录成功时记转化(登录请求带着游客令牌,覆盖前读出 vid)。
- 留存 365 天(同登录事件)。
- 指标:访客(去重 vid)、访问、阅读、AI 调用与 tokens(`AiUsageRecord` 保留账号名下,按用途)、转化与转化率、入口分布、每日走势。
- 现有口径排除游客:运维「用户」页的读者数 / 窗口阅读 / 窗口 AI / 活跃榜、账户增长、账户名单与单用户抽屉;「AI」页按用途的总量照常含游客(成本真实),按用户视图里游客显示为「游客」。

### 3.9 前端

- **启动**:匿名 + 深链 + `guest_access_enabled` → 自动 `POST /api/auth/guest {entry: "deep_link"}`,进阅读器并打开该篇;否则登录页,总闸开时多一个「先逛逛」。已有游客会话直接进阅读器。
- **登录提示**:新组件 `frontend/src/components/LoginPrompt.jsx`(待建),`<Modal closeOnOverlay>`,标题按动作给,按钮「登录」「继续逛逛」;`api.js` 对 403 `login_required`、429 `guest_quota_exhausted` 派发 `dorami-login-required`,由 App 统一弹出,原调用处的报错提示静默。前端拦截优先(不发请求就弹),后端码兜底。
- **去登录与回原处**:「登录」切到现有登录页(不另造表单,下游的登录入口、正在进行的登录页重设计都不受影响),登录页对游客多一个「返回继续逛逛」;登录成功后按深链机制回到原处(在看文章就回这篇,其他视图回原 Tab)。
- **自动请求**:游客不拉收藏 id、不轮询反馈未读;公告关闭只记本机;早报 `ensure` 照常。
- **各处差异**见 §4;桌面与移动壳同改。分享菜单里「站内链接」的「需要登录」字样在总闸开时改为不提登录。

### 3.10 管理面

- 运维管理 →「用户」子页新增「游客」区,全体管理员可见(聚合口径):区头总闸开关 +「游客 AI」开关;五格 KPI(访客 / 访问 / 阅读 / AI 调用 / 转化);每日走势(访客柱 + 转化点);入口分布条。
- 区头齿轮开「游客设置」抽屉:四个用途的每日上限(0 = 不限)、游客日 token 预算、默认订阅名单跳转到「内容」子页既有编辑器。
- 端点:`GET /api/admin/guest?days=`(设置 + 统计)、`PUT /api/admin/guest/settings`;落在 `/api/admin` 前缀,自动仅管理员可写并入审计。

## 4. 游客界面差异(桌面 / 移动)

| 位置 | 游客看到的 | 点击后 |
|---|---|---|
| 视图轨底部头像 / 移动「我的」账户卡 | 「登录」按钮 / 游客卡 + 登录 | 去登录页 |
| 文章收藏星(列表行、阅读窗、早报条目、社交卡、移动文章页) | 照常显示 | 提示「登录后即可收藏」 |
| 列表头「只看收藏」/ 移动「我的 → 收藏」 | 照常显示 | 提示「登录后可查看收藏」 |
| 源栏「订阅 \| 兴趣」轴的「兴趣」、发现页「兴趣」Tab、移动「我的兴趣」 | 照常显示 | 提示「登录后即可设置兴趣」 |
| 订阅 / 退订(发现卡、预览横幅、阅读窗「+订阅」、早报「+订阅」、源行「−」与右键 / 长按菜单)、合集订阅、「订阅全部文章源」 | 照常显示 | 提示「登录后即可订阅『来源名』」/「登录后即可调整订阅」 |
| 「添加文章源」、设置柜「订阅源 OPML」 | 照常显示 | 提示「登录后即可添加来源」 |
| 已读 / 未读、全部标已读、未读筛选 | 照常可用(按访客记) | — |
| 分享菜单 | 「复制站内链接」可用;「生成公开链接」 | 提示「登录后即可生成公开链接」 |
| AI(翻译 / 问答 / 速读 / 点播 / 逐字稿) | 照常可用 | 超出游客额度时提示「游客今日的 AI 试用已用完,登录后继续使用」 |
| 早报页 | 「游客版」标记 + 登录入口;无「重新编排」、无兴趣引导 | — |
| 视图轨「反馈」/ 移动「我的 → 反馈」 | 照常显示(D8) | 提示「登录后即可反馈」 |
| 设置柜 | 「账户」区换成游客卡 + 登录;「外观」「关于」照常;「订阅源 OPML」「反馈」「接入集成」显示登录入口 | — |
| 公告横幅 | 照常显示 | 关闭只记本机 |

## 5. 实施(一个分支、一个 PR)

1. 版本:本波是 v3.66.0 之后第一波,开工即改 `3.67.0-alpha`。
2. 后端:保留账号与会话、白名单与中间件、路由审计守卫、账户面排除 → 默认订阅同步与早报 → 每访客已读 → AI 额度 → 媒体代理 → `guest_visits` 迁移与计量 → 管理端点。
3. 前端:启动与入口、登录提示与回原处、阅读器各处差异(桌面 + 移动)、设置柜、分享菜单、早报页、运维游客区。
4. 自测:`tests/test_guest_mode.py`(待建)覆盖会话 / 总闸 / 白名单 / 排除 / 共享早报 / 每访客已读 / AI 额度码 / 媒体代理 / 计量与转化;前端纯函数 `node --test`;沙箱 Playwright 截图亮暗 + 移动壳。
5. 文档同波:`docs/architecture/accounts-and-access.md`(第三角色与保留账号)、`docs/architecture/llm-and-ai.md`、`docs/architecture/admin-ops.md`、`docs/architecture/reader-distribution.md`、`docs/architecture/analysis-and-briefs.md`(顺手更正已退役的「公共日报就绪追加」触发)、`docs/reference/endpoints.md`、`docs/frontend/conventions.md`(需账号的操作对游客一律走登录提示)、`AGENTS.md` 架构速览「整体」行的角色轴、用户手册;version-history / backlog / decisions。

## 6. 风险与取舍

- **公网暴露与 AI 成本**:开了游客 = 可见归档对任何人开放,且游客 AI 默认不限。故总闸默认关(D1),并提供游客日 token 预算;公网部署开启前应设预算或上限。
- **共享身份的副作用**:长文翻译任务游客之间可见可取消;接受。
- **保留账号漏出**:任何遍历 `users` 的新代码都可能把它算进去。用 `accounts.is_guest_username` 单点判定,测试钉住账户名单、增长、KPI、订阅人数四处不含游客。
- **与登录页重设计并行**:另一条线正在做登录页五方向样页。本波只在登录页加「先逛逛」「返回继续逛逛」两个入口,重设计落地时须保留。
- **下游内网分支**:内网登录入口有下游适配;游客「去登录」走现有登录页,同步 master 时核对两个入口仍在。
- **爬取**:游客会话可遍历可见归档;本波不做限流,观察期看 `guest_visits` 与访问日志再定。

## 7. 检视

(待实现后填写:实现方 / 检视方 / 模型、逐条表态与结论。)
