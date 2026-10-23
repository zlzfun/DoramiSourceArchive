# 多源事件层:同事件归并、热点榜与早报去重共用一份归属(issue #121 + #103)

> 状态:进行中——**wave 1 纯后端已合入 main**(PR #220,2026-10-23);**wave 2 早报同事件去重(#103)已合入 main**(PR #222,2026-10-24,落地记录见 §5.3,检视与目检记录见 §12);wave 3 Agent / MCP 排队;48h 事件窗维持,观察期做 36 / 48 / 72h 单变量对比(§5.3 末)。R2 路径设计经方向调研实测修订,见 [`issue-121-event-layer-research.md`](./issue-121-event-layer-research.md)。
> **拍板(2026-10-23,用户,记录于 issue #121 评论)**:主路线 = FTS5 三通道词面召回 + 辅助模型增量分配(首篇关窗 48h、代表稿描述、只标题、回显校验);向量召回(外部 embedding API)不进首版,留再评估触发。其余 §10 条目按「建议」执行,未另行反对。
> **拍板(2026-10-23 晚,用户目检样页后)**:前端「热点」段**搁置**——三方向样页(`docs/design/issue-121-hot-events/`)都待商榷,B 较优但非视觉定稿;用户计划之后统一重构文章榜 / 播客榜 / 热点的前端并重审文章榜定位,热点段随该重构再出样页。wave 1 以纯后端收口(事件层 + 读者 / 管理 API + 同步流 + 回放脚本)。
> 关联:[issue #121](https://github.com/zlzfun/DoramiSourceArchive/issues/121)(多源事件聚合与热点榜,2026-09-20 需求记录)·
> [issue #103](https://github.com/zlzfun/DoramiSourceArchive/issues/103)(个人早报不做同事件去重)·
> #121 协作者评论(2026-09-30,Agent 取用事件:窗口查询 / 稳定 ID / 合并可追溯 / 令牌范围)·
> 已落地零件:公共日报三层去重(`services/daily_brief.py`)、早报重大事件通道实体并查集(v3.50,`services/digest_selection.py`)、
> 标签趋势榜(#154,`services/rankings.py`)、Archive Sync v3 八流(`archive_sync_v2.STREAMS`)。
> 本文接手 #121 的「待比较路线 / 待回答问题」,结合 #103 方案方向与协作者评论,给出一条可分波落地的路径;
> 参数与表结构为建议,等拍板后开 `feat/issue-121-event-layer` 实现。

## 0. 结论(TL;DR)

1. **平台级持久事件层**:每篇可归并的文章在分析成功后被增量分配到一个稳定事件(`events` / `event_members`),
   外网 authority 唯一写者,经 Archive Sync 新流同步到内网。三种消费端共用这一份归属:热点榜展示、个人早报去重、Agent 查询。
2. **机械层只做召回,归属判断交辅助模型**。本机库回放(§1.2)与 48 天方向调研(研究报告 §3)一致:规则判定上限 F1 0.24,纯向量聚类 0.33,
   LLM 事件签名 35% 一致率;「召回 + 模型判定」是唯一过线的路线。**R2 关键修正**:事件按**首篇**时间关窗 48 小时(不按末篇续期)、候选事件用**代表稿**描述、
   提示词带正向准则 + 禁并后续 / 评测 / 下一版本 + 批内「共同事实」——R1 口径实测会让热点簇滚雪球(最大簇 28–31 篇、精度 0.2–0.26),修正后精度翻倍。
   拿不准一律新开(不回溯实测只损失 0.01–0.02 F1,稳定 ID 零代价)。
3. **热度口径(R2 简化)**:首版排序 = 可见发布主体数 ↓ → 最新首见时间 ↓;实测 24h 半衰期热度与纯来源数的 top-5 在 35/39 天相同,衰减作可选项后加。
   时间一律用成员首见时间(多源 publish_date 被抓取回填到 08:00)。入榜 ≥ 2 个发布主体;趋势只在 ≥ 2 个日快照时显示;价值分只参与代表稿选择。
   **供给是硬约束**:本机 52 源语料理想归并下每天中位只有 4 件可上榜,产品定义为「今日热点,最多 10 件,不足不填」,生产源数下的供给在 wave 0 实测。
4. **分四波**:0 生产样本回放定门槛 → 1 事件层 + 热点榜(含内网同步、管理员修正)→ 2 早报同事件去重(#103)→ 3 Agent / MCP 事件查询。
5. **明确不做**(首版):LLM 事件级综合摘要、embedding 召回、讨论人数 / 互动数换算、读者主动追踪推送、论文 / 汇编 / 播客入事件层。

## 1. 现状与实证

### 1.1 代码事实

| 零件 | 现状 | 可复用点 |
|---|---|---|
| `DuplicateGroupRecord` / `DuplicateGroupMemberRecord` | 有表、全库零写入方(本机与 v3.50 实证一致);早报 `_candidates_from_rows` 已装填 `duplicate_group_id`,`_event_key` 已优先命中 `group:<id>` | 消费端挂钩已就位,只缺写入方;表语义(指纹唯一、一文一组)按事件层改造 |
| 公共日报 `precluster_same_event` → `dedup_clusters` → `cross_day_dedup` | 每次日报生成临时算,不落库,结果只服务当天那一篇日报 | `_pick_cluster_representative`(官方分差门限)、`_titles_look_same`(数字串一致)、`DEDUP_SYSTEM_PROMPT` 可直接搬 |
| 早报重大事件通道 `_group_breaking_events` / `_representative_key` | 只服务头条 ≤ 2 条;按 `entity.*` 并查集 | 代表规则「官方 > 非社交 > 分数 > 发布更早」沿用 |
| 标签趋势榜 `rankings.py` | 07:00 日快照、`previous_rank`、全量落 occurrence 再按当日隐藏源策略过滤 | 快照 / 趋势 / 隐藏源处理范式、榜单页壳 `RankingsPage.jsx` |
| 来源元数据 `SourceConfigRecord.source_owner` | 本机 43 个活跃平台源 37 个已填 | 「独立发布主体」键;X 源一账号一 `source_id` 天然即发布主体 |
| 中文标题缓存 `extensions_json.translation_zh_title` | 早报标题本地化已写回(v3.52.1) | 事件标题优先取它 |
| Archive Sync v3 | 八流独立 checkpoint,`podcast_texts` 是「按能力协商新增流」的先例 | 事件流照此新增 |

### 1.2 本机库最后 7 天回放(2026-09-10 → 09-17,只读)

| 指标 | 数值 |
|---|---|
| 成功分析文章 / 带实体标签 | 59 / 43 |
| 有中文标题缓存 | 4 |
| A 纯实体并查集:分量数 / 最大分量 | 4 / 36 篇(entity.openai ∪ entity.anthropic 把一周并成一件事) |
| B 实体 ∧ 标题相似(difflib ≥ 0.75 ∧ 数字串一致,原题或中文题任一):共享实体候选对 / 判同对 | 246 / **0** |
| 同日跨源共享实体却未并对 | 50(抽样全是不同事件,但也含「Dario 全球共管计划」中英两稿这类真同事件) |

结论:实体目录只有 45 个码且粒度是公司 / 产品,只能当召回键;标题相似在跨语言样本上失效;归属判断必须由模型做。
回放脚本见 §8,wave 0 对生产 7 天导出复跑并接入真实模型判断。

## 2. 事件对象与不变量

- **事件 = 同一次发布 / 公告 / 报告 / 事故**在 48 小时内的多源报道集合(不同来源、语言、角度都算)。评测、后续进展、下一版本、反应稿**另立事件**,
  并以 `story_id` / `follows_event_id` 链接(wave 2 用模型判「后续」填;实测 34% 多篇事件是先前事件的后续、四成间隔 > 72 小时,并簇并不回去,链接才能接上)。
- **一篇文章最多属于一个事件**(沿用 `duplicate_group_members` 的唯一约束)。论文(`content_kind=paper`)、汇编(`digest`,含日报记录)、播客不进事件层,
  从根上避免「综合报道链式误并」;社交帖进(顺手收口 backlog「跨容器去重:一条推 + 一篇 blog」)。
- **私有内容永不进事件层**:`user_rss_` 源、`owner_username` 非空源、凭据源的文章不参与分配;隐藏源文章可入事件但读取时按当日隐藏策略过滤,
  标题 / 摘要 / 来源名单 / 计数全部只从可见成员算。
- **事件 ID 稳定**:整数主键在 authority 分配;合并写 `event_redirects(old → new)`,按旧 ID 查询返回 `merged_into` 与新事件;拆分 = 把成员摘出成新事件,
  原事件 ID 不变。
- **状态**:`open`(距**首篇**首见 ≤ 48 小时,接受分配)→ `closed`(可查不可加,后续同题报道另立并链接)→ `merged`。
- **代表稿**:每次成员变动后按 `_representative_key` 重算(官方 > 非社交 > 分数 > 发布更早);事件标题 = 代表稿中文标题缓存 → 原题;
  事件简述 = 代表稿分析摘要;「最新进展」= 报道时间最新成员的摘要。首版不生成事件级综合摘要。
- **时间口径**(显式三种):`publish_date` 只用于时间线展示;成员 `first_seen_at`(authority 首次写入,同步 / 重传不变)决定热度与窗口;
  成员报道时间 `report_at = clamp(publish_date, first_seen_at − 72h, first_seen_at)`,防止回填旧文和缺发布时间的文章重新变热。

### 2.1 表(建议;同一迁移内删除空的 `duplicate_groups` / `duplicate_group_members`)

| 表 | 关键列 |
|---|---|
| `events` | `id`, `status`, `title`(冻结于最近一次重算), `representative_article_id`, `member_count`, `publisher_count`, `first_seen_at`, `last_member_at`, `story_id`, `follows_event_id`, `assignment_version`, `authority_id`, `created_at`, `updated_at` |
| `event_members` | `event_id`, `article_id`(唯一), `publisher_key`, `report_at`, `first_seen_at`, `assigned_by`(`llm` / `url` / `singleton` / `manual`), `confidence`, `prompt_version` |
| `event_redirects` | `old_event_id`(主键), `new_event_id`, `reason`, `created_at` |
| `event_assignment_ledger` | `article_id`(主键), `status`(`pending` / `assigned` / `skipped` / `blocked`), `attempts`, `next_attempt_at`, `last_error`, `blocked_event_ids_json`(管理员摘出后禁止自动回流) |
| `event_rank_snapshots` | `snapshot_date`, `event_id`, `rank`, `heat`, `publisher_count`(07:00 与趋势榜同刻落;只存上榜行) |

`DigestArticleCandidateDTO.duplicate_group_id` 改名 `event_id`,`_event_key` 返回 `event:<id>`。

## 3. 归并流程(增量、批量、可恢复)
挂钩点:`run_analysis_cycle` 每轮批次跑完后调用 `event_assignment.assign_pending(engine, llm_config)`,自带 try / except,失败不影响分析结果。
入池条件:分析 `succeeded` 且非私有、内容类型允许、ledger 无记录 → 写 `pending`。

1. **机械直连(零 LLM)**:规范化 URL 与现有成员相同 → 直接入该事件(`assigned_by=url`),**但 `docs_*` / `github_*` 等共享页面 URL 的源不走这一步**(否则整条变更日志并成一个事件);
   归一化标题完全相同(≥ 8 字、数字串一致)同样直连(`assigned_by=title`)。
2. **候选召回**:对每篇 pending 文章,取 `open` 事件(距首篇首见 ≤ 48 小时)中 ① 与它共享 ≥ 1 个 `entity.*` 码的;② 其成员被该文**标题 / 摘要 / 中文标题缓存**三路 FTS5 top-10 命中的
   (实测三通道并集召回 87–88%、约 20 候选;一中一外的对占 37%,译中标题通道是关键,生产已有缓存);③ 向量 top-10(外部 embedding API)**首版不做**(2026-10-23 拍板),再评估触发见 §10。
   候选上限 20。零候选 → 直接新建单成员事件(`singleton`),不问模型。
3. **一次辅助模型调用判全批**:输入 = 本批 pending 文章(idx、标题、来源角色、发布日;**不附摘要**——实测摘要只换召回、拖精度且贵 11%)+ 候选事件(eid、**代表稿**标题 + 摘要前 120 字、成员数、首篇日)。
   提示词三件套:正向准则「不同来源 / 语言 / 角度报道同一次发布、公告、报告、事故,相差 1–3 天仍算同一事件」;禁并「后续进展、评测、下一版本、同主题不同事」;
   批内两篇要并须给出一句**含具体产品名 / 版本号**的「共同事实」,主题级事实(「某公司发布 AI」)不算;两条结构规则在模型前后机械执行:同源 + 标题全同 + 非同日 → 不并(连载 / 日更栏目);并入既有事件须 ≤ 1 天或模型明确指向代表稿那次发布。输出 `{"assignments":[{"idx":0,"echo":"标题前 12 字","event":"e123"|"new:1","fact":"…"}],"new_events":[{"key":"new:1","title":"…"}]}`,
   `echo` 与标题不符的条目按单例处理并计数(防 idx 滑位)。走 `llm_config.for_aux()`,`usage_meta` 用途 `event_assignment`、计到 `system`。
4. **落库**:同一事务写成员、重算代表与计数、ledger 置 `assigned`;新事件标题优先用中文成员标题 → 翻译缓存 → 模型给的题 → 代表稿原题。
5. **失败与恢复**:模型失败 / 输出非法 → ledger `attempts+1`、指数退避;`pending` 超 72 小时 → 机械新建单成员事件并标 `skipped`,保证早报键始终可用。
   重启安全:一切状态在 ledger,无内存队列。
6. **回灌守卫**:`assignment_version` 变更(提示词或召回改动)只影响新文章,不自动重跑存量;回灌走显式管理动作。
7. **不回溯**:不做事后合并(实测模型自己的合并 720 对只合 5 对且 3 对错;上帝视角也只 +0.012 F1);误并靠管理员摘出 / 合并修正。

成本:生产约 100 篇 / 天,每轮分析周期至多一次辅助档调用,实测 220–250 token / 篇(只标题 130),日 ≈ 2.5 万 token ≈ ¥0.05 量级;
与公共日报 `dedup_clusters` 的调用相互独立(wave 2 再让日报消费事件成员做预聚类)。

## 4. 热度(可解释、可复算)
```
rank(event, now) = (可见发布主体数 ↓, 最新成员 first_seen_at ↓)      # 首版
heat(event, now) = Σ_{publisher} max_{i} 0.5 ^ ((now − first_seen_at_i) / 24h)   # 可选,后加
publisher_key    = 注册表 source_owner 非空 ? source_owner : source_id;合并表:sam_altman → openai、google_deepmind → google 等
```

- 实测(研究报告 §3.5):半衰期 12 / 24 / 48h 的 top-5 集合 38 / 39、33 / 39 天相同;热度 vs 纯来源数 35 / 39 天相同 → 热度值不进首版 API(AIHOT 也不公开热度值,只给 sourceCount / latestAt)。
- 同一机构多个采集渠道只计一份;同一文章被两个源重复收录已被机械直连并掉;按篇数计数时 17% 的 top-10 席位是单主体多发,去重必须。
- 角色不加权;价值分不进排序,只决定代表稿。点赞 / 转发 / 讨论人数不进首版。
- 入榜:当前 48 小时窗内 ≥ 2 个可见发布主体;**榜最多 10 件,不足不填**;「新」标签 = 首篇首见 6 小时内。
- 时间一律 `first_seen_at`:多源 `publish_date` 是抓取回填值(aiera 75%、anthropic 77%、openai 46% 落在 08:00–08:02),用它衰减与打「新」都是假的。
- 读取时现算;07:00 落 `event_rank_snapshots` 供 `previous_rank` 与趋势;少于 2 个快照不画趋势。
- 榜头注明统计范围「哆啦美监测范围内的报道热度」;采集覆盖变化不做补偿。

## 5. 读者面、API、MCP

### 5.0 读者 / 管理 API 契约(wave 1,R2 定稿;前端样页与后端按此对齐)

- `GET /api/reader/events?window_hours=48&limit=10`(游客白名单;隐藏源与私有源在读取时剔除,可见发布主体 < 2 的事件不出现)→
  `{generated_at, window_hours, snapshot_date|null, empty_reason: null|"no_events", items: [{event_id, rank, previous_rank|null, title, title_lang: "zh"|"en", summary,
  publisher_count, member_count, first_seen_at, latest_at, is_new, representative: {article_id, title, source_id, source_name, source_role, url, publish_date},
  publishers: [{key, name, count}], story: {follows_event_id|null, follows_title|null}}]}`。排序 = publisher_count ↓ → latest_at ↓;`is_new` = 首篇首见 ≤ 6h;`previous_rank` 来自前一日 07:00 快照;最多 10 件,不足不填。
- `GET /api/reader/events/{id}` → 榜单字段 + `members: [{article_id, title, title_zh|null, source_id, source_name, source_role, content_shape, url, publish_date, report_at, is_representative, summary}]`(report_at 倒序,只含可见成员)+ `followups: [{event_id, title, first_seen_at}]` + `merged_into: null|event_id`(旧 ID 命中 redirect 时返回新事件并带此字段)。
- `GET /api/admin/events?status=&q=&limit=`(管理台抽屉列表)· `POST /api/admin/events/{id}/merge-into/{target}` · `POST /api/admin/events/{id}/members/{article_id}/detach`(摘出成新单成员事件并写 ledger `blocked_event_ids`)——全部入审计。
- 内部:`services/event_assignment.assign_pending(engine, llm_config)`(分析周期末尾 + 10 分钟兜底 interval job)· `services/event_board.build_rank_snapshot(engine, date)`(cron `0 7 * * *`,job id `event_rank_snapshot`)。

### 5.1 榜单页第三段「热点」(**搁置**,随榜单前端统一重构 issue #221;以下为原设想)

- 落点:`RankingsPage.jsx` 的 shape 分段从「文章榜 / 播客榜」加「热点」,不新开页面;发现页「榜单」入口与轨底 `reader-ranking-entry` 不变;移动壳同列表。
- 行:事件标题 · 来源 N(发布主体)· 热度 · 更新时间 · 排名变化;点开抽屉:代表报道(可进阅读窗)、来源列表(按发布主体分组、可点原文)、
  报道时间线(按 `report_at`)、最新进展(最新成员摘要)。
- 事件题中文优先(实测代表稿标题 52.8% 非中文,92% 事件有中文成员):中文成员标题 → 翻译缓存 → 原题;官方稿做一手链接。详情页展示「接前报」/「后续」链(story)。
- 读者面文案不出「归档 / 采集 / 聚合」等架构词;空榜文案「近 48 小时还没有被两家以上报道的事件」。
- 游客白名单加 `GET /api/reader/events`、`GET /api/reader/events/{id}`;隐藏源成员在读取时剔除,可见发布主体 < 2 的事件不上榜。
- 样页:拍板路径后按工作流先出桌面 + 手机样页再实现。

### 5.2 管理员修正(wave 1,入抽屉、全入审计)

- `POST /api/admin/events/{id}/merge-into/{target}`:写 redirect,成员迁移,代表重算。
- `POST /api/admin/events/{id}/members/{article_id}/detach`:成员摘出成新单成员事件,ledger 记 `blocked_event_ids`,自动分配不再回流。
- 路径加入 `admin_audit.should_audit` 清单;管理台事件列表只在榜单运维抽屉里,列头即操作。

### 5.3 早报与日报消费(wave 2,即 #103)

- `_event_key` 命中 `event:<id>`;`_choose_at_cap` 的 `event_keys` 跨三条通道共享(头条已代表的事件,兴趣 / 分数通道不再出同事件另一篇)。
- 条目快照加 `related_article_ids` / `extra_sources`,页面出「另见 N 篇」;`selection_stats_json.same_event_merged` 进编排说明行。
- 跨期:事件任一成员出现在昨天版面 → 今天整个事件抑制(与重大事件通道前两期实体抑制对齐,头条口径不变)。
- 公共日报 `dedup_clusters` 前加一步:同事件成员机械并簇,再把剩余交 LLM;LLM 失败兜底不变。

**wave 2 落地记录(2026-10-23,分支 `feat/issue-103-brief-event-dedup`)**:

- 事件键 `digest_selection.event_key`(事件层 `event_id` → 规范 URL → 归一化标题)公开给编排方;头条分组 `_group_breaking_events` 在实体并查集之上**恒按事件键并**(同事件的官方稿与无实体标签的推文此前会被分到两簇)。
- 跨通道:头条代表的事件键集合传回精选,兴趣 / 分数通道剔除同键候选后按同一策略重选(原「提级文章 id 剔除」是它的子集)。
- 精选通道的同事件代表(**合入后补**,2026-10-24,#103 验收第一条):同事件候选在分通道**之前**先按「官方 > 非社交 > 分数 > 更早」定代表(`digest_selection._collapse_same_event`,与头条通道 `_representative_key`、事件层榜单同一口径),其余成员不参选、进代表的「另见」;订阅池与订阅外兴趣池随之带上来源角色 / 形态。此前代表是通道排序里先到的那篇,官方稿 8.0 会让位给媒体转述 8.5,也会出现「选分低的那篇只为凑更大集合」。
- 跨期:昨天版面(任一分区)条目所属事件按事件层**当前归属**查(管理员事后合并 / 摘出以改后为准),今天订阅 ∪ 兴趣池与降级回退整组剔除;被剔除的候选数记 `selection_stats_json.same_event_previous_day`(头条池另记 `_all`)。**升级例外(用户 2026-10-23 目检拍板)**:头条池只剔除昨天**以头条身份**出现过的事件——昨天只是精选里一条低分稿、今天同事件内出现够头条资格的报道(官方一手 ≥ T 或多源印证)时,允许以头条出现,它的「另见」会列出昨天那篇;昨天就是头条的事件照旧整组抑制,既有的前两期实体抑制继续叠加,所以一个事件至多成一次头条。R1 时实现方曾按「头条也受昨日事件抑制」实现(Codex 同意),目检时用户指出这会吞掉事件升级为重大新闻的那一刻,改为例外。
- 官方多形态发布(同一事件的新闻稿 + 播客 + 论文,用户目检追问):播客与论文**不入事件层**(§2),各走「播客精选」「深度阅读」按内容价值入选,新闻稿走事件层与新闻通道——同日三者并存是不同分区里的不同形态,不算重复,「另见」也不列它们;论文也在新闻池与头条池(只排除播客),头条池里它与新闻稿共享实体码时仍按既有实体并查集归一簇只出一条,精选里按各自的 URL 键各算一条(#178 内容分类的既有行为)。把同一事件的多形态串成一组,是后续 story / 关联内容的事(随 #221 拍板)。
- 「另见 N 篇」= 编排时事件层里该事件的**其它读者可见成员**(`event_board.visible_member_links`,与榜单同一可见性判定:隐藏 / 私有 / 凭据源成员不出现;订阅外可见成员照列),按代表稿序最多 10 条写进条目快照 `snapshot.related_sources`(article_id / title / title_zh / source_id / source_name / source_role / url / publish_date)与 `snapshot.event_id`;不是「候选池里被并掉的那几篇」——读者关心的是同一事件还有谁报道了,与该篇是否过了早报门槛无关。页面 chip 的 N 数**篇**(用户 2026-10-23 目检拍板:原按来源计,同源两篇算一会少报),展开逐篇,链接开新窗。
- `selection_stats_json.same_event_merged` = **订阅 ∪ 兴趣池**里事件键已由本期某条(任一通道)代表的其它候选数——与说明行「N 篇里选出 M 篇」的 N 同范围(codex R1 P2);含头条池 / 价值分区池的全版口径另记 `same_event_merged_all`(同理 `same_event_previous_day` / `_all`),只供排障。按条目分别记入 `ranking_features_json.same_event_merged`;编排说明行出「同事件合并 N 篇」。
- 降级回退「订阅源最新更新」:多取三倍再按事件去重、剔除昨日事件与**本期头条及其事件**(codex R1 P2),去重后不足不补。`POLICY_VERSION` → `personal-digest-v2`;历史 v1 快照无 `event_id` / `related_sources` 键,页面按缺省处理。
- 公共日报:`precluster_by_event`(按 `event_members` 当前归属机械并簇,代表规则同 LLM 簇)先于 `precluster_same_event`,再交模型;`merge_same_day` 同用一张 `event_ids_by_article`;模型失败时两层机械结果都保留。
- **有意不做(本波)**:`story_id` / `follows_event_id` 的「接前报」链接——是否做故事线已列入 issue #221 待拍板;「深度阅读」「播客精选」不参与事件键**跨通道**剔除(分区内仍按事件键去重;论文 / 播客不入事件层,教程 / 观点可带 `event_id` 但与新闻通道互不剔除,行为不变)。
- 本机沙箱自查(真实库副本 + 真实辅助模型分配 2026-09-03 批 237 篇:直连 6、模型归并 4、单例 227,12 次调用 10.8 万 token):09-03 版 31 篇候选选 10 篇、同事件合并 5 篇、三条带「另见」(Gemini 3.8 Flash 官方稿 + DeepMind 博客 + IT之家两篇归一条);09-04 版昨日事件整组抑制 4 篇;09-16 版 web_aiera 与量子位的同一科研报道归一条。亮 / 暗 / 移动壳截图零控制台错误、零水平溢出。

### 5.4 Agent / MCP(wave 3,按协作者评论)

- `GET /api/public/subscriptions/{id}/events?updated_since=&q=&tag=&limit≤100&cursor=` 与 `GET …/events/{event_id}`:令牌范围内可见成员算出的事件视图;
  参数说明时区(UTC ISO)与筛选的是「更新时间」;旧 ID 命中 redirect 返回 `merged_into`。
- MCP 加 `list_events` / `get_event` 薄适配,复用 `_resolve_scope`;契约文(wave 3 新建 `contracts/events.md`)。不做推送 / 订阅回调 / Agent 改归属。

## 6. 同步与部署

- 新流 `events`(含 members / redirects / rank_snapshots 三张表的 upsert | tombstone),排在 `analyses` 之后,authority 唯一写者;
  接收端 `authority_id` 围栏,内网本地分析的 `user_rss_` 文章永不分配事件;能力名 `events_v1`,接收端缺能力则跳过该流。
- `event_assignment_ledger` 是节点本地表,不同步。
- 向量召回若启用(§3 步骤 2 ③):向量由外部 embedding API 产出(DeepSeek 无 embedding 端点;首选阿里百炼 text-embedding-v4,1024 维、首百万 token 免费、OpenAI 兼容;SiliconFlow 托管 bge-m3 免费档兜底),
  存文章侧 BLOB 列或独立表,只在 ±72 小时窗内做 numpy 点积(20k 条 < 1 ms),**不引入 sqlite-vec**;凭据经 `services/credentials.py`;未配置时退化为纯 FTS5 召回。符合 RAG 退役方案 §4 的「轻形态」条件。
- 外网先部署(生成事件),内网后部署(消费);内网 role 仍为 `all`,事件分配任务按 `analysis_authority_id` 为空的文章才跑(内网实际为零)。
- 定时任务:`event_rank_snapshot` cron `0 7 * * *`,经 `add_cron_job`(自带 `misfire_grace_time=300` + `coalesce`)。
- 迁移:新增五表 + 删两表 + DTO 改名;`alembic revision --autogenerate` 后人工审阅,`tests/test_migrations.py` drift 测试守卫;
  分析流负载不变,`ANALYSIS_PAYLOAD_GENERATION` 不动。

## 7. 分波

| 波 | 交付 | 门禁 |
|---|---|---|
| 0 回放 | `scripts/replay_event_assignment.py`(从调研脚本改出):对生产 14 天导出跑 §3 全流程(含真实辅助模型;FTS5 召回 vs 向量召回两档),输出事件数、≥ 2 发布主体事件数 / 天、误并 / 漏并抽检、调用成本 | 用户在生产只读导出上跑;误并(抽 50 组目检)≤ 10%、≥ 2 发布主体事件中位 ≥ 4 件 / 天,否则榜单改「今日热点 3–5 件」或只做 #103;本机 52 源语料中位 4 件是下界 |
| 1 事件层(纯后端) | 表 / 迁移、分配服务、排名快照、读者 / 管理 API、管理员合并 / 摘出、Archive Sync `events` 流、游客白名单、回放脚本;**前端热点段搁置**(随榜单统一重构) | 交叉检视 + 全量套件 + CI 绿;生产观察一周看误并修正次数与每日 ≥ 2 主体事件数 |
| 2 早报去重 | #103 全部验收项(事件键跨通道、另见 N 篇、跨期抑制与升级例外、编排说明行)+ 日报预聚类消费事件(**已合入**,PR #222,记录见 §5.3) | 7 天样本回放:4 条 X·OpenAI 同发布推文、GPT-6 官方稿 vs 媒体稿合并正确(需生产导出,待发版后在生产观察期核;本机库副本回放见 §5.3) |
| 3 Agent 事件查询 | 公共事件端点 + MCP 工具 + 契约文 | 令牌范围隔离测试;合并后旧 ID 可追溯 |

每波按 `dorami-issue-loop` 走:worktree 分支、自测、沙箱、文档同波(`version-history` / `backlog` / `decisions`)、交叉检视、目检、squash。

## 8. 验收与回放脚本

- 回放脚本首版在 scratchpad(本机库版本已跑出 §1.2 数字),wave 0 入库为 `scripts/replay_event_assignment.py`:输入导出 JSONL 或库路径,
  按时间顺序逐批模拟分配(召回 + 模型),打印每日事件数、入榜事件数、最大事件成员数、每组成员标题供目检,以及 token 用量。
- 验收(对应 #121 验收方向):多语种 / 多来源同事件归组;同公司不同事件与相近版本不误并(数字串规则保留在 URL 直连与提示词里);
  重复抓取 / 回填 / 同步不重复计热(`first_seen_at` 不变);合并后旧 ID 可追溯;隐藏 / 私有内容不经标题、摘要、来源名单、计数泄露;
  热度可复算(端点返回各发布主体贡献项);缺少快照时不画趋势。

## 9. 风险与缓解

- **实体目录覆盖不足**(45 个码,World Labs 这类不在目录)→ 召回靠 FTS 标题通道补同语种;跨语种且无实体的只能漏并,靠管理员合并或后续加「展示标签名」召回(再评估触发)。
- **模型误并污染稳定 ID** → 提示词「拿不准视为不同」+ 候选上限 40 + 管理员摘出不回流;观察期统计修正次数。
- **单成员事件堆积** → 不上榜、不展示;`closed` 事件只在早报键与 Agent 查询里有用;保留 90 天后随文章保留策略级联。
- **分析周期被拖慢** → 分配调用在批次之后、独立 try / except、单次调用上限 40 候选 × 20 文章;超时只影响 ledger 重试。
- **内网旧版本不认新流** → 能力协商跳过,内网榜单段在缺数据时显示「暂无」。

## 10. 待拍板
1. 表方案:新建 `events` 五表并删除空的 `duplicate_groups` 两表,DTO `duplicate_group_id` → `event_id`(建议)。
2. 事件粒度(R2):事件 = 同一次发布 / 公告 / 报告 / 事故的 48 小时内多源报道;后续进展、评测、下一版本另立事件并以 `story_id` / `follows_event_id` 链接(建议)。
3. **已拍板(2026-10-23)**:归属判断以辅助模型为主体,机械层只做 URL / 同题直连与召回;事件按首篇关窗 48h、代表稿描述、只标题输入、三件套提示词、回显校验、不回溯。
4. **已拍板(2026-10-23)**:召回层 = FTS5 三通道(标题 / 摘要 / 译中标题)+ 实体码,**不引入外部 embedding API**;再评估触发:wave 0 或观察期里一中一外同事件的漏并率明显高于本次实测(译中标题通道 top-10 召回 0.68),届时评估百炼 text-embedding-v4。
5. 热度(R2):首版只排「可见发布主体数 → 最新首见时间」,热度值不进 API;时间用首见;publisher 合并表(建议)。
6. 榜单产品定义:「今日热点,最多 10 件,不足不填」;落点榜单页第三段「热点」;样页在路径拍板后出(建议)。
7. 范围排除:论文 / 汇编 / 播客不入事件层,社交帖入(建议)。
8. 分波顺序 0 → 1 → 2 → 3;wave 0 用生产 14 天导出实测供给与误并,门槛见 §7(建议)。
9. 不做:LLM 事件签名、纯向量聚类、日报聚类持久化为事件层、sqlite-vec、事件级综合摘要、读者追踪推送、Agent 改归属(建议;前四项有实测依据)。

## 11. 检视记录(wave 1 后端,2026-10-23)

- 实现方:Claude Code(Fable 5.1,主代理 + 子代理:模型层 / 分配服务 / 读取侧 / 同步流各一);检视方:Codex(gpt-6-astra,`model_reasoning_effort=ultra`,只读 `codex exec`,全部 findings 附内存 SQLite 复现程序)。往返文件在 worktree `.review/`(不入库)。
- **R1**(commit `99fc1ca`):5 P1 / 5 P2 / 2 P3。P1:隐藏成员信息经模型拟题泄露;同步 payload 未纳入凭据源且事件元数据未按可导出成员重建;管理员合并后迟到分配仍写入 merged 事件;同步成员缺文章被跳过后无恢复路径;回放脚本「只读导出」实际经 `DatabaseStorage` 写源库。P2:候选集合 / 结构规则可从批并集、同题直连、new 组三处绕过;同步事件进入本地候选池导致整批回滚;远端 tombstone 碰本地同 ID 事件覆盖状态;入池扫描被窗外旧文占满;分配侧采纳失效译题。P3:story 链绕过可见性;story 跨页不补链。表态:除「story 跨页补链」记 wave 2 观察外全部接受,成批修复(commit `bed0463`)。
- **R2**(对照清单复检):6 已修、5 有缺口、新引入 2 P1——隔离重放复用过时目标写入已合并事件(N1);backlog 新旧待补行互相覆盖丢归属(N2);F1 缺口 = 待补成员 / 本地成员不完整时模型题仍露;F2 缺口 = merged 旧事件保留历史标题导出。表态全部接受(commit `8e3be3e`):读取侧「不完整事件」判定(隐藏 ∨ 待补 ∨ 本地成员数 < 缓存计数)禁模型题;merged 事件剥离导出 + 接收端清空;隔离重放逐篇重验目标;backlog 按 (authority, article) 单行 + revision 依据;回放摘要比较降级告警。
- **R3**(定向四点):F1、F2 关闭;N1 余一条组合路径(同源跨日同组 + 中间篇失败 + 隔离重放复用 key_events 未对现有成员做结构校验);N2 余迁移搬旧行按最小 ID 取行(只影响本分支开发库)。两条均修并做最后一次定向确认。
- 分歧:无。观察期:`event_board._recompute_event` 与 `event_assignment._recompute_events` 收口;story 跨页补链随 wave 2;`scan_pending` 日期预筛兼容历史时间格式。
- **R4**(最终确认):N1 关闭;N2 迁移余「旧版重试抹平 updated_at」组合 → 选行判据改为已接收 revision 优先(时间 / ID 兜底并告警),补回归测试,按检视方建议修法实施、未再开一轮(影响边界只有本分支开发库)。
- 结论:修后合入(纯后端,无目检界面);前端热点段搁置。

## 12. 检视记录(wave 2 早报去重,2026-10-23)

实现方 Claude Code(Fable 5.1);检视方 Codex(gpt-6-astra,`model_reasoning_effort=ultra`,`codex exec` 只读;任务书 / 报告 / 表态在 worktree `.review/`)。

- **R1**:2 P2 / 1 P3,无 P1。P2-1 降级回退会放回本期头条已代表的事件(回退只排了昨日事件);P2-2 说明行「N 篇里选出 M 篇,同事件合并 K 篇」的 N 是订阅 ∪ 兴趣池而 K 还计了头条池 / 价值分区池;P3 日报失败用例因预聚类后只剩一条而未进模型失败路径。两处判断(头条受昨日事件抑制;「另见」取事件层可见成员而非候选池)检视方同意。其余关注面(四类私有 / 隐藏成员过滤、游客共享版、事后隐藏的历史快照可接受、跨期抑制边界、跨通道重选状态、日报映射覆盖、快照 / API 形状、前端点击层级)逐一核过无新发现。
- **表态**:全部接受(commit `0b804b9`):回退排除集合加本期头条文章与事件;`same_event_merged` / `same_event_previous_day` 改为只计订阅 ∪ 兴趣池、全版口径另记 `*_all`;失败用例加异事件条目并断言模型层被调用一次;§5.3 两处措辞(统计口径、深度阅读边界)。
- **R2**(只对照清单,最后一轮):四条全部「已修」,未发现修复新引入的 P1;检视方在隔离环境复跑相关用例 151 例通过。结论:检视通过,交用户沙箱目检(有可见界面,须目检放行后 push / PR / 合入)。
- **用户目检(2026-10-23)**:效果满意;两处改动——「另见 N 源」改「另见 N 篇」(按篇计);跨期抑制加**升级例外**(头条池只剔除昨天以头条身份出现过的事件,见 §5.3)。追问「昨天没上报、今天后续分高会不会被抑制」:不会,抑制键只认昨天版面上出现过的事件,补了回归用例;追问「官方多形态发布」见 §5.3 末条。
- **合入(2026-10-24)**:用户目检满意后 squash 合入 main(PR #222,`7f899c2`);48h 事件窗经讨论维持现状——调研只证明了「按首篇锚定」是精度主因,36h / 72h 未做单变量实测,留生产观察期用回放脚本对比(误并率 / 每日 ≥ 2 主体事件数 / 早报跨天重复率)。
- **合入后补(精选通道代表规则,2026-10-24,单轮)**:Codex 报 1 P2——来源无配置行也无注册类时 `_source_role_and_shape` 退成 `media / article`,推文被当非社交稿当代表;修法按检视方建议:形态回退到文章 `content_type`(与榜单 `source_shape` 同口径),`social_post` 文章级直接判社交,补回归用例,三处候选池同改。其余四面一致:不加日报式 1 分分差门限(验收顺序明确;观察「低分官方代表拉低整事件排名」),兴趣命中的媒体稿被官方稿折叠后事件改走分数通道可接受,订阅外稿先过门槛再折叠、不能挤掉订阅内稿,统计与「另见」不受影响。

