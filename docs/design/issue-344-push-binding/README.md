# issue #344 推送绑定 · 样页(2026-11)

> 状态:进行中。2026-11-25 由 Claude Code(Fable 5.2)做的第一轮样页,待拍板。方案见 [`docs/plans/issue-344-push-binding.md`](../../plans/issue-344-push-binding.md),issue [#344](https://github.com/zlzfun/DoramiSourceArchive/issues/344)。

要回答的是:管理员在哪里登记一个推送的来源、把它交给哪个令牌,以及怎么一眼看出哪个源有 Agent 在推。

## 怎么看

在仓库根(本 worktree)起一个静态服务,再打开总览页:

```bash
python3 -m http.server 8766 --bind 127.0.0.1
# http://127.0.0.1:8766/docs/design/issue-344-push-binding/index.html
```

字体与品牌图引用 `frontend/public/`,所以要从仓库根起服务;不请求任何外部域名。样式与脚本的地址都带 `?v=`,返修时把三张原型页(由 `tools/build.py` 的 `VERSION` 生成)里的版本号改掉——`http.server` 不发缓存头,浏览器普通刷新不会重新取这些文件。

| 文件 | 内容 |
|---|---|
| `index.html` | 总览:收录令牌 现网 / 样页、令牌的来源抽屉、源管理 现网 / 样页、两种源的检视器、登记来源的三种填法、读者面那一行文字、待拍板、样页的边界 |
| `tokens.html` | 可点原型:运维 → 自定源与收录。`?state=<now\|new>&open=<token>&actor=<调用方>&self=<1>&theme=<light\|dark>[&bare]` |
| `sources.html` / `sources-x.html` | 可点原型:源管理 · 社交,分别选中一个只推送的源、一个又抓又推的源。`?state=<now\|new>&open=<register>&reg=<one>&kind=<social\|article\|podcast>&theme=…[&bare]` |
| `assets/app.css` | 主干(46cc0c5d)应用样式的原样导出(`document.styleSheets`),只去掉登录页专用的三条 `@font-face` |
| `assets/proto.css` | 新增部分的样式。上半是要落地的(`.push-pill` `.bind-*` `.tok-sources` `.reg-*`,取值只用现有 token),下半 `.px-*` 是样页开关 |
| `assets/proto.js` | 示意数据 + 新增部分的渲染 + 样页开关 |
| `tools/build.py` | 把沙箱抓到的现网 DOM 整理成三张原型页(去掉脚本与隐藏着的其它标签页) |
| `tools/shots.py` | 自查:逐个状态打开原型截图,收集控制台报错与失败请求 |

原型底部的深色条是样页开关,不属于产品界面。

## 做法

1. **底稿是现网**。在隔离栈(主干 46cc0c5d,空库,自带 `admin/admin`)里签发三个收录令牌、用现有的推送接口推几条虚构账号的帖子与一篇文章,再用 Playwright 抓「运维 → 自定源与收录」与「源管理 · 社交」的 DOM,以及 `document.styleSheets` 导出的样式。原型里没改过的地方就是现网本身。
2. **新增部分沿用现有形制**。令牌表的列与行(`.acct-table`)、抽屉外壳(节点同步页的 `SyncDrawerShell` 标记)、检视器的分节(`.inspector-section` + `.micro-label`)、行上的徽标(`.tier-pill`)、开关(`.ledger-switch`)、分段选择(`.segmented-control`)都照现网标记写。新起名字的只有「推送」徽标(`.push-pill`)、绑定清单(`.bind-list` / `.bind-row` / `.bind-add`)、令牌表的来源格(`.tok-sources`)和登记表单的排布(`.reg-*`)。
3. **数据是示意**。三个令牌与四个收录源是播种出来的虚构账号;「Grok 推荐」这一行、各处的「最近一批」时间与累计条数由 `proto.js` 写上去。
4. **自查**(2026-11-25):`tools/shots.py` 的 15 个状态(令牌表 现网 / 样页 / 抽屉 / 含自助登记 / 暗色,源管理 现网 / 样页 / 暗色 / 又抓又推,登记来源四种,带开关条,总览页)零控制台报错、零失败请求;截图逐张看过。

## 样页里定的做法

- **收录令牌**:表里加「来源」列(名称一行排开,排不下省略;没有绑定显示「–」);点一行开抽屉,列出这个令牌的来源(名称、源 id、最近一批、解绑),下面一行加来源。
- **源管理**:有推送绑定的源,行上的「无采集」换成「推送」;又抓又推的源在原有徽标后面加「推送」。只推送的源最后一列显示最近一批推送的时间与累计条数。检视器多一区「推送」,列出在推它的令牌(调用方、令牌尾号、最近一批、解绑),下面一行加令牌。
- **登记来源**:源管理页头的按钮,开抽屉。类型(推文 / 文章 / 播客)、出处(一个账号或站点 / 不限)、名称、账号或站点或源 id、头像、令牌、进发现页。
- **读者面**:转推卡片顶上那一行取转推者本人的名字。

待拍板的七项见总览页 §5。

## 没有画的

- 签发令牌的同时选来源(接口支持,界面先不加)。
- 未绑定令牌推送被拒收时的回执(见方案 §4)。
- 手机壳(管理面不在手机壳里)。
