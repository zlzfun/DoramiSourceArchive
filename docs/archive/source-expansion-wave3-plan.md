# 阅读源扩容 · 第三波方案(Newsletter 二批 / Meta AI 网页路线 / RSSHub 评估)

> 性质:**三轨扩源方案**(N/M/H 三条独立轨道,各自可单独批准/推迟;动工后逐项打勾)。
> 上游:[`source-expansion-plan.md`](source-expansion-plan.md)(第二增批,方法论与观察期机制沿用)。
> 日期:2026-07-17。全部候选经 curl/DOM 实测,证据记录在各轨道内。
> 状态图例:☐ 未动工 / ◐ 进行中 / ☑ 完成。

## 0. 三轨总览与建议顺序

| 轨道 | 内容 | 路线 | 工作量 | 状态(2026-07-17 修订) |
|---|---|---|---|---|
| **N · Newsletter 二批** | 5 个 RSS 源(6 候选去 1) | 路线 R(纯声明) | 半天 | **批准开工** |
| **H1 · X/微信 tier3 社交源** | X 动态(Karpathy/sama 等)/ 微信公众号 | 需第三方账号凭据 | — | **搁置(用户拍板)**:与已日落的微信方案同类依赖(账号凭据+封禁风险+过期运维);前置条件 = **管理面账号池设计**(见 H.5) |
| **H2 · RSSHub 按需移植** | 参考 RSSHub 路由实现,移植个别无凭据源为 preset(首候选:Telegram AI 频道) | 形态 A(零运行时依赖) | 每源约半天 | 保留轻量轨道,N 结束后再讨论 |
| **M · Meta AI 网页路线** | ai.meta.com/blog 浏览器发现 | 路线 W 变体(新小机制) | 1-2 天 | 维持挂起(性价比,见 M.3) |

新源一律进 `incubating` 观察期(机制已固化),不进每日采集与日报名单。

---

## 轨道 N · Newsletter 二批(5 源) —— ☑ 已完成(2026-07-17)

**前提说明**:一批 P2 试点(阮一峰/SimonW/LatentSpace)观察期刚开始,原计划「视试点体感」再做二批。既然提前启动,风险由观察期机制兜住(不达标摘除即可),但**建议至少让一批先跑 2-3 天**再给二批转正。

### 复核结果(2026-07-17 实测)与准入决定

| 源 | feed | 实测 | 决定 |
|---|---|---|---|
| ☑ Interconnects → `rss_interconnects` | `interconnects.ai/feed` | 200,**全文**,活跃(build 当日) | ✔ 准入;`feed_content_as_markdown=True` |
| ☑ Ahead of AI (Sebastian Raschka) → `rss_raschka` | `magazine.sebastianraschka.com/feed` | 200,**全文**(Substack),活跃 | ✔ 准入;全文开关同上 |
| ☑ One Useful Thing (Ethan Mollick) → `rss_oneusefulthing` | `oneusefulthing.org/feed` | 200,**全文**(Substack),活跃 | ✔ 准入;全文开关同上 |
| ☑ Lil'Log (Lilian Weng) → `rss_lilianweng` | `lilianweng.github.io/index.xml` | 200,40 条,摘要 feed(Hugo),最近 2026-07-04(低频长文) | ✔ 准入;**detail 回填路线**(实施时验证提取质量,GitHub Pages 静态站预期顺利) |
| ☑ BAIR Blog → `rss_bair_blog` | `bair.berkeley.edu/blog/feed.xml` | 200,10 条,活跃(2026-07-07);首测 60KB 截断误报 0 条,全量复核正常 | ✔ 准入(一批时已核完元数据,直接沿用) |
| Chip Huyen | `huyenchip.com/feed.xml` | 200,但**最近文章 2025-01,停更约 18 个月** | ✘ 不准入;Parking Lot 记「停更,恢复更新再议」 |

### 实施要点(全部复用二批方法论,无新机制)

- ☑ 候选记录:`candidates/personal_newsletter_sources.md` 的 Parking Lot 条目升级为 Recommended(补 v1.1 全字段+实测);BAIR 另立小节或并入(scope=research_repository,tier0)。
- ☑ 5 个 `PresetRssFetcher`:`category="incubating"`;三个 Substack 源 + Interconnects 开 `feed_content_as_markdown`;Lilian Weng 走 detail 回填(摘要 feed);元数据:四个个人源 `tier2_personal_social`(vetted list 扩容),BAIR `tier0_primary`。
- ☑ 白名单/策展表/测试断言(incubating 快照 +5)/live 验证/验证记录回写——照二批八步。
- ☑ 注意:日报源名单机制下,新源天然不进日报,无需额外处理。

---

## 轨道 M · Meta AI 网页路线

### M.1 实测结论(2026-07-17)

- `ai.meta.com/blog/`:HTTP 200 但为 **Facebook 式渲染壳**(196KB,requireLazy/ajaxpipe);壳内仅 3 个 blog href(导航)与栏目名,**文章列表由 GraphQL 客户端加载**;
- `ai.meta.com/sitemap.xml`:301 后仍返回应用壳,**无可用站点地图**;
- RSS 三条路径均 404(二批时已测)。

即:**httpx 路线全灭**,这是比 OpenAI(Cloudflare 挑战,详情级)更深的封锁——连**发现**(列表)都需要浏览器。

### M.2 可选路线

| 路线 | 做法 | 评估 |
|---|---|---|
| **A. 浏览器渲染发现(唯一可行)** | 新 fetcher:crawl4ai `render_html()` 渲染列表页 → 现有锚点启发式解析文章 URL → 详情同样走渲染 + `CrawlProfile`。需要一个小机制增量:**现有 web fetcher 的发现都是 httpx,渲染发现是新形态**(但 `render_html` 与 Segmenter 渲染兜底先例都已存在,增量可控) | 可行;抓取全程起浏览器,成本高于任何现有节点;FB 反爬(IP/指纹)风险要观察期验证 |
| B. GraphQL 逆向 | 抓包列表页 GraphQL 请求直连 | ✘ 不做:FB GraphQL 需 lsd/token 且 schema 随时变,脆弱度机器之心级 |
| C. 第三方镜像/聚合 | RSSHub 无 ai.meta.com 路由;其它镜像不可靠 | ✘ 无稳定供给 |

### M.3 性价比评估(建议用户读完再拍板)

Meta 的重大发布(Llama 系)**新闻面已被现有源覆盖**:The Decoder/TestingCatalog/HF Blog/量子位都会当日报道;缺的只是 tier0 一方原文。为「一方原文」维持一个全程浏览器、对抗 FB 反爬的节点,是本项目**成本最高的单节点**。(注:原论据之一「X 轨道落地后 `@AIatMeta` 可作替代信号」随 H1 搁置一并失效,但覆盖面结论不变。)

**决定(2026-07-17 修订)**:维持挂起;若未来要做,按路线 A 实施(预计 1-2 天:渲染发现小机制 + meta profile + 观察期验证)。

- ☐ 决策点:用户拍板 M 是否/何时实施。

---

## 轨道 H(2026-07-17 修订:拆为 H1 搁置 + H2 轻量保留)

> 修订背景:用户指出 X token 依赖与已日落的微信公众号方案**同类**(第三方账号凭据 + 封禁风险 + 过期运维),搁置 H1;并澄清 RSSHub 定位(路由适配器框架,非源列表)后,确立「按需移植」为整合形态。原试点方案(自建实例+X 路由)归档于 git 历史,此处保留结论性事实。

### H.1(搁置)X/微信 tier3 社交源 —— 前置:管理面账号池

- 实测事实存档:公共实例 `rsshub.app` 403/302 不可依赖;X 路由需 `TWITTER_AUTH_TOKEN`(小号 cookie,封禁风险,人工轮换);微信路由历史性不稳。
- **搁置理由**:与微信方案同类依赖结构,当初否决微信的理由逐条适用;
- **重启前置条件(未来立项:管理面账号池)**:凭据池表(多账号、加密存储)、按源分配与轮换、凭据健康探测(主动发现失效而非等抓取失败)、过期通知(接运维面)与人工刷新流程。届时自建 RSSHub 实例(PM2 纳管,形态 B)作为账号池的执行载体一并设计。
- 止损通路已备:X 动态可由既有 import bridge(`POST /api/import/social-posts`)人工/外部工作流喂入。

### H.2(已关闭,2026-07-17 用户拍板)RSSHub 按需移植 —— 归档

RSSHub 定位澄清:**站点适配器集合**(5000+ 路由代码,MIT),非内容源。无凭据目录逐类盘点后,对本项目的真移植标的所剩无几:Telegram AI 频道被用户降级搁置后,仅剩**即刻 AI 圈子**(中文动态,X 的最接近中文替代;但平台小众、逆向维护归本项目、稳定性未验)——**不足以支撑一条独立轨道,H2 关闭**。

- 即刻 AI 圈子 → Parking(若对中文动态流有真需求单独重启);
- 整合形态结论存档:A 按需移植(零运行时)✔ / B PM2 纳管实例(推迟到 H1 账号池立项时作为其载体——那才是 RSSHub 的真正用武之地)/ C 整库移植 ✘;
- 盘点的重要副产品:**多个候选其实有官方 RSS,根本不需要 RSSHub**(Reddit `.rss`、YouTube channel feed、Apple MLR、NVIDIA 博客、Microsoft Research、Product Hunt,2026-07-17 实测全通)→ 转化为轨道 N3。

---

## 轨道 N3 · 官方 RSS 直连三批(2026-07-17 立项,H2 盘点的转化产出) —— ☑ 已完成(2026-07-17)

**准入 3 个**(feed 预验证已完成),照旧 `incubating` 观察期、不进每日采集与日报名单:

| 源 | feed 实测 | detail 策略 |
|---|---|---|
| ☑ Apple Machine Learning Research → `rss_apple_mlr` | `machinelearning.apple.com/rss.xml`:200,10 条,摘要 feed,活跃(2026-07-16) | detail 回填(静态站,实施验证);tier0 厂商矩阵真空缺(Apple 端侧/基础研究一方原文) |
| ☑ NVIDIA GenAI Blog → `rss_nvidia_genai` | `blogs.nvidia.com/blog/category/generative-ai/feed/`:200,9 条,**content:encoded 全文**,活跃 | 全文 feed(`feed_content_as_markdown`);tier0,硬件+推理栈生态位独特;企业博客营销倾向 → medium_noise 观察 |
| ☑ r/LocalLLaMA(Reddit 日榜)→ `rss_reddit_localllama` | `reddit.com/r/LocalLLaMA/top/.rss?t=day`:200,25 条/日,Atom 自带 self-post 正文 | **top-of-day 变体即天然去噪**(HN `min_points` 的 Reddit 等价物);self-post 正文即用、外链帖降级为发现条目(HN 先例)、不抓详情;风险:Reddit 对数据中心 IP 限流(429),观察期重点验证 |

| ☑ GitHub Trending 日榜 → `github_trending_daily`(**用户点名追加**,2026-07-17) | `github.com/trending?since=daily`:SSR 200,17 行解析 | **H2 形态 A 首例**(无官方 API/RSS,参考 RSSHub 路由思路移植为 Python preset);`content_shape=bulletin` 进「动态」流(bulletin 快照护栏首次更新);**每日汇总形态**(2026-07 用户拍板,否决逐仓库条目):一天一条 GFM 表格榜单,id 按日期幂等;连续在榜者每天自然在列,不沉底不刷屏 |

Parking(留痕):Microsoft Research feed(tier0 但泛研究,重叠面待观察)/ Product Hunt(高噪)/ 即刻 AI 圈子(自 H2 转入)。

**方法论固化(三连发教训,2026-07-17)**:The Decoder、Lil'Log、Apple MLR 先后踩中同一坑——**摘要 feed 的摘要长度(300-900 字符)超过通用 detail 触发线 200,导致详情永不回填**。自本批起规则化:**凡"摘要 feed 型"preset 一律显式声明 `default_detail_min_chars = 1500`**(全文 feed 型与 discovery 型不受影响);预验证清单必须包含"摘要实际长度 vs 触发线"检查项。

---

## 执行顺序与交付定义(2026-07-17 修订)

1. **N 开工**(半天,一个 commit):5 源 + 候选记录 + 测试,入观察期;
2. **H2 待议**:N 结束后讨论 Telegram 频道名单与是否开工;
3. **H1 / M 挂起**:H1 待管理面账号池立项;M 维持挂起。

每轨 DoD 沿用二批(候选记录/元数据/测试/live 验证/观察期);全部新源不进每日采集与日报名单,转正各走各的评审。
