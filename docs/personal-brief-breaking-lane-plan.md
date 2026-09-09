# 个人早报「重大事件」通道(issue #33 §2,v3.50.0)

> 状态:◉ 活跃(v3.50.0 落地,观察期)。上接 issue #22(v3.48 统一新闻价值评分)与 v3.44/v3.46 个人早报链路;
> 拍板记录见 issue #33 评论(2026-09-09,「全按推荐实施」)。

## 1. 问题

个人早报严格冻结订阅范围(v3.44 刻意设计,防止早报变成公共日报)。上量后暴露的不顺:GPT-6 / Fable 5.1 这一级别
的发布对所有读者都是头条,不该因为没订 OpenAI/Anthropic 官博就在早报里缺席。

## 2. 实证(决定了方案形状)

样本 = §1 评分检视同一份生产 7 天导出(2026-08-30～09-08,699 篇,v6b 单分):

| 门槛 | official | media | personal | leaderboard |
|---|---|---|---|---|
| ≥ 9.5 | 9 | 5 | 4 | 0 |
| ≥ 9.0 | 9 | 14 | 5 | 0 |
| ≥ 8.5 | 12 | 27 | 5 | 3 |

- 官方 ≥9.0 的 9 条只对应 **2 个事件**(GPT-6 Astra 8 条横跨三天;NVIDIA 收购 Hugging Face 1 条)。
- **Fable 5.1 的 Anthropic 官博不在归档里**(窗口内 `web_anthropic_news` 只有两篇安全类文章),这件事只以媒体/个人形态
  在库(9.2×3、8.8、8.5×2)——issue 原案「官方 ∧ ≥9.0」会漏掉自己举的例子,与 v3.35 日报实证的「官源缺位」同病。
- 单源高分噪声真实存在(「Fable 遭黑客破解」9.2、「Claude 攻克费马大定理」9.2,各只有一家),放开角色必须配多源印证。
- `DuplicateGroupMemberRecord` 全库没有写入方,`_event_key` 实际只剩 URL/标题两级,4 条 X·OpenAI 推文互为不同事件——
  通道必须自带同事件归并。

## 3. 方案(全部机械层,不靠 LLM 自觉)

**准入(满足其一)**
1. 官方一手:`source_role == official` 且 `score ≥ T`(T 默认 9.0,KV `personal_digest_breaking_min_score`)。
2. 多源印证:同一事件下 ≥2 个不同 `source_id` 的文章 `score ≥ T − 0.5`,且至少一条 `≥ T`。单源不构成印证;
   T−0.5 的宽带是为了不被 9.0 线上的复评抖动单点打掉。

**共同条件**:`publish_date` 在 cutoff 前 24h 内(常量,不做 36/72 回退);来源 = 全库非私有源减隐藏源
(`user_rss_` 与日报记录在 SQL 侧排除,隐藏源事后剔除);**不看用户订阅**——订阅内的重大事件同样走这条,标签更诚实;
mute 硬排除;空订阅维持为空(通道只加在已有早报之上,不是「无订阅也出报」)。
**资格按全池判定**:不因读者订阅了某来源就从池里抠掉那篇——订阅域已选中的文章若成为头条代表,从精选提级到头条位(不重复出现、不算降级);私有源的两个判据(`user_rss_` 前缀 / `SourceConfigRecord.owner_username` 非空)都排除,与订阅域解析同口径(codex 检视 P1/P2 返修)。

**同事件归并**:按 `entity.*` 标签集合做连通分量(并查集);无实体标签的候选回落 `_event_key`。每事件一条代表,
优先级 **官方 > 非社交形态 > 分数 > 发布更早**(能选官博就不选推文)。

**条数与位置**:每期 ≤ N(默认 2,KV `personal_digest_breaking_max_items`,0 = 关闭通道,上限 5);
**额外加在 target 10 之上**,不进兴趣 50% 分母;position 0..N−1,section 固定「重大事件」,lane `breaking`。

**跨天抑制**:事件实体集合与该读者**前 2 个完成日期**的 breaking 条目实体集合有交集即跳过
(实证:GPT-6 在 09-04/05/06 三天都有 ≥9.5 官方稿)。实体码随条目 `ranking_features_json.event_entity_codes` 落库。

**理由文案**(`selection_reason`):官方「今日重大事件 · 「OpenAI 新闻」官方一手发布，不在你的订阅内也为你保留。」;
印证「今日重大事件 · 3 家来源同时报道，代表来源「TestingCatalog」。」;订阅内的来源不带「不在你的订阅内」后缀。
`ranking_features_json.breaking = {basis: official|corroborated, source_count, subscribed}`,API 条目透出 `breaking`。

**7 天样本回放预期**:09-02 期出 Fable 5.1(印证路径);09-05 期出 GPT-6 Astra(官方,代表取 OpenAI 新闻而非推文)+
NVIDIA 收购 HF(官方);09-06/07 期 GPT-6 被跨天抑制;World Labs Atlas 因目录里没有该实体、标题差异大而**不进**
——这是实体归并的诚实边界,不用 LLM 补。

## 4. 落地面

- `models/analysis_contracts.py`:`SelectionLane.BREAKING`、`PERSONAL_DIGEST_BREAKING_*` 常量、
  `DigestArticleCandidateDTO.source_role/content_shape`、`DigestSelectionDTO.breaking_basis/breaking_source_count/event_entity_codes`。
- `models/db.py` + 迁移 `c7e1a9d4b2f6`:`personal_digest_items.selection_lane` CHECK 放宽为三值(SQLite batch 重建;
  downgrade 先物理删 breaking 条目再收窄)。
- `services/digest_selection.py`:`BreakingSelectionPolicy` + 纯函数 `select_breaking_events`(零 DB/LLM 依赖)。
- `services/personal_digest.py`:`breaking_policy`(KV 读取与钳制)、`load_breaking_candidates`(全可见源 24h,
  SQL 侧 `≥ T−0.5` 预过滤)、`_source_role_and_shape`(config 元数据优先、注册表其次)、`_previous_breaking_entities`;
  候选取数抽成 `_query_candidate_rows`/`_candidates_from_rows` 两段供订阅域与通道共用;条目落库时 breaking 在前、
  精选/降级最新更新顺延位置。
- `routers/analysis_ops.py`:`GET/PUT /api/admin/analysis/config` 增 `personal_digest_breaking{min_score,max_items,max_items_limit}`
  与同名 PUT 字段(0～10 / 0～5 校验);`routers/personal_briefs.py` 条目带 `breaking`。
- 前端:`PersonalBriefPage` 「重大事件」chip(`is-breaking`,与「关注 ·」同族)、报头统计只数用户自己那份精选并前置
  「N 条重大事件」;`AdminTaxonomyPanel` 新增「早报重大事件通道」卡(阈值/条数)。视觉细化随 §3 样页。
- 拆分部署:内网靠同步过来的 analyses + tag assignments,零新依赖。
- 测试:`test_digest_selection`(官方单条/多源印证三反例/实体归并+推文让位/跨天抑制+cap+关闭/mute+排除+确定性)、
  `test_personal_digest`(订阅外官方头条端到端/隐藏源与私有源排除+KV 关闭/同实体抑制+空订阅仍空)、
  `test_analysis_personal_api`(旋钮往返与 422)。

## 5. 明确不做 / 观察项

- 不用 LLM 判「是否重大」、不做厂商白名单、不放宽空订阅、不占用户精选名额。
- 观察期看:生产实体目录(45 个)对头部事件的覆盖是否够(World Labs 一类新玩家进目录才会被印证路径捕获);
  T=9.0 下的月均头条数;同实体抑制是否误伤同一厂商两天内的两件不同大事(现按「共享任一实体」抑制,偏保守)。
- §3(页面「个性化/智能化」)、§4(中文标题)、§5(兴趣变更不触发重编)在后续 PR。
