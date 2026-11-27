你是一位界面设计师兼前端工程师。**不要调用任何工具(不读文件、不写文件、不运行命令、不打开浏览器)**:你需要的一切都写在这条消息里,你的交付物就是这条回复本身。另一个设计者(Claude)会把你回复里的文件原样落盘、在浏览器里打开检查,再和他自己的方案并排放进同一张样页给用户选。用中文回答。

# 任务:「AI 产出」管理页的 3 个发散式布局方向

## 背景

哆啦美是一个自托管的 AI 资讯阅读器。管理台现在有一个「AI 日报」页签,只管一件事:每天定时把归档里的新文章加工成一期「公共日报」。要改的是:**日报不再是唯一的专属流程,而是多个流程之一**。第二个流程是「GitHub Trending 精读」(每天从 GitHub Trending 日榜里挑几个仓库写成中文精读)。以后还会有第三、第四个,但不会很多(预期个位数)。

一个「流程」= 定时读一些源(取材)→ 加工 → 写出一期内容(产出,落在它自己的源里)。流程由代码定义,管理面不能新建流程。页签改名「AI 产出」。

管理员在这里做四件事,按频率从高到低:

1. 看一眼:每个流程今天跑了没有、成功 / 失败 / 跳过、下次什么时候跑。
2. 偶尔手动点一次「立即生成」。
3. 很少:改定时(cron)、开关、各流程自己的旋钮。
4. 偶尔:翻往期的某一期(展开读、删除)。

## 用户对上一轮的意见(原话)

> 你可以更发散一点?例如,当前所有设计中近期精度/近期日报(实际上这两者也是可以统一口径的)都是一个列表,但是实际上我觉得这个列表可以常态隐藏、点击展开 之类?

上一轮的三个搭配都是「名册 + 所选流程」的主从结构:名册放左栏 / 顶部磁贴 / 顶部表格,所选流程排成「配置 ｜ 往期列表」两栏或「取材 → 加工 → 产出」三段;往期列表常驻,占掉大半个页面。用户觉得不够发散。

Claude 这一轮已经在做的三个想法,**请避开,做别的**:

- 泳道:所有流程同屏,一行一个流程,列头是 取材 → 加工 → 产出,点开一行才出现细节。
- 台账:流程 × 日期的格子矩阵(一格一天),选中一格下面显示那一期的内容,配置收进右侧栏。
- 在上一轮的三段排法里把往期列表收起。

请给出 **3 个彼此差别大、也与上面这些不同** 的方向。可以重新想「这一页的主角是什么」「往期用什么形态出现」「配置何时出现」「多个流程怎么并置」。不要照搬别的产品的形态。

## 每个方向必须放得下的东西

每个流程都有:名称、产出源 id、上次结果(成功 / 失败 / 跳过 / 运行中)、近 7 日结果、定时 cron(可改)、距下次、开关、立即生成、生成中的阶段进度、失败或跳过的原因。

每个流程自己的:取材、旋钮、往期。往期与配置都不必常驻可见,但要能到达。

| | 公共日报 | GitHub Trending 精读 |
|---|---|---|
| 名称 / 产出源 id | 哆啦美·AI资讯日报 / `dorami_daily_brief` | GitHub Trending 精读 / `dorami_trending_picks` |
| cron / 距下次 | `30 8 * * *` / 16:18:19 | `30 9 * * *` / 17:18:19 |
| 累计 | 41 期 | 12 期 |
| 近 7 日(旧 → 新) | 七天都成功 | 成功 ×4、跳过(11-24)、成功 ×2 |
| 取材 | 「全部来源 / 自定名单」二选一;「增量游标 2026-06-06T21:51:37」+ 重置;一段可展开的「取材与去重口径」(现网的 `<details>` 节点,shell 会交给你) | 固定一个源:GitHub Trending 日榜(最近入库 11-26 07:00) |
| 旋钮 | 每期条数 12(1–50)、入选门槛 6(0–10)、正文保底条数 8(0–50)、论文门槛 7(0–10) | 挑选个数 6(1–10)、只看 AI 相关(开) |
| 加工的阶段 | 收集 · 评分 · 精选 · 点评 · 成稿 | 读榜 · 挑选 · 成稿 |
| 往期的一期 | 日期 + 「收录 33 条 · deepseek-chat」+ 生成时刻(如 08:31);每期有若干条目(标题 / 分类 / 分数 / 来源) | 日期 + 「选 6 个 · 榜单 25 个」+ 生成时刻(如 09:30) |

样页里的「今天」是 2026-11-26,当前时刻 16:11。三种状态(`state` 变量):

- `ok`:都正常。
- `run`:精读正在手动生成,阶段停在「挑选」,附一行「从 25 个仓库里挑 6 个」。
- `bad`:日报今天失败(「LLM 请求超时」),今天那一期不存在,累计 40 期;精读今天跳过(「当日榜单未入库」),累计 11 期。跳过不算失败(琥珀色,不是红色)。

## 用户的审美(违反会被打回)

- **不靠说明文字解释界面,让版面自己讲清楚。** 不要副标题、引导句、口号、解释性文字。含糊的地方改版面,不要加说明。
- 管理面:列头即操作;小图标钮,不要成排的文字大按钮;入口不能只靠悬停才出现。
- 层次用包含(框住、分区)表达,不靠 1px 的字号差;标题不能比它辖区里的正文小。
- 像素级:并排区域的发丝线要对齐,行高不要跳,头部左右要平衡。
- 少层级,不要二级侧栏,不占横向宽度。

## 技术契约(必须严格遵守,否则跑不起来)

页面骨架已经由 Claude 写好,你**只交三样**:`g.css`、`g.js`、`NOTES.md`。`g1.html` / `g2.html` / `g3.html` 三个页面除了 `<html data-g="1|2|3">` 之外完全相同,都按这个顺序加载:

```html
<link rel="stylesheet" href="../assets/app.css">    <!-- 现网应用样式原样导出:全部 token 与组件 -->
<link rel="stylesheet" href="../assets/proto.css">  <!-- Claude 上一轮的小块,可直接用,见下 -->
<link rel="stylesheet" href="assets/g.css">         <!-- 你的 -->
<script src="../assets/data.js"></script>            <!-- window.PF_DATA,见下 -->
<script src="assets/shell.js"></script>              <!-- window.shellReady,见下 -->
<script src="assets/g.js"></script>                  <!-- 你的 -->
```

`g.js` 的入口必须是:

```js
shellReady.then(({ paper, shell, parts, state, theme }) => {
  const dir = document.documentElement.dataset.g; // '1' | '2' | '3'
  // 往 paper 里放你的版面
});
```

- `paper`:一张空的「纸」——`<div class="runs-paper">`,`display:flex`(横向)、`overflow:hidden`、圆角卡片底,铺满页头以下。1440×900 视口下它是 1320×794,1280 宽时是 1160 宽。左边的应用导轨与上方的页头(标题「AI 产出」+ 模型状态胶囊)已经在了,不用管。要纵向排就给它加 `flex-direction: column`;内部要滚动的区域自己设 `overflow-y:auto` 与 `min-height:0`。
- `shell`:包着页头与纸的 `.runs-shell`(一般用不到)。
- `parts.values`:`{ cron: '30 8 * * *', topN: '12', minScore: '6', minItems: '8', paperMin: '7' }`。
- `parts.cursor`:`'2026-06-06T21:51:37'`。
- `parts.scope`:现网的 `<details class="scope-note">`(「取材与去重口径」)DOM 节点,只有一个,插到你要的位置。
- `parts.rows`:现网往期行的 10 个真实 DOM 节点,结构是 `div.brief-run > div.brief-run-line > button.brief-run-head(svg.brief-run-caret + span.brief-run-date + span.brief-run-meta + span.stamp.stamp-ok「已生成」) + button.brief-run-del`。要用往期行就 `cloneNode(true)` 再改字。
- `parts.pager`:现网分页条节点(`div.brief-pager-row`)。
- `parts.playIcon` / `parts.spinIcon`:播放与转圈图标的 HTML 字符串(svg,class 里已带尺寸)。
- `state`:`'ok' | 'run' | 'bad'`;`theme`:`'light' | 'dark'`(`<html data-theme>` 已设好)。
- 地址带 `&bare` 时 `<html>` 上有 `data-bare` 属性;你的样页开关条必须在这时隐藏(用 `.px-bar` 就自动隐藏)。

`window.PF_DATA`(真实数据,直接用):

```js
PF_DATA.briefPeriods  // 日报 41 期,新 → 旧:[{ date: '2026-11-26', time: '08:31', count: 33, model: 'deepseek-chat',
                      //   items: [{ title: 'Claude Fable 5、GPT-5.6 Sol、Kimi K3 三大AI包揽IMO 2026满分…', kind: '模型发布', score: 9.0, source: '新智元' }, …最多 20 条] }, …]
                      // 10-18 到 11-26 每天一期,另有一期 10-10
PF_DATA.trendingRepos // 一期榜单的 10 个仓库:[{ repo: 'block/buzz', lang: 'Rust', stars: 6744, today: '2,460' }, …]
```

精读的往期没有真实数据:11-14 到 11-26 每天一期、11-24 缺(跳过),自己生成;精读某一期的内容用仓库名加灰色占位(「一句中文说明;为什么值得看」),不要代写正文。

**样式只能用现网已有的变量**,不要写十六进制颜色,不要新造字号(现有刻度:12 / 12.5 / 13 / 14 / 15 / 24 px):

- 文字:`--dorami-ink`(最深)/ `--dorami-ink-2` / `--dorami-muted` / `--dorami-faint`(最浅)
- 面与线:`--dorami-border`(发丝线)/ `--dorami-border-strong` / `--dorami-soft`(浅底、悬停)/ `--dorami-well`(凹槽底)/ `--dorami-card` / `--dorami-surface` / `--dorami-select`(选中底)/ `--dorami-blue`(主色)/ `--dorami-wash` + `--dorami-accent-ink`
- 状态:`--state-ok` / `--state-bad` / `--state-warn` / `--state-run`,各有 `-bg` 版本(如 `--state-ok-bg`)
- 圆角:`--r-sm` / `--r-control` / `--r-card` / `--r-pill`;等宽字体 `--mono`;过渡 `var(--motion-fast) var(--motion-ease)`

可以直接用的现网组件(类名与结构照抄):

- 名册的一行三层(约 85px 高):`div.tt-item > button.tt-row > div.tt-row-top(span.tt-name + span.tt-last.ok|bad|warn|run) + div.tt-row-mid(文字 + span.tt-cron) + div.tt-row-bot(span.tt-dots > i.ok|bad|warn ×7 + span.tt-next > span.u「距下次」+ 时间)`。`.tt-last` 是 6px 的结果记号(ok 绿方块 / bad 红菱形 / warn 琥珀三角 / run 蓝色脉动圆点);`.tt-dots i` 不带类是灰色空位。
- 表格:`table.flow-table > thead th.flow-th`(12px、加字距、faint;数字列加 `.is-num`)+ `tbody tr.run-row`(44px 高);单元格里 `div.run-obj > div.run-obj-txt > div.run-obj-name`(13/600)+ `div.run-obj-sub`(等宽 12 faint)、`span.run-n`(等宽数字)。
- 参数行(约 46px 高,行间发丝线):`div.inspector-params > div.inspector-param > label.inspector-param-label + input.inspector-param-input[type=number]`(84px 宽的凹槽输入)。
- 阶段条:`div.ledger-pipeline > span.ledger-pipeline-step`(加 `.is-done` 已完成 / `.is-now` 进行中 / `.is-now.is-err` 出错);它下面的一行说明 `div.pipeline-note`(加 `.is-err` 红 / `.is-warn` 琥珀)。
- 徽标:`span.stamp.stamp-ok|stamp-warn|stamp-bad|stamp-run`;空心中性 `span.stamp.is-hollow`。
- 开关:`button.ledger-switch`(开着加 `.is-on`),34×19。
- 分段:`div.mini-seg > button.mini-seg-btn`(选中加 `.is-on`);要撑满宽度给外层加 `.is-fluid`。
- 按钮:主 `button.action-button.action-button-primary.min-h-[32px].px-3.text-xs`;次 `button.action-button.action-button-secondary.min-h-[32px].px-3.text-xs`;图标钮 `button.icon-button`(默认 38px 方;加 Claude 的 `.pf-icon` 变成 30px、图标 14px)。
- 输入:`input.form-input`;小胶囊按钮 `button.inspector-metabtn`(30px 高、浅底)。
- 分页:`div.pager > button.pager-btn`(当前页加 `.is-on`)。
- 可展开说明:`details.scope-note > summary + p`。
- 微型文字:`.tiny-meta`(12px 灰)、`.micro-label`(12/600)、`.tools-title`(13/600)。

`proto.css` 里可直接用的(Claude 的):`.pf-cron`(加在 `input.form-input.font-mono` 上:108px 宽、30px 高、居中的 cron 输入)、`.pf-icon`、`.pf-when`(等宽 12 faint 的时刻)、`.pf-save`(上方带发丝线的保存区)、`.px-bar`(样页开关条的整套样式:`div.px-bar > span.px-grp > span.px-lbl + a(.is-on)`;固定在底部居中的深色条,`data-bare` 时自动隐藏)。

你新起的类一律用 `.gx-` 前缀(现网里没有这个前缀)。**不要用 `.pl-`、`.pm-`、`.pf-`、`.lane-` 开头的新类名**(前者与现网撞名,后三个是 Claude 的)。

每个方向在页面底部用 `.px-bar` 放一条样页开关:至少能切 `state`(链接到 `?state=ok|run|bad`,保留其它参数)与 `theme`(`?theme=light|dark`);你的方向有「展开 / 选中」之类的初始状态时,也做成地址参数放进这条开关里,这样 Claude 能给每个关键状态截图。

## 交付格式(严格照此,方便程序切分)

回复里依次给出三段,每段以单独一行的标记开头,后面紧跟文件的完整内容,**不要用 Markdown 代码围栏包文件内容**,不要省略号,不要「此处同上」:

```
=== FILE: g.css ===
(完整内容)
=== FILE: g.js ===
(完整内容)
=== FILE: NOTES.md ===
(完整内容)
=== END ===
```

`NOTES.md` 写:每个方向一小节——名字(两到四个字)、想法(两三句)、往期与配置分别藏在哪 / 怎么到达、新起的类名、没做的部分;最后一节写你认为三个里哪个最好、为什么。

质量要求:`g.js` 用原生 JS,不依赖任何库;三个方向都要完整可用(三种状态都要有对应的呈现);1440 与 1280 宽都不能出现横向滚动;暗色主题靠变量自动成立。先在脑中把每个方向的版面画清楚再写代码,宁可少而精。
