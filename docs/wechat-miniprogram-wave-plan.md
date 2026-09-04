# 微信小程序端阅读器 Wave 方案(P0)

> 对应需求:[Issue #17](https://github.com/zlzfun/DoramiSourceArchive/issues/17)
> 文档状态:Proposed(P0 方案,待负责人拍板 §3 三处决策 + §8 前置确认)
> 起草日期:2026-09-03(Asia/Shanghai)
> 工作分支:`feat/issue-17-wechat-miniprogram`(基于 main @ v3.45)
> 前史:v3.29 移动波评估「小程序 vs H5」拍板 H5 先行、小程序留二期;H5 移动壳自 v3.29 稳定运行至 v3.45

## 1. 结论先行

小程序端定位是 **H5 移动壳的二期**,不是第三个产品:同一后端、同一账号体系、同一读者门控,复刻 H5 移动壳的阅读闭环,只多做 H5 做不到的两件微信生态能力——**原生转发卡片**与(可选的)**订阅消息推送**。

四项核心拍板建议(细节与备选见 §3):

| 决策 | 建议 | 一句话理由 |
|---|---|---|
| 框架 | **Taro 4 + React**,独立目录 `miniprogram/` | 与前端同语法、可共享 DOM 无关的 utils;但 `useReaderState` 与 DOM 强耦合,数据层**重写不复用** |
| 鉴权 | 后端会话 token 增加 **`Authorization: Bearer` 载体**,登录响应可回 token | 零新令牌类型,复用现有签名/过期/世代吊销语义;比手工搬运 Set-Cookie 稳 |
| 正文 | 服务端新增 **markdown → 净化 HTML** 端点,客户端 `rich-text` 渲染 | 小程序无 DOM,react-markdown 不可用;服务端统一处理图链改写与公式降级 |
| 图片 | 新增 **签名公开图链** `GET /api/public/media?u=&exp=&sig=` | `rich-text` 的 `<img>` 带不了鉴权头,现有 `/api/media/proxy` 在读者门控内不可达 |

后端改动收敛为三处小切口(§5),全部落在既有前缀的门控语义内,不引新表、不动 Cookie 流程;桌面与 H5 零行为变化。

**硬前置**(§8):小程序 `request`/`downloadFile` 合法域名要求公网 HTTPS + ICP 备案域名,且主体与类目选择直接决定能否过审。这两项不由代码解决,须在 P1 动工前由负责人确认;内网部署(master 分支环境)微信客户端不可达,**小程序端只面向公网部署**。

## 1.1 两版策略(2026-09-04 用户拍板方向)

资讯聚合类小程序有上架压力(类目资质 / 审核口径,§8),负责人提出**做两版**:

- **基础版(本文全部内容)**:忠实复刻 H5 移动壳的阅读闭环,承接转发卡片。先做、先内部体验版,提审结果决定是否公开上架。
- **工具版(二期构思,另立方案文档)**:弱化「新闻资讯流」形态,突出「工具」属性——以**语音 / 文字对话的 AI 助手**为主界面(可配一个卡通形象),读者问「最近有什么发布的大模型?」「具身智能有哪些进展?」,助手检索归档后**语音播报**答复。
  技术上它是既有能力的重组而非新建:问答走 `POST /api/reader/ai/ask`(scope=subscription/all,v3.30 「LLM 计划检索 + FTS5」两段式 + v3.32 引用联动),语音输入用微信同声传译插件或 `RecorderManager` + 服务端 ASR,语音输出接 TTS(v3.44 播客波已在评估语音栈,见 `specs/007-podcast-intelligence/voice-stack-decision.md`);文章列表退为回答里引用 `[n]` 的落地页,不再是首屏。
  两版共用本文 §5 的后端切口(Bearer 载体 / 渲染端点 / 签名图链)与 §6 的工程底座——**工具版是基础版之上换首屏与交互形态,不是分叉**;若基础版未过审,工具版可作为同一 AppID 的下一个提审版本。

**先推进基础版**;工具版的方案文档在基础版 P1 真机跑通后再起草(届时 §7 实测结论可直接复用)。

## 2. 范围

### 2.1 首期(P1+P2)做什么

读者面阅读闭环,与 H5 移动壳(`frontend/src/components/mobile/`)同口径:

- 登录(账号密码,账号由管理员建立)→ 会话保持 7 天(沿 `[auth] session_seconds`)
- 四容器条目流:文章 / 播客 / 动态 / 社交(H5 底部 Tab 的原生翻译),日期分组、未读小蓝点、无限滚动
- 正文页:标题 / meta / 正文 / 「阅读原文」/ 收藏 / 标读 / 分享;播客单集含原版音频播放
- 源过滤(抽屉或独立页)+ 发现页(源目录一键订阅/退订;合集视图)
- 我的:头像与账号、主题、反馈与建议、公告、退出
- **转发卡片**:文章 / 日报单篇 → `onShareAppMessage`,收件人点开落该篇(需登录则先登录再落地)
- 个人早报(v3.44):`personal_digest_enabled` 能力位开启时提供入口与阅读

### 2.2 首期不做

- 不改桌面 / H5 现有行为;后端新端点一律走 `/api/reader/*`(读者门控)或 `/api/public/*`(免登录、自带签名)前缀,不改中间件表
- 不做微信 openid 自动注册账号、不做微信登录授权、不做支付
- 管理台、日报配置、节点管理等 admin 面不下放(admin 账号登录小程序同落阅读器,与 H5 同口径)
- AI 问答面板与阶段化等待态(v3.32)不下放;翻译 / 速读按能力位视 P2 余力
- 用户自定源添加(v3.40 两步流)不下放,源栏只展示已订阅的自定源
- 公开分享链接 `#/s/{token}` 不做小程序侧承接(访客无账号,继续走 H5 公开页)
- 朋友圈分享(`onShareTimeline`)不做:朋友圈打开的是单页模式、无登录态,阅读器内容无法呈现

## 3. 关键决策与备选

### 3.1 框架:Taro 4 + React(建议)

**方案 A · Taro 4(React 语法)**——建议。

- 收益:与 `frontend/` 同语法与同一批开发者心智;`frontend/src/utils/` 里 DOM 无关的助手可直接共享(`readerText.js` 的 `looksChinese`、`readerTime.js` 的 `dayKeyOf`、`markdownTitle.js`、`sourceTaxonomy.js` 的角色判定与分组);`hooks/useAbortableLoad`/`useDebouncedValue` 类纯逻辑 hook 可共享。
- 明确**不复用** `hooks/useReaderState.js`(1162 行):它依赖 IntersectionObserver、`useLayoutEffect` 量 DOM、lucide-react 图标元素、`window`/`document`(右键菜单、剪贴板、深链 hash),是桌面/H5 的胶水层。小程序的数据层按其**职责清单**重写一份精简版(源目录/订阅、未读、收藏、列表分页、正文与译文缓存),接口命名对齐以便对照。
- 共享方式:Taro `alias` 指向 `../frontend/src/utils`,并加一条 lint 规则/测试守卫,禁止 `miniprogram/` 引入含 `window`/`document` 的模块(共享清单白名单化)。
- 风险:Taro 版本升级与微信基础库兼容;`lucide-react` 不可用,图标改内联 SVG(base64 `<image>`)或图标字体;包体积主包 2MB 限制(Taro React 运行时 ~300KB,可控;必要时分包)。

**方案 B · 原生小程序(WXML/WXSS)**:零框架风险、体积最小,但与现有前端零共享、第二套心智;若 A 的实测(§7)暴露运行时问题,退回 B。

**方案 C · web-view 套 H5**:小程序只做一页 `<web-view src="https://…/#/…">`,直接复用现成移动壳,转发卡片经 `webViewUrl` 携带路径。
理论上 P1 成本最低,但:①个人主体不可用 web-view;②平台运营规范对「仅以 web-view 跳转网站」的套壳形态有驳回风险(需核实当期规则);③导航栏被微信接管、页内无原生控件、无法承接订阅消息;④业务域名另需校验文件。**不作主路径**,可作为审核通道打通前的内部验证壳(不提审)。

### 3.2 鉴权:Bearer 载体(建议)

现状:`src/api/app.py` 的 `create_auth_token` 生成 HMAC 自签名 token(`sub/role/gen/exp`),`set_auth_cookie` 以 HttpOnly + SameSite=Lax Cookie 下发;中间件 `current_auth_session` **只从 Cookie 读**。小程序 `wx.request` 不维护 Cookie jar。

**方案 A · Authorization: Bearer 同 token**——建议。

- `current_auth_session(request)`:Cookie 缺失时回退读 `Authorization: Bearer <token>`,交给同一个 `read_auth_token`——签名校验、过期、账户存在/启用/角色一致、`session_epoch` 世代吊销**全部原样生效**,改密/删号/停用对小程序会话的吊销语义与浏览器完全相同。
- `POST /api/auth/login`:请求头带 `X-Dorami-Client: miniprogram`(或 body `return_token: true`)时,响应体多回 `session_token`;Cookie 照常下发(对小程序无害)。浏览器路径不带该标记,响应形状不变。
- 客户端:`wx.setStorageSync('dorami_session', token)`,请求拦截器统一挂头;401 → 清 token → 登录页(带 redirect 回落地页)。
- 退出:`POST /api/auth/logout` 照调(清 Cookie 对小程序是空操作)+ 本地清 token。token 无服务端黑名单,与浏览器 Cookie 现状一致(退出=丢弃凭证),不新增语义。
- 会话时长 7 天,到期重登;**不做 refresh token**(记 backlog,量上来再议)。

**方案 B · 手工搬运 Set-Cookie**:`wx.request` 的 `res.header['Set-Cookie']` 可读(HttpOnly 只约束浏览器 JS),存下后逐请求挂 `Cookie` 头。零后端改动,但多 Cookie 合并、iOS/Android 头字段大小写差异、Cookie 属性剥离都是已知坑,且把 Cookie 语法当传输格式用不如显式 Bearer 干净。不建议。

CORS 与小程序无关(`wx.request` 不带 Origin、不受同源策略约束),`[cors]` 节无需为小程序放宽。

### 3.3 正文渲染:服务端 HTML + rich-text(建议)

现状:正文以 markdown 存 `ArticleRecord.content`,前端 `ReaderMarkdown.jsx` 用 react-markdown(gfm/breaks/math+katex)渲染,图片经 `mediaProxyUrl` 改址。小程序无 DOM,这条链路整体不可用。

**方案 A · 服务端渲染端点**——建议。

- `GET /api/reader/articles/{id}/render` → `{ id, title, html, translated_html?, podcast?, … }`
  - markdown → HTML:`markdown-it-py`(纯 Python,gfm 表格/删除线/任务列表插件齐备;项目当前无 markdown 库依赖,需入 pyproject 并重导出 `docker/requirements.txt`)
  - **净化**:输出限定在 `rich-text` 支持的标签白名单内(`p/h1–h6/ul/ol/li/blockquote/pre/code/table/…/img/a`),剥 `script/iframe/style` 与事件属性;`class` 只保留渲染需要的少数几个(代码块语言标记)
  - **图链改写**:`<img src>` 一律换成 §3.4 的签名公开图链;`data:`/`svg` 原样丢弃
  - **公式降级**:`$…$`/`$$…$$` 无法在 rich-text 里排版,不解析、按原文文本保留——首版接受,AI/学术源公式密度高的文章观感打折,记 backlog
  - 复用既有:`stripDuplicateLeadingHeading`(日报首行重复标题剥离)的服务端等价实现;`translation_zh`/`translation_zh_title` 缓存命中时同步渲染 `translated_html`
  - 渲染现算不落库(与媒体热点图同思路);正文 markdown 在库中不变,导出契约零影响
- 客户端:`<rich-text nodes={html}>`。**已知限制**:`rich-text` 内 `<a>` 不可点、无法拿到 href——正文内链接首版不可点,「阅读原文」由页面按钮承担(与 H5 正文末尾「查看原文 ↗ · 域名」行同语);表格横向溢出需外套 `scroll-view`。
- 社交推文卡:正文本就是纯文本 + `extensions.media_urls`(推文图不塞正文,v3.12 契约),不经 markdown,直接用 `<text>` + `<image>` 网格,引用推/转推按 `quoted`/`reposted` 扁平结构渲染,与 `SocialFlow.jsx` 同源同契约。

**方案 B · 客户端 markdown 解析(towxml / wxParse 类)**:把解析器塞进包体(towxml 完整包 ~几百 KB 需分包),图链改写与净化逻辑要在客户端重做一遍,且公式/表格支持参差。服务端一处生效更可控,不建议。

**方案 C · `web-view` 只嵌正文页**:混合形态,导航割裂、正文页内原生控件(收藏/分享/播放器)全部让位,不建议。

### 3.4 图片:签名公开图链(必须项)

现状:`GET /api/media/proxy?url=` 在 `READER_API_PREFIXES` 内,需会话;`rich-text` 内 `<img>` 由微信客户端发起请求,**不带自定义头也不带 Cookie**,鉴权代理不可达。直连原图链则撞两堵墙:防盗链 CDN 无 Referer 即 403(v3.11 图床立项原因),且小程序 `<image>` 的远程域名不可控。

**方案 · `GET /api/public/media?u=<url>&exp=<unix>&sig=<hmac>`**

- 走 `/api/public/*` 既有免登录豁免(与 v3.25 分享媒体端点同路,零中间件改动)
- 签名 = HMAC-SHA256(`AUTH_SECRET`, `u|exp`),服务端校验签名与过期,任一失败 → 404(与分享链接同口径:不区分原因);有效期随会话档取 7 天
- **不是开放代理**:只有服务端在渲染正文时才会签发,客户端拿不到签名密钥;放行后复用 `media_store.get_or_fetch`(SSRF 拦截 / 魔数嗅探 / 大小上限 / 失败负缓存全部沿用),未命中即时下载,失败 302 回源
- 不计 `view_count` 之类计量;`[media] enabled=false` 时同 proxy 行为(302 回源)
- 播客封面 `podcast.image_url`、源头像 `avatar_url`、社交推文 `media_urls` 同样由服务端投影层签名后下发(render 端点与列表端点在 `client=miniprogram` 时附 `*_signed` 字段,或客户端调一个批量签名端点——实施时二选一,倾向前者:一次请求带齐)

**音频**:播客原版音频 `podcast.audio_url` 是发布者 enclosure(v3.44 拍板不镜像)。小程序 `InnerAudioContext.src` 对域名白名单的实际执行口径需**实测**(§7);若受限,音频同样走签名代理(流式透传,注意 Range 请求支持)。后台播放需 `app.json` 声明 `requiredBackgroundModes: ["audio"]`。

### 3.5 导航结构:原生 tabBar 5 项(待拍板)

H5 底部 Tab 最多 6 项(早报按能力位条件出现 + 文章/播客/动态/社交/我的),微信原生 tabBar 上限 5 且不能条件增减。

- **建议**:原生 tabBar = 文章 / 播客 / 动态 / 社交 / 我的;「早报」入口放「我的」页首行卡 + 文章页顶部一条入口签(`personal_digest_enabled` 为真时才画)。发现页、源过滤、兴趣管理均为 push 页(与 H5 同:低频目的地不占 Tab)。
- 备选:`custom-tab-bar` 自绘可条件增减,能 1:1 复刻 H5,代价是自绘 Tab 的样式与安全区适配全部自担。若负责人认为早报必须是一级 Tab,取此备选。

### 3.6 转发卡片与落地

- `onShareAppMessage` → `{ title: 文章标题, path: 'pages/article/index?id=<article_id>', imageUrl: 首图签名链或品牌图 }`
- 落地:`pages/article/index` 读 `id`;无会话 → 跳登录页并带 `redirect`;登录成功回落地页。这是站内深链 `#/reader/a/{id}` 的小程序翻译,**同样不落库、不外泄**;深链指向隐藏源 / 自定源且收件人非订阅者时,与 H5 一样得 404 → 页面呈「暂不可用」空态(v3.40 隔离语义的设计内静默)
- 日报单篇同路(`content_type=daily_brief` 也是 `ArticleRecord`)
- 小程序码 / 太阳码非首期

### 3.7 订阅消息推送(P3,可选,价值有限)

微信订阅消息分「一次性」与「长期」两类,长期订阅仅开放给特定政务/医疗等类目;资讯工具只能用**一次性订阅**——用户每次授权只换一条推送。可行形态:用户在早报页点「明早提醒我」授权一次,次日早报生成完成推一条。实现代价不小:①账号 ↔ openid 绑定(`wx.login` code → 服务端 `code2Session`,需保管 AppSecret,入 `services/credentials.py` 命名空间)与 `users` 表新列;②模板申请与 access_token 管理;③早报生成完成钩子。**建议后置到 P3 并在 P2 提审通过后重新评估是否值得**,首期不承诺。

## 4. 页面清单与 API 依赖

| 页面 | 路径(拟) | 主要 API |
|---|---|---|
| 登录 | `pages/login/index` | `POST /api/auth/login`(带 `X-Dorami-Client`)、`GET /api/runtime` |
| 条目流(4 容器) | `pages/feed/index`(tabBar ×4,`shape` 参数化) | `GET /api/articles?shape=&subscribed_scope=only&with_unread=true&skip=&limit=&search=`、`GET /api/reader/unread-counts`、`POST /api/reader/mark-all-read`、`POST /api/reader/sources/{id}/mark-all-read` |
| 正文页 | `pages/article/index?id=` | **新** `GET /api/reader/articles/{id}/render`、`POST /api/reader/articles/{id}/read`、`.../mark-read`、`.../mark-unread`、`POST/DELETE /api/reader/favorites/{id}` |
| 收藏 | 条目流的容器级过滤态 | `GET /api/reader/favorites?shape=` |
| 源过滤 | `pages/sources/index`(push) | `GET /api/reader/sources` |
| 发现页 | `pages/discover/index`(push) | `GET /api/reader/sources`、`GET /api/reader/collections`、`POST/DELETE /api/reader/sources/{id}/subscribe`、`POST/DELETE /api/reader/collections/{id}/subscribe` |
| 早报 | `pages/brief/index`(push) | `GET /api/reader/briefs/today`、`POST .../today/ensure`、`GET /api/reader/briefs`、`GET /api/reader/briefs/{date}`;兴趣 `GET/PUT /api/reader/interests`、`GET .../catalog` |
| 我的 | `pages/me/index`(tabBar) | `GET /api/auth/session`、`POST /api/auth/logout`、`POST /api/auth/change-password`、反馈 `GET/POST /api/reader/feedback`、公告 `GET /api/reader/announcements` + `.../dismiss` |
| 图片 | — | **新** `GET /api/public/media?u=&exp=&sig=` |

所有 `/api/reader/*`、`/api/articles`、`/api/media` 均在既有读者门控内;`/api/public/*` 免登录、自带签名。**不新增中间件前缀条目**。

## 5. 后端改动清单(最小切口)

1. **Bearer 会话载体**(`src/api/app.py`):`current_auth_session` 增加 `Authorization: Bearer` 回退;`login_admin` 按 `X-Dorami-Client` 头(或 `return_token`)在响应体附 `session_token`。中间件、`read_auth_token`、世代吊销零改动。
2. **渲染端点**(`src/api/routers/reader.py` + 新 `src/services/article_render.py`):markdown → 净化 HTML → 图链签名改写;`translated_html` 缓存命中即附;日报首行重复标题剥离。依赖 `markdown-it-py`(入 pyproject → `uv lock && uv sync` → 重导出 `docker/requirements.txt`,`tests/test_docker_requirements.py` 守卫)。
3. **签名公开图链**(`src/api/routers/media.py` 或 `share.py` 旁):`GET /api/public/media` + `services/media_signing.py`(签发/校验,密钥 `AUTH_SECRET`);列表/详情投影在小程序客户端标记下附签名字段。
4. **测试**(`tests/test_miniprogram_client.py`):Bearer 与 Cookie 等价(含改密后世代吊销、停用即 401)、浏览器路径响应形状不变;渲染端点白名单净化(script/iframe/事件属性剥除、img 全部改址)、公式降级、翻译缓存附带;签名图链篡改/过期/缺参 → 404、隐藏源文章图链不签发、`[media] enabled=false` 302。

### 5.1 实施记录

- **2026-09-04 · 后端三切口落地**(分支 `feat/issue-17-wechat-miniprogram`,待 PR):
  - `app.py`:`current_auth_session` Cookie 缺失时回退 `Authorization: Bearer`(仅认 `<payload>.<sig>` 形态,dsub_/dfeed_ 无点号值不当会话);`POST /api/auth/login` 带 `X-Dorami-Client: miniprogram` 头或 body `return_token=true` 时响应附 `session_token`/`session_expires_in`,浏览器路径响应形状不变。
  - `api/media_signing.py` + `GET /api/public/media?u=&exp=&sig=`(`routers/media.py`,与 proxy 共用供给主体 `_serve_media`):HMAC-SHA256(AUTH_SECRET) 前 32 位 hex,7 天有效;缺参/篡改/过期一律 404。
  - `services/article_render.py` + `GET /api/reader/articles/{id}/render`(`routers/article_render.py`):markdown-it-py(commonmark + table/strikethrough,breaks,html=False)→ 白名单净化(rich-text 标签子集;属性只留 a.href[http(s)]/img.src+alt/code.class[language-*]/ol.start/td|th.style[text-align])→ img 一律签名改写、非 http(s) 图整图丢弃;重复首行标题剥离与前端 `markdownTitle.js` 同规则;译文缓存有效时附 `translated_html`/`translated_title`;播客附 `podcast` 投影 + `cover_image`;社交帖附 `media_images`。可见性复用自 `routers/articles.py` 抽出的 `load_reader_visible_article`(单条详情同函数)。
  - 依赖:pyproject 加 `markdown-it-py>=3.0`,`docker/requirements.txt` 重导出(+markdown-it-py 4.2.0 / mdurl 0.1.2)。
  - 测试 `tests/test_miniprogram_client.py` 9 项全过;全套 851 项中 4 项失败均为个人早报 deadline 时序用例,在 main 干净代码上同样失败(既有问题,与本波无关)。

- **2026-09-04 · 小程序工程骨架落地**(`miniprogram/`,Taro 4.2.1 + React 18,JS/JSX 与 `frontend/` 同语法,`npm run build:weapp` 编译通过,dist 520 KB):
  - 页面:登录门(redirect 回落)/ 条目流 ×4(tabBar,`FeedPage` 参数化:全部|未读 seg、搜索、标读、下拉刷新、触底分页、日期组头、未读点、收藏星)/ 正文页(render 端点 → `rich-text`,原文|中文二段、播客播放器 `InnerAudioContext`、底部工具条 收藏/标未读/分享、`onShareAppMessage` 带 id)/ 源过滤页(聚合入口 + 角色分组 + 长按退订)/ 发现页(源目录 + 形态 seg + 搜索 + 一键订阅)/ 我的(收藏/发现/反馈/改密/关于/退出确认)。
  - 数据层:`api/request.js`(Bearer 挂头、`X-Dorami-Client`、401 回登录门)+ `store/session.js` + `store/reader.js`(源/未读/收藏/各容器过滤器,模块级单例)+ `features/bootstrap.js`(会话引导,60s 节流);`shared/` 复制 `readerTime`/`readerText`/角色判定三份纯函数(改动两侧同步——比 alias 跨目录编译更稳)。
  - 导航:原生导航栏 + 原生 tabBar 5 项(§3.5 建议档),标题按过滤器 `setNavigationBarTitle`;避开自绘导航与右上角胶囊的适配成本。暗色:`darkmode: true` + `theme.json` + `prefers-color-scheme` 令牌翻转。
  - 图片:正文图走签名公开链(客户端拼 origin + 内联限宽 style);头像/推文图/播客封面经 `ProxyImage`(代理 401 → 回退原链 → 占位)。
  - 待真机实测(§7):未在微信开发者工具/真机运行过,仅编译验证。

版本:后端 + 前端改动随 main 版本走(建议 **v3.46.0**,新客户端面属功能波);小程序端自身版本在 `miniprogram/project.config.json` 与微信后台版本管理独立演进,不与 `src/version.py` 绑死,但「关于」页展示后端 `/api/runtime` 的 `version` 以便对账。

## 6. 工程布局

```
miniprogram/                 # Taro 项目根(独立 package.json / node_modules,不进 frontend/)
├── project.config.json      # appid 不入库(占位 + .gitignore 覆盖的 project.private.config.json)
├── src/
│   ├── app.config.js        # pages / tabBar / darkmode / requiredBackgroundModes(JS/JSX,与 frontend 同语法,不用 TS)
│   ├── api/                 # Taro.request 封装 + 拦截器(Bearer 挂头、401 处理)+ 端点函数(命名对齐 frontend/src/api.js)
│   ├── store/               # 精简数据层:源/订阅、未读、收藏、列表分页、正文缓存
│   ├── pages/               # login / feed / article / sources / discover / brief / me
│   ├── components/          # ArticleCard / SocialCard / PodcastPlayer / SegControl / ActionSheet…
│   ├── app.scss             # 自 frontend/src/index.css 的 --dorami-* / --r-* 令牌移植(亮暗成对)+ 通用原语
│   └── shared/              # 自 frontend/src/utils 复制的 DOM 无关纯函数(实施时弃 alias 改复制:跨目录 babel 编译不稳)
└── README.md                # 本地开发:微信开发者工具导入 dist、后端地址配置、测试账号
```

设计纪律沿 `docs/frontend/conventions.md`:令牌化色值/圆角/动效,`.m-*` 移动壳的视觉口径为基准(样页 `docs/design/dorami-mobile-quiet.html`);暗色经 `darkmode: true` + `prefers-color-scheme` 令牌翻转。

## 7. P0 实测清单(P1 动工前必须过)

在 Taro 4 空项目上逐项实测,任一项不过即触发 §3 备选:

- ☐ `Taro.request` 登录取 token → Bearer 挂头拉 `/api/articles?shape=article` 列表,真机(iOS + Android)通
- ☐ `rich-text` 渲染三类代表文章的服务端 HTML:中文长文(qbitai)/ 含围栏代码块与表格(changelog 类)/ 含公式(HF Daily Papers),目检可读
- ☐ 签名图链在 `rich-text` 内 `<img>` 与 `<image>` 组件两处加载成功;篡改 `sig` 得 404
- ☐ 播客 enclosure 直链在 `InnerAudioContext` 能否播放(域名白名单执行口径);后台模式锁屏续播
- ☐ 转发卡片:分享 → 另一账号点开 → 登录 → 落该篇;已登录直落
- ☐ 暗色模式跟随系统;安全区(刘海 / 底部指示条)垫边
- ☐ 主包体积 < 2MB(不分包);列表 200 条滚动无卡顿
- ☐ 共享 `frontend/src/utils` 的白名单模块在小程序运行时无 `window`/`document` 引用报错

## 8. 前置确认(负责人,非代码)

- ☐ **公网部署机 HTTPS 与域名备案状态**:合法域名要求 HTTPS + ICP 备案;当前公网裸机部署(v3.39 路径)是否已开 TLS(`[nginx] enable_ssl` / `[auth] cookie_secure=true`)与备案
- ☐ **小程序主体**:个人 / 企业。个人主体不可用 web-view、类目受限;资讯聚合若选「资讯」类目通常要求新闻信息服务资质,建议按「工具 → 信息查询 / 效率」类目申报,并在版本说明提供**审核测试账号**(登录后可见的内部工具形态)
- ☐ **AppID / AppSecret 保管**:AppID 进 `project.private.config.json`(不入库);AppSecret 仅 P3 推送才需要,届时入 `services/credentials.py` 命名空间
- ☐ §3.5 导航结构(原生 5 Tab vs 自绘)与 §3.7 推送是否列入承诺

## 9. 里程碑

1. **P0(本文)**:方案拍板 + §7 实测清单跑通 + §8 前置确认 → 决定是否进 P1
2. **P1 阅读闭环**:§5 后端三切口(独立 PR,先合)→ 登录 / 四容器条目流 / 正文页 / 收藏标读 / 源过滤,真机可用,内部试用
3. **P2 微信增量**:转发卡片 + 深链落地 + 发现页与合集订阅 + 早报 + 我的(反馈 / 公告 / 改密)→ 提审
4. **P3(可选)**:订阅消息推送;翻译 / 速读按能力位下放;refresh token

PR 拆分沿仓库节奏(feature 分支 + PR + 检视门禁):后端切口一个 PR(含测试),小程序 P1、P2 各一个 PR;小程序目录首个 PR 同时更新 `docs/README.md` 与 `CLAUDE.md` 的项目结构节。

## 10. 风险与 backlog 候选

- 审核类目驳回 / 备案未就绪:非代码风险,§8 前置;不过审时小程序仍可作**体验版**供内部用(体验版无需过审,限成员)
- `rich-text` 正文内链接不可点、公式降级为原文:首版接受,记 backlog(候选:服务端把链接抽成正文末「文中链接」列表;公式渲染服务端转图)
- 7 天会话无静默续期:记 backlog(refresh token 或滑动续签)
- 播客音频域名限制若成立:签名代理需支持 Range 流式透传,带宽经服务器,与 v3.44「不镜像音频」拍板需再对齐
- 社交流图片网格 / 引用推嵌套在小程序的复刻成本被低估:P1 先以单图 + 文本形态上,P2 补齐
- 后端 markdown 渲染新增依赖:`tests/test_docker_requirements.py` 守卫清单一致性,裸机路径按 `docker/requirements.txt` 装(v3.39.1 钉版纪律)
