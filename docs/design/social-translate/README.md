# 社交流翻译样页(issue #84)

社交媒体容器里推文的中文翻译:入口放哪、译文怎么呈现。拍板后落到前端,这里是设计记录,
不随实现更新;实现以 `frontend/src` 为准。

第二轮(用户目检):第一轮 A 把入口放在主推正文下、与「显示更多」同一行,首次使用会误以为只翻主推、不管引用推;
改为卡片最后一行(媒体与引用推之后)的「卡片底栏」,B 增加「常显」变体对照。

## 文件

| 文件 | 内容 |
|---|---|
| `index.html` | 外壳:入口(A 卡片底栏 / B 卡片动作区[悬停 / 常显] / C 流头开关)× 译文呈现(原文下追加 / 替换原文)× 主题 × 配色 × 状态,桌面与手机并排 |
| `frame.html` | 单个画面(`device=desktop|mobile`),结构与类名照抄现网 SocialFlow / ReaderTab / MobileReader / ActionSheet / ContextMenu |
| `assets/app.css` | 前端编译产物(`npm run build` 的 `dist/assets/index-*.css`,基于 #201 合入后的 main),勿手改 |
| `assets/social-tr.css` | **本波提案样式**(`.social-tr-row` / `.social-tr*` / `.social-quote-tr` / 方案 B、C 的补充),实现时并入 `index.css` 推文卡段 |
| `assets/frame.css` | 画框几何(现网靠 App 外层的 `--rail-w` / `fixed` 定位,样页补上),不含方案样式 |
| `assets/data.js` | 7 条本机库真实推文 + 手写示意译文 |
| `assets/frame.js` | 渲染与交互模拟(翻译耗时 1.1 秒,收起再展开读缓存) |

## 查看

在仓库根起静态服务(如 `python3 -m http.server 5191`),打开
`http://127.0.0.1:5191/docs/design/social-translate/index.html`。
参数:`entry=foot|act|head`、`actvis=hover|always`、`show=below|replace`、`state=idle|loading|done|error|quota|menu|aioff`、
`theme=dark|light`、`palette=porcelain|graphite|paper`、`view=both|desktop|mobile`。

## 素材与示意的边界

- 推文取自本机库(2026-04 至 07 月),样页把「现在」定在 2026-07-24;本机库没有中文推文。
- 译文是 Claude 按平台 `TRANSLATE_SYSTEM_PROMPT` 的规则手写的示意,不是平台模型的输出。
- Qoder 的头像上游已 404,按现网 `SocialQuote` 的表现显示灰色圆底。
- 「可译」判定在样页里由数据里是否有译文表示;实现时按 X 的 `lang` 字段加启发式回退判定(见外壳页「不随方案变化的规则」)。
