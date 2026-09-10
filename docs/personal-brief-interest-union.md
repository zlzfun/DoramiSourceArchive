# 个人早报「订阅 ∪ 兴趣」+ 页面体现个性化(issue #33 §3,v3.54.0)

> 状态:已实现(v3.54.0)。样页 `docs/design/dorami-brief-personal-quiet.html`(§3 一稿,拍板项已按推荐落地)。
> 前置讨论:issue #27 一波把「早报兴趣半取全站」列为待拍板;本波与 §3 页面触点一起做,因为
> 说明行要说清「从哪里选的」,并集不先定,措辞定不下来。

## 0. 问题

早报此前**只从订阅源里选**。兴趣在这个池子里只起排序作用:命中兴趣标签的条目最多占 10 篇里的一半,
其余按新闻价值分补齐(门槛 5.0),屏蔽硬排除;唯一能越过订阅面的是「重大事件」通道(v3.50)。
用户以为是「订阅和兴趣的并集」——一个订阅了三个博客、兴趣设了「智能体」的读者,全站当天关于智能体的
官博/论文与他无缘。另一头,页面本身看不出「这是为我编的」:报头一行统计,卡片和普通列表无异。

## 1. 拍板(2026-09-10,按推荐)

### 1.1 选篇改「订阅 ∪ 兴趣」

| 项 | 决定 | 理由 |
| --- | --- | --- |
| 兴趣半候选池 | 订阅源 ∪(全站可见源 ∩ 命中兴趣标签 ∩ 非屏蔽) | 兴趣半不再被订阅面截断;质量半仍只从订阅源选,早报不变成公共日报 |
| 订阅外门槛 | **6.0**(订阅内仍 5.0),KV `personal_digest_external_min_score` | 订阅是读者明说的信任,订阅外没有这层背书;6.0 = 公共日报入选线,同一根线好解释;新分布下 4.5–5.5 那层(常规论文/博客)进不了订阅外的兴趣半 |
| 订阅外每源每期上限 | **2**,硬上限不参与放宽,KV `personal_digest_external_per_source_max`(1～5) | HF Daily Papers / HN 一类高产源一天几十篇,只要两三篇过 6.0 又命中标签,5 个兴趣名额就全是它;订阅内不设(你订它就是要看它),仍是既有软上限 |
| 订阅为空但设了兴趣 | **出报**,只有兴趣半(≤ 5 篇),50% 上限对该形态解除;订阅与兴趣都空才 `empty_subscriptions` | 首登引导让人先选兴趣,不出报等于白选;报头与空态如实说明「你还没有订阅来源」 |
| 排序 | 订阅内命中排在订阅外之前,其后同旧序(命中强度 → 分数 → 发布时间) | 同样命中时先给读者明说信任的 |
| 命中判据 | 主标签或相关度 ≥ 0.8(`reader_interests.INTEREST_MATCH_MIN_RELEVANCE`),**订阅内外同一尺**;屏蔽仍看全部指派 | 与阅读器兴趣透镜同一定义——早报说「命中你的兴趣」时,兴趣轴里也能看到它;宁漏放勿误放 |
| 隐藏源 / 私有自定源 / 公共日报记录 | 照旧排除(与重大事件池共用 `_reader_visible_rows`) | 跨订阅的池是全读者共享的,私有内容一条不能漏出 |
| 冻结与就绪 | **只看订阅源**:`expected_source_ids` / `due_source_ids` / `sync_stale` / `analysis_incomplete` 语义不变 | 订阅外条目不影响「来源是否更新完成」的判定;权限边界也不因兴趣扩大 |
| feed / MCP | 不跟,仍是订阅域 | 交付契约不动 |
| 成本 | 零 LLM 调用;订阅外池在 SQL 里用 `exists(命中指派)` 收窄,不是全站载入再过滤 | |

**没做回放**:v3.49/v3.50 用的生产 7 天 699 篇样本未留在本地(dev 库近 7 天只 7 个来源 / 23 篇 ≥ 6.0),
重跑要 699 次 LLM 调用。两枚参数因此做成管理面旋钮(运维管理 → 分析 → 「早报订阅外兴趣」卡),
上线后按真实命中量调:订阅外命中很少就把门槛回落到 5.5,单源刷屏就把上限压到 1。

### 1.2 页面体现个性化 / 智能化(§3)

三处改动一条主线——**把「为什么」说出来**:

1. **编排说明行**(报头 hairline 之下、正文之上,一行):渐变星 + 渐变「哆啦美」是 AI 身份(全站唯一渐变 =
   AI 触点),其后是事实句,右端「调整兴趣」深链发现页兴趣段(兴趣回路)。事实句按形态:
   - 正常:「从你订阅的 **12** 个来源和 **4** 个兴趣 · **86** 篇里选出 **10** 篇,**3** 篇命中你的兴趣(**1** 篇来自订阅外)」
   - 订阅为空:「你还没有订阅来源,本期只按你的 **4** 个兴趣编排 · **31** 篇里选出 **5** 篇」,动作链改「去发现来源」
   - 降级:「今天没有达到入选标准的内容,以下是 **12** 个订阅来源的最新更新,不计入正式精选」
   - 「M 篇里」来自 `selection_stats_json.candidate_count`(**拍板:入库**,一条迁移 `b2d8e4f6a9c1`);历史版本无值省略这半句。
   - §5 的「兴趣已更新 · 立即重编」、「部分来源/分析未完成 · 重新编排」、「重编请求已记录」三条提示行**并入尾句**,
     一次只显一条(优先级 排队 > 未完成 > 已更新),页面上永远只有一行说明。
2. **选篇理由进卡头右侧**(元信息档),词汇只有四个,回答同一个问题「它为什么在这」:
   `兴趣 · 标签`(accent + 实点)/ `重大事件 · 官方一手 | N 家来源`(accent + 闪电)/ `新闻价值入选`(faint)/
   `最新更新`(faint,降级版面)。**拍板:用「新闻价值入选」不用「今日最重要」**——一篇 6.5 分的稿子叫不出
   「今日最重要」,「入选」平实,说的是它靠分数进来的。**拍板:落卡头右侧不落脚部**——脚部 chip 自此只答
   「讲什么」(主题标签),「为什么」和「讲什么」不再混在一条 chip 行(conventions §4.1.1 单轴)。
   普通卡里理由跟在来源名后、分数仍在右缘;通栏卡分数在右栏,理由独占头行右侧。
3. **订阅外标记**:订阅外命中的卡在源名后挂 faint「未订阅」,悬停卡片翻成 accent「+ 订阅」就地订阅
   (沿条目列 `.reader-entry-unsub` 同语);快照 `subscribed:false` 是编排时的事实,订阅后即不再画(不必等重编)。
4. 移动壳同源:说明行 flex-wrap 折两行(身份句 / 尾句 + 动作链左对齐到文字起点),卡头理由照旧。

## 2. 实现面

- `models/analysis_contracts.py`:`PERSONAL_DIGEST_EXTERNAL_MIN_QUALITY_SCORE=6.0` / `..._PER_SOURCE_MAX=2` / `..._LIMIT=5`;
  `DigestArticleCandidateDTO` 加 `subscribed`(默认 True)与 `interest_tag_codes`(None = 回退 tag_codes 全集)。
- `services/digest_selection.py`:`DigestSelectionPolicy.external_min_quality_score / external_per_source_max`;
  `eligible_for_selection`(屏蔽 → 按订阅态取门槛 → 订阅外必须命中)、`interest_codes_of`、`interest_only_policy`
  (target = 兴趣半大小、ratio 1.0);`_choose_at_cap` 对订阅外用固定 `external_source_cap`;放宽循环的
  `maximum_cap` 只按订阅内行算;`_sort_interest` 与 `_coverage_order` 保持订阅内先于订阅外;
  订阅外命中的理由句「命中你的兴趣「X」,来自你未订阅的「源」,按新闻价值入选。」。
- `services/personal_digest.py`:`selection_policy(session)` 读 KV;`_query_candidate_rows` 加
  `interest_tag_codes`(exists 子查询,主标签或相关度过线)与 `exclude_source_ids`;`load_interest_candidates`
  (全站可见 ∩ 命中 ∩ ≥ 门槛,减订阅源,经 `_reader_visible_rows` 去隐藏/私有);`_candidates_from_rows`
  按 `followed_codes` 算 `interest_tag_codes`、打 `subscribed`;`generate_personal_digest` 走 `_load_union`
  (订阅池 ∪ 订阅外兴趣池,36h 不足退 72h)、订阅为空且有兴趣时 `interest_only_policy`、写
  `selection_stats_json`(candidate_count / subscribed_candidate_count / external_candidate_count / window_hours /
  followed_count / muted_count / selected_count / interest_hits / external_hits / breaking_count / interest_only /
  external_min_score / external_per_source_max);条目 `ranking_features.subscribed` 与快照 `subscribed`;
  `start_personal_digest_edition` 只在「订阅空且无兴趣」才走空订阅分支;降级区(最新更新)的命中也按同一判据。
- `api/routers/personal_briefs.py`:`serialize_edition` 透出 `selection_stats`(历史为 null)。
- `api/routers/analysis_ops.py`:`personal_digest_external_min_score` / `personal_digest_external_per_source_max`
  两旋钮,响应加 `personal_digest_selection`。
- 迁移 `b2d8e4f6a9c1`:`personal_digest_editions.selection_stats_json` 可空列,带收养回放列守卫。
- 前端 `PersonalBriefPage`:`brief-byline` 说明行(三态尾句 + 调整兴趣 / 去发现来源)、`whyOf` 四词理由
  `brief-card-why`、`brief-card-unsub`、脚部 chip 只剩主题标签、空订阅 + 无兴趣的空态双动作;
  `ReaderTab` / `MobileReader` 传 `onManageInterests`(发现页兴趣段)与 `onSubscribeSource`;
  `AdminTaxonomyPanel` 新增「早报订阅外兴趣」卡;`index.css` 三组类(容器 < 720 折行)。

## 3. 测试

- `tests/test_digest_selection.py`:订阅外门槛与「必须命中」、订阅内命中排前、订阅外硬上限不放宽、
  policy 覆盖、`interest_tag_codes` 命中 / 屏蔽看全集、`interest_only_policy`、越界旋钮。
- `tests/test_personal_digest.py`:并集取到订阅外命中(6.5 进、5.5 不进、相关度 0.5 不算命中)+ 冻结边界仍是订阅源
  + `selection_stats`;隐藏 / `user_rss_` / owner 私有 / 公共日报排除;订阅空 + 有兴趣 → 兴趣半版本(每源 ≤ 2);
  两者皆空 → `empty_subscriptions`;兴趣半无合格稿 → 诚实空版;订阅内相关度阈值与透镜同尺;KV 旋钮读取与钳制。
- `tests/test_analysis_personal_api.py`:§5 用例改为「退订清空 → 重编出兴趣半版本 → 清空兴趣 → 才空报」;
  管理面旋钮端点。

## 4. 明确不做 / 边界

- **feed / MCP 不跟并集**:交付契约仍是订阅域;IM bot / 聚合接口不受影响。
- **重大事件通道不变**:它已是跨订阅;订阅外的兴趣命中与头条各自准入,同一篇先成头条则提级(既有逻辑)。
- **不为订阅外条目改冻结 / 就绪语义**:`expected_source_ids` 仍是权限边界与「等谁」的集合。
- **阅读器兴趣轴不动**:早报只是消费方,透镜的准入判据(0.8)复用不改。
- **样页里的「今日最重要」措辞不采用**;每卡一句人话长理由不回来(issue #23 已退役);欢迎卡不做;说明行无动画。
- **回放待补**:两枚参数没有生产样本背书,先按推荐值上线,观察「订阅外命中占比 / 单源集中度 / 空报率」再调旋钮。
