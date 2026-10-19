# 播客播放器样页(issue #199)

issue #199 第三项「播客区重设计」的三轮样页。已拍板并落到前端(`frontend/src/components/player/`),
这里是设计记录,不随实现更新;实现以 `frontend/src` 为准,规则见 `docs/frontend/conventions.md`
(§8「全局播放器」、§7「具名例外·播放条形变」)。

## 拍板组合

- **方案 C「随行」**:标题区收听入口(实心「收听原节目 N 分钟」+ 旁边的精品导读入口),点了才出播放条。
- **条位 = 内容区浮层**(`layout=float`):视图轨右缘到窗口右缘、四周留边距,横跨源栏 / 条目列 / 阅读窗;
  换文章、换容器、进出早报 / 发现都不中断;可缩成右下角迷你。
- **配色 = 封面模糊**(`tone=blur`):封面缩成小位图模糊后当底,遮罩浓度按封面逐张算到文字过 4.5:1。
- **正文与音轨解耦**:正文区自带「节目简介 | ✦ 导读稿」切换,与正在播哪条音轨无关;AI 卡常在。

## 文件

| 文件 | 内容 |
|---|---|
| `index.html` + `pane.html` | 第一轮:阅读窗里的四个方案(现状 / A 同框 / B 音轨 / C 随行)× 六个状态 s1–s6(`assets/data.js` 的 `PP.states`),桌面与手机并排 |
| `global.html` | 第二、三轮:全局播放条。条位(pane / dock / float / full)× 配色(neutral / cover / blur / inverse / brand)× 页面(podcast / article / social / discover),外壳可实时切换 |
| `assets/` | `opt-*` 第一轮方案,`global.*` 全局播放条,`app.css` / `app-shell.css` 自前端编译产物抽取的样式(勿手改),`pp-base.css` 画框底座,封面与社交流示意素材 |

## 查看

在仓库根起静态服务(如 `python3 -m http.server 5188`),然后打开:

- 最终组合:`http://127.0.0.1:5188/docs/design/podcast-player/global.html?layout=float&tone=blur`
  (外壳并排显示桌面与手机;可加 `theme=light`、`motion=reduce`、`container=article|social|discover`、`width=1280|1920`,
  或 `device=desktop|mobile` 只看单个画面)
- 第一轮方案 C:`http://127.0.0.1:5188/docs/design/podcast-player/index.html?opt=c`

样页里有测试钩子(`PPG.audit()`)和写死的示意数据,只用来看视觉与动效。
