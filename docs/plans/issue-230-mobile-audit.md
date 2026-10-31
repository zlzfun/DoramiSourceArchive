# 手机端审计清单(issue #230 阶段 1 · A)

> 状态:进行中(2026-10-27 审计产出,待阶段 2 拍板取舍)。父方案 [`issue-230-mobile-wave.md`](./issue-230-mobile-wave.md)。
> 审计方:Claude Opus 5.5 子代理(只审不修,未改 `src/` / `frontend/src/`)。基线:`feat/issue-230-mobile-wave` @ `6f825e7`(v3.67.1 之上的开工提交,3.68.0-alpha)。

## 方法与复跑

- **隔离栈**:真实库副本(`data/cms_data.db` 09-17 开发副本 `.backup` + `alembic upgrade heads`)+ uvicorn 8093 + vite 5174→5177(避开并行波次端口)+ 生产构建预览 5178;临时 ini 在 scratchpad,`[media] enabled=false`(外链图因外站防盗链显示「图片加载失败」,属环境,不计缺陷);副本库里的 LLM 地址改成死地址(不花钱;翻译任务因此停在「进行中」,正好用来看 #225 / #218 的逐段骨架)。读者账号 = 副本里的 `user`(45 个订阅、有 9 月往期早报;只在副本里重置了密码、打开了账户 AI 开关),游客走登录页「游客登录」。播客单集为沙箱现抓(晚点聊 / 硅谷101 / Latent Space 各 5 期),音频请求被拦成本地静音 WAV。
- **视口**:375×667(iPhone SE,DSF 2)、390×844(DSF 3)、360×780 / 412×915(Android UA)、820×1180(iPad 竖屏);全部 `is_mobile + has_touch`;亮 / 暗两主题;读者 / 游客 / 未登录三种身份。另做 1180×820 / 1024 / 1025 / 768 断点两侧、844×390 横屏、Playwright WebKit 26.4(近似 Safari 排版)。
- **量化**:每个页面状态跑一遍 `metrics.js`(横向溢出、触控目标——含 `::after` 扩区的命中测试、<12px 文本、输入框字号、固定层、`vh` 实际命中、未守卫 `:hover` 实际命中、在跑动画、WCAG 对比度、暗色亮块);另有长按 / 返回键 / 软键盘 / 安全区(CDP `Emulation.setSafeAreaInsetsOverride`)/ 减少动效 / 跨版式 / 首屏网络 / 长列表专项。
- **规模**:逐页截图 754 张 + 指标 JSON 754 份(其中 745 份带量化)+ 专项截图 54 张;300 个脚本步骤 0 失败;Playwright 全程 `pageerror` 为 0(console 只有外站图片断连与发现页源图标 404,与业务无关)。
- **产物**(`tmp/` 在 `.gitignore`,不入库):截图 `tmp/issue-230/audit/shots/`、指标 `tmp/issue-230/audit/data/`、对照拼图 `tmp/issue-230/audit/sheets/`。截图命名 `<身份>__<视口>__<主题>__<页面状态>.png`,专项为 `special__*.png`。
- **复跑**(先按 `docs/dev/sandbox-acceptance.md` 起栈,改脚本顶部端口常量):
  ```bash
  .venv/bin/python tmp/issue-230/audit/static_css_audit.py           # 静态:@media / vh / hover / 触控声明 / 字号
  .venv/bin/python tmp/issue-230/audit/walk.py --viewports 390x844 --themes light,dark --roles user,guest,anon
  .venv/bin/python tmp/issue-230/audit/special.py --only backkey,keyboard,safearea,motion,tablet,webkit,netload,longlist
  .venv/bin/python tmp/issue-230/audit/aggregate.py                  # 汇总成 data/aggregate.json
  .venv/bin/python tmp/issue-230/audit/build_audit.py <dist 目录>      # 需 vite build --manifest --sourcemap
  ```

---

## §0 总览

### 计数(严重度 × 六个面)

P1 = 坏了(核心动作在主流环境下不可用);P2 = 明显(可用但吃力 / 有误操作风险 / 明显违背规范);P3 = 打磨。

| 面 | P1 | P2 | P3 | 小计 |
|---|---|---|---|---|
| ① 断点与视口 | 0 | 3 | 3 | 6 |
| ② 触控与可达性(含手势 / 返回键 / 键盘 / 安全区) | 1 | 6 | 4 | 11 |
| ③ 桌面组件逐页走查 | 0 | 5 | 14 | 19 |
| ④ 功能缺口 | 0 | 1 | 3 | 4 |
| ⑤ 性能与体感 | 0 | 1 | 3 | 4 |
| ⑥ 验证基线 | 0 | 2 | 0 | 2 |
| **合计** | **1** | **18** | **27** | **46** |

真机待验:20 项(§6)。

### 最该先修的 10 条

1. **M-01 长按动作单松手即关**(P1):文章行、源抽屉行长按 → 450 ms 弹出动作单 → 松手的合成 click 落在新盖上的遮罩 `.m-dim` → 立即关闭。四档手机视口 × 亮暗 16/16 复现;列表与源抽屉里「不进正文就能做」的标读 / 收藏 / 复制链接 / 该源全部已读 / 退订此源因此失效(进正文后顶栏「更多」点按打开的同一张动作单不受影响)。
2. **M-05 返回键握手缺口**(P2):顶栏搜索、往期月历、分享浮层、全部 `Modal`(安装指引 / 退出确认 / 登录提示 / 添加自定源)、游客叠层登录、图片灯箱都没接 `useLayerHistory`——按返回要么直接退出当前文档(月历 / 搜索 / Modal;会话第一屏即离站),要么把下面的正文页关掉(分享浮层、登录提示、叠层登录)。
3. **M-03 发现页「✓ 已订阅」触屏一点即退订**(P2):桌面靠悬停翻出红色「取消订阅」预警,触屏没有这一拍;自定源走「移除」(退订 + 无人订阅即清配置与文章),无确认、无撤销。
4. **M-02 列表行收藏星触屏不可达**(P2):`.reader-entry-fav` 默认 `opacity:0; pointer-events:none`,只有 `:hover` 浮出——手机上列表里收藏不了(列表层的替代入口正是坏掉的长按,见 M-01;只能进正文再收藏)。
5. **M-06 iOS 聚焦缩放**(P2):发现页 / 兴趣搜索 12.5px、账户改密 3 个输入 13px、反馈 textarea 13px、添加自定源地址框 13px、安装指引平台 select 12px——iOS 聚焦即整页放大。
6. **M-07 触控目标系统性偏小**(P2):81 类可点元素有效命中 < 44px,其中 44 类 < 32px;全部顶栏图标钮 36×36、「全部|未读」40×22、发现页视图分段 28×24、早报「重新编排 / 回到今天」16×28、OPML 勾选框 16×16(行不可点)。
7. **M-09 设置分区窄屏不换行**(P2):`.sett-row` 控件列 `flex:none`,说明列被挤到每行 2–4 字(375 下外观「中性色」说明折成 10 行)。
8. **M-11 公告横幅盖住顶栏 8 秒**(P2):两条公告约 150px 高,悬浮在顶栏与早报眉头之上,入口后头 8 秒订阅源 / 搜索 / 往期 / 重编都点不到。
9. **M-14 首屏 JS 带着图表与公式库**(P2):入口静态依赖 `vendor-charts`(recharts,378 KB / gzip 112 KB)与 `vendor-markdown`(含 KaTeX,418 KB / gzip 128 KB),登录页与手机首屏都预加载——限速 1.6 Mbps 下登录页 354 KB 脚本里 232 KB(66%)用不到,读者首屏 LCP 4.4 s;阅读器路由首屏 JS 合计 1477 KB(raw)。
10. **M-12 横屏无左右安全区**(P2):移动壳只垫 top / bottom,844×390 横屏左右各 47px 刘海区里压着汉堡钮、标题、条目与 TabBar;横屏下播放条 + TabBar + 顶栏 + 类型带合计约 250px,内容区只剩一行。

### 建议先拍板的结构性问题

1. **平板档口径**(M-08 / M-15):768–1024 竖屏现在吃「拉宽的手机壳」(行宽 ~780px、6 个 Tab 各 136px),1025+ 横屏吃「无触屏适配的桌面壳」。选项:a) 平板竖屏给手机壳加版心(640–720)居中;b) 平板单独两栏;c) 维持现状只修触屏可达。决定后 `useCompactLayout` 与命名断点一起定。
2. **移动壳的层与返回键模型**(M-01 / M-05 / M-34):把「层」收敛成一个原语(sheet / drawer / page / popover 四种),统一负责 `useLayerHistory` 握手、遮罩「按下与松开同在遮罩」判定、焦点管理与 Esc;`Modal`、分享浮层、往期月历、灯箱都改走它。这是 M-01 的根治方案,也决定问答 bottom-sheet(M-17)怎么落。
3. **触屏的「悬停语义」怎么替代**(M-02 / M-03 / M-29):全站 259 条 `:hover` 里 248 条无 `(hover: hover)` 守卫,63 条实际命中手机页面;其中有 3 类是「悬停才出现 / 悬停才改文案」的功能性入口(列表星标、已订阅翻退订、桌面横屏的源栏减号等)。需要定一条规则:触屏一律常显 / 改走动作单 / 改两击确认,三选一按场景。
4. **命名断点与移动 token**(M-31 / M-38):`@media` 宽度 10 个取值 + 容器查询 5 个取值 + JS 1024 各说各话(含 1023 / 1024 双口径),移动壳页面实际在用 22 档字号;规范草案(§1 末)需要先拍板档位,再动码。
5. **问答 / 翻译在手机上的形态**(M-17 / M-19):`ReaderAiPanel` 没有移动版;译钮是顶栏第 4 个无文字图标。问答做 bottom-sheet 还是整页、译钮留顶栏还是下沉到标题区尾行,决定本波功能缺口的工作量。

---

## §1 断点清单

### 1.1 全部 `@media` / `@container` / JS 断点(静态扫描 `frontend/src/index.css` + `frontend/src/mobile.css` + JS)

| 位置 | 条件 | 规则数 | 作用面 / 用途 | 手机壳命中 | 建议 |
|---|---|---|---|---|---|
| `useCompactLayout.js:4` | JS `(max-width: 1024px)` | — | 手机壳 ⇄ 桌面四栏总开关 | 是(定义者) | 收进命名断点 `--bp-shell`;平板档拍板后再定值 |
| index.css:268 | `(min-width: 1024px)` | 1 | 导轨几何 `--rail-w / --app-top` | 1024px 处与手机壳同时成立 | 改 1025 或统一用 `--bp-shell` |
| index.css:1799 | `(max-width: 880px)` | 6 | 登录页 hero / 卡片纵排 | 是 | 归 #214;并入 `--bp-tablet` 下沿 |
| index.css:1829 | `(max-width: 480px)` | 5 | 登录页小屏 | 是 | 并入 `--bp-phone-s` |
| index.css:3244 / 3626 / 3860 / 4544 / 7005 | `(max-width: 1023px)` ×5 | 30 | 管理面台账 / 运行 / 日报卡 / 运维 / 节点板 | 否(手机壳不挂管理面) | 1023 与 1024 差 1px 的双口径;M24 不在范围,只记录 |
| index.css:4171 | `(max-width: 767px)` | 6 | 桌面设置柜竖排 + `.sett-pw-grid` 单列 | 是(只有 `.sett-pw-grid` 等栅格在移动设置栈生效) | 并入 `--bp-phone`;`.sett-row` 漏了(M-09) |
| index.css:4447 / 11655 | `(max-width: 900px)` ×2 | 9 | 管理面热点图 / 源审核栅格 | 否 | 只记录 |
| index.css:7262 | `(hover: none)` | 2 | 桌面视图轨微标签常显 | 否(桌面壳) | 保留;同类适配全站仅 3 处 |
| index.css:8130 / 8139 | `(hover: hover)` / `(hover: none)` | 4 | 速读卡评分依据浮签 ⇄ 内联 | 是 | 范式正确,可作规范样例 |
| index.css:9836 | `(max-width: 1120px)` | 1 | 榜单桌面两栏 → 单栏 | 被 `mobile.css` 覆写 | 桌面内部断点,保留 |
| index.css:10125 / 10231 | `(max-width: 1280px)` ×2 | 2 | 发现页源 / 合集栅格单列 | 是(单列) | 桌面内部断点,保留 |
| index.css:11092 / 11435 | `(hover: hover)` ×2 | 8 | 收听入口 / 播放条悬停 | 是(正确守卫) | 范式正确 |
| index.css:11105 | `(min-width: 560px)` | 1 | 手机正文页播客收听入口回到按内容宽 | 是 | 并入 `--bp-phone` |
| index.css:11450 / 11469 | `@container player` 640 / 900 | 14 | 播放条按容器宽排版 | 是 | 容器查询,保留 |
| index.css:11589 | `(forced-colors: active)` | 8 | 播放条高对比 | — | 保留 |
| index.css:11608 | `(max-width: 1024px)` | 5 | 管理面目录 / 顶部 tab | 否 | 只记录 |
| index.css:12744 / 12780 | `(hover: hover)` ×2 | 3 | 早报条目悬停淡出 / 星标 | 是(正确守卫) | 范式正确 |
| index.css:12918 / 12925 / 12957 | `@container` 960 / 680 / 420 | 36 | 早报夜航版式(680 = 手机档) | 是 | 容器查询保留;420 档把「重新编排」压成 16px 图标(M-07) |
| index.css:13031 / 13144 | `(max-width: 1024px)` ×2 | 4 | 早报 / 兴趣在桌面壳窄窗兜底 | 否(手机壳不渲染桌面壳) | 只是兜底,可留 |
| index.css:13232 | `(max-width: 1024px)` | 2 | 管理面旋钮卡 | 否 | 只记录 |
| index.css:13413 | `(max-width: 1280px)` | 1 | 兴趣栅格两列 | 嵌入态 | 桌面内部断点 |
| index.css:13475 | `(max-width: 720px)` | 2 | 早报首登引导横幅纵排 | 是 | 并入 `--bp-phone` |
| index.css:13481 | `(hover: none)` | 3 | 条目「+ 订阅」/ 兴趣 chip × 常显 | 是 | 范式正确(但没覆盖收藏星,M-02) |
| `App.jsx`(Tailwind) | `sm/md/lg/xl:` 共 13 处 | — | 管理面顶栏 / 自定节点 / 管理区 | 否 | 只记录 |
| `mobile.css` | (无 `@media`) | — | 全部 `.m-*` 无条件生效 | 是 | 移动壳内部无断点:375 与 412 同一套尺寸 |

统计:`@media` 30 处——宽度类 21(`max-width` 1023 ×5、1024 ×4、1280 ×3、900 ×2、880 / 767 / 720 / 480 / 1120 各 1,`min-width` 1024 / 560 各 1)、`hover` 类 8(`none` ×3、`hover` ×5)、`forced-colors` 1;另有 `@container` 5 处(420 / 640 / 680 / 900 / 960)与 JS 1 处(1024)。宽度取值共 10 个(`@media`)+ 5 个(容器)。

### 1.2 平板档观察(820×1180 / 768×1024 / 1024 / 1025 / 1180×820)

- **≤1024 一律手机壳**:820 竖屏下条目行通栏约 780px,摘要一行约 50 个汉字,TabBar 6 个钮各约 136px 宽,顶栏与正文页都无版心约束(`special__tablet-ipad-portrait.png`、`special__tablet-w768.png`)。看得清,但是「拉宽的手机」。
- **1025+ 桌面四栏,但仍是触屏**:列表收藏星 `opacity:0`、源栏右键菜单没有触屏路径(桌面壳不挂 `useLongPress`)、30 条无守卫 `:hover` 规则命中(`data/special-tablet.json`)。
- **iPad 旋转即跨断点**:820 ⇄ 1180 每转一次就在两套壳之间切;早报往期日期与滚动会丢(M-36),播放不中断(核过)。

### 1.3 命名断点草案(供阶段 2 拍板)

| token | 值 | 语义 | 收编的现有断点 |
|---|---|---|---|
| `--bp-phone-s` | ≤ 480 | 小屏手机微调(SE 级) | 480(登录)、早报容器 420 |
| `--bp-phone` | ≤ 767 | 手机竖屏主档 | 767、720、680(早报容器)、560 |
| `--bp-tablet` | 768–1024 | 平板竖屏 / 大屏手机横屏 | 880(登录)、1023 / 1024 双口径归一 |
| `--bp-shell` | ≤ 1024(暂) | 手机壳 ⇄ 桌面壳开关(JS 与 CSS 共用) | `useCompactLayout`、`min-width:1024` |
| `--bp-desktop-m` | ≤ 1280 | 桌面内部收窄 | 1120、1280 |

CSS 自定义属性不能直接写进 `@media`,落地可选 Tailwind v4 `@custom-media` / `@theme --breakpoint-*` 或 JS 常量 + 文档表,由阶段 3 定实现方式。

---

## §2 触控与可达性量化结果

数据:`tmp/issue-230/audit/data/aggregate.json`(745 份状态指标;触控 / 输入 / 悬停 / `vh` 按四档手机视口亮色汇总,对比度取 390 档亮暗)。

| # | 检查 | 结果 | 结论 |
|---|---|---|---|
| ① | 横向溢出 | 根滚动从不溢出(`.m-shell` fixed + `overflow:hidden`);容器级:设置「MCP 接入」滚动区 scrollWidth 574 > 360/375/390(横向可拖,M-10);社交卡 `.social-acts` 盒子越界 17px 但图标可见(核过无视觉影响);类型筛选带、代码块是有意的横向滚动 | 1 处真溢出 |
| ② | 触控目标 | 81 类可点元素有效命中(含 `::after` 扩区)< 44px,44 类 < 32px;合格:TabBar(≥ 44)、动作单行(44)、我的页 / 设置清单行(46)、正文页上 / 下一篇(44)、迷你播放钮(40,接近)、播放条按钮 40–46(命中带 42,略低于 44) | 系统性偏小(M-07) |
| ③ | 文本 < 12px | 只有来源字母徽标(8–9px,规范豁免)与登录卡眉头 / 页脚(11px,品牌豁免);读者面零违例 | 核过 |
| ④ | 输入框 < 16px | 5 类违例:发现 / 兴趣搜索 12.5、改密 13、反馈 13、添加自定源 13、安装指引 select 12;顶栏就地搜索与登录框 16(搜索框是继承 body 16px,未显式声明) | M-06 |
| ⑤ | 安全区 | 竖屏 47/34 覆盖下顶栏、TabBar、抽屉、动作单、播放条、OPML 底栏、设置栈、整页层都正确垫边,无可点元素落入刘海或横条带;横屏左右 47 完全未处理;分享页顶部只有 48px 固定内边距 | 竖屏核过,横屏 M-12 |
| ⑥ | `vh` 实际命中 | 手机页面命中 4 条:`.modal-panel` `calc(100vh - 120px)`(登录提示、退出确认、添加自定源等;安装指引另有 `dvh` 覆盖)、`.shared-page` `min-height:100vh`、`.bp-top` `6.4vh`、`.auth-eyebrow` `6vh`;静态还有灯箱 `92vh`、播客文稿 `55vh`、`.ranking-state 44vh`、`.bp-state 46vh` | M-30 |
| ⑦ | `:hover` 泄漏 | 全站 259 条 `:hover`,248 条无 `(hover: hover)` 守卫;63 条实际命中手机页面元素(背景着色为主;3 条改文案 / 显隐 / 位移) | M-29 |
| ⑧ | 减少动效泄漏 | `data-motion=reduce` 下抽屉、动作单、骨架流光、律动条、月历浮层全部压到 1ms / 停;播放条走 120ms 淡入(符合 §7 具名例外);阅读进度线是滚动驱动,不算泄漏 | 核过 |
| ⑨ | 聚焦指示 | 触屏点按按钮后焦点落在按钮上但 `:focus-visible` 不匹配、无外环(Chromium);顶栏搜索框聚焦画 accent 底线;Safari 点按不聚焦按钮的差异需真机看 | 核过(真机待验) |
| ⑩ | 软键盘 | resizes-content 近似(视口减 336 / 260):TabBar 与播放条随键盘上移,390 剩 508 可视高里被吃 158;改密 / 反馈输入框本身仍可见;iOS 与新版 Chrome 默认 resizes-visual 行为不同 | M-39(真机待验) |
| ⑪ | 长列表 | 见 §5 | — |
| ⑫ | 两套视图共载 | 手机壳与桌面壳同在 `ReaderWorkspace` chunk(280 KB / gzip 84 KB),只切视图不重下;问题在入口带上的图表与公式库 | M-14 |

**补充量化**:暗色亮块只有播放钮(有意反相);WCAG 对比度 < 4.5 的 19 类(亮)/ 9 类(暗)多为设计系统共有的状态章(`.stamp-ok` 3.58)、搜索高亮(3.67)与动作单遮罩下的残影,非手机特有,另记观察;手机页面字号直方图 22 档(M-38)。

---

## §3 逐页走查

每页列发现与「核过无问题」。截图路径均相对 worktree 根;`<vp>` 表示该页在五档视口都有截图。

### 3.0 移动壳里挂的桌面组件与覆写情况

| 组件 | 挂在哪 | 移动覆写 | 漏覆写 / 问题 |
|---|---|---|---|
| `PersonalBriefPage`(`mobile`) | 早报 Tab | 容器查询 680 / 420 + `mobile.css:142-143` | 重编钮 16px(M-07)、`vh`(M-30)、提示行换行(M-23)、跨版式丢日期(M-36) |
| `RankingsPage` | 榜单 Tab | `mobile.css:118-140`(单列、内边距、轴横滑) | 双标题(M-21)、分段 24 / 32px(M-07);版式归 #221 |
| `SocialFlow`(`headless`) | 社交 Tab | `mobile.css:596-618`(侧距、日期头、动作钮常显、译钮扩命中) | 长按(M-04)、动作钮 26px(M-07) |
| `DiscoverPage` | 整页层 | `mobile.css:252-263`(侧距、工具行换行、搜索满行) | 双标题(M-21)、分段 / 添加 / 排序 24–28px(M-07)、源名截断(M-13)、一点即退订(M-03)、搜索 12.5px(M-06) |
| `InterestPage`(`mobile embedded`) | 发现页兴趣段 | `mobile.css:204-242`(移动专用行式) | 三段分段 30px(M-07);其余核过 |
| `AnnouncementBanner` | `.m-shell` | `mobile.css:627`(只改 top) | 悬浮遮挡(M-11)、关闭 24px |
| `ShareMenu` | 正文页顶栏 | `mobile.css:630`(只限宽) | 锚定浮层(M-28)、返回键(M-05) |
| `AiReadingCard` | 正文页 | 无专门覆写,`(hover: none)` 时评分依据改内联(`index.css:8139`) | 核过 |
| `PodcastExperiencePanel` / `PodcastTextPanel` | 正文页 | `mobile.css:289-293` | 文稿 `55vh` 嵌套滚动(M-30) |
| `ListenEntries` | 正文页署名行下 | `index.css:11105`(≥ 560 回内容宽) | 核过;点播入口核过 |
| `ArticleCopy` / `ReaderMarkdown` / `TranslationSkeleton` | 正文页 | 沿用 `.markdown-body` | 灯箱(M-40);翻译骨架核过 |
| `PaperContentNote` / `AnalysisTagChip` | 正文页 | 无 | 标签 chip 高 22px(M-07) |
| `ArticleRow` / `ArticleCardsSkeleton` | 列表 | `mobile.css:621-624`(只改位置) | 星标悬停(M-02)、嵌套交互(M-35) |
| `AxisSeg` / `TagRows` | 源抽屉 | `mobile.css:682-684` | 34px |
| `settings/*` 分区 | 移动设置栈 | `index.css:4171-4178`(栅格塌陷)+ `mobile.css:662` | 行不换行(M-09)、MCP 漂移(M-10)、代码块压字(M-26)、输入 13px(M-06) |
| `OpmlPreview`(`layout="mobile"`) | 设置栈 | `mobile.css:664-679` | 勾选框 16px(M-07);底栏核过 |
| `InstallApp` | 「我的」行变体 | `index.css:4822-4850` | select 12px(M-06)、字体栈(M-38) |
| `Modal` 系(`LoginPrompt` / `ConfirmDialog` / `AddCustomSourceModal`) | portal | 无移动变体,居中卡 top 96px | `vh`(M-30)、返回键(M-05)、按钮 32 / 关闭 28(M-07)、输入 13px(M-06) |
| `BriefHistoryCalendar` | 早报眉头 | 容器 680 下宽度 `min(296px, …)` | 返回键(M-05) |
| `PlayerDock`(`mobile`) | `.m-shell` | `index.css:11549-11573` | 核过;条高与迷你遮挡(M-42) |
| `PwaStatus` | 根 | 无 | 压顶栏(M-27) |
| `SharedArticlePage` | 独立入口 | 无移动专门样式 | `vh` 与顶部内边距(M-30) |
| `LoginScreen` | 独立 / 游客叠层 | 880 / 480 两档 | 首屏不见表单(M-16,归 #214) |

### 3.1 条目流(文章 / 播客 / 社交 / 榜单 / 早报 Tab)

- **文章列表**(`user__<vp>__<theme>__article-list.png`):M-02 收藏星触屏不可达;M-24 类型筛选带横向溢出无渐隐提示,末尾 chip 被切半(360 下 420 / 348);「全部|未读」40×22、顶栏图标 36。核过:顶栏单行不折(长源名省略)、日期组头、紧凑行、分数色阶、亮暗。
- **就地搜索**(`article-search.png`):输入框计算字号 16px 不触发缩放(继承);聚焦底线可见;无结果空态只有一句「没有匹配的文章」(M-41,范围档归 #94)。
- **全部 | 未读**(`article-list-unread.png`):切换正常。
- **无限滚动**:见 §5;哨兵与骨架正常。
- **长按动作单**(`action-sheet.png`):M-01;标题行截断漏出第二行上半截(M-20)。
- **播客列表**(`podcast-list.png`):核过(封面占位、时长、「仅提供原节目」章)。游客播客 Tab 空态「暂无播客」不指向动作(M-41)。
- **社交流**(`social.png`、`social-scrolled.png`):社交卡收藏 / 标读 26×26;长按在 Chromium 触屏与 iOS 都没有动作单(只挂 `contextmenu`),松手的 click 落到卡片内容(M-04)。核过:动作钮触屏常显、译为中文按钮扩命中、日期组头侧距。
- **榜单**(`rankings.png`、`rankings-mid.png`、`rankings-bottom.png`):顶栏「榜单」+ 页头「榜单」双标题(M-21);文章榜 / 播客榜分段 73×24、话题 / 产业 / 实体 108×32。榜单版式本身归 #221。
- **早报**(`brief.png`、`brief-bottom.png`):眉头即顶栏;「重新编排」在 ≤420 容器只剩 16px 图标(M-07);375 下状态提示的图标单独占一行(M-23);`.bp-top` 用 `6.4vh`。核过:夜航手机档字号、往期 chip、本期编排、亮暗。
- **往期月历**(`brief-calendar.png`):日格 36×36、翻月 32×32;按返回直接离开页面(M-05)。
- **历史日期页**(`brief-past.png`、`brief-past-mid.png`):头条宋体 900 在 390 下贴右缘但未裁切(核过);跨版式会丢日期(M-36)。

### 3.2 源抽屉(`drawer.png`、`drawer-bottom.png`、`drawer-source-sheet.png`)

- 源行 40px 高(< 44);订阅 / 兴趣轴 34px;长按源行同 M-01;没有关闭钮,也不能左滑关(M-34);无 dialog 语义与焦点管理(M-35)。
- 核过:有未读靠字重、底部「发现更多来源」44px、安全区垫边、暗色。

### 3.3 正文页

- **普通文章**(`article-zh.png` / `-mid` / `-bottom`):顶栏 5 个 36px 图标钮(原文 / 收藏 / 分享 / [译] / 更多),无文字、全在右上(M-45);上一篇 / 下一篇标题 12px 单行省略(M-25)。核过:眉头 / 宋体标题 23px / 署名行 / 标签、速读卡与分数里程表、阅读进度线(WebKit 26 也支持)、正文排印、原文行、亮暗、安全区。
- **英文文章 + 翻译**(`article-en.png`、`article-en-translating.png`、`article-en-partial.png`):译钮按下即进中文视图,未完成块画骨架、完成块原位替换(注入的部分译文验证);核过版式。译钮位置见 M-19。
- **论文**(`article-paper.png`):`PaperContentNote` 小字两段,核过。
- **点播入口**(沙箱 `ondemand` 能力位关,拦 `/api/runtime` 打开后补拍 `user__390x844__light__ondemand-podcast.png`、`ondemand-article.png`):「点播精品导读」整宽次按钮、「✦ 听导读」胶囊,核过;生成中 / 已就绪态未覆盖(需真模型)。
- **播客单集**(`podcast-episode.png`、`-mid.png`):收听原节目主按钮 320×40;「生成节目简介导读」文字钮 18px 高(M-07);节目简介里 Markdown 标题渲染得很大(来源内容,不计)。文稿面板 `max-height:55vh` 在滚动区里再嵌一层滚动(M-30)。
- **分享浮层**(`article-share.png`):锚定在分享钮下的小浮层盖住标题,关闭钮 24px;按返回关掉的是正文页而浮层随之消失(M-05、M-28)。分享去留归 #215。
- **更多动作单**(`article-more.png`):同 M-20。
- **图片灯箱**:未接返回键、`92vh`、只能整页缩放(M-40)。

### 3.4 播放坞与 TabBar 叠放(`player-on-article.png`、`player-on-list.png`、`player-on-social.png`、`player-mini.png`、`player-mini-me.png`)

- 核过:正文页条贴安全区之上、列表页条在 TabBar 之上、切 Tab / 进出正文 / 跨版式(390 ⇄ 1280)播放不中断(`data/special-crosslayout.json`:t 从 2.3s 走到 6.4s、未暂停);迷你态 40px 按钮;各滚动区底部留白接上了 `--player-clear-end`。
- 体感:条 94 + TabBar 64 = 158px,375×667 内容区约 400px;横屏只剩一行;迷你态浮在发现卡右下会压住「✓ 已订阅」(M-42)。

### 3.5 「我的」页(`me.png`、`guest-me.png`、`install-guide.png`)

- 外观三态 30×22(M-07);「添加到主屏幕」弹出居中指引,平台 select 12px(M-06),PWA 指引用的是 `"Mona Sans VF"` 字体栈而非 `--font-sans`(M-38)。核过:游客卡 + 登录、读者卡、行高 46、退出确认、暗色。

### 3.6 设置栈(`settings.png`、`settings-<分区>.png`、`opml-preview.png`、`guest-settings*.png`)

- **账户**:改密输入 13px、按钮 32px(M-06 / M-07)。
- **外观**:`.sett-row` 不换行,说明列被挤成竖条(M-09,`user__375x667__light__settings-appearance.png` 最明显)。
- **阅读**:开关 34×19(M-07)。
- **订阅源 OPML / 导入预览**:预览勾选框 16×16 且整行不可点(M-07);底部确认条贴底、安全区正确(核过)。
- **反馈**:分类 mini-seg 22px、textarea 13px。
- **聚合接口 / MCP 接入**:代码块复制钮压字(M-26);MCP 工具表横向漂移 574px(M-10);复制钮 26px。
- **技能包 / 关于**:核过。
- **游客**:锁标与「登录后可用」页核过。
- 结构:分区页按返回回到清单、再返回关设置(核过);`role="dialog" aria-modal` 但无焦点陷阱(M-35)。

### 3.7 发现页 / 合集 / 兴趣(`discover.png`、`discover-mid.png`、`discover-collections.png`、`discover-collection-detail.png`、`discover-interests.png`、`interests*.png`)

- 顶栏「发现」+ 页头「发现」双标题(M-21);视图分段 28×24、「+ 添加源」/ 排序 28px(M-07);源名被类型 chip 与订阅钮挤到 6–8 字(M-13);「✓ 已订阅」一点即退订(M-03);搜索 12.5px(M-06)。
- 核过:合集卡整卡可点(42px+)、合集详情返回、兴趣行 58px 高、三段分段 30px(偏矮,记入 M-07)、暗色。

### 3.8 分享落地页 `SharedArticlePage`(`anon__<vp>__<theme>__share-page.png`)

- `min-height:100vh`、顶部固定 48px 内边距不认安全区(浏览器模式下影响小,PWA / 横屏会贴边)(M-30);「查看原文」链接 18px 高。核过:窄屏排印、亮暗、无溢出。

### 3.9 登录页与游客登录(`anon__<vp>__<theme>__login.png`、`login-bottom.png`、`guest__<vp>__<theme>__guest-login-prompt.png`、`guest-login-overlay.png`)

- 375×667 下 hero 占满首屏,表单要上滑约 400px 才出现;390×844 登录钮半截在折线下,「游客登录」在首屏外(M-16,视觉方向归 #214)。登录提示 Modal 的关闭钮 28px、两个按钮 32px;叠层登录按返回关掉的是下层正文(M-05)。
- 核过:输入框 16px(不缩放)、品牌演出、暗色豁免。

### 3.10 公告横幅、PWA 状态条

- 公告:M-11(`announcement.png`、`announcement-on-list.png`);关闭钮 24px。
- PWA 断网条:固定在顶部中间、`z-index:80`,正好压住顶栏标题与分段(M-27,`pwa-offline.png`、`special__safearea-offline.png`);更新条同位置同问题(静态核对 `index.css:4847`)。

### 3.11 手机壳 ⇄ 桌面壳的状态同步点

- 共享(核过):`useReaderState` 挂在 `ReaderWorkspace`,文章 / 订阅 / 选中 / 搜索 / 发现段位两套视图共用;`PlayerProvider` 共用,跨版式播放不断;列表滚动偏移与正文阅读比例由 `ReaderWorkspace.jsx:58-91` 恢复。
- 只在一侧:移动抽屉 / 动作单开合(切桌面即丢,可接受);早报选中日期与滚动在 `PersonalBriefPage` 组件内(M-36);发现页 / 设置 / 兴趣页滚动不记;设置面板由 `App.jsx:761` 按断点在 `SettingsModal` 与 `MobileSettings` 间切换,打开中跨版式会回到打开时的分区。

---

## §4 功能缺口与操作适配

| 项 | 现状(证据) | 期望 / 建议 |
|---|---|---|
| 问答面板 | `ReaderAiPanel` 只挂在桌面 `ReaderTab.jsx:1588-1596`,移动壳无入口(M-17) | 底部 sheet(正文页「问这篇」+ 列表页「问订阅」),经 `useLayerHistory` 握手;形态待拍板 |
| 翻译入口 | 顶栏第 4 个无文字「译」图标,只在非中文文章出现(`MobileArticlePage.jsx:140-152`);逐段骨架与原位替换在手机正常 | 是否下沉到标题区尾行(backlog「译钮下沉」)待拍板(M-19) |
| 长按 | 文章行 / 源行长按松手即关(M-01);社交卡无触屏长按(M-04) | 遮罩改「按下与松开同在遮罩」;社交卡接 `useLongPress` |
| 手势 | 无左滑关抽屉、无下拉关动作单(有抓手装饰)、无下拉刷新(滚动区 `overscroll-behavior:contain` 同时挡掉了浏览器下拉刷新)、无边缘右滑返回(浏览器模式靠系统,iOS PWA 独立窗口没有系统返回手势) | 抓手要么能拖要么去掉;iOS PWA 下每层必须有一个不靠系统返回、也不靠拖动的出口(卡外能点即算,整屏推入的页面要返回钮;抽屉现在没有)(M-34;2026-10-29 拍板改写,原为「可见返回 / 关闭」);下拉刷新要不要做待拍板(M-44) |
| 返回键 | 已接:正文页、发现页、合集详情、抽屉、动作单、设置栈三层(`data/special-backkey.json` 全部关得掉且不离站);未接:见 M-05 | 统一层原语 |
| 键盘 | 移动壳只有搜索与表单输入;动作单 `role="menu"` 不移焦、无方向键,自挂 document Esc(违反 conventions §8「只用 useModalA11y」) | 随层原语一起收(M-35) |

---

## §5 性能与体感

- **首屏网络**(生产构建 `vite preview`,390×844,禁用缓存,限速 1.6 Mbps / RTT 150ms + CPU 4×;`data/special-netload.json`):
  - 登录页:脚本 6 个共 354 KB(传输量),其中 `vendor-markdown` 124 KB + `vendor-charts` 108 KB = 232 KB(66%)登录页用不到;样式 137 KB(含 `dorami-serif.css` 61 KB 的 `@font-face` 声明,head 里阻塞渲染);DCL 2.79 s、LCP 3.26 s(不限速 0.62 s)。
  - 读者首屏(落早报):脚本 18 个 453 KB、样式 137 KB、字体 8 个 342 KB(宋体分片,`font-display:swap` 不阻塞文字)、API 14 个 237 KB;壳出现 4.14 s、早报内容 4.44 s、LCP 4.43 s(不限速 0.83 s)。
- **构建体积**(`data/build_audit.json`):入口 `index` 170 KB 静态依赖 `vendor-react` 174 KB、`vendor-charts` 378 KB、`vendor-markdown` 418 KB、`api` 40 KB;`ReaderWorkspace` 274 KB(手机壳与桌面壳、早报、发现、兴趣、播放器、问答面板都在里面);阅读器路由首屏 JS 合计 1477 KB(raw)、CSS 397 KB(raw,gzip 约 75 KB)。根因:`main.jsx` 静态引入 `SharedArticlePage` → `ReaderMarkdown` 进入口,而 `vendor-markdown` 又从 `vendor-charts` 取一个共享模块,两个大 vendor 被一起 modulepreload(`vite.config.js` `manualChunks`)。入口里还带着只有管理员用得到的设置分区(`DataSyncSection` 17 KB、`CredentialsSection` 8.5 KB)(M-14)。
- **长列表**(生产预览,CPU 4×,`data/special-longlist.json`):首屏 30 条、DOM 701 节点;无限滚动 8 轮到 240 条,DOM 4321、来源 logo 图 234 张、JS 堆约 13 MB;程序化滚动帧间隔 p50 8.3 ms / p95 9.3 ms / 最大 25 ms,无 > 33 ms 帧——这个量级不卡,现在不必做虚拟化(M-46 只建议加 DOM 规模守卫)。这是桌面 Chromium 降频近似,低端安卓另验(R-16)。
- **图片**:正文图走 `/api/media/proxy`(沙箱关了媒体库,302 回源);源头像 `loading="lazy" decoding="async"`;正文图懒加载与尺寸适配在本地无法量真实字节,列入真机待验。
- **动效体感**:抽屉 / 动作单只有 240ms 入场,关闭是瞬间卸载;正文页入栈 / 出栈、设置分区切换没有过渡(`data/special-motion.json`)(M-33,视觉方向拍板时一起定)。
- **PWA 更新提示**:SW 只在生产 + 安全上下文注册,回前台 / 联网时最多 5 分钟检查一次(`pwa.js:57-77`);提示条位置同 M-27。

---

## §6 真机待验清单(本地只能近似)

| # | 项 | 本地近似结果 | 真机验收步骤建议 |
|---|---|---|---|
| R-01 | iOS Safari 长按动作单(M-01) | Chromium 触屏 16/16 松手即关 | 列表长按一篇 1 秒后松手:动作单应保持;再长按源抽屉里的源 |
| R-02 | Android Chrome 长按 | 原生长按走 `contextmenu`,预计不受 M-01 影响 | 同上,另测社交卡长按 |
| R-03 | iOS 输入聚焦缩放(M-06) | 计算字号 12.5 / 13 / 12 | 依次聚焦发现搜索、兴趣搜索、改密、反馈框、安装指引下拉:页面不应放大 |
| R-04 | 软键盘(iOS resizes-visual / Android Chrome / 华为 / 微信) | 只能模拟 resizes-content(TabBar 与播放条上移) | 播放中进列表 → 点搜索输入:看 TabBar、播放条去哪、输入框是否被顶出;改密页依次聚焦三个框;登录页聚焦密码框后登录钮是否可见 |
| R-05 | 刘海 / 灵动岛 / 底部横条 | CDP 覆盖 47 / 34 竖屏全部核过 | iPhone 竖屏浏览器模式 + 添加到主屏幕后的独立窗口各看一遍顶栏、TabBar、抽屉、动作单、播放条 |
| R-06 | 横屏左右安全区(M-12) | 左右 47 未处理 | iPhone 横屏(刘海在左 / 右各一次)看顶栏汉堡钮、列表左缘、TabBar |
| R-07 | iOS 粘滞悬停(M-29) | Chromium 不复现 | 点一篇进正文再返回:那一行是否残留悬停底色;发现页点「✓ 已订阅」看是否先翻「取消订阅」 |
| R-08 | Safari 地址栏伸缩 + `100vh` 残留 | 静态命中 `.modal-panel` / `.shared-page` / 灯箱 | 打开登录提示、退出确认、图片灯箱、分享页,滚动让地址栏收起 / 展开,看是否跳动或被裁 |
| R-09 | 橡皮筋 / 下拉刷新 | 滚动区 `overscroll-behavior:contain` | 列表顶部下拉:不应拖动整个壳;确认没有下拉刷新这件事是否可接受 |
| R-10 | 系统返回(Android 返回键 / iOS 边缘右滑) | `history.back` 矩阵见 M-05 | 依次打开 正文 / 抽屉 / 动作单 / 发现 / 设置分区 / 月历 / 分享浮层 / 安装指引 / 搜索,各按一次返回 |
| R-11 | iOS PWA 独立窗口无系统返回 | — | 独立窗口里打开抽屉、灯箱、分享浮层、播放悬浮卡,确认每层都有不靠系统返回、也不靠拖动的出口(卡外能点,或可见的关闭 / 返回钮) |
| R-12 | 华为浏览器 | 安装入口按 UA 隐藏(`pwa.js:8`) | 鸿蒙 / 华为浏览器:安装入口不出现、长按 / 键盘 / 安全区同 R-01 ~ R-05 |
| R-13 | 微信内置浏览器 | `installGuide` 给 embedded 文案 | 微信打开站内深链:游客自动进入、返回键关层、底部工具栏与 TabBar 叠放 |
| R-14 | 播放连续性(锁屏 / 后台 / 切 App) | 应用内切 Tab / 进出正文 / 跨版式核过 | 播放中锁屏 1 分钟、切到别的 App 再回来、来电打断后恢复 |
| R-15 | 早报日期页跨版式 / 旋转(M-36) | 1280 ⇄ 390 丢日期与滚动 | iPad 在往期某天滚到一半后旋转 |
| R-16 | 首屏真实网络 | 限速 1.6 Mbps 近似 | 4G / 内网 Wi-Fi 冷启动计时到早报可见 |
| R-17 | 图片懒加载与代理尺寸 | 沙箱关媒体库 | 正文含 10+ 图的文章:滚动时逐张加载、无布局跳动 |
| R-18 | iOS 16–17 等老 Safari | WebKit 26 全部特性支持;`animation-timeline` 老版本不支持(进度线隐形,设计如此)、`color-mix(in oklab)` 需 16.2+ | 老 iPhone 打开正文页、播放条、三套中性色 |
| R-19 | 浏览器 UI 配色(M-32) | `theme-color` 只跟系统深浅 | 应用内切暗色(系统亮色)看地址栏 / 状态栏颜色 |
| R-20 | iOS「悬停揭示」拦首击(待确认,高优先) | iOS WebKit 的 ContentChangeObserver 会在点按引起的悬停让可点内容显现(opacity / display 变化)时吞掉这次 click;本地 WebKit / Chromium 都没有这套逻辑,无法近似 | 列表里单击一篇未收藏的文章:是否一次就进正文(还是先浮出星标、要点第二次);发现页单击「✓ 已订阅」:是直接退订,还是先翻成「取消订阅」 |

---

## §7 发现总表

面:BP 断点与视口 / TA 触控与可达性 / PG 逐页走查 / FG 功能缺口 / PF 性能与体感 / VB 验证基线。截图前缀省略 `tmp/issue-230/audit/`;`<vp>` = 该现象在多档视口都有截图。

| ID | 面 | 严重度 | 现象(视口 / 主题 / 证据) | 期望 | 涉及面(文件:行) | 建议归属 |
|---|---|---|---|---|---|---|
| M-01 | TA | P1(iOS 待真机) | 文章行 / 源抽屉行长按:450ms 弹出动作单,松手的合成 click 命中新盖上的 `.m-dim` → 立即关闭;四档手机 × 亮暗 16/16 复现(`data/user__<vp>__<theme>__article-row-longpress.json`:`open@ → click->m-dim → close@`) | 松手后动作单保持;遮罩只在「按下与松开都在遮罩」时关(同 `utils/overlayClose`) | `frontend/src/hooks/useLongPress.js:53-59`(只拦包装内的 click)、`frontend/src/components/mobile/ActionSheet.jsx:21`、`MobileSourceDrawer.jsx:92-96` | 本波 |
| M-02 | TA | P2 | 列表行收藏星 `opacity:0; pointer-events:none`,只在 `:hover` 浮出;所有宽度(含平板)触屏下都点不到(`data/special-tablet.json` favStar) | 触屏常显(同社交卡动作钮「不画摸不到的钮」)或明确只走动作单;iOS 上悬停渐显还可能拦掉点开文章的首击(R-20 待确认) | `frontend/src/index.css:8344-8360`、`mobile.css:622` | 待拍板 |
| M-03 | TA | P2 | 发现页「✓ 已订阅」触屏一点即退订;自定源走移除(退订 + 无人订阅即清配置与文章);无确认无撤销(`shots/user__<vp>__light__discover.png`;悬停规则 `.reader-disc-sub.is-subbed:hover .t-on{display:none}` 在手机页面命中) | 触屏两击确认或 toast 带撤销 | `DiscoverPage.jsx:83-92`、`index.css:10217-10221`、`useReaderState.js:1016-1033 / 1065-1079` | 本波 |
| M-04 | TA | P2 | 社交卡只挂 `contextmenu`,iOS 长按无动作单;Chromium 触屏长按同样无反应,松手 click 落到卡片内容(`data/...social-post-longpress.json`) | 接 `useLongPress`,与文章行同一条路径 | `SocialFlow.jsx:248`、`MobileReader.jsx:395` | 本波 |
| M-05 | TA | P2 | 未接返回键握手:顶栏搜索、往期月历、分享浮层、`Modal` 系(安装指引 / 退出确认 / 登录提示 / 添加自定源)、游客叠层登录、图片灯箱。按返回:月历 / 搜索 / Modal 这一层不关,history 直接退到上一条记录——本测里退回上一个文档并重载到早报,会话第一屏时就是离开站点 / 退出 PWA;分享浮层、登录提示、叠层登录关掉的是下层正文,浮层本身或随之卸载或仍留着(`data/special-backkey.json`) | 每一层都入层栈:返回先关最上层 | `useLayerHistory.js`、`MobileReader.jsx:227-232`(已接清单)、`Modal.jsx`、`BriefHistoryCalendar.jsx:51`、`ShareMenu.jsx`、`App.jsx:1014-1024`、`ReaderMarkdown.jsx:165-189` | 本波 |
| M-06 | TA | P2 | iOS 聚焦缩放:`.reader-disc-search input` 12.5px(发现 / 兴趣搜索)、`.form-input` 13px(改密 ×3、反馈 textarea)、`.csrc-urlbox input` 13px(添加自定源,`shots/user__390x844__light__add-custom-source.png`)、`.pwa-platform select` 12px(`data/aggregate.json` inputs) | 移动壳内所有输入 ≥ 16px(或 `max(16px, …)`) | `index.css:12100`、`index.css:4646`、`index.css:12094`、`index.css:4846`;顶栏搜索靠继承 `mobile.css:96-104` | 本波 |
| M-07 | TA | P2 | 触控目标:81 类 < 44(44 类 < 32)。顶栏 / 正文页 / 整页层图标钮 36×36;全部 \| 未读 40×22;发现视图分段 28×24;早报重编 / 回到今天 16×28;OPML 勾选 16×16 且行不可点;社交收藏 / 标读 26;外观三态 30×22;阅读开关 34×19;标签 chip 高 22;复制钮 26;分享浮层 / 公告关闭 24;M 档按钮 32 全量沿用(`data/aggregate.json` targets_lt44) | 移动触控规则:主要动作 ≥ 44,行内次要 ≥ 32 + 扩命中 | `mobile.css:70-79`、`index.css:3314-3322`、`7853`、`12584 / 12957-12959`、`OpmlSection.jsx:148-154`、`index.css:9451`、`mobile.css:583-588`、`3040`、`12496`、`3691`、`8705`、`12293` | 本波 |
| M-08 | BP | P2 | 平板竖屏 820 / 768 吃手机壳:条目行通栏 ~780px、正文行宽 780px(17px 字约 45 字 / 行)、TabBar 6 钮各 ~136px、无版心(`shots/special__tablet-ipad-portrait.png`、`special__tablet-w768.png`、`user__820x1180__light__article-zh.png`) | 定平板档(居中版心 / 两栏 / 维持) | `useCompactLayout.js:4`、`mobile.css` 全文无断点 | 待拍板 |
| M-09 | PG | P2 | 设置分区 `.sett-row` 窄屏不换行:控件列 `flex:none`,说明列被挤成每行 2–4 字;375 下外观「中性色」说明 10 行(`shots/user__375x667__light__settings-appearance.png`、`user__390x844__light__settings-appearance.png`) | ≤ 767 时 label / 控件上下堆叠 | `index.css:4010-4017`、`4171-4178`(只塌陷了栅格) | 本波 |
| M-10 | PG | P2 | 设置「MCP 接入」滚动区横向漂移:scrollWidth 574 > 360/375/390,工具表固定 168px 列 + 参数行不换行,说明被裁(`shots/user__<vp>__light__settings-mcp.png`、`-bottom.png`) | 窄屏工具表改上下排 | `index.css:3721-3729`、`McpAccessSection.jsx:251-260` | 本波 |
| M-11 | PG | P2 | 公告横幅悬浮约 150px(两条),入口后 8 秒盖住顶栏 / 早报眉头,期间订阅源 / 搜索 / 往期 / 重编点不到,玻璃底透字(`shots/user__<vp>__light__announcement.png`、`announcement-on-list.png`) | 移动壳改占位式或贴顶栏下沿、可上滑收起 | `index.css:12195-12226`、`mobile.css:627`、`AnnouncementBanner.jsx:45` | 本波(形态待拍板) |
| M-12 | BP | P2 | 横屏左右安全区全未处理:844×390 + 左右 47 下汉堡钮、标题、条目、TabBar 都在刘海区;横屏可视内容约 1 行(条 94 + TabBar + 顶栏 + 类型带)(`shots/special__safearea-landscape-list.png`、`-article.png`) | 壳与 TabBar 垫 `env(safe-area-inset-left/right)`;横屏播放条默认迷你 | `mobile.css` 全文只有 top / bottom(17 处);`index.css:4832` 是唯一用左右 inset 的地方 | 本波 |
| M-13 | PG | P2 | 发现页源卡:名字被类型 chip 与订阅钮挤到 6–8 字(「Claude ...」「Codex C...」),375 更短(`shots/user__375x667__light__discover.png`) | 窄屏 chip 下移到描述行或只留一个 | `DiscoverPage.jsx` 卡片头、`.reader-disc-name` | 本波 |
| M-14 | PF | P2 | 入口静态依赖 `vendor-charts`(recharts 378 KB / gz 112 KB)与 `vendor-markdown`(KaTeX 等 418 KB / gz 128 KB),登录页与手机首屏都 modulepreload;限速下登录页 232 KB / 354 KB 脚本用不到,读者首屏 LCP 4.43 s;阅读器路由首屏 JS 1477 KB raw(`data/build_audit.json`、`data/special-netload.json`) | 图表只随管理面懒加载;Markdown / KaTeX 随正文首开再拉;分享页单独入口 | `vite.config.js` `manualChunks`、`main.jsx:6`、`index.html` modulepreload | 已修(批 4b,10-31):分组显式排优先级、Markdown 渲染器与分享页按需加载;登录页脚本 357 → 125 KB |
| M-15 | BP | P2 | iPad 横屏 / ≥ 1025 触屏走桌面四栏:列表星标隐形、源栏右键无触屏路径、30 条无守卫悬停规则命中(`shots/special__tablet-ipad-landscape.png`、`data/special-tablet.json`) | `(pointer: coarse)` 下桌面壳补触屏路径,或平板横屏也用移动壳 | `ReaderTab.jsx`(无 `useLongPress`)、`index.css:8344-8360` | 待拍板 |
| M-16 | PG | P2 | 登录页 / 游客叠层登录:375×667 首屏只有 hero,表单在约 400px 之下;390×844 登录钮半截在折线下、「游客登录」在首屏外(`shots/anon__375x667__light__login.png`、`guest__375x667__light__guest-login-overlay.png`) | 窄屏首屏可见账号框与登录钮 | `LoginScreen.jsx:469-630`、`index.css:1799-1840` | 归 #214(约束写进其方案) |
| M-17 | FG | P2 | 问答(问哆啦美)在移动壳无入口(`ReaderAiPanel` 仅桌面挂载) | bottom-sheet + 返回键握手 | `ReaderTab.jsx:1588-1596`、`MobileReader.jsx`(无) | 拆 #249(10-31) |
| M-18 | VB | P2 | 自动化只覆盖 390×844 单视口(`e2e/mobile_reader.py:54`),无长按、无返回键矩阵、无安全区、无软键盘、无输入字号 / 触控尺寸守卫;M-01 因此一直没被发现 | `scripts/check_mobile_reader_e2e.py` 扩手机四档 + 本审计的守卫断言 | `e2e/mobile_reader.py`、`e2e/focus_ring.py:184` | 已修(批 4b,10-31):`guards` 流程四档并行;焦点流程偶发失败查清并加固 |
| M-19 | FG | P3 | 译钮是顶栏第 4 个无文字图标(只在非中文文章出现),5 个 36px 图标并排;backlog「译钮下沉」(`shots/user__390x844__light__article-en.png`) | 拍板位置:顶栏保留 / 标题区尾行「原文 \| 中文」二段 | `MobileArticlePage.jsx:140-152` | 待拍板 |
| M-20 | PG | P3 | 动作单标题 `-webkit-line-clamp:1` + `padding-bottom:10px`,第二行上半截漏出(`shots/user__390x844__light__action-sheet.png`、`social-sheet.png`) | 截断盒内不留下内边距(外层留白) | `mobile.css:463-475` | 本波 |
| M-21 | PG | P3 | 榜单 / 发现双标题:顶栏「榜单」+ 页头「榜单」,顶栏「发现」+ 页头「发现」(`rankings.png`、`discover.png`) | 手机壳去掉其一 | `MobileReader.jsx:241-243`、`585` | 本波(榜单版式归 #221) |
| M-22 | PG | P3 | 对比度个别 < 4.5:状态章 `.stamp-ok` 3.58、搜索高亮 3.67、`.podcast-status` 4.34、暗色收听时长 3.84、字母头像 4.27;暗色下开关(减少动效 / 默认中文标题)关闭态轨道几乎与底同色,只剩白色滑块(非文本控件要求 3:1)(`data/aggregate.json` contrast_light / contrast_dark、`sheets/zoom-switch.png`、`shots/user__390x844__dark__settings-appearance.png`)。均非手机特有 | 状态章、高亮、开关轨道提一档 | `index.css` `.stamp-*`、`.search-hl`、`.listen-len`、`.ledger-switch`(3040) | 观察 / 另 issue |
| M-23 | PG | P3 | 375 下早报状态提示的图标单独一行、文字另起(`shots/user__375x667__light__brief.png`) | 图标与首行文字同行 | `index.css:12627` | 本波 |
| M-24 | PG | P3 | 类型筛选带横向溢出无渐隐 / 箭头提示,末尾 chip 被切(360:420 / 348) | 右缘渐隐或换行 | `mobile.css:692-699` | 本波 |
| M-25 | PG | P3 | 上一篇 / 下一篇标题 12px 单行省略,左右两卡各约 160px | 两行 13–14px 或只留下一篇 | `mobile.css:318-353` | 本波 |
| M-26 | PG | P3 | 代码块复制钮压在代码文字上(`$DORAMI_TOKEN` 被盖)(`shots/user__390x844__light__settings-feed.png`) | 代码块右侧预留按钮位 | `index.css:3691-3710` | 本波 |
| M-27 | PG | P3 | PWA 断网 / 更新条固定在顶部中间(z 80),压住顶栏标题与分段(`pwa-offline.png`、`special__safearea-offline.png`) | 移动壳放到顶栏下沿或 TabBar 上沿 | `index.css:4847` | 本波 |
| M-28 | PG | P3 | 分享浮层在手机是锚定小浮层,盖住标题;关闭钮 24px(`article-share.png`) | 手机改底部 sheet(去留归 #215) | `ShareMenu.jsx`、`mobile.css:630`、`index.css:8671-8715` | 本波(去留归 #215) |
| M-29 | TA | P3 | 259 条 `:hover` 中 248 条无 `(hover: hover)` 守卫,63 条命中手机页面;`hover: none` 适配全站仅 3 处(`data/aggregate.json` hover_hits、`static_css_audit.md`) | 规范:悬停样式一律包 `(hover: hover)`,功能性悬停改常显 | `index.css` 全文;范式样例 `index.css:8130-8150` | 已修(批 4b,10-31):计数棘轮 `test/hoverGuard.test.mjs`;手机命中的装饰性悬停已包守卫,余收藏星 / 翻退订 / 公告关闭随 M-02 / M-03 / M-11 |
| M-30 | BP | P3 | `vh` 残留:`.modal-panel` `calc(100vh - 120px)`、`.shared-page` `100vh`、灯箱 `4vh / 92vh`、播客文稿 `55vh`、`.ranking-state 44vh`、`.bp-state 46vh`、`.bp-top 6.4vh`、`.auth-eyebrow 6vh`;`index.css` 字面 `100vh` 共 9 处(3 处已带 dvh 兜底,4 处是桌面 / 管理壳) | 移动可达的一律 `dvh` / `svh` | `index.css:4811`、`8584`、`10922 / 10930`、`mobile.css:289`、`index.css:9824`、`12955`、`12578`、`1348` | 已修(批 1 / 3b / 4b,10-31):余登录页演出 3 处归 #214;守卫 `test/viewportUnits.test.mjs` |
| M-31 | BP | P3 | 断点散落:`@media` 宽度 10 个取值(480 / 560 / 720 / 767 / 880 / 900 / 1023 / 1024 / 1120 / 1280)+ 容器查询 5 个(420 / 640 / 680 / 900 / 960)+ JS 1024,1023 / 1024 双口径,无命名 token(§1) | 命名断点(§1.3) | `index.css` 全文、`useCompactLayout.js:4` | 已修(批 1,10-31;批 4b 复核无余项) |
| M-32 | PG | P3 | `theme-color` 只跟系统深浅,应用内切暗色 / 三套中性色时浏览器地址栏与 PWA 状态栏不跟;manifest `background_color` 只有亮色 | 运行时按 `data-theme` / `data-palette` 改写 meta | `frontend/index.html:11-12`、`manifest.webmanifest` | 本波 |
| M-33 | PF | P3 | 抽屉 / 动作单只有入场无退场;正文页入栈 / 出栈、设置分区切换无过渡(`data/special-motion.json`) | 随视觉方向拍板定「合理动效」 | `mobile.css:375-380 / 450-455` | 待拍板 |
| M-34 | TA | P3 | 抽屉无关闭钮、不能左滑关;动作单有抓手但不能下拉关(假 affordance);iOS PWA 独立窗口没有系统返回 | 抓手能拖、能点、读屏念得出;每层有不靠系统返回、也不靠拖动的出口(卡外能点即算,整屏推入页要返回钮)——2026-10-29 拍板(设计 README 拍板 33),原建议「可见关闭 + 手势或去掉抓手」 | `MobileSourceDrawer.jsx:47-131`、`ActionSheet.jsx:23` | 本波 |
| M-35 | TA | P3 | 无障碍:动作单 `role="menu"` 不移焦、无方向键、自挂 document Esc;源抽屉无 dialog 语义 / 焦点管理 / Esc;移动设置 `aria-modal` 无焦点陷阱;正文页打开后下层列表未 `inert`;列表行 `button` 内嵌 `span[role=button]` 星标 | 层原语统一挂 `useModalA11y` | `ActionSheet.jsx:10-15 / 22`、`MobileSourceDrawer.jsx:47-49`、`MobileSettings.jsx:141`、`ReaderTab.jsx`(ArticleRow 星标) | 本波 |
| M-36 | BP | P3 | 跨版式丢早报往期:9月16日 @1500px → 切桌面再切回变成今天 @0;iPad 旋转即触发(`data/special-crosslayout.json`) | 早报选中日期上提到 `ReaderWorkspace` 共享态 | `PersonalBriefPage.jsx`(日期为组件内状态)、`ReaderWorkspace.jsx:58-91`(只记列表 / 正文) | 本波 |
| M-37 | PG | P3 | `mobile.css` 规范违例:`#f59e0b` ×2、遮罩 `rgba` ×2、`border-radius:12px`;死样式 `.m-agg-row` 族 23 行,抽屉头注释仍写「聚合入口」 | token 化 + 清扫 | `mobile.css:83 / 231 / 359 / 361 / 394-416`、`MobileSourceDrawer.jsx:10` | 本波 |
| M-38 | PG | P3 | 手机页面字号 22 档(12 / 12.5 / 13 / 13.5 / 14 / 14.5 / 15 / 15.5 / 16 / 17 / 18 / 19 / 20 / 23 …,`data/aggregate.json` font_hist_390);PWA 指引另用 `"Mona Sans VF"` 字体栈 | 移动字号刻度(规范) | `mobile.css` 全文、`index.css:4826-4830` | 本波(规范) |
| M-39 | TA | P3 | 软键盘(resizes-content 近似):TabBar 与播放条跟到键盘上方,390 剩 508 可视高被占 158,375 剩 407(`shots/special__kbd-390x844-search-with-player.png` 等) | 键盘弹起时隐藏 TabBar / 迷你化播放条(真机先确认) | `mobile.css:161-168`、`index.css:11549-11560` | 真机后定 |
| M-40 | PG | P3 | 图片灯箱:未接返回键、`92vh`、只能整页缩放、自挂 document Esc | 入层栈,手机支持双指 | `ReaderMarkdown.jsx:165-189`、`index.css:10915-10931` | 本波 |
| M-41 | PG | P3 | 空态不指向动作:游客播客「暂无播客」、搜索无结果「没有匹配的文章」(`shots/guest__390x844__light__empty-guest-podcast.png`、`user__390x844__light__empty-search.png`) | 给第一个动作(conventions §1) | `MobileReader.jsx:485-505` | 本波 |
| M-42 | PF | P3 | 播放条 94 + TabBar 64 = 158px,375×667 内容区约 400px;迷你态浮在发现卡右下压住「✓ 已订阅」(`shots/user__375x667__light__player-on-list.png`、`discover.png`) | 视觉方向时一起定条高与迷你位置 | `index.css:11549-11573` | 待拍板 |
| M-43 | VB | P2 | 移动壳从 #86 起没做过真机验收(`docs/frontend/mobile-navigation.md` 收口状态) | §6 清单逐项过 | — | 本波 |
| M-44 | FG | P3 | 无下拉刷新:滚动区 `overscroll-behavior:contain` 同时挡掉浏览器下拉刷新,新内容只能等「载入 N 篇新文章」胶囊 | 拍板要不要做应用内下拉刷新 | `mobile.css:149 / 222 / 247 / 281 / 391 / 509` | 待拍板 |
| M-45 | FG | P3 | 正文页工具条:原文 / 收藏 / 分享 / 译 / 更多 5 个动作全在顶栏右上(单手拇指热区外),无底部工具条;「更多」与长按动作单是同一组动作(`shots/user__<vp>__light__article-zh.png`) | 拍板是否加底部工具条(收藏 / 分享 / 译 / 问答下沉) | `MobileArticlePage.jsx:91-156` | 待拍板 |
| M-46 | PF | P3 | 长列表无回收上限:240 条时 DOM 4321、logo 图 234 张常驻;CPU 4× 下滚动帧 p95 9.3 ms、无 > 33 ms 帧,当前不卡(`data/special-longlist.json`) | 暂不做虚拟化;e2e 加「滚到 200 条 DOM < 6k」守卫 | `MobileReader.jsx:507-543` | 已修(批 4b,10-31):e2e 守卫,210 条时 DOM 约 3000 |

### 核过无问题(便于确认审过的面)

- 根滚动零溢出、零 `pageerror`;四档手机 × 亮暗所有状态无 JS 异常。
- 竖屏安全区(47 / 34 覆盖):顶栏、TabBar、抽屉、动作单、播放条、OPML 底栏、设置栈、整页层、登录叠层全部正确。
- 减少动效:无泄漏;登录品牌演出按规定豁免。
- 读者面文本 ≥ 12px(豁免项除外);顶栏就地搜索与登录框 16px。
- 播放连续性:切 Tab、进出正文、手机 ⇄ 桌面跨版式都不中断。
- 返回键:正文页、发现页、合集详情、抽屉、动作单、设置栈三层都能关且不离站。
- 暗色:无亮色残块(播放钮浅底是有意反相);WebKit 26 排版与 Chromium 一致,`dvh` / `:has` / `color-mix(in oklab)` / 滚动时间线 / `linear()` 全支持。
- 翻译逐段骨架 → 原位替换在手机正常;速读卡、论文说明、播客收听入口、早报夜航手机档版式正常。
