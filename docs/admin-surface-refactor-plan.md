# 运维面「播客」与「分析与标签」重构(issue #76)

> 状态:◉ 活跃(实现已落地,待 codex 复检 + 用户本地验收)。
> 样页:`docs/design/dorami-admin-podcast-taxonomy-quiet.html`(拍板依据,含 2026-09-15 目检返修)。
> 起因:播客(#43–#46)与标签(v3.44)两波由 codex 开发的管理面,与 2026-09 拍板的管理面纪律
> (`docs/frontend/conventions.md` + `docs/admin-usability-audit.md` 整改记录)存在规范性 / 一致性漂移。
> 检视方式:Claude 与 codex 各自独立检视后归并(31 条:P1×10 / P2×12 / P3×9),再由样页拍板落码。

## 1. 拍板记录(2026-09-15,用户)

| # | 决策 | 结论 |
| --- | --- | --- |
| ① | 「标签」子页信息架构 | seg 改名**分析与标签**,页内两区「分析链路」(开关 / 指标 / 分布 / 回填)与「标签治理」(版本 / 统一总账);早报两旋钮 + 兴趣目录 Top N 迁到「内容」子页新立**「早报与兴趣」**区(它们是 `personal_digest_*` 分发策略,不是分析 worker 或目录的生产状态);不加第六个 seg。 |
| ② | 标签 / 候选列表形态 | **一张表 + 抽屉**;不设独立编辑列,点整行任意位置开抽屉,行内「可选」开关阻断冒泡。 |
| ③ | 早报策略旋钮形态 | **表单卡**(各卡一枚 secondary 保存,零说明句);播客优质门槛行同取显式「保存」。 |
| ④ | 回填默认档 | `missing_or_outdated`(仅缺失或版本过期);`all` 留作显式强制档(估算 + 确认)。 |
| ⑤ | 页头时间窗 | **内容子页隐藏**(三个数据源都不接时间窗参数,媒体热点图自带年份切换,页头时间窗在该页零作用);其它子页只写「时间窗」,不加引导语。 |
| ⑥ | 音频表节目列 | 后端补 `episode_title` / `source_name`(接口卫生,本波纳入)。 |
| ⑦ | 播客两面板落位 | 内容子页立「播客」独立分区:KPI 六格 → 处理参数开关板 → 单集处理表 → 中文精简音频表,三处整行可点开同一个**单集抽屉**。 |
| 目检返修 | 判定拆回两层 | 「阶段」章(未初评 / 未入选 / 待全文 / 处理中 / 全文完成 / 待对账 / 失败)+ 章下一行原因;「判定」章只表优质线(优质 / 未达门槛 / 未终评);⋯ 详情钮撤除,整行开抽屉;两表卡头「单集处理」「中文精简音频」标明职责,「安全回收」归音频卡头。 |

## 2. 落地面

### 前端(`frontend/src/components/admin/`)

- `PodcastZone.jsx`(区级编排:四组数据各自 loading / error / data、活动态 3s 静默轮询、动作 + 确认)
  + `PodcastTasksTable.jsx`(单集处理:节目搜索 / 阶段·判定·TTS 轮换筛选 / 评分·更新排序 / rowact)
  + `PodcastAudioTable.jsx`(中文精简音频:节目搜索 / 状态筛选 / 大小·创建排序 / 试听·发布·下架·删除·安全回收)
  + `PodcastEpisodeDrawer.jsx`(评分与判定 / 处理时间线 / 产物 / 逐字稿 / 强制全文·强制 TTS);
  退役 `PodcastPremiumGuidesPanel.jsx`、`PodcastArtifactsPanel.jsx`。
- `BriefInterestZone.jsx`(内容子页「早报与兴趣」:重大事件通道 / 订阅外兴趣 / 兴趣目录 Top N 三张表单卡)。
- `AdminTaxonomyPanel.jsx`(重写为两区)+ `TaxonomyLedger.jsx`(统一总账表 + 标签抽屉 + 候选抽屉 + 新建标签 form-sheet)
  + `FullAnalysisBackfillCard.jsx`(一行参数 + 任务表,`role=progressbar`,rowact,表脚诚实计数)。
- 共享原语:`Kpi.jsx`(`Kpi` / `KpiState`,四份私有 KPI 复制合一)、`Pager.jsx` 新增 `TableFoot`
  (「共 N 条 · 第 a–b 条」)、`TableTh` 三件套复用。
- 归一层:`utils/podcastProcessing.js`(阶段 / 判定 / TTS 的规范化谓词、章文案与 tone、可用动作、审计 reason、
  成功 toast——面板 / 台账 / 抽屉 / 命令组装同源;`utils/podcastFullAnalysis.js` 与 `DataTab` 改吃它)、
  `utils/taxonomyLabels.js`(status / kind / alias_type / entity_type 的 label + tone,未知值「未知状态(raw)」)。
- `AdminOpsTab.jsx`:seg 改「分析与标签」+ `role=tablist/tab/aria-selected`;内容子页隐藏时间窗;内容 KPI 独立错误态
  (失败只让那一条变脸,其余分区照常);`refreshTick` 透传到 分析与标签 / 播客 / 早报与兴趣 / 自定源;
  X API / 媒体库 zone-hint 说明句撤除。
- `api.js`:`request()` 网络层失败统一为「对象 + 原因 + 下一步」(不再裸露 Failed to fetch);删除零引用的
  `fetchPodcastPremiumGuides` / `runPodcastPremiumGuide`;`getPodcastAsrQuota` / `savePodcastAsrQuota` 归一为
  fetch / update 命名;新增 `fetchPodcastPremiumTaskDetail` / `fetchTaxonomyLedger` / `fetchCmsTag` / `fetchCmsTagCandidate`。
- `index.css`:退役 `.taxonomy-*`(flag / row / alias-chip / version-stat)与管理面专属 `.podcast-assets-*` /
  `.podcast-premium-*`(读者面 `.podcast-premium-badge` / `.podcast-guide-*` 保留);新增未分层区一组
  (`.knob*` / `.tbl-head` / `.table-foot` / `.cell-2` / `.score-pair` / `.tag-kind` / `.prog*` / `.timeline` /
  `.alias-chip*` / `.acct-sub` / `a.rowact-btn:focus-visible` 等),刻度与样页 1:1。

### 后端接口卫生(不改语义,旧参数与响应键全部兼容)

| 端点 | 变化 |
| --- | --- |
| `GET /api/admin/podcast-premium-tasks` | 新增 `q`(节目 / 来源 / id 子串)、`stage`(7 档规范化阶段,待对账独立)、`verdict`(premium / below_threshold / unscored)、`tts`(not_started / active / ready / failed)、`sort`(publish / score / updated)+ `order`;item 补 `stage_code` / `verdict` / `updated_at` / `publish_date` / `processing_error`;响应补 `breakdown{stage, verdict, tts, shows}`;旧 `status` 档位保留可叠加。 |
| `GET /api/admin/podcast-premium-tasks/{episode_id}` | 新增:单集抽屉载荷 = 任务行 + 六步处理时间线(状态 done / run / fail / warn / pending / skipped,时间戳只取记录里真有的)+ 文本产物 + 精简音频列表。 |
| `GET /api/admin/podcast-artifacts` | 新增 `offset` / `q` / `sort`(created / size / published)/ `order`;响应补 `total` / `offset` / `limit`;每行补 `episode_title` / `source_name`。 |
| `GET /api/admin/taxonomy/ledger` | 新增:规范标签 ∪ 候选 统一总账,`type` / `kind` / `status` / `q`(含别名)/ `sort`(hits / name / updated)/ `offset` / `limit`;行只带轻字段(近 7 天按 `fetched_date` 的文章数 / 来源数、别名数 / 证据数、相似项名);响应 `total` + `counts{tags, candidates}`。 |
| `GET /api/admin/cms-tags/{id}`、`GET /api/admin/cms-tag-candidates/{id}` | 新增:抽屉详情(别名全量 / 证据)。 |
| `GET /api/admin/analysis/backfills` | 新增 `offset`,响应补 `total` / `offset` / `limit`。 |

服务层:`services/podcast_premium.py` 抽出 `_load_states` / `_serialize_state` / `_stage_code` / `_guide_status`,
新增 `episode_detail`;`services/podcast_artifacts.py` `list()` 支持 offset / q / sort + `count()`;
`services/analysis_backfill.py` 新增 `count_full_analysis_backfills`。测试 `tests/test_admin_surface_refactor.py` 7 项。

## 3. 验收

- 隔离栈 Playwright(临时 ini + 播种沙箱库,`VITE_PROXY_TARGET` 指向临时后端):内容页无时间窗、
  `role=tab` 五枚、三张表表头 36px / 行 46px、列头搜索展开表头零跳变、阶段轮换筛选与 KPI 下钻生效、
  整行开抽屉、行内动作(试听 / 可选开关)不冒泡、单集抽屉时间线六步、标签 / 候选抽屉分区、新建标签 sheet、
  暗色;`pageerror = 0`。
- 合入前仍需用户本地端到端验收 + 目检放行(CLAUDE.md 协作流程)。

## 4. 检视记录

- **R1(2026-09-15,落码前)**:Claude 与 codex(gpt-5.6-sol)各自独立检视两波前端(21 / 20 条),一轮沟通即收敛为
  31 条(P1×10 / P2×12 / P3×9)+ 撤回 2(全局 CustomEvent 是仓内既有模式;阶段章不加 `role=status`)+ 拍板 7;
  信息架构采纳 codex 修正版(早报策略归内容子页),多 primary 定性改为 §4 accent 预算。
- **R2(同日,落码后复检,PR #88)**:codex 逐条核对 31 条 + 拍板记录,无契约不兼容;2 条 P1 返修全部接受并修——
  ① 总账「可选」开关只靠颜色 → 沿账户表 AI 列先例改 `Zap / ZapOff` 形状对 + 色(不改行内 switch:样页拍板为小图标,46px 行里
  34×19 开关重一档);② loader 保留旧快照后刷新失败被静默 → 新增共享 `admin/StaleNotice.jsx`,旧快照存在时在区头 / 卡头 /
  表头就地显示「刷新失败 · 原因 · 重试」(404 同形「未接入」),覆盖播客两表、播客区头、分析链路与标签治理区头、标签总账、
  早报与兴趣、回填卡。观察期项顺手做掉:音频表副行改「来源名 · 节目 ID」(拍板⑥收口)、错误 / 确认文案去 `Alias / taxonomy v1 / bootstrap`、
  `.drawer-sec-title` 12 → 12.5px、前端 `node:test` 卫生(删读取已退役面板的用例,`podcastTtsStatusMeta` 随之退役,
  `/retry` 路径审计 reason 恢复按阶段措辞,新增 `podcastProcessing.test.js`)。
  **观察期(记 backlog)**:`DataTab` 行章与阅读抽屉仍用 `utils/analysis.js` 两套投影(下一波让旧投影成为归一层适配器);
  `handleApiError` 对非 JSON HTTP 错误补状态类别与下一步;标签总账端点 Python 侧合并排序再切页(目录百级可接受,显著增长再评估)。
- **R3–R5(同日,收口复检)**:R3 判 R2-P1-2 仍有两处缺口——播客 / 回填 / 总账 loader 未把 404 映射为「未接入」、回填卡按
  `items` 非空而非「有过快照」挂载提示;两处接受并修(`ebc0845`:`useLoader` / `loadJobs` / `load` 统一 `404 → unavailable`,
  无快照整块态经 `StaleNotice` 分「刷新失败(可重试)/ 未接入(不给重试)」,回填卡改 `jobs.loaded`)。R4 又判播客两表无快照态缺
  `unavailable` 分支,与磁盘代码不符(是压缩前旧上下文),按协商式流程给证据请其从磁盘重读(R5)——两处确认已闭环,**结论「可合入」**。
  合并 `origin/main`(v3.57.2 + CLAUDE.md 瘦身)时 CLAUDE.md 冲突按新纪律解决:年表只加一行 + KDD 一段,详细记录入 `docs/version-history.md`。

## 5. 有意边界

- 播客单集抽屉的时间线以现有记录推断阶段状态(stage attempts / processing / guide / artifact),不新增事件表;
  没有时间戳的步骤不画时间。
- 标签总账的「近 7 天」对规范标签按指派表现算(`ArticleTagAssignment ⋈ Article.fetched_date`),候选取自身快照字段;
  两者口径相近但不完全同源,列头不做跨类型精确比较的承诺。
- 回填任务表只取近 100 条并本地筛选状态;超出时表脚如实注明「仅载入最近 N 条(共 M 条)」。
- 音频「安全回收」仍是区级动作(归音频卡头),不进抽屉。
