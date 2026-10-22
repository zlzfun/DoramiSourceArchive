# 游客模式样页(issue #213)

收到站内链接的人不登录也能先逛;需要账号的操作再提示登录。拍板后落到前端,这里是设计记录,不随实现更新;
实现以 `frontend/src` 为准。方案与待拍板项见 [`docs/archive/issue-213-guest-mode.md`](../../archive/issue-213-guest-mode.md)。

## 文件

| 文件 | 内容 |
|---|---|
| `index.html` | 外壳:九个场景(深链直达 / 登录页 / 登录提示 / 游客版早报 / 发现页 / 分享 / AI 试用 / 设置与我的 / 运维游客区)× 子选项 × 主题 × 配色,桌面与手机并排;右侧列已拍板事项 |
| `frame.html` | 单个画面,底稿是现网 DOM 快照 |
| `assets/snap/` | 2026-10-22 在本机库沙箱里以读者 / 管理员身份抓取的现网 DOM(脚本已剥离、资源路径改相对),勿手改 |
| `assets/app.css` | 同版前端编译产物(`npm run build`,v3.66.0 之后的 main),勿手改;登录页字形子集两枚 woff2 随附 |
| `assets/guest.css` | **本波提案样式**,实现时并入 `frontend/src/index.css` |
| `assets/frame.css` | 画框几何与静态快照修正(如登录页开场直接落终态),不含方案样式 |
| `assets/frame.js` | 场景装配、游客态改写与交互模拟 |

## 查看

在仓库根起静态服务(如 `python3 -m http.server 5192`),打开
`http://127.0.0.1:5192/docs/design/guest-mode/index.html`。
画面参数:`scene=deeplink|login|prompt|brief|discover|share|ai|settings|admin`、`device=desktop|mobile`、
`action=favorite|favlist|subscribe|unsub|interest|addsrc|publink|feedback|ai`(prompt)、`from=guest`(login)、
`toast=on|off`(deeplink)、`view=menu|page`(share)、`drawer=open`(admin)、`theme=light|dark`、`palette=porcelain|graphite|paper`。

画面可以直接点:收藏星、只看收藏、「兴趣」轴、订阅 / 退订、添加文章源、反馈都会弹登录提示;提示里与轨底的「登录」切到登录页
(带「返回继续逛逛」),登录页的「先逛逛」落到游客版早报;运维游客区的开关可拨、齿轮开抽屉。

## 素材与示意的边界

- 底稿是现网真实结构与同版编译样式;本波新增的只有 `assets/guest.css` 与 `assets/frame.js` 里 `guest-` 前缀的片段。
  登录提示照抄现网确认框,早报说明行沿用 `.bp-notice`,运维开关行沿用 `.ai-switchboard`,抽屉沿用 `.ledger-drawer`。
- 本机库是 9 月的数据,最近没有过线稿:早报用 9 月 16 日那一版当「今天」展示。
- 运维游客区的数字是示意(本机没有游客数据),口径见方案 §3.8。
- 登录页背景的星座动画不重放,开场直接落终态;另一条线正在做登录页重设计,本波只在登录卡上加「先逛逛」「返回继续逛逛」两个入口。
- 目检后的两处改动(见方案 §0 第三批):登录页游客入口改为登录按钮下方一行小字「游客登录」;总闸开时分享菜单只留站内链接。样页保留拍板时的原样,不重绘。
- 本机测试账户名已换成示意名。
- 手机发现页卡片右侧被裁是现网既有问题(发现页网格 `1fr` 的最小值被卡片内容撑破),与本波无关,另行处理。
