# Hacker News Popular Blogs 2025：源审查与 Scour 评估

> 状态：`under_review`（documentation-only）
> 审查日期：2026-08-24（Asia/Singapore）
> 原始名单：[Evan Schwartz 的 Gist](https://gist.github.com/emschwartz/e6d2bf860ccc367fe37ff953ba6de66b)
> 名单出处：[HN Popularity Contest](https://popularity.refactoringenglish.com/)

本档案只记录候选筛选和实现前证据，不直接新增 fetcher、SourceConfig 或数据库记录。最终准入仍遵循
[`admission_workflow.md`](../admission_workflow.md)、[`curation_policy.md`](../curation_policy.md) 和
[`node_audit_playbook.md`](../node_audit_playbook.md)：先记录候选，再做真实抓取质量校对，最后以
`incubating` 观察期进入默认目录。

## 1. 先给结论

这份 OPML 是“HN 上受欢迎的个人博客/Newsletter”名单，不是 AI 源名单。它对 Dorami 的价值主要在
长尾的 AI 工程、模型实践和行业评论；若把 92 个源整体引入，会显著稀释项目现有的 AI 资讯信号，
并增加大量重复、低频、静态文章库和正文补抓成本。

建议采用以下口径：

- **已有覆盖**：`simonwillison.net` 已由 `rss_simonwillison` 覆盖，不重复建源。
- **推荐复审**：先看 12 个 AI/开发者/行业分析候选。第二轮项目抓取复审后，7 个已实现为 `incubating` preset，5 个保留为专项/暂缓候选；这两种结论都不等于已经加入默认每日任务。
- **停车区**：68 个源 HTTP/解析基本可用，但主题过宽、AI 边缘相关、内容过低频或需要单独产品形态，不进入当前默认目录。
- **暂不引入**：11 个源存在当前 feed 无效、限流、无日期/无正文、静态归档或长期停更问题。

### 1.1 一次性可用性探测

对 Gist 的 92 个 `xmlUrl` 做了跟随重定向的只读 HTTP 探测，并用 XML 解析确认 RSS/Atom 结构；
这是 2026-08-24 的单次快照，不等价于源的长期 SLA。

| 结果 | 数量 | 说明 |
| --- | ---: | --- |
| HTTP 200 | 88 | 其中大多数能解析出 RSS/Atom；HTTP 200 仍可能是空 feed 或静态/错误页面 |
| HTTP 404 | 1 | `joanwestenberg.com/rss` |
| HTTP 429 | 1 | `rachelbythebay.com/w/atom.xml`，应使用退避后复测 |
| 请求失败 | 2 | `chiark.greenend.org.uk/~sgtatham`、`tedunangst.com` |
| 能识别为 RSS/Atom 根节点 | 87 | 仍需额外检查日期、正文、粒度和主题相关性 |

几个有代表性的结构问题：

- `paulgraham.com` 的 feed 虽然返回 219 条，但文章没有可用发布时间，更像静态历史索引。
- `dfarq.homeip.net` 返回 HTTP 200，但本次响应没有 RSS/Atom 条目。
- `chadnauseam.com` 能解析出条目，但本次条目没有可用日期和正文，无法作为可靠的时间线源。
- `tedunangst.com` 不仅本次请求失败，Gist 评论中也已有“is down”的反馈；不能直接按健康源接入。

## 2. 推荐复审源

这些源满足“主题至少有明确 AI/开发者增量、最近仍有更新、RSS/Atom 可达”的第一轮条件。初审时统一
标为 `under_review`；第二轮结果见 2.2，7 个通过者现已进入 `implemented_incubating` 观察期，不代表直接进入默认每日任务。

| 编号 | 源 | Feed | 本次观测 | 建议角色与风险 |
| ---: | --- | --- | --- | --- |
| 3 | Sean Goedecke | [`seangoedecke.com/rss.xml`](https://www.seangoedecke.com/rss.xml) | 200；30 条；最近 2026-08-22；粗测平均正文约 13.5k 字符 | AI/软件工程评论，全文质量好；个人观点占比高，适合第二层分析源，低到中风险 |
| 21 | Gary Marcus | [`garymarcus.substack.com/feed`](https://garymarcus.substack.com/feed) | 200；20 条；最近 2026-08-23；平均约 65 字符 | AI 批评、产业与社会影响；feed 很短，必须详情补抓，并核对付费/摘要边界 |
| 26 | Giles Thomas | [`gilesthomas.com/feed/rss.xml`](https://gilesthomas.com/feed/rss.xml) | 200；10 条；最近 2026-08-20；平均约 40.6k 字符 | “从零实现 LLM”等高价值工程长文；全文体积大，需硬上限、结构保真和分篇去重 |
| 41 | Dwarkesh Podcast | [`www.dwarkeshpatel.com/feed`](https://www.dwarkeshpatel.com/feed) | 200；20 条；最近 2026-08-11；平均约 93 字符 | 前沿 AI 访谈发现信号；本质偏播客，当前项目没有音频/逐字稿管线，建议仅 discovery，不直接当全文源 |
| 43 | Where’s Your Ed At | [`wheresyoured.at/rss/`](https://www.wheresyoured.at/rss/) | 200；15 条；最近 2026-08-18；平均约 517 字符 | AI 商业、资本和产业分析；有付费内容/观点性风险，适合低权重观察源 |
| 45 | Max Woolf | [`minimaxir.com/index.xml`](https://minimaxir.com/index.xml) | 200；10 条；最近 2026-07-23；平均约 58 字符 | LLM 应用实验、模型行为和 ML 实践；需详情补抓，适合 AI 实践补充 |
| 46 | geohot | [`geohot.github.io/blog/feed.xml`](https://geohot.github.io/blog/feed.xml) | 200；10 条；最近 2026-07-12；平均约 3.5k 字符 | AI 系统/工程与反炒作评论；观点强、个人色彩强，但题材增量明显 |
| 52 | Geoffrey Litt | [`geoffreylitt.com/feed.xml`](https://www.geoffreylitt.com/feed.xml) | 200；11 条；最近 2026-07-02；平均约 365 字符 | AI × HCI/软件工具；低频但有独特视角，需详情校对 |
| 62 | Bert Hubert | [`berthub.eu/articles/index.xml`](https://berthub.eu/articles/index.xml) | 200；369 条；最近 2026-07-31；平均约 486 字符 | AI、基础设施与公共政策；中英混合且主题较宽，适合行业/技术背景源，不宜高频默认采集 |
| 69 | Mat Duggan | [`matduggan.com/rss/`](https://matduggan.com/rss/) | 200；15 条；最近 2026-08-21；平均约 314 字符 | AI、DevOps、云和工程管理；相关性中等，适合作为应用/工程观察源 |
| 80 | Martin Alderson | [`martinalderson.com/feed.xml`](https://martinalderson.com/feed.xml) | 200；62 条；最近 2026-08-23；平均约 150 字符 | 开放权重、AI 成本和工程实践；短摘要，需要详情补抓，题材与当前项目很贴合 |
| 85 | Anil Dash | [`anildash.com/feed.xml`](https://anildash.com/feed.xml) | 200；12 条；最近 2026-08-21；平均约 10.7k 字符 | AI 治理、技术社会影响和安全；全文质量高，但主题远宽于 AI，建议低权重/观察 |

### 2.1 初审时建议的第一批人工校对顺序（已执行）

如果下一步要进入统一实现，建议先审：

1. `gilesthomas.com`：验证超长全文是否能稳定保留标题、代码和段落。
2. `seangoedecke.com`：验证 AI/软件文章占比和观点重复度。
3. `geohot.github.io`：验证强观点文章是否符合日报和读者问答的使用方式。
4. `martinalderson.com`：验证短摘要详情补抓、日期和正文容器。
5. `garymarcus.substack.com`：验证 Substack 详情、付费边界和重复推广文案。
6. `minimaxir.com`：验证短 feed 到详情页的补抓成功率。

这 6 个源的顺序已在第二轮实测中执行。下面的最终结论会覆盖本节的初始排序；所有通过者仍需以
`incubating` 观察若干轮，不直接进入每日自动任务。

### 2.2 第二轮项目抓取复审（2026-08-24）

复审使用项目现有 `GenericRssFetcher`，每个源取最新 3 条，开启默认的短正文详情补抓，并检查
正文开头、中段、结尾、日期排序、链接和明显订阅/赞助模板。12/12 个源均能沿项目路径返回条目，
且最新条目在前；数字是字符数，按抓取顺序记录。

| 源 | 二审实测 | 正文与风险 | 最终结论 |
| --- | --- | --- | --- |
| Sean Goedecke | 3/3；10.6k / 4.4k / 4.2k；2026-08-22 至 08-19 | 全文连续，代码/链接和段落边界正常；中段未见明显订阅模板 | **通过**：进入 `incubating`，AI/软件工程分析，低频观察 |
| Gary Marcus | 3/3；2.5k / 1.8k / 2.2k；前 2 条详情补抓成功 | 详情能补齐正文，但保留 Substack 图片、Subscribe/Sign in 等文案；最新一条是美加政治而非 AI | **暂缓**：需做模板清理和主题过滤，不能按纯 AI 源直接接入 |
| Giles Thomas | 3/3；8.7k / 18.7k / 10.8k；2026-08-20 至 07-31 | LLM 实现、训练实验和 AI 使用实践；中段代码/段落可读，正文较长但在合理范围 | **通过**：进入 `incubating`，全文工程源 |
| Dwarkesh Podcast | 3/3；143.6k / 7.9k / 492 | 本质是播客/访谈；首条是超长逐字稿，另有视频赞助文案，条目体积会冲击日报与详情链路 | **专项暂缓**：只做 podcast/transcript discovery，暂不当普通文章源 |
| Where’s Your Ed At | 3/3；52.3k / 3.1k / 47.8k | 产业分析增量明显，但长文常带 Premium/Subscribe 促销语，正文体积和付费边界需治理 | **暂缓**：先验证付费边界、清洗和超长文章策略 |
| Max Woolf | 3/3；9.2k / 5.4k / 10.3k；最近 2026-07-23 | LLM 实验、模型行为和 Agent/API 观察，全文连续；更新较低频但仍有增量 | **通过**：进入 `incubating`，AI 实践补充源 |
| geohot | 3/3；3.1k / 5.3k / 3.3k；最近 2026-07-12 | 全文可读，系统/AI 工程和反炒作观点鲜明；个人立场强，适合低权重 | **通过**：进入 `incubating`，观点型工程源 |
| Geoffrey Litt | 3/3；11.1k / 5.0k / 5.6k；最近 2026-07-02 | AI × HCI/编程工具长文，首中尾结构正常；更新低频，不能期待日更信号 | **通过**：进入 `incubating`，低频专题源 |
| Bert Hubert | 3/3；467 / 472 / 435 | 均为短摘要，且出现英文/荷兰文平行内容；通用详情补抓未被触发，无法形成稳定全文归档 | **暂缓**：需要更高详情阈值、语言策略和重复判断后再评估 |
| Mat Duggan | 3/3；20.6k / 18.7k / 705 | 正文质量好，但最新样本含生活随笔、OTel 工程文和 Blogroll，主题混杂且有明显非 AI 噪声 | **暂缓**：若接入需低权重并增加主题过滤，不进当前首批 |
| Martin Alderson | 3/3；6.3k / 7.7k / 5.6k；2026-08-23 至 08-10 | 开放权重、成本和缓存实践，全文连续；项目通用抓取不需详情补抓 | **通过**：进入 `incubating`，与项目主轴最贴合 |
| Anil Dash | 3/3；8.1k / 4.4k / 2.8k；2026-08-21 至 07-24 | 正文完整、首中尾干净；AI 治理与社会影响和软件项目并存，主题明显宽于 AI | **通过**：进入 `incubating`，低权重/低频社会技术源 |

**二审汇总**：7 个通过并已实现为 `implemented_incubating`（Sean、Giles、Max、geohot、Geoffrey、Martin、Anil），5 个专项或暂缓
（Gary、Dwarkesh、Where’s Your Ed At、Bert、Mat）。通过表示“值得实现并观察”；它们现在已写入默认
可见白名单，但仍不进入每日自动任务；其中 Geoffrey、Max、Anil 仍应按低频策略运行。

## 3. 全量处置登记

编号对应 Gist OPML 中的顺序；精确 feed URL 以原始 [OPML](https://gist.github.com/emschwartz/e6d2bf860ccc367fe37ff953ba6de66b) 为准。

### 3.1 已有覆盖

| 编号 | 源 | 处置 |
| ---: | --- | --- |
| 1 | `simonwillison.net` | **已有覆盖**：项目已有 `rss_simonwillison`，且项目选择了同站的 `/atom/entries/` 全文 feed；不重复注册。 |

### 3.2 暂不引入：可达但当前项目匹配度不足，进入 Parking Lot

这些源并非“坏源”；它们大多是 HN 喜欢的优质个人技术博客，但与 Dorami 当前的“模型/AI
应用/Agent/开发者平台资讯”主轴不够窄，或需要独立的安全、硬件、播客、文化内容产品定位。

| 主题 | 源（编号） | 暂缓理由 |
| --- | --- | --- |
| 硬件、系统、编程和安全 | `jeffgeerling.com` (2)、`krebsonsecurity.com` (4)、`daringfireball.net` (5)、`ericmigi.com` (6)、`antirez.com` (7)、`idiallo.com` (8)、`maurycyz.com` (9)、`shkspr.mobi` (11)、`lcamtuf.substack.com` (12)、`mitchellh.com` (13)、`utcc.utoronto.ca/~cks` (15)、`xeiaso.net` (16)、`devblogs.microsoft.com/oldnewthing` (17)、`righto.com` (18)、`lucumr.pocoo.org` (19)、`overreacted.io` (23)、`timsh.org` (24)、`johndcook.com` (25)、`matklad.github.io` (27)、`evanhahn.com` (29)、`terriblesoftware.org` (30) | 内容质量普遍可用，但不是 AI 时间线；未来若增加“安全/系统/编程”专题容器，可再从此处挑选。 |
| 泛科技、社会和文化评论 | `pluralistic.net` (10)、`dynomight.net` (14)、`skyfall.dev` (20)、`derekthompson.org` (28)、`rakhim.exotext.com` (31)、`filfre.net` (48)、`blog.jim-nielsen.com` (49)、`jyn.dev` (51)、`simone.org` (64)、`hey.paris` (67)、`steveblank.com` (73)、`herman.bearblog.dev` (77)、`tomrenner.com` (78)、`experimental-history.com` (84)、`aresluna.org` (86) | 有时会出现 AI/技术文章，但主题混合且日报相关性不稳定；不应因单篇热门文章直接入默认源。 |
| 开发者/工程补充 | `xania.org` (33)、`micahflee.com` (34)、`nesbitt.io` (35)、`construction-physics.com` (36)、`susam.net` (38)、`entropicthoughts.com` (39)、`buttondown.com/hillelwayne` (40)、`borretti.me` (42)、`jayd.ml` (44)、`eli.thegreenplace.net` (55)、`hugotunius.se` (60)、`it-notes.dragas.net` (65)、`beej.us` (66)、`bernsteinbear.com` (74)、`danieldelaney.net` (75)、`troyhunt.com` (76)、`michael.stapelberg.ch` (87)、`miguelgrinberg.com` (88)、`keygen.sh` (89)、`mjg59.dreamwidth.org` (90)、`computer.rip` (91)、`danielchasehooper.com` (81) | 适合工程师个人阅读，但对 AI 资讯的边际增量有限；其中少数 AI 文章可作为人工发现线索，不值得现阶段单独注册。 |
| 安全/研究/硬件长尾 | `brutecat.com` (54)、`downtowndougbrown.com` (53)、`abortretry.fail` (56)、`fabiensanglard.net` (57)、`oldvcr.blogspot.com` (58)、`grantslatton.com` (83) | 主题偏安全、逆向、硬件、游戏或一般工程，和当前 AI 主轴不匹配。 |
| 低频或需独立形态 | `feed.tedium.co` (37)、`blog.pixelmelt.dev` (79)、`worksonmymachine.substack.com` (71) | 文化/实验性项目/产品评论等内容不适合直接混入 AI 资讯流；`Dwarkesh` 已单列为播客 discovery 候选。 |
| 软件产品/实践评论 | `refactoringenglish.com` (70)、`philiplaine.com` (72)、`danielwirtz.com` (68) | 文章有工程方法价值，但更新和 AI 相关性不足；其中长期停更项转入问题清单。 |

> 注：上表中的 `johndcook.com` (25) 只出现一次；`danielwirtz.com` (68) 和
> `philiplaine.com` (72) 的停更问题同时在下一节列出。由于该节是 Parking Lot，重复提及是为了
> 保留“主题处置”和“可用性处置”两条维度，不代表建议实现。

### 3.3 暂不引入：当前 feed 的可用性或时间线结构不合格

| 编号 | 源 | 本次观测 | 结论 |
| ---: | --- | --- | --- |
| 22 | `rachelbythebay.com` | HTTP 429 | 限流不是永久失效；先不接入，后续需退避、低频复测，确认是否允许无人值守采集。 |
| 32 | `joanwestenberg.com` | HTTP 404 | Gist 给出的 RSS 地址当前失效；需要重新发现官方 feed 或新站点后再评估。 |
| 47 | `paulgraham.com` | HTTP 200；219 条；无可用日期 | 静态历史 essay 索引，不满足 Dorami 的“按发布时间增量归档”要求；拒绝作为默认时间线源。 |
| 50 | `dfarq.homeip.net` | HTTP 200；无法解析 RSS/Atom，0 条 | 入口返回但没有可用 feed；拒绝，除非发现新的真实 feed 地址。 |
| 59 | `bogdanthegeek.github.io` | HTTP 200；最近条目 2025-10-19 | 长期没有新内容；保留记录但不进入当前观察批次。 |
| 61 | `gwern.net` | HTTP 200；Substack feed 最近 2021-06-11 | 当前地址更像历史 Newsletter 归档，不是活跃增量源；不引入。 |
| 63 | `chadnauseam.com` | HTTP 200；43 条但无日期/正文 | 不能形成可信的时间线和可检索正文；结构性不合格。 |
| 68 | `danielwirtz.com` | HTTP 200；最近 2021-12-09 | 长期停更；不引入。 |
| 72 | `philiplaine.com` | HTTP 200；最近 2025-04-21 | 长期低活跃；不进入本轮。 |
| 82 | `chiark.greenend.org.uk/~sgtatham` | 本次请求失败 | 可能是临时网络/服务器问题，需独立复测；在复测前不纳入。 |
| 92 | `tedunangst.com` | 本次请求失败；Gist 评论也报告 down | 明确列入淘汰候选；除非站点恢复且连续观测稳定，否则不重试接入。 |

## 4. 推荐实现形态（仅供下一阶段）

二审通过的 7 个源已复用 `PresetRssFetcher`/`GenericRssFetcher`，没有新建采集机制；它们以
`incubating` 注册，完成快照 fixture、2–3 轮真实抓取和正文质量回归后再考虑转正：

- **已实现的全文 feed**：`seangoedecke.com`、`gilesthomas.com`、`minimaxir.com`、`geohot.github.io`、
  `geoffreylitt.com`、`martinalderson.com`、`anildash.com`。优先启用 `feed_content_as_markdown`，
  保留代码、列表、链接和图片结构；Giles 需特别做长文 fixture。
- **暂缓的摘要/付费/混合源**：`garymarcus.substack.com`、`wheresyoured.at`、`berthub.eu`、
  `matduggan.com`。分别需要 Substack 模板清理、付费边界、语言/详情阈值或主题过滤，暂不放进首批。
- **播客/音频 discovery**：`www.dwarkeshpatel.com` 只保存标题、链接、嘉宾/摘要等发现元数据；除非项目
  明确增加 transcript/音频内容形态，否则不应伪装成普通文章源。首条 143.6k 字符也说明需要独立的
  转录体积和赞助文案策略。

本批实现还修复了共享 Markdown 压缩层：`compact_text()` 现在保留 fenced code block 和嵌套列表的
行首空格，避免 Python/配置代码进入阅读器后缩进塌陷；该修复由 Giles Thomas 的真实代码样本触发，
并已用独立提取测试回归，影响所有走 RSS Markdown 转换的来源。

所有新源都应先以 `incubating` 类别观察，不直接加入每日自动采集；每个源至少检查 2–3 篇真实
文章的开头、中段、结尾、发布日期、排序、重复和详情补抓结果。

## 5. Scour 评估

### 5.1 它是什么

[Scour](https://scour.ing/) 是 Evan Schwartz 维护的托管式个性化内容发现服务。公开文档将它拆成两层：

- **Interests**：用户用自然语言描述兴趣；系统用语义匹配和词法匹配判断文章是否真正相关，并支持 broad/normal/specific 精度档位。
- **Feeds**：订阅源决定内容来自哪里；默认可以在约数万社区贡献源中搜索，也可以只搜已订阅源。

它的排序理念不是“谁热谁上”：文档明确强调显式兴趣、内容质量、来源多样性和跨源去重；语义匹配
使用 embedding，词法匹配用于消歧，且通过来源惩罚避免单一站点淹没阅读流。详见
[Interests](https://scour.ing/docs/interests)、[Feeds](https://scour.ing/docs/feeds) 和
[How Ranking Works](https://scour.ing/docs/ranking)。

产品还提供：

- 每个用户、每个兴趣、Likes、Popular/Discussed 和 Changelog 的 RSS/Atom/JSON Feed；
- OPML 导入/导出，并能从 OPML 订阅反推兴趣建议；
- 反应、已读/历史、域名屏蔽、付费墙过滤和每周邮件摘要；
- “Why didn’t I see this?” 的结果解释方向，以及对同一故事的覆盖关系。

这些能力的产品证据见 [Export & Integration](https://scour.ing/docs/export)、
[Content Filtering](https://scour.ing/docs/filtering) 和 [Changelog](https://scour.ing/changelog)。

### 5.2 是否适合直接引入 Dorami

结论：**不建议把 Scour 作为 Dorami 的默认上游或核心依赖；建议把它作为源发现参考和算法产品
对照样本，必要时允许用户把自己的 Scour feed 作为外部 RSS 源导入。**

| 方向 | 判断 | 原因 |
| --- | --- | --- |
| 把 Scour 的全量内容同步进 Dorami | 不适合 | 它输出的是按用户兴趣排序后的结果，不是权威源；会丢失原始来源治理、时间线完整性和可重复性，还会把外部聚合的去重结果再次归档。 |
| 把某个用户的 Scour RSS 当成可选源 | 可以，但仅限高级/私有配置 | Scour 明确支持带用户名的 RSS/Atom/JSON Feed；项目现有 `generic_rss` 已能承载这种外部 feed。应显示为“Scour 个性化 feed”，不能伪装成原始站点。 |
| 将 Scour 作为默认推荐源 | 不适合 | feed 是账号绑定的个性化结果，依赖第三方服务可用性，且服务当前把兴趣、喜欢和个人 feed 设计为公开可见。 |
| 借鉴它的发现/排序思路 | 很适合 | 与 Dorami 的“精选源 + 读者订阅 + FTS5/LLM 检索”路线互补，尤其适合解决长尾发现和来源多样性。 |
| 引入它的 embedding 服务/向量架构 | 当前不适合 | Dorami 已因生产硬件、零向量化和维护成本退役向量 RAG，当前权威检索路径是“LLM 计划检索 + SQLite FTS5”；不应为模仿 Scour 再引入外部向量依赖。 |

还应注意：Scour 的公开文档显示它是由 Evan 全职维护的免费独立产品；embedding 由 Mixedbread 赞助，
未来 premium/高级排序仍在规划。[Pricing](https://scour.ing/docs/pricing) 说明了这一点。它的
数据文档也明确写出 feed、likes 和 interests 默认公开，点击/历史等部分数据才是私有的；这与
Dorami 的私有部署和管理员控制模型不同，见 [Your Data & Account](https://scour.ing/docs/privacy)。

### 5.3 对 Dorami 有直接借鉴意义的部分

1. **显式兴趣优先，行为只调节不改主题**：用户明确说“我关心什么”，点击/反应只调整来源权重和主题配比。可用于 Dorami 的读者推荐，避免一次点击把订阅域带偏。
2. **语义 + 词法双通道**：语义召回负责发现同义表达，词法校验负责处理 `Rust`、`AI` 之类歧义。Dorami 当前的 LLM 查询规划 + FTS5 已有天然落点，可优先补充“来源/主题标签”和解释信息，而不是引入向量服务。
3. **来源多样性与跨源故事去重**：Scour 通过来源惩罚和 coverage 合并避免一篇事件刷屏。Dorami 的日报已有 same-event dedup，可把同样的 coverage 关系下沉到阅读器发现页。
4. **“为什么没看到”解释**：将“未命中是因为未订阅、被过滤、日期窗不符、还是相关性不足”做成检索/日报诊断信息，能显著提升系统可解释性。
5. **按兴趣导出 feed**：未来可考虑为 Dorami 的订阅域、日报主题或保存的检索条件生成只读 RSS/MCP 视图；这是建立在现有 feed/token 交付能力上的小增量，不需要依赖 Scour。

### 5.4 Scour 的建议落地顺序

- **现在**：不加外部依赖；把 Scour 记录为“源发现和个性化排序参考”。
- **下一波源扩展**：用本档案的推荐复审源做小批量质量验证，观察 Scour 与 Dorami 日报的重复率和漏报差异，结果只作为策展参考。
- **若用户确实需要**：以 `SourceConfigRecord(source_type=rss)` 手动接入个人 Scour RSS，默认关闭自动采集，清晰标注“个性化聚合源”，并保留原始条目链接和 feed 账号范围。
- **以后再做产品能力**：优先实现本地的 source discovery、coverage/dedup 和检索解释；不要把 Scour 的托管排名当作本项目的不可替换后端。

## 6. 下一步

1. 对已实现的 7 个源完成 2–3 轮观察抓取，重点记录正文尾部、代码/列表结构、日期排序和重复率，不直接转入每日任务。
2. 为 5 个暂缓源分别记录专项问题：Substack/付费边界、超长转录、语言与详情补抓、主题过滤。
3. 单独记录 Scour feed 的“外部个性化源”契约，不把它混入原始来源目录。
