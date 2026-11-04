# issue #114 运维管理信息架构:首页即目录 → 满幅功能页

> 状态:进行中(2026-11-04 开工;两轮交叉检视通过,待用户目检)。分支 `feat/issue-114-admin-ia`,worktree `~/Codes/worktrees/DoramiSourceArchive/issue-114-admin-ia`。
> 样页:[`docs/design/issue-114-admin-ia/`](../design/issue-114-admin-ia/)(v1–v4;实现以 v4 的 **A 精修** 为准)。
> 实现:Claude Code(Opus 5.5);检视:Codex(按 `docs/dev/workflow.md` 交叉检视,模型与轮次记在 §9)。

## 1. 拍板记录

| 日期 | 拍板 | 来源 |
|---|---|---|
| 2026-11-02 | R1「域 → 子页栏」样页否决:分得太碎、侧栏挤正文;改为**首页即目录 → 满幅功能页** | 用户目检 R1 |
| 2026-11-03 | v2:首页不等大 bento、功能页 10 → 8(读者 AI + 读者默认 → 读者功能;审计并回账户)、图表按 Carbon(逐日离散柱 / 增长阶梯线)+ 统一浮层 | 用户目检 v1 |
| 2026-11-03 | v3:三档磁贴解剖固定;重要度 **账户 > 阅读与榜单 > 其它**;**不要搜功能框**;首页零说明句 | 用户目检 v2 |
| 2026-11-03 | v3.1:去掉磁贴左下角板块链接行;播客构成改**环图**;P50 不用分数衬线体 | 用户目检 v3 |
| 2026-11-04 | v4 风格选 **A 精修**(标签在数字上方、KPI 列发丝竖线分隔、单系列柱强调末柱、Top N 名字压在淡色条上) | 用户目检 v4 |
| 2026-11-04 | **本波一起加只读聚合端点** `GET /api/admin/home`(突破 issue 原「不改后端」边界:逐日活跃账户、窗口内各源阅读排行、回填失败汇总) | 用户选择 |

样页里其它待拍板项,本波按下表默认处理(与现行纪律冲突处一律从纪律,偏离要用户再拍板):

| 项 | 本波做法 | 理由 |
|---|---|---|
| 首页图表首帧生长动画 | **不做** | conventions §7:工作区不加入场编排;要做需登记具名例外 |
| 切时间窗时数字滚动 | 做(≈ 400ms,减少动效直落) | 是对用户操作的反馈,不是入场编排 |
| 大号数字比例 / 等宽 | **等宽 `tabular-nums`** | conventions §3「计数一律 tabular-nums」;v4 样页的比例数字不改纪律 |
| 新字号档(L 主角 40 / L 次级 22 / M 主角 32 / S 主角 20) | 先进 conventions §3 角色表再落码(`.ops-kpi-*`) | §3「需要新刻度先改本表」 |
| 首页总闸 | 只读点,点击跳所在板块 | v3 已定 |
| 备份状态 | 状态点(绿点 / 红菱)而非 chip | 3 列 S 磁贴放不下文字 chip |
| 各源 Top N | 按磁贴高度放 5–8 条 | 端点 `top` 参数 + 前端按高度截取 |
| S 磁贴窄时收图标 | 容器查询(内容盒 < 270px) | 1280 下 S 磁贴放不下 |

## 2. 信息架构

两层:**首页**(`#/admin`)= 宏观数字 + 目录;**8 个满幅功能页**(`#/admin/<页>`),页内板块可深链(`#/admin/<页>/<板块>`,落位后短促 wash 高亮)。页头面包屑 `运维管理 › <页> ▾`,`▾` 弹层横切 8 页(按重要度排序)。没有搜功能框,没有二级侧栏。

| 页 id | 名称 | 板块(页内顺序;id 即深链末段) | 来自旧版 |
|---|---|---|---|
| `accounts` | 账户 | 账户增长 `growth`(含窗口 KPI 条)· 游客 `guest` · 活跃用户 Top `top`(根)· 账户管理 `table`(根)· 操作审计 `audit` | 用户档 |
| `reading` | 阅读与榜单 | 内容概览 `kpi` · 各源热度 `heat`(含收藏 TOP)· 榜单快照 `rank` | 内容档 |
| `reader` | 读者功能 | 每日用量 `usage` · 开关与默认 `switches`(用户 AI / 新账号默认开 / 模型 / 日预算 · 公开分享 · 读者点播)· 默认订阅 `defaults` · 早报与兴趣 `brief` | AI 档 + 内容档的读者策略 |
| `podcast` | 播客 | 概览 `overview` · ASR 配额 `asr` · 单集处理 `tasks` · 中文精简音频 `audio` | 内容档「播客」分区 |
| `analysis` | 分析与标签 | 分析指标 `metrics`(含分析开关与分布)· 完整分析回填 `backfill` · 标签目录 `catalog`(版本 + 总账) | 分析与标签档 |
| `external` | 存储与外部资源 | 存储与备份 `storage` · 媒体库 `media`(KPI + 热点图)· X API `xapi` | 内容档 |
| `user-sources` | 自定源 | 自定源 `sources` | 内容档 |
| `inbox` | 反馈与公告 | 反馈收件箱 `feedback` · 公告管理 `notice` | 消息档 |

注册表与解析以代码为准:`frontend/src/utils/opsNav.js`(`OPS_PAGES`;守卫 `frontend/test/opsNav.test.mjs` 断言每个注册板块在源码里都有 `opsBlockId` 锚点)。旧载荷兼容:设置柜「ASR 配额」回指原为 `{ sub: 'content', zone: 'podcast-asr-quota' }`,现直接跳 `#/admin/podcast/asr`;`normalizeOpsTarget` 仍把旧五档 `sub` 映射到对应页。

### 2.1 板块归属规则(新增板块按此落位)

1. **先问「这是谁的事」**,按对象归页:账户与游客 → 账户;读者看到的内容与榜单 → 阅读与榜单;读者能用的能力、默认与额度 → 读者功能;播客流水线 → 播客;读者自己加的源 → 自定源;存储、备份、媒体库、付费外部 API → 存储与外部资源;分析、评分、标签目录 → 分析与标签;读者来信与站内公告 → 反馈与公告。
2. **同页内按「观测 → 策略 → 台账」排序**:看板(KPI + 图)在上,开关 / 默认 / 阈值居中,表格类台账铺满在下。
3. **告警不进板块**:需要处理的事项只在首页「状态」磁贴出现一次(待处理列表),板块里只给状态点。
4. **全站总闸**在所属页的「开关」板块里可拨,首页只读展示。
5. 新页要过三问:首页磁贴放哪一档(L / M / S)、主角数字是什么、哪些数进 hover;答不上来就并进现有页。

## 3. 首页(A 精修)

- 12 列栅格、16px 缝;行 1 = 账户 L(7 列)+ 阅读与榜单 L(5 列);行 2 = 读者功能 / 播客 / 分析与标签 M(各 3 列)+ 状态(3 列,纵跨行 2–3);行 3 = 存储与外部资源 / 自定源 / 反馈与公告 S(各 3 列,56px)。行高随视口(行 3 固定,行 1 : 行 2 = 1.58 : 1)。
- L:单行页头(图标 + 页名;右侧时间窗文字 + 状态点)→ KPI 行(标签在上、数字在下,列间发丝竖线;主角列 1.55 份)→ 全宽图。M:页头 → 数字压在左下角(标签在上)+ 小图居右。S:一行。状态:待处理列表 + 8 个开关点。
- 图:账户 = 逐日活跃账户离散柱(末柱「今天」实靛、其余淡靛,顶端圆角 3,右侧两条刻度,X 轴只标首尾);阅读与榜单 = 各源 Top N(名字压在淡靛条上);读者功能 = 读者 / 系统两档同色相迷你柱;播客 = 阶段构成环图(环厚为半径 25%,中心「单集」总数);分析与标签 = 新闻价值直方图(靛色序数阶 + P50 标线)。
- 首页零说明句;被削的数进数字 / 页名 hover 浮层(与图表浮层同一套)。

## 4. 聚合端点 `GET /api/admin/home?days=N&top=K`

`services/admin_home.py` + `api/routers/admin.py`,测试 `tests/test_admin_home.py`,端点登记 `docs/reference/endpoints.md`。

- `active`:逐日活跃账户 `series[{day, accounts}]`(补零,长度 = days)+ 窗口去重合计 `accounts`。活跃 = 当天登录或阅读过;账户口径,排除 `guest:*` 与 `deleted:*`;登录事件按 `at` 前 10 位归本地日。
- `reads`:窗口内各源阅读 `total` + 排行 `by_source[{source_id, name, reads}]`(前 `top` 条)。含游客阅读,与 `/admin/content` 同口径,只多一个时间窗。
- `backfills`:全量分析回填 `active`(queued / running / paused)、`unresolved_failed`(最近一次成功之后结束于 failed / partial_failed 的任务数)、`latest`(最近一条)。
- 纯聚合、不含逐账户明细,**全体管理员可见**(与 `account-growth` 同类,不进 `root_admin_required`)。

## 5. 实现拆分

- **路由**:`App.jsx` 把 `admin` 加进 `SUBVIEW_TABS`,子视图 = 页 / 板块路径(空串 = 首页),`hashToRoute` 经 `parseOpsPath` 校验(未知页回首页、未知板块只留页);popstate 回到 `#/admin` 时写回空视图;已在功能页时再点左轨「运维」回首页。
- **外壳** `components/AdminOpsTab.jsx`(1603 → 约 190 行):页头(首页标题 / 功能页面包屑 `OpsCrumb`)、时间窗(跨页保留,只在用到它的页显示)、`refreshTick`(从其它 Tab 切回自增)、板块落位(视图带板块或收到 `pendingFocus` 时逐帧找锚点,约 1.5s 内找到即滚动、聚焦、wash 高亮)。
- **首页** `admin/OpsHome.jsx`:约 20 个只读请求各自代次守卫,一条失败只让那一块显示「—」;数据推导在 `utils/opsHome.js`(待处理 / 总闸点 / 窗口切片 / 读者与系统用途拆分 / 阶段构成 / Top N 回退)。图表 `admin/OpsCharts.jsx`(手写 SVG:逐日柱、阶梯线、排行条、环图、直方图、共用浮层、数字滚动)+ `admin/opsChartKit.js`(工具与 hook)。样式在 `index.css` 末尾「issue #114」段,全部 token、悬停包 `(hover: hover)`、位移动效挂 `data-motion`。
- **功能页**:`admin/pages/AccountsPage.jsx`、`ReaderPage.jsx`、`ReadingPage.jsx`、`ExternalPage.jsx` 自旧文件**原样迁出**(状态、loader、写操作与 JSX 不改逻辑,只加板块锚点与改一处文案「AI 子页」→「读者功能页」);播客 / 分析与标签 / 自定源 / 反馈与公告直接挂既有面板,`PodcastZone` 与 `AdminTaxonomyPanel` 内补锚点(播客区头改称「概览」)。切页即重挂 = 重取,与旧版切档语义一致。
- **后端** `services/admin_home.py` + `GET /api/admin/home`(§4),测试 `tests/test_admin_home.py`。
- **顺修**:`PURPOSE_LABELS` 补 `event_assignment`(事件·归并,守卫比对后端 `VALID_PURPOSES`);`.knob.is-fixed strong` 显式 600(preflight `bolder` 推成 900);`.ai-switch-lbl` / `.knob-field > span` 不折行;X API 超预算 KPI 的 tone 由 `bad` 改 `is-bad`(此前不变红);媒体热点图等统计确认媒体库开启后再挂(未启用时端点 503);旧 IA 文案(设置柜「运维管理 → 内容」、自定源提示)改为新页名。

## 6. 自测与验收

- 后端:`tests/test_admin_home.py` 4 项(活跃按天去重与排除、窗口阅读含游客、回填未解决失败、端点形状与门控 / days 收口)。
- 前端:`npm test` 全过(新增 `opsNav.test.mjs` 7 项、`opsHome.test.mjs` 8 项;`podcastAsrQuota.test.mjs` 改为新路由);`npm run lint`、`npm run build` 通过;守卫反向对照:删掉「事件·归并」标签,用途覆盖测试即失败。
- 沙箱(真实库副本 + 8089 / 5174,模型地址改死、采集任务停用,补 45 天登录 / 阅读 / AI 用量示意数据):首页 1440×900 / 1280×800 全部 9 块磁贴完整可见、无横向滚动、字号 ≥ 12px、字重只 400 / 600、控制台零报错;亮 / 暗;根 / 非根(非根账户页只见聚合);8 页面包屑名正确;`#/admin/podcast/asr` 深链落位并聚焦;面包屑 ▾ 列 8 页、点选换页、「运维管理」回首页。截图在 worktree `tmp/issue-114/impl-shots/`(不入库)。

## 7. 遗留与不做

- 首页图表首帧生长动画(见 §1 表)。
- 读者功能迷你柱在 90 天窗口下偏密(一根约 2px)。

## 8. 文档同波

version-history / backlog / decisions;conventions §3 新字号档;`docs/architecture/admin-ops.md` 改写信息架构一节;`docs/design/README.md` 登记样页;R1 未跟踪样页 `dorami-admin-ia-quiet.html` 删除。

## 9. 检视

- 实现方:Claude Code(Opus 5.5);检视方:Codex CLI 0.153.4,`gpt-6-astra` + `model_reasoning_effort=ultra`,`codex exec` 只读沙箱;任务书、报告、表态与修复清单在 worktree `.review/`(不入库)。
- **第 1 轮**(检视 `2b6bc4bf`):3 项 P1 + 6 项 P2,**全部接受、无分歧**,修复提交 `c7ff8a85`:
  1. P1 切页重挂绕过「新账号默认订阅」的串行写保护(整集替换写可互相覆盖)→ 模块级在途写,新实例等它落地再取名单(不改成页面常驻:常驻会让账户页抽屉的 body 滚动锁跨页残留)。
  2. P1 回填「未解决失败」按 id 判先后,漏报旧任务重试后的新失败 → 按 `updated_at` 比较。
  3. P1 首页切窗失败仍显示旧窗口数据、告警未读到时显示「无待处理」→ 窗口类快照带查询窗口;告警来源读全才下结论;失败给「N 项读取失败」条件性小章。
  4. P2 `#/admin/constructor`、`__proto__`、坏的百分号编码会抛错 → `Map` 查找 + 解码兜底。
  5. P2 深链落位请求在之后普通进入该页时重放 → 普通导航清空、已落位不重放。
  6. P2 登录窗口过滤 `substr(at)` 绕过时间索引 → 原列范围过滤。
  7. P2 排行条首次空态未挂尺寸观察器 → `useBox` 改 callback ref。
  8. P2 待处理多时被裁 → 列表内部滚动。
  9. P2 关闭态开关名称被浮层定位规则左移 → 定位规则限定 `:not(.is-off)`。
- **第 2 轮**(复检 `c7ff8a85`,最后一轮,只对照修复清单):9 项全部「已修」,未发现修复引入的 P1,无新增观察;Codex 定向复验前端 20 / 20、后端 5 / 5。
- 观察(未修):落位发生在板块上方内容装载前时,内容随后撑高会让落点下移。
