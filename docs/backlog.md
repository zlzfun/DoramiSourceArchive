# 待办栈(Backlog)

> 性质:**跨波次的待办总账**——「进行中 / 排队中 / 展望」三档,动工时在此标注并链接方案文档。
> 波内逐项 ☐ 以各方案文档为准;已完结波次的执行记录在 `docs/archive/`(索引见其 README)。
> 建立于 2026-07-19(用户指示:待办栈落文件)。

## 进行中

- ☐ **issue #74 个人早报三处**(分支 `feat/issue-74-brief-grid`,方案 `docs/personal-brief-grid-and-sections.md`):
  公共日报剔出早报范围 + 板块固定顺序 / 板块内分数降序 + 分值驱动网格(策略 F,样页 `docs/design/dorami-brief-grid-quiet.html`)
  已实现;待用户本地验收与 codex 检视。观察期:T = 9.0 / Δ = 1.0 两个常量在生产分布下的通栏频率与 2 + 2 出现率。
- ☐ **issue #69 图片理解波**(分支 `feat/issue-69-vision`,方案 `docs/image-understanding-wave-plan.md`):
  首波已实现 `vision_model` 档位 + 配图识别并入分析 / 日报 / 问答;待用户本地验收与 codex 检视。
  展望(未动工):Archive Sync 加 `image_insights` 流(内网检索档 cached-only 拿不到说明)、阅读窗图片下「图片文字」
  可展开(读者面直接受益,含表格译文)、`ocr_text` 进 FTS(需评估索引噪声)、按源开关识图(推文截图多的源优先)。
- ☐ **issue #33 个人早报产品优化波**(v3.50.x):§2 重大事件通道已落地(v3.50.0,方案
  `docs/personal-brief-breaking-lane-plan.md`,观察期看实体目录覆盖/月均头条数/同实体抑制误伤);
  §5 兴趣/订阅变更不触发重编已落地(v3.51.1,`docs/personal-brief-rebuild-entrypoints.md`);
  §4 中文标题已落地(v3.52.1,`docs/personal-brief-title-localization.md`);
  余下 §3 页面「个性化/智能化」(样页先行,把 §5 的过期提示行并入编排说明头)。

## 排队中(用户拍板、未动工)

- ☐ **管理面看板余项**(issue #31,v3.55.0 只做了两点:用户明细仅根管理员 + 账户增长曲线,
  方案 `docs/admin-root-admin-and-account-growth.md`):曲线上标注版本发布等关键节点(需启动时登记版本首见日期
  或手工标注表)、文章量/采集量/分析量统一为「当前值 + 增长趋势 + 历史曲线」、根管理员转让/多根(升级为账户列)。
- ☐ **文章分析 / Taxonomy / 个人早报首发后的规模化与二期边界**(PR #6 交叉检视中
  明确不阻断首发的余项):①读者 AI 预算从当前软闸升级为 DB 原子预留/核销/释放；
  ②`full_analysis` 估算和历史范围改为游标/批次查询，避免一次载入全部文章；
  ③灵活展示标签建立 `(normalized_label, article_id)` 倒排表，替代读者筛选时全表扫描；
  ④为 `tagging_status=failed` 增加不重复调用模型的独立修复队列；⑤治理统计和 Candidate
  evidence 做有界分页，且定向重标真正执行 `tag_ids` 范围；⑥含凭证自定 RSS 如需开放
  MaaS，先设计逐订阅者授权、撤回和费用归属（当前默认硬禁用）。

- ☐ **管理面审计余项**(docs/admin-usability-audit.md,P0/账户 V2/列表规模化/数据生命周期
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

- ◇ **鸿蒙原生应用套壳**（#85 用户确认的后续方向）：保留现有 Web 阅读器，以鸿蒙原生应用承载。
  本轮不设计桥接、打包或分发，也不将鸿蒙 PWA 安装作为 #85 验收要求；背景见 [PWA Evolution](./frontend/pwa-Evolution.md)。
- ◇ **兴趣走出早报 · 后续波次**(issue #27 第一波 v3.52.0 已落地,方案 `docs/interest-lens-wave-plan.md`):
  已做=左栏一根轴(栏头「订阅 | 兴趣」互斥切换,其下列源或列关注标签,点行下钻;兴趣轴 = 全站)+ 列头收藏星
  + 命中胶囊 / 订阅外就地订阅 + 兴趣编辑面并入发现页第三段。**v3.56.0(2026-09-14)取消屏蔽**:兴趣只剩关注一极
  (折叠行 / 早报 mute 硬排除与打标等待 / 头条 mute 例外一并退役),「兴趣轴是否露屏蔽」随之消失。**待拍板**:
  无兴趣时的引导形态(2026-09-09 另开讨论,现为占位形态)。**未做,逐项等数据或拍板**:
  发现页标签卡「主要来源」脚部(不带数字的桥,可选);兴趣渠道准入门槛是否再加
  新闻价值分下限(现只认主标签或相关度 ≥ 0.8);~~早报兴趣半是否取全站~~(v3.54.0 已做:「订阅 ∪ 兴趣」,
  订阅外门槛 6.0 / 每源硬上限 2 为管理面旋钮,**待生产数据回放校准**——观察订阅外命中占比 / 单源集中度 / 空报率,
  见 `docs/personal-brief-interest-union.md`);~~feed / MCP 屏蔽~~(屏蔽已退役);
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
- ◇ **跨容器去重**(X 社交波遗留):同一次发布 = 一条推 + 一篇 blog。
  首版不做;观察期用 `x_openai` 对照组量化与 `rss_openai_news` 的重复率后再定策略
  (候选:复用日报的 `dedup_clusters`)。见 `docs/social-x-wave-plan.md` §1「重叠率备案」。
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
  `docs/rag-retirement-plan.md`。
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
- **OpenAI 摘要正文回填**:09-09 至修复上线期间入库的 ~18 篇 `rss_openai_news` 正文只有 RSS 摘要(`has_content=True`,不会自动重抓),需一次性重渲染回填脚本。
- **日报 `per_source_cap=5` 是软配额**:`select_top` 的 overflow 补位可再加同源条目,淡日单源可能超过 5 篇(The Decoder 入名单后更可能出现);若运营要硬上限需改 overflow 语义。

## 已完结(近期,留档索引;执行记录与更早波次见 `docs/archive/README.md`)

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
  审视结论、两波清单与重新引入触发器见 `docs/rag-retirement-plan.md`。

- ☑ 源扩容 wave4(v3.23.0):Microsoft AI 模型、Artificial Analysis、Meta AI 博客、
  Kimi Research、MiniMax Research、Import AI、Arena 排行榜更新 7 个 preset 全链路接入；
  全批 `incubating`，真实抓取/正文首中尾/格式与 CTA 清洗验收记录见
  `docs/sources/candidates/source_expansion_wave4_sources.md`。
- ☑ X 社交波(v3.12.0):X API v2 官方按量付费采集 + 社交媒体第三容器(shape=social,SocialFlow 卡片流);
  6 个白名单账号 preset(全批 incubating;立项 8 个,7-21 压缩成本删 meta/openrouter)+ config 自助加号双路径;引用推/转推扁平化跨平台抽象、真实头像、
  配额守卫与配置面板、零成本本地回填;取缔「今日」容器、隐藏通用源、社交收藏筛选。方案 `docs/social-x-wave-plan.md`。
- ☑ 图床波(v3.11.0):媒体库——正文外链图本地缓存(懒代理 + 随文预取 + 媒体热点图/定点重抓),
  原链不改写、三层降级、防盗链 Referer 解;设计细节见 `CLAUDE.md`「媒体库」节。
- ☑ 文档整理波(2026-07-20):归档已完结方案文档、建三层索引机制(CLAUDE/AGENTS → docs/README → 子 README)。
- ☑ 发现页(v3.10.0)+ 问哆啦美 fab 收缩态(v3.10.1)—— `docs/archive/reader-reshell-plan.md`「发现页轮」。
- ☑ 「接入集成」并入设置柜(v3.9.0)—— 同前档「并入设置轮」。
- ☑ 阅读器样页复刻+双轨合并(v3.6.0)→ 容器化(v3.7.0)→ 导轨靠拢·轨语言统一(v3.8.0)—— 同前档。
- ☑ 源扩容 wave1–3(v3.2.0 → v3.5.0)—— `docs/archive/source-expansion-plan.md` 及 wave3 篇。
- ☑ 静默仪器全站重构(v3.0.0)—— `docs/archive/quiet-instrument-restyle-plan.md`;
  耐久规范沉淀于 `docs/frontend/conventions.md`。

- **迁移层两处既有边界(codex 检视 PR #111 时指出,2026-09-16,非本波范围)**:① `storage.migrations._has_user_tables`
  只以 `articles` 判「有业务表」,不含 articles 的库会被当空库;② `ensure_migrated` 经 `_current_revision` 调单数
  `get_current_revision()`,库里已有多个 heads(下游分叉仓形态)时会抛错,而同文件的 `plan_migrations` 已用复数
  `get_current_heads()`。两处改成复数 / 多表判定即可,顺手时做。
