# 设置 · 聚合接口列全令牌可调的接口(issue #315)

> 状态:进行中。2026-11-20 立项并出第一轮样页,待用户拍板;拍板前不动产品代码。
> 样页:[`docs/design/issue-315-feed-endpoints/`](../design/issue-315-feed-endpoints/README.md)。实现方 Claude Code(Fable 5.2);检视方待定(按流程交 Codex)。

## 1. 来由

issue #312 目检时用户指出:issue #310 新增的「用令牌拉热点事件榜」在设置 → 聚合接口没有明示,普通读者没法知道系统里现在有哪些接口可调。查下来属实,而且不止这一条。

## 2. 现状

个人聚合令牌(`dfeed_`)能调 5 个 HTTP 接口,设置页列了 2 个:

| 接口 | 设置页 | 范围 | 一次最多 |
|---|---|---|---|
| `GET /api/public/feed/articles` | 列了 | 已订阅来源(管理员:全站公开内容) | 500 条 |
| `GET /api/public/feed/articles.md` | 列了 | 同上 | 200 条 |
| `GET /api/public/feed/articles.xml`(Atom) | 没列(v3.20 起就有) | 同上 | 200 条 |
| `GET /api/public/feed/events` | 没列(issue #310) | 全站公开内容 | 10 件 |
| `GET /api/public/feed/events/{event_id}` | 没列(issue #310) | 全站公开内容 | 1 件 |

- 设置页之外读者没有别的途径:应用里没有指向用户手册的链接;自动生成的接口文档页在容器部署下到不了(nginx 只转发 `/api/` 与 `/mcp`)。
- 事件榜是 issue #310 有意延后的(卡头的范围签写「已订阅来源」,事件榜是全站口径,要重想范围怎么表达);Atom 是一直漏着。这张卡靠人手维护,没有东西保证它和后端一致。
- 顺带查到:现网「接口参数」写「limit 上限 500」,只对 JSON 成立,Markdown 上限 200 且不认 `include_content`;Atom 接口在 `docs/contracts/reader_subscription.md` 与 `docs/reference/endpoints.md` 里也没有记。
- MCP 分区的 5 个工具是列全的;单订阅令牌(`dsub_`)没有任何界面。

## 3. 待拍板(样页总览页同一张表;首项为推荐)

1. 方向:**A 分组列全**(一行一个接口,点开看用法)/ B 一组一行(行尾分段切换)。
2. A 打开时:**全收起,五行一屏看全** / 第一行点开(同现网默认露着示例,正文要多滚约 150px)。
3. A 行尾标返回格式:**标** / 不标。
4. 卡头加一条通往「MCP 接入」的链接:**加** / 不加。
5. Atom 的示例:**地址里带令牌**(RSS 工具带不了请求头)/ 同其它接口写 curl。
6. 单订阅令牌的接口:**不列** / 一起列。

## 4. 做法(初步,拍板后定稿)

- **清单单点**:接口清单(路径、组、范围、返回格式、示例、参数)放一个前端模块,`FeedTokenSection.jsx` 照它渲染;新样式进 `frontend/src/index.css`,取值只用现有 token。
- **守卫**:一条前端测试把清单与后端 `/api/public/feed/` 下的路由逐条对账(扫 `src/api/routers/*.py` 的路由声明),任一边多出来都红。
- **文档**:用户手册 `docs-site/features/integrations.md`(页面变了,截图与步骤跟着改);契约与端点清单补 Atom;波次史、待办、决策。
- **不改后端行为**:不动任何接口的请求与返回。

依赖:`FeedTokenSection.jsx` 与 issue #312(PR #316)改的是同一个文件,等它合入后再从主干开工实现。

## 5. 部署顺序与混版本

纯前端 + 测试 + 文档;不改接口、不动库、不进 Archive Sync。外网、内网先后升级互不影响。

## 6. 本波不做

- 设置卡逐接口的调用次数(issue #312 样页设置卡 B,当时未选)。
- 应用内放用户手册或接口文档的入口。
- 单订阅令牌的界面(待拍板第 6 项的推荐)。
