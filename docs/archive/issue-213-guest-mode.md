# 游客模式:收到站内链接免登录先逛(issue #213)方案

> 状态:归档(2026-10-23,用户目检放行;实现随 PR 合入 main,开发版 3.67.0-alpha,版本号以实际发版为准)
> **归档于 2026-10-23(issue #213,开发版 3.67.0-alpha)**:现状以 `docs/architecture/` 与代码为准,文中路径与状态为当时记录;公开链接退役另见 issue #215。
> 实现方:Claude Code(Opus 5.5);检视方:Codex(gpt-6-astra,ultra,两轮通过,见 §7)。issue [#213](https://github.com/zlzfun/DoramiSourceArchive/issues/213) · 样页 [`docs/design/guest-mode/`](../design/guest-mode/README.md)

## 0. 拍板记录

**2026-10-22 第一批(用户)**

1. 游客碰到需要账号的操作**只提示登录,不做注册**。
2. AI 各用途全开(翻译 / 问答 / 速读 / 社交翻译 / 逐字稿翻译 / 长文后台翻译 / 播客与文章点播),默认额度尽可能大。
3. 个人早报**全体游客共享一版**:早报本就按默认订阅编排,游客改不了订阅,想改就登录。

**2026-10-22 第二批(用户,看过 R1 样页)**

4. 游客总闸**默认开**。
5. AI **按每位游客分别计次**,不要「全体游客合计」的总量设计(R1 的游客池与游客日 token 预算取消)。
6. 登录提示里「收藏跟着账号走,换台设备也在」太像套话,换成平实说法;其余文案照样页。
7. 其余按推荐:深链直接以游客进入、根地址登录页加「先逛逛」(D2);公开分享页加入口(D3);可逛范围同正式读者(D4);已读每访客各记各的(D5);游客单列统计(D7);反馈照常显示、点了提示登录(D8);登录后只回原处(D9);公告游客可见、关闭按访客记(D10)。
8. 顺手修手机端:发现页卡片被裁且不等宽;发现页与社交媒体页能左右漂移。

**2026-10-23 第三批(用户,目检)**

9. 游客总闸开时**分享菜单只留站内链接**(「复制链接 · 免登录可看」,公开链接区不出现);总闸关时两档照旧;已发出的公开链接照常有效。公开链接整体退役另立 [#215](https://github.com/zlzfun/DoramiSourceArchive/issues/215) 评估。样页里公开链接「仅这一篇」的注记不再采用。
10. 每位游客的 AI 日额度确认按读者同额(50 / 100 / 50 / 5)。
11. 登录页的游客入口不要整条次级按钮「先逛逛」,改为登录按钮下方一行小字「游客登录」(连同其下的说明句一起去掉)。

## 1. 目标与边界

- 收到站内链接的人不登录也能第一时间看到那篇,并像正式读者一样逛:早报、文章 / 播客 / 社交流、发现页、榜单、检索、AI。
- 需要「属于自己」的操作(收藏、订阅、兴趣、公开分享、反馈、接入集成、账户设置)统一弹登录提示;登录后回到原处。
- 管理面看得到游客数据:访客、访问、阅读、AI 用量、入口来源、转化。
- **不做**:注册 / 申请开通;游客数据迁移到登录后的账号;对外公开的匿名内容端点(游客照样要先拿到游客会话)。

## 2. 现状要点(决定方案的事实)

- **鉴权**:`src/api/app.py` 中间件 `require_admin_session` 对 `/api/*` 一律要会话(登录 / 会话 / 健康 / `/api/public/*` 除外);`read_auth_token` 每请求查 `users`(行存在、启用、角色一致、会话世代一致);角色只有 admin / user,`reader_role_enabled` 只放行这两者。
- **前端 401 即踢回登录页**:`frontend/src/api.js` 的 `apiFetch` 对任何非 `/auth/` 的 401 派发 `dorami-auth-expired`,`App.jsx` 收到即回登录页。**所以游客被拒的端点不能回 401**。
- **个人早报版面外键到 `users`**:`personal_digest_editions.owner_username → users.username`(SQLite 开 `foreign_keys`);兴趣表同。没有账号行的身份建版面会撞外键,`start_personal_digest_edition` 把 IntegrityError 当并发重试,会无限递归。
- **早报的范围只读订阅行**:`resolve_personal_digest_source_ids` 只读 owner 的 `reader_subscriptions` 行;默认名单(`src/services/reader_defaults.py`)只在账号首次播种时用一次。
- **按用户名记的个人状态都没有外键**:已读两张表、`AiUsageRecord`、`ReaderReadRecord`、公告关闭、长文翻译任务的 `created_by` 都只存用户名字符串。
- **AI 授权查账号行**:`_require_reader_ai` 与 `translation_jobs.authorize` 都要求账号行存在、启用、`ai_beta_enabled`;额度按 `AiUsageRecord.username` 计(translate 50 / ask 100 / summarize 50 / 点播共池 5);长文翻译每用户同时最多 3 篇。
- **媒体代理** `/api/media/proxy` 缓存未命中时会去源站下载——对游客放开就成了公网可用的取图代理。
- **用户名不允许含冒号**(`create_user`),删号墓碑已用 `deleted:` 前缀;冒号是系统保留身份的天然命名空间。

## 3. 方案

### 3.1 身份:会话按访客,三件事落到共享保留账号

- **每位访客一个身份** `guest:v:<vid>`(vid 随机 16 字节)。游客会话令牌沿用现有登录 Cookie(同名同签名),载荷 `{"sub": "guest:v:<vid>", "role": "guest", "gen": <保留账号会话世代>, "exp": …}`,有效期同账号会话(7 天);另写一个签名的长效 Cookie 记住 vid,同一浏览器回来沿用同一身份。登录成功后账号令牌覆盖游客令牌。
- 因为会话的用户名就是访客身份,**已读状态、AI 计次与用量、阅读计量、长文翻译任务、公告关闭**都天然按访客分开,这些链路不改。
- **共享保留账号** `guest:shared`(`users` 表一行,`role = "guest"`,不可用的密码哈希,免兴趣引导,`ai_beta_enabled` 即「游客 AI」开关)。只有三件事落到它身上:
  1. **订阅范围**:`resolve_subscribed_source_ids` 对访客身份改读保留账号的订阅行(§3.3);
  2. **共享早报**:早报路由把访客身份换成保留账号再读写版面(§3.4);
  3. **授权**:会话校验、运行时能力、`_require_reader_ai`、`translation_jobs.authorize` 查账号行时换成保留账号。
- `read_auth_token` 对 `role == "guest"`:sub 必须是访客身份;保留账号行启用、世代一致、总闸开。**总闸关 = 保留账号停用 + 轮换世代**,在线游客下一次请求即失效(回 401,前端回登录页)。
- 保留账号排除在一切账户管理面之外:账户名单、批量操作、改角色 / 启停 / 删号 / 重置密码一律拒绝;`seed_root_admin_if_empty` 的「空表」判定忽略它;账户增长、读者数不计它。统一经 `accounts.is_guest_username` 判定(保留账号与 `guest:v:*` 都算)。

### 3.2 门控:游客白名单(fail-closed)

- 新模块 `src/api/guest_policy.py`:游客可达路由的**唯一白名单**,按 (方法, 路由模板) 登记;中间件对游客会话先查白名单,不在其中的一律 **403 `{"code": "login_required"}`**(不回 401,见 §2)。`/mcp` 一并拒绝。
- 白名单内的请求按读者面处理:`reader_role_enabled` 增收 guest;collector / admin 判定对游客恒假。
- 守卫:`tests/test_route_authz_audit.py` 增一条——白名单条目必须真实存在、其余路由对游客会话一律 403 `login_required`。新端点默认不对游客开放。
- 新公开端点 `POST /api/auth/guest`(`{entry}`):总闸开时签发游客会话并记一次访问;总闸关回 403 `guest_disabled`;已登录账号调用回 409,不降级。
- `GET /api/auth/session` 匿名时多给 `guest_access_enabled`;游客会话时 `user.role = "guest"`,展示名「游客」。`/api/runtime` 对游客给 `account_role: "guest"`,对所有会话给 `guest_access_enabled`(分享菜单文案要用)。

**游客白名单(初稿,实现时以路由表为准)**

| 类别 | 路由 |
|---|---|
| 会话 | `GET /api/runtime`;`/api/auth/session`、`/api/auth/logout`、`/api/auth/guest` 本就公开 |
| 读内容 | `GET /api/articles`、`/api/articles/facets`、`/api/articles/{id}`、`/api/articles/{id}/analysis`;`GET /api/reader/sources`、`/collections`、`/rankings*`、`/announcements`、`/favorites`(恒空)、`/interests`(恒空)、`/interests/catalog`;`GET /api/podcasts/episodes/{id}/texts`、`/api/reader/articles/{id}/listen-audio`、`/api/reader/podcast-artifacts/{id}/audio`;`GET /api/media/proxy`(仅命中缓存,§3.7) |
| 早报 | `GET /api/reader/briefs`、`/today`、`/{date}`;`POST /api/reader/briefs/today/ensure` |
| 已读(按访客) | `POST /api/reader/articles/{id}/read`、`/mark-read`、`/mark-unread`;`GET /api/reader/unread-counts`;`POST /api/reader/mark-all-read`、`/mark-scope-read`、`/sources/{id}/mark-all-read` |
| 公告(按访客) | `POST /api/reader/announcements/{id}/dismiss` |
| AI | `/api/reader/ai/*` 全部(翻译、社交翻译、长文任务及续译 / 取消、逐字稿翻译、播客 / 文章点播、速读、问答及进度) |

其余全部拒绝,包括订阅 / 退订 / 合集 / 批量订阅、自定源、OPML、收藏写、公开分享、聚合令牌、`/api/subscriptions*`、反馈、兴趣写与引导、早报重编、改密 / 头像 / 偏好、`/api/feed*`、`/api/skill*`、`/api/mcp*` 以及全部管理与采集面。

### 3.3 默认订阅同步

- 保留账号的订阅行**镜像默认名单**(`reader_defaults.default_source_ids`):开总闸、改默认名单、启动时各同步一次,增缺删多;不写水位(已读水位按访客懒初始化,即新账号同款「最近 20 篇未读」)。
- 名单有变化时,系统替游客早报发起一次重编(原因 `subscription_changed`,与下架源扇出同路),当天的游客版就跟上新名单。
- 保留账号的订阅行会被几处全站统计误算,一律排除:发现页「N 人订阅」、运维总订阅数与各源订阅数。访客身份没有订阅行,`GET /api/reader/sources` 对游客跳过首次播种。

### 3.4 游客早报:共享一版

- 版面归保留账号,所有游客读同一份;08:30 定时本就遍历全部启用账号,自然包含它;当天第一位游客打开时若尚未生成,`ensure` 现场生成。
- 生成是确定性的,唯一的模型调用是标题中文化(有缓存、有预算),一天一版成本可忽略。
- 游客不能重编(白名单不含);页面不显示「重新编排」、兴趣引导横幅、「下次编排生效」提示;说明行打头挂「游客版」并给登录入口;选篇理由「来自你订阅的」对游客改为「来自默认订阅的」。
- 当天版面生成失败时,游客打开会触发一次系统重试(10 分钟内不重复),避免失败版挂一整天。
- 运维「分析 → 指标」的早报版面数排除游客版。

### 3.5 已读状态:每访客独立

- 会话用户名即访客身份,已读两张表按它读写;订阅范围经 §3.1 的映射取保留账号。阅读器的未读加粗、未读数、「全部 / 未读」、各种「全部标已读」、「载入 N 篇新文章」**前端一行不改**就对游客成立。
- `POST /read` 对游客照常:逐篇已读记在访客名下,文章阅读量 +1,阅读计量记在访客名下。
- 回收:访客 30 天无访问,其已读行与公告关闭行随每日 04:30 留存清理删除。

### 3.6 AI 试用:每位游客分别计次

- 授权:全局 AI 总闸 ∧ 保留账号 `ai_beta_enabled`(运维游客区的「游客 AI」开关)∧ LLM 已配置。
- 额度:`_enforce_ai_daily_quota` 与长文翻译的 `_cost_gate` 对访客身份改读 KV `guest_ai_daily_limits`(translate / ask / summarize / 点播池四个键,**缺省与正式读者相同**:50 / 100 / 50 / 5),计数照旧按 `AiUsageRecord.username`——即每位访客各自一份。管理员可在游客设置里调高调低(0 = 不限)。
- 长文后台翻译每位访客同时最多 3 篇,与读者一致;翻译结果照旧写文章缓存,正式读者也受益。
- 超出访客额度回 **429 `{"code": "guest_quota_exhausted"}`**(带 Retry-After),前端弹「登录后继续使用」;全站日 token 预算耗尽仍是原文案,不引导登录(登录也解决不了)。
- 已知取舍:清 Cookie 即得到新访客身份、额度重置;全站日 token 预算是最终兜底(§6)。

### 3.7 媒体代理

- 游客会话访问 `/api/media/proxy` 只返回**已缓存**的图片,未命中一律 302 回源站,不在服务端下载。正文图随文预取,绝大多数已在库里。

### 3.8 计量与统计

- 阅读与 AI 用量直接复用现有聚合表:`ReaderReadRecord` / `AiUsageRecord` 里 `guest:v:*` 的行就是游客的。
- 新表 `guest_visits`(迁移 `660028d4ab6f`):`(day, visitor_id)` 唯一;`entry`(当天首个入口:`deep_link` / `share_page` / `login_page` / `return`)、`visits`、`first_seen_at`、`last_seen_at`、`converted_username`、`converted_at`。写入点:`POST /api/auth/guest`(建行、记入口);游客打开应用(`/api/runtime`)`visits` +1;游客在本浏览器登录成功时,在其最近一行记转化。留存 365 天(同登录事件)。
- 指标:访客(去重 vid)/ 新访客、访问、阅读、AI 调用与 tokens、转化与转化率、入口分布、每日走势。
- 现有口径排除游客:运维「用户」页的账户 / 活跃 / 登录 / 阅读 / AI 五格、活跃榜、账户增长、账户名单与单用户抽屉;「AI」页按用途的总量照常含游客(成本真实),按用户视图里全部访客合成一条「游客」。

### 3.9 前端

- **启动**:匿名 + 深链 + `guest_access_enabled` → 自动 `POST /api/auth/guest {entry: "deep_link"}`,进阅读器并打开该篇;否则登录页,总闸开时登录按钮下方多一行小字「游客登录」(第三批拍板 11;R2 样页为按钮「先逛逛」)。已有游客会话直接进阅读器。
- **登录提示**:新组件 `frontend/src/components/LoginPrompt.jsx`,外形照抄 ConfirmDialog(`<Modal closeOnOverlay centered>`),标题按动作给,按钮「继续逛逛」「登录」;`api.js` 对 403 `login_required`、429 `guest_quota_exhausted` 派发 `dorami-login-required`,由 App 统一弹出,原调用处的报错静默。前端拦截优先(不发请求就弹),后端码兜底。
- **去登录与回原处**:「登录」切到现有登录页(不另造表单,下游的登录入口、正在进行的登录页重设计都不受影响),登录页对游客多一个「返回继续逛逛」;登录成功后按深链机制回到原处:在看文章就回这篇,否则落早报——阅读器按新账号整体重挂(key 含 `account_role`),游客态不带进账号。
- **自动请求**:游客不拉收藏 id、不轮询反馈未读;早报 `ensure` 照常。
- **各处差异**见 §4;桌面与移动壳同改。分享菜单:总闸开时只留站内链接(注记「免登录可看」),公开链接区不出现、也不拉分享列表;总闸关时两档照旧(第三批拍板 9)。

### 3.10 管理面

- 运维管理 →「用户」子页在「账户增长」下新增「游客」区,全体管理员可见(聚合口径):总闸 +「游客 AI」两枚开关、五格 KPI(访客 / 访问 / 阅读 / AI 调用 / 转化)、每日走势(访客柱 + 转化点)、入口分布条。
- 区头齿轮开「游客设置」抽屉:翻译 / 问答 / 速读 / 点播四类**每位游客**的每日上限(缺省与读者相同,0 = 不限);默认订阅名单只读展示,回指「内容」子页既有编辑器。
- 端点:`GET /api/admin/guest?days=`(设置 + 统计)、`PUT /api/admin/guest/settings`;落在 `/api/admin` 前缀,自动仅管理员可写并入审计。

### 3.11 顺手修(手机端)

- 发现页网格 `grid-template-columns: 1fr` 的最小值被卡片内容撑破(列宽 = 卡片最小内容宽,超出容器且各分区不一致)→ `minmax(0, 1fr)`(两列档同改)。实测 390px 屏卡片由 403px(右侧「订阅」被裁)回到 358px 等宽。
- 发现页窄屏侧距:桌面 `.reader-disc-head` / `.reader-disc-scroll` 的 28px 侧距在手机上与内层 16px 叠成 44px(`mobile.css` 原意是「此处收窄」)→ 外层归零,只留 16px。
- 社交媒体页日期组头 `.reader-social-day` 用桌面 `margin: 0 -24px` 抵消 24px 列侧距,手机列侧距是 12px → 两侧各伸出 12px,整列可横向拖动 → 手机改 `-12px`。
- 两页的移动滚动容器加 `overflow-x: hidden` 兜底锁横向。全部手机 Tab 与发现页四个分段逐一测量:任何容器横向可滚宽度为零。

## 4. 游客界面差异(桌面 / 移动)

| 位置 | 游客看到的 | 点击后 |
|---|---|---|
| 视图轨底部头像 / 移动「我的」账户卡 | 「登录」按钮 / 游客卡 + 登录 | 去登录页 |
| 文章收藏星(列表行、阅读窗、早报条目、社交卡、移动文章页) | 照常显示 | 提示「登录后即可收藏 · 收藏会保存到你的账号。」 |
| 列表头「只看收藏」/ 移动「我的 → 收藏」 | 照常显示 | 提示「登录后可查看收藏」 |
| 源栏「订阅 \| 兴趣」轴的「兴趣」、发现页「兴趣」Tab、移动「我的兴趣」 | 照常显示 | 提示「登录后即可设置兴趣」 |
| 订阅 / 退订(发现卡、预览横幅、阅读窗「+订阅」、早报「+订阅」、源行「−」与右键 / 长按菜单)、合集订阅、「订阅全部文章源」 | 照常显示 | 提示「登录后即可订阅『来源名』」/「登录后即可调整订阅」 |
| 「添加文章源」、设置柜「订阅源 OPML」 | 照常显示 | 提示「登录后即可添加来源」 |
| 已读 / 未读、全部标已读、未读筛选 | 照常可用(按访客记) | — |
| 分享菜单 | 总闸开时只有「复制链接 · 免登录可看」(所有人一样,无公开链接区) | — |
| AI(翻译 / 问答 / 速读 / 点播 / 逐字稿) | 照常可用 | 用满当天额度时提示「游客今日的 AI 试用已用完,登录后继续使用」 |
| 早报页 | 说明行打头「游客版」+ 登录入口;无「重新编排」、无兴趣引导 | — |
| 视图轨「反馈」/ 移动「我的 → 反馈」 | 照常显示 | 提示「登录后即可反馈」 |
| 设置柜 | 「账户」区换成游客卡 + 登录;「外观」「关于」照常;「订阅源 OPML」「反馈」「接入集成」挂锁,点开是登录入口 | — |
| 公告横幅 | 照常显示 | 关闭按访客记 |

## 5. 实施(一个分支、一个 PR)

1. 版本:本波是 v3.66.0 之后第一波,开工即改 `3.67.0-alpha`。
2. 后端:保留账号与访客会话、白名单与中间件、路由审计守卫、账户面排除 → 默认订阅同步与共享早报 → AI 授权映射与访客额度 → 媒体代理 → `guest_visits` 迁移与计量 → 管理端点与排除口径 → 留存清理。
3. 前端:启动与入口、登录提示与回原处、阅读器各处差异(桌面 + 移动)、设置柜、分享菜单、早报页、公开分享页入口、运维游客区;手机发现页与社交页的横向修复。
4. 自测:`tests/test_guest_mode.py` 覆盖会话 / 总闸 / 白名单 / 排除 / 共享早报 / 每访客已读 / AI 额度码 / 媒体代理 / 计量与转化;前端纯函数 `node --test`;沙箱 Playwright 截图亮暗 + 移动壳。
5. 文档同波:`docs/architecture/accounts-and-access.md`(第三角色、访客身份与保留账号)、`docs/architecture/llm-and-ai.md`、`docs/architecture/admin-ops.md`、`docs/architecture/reader-distribution.md`、`docs/architecture/analysis-and-briefs.md`(顺手更正已退役的「公共日报就绪追加」触发)、`docs/reference/endpoints.md`、`docs/frontend/conventions.md`(需账号的操作对游客一律走登录提示)、`AGENTS.md` 架构速览「整体」行的角色轴、用户手册;version-history / backlog / decisions。

### 5.1 落地记录(2026-10-22)

- 后端(提交 `2769c6f`):`services/accounts.py`(游客身份与保留账号、账户面排除)、新 `services/guest_access.py`(总闸 / 每访客额度 KV / 入口与转化计量 / 统计 / 访客状态回收)、新 `api/guest_policy.py`(白名单)、`api/app.py`(中间件门控、`POST /api/auth/guest`、会话与运行时载荷、登录转化、默认订阅镜像与共享早报同步)、`api/feed_service.py`、`api/routers/{reader,personal_briefs,media,admin}.py`、`services/{translation_jobs,ai_usage,reader_activity,analysis_observability,admin_audit,retention}.py`、模型 `GuestVisitRecord` + 迁移 `660028d4ab6f`。
- 前端:`utils/loginPrompt.js`(文案表 + 事件)、`utils/guestCopy.js`(锁定分区说明 + 早报理由改写)、`components/LoginPrompt.jsx`、`components/settings/GuestSections.jsx`、`components/admin/GuestZone.jsx`;`App.jsx`(启动 / 深链自动游客 / 入场提示 / 叠层登录页 / 回原处 / 会话静默续期)、`api.js`、`LoginScreen.jsx`(「游客登录」「返回继续逛逛」)、`useReaderState.js`(写操作拦截)、`ReaderTab.jsx` / 移动三页(轨底登录、各入口)、`SettingsModal.jsx` / `MobileSettings.jsx`、`ShareMenu.jsx`、`PersonalBriefPage.jsx`、`SharedArticlePage.jsx`、`ReaderAiPanel.jsx`、`DiscoverPage.jsx`、`Modal.jsx`(叠层标记)、`Toast.jsx`(行内动作)、`AdminOpsTab.jsx`(游客区 + AI 看板「游客」系列)、`index.css` / `mobile.css`。
- 自测:后端全量通过(含 `tests/test_guest_mode.py` 14 项、路由审计 3 项反向对照);前端 lint / 128 项单测(新 `test/guestMode.test.mjs` 7 项)/ build 通过;沙箱(真实库副本)Playwright:桌面游客流程 16 项 × 亮暗、运维游客区 17 项、分享页入口 7 项、AI 额度(`ctx.route` 伪造 429,不打真模型)4 项、手机游客 12 项、手机横向溢出扫描全部 Tab 与发现页四分段为零,控制台无报错(既有 404 除外)。
- 实现中的两处修正:轨底「登录」图标被未分层的全局 `* { box-sizing: border-box }` 压成 6px(样页的 `content-box` 在应用里不生效)→ 改为 30px 方块 + 6px 内距;问答面板「点外即收起」把叠在上面的登录提示也算作外部,关掉提示面板跟着收起 → 共用外壳 `Modal.jsx` 的遮罩加 `data-modal-layer` 标记,面板把弹窗里的点击(含遮罩)同灯箱一样当作上层操作(lint 规则 `no-raw-modal-overlay` 不许别处出现遮罩类名,故用标记而非类名);发现页「添加源」对游客改为点按钮即提示,不先打开表单。

## 6. 风险与取舍

- **公网暴露与 AI 成本**:总闸默认开(拍板 4)= 升级后外网可见内容对任何人开放,游客也能用 AI。兜底:每位游客与读者同额的日额度;全站日 token 预算(运维 AI 页,现缺省不限)对游客同样生效;总闸一键关。清 Cookie 能换新访客身份绕开单人额度,公网部署宜设全站预算。
- **保留账号漏出**:任何遍历 `users` 的新代码都可能把它算进去。用 `accounts.is_guest_username` 单点判定,测试钉住账户名单、增长、KPI、订阅人数四处不含游客。
- **与登录页重设计并行**:另一条线正在做登录页五方向样页。本波只在登录页加「游客登录」「返回继续逛逛」两个入口,重设计落地时须保留。
- **下游内网分支**:内网登录入口有下游适配;游客「去登录」走现有登录页,同步 master 时核对两个入口仍在。
- **爬取**:游客会话可遍历可见归档;本波不做限流,观察期看 `guest_visits` 与访问日志再定。

## 7. 检视

实现方 Claude Code(Opus 5.5);检视方 Codex(`gpt-6-astra`,reasoning effort ultra),本机 `codex exec` 只读沙箱,任务书 / 报告 / 表态 / 修复清单在 worktree `.review/`(不入库)。

**R1(2026-10-22,`fa8b254..549f488`)**:1 P1 + 8 P2 + 1 P3,未发现越权成账号、访客间串号、保留账号可被管理面操作,方案形状不需重做。逐条核对后**十条全部接受**:

| # | 问题 | 结论 |
|---|---|---|
| F1 P1 | 逐字稿翻译不走成本闸,免登录访客可绕过访客额度与全站预算 | 修;**范围收窄**:现役用例钉住「逐字稿翻译对正式读者不设日额度与全站预算」(多段调用算一次阅读动作),读者侧保持原决定,只对访客在缓存未命中时加闸 |
| F2 P2 | 登录后 runtime 晚到,回跳被旧游客阅读器消费,新阅读器落早报 | 修:叠层登录不预热 runtime,完成时主界面卸载、新 runtime 到达后重挂 |
| F3 P2 | AI 超额自动弹出的提示框登录没有回跳目标 | 修:统一按「眼前正在看的那篇」回跳,入口一律走它 |
| F4 P2 | 晚到的并发 401 把刚续期的游客踢回登录页 | 修:会话代次 + 认证请求串行队列(协商两轮补齐:代次在 Cookie 换身份时推进、登录与续期互斥、续期不误判自身推进) |
| F5 P2 | 游客登录默认管理台的管理员落到管理台 | 修:登录完成即进阅读器 |
| F6 P2 | 共享早报失败重试非原子,并发突破 10 分钟去重 | 修:写锁内复核 + 新 pending 版本即认领(协商后弃用 KV 认领键:单键不等判断挡不住旧版本晚到,且认领与建版须同事务) |
| F7 P2 | 转化按访问日期预过滤,漏掉窗口内发生的转化 | 修:转化按事件时间;窗口访客 = 访问 ∪ 转化,转化率 ≤ 100%(协商中弃用「补转化当天行」——会让补行续延 30 天资格) |
| F8 P2 | 统计刷新失败时把旧数据当新窗口展示 | 修:StaleNotice + 派生数字按快照自带窗口 |
| F9 P2 | 长文翻译双重超额时错误引导登录 | 修:先判全站预算 |
| F10 P3 | 两处硬编码圆角 | 修:token / §5 特例 |

修复与定向验证逐条见提交 `fix(guest): R1 检视返修` 的说明;每条新增回归用例都做了反向对照(去掉修复即失败)。

**R2 复检(2026-10-23,只对照修复清单,最后一轮)**:**通过**,F1–F10 逐条成立,无新引入 P1;F1 收窄范围与 F7 口径(不补行)均确认。两条非阻断观察项已顺手收口:①续期与登录交错时在途的旧身份 runtime 可能盖住新会话 → `loadRuntimeAndFetchers` 拉取途中会话代次变了就按新身份重拉,`refreshRuntime` 丢弃旧身份结果;②缓存命中用例补满访客 translate 次数,与清单描述一致。
