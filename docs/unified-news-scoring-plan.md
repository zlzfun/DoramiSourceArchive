# 统一新闻价值评分波(issue #22)方案

> 状态:**实施中**(2026-09-07 动工,分支 `feat/issue-22-news-value-scoring`)。
> 底稿是 [issue #22](https://github.com/zlzfun/DoramiSourceArchive/issues/22) 的讨论定向;
> 本文只钉动工时拍板的形态与取舍,讨论脉络与被推翻的方案见 issue 本体。
> v3.44 文章分析尚未发布到生产/内网,**不做迁移与兼容**——直接改语义、改版本号,
> 存量分析结果按版本键失效重跑。

## 0. 拍板(2026-09-07,与同事讨论后)

1. **取缔阅读价值维度,文章级评分只有一把尺子:新闻价值**——量「这件事有多重要」,
   不量「这篇写得好不好」。入库分析改用以公共日报 MAP 提示词为底改写的新闻价值评分标准。
2. **入库分析一次调用产出**:新闻价值分 + 一句理由(为什么重要/不重要)+ summary
   + 体裁 + 规范标签 + 实体(与 v3.44 契约一致,只换评分语义)。
3. **公共日报复用这份评分**:低于阈值直接 pass;过线者按原有规则(聚类/官方加成/
   多样性/跨天查重)择优,入选后再逐篇生成点评与中文标题,聚合成日报。
4. **公共日报对入库分析是软依赖**:生成时评分部分具备或完全不具备都能 work——
   缺分的候选由日报就地调用**同一个评分函数**补评,结果只用于本次生成、不写回分析表。

整体取向:架构简洁、原理易懂、松耦合、不易出错。

> 落地后的链路审视与收口(补评闭集/无正文评分/轻列扫描/编辑喂事实/直方图/预聚类等)
> 见 [`analysis-brief-review-plan.md`](./analysis-brief-review-plan.md),与本波同 PR。

## 1. 一把尺子:评分文档

位置 `src/llm/article_analysis_prompt.py`(`ARTICLE_ANALYSIS_SYSTEM_PROMPT`)。
以 v3.35/v3.39.2 两轮生产实证校准过的 `MAP_SYSTEM_PROMPT` 锚点体系为底,并入 v3.44 分析
契约的标签/实体/摘要要求,新增两条 issue 点名的补课:

- **档位**(1.0～10.0,一位小数):9.0+ 行业级重大突破/格局级事件;7.0～8.9 头部厂商
  旗舰模型/核心 API/开源权重、Agent 与产品线正式 GA 与重大里程碑、有明确新意的研究、
  重大融资/收购/政策;5.0～6.9 常规更新、增量改进、二线消息;1.0～4.9 边角消息、
  信息稀薄、营销通稿。
- **厂商主次甄别、风向标厂商例外、车载降权、营销重罚**原样保留。
- **领域相关性**:与 AI/前沿技术无关的内容(职场随笔、泛商业)不因写得好而高分。
- **非新闻体裁写实锚点**:论文/教程/观点量「影响力」——里程碑论文、被广泛讨论的观点、
  行业级教程各留上行空间;普通论文/教程/随笔落 4～6。
- **不按发布时间打分**:量的是「发布当时这件事的分量」,时效由消费方的候选窗口给,
  历史回填不因文章旧而压分。
- `score_reason` 改为「为什么重要/不重要」的一句注脚(≤40 字,先于分数输出——沿 issue #13)。

提示词输入补两个信号:**来源友好名**与**来源角色**(官方/媒体/个人/榜单,后端
`source_naming.source_role` 镜像)——厂商主次甄别与官方短公告识别需要它。仍不喂 URL。

版本键:`ARTICLE_ANALYSIS_PROMPT_VERSION = article-analysis-v5`、
`ARTICLE_ANALYSIS_SCORING_VERSION = news-value-v1`。既有 `is_current` 判定按版本失效,
近 7 天存量自动重跑,更早等 `full_analysis` 回填。

**列名 `quality_score` 保留**(拍板):改名会牵动分析契约、归档同步 v2 的 analyses 流、
API 投影与前端键名,是纯机械改动;语义以本文档与代码注释为准。

## 2. 公共日报:复用评分,软依赖

`src/services/daily_brief.py` 新链路(替换 map 逐篇打分):

```
collect_candidates(游标/名单/裁剪,不变)
  → load_stored_scores:批量读 article_analyses 里「succeeded 且 scoring_version==当前」的分数
  → score_candidates:有分数的直接投影;没有的就地调 analyze_article_with_llm(同一函数、
    同一提示词、空标签闭集)补评,并发+串行重试一轮,仍失败者进「📎 其它收录」附录
  → 阈值过滤:score < daily_brief_min_score 直接 pass(不进正选也不进附录,随游标跳过)
  → dedup_clusters(不变) → select_top(top_n+buffer 预选,机械层不变)
  → editorial_polish:入选者逐篇一次 LLM 调用产 title_cn/来源名/company/realm/
    1–3 条加粗要点/100–150 字点评/常规标签(提示词=原 MAP 去掉 score/classification);
    失败串行重试一轮,仍失败者保留分析摘要作要点、点评留空,不降附录
  → cross_day_dedup(不变) → 裁回 top_n → 同日合并(不变) → 确定性渲染(不变) → 写库
```

要点:

- **就地补评不写回分析表**。写回意味着日报要进入分析子系统的租约/权威/总闸状态机——
  那是耦合;不写回则日报与 worker 互不知晓,各自按自己的开关工作。代价是 worker 之后
  会再算一次,但那批本就是 worker 还没跑到的文章,正常节奏下量很小;最坏(worker 整晚
  没动或总闸关着)成本等于今天的 map,不会更贵。
- **分析总闸关着日报照常**:总闸只管 worker,日报补评直接调函数。源级 `ai_analysis_enabled`
  同理只约束 worker;日报名单是管理员手工 allowlist,不做双向校验。
- **选篇前需要的字段全由分析产出机械推导**:classification ← genre 映射;realm ← 首个
  topic/industry 规范标签名;company ← 首个 entity 标签名(否则 entities 首项);聚类/
  跨天查重的 hint ← 分析 summary 首句。editorial 之后 company/realm 以编辑产出为准。
- **阈值**:KV `daily_brief_min_score`,默认 6.0,配置面板可调。语义是「pass 掉边角料」的
  下限,入选仍靠排序与配额——淡日子日报自然变短而不是被灌水。
- `extensions.items` 形状不变(title_cn/summary/comment/classification/score/tags/company/
  realm…),神灯导出与内网流水线零感知;`score_reason` 仍不进 items。
- 进度阶段改为 collecting / scoring / selecting / editing / reducing / persisting;
  `last_run` 增 `scored_stored` / `scored_inline` / `below_threshold` 三个观测读数。
- 退役:MAP 逐篇打分、adapter 开关 `public_digest_analysis_adapter_enabled`、shadow 指标
  KV、`REDUCE_SYSTEM_PROMPT` 以外的 legacy 兼容层;AI 用量 purpose `daily_brief_map`
  → `daily_brief_editorial`,补评走 `article_analysis`(归属触发者/system)。

## 3. 个人早报

- 质量门槛 `PERSONAL_DIGEST_MIN_QUALITY_SCORE` 7.0 → **5.0**:新尺子下博客/教程/观点落
  4～6 分,7.0 会让博客订阅者的早报常态为空;5.0 只挡边角与营销,兴趣/质量两通道同门槛,
  不加通道特例。「订阅源最新更新」降级通道原样兜底。
- **去等待**(issue #22 评论 2026-09-06):打开/重编/08:30 自动编排三条路都立即用库里
  现有内容生成;`sync_stale` / `analysis_incomplete` 按生成时刻事实计算作版面标记,
  想要更全的一版再点重编。`check_after` / `deadline_at` 不再门控生成(列保留作记录),
  前端等待态只剩「正在编排」。

## 4. 消费方改名

读者面分数统称**「新闻价值」**:速读卡/早报卡/管理面抽屉与弹窗的「内容价值分」注脚、
aria-label、免责文案(统一为 `SCORE_DISCLAIMER` 常量,四处手写副本收敛)、台账筛选档位
(7+ 重要资讯 / 8+ 头条候选 / 9+ 重大事件)。

## 5. 明确不做

- 阅读价值第二轴、延期名单、附录「未评估」、08:30 覆盖率等待(有无上限补评兜底后属可选优化)。
- 点评/中文标题并入分析调用(覆盖面不对等 + 编辑口吻与中性描述冲突)。
- `quality_score` 列改名(纯机械改动,收益是名字好看)。
- 黄金集门禁(约 30 篇手标)——留作生产观察期的后续,记 backlog。
