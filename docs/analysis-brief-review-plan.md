# 入库分析与日报生成链路审视与优化(v3.48 收口波)

> 状态:**实施中**(2026-09-08 动工,分支 `feat/issue-22-news-value-scoring`,与统一评分波同 PR)。
> 对象是 [`unified-news-scoring-plan.md`](./unified-news-scoring-plan.md) 落地后的两条链路:
> 入库分析(`services/article_analysis.py`)与公共日报生成(`services/daily_brief.py`)。
> 本文只钉审视结论与拍板的修法;实施记录见文末。

## 0. 审视结论

骨架站得住:一把尺子、日报对分析软依赖、权威机械层不靠 LLM 自觉,三条都落实了。
问题集中在四类:**两条来路口径不一致**(§1)、**成本与性能**(§2)、**可观测性缺口**(§3)、
**可选打磨**(§4)。全部拍板实施(2026-09-08 用户:「1 到 7 和可选三点全做」)。

## 1. 口径一致性

### 1.1 领域词汇两套 → 补评也喂闭集

`_scored_item` 的 realm 取 `topic_tags[0]`,但两条来路词汇不同:复用分析表的条目拿规范标签
中文名(「大模型」),就地补评因传空闭集,拿的是模型自由填写的 `tag_candidates` 标签(「LLM」
「大语言模型」「」)。后果:`select_top` 的 `per_realm_cap` 按字符串计数,同一领域拆成几个桶;
空 realm 全部落「未分类」桶被 8 条上限卡住;`extensions.items.tags` 词汇随来路波动。

**修法**:补评复用 worker 同一套 `load_relevant_active_tags` 词法召回的闭集(按标题正文召回的
小子集,不是 96 条全量),在 `generate_daily_brief` 的 session 内为每个待补评候选预取,
`score_candidates` 经 `taxonomy_by_id` 传入。补评产出的 topic/entity 与存储路径同样只取
**规范标签名 + entities 名**,不再混入自由标签——「同一个函数、同一把尺子」自此从分数扩展到标签。

### 1.2 附录无门槛 → 无正文候选按标题走同一把尺子

有正文但低于门槛的条目被 pass,而无正文候选(HN 外链帖、只给摘要的源)一律进「📎 其它收录」,
完全不经评分:5.5 分的完整文章被丢,无法评估的裸标题反而上报。

**修法**(拍板:走评分而非按角色一刀切——HN 类发现源的价值就在外链标题):无正文候选也调
同一个评分函数(body 为空),**低于门槛 pass、过线者进附录**(无正文无法写要点与点评,不进正选);
附录自此只装「过线但无正文」与「评分失败」两类。成本上限 = 候选上限,与旧 map 持平。

## 2. 成本与性能

### 2.1 worker 每分钟全量扫 7 天正文 → 轻列 + 只挑需要动作的行

`scan_analysis_backfill` 每 1 分钟 `select(ArticleRecord)` 取近 7 天全部行(含正文),逐篇
`queue_article_analysis`(3 次 `session.get` + 全文哈希),绝大多数结果是 `unchanged`。

**修法**:扫描改为 `ArticleRecord` 轻列 LEFT JOIN `article_analyses`,只挑三类行:无分析行、
`skipped`(源开关可能已重开)、`succeeded` 但版本键过期(§2.3 限流)。其余行(pending/running/
failed/timeout/当前 succeeded)扫描不碰——重试与租约机制自管,内容变化由入库钩子与
`PUT /api/articles` 显式入队覆盖(`save()` 只在插入与「无正文→有正文」时回报 id,
元数据自愈路径不改正文;标题级变化留给 `full_analysis` 的 `missing_or_outdated` 判定)。
`scanned` 读数语义随之改为「需要动作的行数」。

### 2.2 编辑阶段不看分析产出 → 喂已知事实

`build_editorial_user_prompt` 只给标题/来源/正文前 6000 字;分析阶段读了 24000 字并已有
summary、score_reason、genre。长文要点只能来自开头,编辑还会重推 company/realm 覆盖选篇值。

**修法**:`ArticleScore`/`ScoredItem` 增 `score_reason`(不进 `extensions.items`),编辑输入
增「【系统分析(已知事实)】评分理由 + 客观摘要」段,提示词说明「以正文为准、分析结论用于
定位重点」。正文截断上限不变(要点来自正文细节,摘要已覆盖全局)。

### 2.3 版本号一变近 7 天全量重跑 → 限流慢滴

`is_current` 按版本失效后,7 天存量瞬时全部 invalidated,与当天新文章抢同一队列。

**修法**(偏离最初「交给 full_analysis 作业」的建议,理由:个人早报不按 `scoring_version`
过滤,若操作者忘建作业,旧尺子分数会无限期混用;自动慢滴不需要人记步骤):版本键过期的
`succeeded` 行由扫描**每 tick 最多 `VERSION_REFRESH_PER_CYCLE=16` 篇**invalidated,新到文章
永远优先;7 天外照旧等 `full_analysis`。指标 `article_analysis.version_stale` 透出剩余篇数。

## 3. 可观测性

### 3.1 分数分布

`last_run` 增 `score_histogram`(1～10 整数档条数,含补评与复用);运维分析面板的 KPI 条已有
近 7 天 `score_histogram`,补一行迷你柱状图(手写 grid,不引图表库),阈值 6.0/5.0 自此有据可调。

### 3.2 分析总闸默认关

`ARTICLE_ANALYSIS_ENABLED_KEY` 默认 `False`。生产忘开则日报每天全量补评、读者面永无分数,
日志只见 `scored_inline` 很大。上线手册补发布步骤;日报面板对最近一次运行「补评占比 ≥ 50%」
给一句提示,引导去开总闸或调整 cron。

### 3.3 补评与 worker 撞车

补评的候选若正 `pending/running`,worker 随后会再算一次(方案 §2 已接受的代价)。`last_run`
增 `scored_inline_pending`(补评里正在排队的篇数),面板同上提示;文档建议日报 cron 排在
worker 追平之后(默认 08:30 已晚于抓取高峰)。不引入写回。

## 4. 可选打磨

- **同事件机械预聚类**:分析结果带规范实体,先按「同 company(或首个实体)+ 标题高相似」
  机械并簇(`difflib` 比率 ≥ 0.6,标题归一化后),LLM 只处理剩余;LLM 失败时机械层是兜底。
  跨语言(官方英文 vs 媒体中文)仍交 LLM。
- **跨天查重对照物带要点**:`fetch_recent_brief_items` 的 items 已有 summary,对照物每条附
  要点首句(截 60 字),提示词同步说明;`titles` 键保留供同日合并等旧消费方。
- **补评撞车观测**:见 §3.3。

## 5. 明确不做

- 补评结果写回分析表(耦合租约/权威/总闸状态机,方案 §2 已否)。
- `scan_analysis_backfill` 改增量水位(轻列 LEFT JOIN 已把每 tick 成本压到「需要动作的行」,
  水位会引入第二个游标与内容变化漏判)。
- 编辑阶段正文上限提高(要点来自正文细节,分析摘要已提供全局;先看效果)。
- 黄金集门禁(记 backlog,等生产分布数据)。

## 实施记录

- 2026-09-08 §1.1/§1.2/§2.1/§2.2/§2.3/§3.1/§3.2/§3.3/§4 全部落地,测试见
  `tests/test_daily_brief.py`(补评闭集/无正文评分/预聚类/对照物要点/直方图与撞车读数)、
  `tests/test_article_analysis.py`(轻列扫描/版本限流)、`tests/test_analysis_observability.py`
  (`version_stale`)。
