# 待办栈(Backlog)

> 性质:**跨波次的待办总账**——「进行中 / 排队中 / 展望」三档,动工时在此标注并链接方案文档。
> 波内逐项 ☐ 以各方案文档为准;已完结波次的执行记录在 `docs/archive/`(索引见其 README)。
> 建立于 2026-07-19(用户指示:待办栈落文件)。

## 进行中

- (暂无)

## 排队中(用户拍板、未动工)

- ☐ **文章分析 / Taxonomy / 个人早报首发后的规模化与二期边界**(PR #6 交叉检视中
  明确不阻断首发的余项):①读者 AI 预算从当前软闸升级为 DB 原子预留/核销/释放；
  ②`full_analysis` 估算和历史范围改为游标/批次查询，避免一次载入全部文章；
  ③灵活展示标签建立 `(normalized_label, article_id)` 倒排表，替代读者筛选时全表扫描；
  ④为 `tagging_status=failed` 增加不重复调用模型的独立修复队列；⑤治理统计和 Candidate
  evidence 做有界分页，且定向重标真正执行 `tag_ids` 范围；⑥订阅/隐藏变更触发早报
  revision 由当前进程内异步任务升级为持久化 outbox；⑦如要分析私有自定 RSS，先设计
  逐订阅者授权、撤回和费用归属，首版继续硬禁用。

- ☐ **管理面审计余项**(docs/admin-usability-audit.md,P0/账户 V2/列表规模化/数据生命周期
  四波已收口,负责人拍板余项暂缓):M18(核心运维数据导出)、M20(批量开户/邀请流)、
  M24(移动管理,显式产品边界)、M25(FTS 大结果集,随语料量);
  另 v3.42 列表规模化波的刻意裁剪:反馈批量流转/负责人/优先级、FetchRunsTab 完全服务端分页
  (现为「过滤/时间窗下沉 SQL + 500 上限 + total 诚实提示」折中——父子聚合展开模型下
  完全分页需子运行 lazy 化,规模再涨时做)、FetchTab 节点板文本搜索;
  v3.43.1 交叉检视残余:AI 用量按用户图的「其它」桶已改冒号 sentinel + 前端渲染层映射,
  真实用户名恰为「其它（用户）」的 identity 碰撞概率近零——彻底解法是图表层
  identity/display 分离(MultiSeriesArea 加 displayName formatter),需要时做;
  拆分部署(role≠all)下用户自定源的归属故事未定义(GC/抓取均已 collector 门控,
  真拆分时需先拍自定源属于哪一侧);
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

- ◇ **用户自定源安全纵深二期**(v3.40 codex 检视遗留,方案 §9.1):①redirect hop
  级 SSRF 校验/连接 peer 固定(现与 source_builder 同水位,拍板不加深);②全站
  正式抓取统一响应大小上限(用户源已限 5MiB,策展源 55+ 无上限是全站级决策);
  ③多 worker 部署时把用户源成员关系正规化为带唯一约束的表(现进程内写锁,
  单 worker 前提)。
- ◇ **跨容器去重**(X 社交波遗留):同一次发布 = 一条推 + 一篇 blog。
  首版不做;观察期用 `x_openai` 对照组量化与 `rss_openai_news` 的重复率后再定策略
  (候选:复用日报的 `dedup_clusters`)。见 `docs/social-x-wave-plan.md` §1「重叠率备案」。
- ◇ **X 第二批账号**(观察期后按数据决定):候补名单与不收理由见方案文档 §1。
  同脉络的旧待办「管理面账号池(凭据池/轮换/健康探测)」**已作废**——X 改按量付费后
  官方 API 路径不需要真实账号 Cookie,前提消失。
- ◇ **archive sync 媒体伴随包**(图床波遗留):collector→reader 的 JSONL 契约尚不携带 `data/media/`
  缓存文件,内网 reader 部署要吃到图,需要媒体导出/导入通道(tar 流或清单+分块)。单机部署无此需求。
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
- ◇ **Agentic 源接入(长期愿景,2026-07-25 用户表态)**:产品差异化 = **开箱即用的策展源**——
  最好的源已事先备好,用户不需要像 Folo 那样自己发现和收集。权限形态**永久维持**:仅管理员侧添加源,
  用户侧只提建议/申请,管理侧审核。长期演进方向是**类 OpenClaw 的 Agentic 后端**:接入模型智能 +
  Loop 构建,基于既有经验与流程(curation_policy 准入/观察期、preset 硬化范式)自动化完成
  「接纳审批 → 拉分支编写源固化代码 → 合入 → 添加源 → 观察孵化 → 转正」全流程,
  管理员/维护者只做观察或极小工作量的 Human-in-the-Loop 确认。
  近期铺垫参考(2026-07-25 分析,均未立项):0 级 = 放开 `ENABLE_CUSTOM_NODE_BUILDER` +
  source-configs 管理列表(基建已齐,见 `CustomNodeBuilder.jsx` / `source_builder.py`);
  1 级 = RSSHub 可选容器 + `rsshub://` 路由识别;2 级 = 读者「推荐源」申请 → 管理员收件箱审核。

## 已完结(近期,留档索引;执行记录与更早波次见 `docs/archive/README.md`)

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
