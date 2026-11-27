# 任务书:issue #72「AI 产出」管理页的发散式布局尝试

你和另一个设计者(Claude)同时在为同一个页面出方案,最后两边的结果会并排放进同一张样页给用户选。请你独立做 **3 个彼此差别大的布局方向**,每个做成一张可以直接打开的静态原型页。

## 背景

哆啦美是一个自托管的 AI 资讯阅读器。管理台现在有一个「AI 日报」页签,只管一件事:每天定时把归档里的新文章加工成一期「公共日报」。要改的是:**日报不再是唯一的专属流程,而是多个流程之一**。第二个流程是「GitHub Trending 精读」(每天从 GitHub Trending 日榜里挑几个仓库写成中文精读)。以后还会有第三、第四个,但不会很多(预期个位数)。

一个「流程」= 定时读一些源(取材)→ 加工 → 写出一期内容(产出,落在它自己的源里,读者可以订阅)。流程由代码定义,管理面不能新建流程。

这个页签改名「AI 产出」。管理员在这里做四件事,按频率从高到低:

1. 看一眼:每个流程今天跑了没有、成功 / 失败 / 跳过、下次什么时候跑。
2. 偶尔手动点一次「立即生成」。
3. 很少:改定时(cron)、开关、各流程自己的旋钮。
4. 偶尔:翻往期的某一期(展开读正文、删除)。

## 用户对上一轮的意见(原话)

> 你可以更发散一点?例如,当前所有设计中近期精度/近期日报(实际上这两者也是可以统一口径的)都是一个列表,但是实际上我觉得这个列表可以常态隐藏、点击展开 之类?

上一轮的三个搭配(左栏名册 + 两栏、顶部磁贴 + 取材 → 加工 → 产出 三段、顶部表格 + 两栏)都是「名册 + 所选流程」的主从结构,往期列表常驻占掉大半个页面。用户觉得不够发散。先看一眼上一轮:<http://127.0.0.1:8772/docs/design/issue-72-product-registry/index.html>(静态服务已经在跑,不要再起)。

Claude 这一轮在做的想法(请避开,做别的):**泳道**(所有流程同屏,每个流程一条「取材 → 加工 → 产出」)、**往期日历带**(用一排日期格子代替列表)、**以最新一期的内容预览为主**。

## 每个方向必须放得下的东西

每个流程都有(外壳,所有流程一样):名称、产出源 id、上次结果(成功 / 失败 / 跳过 / 运行中)、近 7 日结果、定时 cron(可改)、距下次、开关、立即生成、生成中的阶段进度、失败或跳过的原因。

每个流程自己的:取材、旋钮、往期。往期不必常驻可见,但要能到达(展开读正文、删除、翻页)。

| | 公共日报 | GitHub Trending 精读 |
|---|---|---|
| 名称 / 产出源 id | 哆啦美·AI资讯日报 / `dorami_daily_brief` | GitHub Trending 精读 / `dorami_trending_picks` |
| cron / 距下次 | `30 8 * * *` / 16:18:19 | `30 9 * * *` / 17:18:19 |
| 累计 | 41 期 | 12 期 |
| 近 7 日(旧 → 新) | 七天都成功 | 成功 ×4、跳过(11-24)、成功 ×2 |
| 取材 | 「全部来源 / 自定名单」二选一;另有「增量游标 2026-06-06T21:51:37」+ 重置;一段可展开的「取材与去重口径」说明(现网已有,原样保留) | 固定一个源:GitHub Trending 日榜(最近入库 11-26 07:00) |
| 旋钮 | 每期条数 12(1–50)、入选门槛 6(0–10)、正文保底条数 8(0–50)、论文门槛 7(0–10) | 挑选个数 6(1–10)、只看 AI 相关(开) |
| 加工的阶段 | 收集 · 评分 · 精选 · 点评 · 成稿 | 读榜 · 挑选 · 成稿 |
| 往期的一行 | 日期 + 「收录 33 条 · deepseek-chat」+ 生成时刻(如 08:33) | 日期 + 「选 6 个 · 榜单 25 个」+ 生成时刻(如 09:30) |

三种状态(用地址参数 `?state=` 切换):

- `ok`:都正常。
- `run`:精读正在手动生成,阶段停在「挑选」,附一行「从 25 个仓库里挑 6 个」。
- `bad`:日报今天失败(「LLM 请求超时」),今天那一期不存在,累计 40 期;精读今天跳过(「当日榜单未入库」),累计 11 期。跳过不算失败(琥珀色,不是红色)。

## 用户的审美(违反会被打回)

- **不靠说明文字解释界面,让版面自己讲清楚。** 不要副标题、引导句、口号、空态之外的解释性文字。含糊的地方改版面,不要加说明。
- 管理面:列头即操作;小图标钮,不要成排的文字大按钮;高危操作收进抽屉;入口不能只靠悬停才出现。
- 层次用包含(框住、分区)表达,不靠 1px 的字号差;标题不能比它辖区里的正文小。并排的几个小标题不要看起来同级又各说各的。
- 像素级:并排区域的发丝线要对齐,行高不要跳,头部左右要平衡。
- 少层级,不要二级侧栏,不占横向宽度。
- 不要照搬别的产品的形态。

## 技术约束

- 工作目录:`/Users/zhuliuzi/Codes/worktrees/DoramiSourceArchive/issue-72-product-registry/docs/design/issue-72-product-registry/`
- **只在 `gemini/` 这个子目录里写文件。** 不要改这个子目录之外的任何文件(`index.html`、`app.html`、`assets/`、`r1/`、`tools/` 都是 Claude 的)。不要运行任何 git 命令。不要起服务、不要装依赖、不要请求任何外部域名。
- 交付物:
  - `gemini/g1.html`、`gemini/g2.html`、`gemini/g3.html`:三个方向,各一张。1440×900 的桌面视口为准,1280 宽也不能坏(不出现横向滚动)。
  - `gemini/assets/g.css`、`gemini/assets/g.js`(或每个方向各一份,随你):你新增的样式与脚本。
  - `gemini/NOTES.md`:每个方向一小节——想法(两三句)、往期与配置分别藏在哪 / 怎么到达、你新起的类名、没做的部分。再加一节「自查」写你怎么看过。
- **底页用现网外壳。** 已经给你备好 `gemini/assets/shell.js`(先读它的头部注释):它把现网的导轨、页头、应用样式载进来,交给你一张空的「纸」和现网日报的真实节点(10 行往期、分页条、取材口径、各项取值、图标)。页面骨架照下面写:

```html
<!doctype html>
<html lang="zh-CN" data-theme="light" data-palette="porcelain">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 产出 · G1</title>
<link rel="stylesheet" href="/frontend/public/fonts/dorami-serif-v1/dorami-serif.css">
<link rel="stylesheet" href="/frontend/public/fonts/dorami-latin-v1/dorami-latin.css">
<link rel="stylesheet" href="../assets/app.css?v=2">
<link rel="stylesheet" href="assets/g.css?v=1">
</head>
<body>
<script src="assets/shell.js?v=1"></script>
<script src="assets/g.js?v=1"></script>
</body>
</html>
```

- **样式只用现网已有的 token 与组件。** `../assets/app.css` 是现网应用样式的原样导出(630KB,别整个读,按类名搜)。颜色、圆角、阴影、字体一律用变量:`--dorami-ink` / `--dorami-ink-2` / `--dorami-muted` / `--dorami-faint` / `--dorami-border` / `--dorami-soft` / `--dorami-well` / `--dorami-card` / `--dorami-surface` / `--dorami-select` / `--dorami-blue` / `--dorami-accent-ink` / `--dorami-wash`、`--state-ok` / `--state-bad` / `--state-warn` / `--state-run`(各有 `-bg`)、`--r-sm` / `--r-control` / `--r-card` / `--r-pill`、`--mono`、`--motion-fast` / `--motion-ease`。**不要写十六进制颜色,不要新造字号**(现有刻度:12 / 12.5 / 13 / 14 / 15 / 24)。暗色主题靠这些变量自动成立,做完要用 `?theme=dark` 看一眼。
- 可以直接用的现网组件(类名):
  - 名册一行三层:`.tt-item > button.tt-row > .tt-row-top(.tt-name + .tt-last.ok|bad|warn|run) / .tt-row-mid(+ .tt-cron) / .tt-row-bot(.tt-dots > i.ok|bad|warn + .tt-next > .u)`
  - 表格:`table.flow-table` + `th.flow-th` + `tr.run-row`(44px 行)、`.run-obj` / `.run-obj-name` / `.run-obj-sub` / `.run-n`
  - 参数行:`.inspector-params > .inspector-param > label.inspector-param-label + input.inspector-param-input`
  - 阶段条:`.ledger-pipeline > span.ledger-pipeline-step(.is-done / .is-now / .is-err)`;说明行 `.pipeline-note(.is-err / .is-warn)`
  - 徽标:`.stamp.stamp-ok|stamp-warn|stamp-bad|stamp-run`、空心 `.stamp.is-hollow`
  - 控件:开关 `button.ledger-switch(.is-on)`、分段 `.mini-seg > button.mini-seg-btn(.is-on)`、主按钮 `button.action-button.action-button-primary.min-h-[32px].px-3.text-xs`、次按钮 `.action-button-secondary`、图标钮 `button.icon-button`、输入 `input.form-input`(等宽加 `.font-mono`)、小胶囊 `button.inspector-metabtn`
  - 往期行:`.brief-run`(shell.js 交给你的真实节点;精读的行克隆它再改字)、分页 `.pager > .pager-btn(.is-on)`
- 参考实现:`assets/proto.css`、`assets/proto.js`(Claude 上一轮的写法,`.pf-*` 是他新起的类;你可以读,可以借用其中的做法,类名自己另起前缀,比如 `.gx-*`)。
- 每张原型页底部可以放一条你自己的样页开关(切状态 / 主题),要与产品界面明显区分,并且在地址带 `&bare` 时隐藏。
- 界面文字用中文。

## 自查(交付前)

用浏览器打开 `http://127.0.0.1:8772/docs/design/issue-72-product-registry/gemini/g1.html`(以及 g2、g3),逐个看 `?state=ok|run|bad` 与 `?theme=dark`:控制台无报错、没有失败的请求、1440 与 1280 宽都没有横向滚动、纸不超出视口底部。把看到的问题修掉,把修不了的写进 NOTES.md。

做完后在对话里回一句「完成」并列出你写的文件。
