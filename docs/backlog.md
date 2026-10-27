# 待办栈(Backlog)

> 性质:**跨波次的待办总账**——「进行中 / 排队中 / 展望」三档,动工时在此标注并链接方案文档。
> 波内逐项 ☐ 以各方案文档为准;已完结波次的执行记录在 `docs/archive/`(索引见其 README)。
> 建立于 2026-07-19(用户指示:待办栈落文件)。

## 进行中

- ☐ **备份与 TTS 紧急回收的不变量冲突**(issue #231 发现,2026-10-27):#143 的紧急回收(缓存超限)会删除哈希核对过但 zlib 压不小的 WAV、以已发布制品为持久副本,而备份要求每条 `succeeded` 都有本地音频(`.wav` 或 `.wav.zlib`),真发生时每日备份又会 fail closed。待拍板:备份是否认已发布制品(`podcast_artifacts` / OSS)为替代副本,或紧急回收不删只压。
- ◇ **AI 用量看板的未登记用途**(2026-10-26 守卫用例发现):`article_ondemand_narration` / `podcast_premium_blog` / `podcast_premium_narration` 已于 2026-10-27 按用户拍板登记(只补 token 可见,读者次数仍按 `*_ondemand` 计);剩 `taxonomy_bootstrap`——CLI 入口未装用量 recorder,登记也不入库,要计量须先给 CLI 装 recorder,暂记观察。
- ☐ **issue #121 + #103 事件层**(方案 `docs/plans/issue-121-event-layer.md`,调研报告同目录):**wave 1 纯后端已合入 main(PR #220,2026-10-23)**;**wave 2 早报同事件去重(#103)已合入 main(PR #222,2026-10-24)**(方案 §5.3):事件键跨三条通道、昨日事件整组抑制(头条升级例外)、「另见 N 篇」、说明行计数、日报事件层预聚类;本波不做 story「接前报」链接与官方多形态发布关联(随 #221 拍板)。精选通道代表规则(官方 > 非社交 > 分数 > 更早)已随 PR #224 补齐。**生产 14 天回放已做(2026-10-26,方案 §13)**:#103 点名案例全部正确、误并率 2–7%、每日 ≥2 主体事件中位 6、早报机械层前重复率 32.9% → 0;36 / 48 / 72h 对比后 48h 维持。后补已合入(PR #226):汇编体裁不入事件层、早报跨期抑制与文章排除看前两期;**#103 已关闭(2026-10-26)**。观察期:「低分官方代表拉低整事件排名」;辅助档 token 实测约 5.3 万 / 天。#121 已关闭(2026-10-24);wave 3 = Agent / MCP 事件查询 → issue #223。wave 1 记录:后端经 Codex 四轮检视返修;**前端「热点」段搁置**(2026-10-23 用户目检三方向样页后:都待商榷、B 较优非定稿,随之后文章榜 / 播客榜 / 热点前端统一重构再出,样页留档 `docs/design/issue-121-hot-events/`)。本波有意不做:LLM 事件签名、纯向量聚类、日报聚类持久化为事件层、sqlite-vec、事件级综合摘要、外部 embedding API(再评估触发:一中一外同事件漏并率明显高于译中标题通道 top-10 召回 0.68)。观察期:story / follows 跨页补链(wave 2 填链时实现)、`event_board._recompute_event` 与 `event_assignment._recompute_events` 收口为一处、生产供给(每天 ≥ 2 主体事件数,本机语料中位 4 件是下界)、首篇关窗 36h 是否再提精度。wave 2 = #103 早报去重;wave 3 = Agent / MCP 事件查询。
- ☐ **issue #221 榜单前端统一重构**(2026-10-23 立项,排队中):文章榜 / 播客榜 / 热点三段一致化,并重审文章榜的定位与展示内容;热点段的事件卡样页(方向 B)作为起点。后端 `GET /api/reader/events` 已就位。
- ☐ **issue #217 媒体修复观察期**（方案 `docs/archive/issue-217-decisions-images.md`，PR #219）：相对地址解析与 OpenAI 受限静态图浏览器取图已实现，检视和用户目检通过；本地加近期生产历史共 106 张真实取图全部成功，211 篇 OpenAI 文章仅本篇出现同类地址。不做全量回填、实时语音接入。观察期：JSON 行号粘连、源站检查变化、非规范 URL 标准化差异、量增后的引用扫描开销，以及浏览器极端卡死时清理阶段无独立硬超时。两端部署先 replica 后 authority，防旧 GC 误回收相对图媒体。
- ☐ **历史 HTML figure 图片转换**（#217 回测发现）：Lilian Weng《Reward Hacking in Reinforcement Learning》两张相对 PNG 已可取回，但旧正文 HTML `<figure>` 在阅读器显示字面标签。另行评估提取 / 读取时的安全 HTML→Markdown 转换；不直接放开任意 HTML。见 [回测记录](./archive/issue-217-decisions-images.md)。

- ☐ **issue #210 文档整理波**(分支 `feat/issue-210-docs-tidy`,方案 `docs/plans/issue-210-docs-tidy.md`;2026-10-21 用户拍板 D1–D10 全按推荐):
  L0 改为 `AGENTS.md` 正文 + `CLAUDE.md` `@AGENTS.md`;CLAUDE.md 的 Key Design Decisions / 目录树 / 端点 / 测试 / 迁移 / 配置 / 年表原文迁入 `docs/{architecture,reference,dev,ops}/` 与 version-history;
  28 篇完结方案归档 + `specs/` `artifacts/` 归档 + 零引用样页归档;新建 `decisions.md`、`docs/dev/workflow.md` 等、四枚 `.claude/rules/`、三枚项目技能、守卫 `scripts/check_docs.py`。
  待:Codex 本地检视 → 用户目检 → 合入;合入后本机记忆按方案 §3.6 迁移(保留 11 / 缩短 11 / 删除 18)、个人技能三枚;下一次 tag 同步 master 时在 master worktree 手工合并一次 rename 冲突。
  有意不做:归档文档内容不改写(只加归档注);`docs/sources/` `contracts/` `frontend/` 的既有文档不补状态行(守卫只对 plans / architecture / dev / reference / ops 新文件强制);未跟踪的 `scripts/netcheck.*`(内网专属,归 master)与两页未立项样页不入库。
- ☐ **issue #199 前端细项优化波**(分支 `feat/issue-199-frontend-polish`,记录见 `docs/version-history.md`):撤回源栏每源未读数、取缔条目未读小蓝点、播客区重设计为全局播放条(样页 `docs/design/podcast-player/`)三项已实现并经用户目检,待 Codex 本地检视 → PR 合入。
  遗留(本波有意不做):Windows 实机看一遍播放条(只做了理论判断);播客导读生成完成后入口要等约 30 秒的分析轮询才刷新;生产里封面代理 302 回源成跨域时回落中性底的路径未端到端;部分播客节目简介里链接文字独占一行——库里存的简介原文即如此断行,是抓取时 HTML 转文本的问题(Interconnects 等 Substack 源可见),与播放器无关。
- ☐ **issue #212 文章分析提示词按类型装配**(RFC,未开工):规则已判定 `kind_hint` 时只发该类型的内容价值标准(通用段照发),判不出才发全套;`article-analysis-v8`,尺子版本号不动;门禁 = 74 篇内容价值黄金集 + 62 篇新闻黄金集不劣于 v7;近 7 天约 700 篇随巡检重跑。
  有意不做:每类一套提示词;论文专用提示词等 3c 定下论文的新闻维度后一并评估。
- ☐ **issue #178 内容分类与双评分**(方案 `docs/plans/issue-178-content-classification.md`,样页 `docs/design/dorami-classification-scores-quiet.html`):
  第一波已合入(#180):内容类型 / 事件类型读取时推导 +「动态」并入「文章」+ 类型签 / 筛选带 / 紧凑行 + 词表收口。
  第一波补已合入(#181):`category` 拆出 `admission_status` 后退役、源级 `content_tags` 退役、条目标签不再拼入源级分类。
  第二波(分支 `feat/issue-178-content-value`,方案 §3):文章提示词 v7 产出内容价值与内容类型、评分卡新交互、回填「仅从未分析」
  「补内容价值」。**已上线**(v3.64.0,2026-10-13):「仅从未分析」6,394 篇与「补内容价值」763 篇都已跑完、全部成功,
  用量与费用记录见方案 §5.5。
  第三波 3a(分支 `feat/issue-178-distribution`,方案 §5):早报「深度阅读 / 播客精选」、播客退出新闻通道、日报论文名额改看内容类型与内容价值。
  已随 v3.64.0 上线;三个门槛按生产只读聚合核对后都不动(方案 §5.5)。**观察期已于 2026-10-21 收口**(方案 §5.5「观察期复核」:外网只读统计 + 30 位 mock 读者回测):日报论文门槛维持 7.0 定案;深度阅读维持 7.0 / 每源 1 篇(放宽会把同源几篇集中到一天,空板率 42% → 50%);播客精选建议 7.5 → 7.0 试一周(KV,待用户在内网改);
  外网账号不是真实读者,真实读者的两分区数据等内网升级 ≥ v3.64.0 后一周再用同一脚本看。
  排队:3b 播客提示词拆分与门控改读内容价值(依赖 #97 播客黄金集,按切换日生效);3c 新闻尺子删「非新闻体裁」条款(暂缓,3a 上线后再评估)。
  有意不做:观察期只读统计与 mock 回测脚本不入库(执行者个人技能目录,内网可原样跑);公共日报分类字符串(神灯契约,媒体研究报道仍标「学术论文」但不占名额,改标签需先与神灯侧确认);官方观点类主分特例;
  feed / MCP 的内容价值字段(有下游需求再加);文档站「看版本更新」配图未重拍;早报页配图未重拍;「隐藏版本更新」的记忆选项。
  后续结构清理:`source_configs.category` / `content_tags_json` 两列已不读写,物理删除需重建表并重装归档同步触发器,攒到下次结构清理一并做;
  归档同步 sources 流的线路兼容键 `category`(由准入状态派生)在不再支持 issue #178 之前的旧版本对端后删除。
- ☐ **issue #184 视觉精修波(含 #176)**(分支 `feat/issue-184-visual-refine`,方案 `docs/archive/visual-refine-plan.md`):实现完成,
  待 Codex 本地检视 → 用户 Mac 目检 → Windows 按 #176 矩阵实机验收(Win10 / 11 × Chrome / Edge × 100 / 125 / 150% × 亮 / 暗)→ 合入后关 #176。
  后续(本波有意不做):早报条目配图缩略图(早报接口要给首图与尺寸)、`text-autospace` 评估、尾区「阅读器按钮字号」并回层内、
  管理面死类清扫(`.field-box` / 来源目录族 / `.scope-toggle`)、e2e `mobile` 流程本机 `ERR_ABORTED` 基线问题(main 上同样失败)。
- ☐ **迁移环境的归档同步触发器重装不生效**(2026-10-12 第一波补盘点时发现,origin/main 上同样复现):`alembic/env.py` 跑迁移前删光
  43 个同步触发器、跑完按理重装,实测升级后为 0,直到启动时 `DatabaseStorage` 的 `create_all` after_create 钩子才补齐。生产是
  「迁移 → 启动」顺序且中间无写入,所以会自愈,但只跑迁移不启动(运维脚本、离线升级)的库会缺触发器。疑点:重装语句所在的事务
  在 `transaction_per_migration=True` + 手动 `BEGIN IMMEDIATE` 下没有提交。

- ☐ **issue #126 裸机部署回滚**(分支 `feat/issue-126-baremetal-rollback`,方案 `docs/archive/baremetal-rollback-plan.md`;2026-09-21 用户拍板 §7 七项全按推荐后实现):
  运行副本版本化 release 形态 + 事务阶段 + 两级健康门告警(不自动回滚)+ `--rollback`(DB 按迁移计划分流,`--restore-db` 显式恢复)+ 收养 + `--code` / `--status` / `--discard-txn`,
  `docker/requirements-crawl4ai.txt` extras 钉版,锁与 Docker 同一把;桩测试 `tests/test_deploy_baremetal.py`。待 codex 检视、用户本地(内网)实机验收:收养一次 → 部署新版 → 故意起不来的提交告警 → `--rollback` → 机器重启 resurrect 起当前 release → 带迁移版本回滚被拒 / `--restore-db`。
  观察期:内网 `--here` 场景的收养(旧 `/api/health` 须透出构建身份)、`[nginx] releases_dir` 是否需要(仓库在 /root 下时)。
- ☐ **issue #74 个人早报三处**(分支 `feat/issue-74-brief-grid`,方案 `docs/archive/personal-brief-grid-and-sections.md`):
  公共日报剔出早报范围 + 板块固定顺序 / 板块内分数降序 + 分值驱动网格(策略 F,样页 `docs/design/dorami-brief-grid-quiet.html`)
  已实现;待用户本地验收与 codex 检视。观察期:T = 9.0 / Δ = 1.0 两个常量在生产分布下的通栏频率与 2 + 2 出现率。
- ☐ **issue #69 图片理解波**(分支 `feat/issue-69-vision`,方案 `docs/archive/image-understanding-wave-plan.md`):
  首波已实现 `vision_model` 档位 + 配图识别并入分析 / 日报 / 问答;待用户本地验收与 codex 检视。
  展望(未动工):Archive Sync 加 `image_insights` 流(内网检索档 cached-only 拿不到说明)、阅读窗图片下「图片文字」
  可展开(读者面直接受益,含表格译文)、`ocr_text` 进 FTS(需评估索引噪声)、按源开关识图(推文截图多的源优先)。
- ☐ **issue #33 个人早报产品优化波**(v3.50.x):§2 重大事件通道已落地(v3.50.0,方案
  `docs/archive/personal-brief-breaking-lane-plan.md`,观察期看实体目录覆盖/月均头条数/同实体抑制误伤);
  §5 兴趣/订阅变更不触发重编已落地(v3.51.1,`docs/archive/personal-brief-rebuild-entrypoints.md`);
  §4 中文标题已落地(v3.52.1,`docs/archive/personal-brief-title-localization.md`);
  余下 §3 页面「个性化/智能化」(样页先行,把 §5 的过期提示行并入编排说明头)。


## 排队中(用户拍板、未动工)

- ☐ **issue #132 智能管家**(2026-09-24 用户确定 Clowder AI;[R1 设计方案](./plans/issue-132-intelligent-butler.md)):
  Mac mini 常驻专职 agent;Dorami 现有 API / 日志 / 只读聚合 + 云监控 / X 余额直连;接入与日常运行不改 Dorami 后端。
  首期 = 宿主与渠道接通 + 可核对日报 / 预警 / 投递及外部失联兜底;后续反馈归并 → beta 自愈 → 小 bug 修复 / 独立复核 / PR。已完成设计稿,尚未安装部署。
- ☐ **issue #94 文章全局搜索**(2026-09-21 立项,复用旧 issue;worktree `feat/issue-94-global-search` 自 3b6e4b9):一稿样页 `dorami-search-scope-quiet.html`(未入库)与七条拍板项
  (推荐二段「当前 | 全站」、不跨容器、后端零改动——`GET /api/articles?search=` 对读者会话传 `subscribed_scope=off` 已能取全站,缺口只在前端三处入口 `useReaderState.buildListFilters`)**待用户逐条拍板后再实现**。
- ☐ **issue #82 节点看护机制**(三层:判定升级 → 异常通知 → 自主自愈):第一层方案 `docs/plans/issue-82-node-watchdog-layer1.md` R2 稿
  (2026-09-15 codex 检视一致;**2026-09-19 用户拍板「全按推荐」**,§5 七点定案;PR-0 已开工);落地拆 **PR-0 调度重载现役 bug 先修**(`load_tasks_to_scheduler` 全量 `remove_all_jobs()` 被采集任务 CRUD 调用,
  留存清理 / 远程同步 / 用户源刷新 / 播客 ASR worker 编辑一次任务即消失到重启)→ PR-A schema 与上报通道(判定 feature-gated 关)→ PR-B 开判定 → PR-C 收据对账 / Q1 / 日报运行表 / 看护页。
  第二层通道待拍板(SMTP / IM / Bark·ntfy / 站内告警条),凭据进 credentials 层;第三层与「系统自主化」大波次合并立项。
  L1 明确不做:持久时间改 UTC(独立波次)、interval 任务收据、`silent` 影响早报选篇。
- ☐ **管理面看板余项**(issue #31,v3.55.0 只做了两点:用户明细仅根管理员 + 账户增长曲线,
  方案 `docs/archive/admin-root-admin-and-account-growth.md`):曲线上标注版本发布等关键节点(需启动时登记版本首见日期
  或手工标注表)、文章量/采集量/分析量统一为「当前值 + 增长趋势 + 历史曲线」、根管理员转让/多根(升级为账户列)。
- ☐ **文章分析 / Taxonomy / 个人早报首发后的规模化与二期边界**(PR #6 交叉检视中
  明确不阻断首发的余项):①读者 AI 预算从当前软闸升级为 DB 原子预留/核销/释放；
  ②`full_analysis` 估算和历史范围改为游标/批次查询，避免一次载入全部文章；
  ③灵活展示标签建立 `(normalized_label, article_id)` 倒排表，替代读者筛选时全表扫描；
  ④为 `tagging_status=failed` 增加不重复调用模型的独立修复队列；⑤治理统计和 Candidate
  evidence 做有界分页，且定向重标真正执行 `tag_ids` 范围；⑥含凭证自定 RSS 如需开放
  MaaS，先设计逐订阅者授权、撤回和费用归属（当前默认硬禁用）。

- ☐ **管理面审计余项**(docs/archive/admin-usability-audit.md,P0/账户 V2/列表规模化/数据生命周期
  四波已收口,负责人拍板余项暂缓):M18(核心运维数据导出)、M20(批量开户/邀请流)、
  M24(移动管理,显式产品边界)、M25(FTS 大结果集,随语料量);
  另 v3.42 列表规模化波的刻意裁剪:反馈批量流转/负责人/优先级、FetchRunsTab 完全服务端分页
  (现为「过滤/时间窗下沉 SQL + 500 上限 + total 诚实提示」折中——父子聚合展开模型下
  完全分页需子运行 lazy 化,规模再涨时做)、FetchTab 节点板文本搜索;
  v3.43.1 交叉检视残余:AI 用量按用户图的「其它」桶已改冒号 sentinel + 前端渲染层映射,
  真实用户名恰为「其它（用户）」的 identity 碰撞概率近零——彻底解法是图表层
  identity/display 分离(MultiSeriesArea 加 displayName formatter),需要时做;
  严格 `role≠all` 隔离部署不作为当前生产拓扑；双节点均为 `all`，自定 RSS 归内网采集分析;
  读者 AI 配额/预算是**软上限**(v3.43.2 拍板):成本闸 check 与计量落库之间存在
  并发窗口,恶意并发可短暂超出日限——硬上限需 DB 原子预留-核销-释放并下沉到 LLM
  调用层按 UsageMeta 统一执行,重构面大而内网收益低,滥用真实出现时再做。

- ☐ **问答流式输出(SSE)**(阅读面 AI 打磨波 v3.32 遗留,拍板挂起):
  `chat_completion` 增流式通道 + ask 端点 SSE 化,现代对话手感的最大增量;
  本波以阶段化等待态(ask_id 进度轮询)过渡,体验已可接受故不急。
- ☐ **阅读器可发现性波余项**(v3.45,issue #9 三处结构性修复已落地,以下刻意未动):
  移动壳正文页顶栏译钮是否同步下沉标题下(拍板桌面先行,移动端顶栏已是「查看来源/译」
  图标组,看桌面反馈再定);一次性「阅读器导览」层(拍板本波不做——v3.22 欢迎卡曾因啰嗦
  被拆、v3.44 首登已有兴趣引导,前三条落地后反馈仍在再补,须可跳过、可在设置柜重看);
  度量:翻译用量基线(`ai_usage` translate 在有 AI 权限读者中的使用占比,上线前后各看一次
  即知修没修对),跳原文无埋点、本波未加。
- ☐ **移动壳问答下放**(v3.32 遗留):ReaderAiPanel 的 bottom-sheet 翻译
  (数据层/引用联动均现成,长按/返回键握手沿 useLayerHistory 惯例)。
- ☐ **scope=all / articles 的前端露出与门控档位**(v3.32 后端已全量落地,
  前端仍只露 本文/我的订阅):等依赖它的新功能规划成形时一并拍
  (全员开放 vs admin-only vs 总闸;all 检索域=发现页可见口径,无新信息泄露)。

- ☐ **30 个 incubating 源观察期转正评审**
  转正流程见 `docs/sources/curation_policy.md`「Incubation」节;
  Reddit 转正门槛 = 生产出口 IP 复验 429。转正时顺带更新 `docs/sources/node_catalog_and_risks.md` 快照。
- ☐ **日报源手工名单实践观察**(v3.3.0 落地 `daily_brief_source_ids` KV,观察实际日报质量后调整名单;
  v3.35 官源排查建议生产名单勾入 x_alibaba_qwen/x_deepseek_ai/x_openai/rss_deepmind_blog + 新源 x_zai_org/hf_qwen_models)
- ☐ **z.ai 官方博客源**(v3.35 官源排查发现:智谱一手宣发已迁至 z.ai/blog/*[如 /blog/glm-5.3],
  我们盯的 docs.z.ai/release-notes 官方停更于 GLM-5.2;z.ai/blog 无列表页/无 RSS/无 sitemap 收录/
  冷 curl 无公开 JSON API,需浏览器后端(crawl4ai)或抓到它的数据接口;短期由 x_zai_org preset 顶发现通道)
- ☐ **x.ai/news 产品线新闻源**(可选:docs_xai_release_notes 已覆盖模型发布且时效达标,
  x.ai/news 补产品动态[如 Grok Bot];Cloudflare 盾,需 Playwright,与 rss_openai_news 同款处理)

## 展望(用户表态、未立项)

- ◇ **退役公开分享链接**(issue #215,#213 目检时用户提出):游客总闸开时分享菜单已只留站内链接;待评估彻底退役——停发新链接、`#/s/{token}` 旧链接去向与过渡期、后端路由 / 服务 / 表 / 留存 / 运维总闸 / 公开取图端点与契约清理;先拍板「游客总闸关闭的部署是否仍需免登录单篇分享」。

- **论文全文后续（#41 首期已完成，未立项）**：PDF/OCR 与 ar5iv 回退；HF 之外论文源的自动全文接入；复杂 SVG、嵌套表格、自定义宏和 PDF 级排版保真；成功任务的存量重转换及 arXiv 修订自动跟踪；公开全文分发许可策略。
  触发：出现具体不可读论文、转换失真、版本更新或全文授权交付需求时，携带固定版本样本另建 issue；不自动全库重抓/重译，不改变论文自动分析只用摘要的成本边界。
  结构完整不等同语义正确，图片内文字翻译与任意术语/语义质量仍需独立评估；细节见 [归档 §0](./archive/issue-41-paper-fulltext.md)。这些是已明确后置的范围，不作为 #41 未完成项。

- ◇ **鸿蒙原生应用套壳**（#85 用户确认的后续方向）：保留现有 Web 阅读器，以鸿蒙原生应用承载。
  本轮不设计桥接、打包或分发，也不将鸿蒙 PWA 安装作为 #85 验收要求。
- ◇ **兴趣走出早报 · 后续波次**(issue #27 第一波 v3.52.0 已落地,方案 `docs/archive/interest-lens-wave-plan.md`):
  已做=左栏一根轴(栏头「订阅 | 兴趣」互斥切换,其下列源或列关注标签,点行下钻;兴趣轴 = 全站)+ 列头收藏星
  + 命中胶囊 / 订阅外就地订阅 + 兴趣编辑面并入发现页第三段。**v3.56.0(2026-09-14)取消屏蔽**:兴趣只剩关注一极
  (折叠行 / 早报 mute 硬排除与打标等待 / 头条 mute 例外一并退役),「兴趣轴是否露屏蔽」随之消失。**待拍板**:
  无兴趣时的引导形态(2026-09-09 另开讨论,现为占位形态)。**未做,逐项等数据或拍板**:
  发现页标签卡「主要来源」脚部(不带数字的桥,可选);兴趣渠道准入门槛是否再加
  新闻价值分下限(现只认主标签或相关度 ≥ 0.8);~~早报兴趣半是否取全站~~(v3.54.0 已做:「订阅 ∪ 兴趣」,
  订阅外门槛 6.0 / 每源硬上限 2 为管理面旋钮,**待生产数据回放校准**——观察订阅外命中占比 / 单源集中度 / 空报率,
  见 `docs/archive/personal-brief-interest-union.md`);~~feed / MCP 屏蔽~~(屏蔽已退役);
  问答先验;隐式建议关注(只建议不落库)。观察指标:设兴趣的用户占比 / 兴趣开关使用 / 早报兴趣命中率。
- ◇ **文章分析拆两次调用「先打分再理解」**(v3.45.1 issue #13 评估后不做):打分也要读全文,
  两次调用 prompt 翻倍(dev 实测 prompt 均 2869 tokens 占大头),低分短路能省的 completion
  与覆盖面(<5 分仅 4%)远抵不过;真收益只有「只改评分规则时只重打分」,但 `scoring_version`
  至今未变过——需要把一行结果拆成「评分组 / 内容理解组」两组版本键与状态机,租约/重试/
  `full_analysis` 回填都要分叉。等真出现频繁只改评分规则的需求再做。
- ◇ **评分链路观察期待办**(v3.49.1 issue #33 §1 检视返修后;黄金集门禁已落地——62 篇
  `tests/fixtures/golden_news_value.json` + `scripts/eval_news_value_golden.py`,取代原「约 30 篇
  手标」待办):①生产升到 v3.49.1 后看 `last_run` 的 threshold_backfilled/near_miss_appendix 常态
  是否非零(常态非零=门槛偏高或名单过窄);②无正文官方模型卡(hf_deepseek_models)复评 8.0/5.5
  摇摆——若日报漏掉一手权重发布,考虑对 hf_* 源在抓取侧补模型卡正文;③个人早报 5.0 门槛在新分布
  下(博客/论文落 4.5–5.5)看空报率,必要时降到 4.5 或改「兴趣通道不看分」;④黄金集扩到 100+ 篇
  时保持分层与双评审独立;⑤issue #33 §2–§5(重大事件通道/个性化页面/中文标题/兴趣不触发重编)
  另立波次。同波已了结的旧待办:「公共日报迁移到文章分析结果」(map 退役、日报复用分析)、
  「个人早报 API 测试的时段依赖」(去等待后门槛消失,依赖随之消失)。
- ◇ **用户自定源安全纵深二期**(v3.40 codex 检视遗留,方案 §9.1):①连接 peer 固定
  (redirect hop 级 SSRF 复检已在 v3.46 完成);②全站
  正式抓取统一响应大小上限(用户源已限 5MiB,策展源 55+ 无上限是全站级决策);
  ③多 worker 部署时把用户源成员关系正规化为带唯一约束的表(现进程内写锁,
  单 worker 前提)。
- ◇ **社交流整流翻译开关**(issue #84 遗留,样页方案 C):社交流头部「原语言 | ✦ 中文」一键翻整屏。
  首版只做逐卡底栏入口;整流开关开着时每条可译推文进入视口就是一次调用,现行读者 `translate` 日额度 50 次撑不住,
  要做先定批量合并(一次调用翻多条)或单独额度。见 `docs/archive/social-translate-plan.md` §6。
  2026-10-23 #218 R1 曾考虑以系统预译解决整流展示，已被 [R2](./archive/issue-218-title-translation-progressive-reading.md) 收敛：仅自动译标题，社交正文仍手动；本条仍为展望。
- ◇ **X 第二批账号**(观察期后按数据决定):候补名单与不收理由见方案文档 §1。
  同脉络的旧待办「管理面账号池(凭据池/轮换/健康探测)」**已作废**——X 改按量付费后
  官方 API 路径不需要真实账号 Cookie,前提消失。
- ◇ **媒体库容量策略**(图床波遗留,2026-07-20 决定先观察不设计):当前只做随文预取,增长有界
  (估每日几 MB~十几 MB)。**过期删除与「归档」初衷相抵触**,故不急于加;运维面板「占用空间」读数
  即观察窗口,真需要时从「按源白名单预取 / 老文章降采样压缩 / 容量上限+淘汰」三选。
- ◇ **E 体验波余项**:键盘导航 / 移动端适配(用户表态低优先)。
  含「沉浸阅读模式」——小视口(≤1280)正文行长不足的对症解,替代旧折叠把手方案(2026-07-20 评估结论)。
- ~~◇ **F 语义搜索入阅读器**(RAG 检索接入用户面)~~ **已被取代**(2026-08-11):
  RAG 层审视后定向为「取缔向量 RAG,检索改 LLM 计划检索 + FTS」,见排队中条目与
  `docs/archive/rag-retirement-plan.md`。
- ◇ **Newsletter 三批候补**(见 `docs/archive/source-expansion-wave3-plan.md` 候补名单)。
- ◇ **暗色 / 登录 / 动效三区扩审**(静默仪器重构收官时留下的截图立项项)。
- ◇ **`button/input { font: inherit }` 未分层陷阱同法收口**(issue #108 聚焦环入 base 层后的遗留):`index.css` 顶部这条
  全局 `font` 简写仍未分层,压掉 `@layer components` 里按钮 / 输入框类的 `font-size`,文件尾未分层区因此攒了十几条字号钉法
  (conventions §3)。同一手法(移入 `@layer base`)可让层内字号按本意生效并删掉尾区钉法,但涉及全站按钮 / 输入框字号的
  逐处目检,单独立波;做之前先盘点层内所有写了 `font-size` 却被压掉的按钮类,以免降层后字号集体变化。
- ◇ **Agentic 源接入(长期愿景,2026-07-25 用户表态)**:产品差异化 = **开箱即用的策展源**——
  最好的源已事先备好,用户不需要像 Folo 那样自己发现和收集。权限形态**永久维持**:仅管理员侧添加源,
  用户侧只提建议/申请,管理侧审核。长期演进方向是**类 OpenClaw 的 Agentic 后端**:接入模型智能 +
  Loop 构建,基于既有经验与流程(curation_policy 准入/观察期、preset 硬化范式)自动化完成
  「接纳审批 → 拉分支编写源固化代码 → 合入 → 添加源 → 观察孵化 → 转正」全流程,
  管理员/维护者只做观察或极小工作量的 Human-in-the-Loop 确认。
  近期铺垫参考(2026-07-25 分析,均未立项):0 级 = 放开 `ENABLE_CUSTOM_NODE_BUILDER` +
  source-configs 管理列表(基建已齐,见 `CustomNodeBuilder.jsx` / `source_builder.py`);
  1 级 = RSSHub 可选容器 + `rsshub://` 路由识别;2 级 = 读者「推荐源」申请 → 管理员收件箱审核。

### issue #79 源静默停产修复波遗留(2026-09-15)

- **公共日报进入个人早报的专用投影**:目前订阅了公共日报的读者靠日报记录的通用文章分析行(候选查询内连接 + `DAILY_BRIEF_READY` 重编)把日报条目带进版面,所以日报记录必须被分析 worker 评一次分(首次尝试常瞬时失败、重试成功,每天多一次调用且分数无读者面语义)。若要停止分析日报,需要给早报候选查询、`_analysis_readiness` 与 `DAILY_BRIEF_READY` 判定做一条不依赖分析行的专用投影,并改写 `test_personal_brief_accepts_persisted_public_brief_without_source_state`。
- **crawl4ai 主路的共用上下文**:dev 可选 extra 的 `Crawl4AIContentBackend` 复用单个 `AsyncWebCrawler`,是否同样撞上 openai.com「同一上下文第二次导航恒 403 挑战」未实测;生产未装 crawl4ai 故本波只修 Playwright 兜底。实测后若同病,让 OpenAI 详情绕过 crawl4ai 主路或逐篇新会话。
- **OpenAI 摘要正文回填**:09-09 至修复上线期间入库的 ~18 篇 `rss_openai_news` 正文只有 RSS 摘要(`has_content=True`,不会自动重抓),需一次性重渲染回填脚本。**2026-09-15 回放补正**:退化自 07-15 起就存在(每次运行首篇过、其余被 CF 拦回退摘要,近 5 周 <400 字占比 64%),09-15 只回填了 09-09 起的 14 篇,7 月中至 9 月初的摘要正文仍待同一脚本回填(见 `docs/plans/issue-82-node-watchdog-layer1.md` §2.5)。
- **日报 `per_source_cap=5` 是软配额**:`select_top` 的 overflow 补位可再加同源条目,淡日单源可能超过 5 篇(The Decoder 入名单后更可能出现);若运营要硬上限需改 overflow 语义。

## 已完结(近期,留档索引;执行记录与更早波次见 `docs/archive/README.md`)

- ☑ **issue #218 标题自动翻译与正文分段阅读**（2026-10-26 用户验收通过，未部署）：标题自动译、中文标题偏好默认开、正文手动逐块骨架原位替换、预算去重与同步保护；双向交叉检视通过，记录见 [R2归档](./archive/issue-218-title-translation-progressive-reading.md)。观察社交标题预算占比、租约等待、极大预览与旧客户端流量；内网本地标题回执保留、事件legacy缓存、日文识别、历史回填、正文预译、逐字稿机制改造及预算专门管理UI均不扩本波。

- ☑ **跨容器去重**(X 社交波遗留,「一条推 + 一篇 blog」):由事件层收口——社交帖入事件层(wave 1),个人早报与公共日报按事件归属同事件只出一条代表(wave 2,issue #103,方案 `docs/plans/issue-121-event-layer.md` §5.3)。原备案见 `docs/archive/social-x-wave-plan.md` §1。

- ☑ **issue #213 游客模式**(开发版 3.67.0-alpha;2026-10-23 目检放行,已预合入 master 供内网验收):访客会话 + 共享保留账号、白名单门控、共享游客早报、每访客已读与 AI 计次(与读者同额)、登录提示与叠层登录回原处、运维游客区;检视与目检记录见 [归档方案](./archive/issue-213-guest-mode.md)。同步 master 时核对内网登录入口仍保留「游客登录」「返回继续逛逛」,并行中的登录页重设计(#214)落地时同样保留。
  有意不做:注册 / 申请开通;游客数据(已读、AI 计次)迁移到登录后的账号;游客限流与反爬(观察期看 `guest_visits` 与访问日志再定);清 Cookie 换新访客身份绕开单人额度(全站日 token 预算兜底,公网部署宜设);用户手册未补游客截图。公开链接退役见上方展望 #215。

- ☑ **issue #41 论文全文阅读首期**（v3.66.0；2026-10-22 用户确认收口）：HF 官方 arXiv HTML 异步补全、章节/图片/公式/表格与基本排版、失败回退摘要、摘要分析边界、默认 dry-run 回填及 FTS 更新优化由 #201 完成；真实 R1 翻译问题由 #206/#207 修复，后台续译由 #208 完成（#202 已关闭）。测试、目检与交叉审查见 [归档方案](./archive/issue-41-paper-fulltext.md)；后续边界已单列上方展望。

- ✅ **issue #150 CI 后端测试并行化**(PR #151,归因与检视记录在 `docs/version-history.md`):后端拆 `backend-unit` / `backend-deploy` 两 job 各 `-n 4 --dist worksteal`
  + `backend (pytest)` 汇总门 + conftest 按 worker 沙箱 + 守卫 `tests/test_ci_workflow.py`(11 种反向对照);PR CI 实测整次 run 6.0 min(此前 28 min)。
  **暂缓**(并行后墙钟收益约 1 min):③ 39 个 baremetal 用例共享「已首装」快照(状态文件 / symlink 烤了绝对路径)与 sudo 桩改 sh;④ ffmpeg 静态包 / apt 缓存、
  路径过滤(近 40 个合入 PR 仅 4 个 docs / frontend-only;若做须 job 级 `if` + paths-filter)。观察期:时序型用例在 4 vCPU 下的抖动;入库 `uv.lock` 陈旧是否单独清理。

- ✅ **issue #85 移动端 PWA**（PR #100，方案 [frontend/pwa.md](./frontend/pwa.md)）：Android／iOS 主屏幕安装与联网恢复，用户两平台真机放行，本地检视通过；鸿蒙保留网页阅读，原生套壳另列展望。

- ☑ **阅读面小特性波(v3.53.0,issue #54 + #55)**:新闻价值分按分值分档着色(灰线 6.0 = 评分尺子档界、固定刻度,
  其上四档渐变递鲜明,灰线以下与元信息同灰;样页 `docs/design/dorami-score-tiers-quiet.html`)+
  发现页从哪个容器进入就先落该形态(初值不锁定;「发现更多来源」入口顺修落「源」段)。

- ☑ **落地页早报波(issue #56)**:登录 / 刷新落地 = 个人早报页(深链仍压过;总闸关维持文章容器;移动壳落地 Tab 同步为早报、
  TabBar 早报居首),新用户旅程拍板 **B 早报前置、引导内嵌**——首登引导横幅挂早报页顶,「设置兴趣」→ 发现页兴趣段、
  「稍后再说」与兴趣页跳过同语义;首登引导首次完成且选了兴趣时后端就地重编一次(v3.51.1「只记录不重编」的唯一显式例外,
  响应 `brief_rebuilt`);早报空态 / 失败态补「先去看文章」出口。默认订阅名单重定为 9 源(日报 + 量子位 / IT之家 AI / 新智元 +
  The Decoder + OpenAI / Anthropic / DeepMind 官方 + X·OpenAI;播客暂不进;移除 Claude Code Changelog——对早报零贡献)并改
  **代码缺省 + KV `reader_default_source_ids` 覆盖**(运维 → 内容「新账号默认订阅」卡,`/api/admin/reader-defaults`,
  未知 id / 私有源 400;只影响此后新建账号)。留观察:新账号首版早报清淡日观感(保底旋钮 `daily_brief_min_items` 归公共日报,
  个人早报无同款保底,先看空报率再议);引导横幅是否只显示前 N 天(现为直到完成或跳过)。

- ☑ **双节点分析与就绪状态收口(v3.46,#12/#14/#15/#16)**:Archive Sync v2 分流同步
  公共源/taxonomy/文章/分析/图片/来源状态，authority 围栏消除双端重复采集分析；普通
  自定 RSS 改为内网分析、含凭证源禁止 MaaS/公开分享；阅读器诚实呈现未分析/更新中，
  worker 有界 drain；个人早报合并 pending/generating 触发并展示 readiness 与部分完成原因。

- ☑ RAG 退役与问答检索重构(v3.30 检索扶正 + v3.31 退役清仓,2026-08-11/12):
  ask 订阅域改「LLM 计划检索 + FTS5」两段式、MCP 两工具同名换 FTS 芯;向量子系统
  (chromadb/extras/compose rag profile/双状态列/对账巡检/向量雷达)整体下架。
  审视结论、两波清单与重新引入触发器见 `docs/archive/rag-retirement-plan.md`。

- ☑ 源扩容 wave4(v3.23.0):Microsoft AI 模型、Artificial Analysis、Meta AI 博客、
  Kimi Research、MiniMax Research、Import AI、Arena 排行榜更新 7 个 preset 全链路接入；
  全批 `incubating`，真实抓取/正文首中尾/格式与 CTA 清洗验收记录见
  `docs/sources/candidates/source_expansion_wave4_sources.md`。
- ☑ X 社交波(v3.12.0):X API v2 官方按量付费采集 + 社交媒体第三容器(shape=social,SocialFlow 卡片流);
  6 个白名单账号 preset(全批 incubating;立项 8 个,7-21 压缩成本删 meta/openrouter)+ config 自助加号双路径;引用推/转推扁平化跨平台抽象、真实头像、
  配额守卫与配置面板、零成本本地回填;取缔「今日」容器、隐藏通用源、社交收藏筛选。方案 `docs/archive/social-x-wave-plan.md`。
- ☑ 图床波(v3.11.0):媒体库——正文外链图本地缓存(懒代理 + 随文预取 + 媒体热点图/定点重抓),
  原链不改写、三层降级、防盗链 Referer 解;设计细节见 `CLAUDE.md`「媒体库」节。
- ☑ 文档整理波(2026-07-20):归档已完结方案文档、建三层索引机制(CLAUDE/AGENTS → docs/README → 子 README)。
- ☑ 发现页(v3.10.0)+ 问哆啦美 fab 收缩态(v3.10.1)—— `docs/archive/reader-reshell-plan.md`「发现页轮」。
- ☑ 「接入集成」并入设置柜(v3.9.0)—— 同前档「并入设置轮」。
- ☑ 阅读器样页复刻+双轨合并(v3.6.0)→ 容器化(v3.7.0)→ 导轨靠拢·轨语言统一(v3.8.0)—— 同前档。
- ☑ 源扩容 wave1–3(v3.2.0 → v3.5.0)—— `docs/archive/source-expansion-plan.md` 及 wave3 篇。
- ☑ 静默仪器全站重构(v3.0.0)—— `docs/archive/quiet-instrument-restyle-plan.md`;
  耐久规范沉淀于 `docs/frontend/conventions.md`。

- **迁移层既有边界(codex 检视 PR #111 时指出,2026-09-16,非本波范围)**:`storage.migrations._has_user_tables`
  只以 `articles` 判「有业务表」,不含 articles 的库会被当空库;改成多表判定即可,顺手时做。
  (同时指出的另一条——`ensure_migrated` 调单数 `get_current_revision()` 在多 head 库会抛——main 已在 `10f9d54 feat(storage): OSS 持久存储、自动缓存与备份及本地回退 (#109)` 改为
  `get_current_heads()`,2026-09-17 同步合入 #111 分支时确认关闭。)

- **issue #202 后台翻译**：本波实现持久化分段续译、刷新/重启恢复、任务去重/取消、调用预留与清理；验证和检视见 [方案](./archive/background-translation-plan.md)。
