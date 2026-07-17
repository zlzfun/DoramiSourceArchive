# 阅读源扩容 · 第二增批方案(D 短期 · curated 文章源) —— ☑ 已完成(2026-07-17)

> 性质:**扩源批次方案**(待拓展源清单 + 逐源方法论;批准后按此实施,动工后逐项打勾)。
> 上游依据:[`reader-usability-plan.md`](reader-usability-plan.md) 并行线 D;**完全复用并延伸既有准入体系**——
> [`docs/sources/classification_standard.md`](../sources/classification_standard.md)(分类标准 v1.1)、
> [`docs/sources/admission_workflow.md`](../sources/admission_workflow.md)(add-only 准入流程)、
> [`docs/sources/curation_policy.md`](../sources/curation_policy.md)(默认目录策展)、
> [`docs/sources/node_audit_playbook.md`](../sources/node_audit_playbook.md)(节点验证手册)、
> [`crawl4ai-migration-nodes.md`](crawl4ai-migration-nodes.md)(网页详情 B 类方法论)。
> 日期:2026-07-17。全部候选 feed 经 curl 实测(状态码/条目数/全文性/最近更新),结果记录在各源条目内。
> 状态图例:☐ 未动工 / ◐ 进行中 / ☑ 完成。

## 0. 一句话结论

现有 23 个内置源中文章形仅约 9 个,且**三块结构性空缺**:① 前沿实验室官方博客(DeepMind/Mistral/HF 缺席);② 英文 AI 垂直媒体(tier1 现全为中文);③ 高质量个人/Newsletter(tier2 全空)。本批次按既有准入标准增补 **8 个文章形 RSS 源**(P1 官方+媒体 4 个;P2 读者向增补 4 个——中文最大公约数、AI 产品动态、个人源试点,构成经 §1.1 需求侧校准),全部走最低成本的 `PresetRssFetcher` 路线——**不新增任何抓取机制**,纯声明式扩源。形态分流(迭代 2)已就位,新源自动进文章流。

## 1. 批次定位与准入口径

- 遵守 `curation_policy.md`:默认目录只增不藏,每源必须过 `ESSENTIAL_FETCHER_IDS` 白名单 + 全套 v1.1 分类元数据(`test_fetcher_curation.py` 强制)。
- 遵守 `admission_workflow.md`:先写候选记录(`docs/sources/candidates/`),后写实现;每厂商 3-5 源上限;非本批源进 Parking Lot 留痕。
- **对既有标准的一处延伸**:分类标准 §6.4 规定 tier2_personal_social「有 vetted list 前不进内置直采」。本方案将 P2 中的个人源正式确立为**首个 vetted list**(准入理由:它们是 RSS/Atom 规范分发的个人出版物,`stable_public`,与 X/Reddit 类碎片社交内容有本质区别;试点通过后再考虑二批)。

### 1.1 需求侧校准(2026-07-17 增订,回应「候选是否是读者所期待的」)

选源不只看供给侧声誉,以两个需求锚为准绳:

1. **项目内已固化的读者画像**——`daily_brief` 的兴趣档(`src/llm/prompts.py` map 打分标准):**模型/能力发布 > AI 应用/产品动态 > 大厂与行业新闻 > 新颖研究**,惩罚营销稿。这是本产品对「读者要什么」的既有权威表述,扩源应向前两档倾斜。
2. **Folo 中文用户的实际订阅结构**(社区教程/用户清单调研):中文 AI 读者的共识组合 = 国内三家第一梯队媒体(量子位/机器之心/新智元)+ 一线厂商官方博客(OpenAI/Anthropic/**Mistral** 被点名)+ arXiv 分类 + HN,个人源以**阮一峰·科技爱好者周刊**为最大公约数。

校准结论:**P1(官方博客+英文垂直媒体)与两锚吻合,维持**;初版 P2(全英文长文分析型 Newsletter)与锚偏离——它们是英文工程圈高声誉的「精读」内容,不是读者画像里的「资讯」内容——**裁剪为 2 个代表**(Simon Willison / Latent Space,全文 feed 零成本,配已上线的翻译+AI 摘要消化),腾出的名额换成两个更贴读者的源:**阮一峰周刊**(中文,读者最大公约数)与 **TestingCatalog**(正中画像第二档「AI 应用/产品动态」)。Interconnects/BAIR 转 Parking 二批。

## 2. 待拓展源清单

### P1 · 官方实验室 + 英文媒体(4 个)

#### ☑ 1. Google DeepMind Blog → `rss_deepmind_blog`

- feed:`https://deepmind.google/blog/rss.xml`(实测 200;~81 条;更新至 2026-07-16;**无全文**)
- 分类:`source_owner=google_deepmind` `source_brand=deepmind` `source_scope=company` `source_channel=blog` `tier0_primary`;tags `model_release, research_paper, product_update`;`high_signal / low_noise / stable_public`
- 准入理由:前沿实验室第一梯队,Gemini/Veo/AlphaFold 级发布的第一现场;google 家族现有 2 源(gemini models blog、gemma notes),+1 后 3 个,厂商限额内。
- detail 策略:feed 仅摘要 → `fetch_detail` 走 `article_extractor`;deepmind.google 前端较重,若提取不达标按 B 类方法论补 `CrawlProfile`(验收线:`compare_web_backends.py` 相似度 ≥0.8 或人工核对)。
- 候选记录落点:更新 `candidates/google_gemini_antigravity_sources.md`。

#### ☑ 2. Mistral AI News → `rss_mistral_news`

- feed:`https://mistral.ai/rss.xml`(实测 200;注意 `/feed.xml`、`/news/feed.xml` 均 404,以此为准)
- 分类:`source_owner=mistral` `source_brand=mistral` `source_scope=company` `source_channel=newsroom` `tier0_primary`;tags `model_release, product_update, api_platform`;`high_signal / low_noise / stable_public`
- 准入理由:欧洲前沿实验室代表,当前厂商矩阵完全空缺;开源权重发布(Mistral/Magistral 系)对读者高价值。
- detail 策略:同上(摘要 feed → article_extractor,备选 CrawlProfile)。
- 候选记录落点:新建 `candidates/mistral_sources.md`(顺带把 Mistral docs changelog 记入其 Parking Lot,动态形候补)。

#### ☑ 3. Hugging Face Blog → `rss_hf_blog`

- feed:`https://huggingface.co/blog/feed.xml`(实测 200;~196 条,**含社区文章,量大**;更新至当日)
- 分类:`source_owner=huggingface` `source_brand=huggingface` `source_scope=company` `source_channel=blog` `tier0_primary`;tags `model_release, product_update, tutorial_or_practice, research_paper`;`high_signal / medium_noise / stable_public`
- 准入理由:开源模型生态的中枢发布口;与现有 `web_huggingface_daily_papers` 互补不重复(官方+社区博客 vs 论文日榜)。
- detail 策略:摘要 feed → article_extractor(HF blog 是 SSR,预期顺利);**medium_noise 处置**:`default_limit` 取小(8~12),观察社区文章占比,必要时后续加官方作者过滤(记入候选记录的 Risks)。
- 候选记录落点:新建 `candidates/huggingface_platform_sources.md`。

#### ☑ 4. The Decoder → `rss_the_decoder`

- feed:`https://the-decoder.com/feed/`(实测 200;10 条;更新至当日;WordPress 摘要 feed)
- 分类:`source_owner=the_decoder` `source_brand=THE DECODER` `source_scope=ai_media` `source_channel=blog` `tier1_curated`;tags `market_news, model_release, product_update`;`medium_signal / medium_noise / stable_public`
- 准入理由:tier1 现有媒体全为中文(量子位/新智元/IT之家),英文 AI 垂直媒体空缺;The Decoder 是分类标准 v1.1 文档中 tier1 的钦点示例,AI 专注、无泛科技噪声。
- detail 策略:摘要 feed → article_extractor。
- 重叠审视:与 `rss_hn_ai` 中等重叠(事件面),但 HN 是 discovery 源多数无正文,The Decoder 提供成文报道,互补。
- 候选记录落点:追加 `candidates/tier1_media_community_sources.md`。

### P2 · 读者向增补 + 个人源试点(4 个;个人源即 tier2 首个 vetted list)

#### ☑ 5. 阮一峰 · 科技爱好者周刊 → `rss_ruanyifeng`

- feed:`https://www.ruanyifeng.com/blog/atom.xml`(实测 200;Atom **全文**;周更,最近 2026-07-10)
- 分类:`source_owner=ruanyifeng` `source_brand=科技爱好者周刊` `source_scope=personal_commentary` `source_channel=blog` `tier2_personal_social`;tags `market_news, developer_tool, tutorial_or_practice, opinion`;`high_signal / low_noise / stable_public`
- 准入理由:**中文技术读者的最大公约数**(Folo 中文用户清单里出现率最高的个人源);周刊形态天然是「他人已策展」的低噪聚合,近年 AI 浓度极高;中文原生,无翻译成本。
- detail 策略:全文 feed,无 detail。
- 风险:泛技术非纯 AI(接受——周刊单篇覆盖面广,读者自行略读非 AI 段落;这正是其受欢迎的形态)。

#### ☑ 6. TestingCatalog → `rss_testingcatalog`

- feed:`https://www.testingcatalog.com/rss/`(实测 200;更新至当日;摘要 feed)
- 分类:`source_owner=testingcatalog` `source_brand=TestingCatalog` `source_scope=ai_media` `source_channel=blog` `tier1_curated`;tags `product_update, model_release`;`high_signal / medium_noise / stable_public`
- 准入理由:专注追踪 **ChatGPT/Gemini/Claude 等 AI 产品的功能上线、灰度与爆料**——正中读者画像第二档「AI 应用/产品动态」,这一档现有源覆盖最弱;条目短平快、事实型,配自动翻译/摘要消化成本低。
- detail 策略:摘要 feed → article_extractor(Ghost 站,SSR,预期顺利)。
- 风险:含未证实爆料(rumor)成分 → `medium_noise`,候选记录 Risks 注明;观察期如噪声超预期则摘除白名单。

#### ☑ 7. Simon Willison's Weblog → `rss_simonwillison`

- feed:主选 `https://simonwillison.net/atom/entries/`(**仅长文**,规避 everything feed 里的 blogmark/quote 短条噪声;everything 实测 200,entries 同域同构,实施时验证);Atom 带全文。
- 分类:`source_owner=simonwillison` `source_brand=Simon Willison` `source_scope=personal_commentary` `source_channel=blog` `tier2_personal_social`;tags `developer_tool, tutorial_or_practice, opinion`;`high_signal / low_noise / stable_public`
- 准入理由:LLM 应用/工具实践领域引用度最高的个人博客之一;全文 Atom,零抓取成本。
- detail 策略:**feed 自带全文,无 detail 抓取**。

#### ☑ 8. Latent Space → `rss_latent_space`

- feed:`https://www.latent.space/feed`(实测 200;Substack `content:encoded` **全文**;更新至当日)
- 分类:`source_owner=latent_space` `source_brand=Latent Space` `source_scope=personal_commentary` `source_channel=newsletter` `tier2_personal_social`;tags `opinion, market_news, tutorial_or_practice`;`high_signal / low_noise / stable_public`
- 准入理由:AI 工程师社区头部 newsletter/播客,访谈与行业分析(播客期的 show notes 也是成文长文)。
- detail 策略:全文 feed,无 detail。

- 候选记录落点(5-8):阮一峰/Simon Willison/Latent Space 进新建 `candidates/personal_newsletter_sources.md`(含 vetted list 声明与二批候补);TestingCatalog 追加 `candidates/tier1_media_community_sources.md`。

### Parking Lot(留痕不实施)

| 源 | 原因 |
|---|---|
| Google Research Blog(feed 200 实测可用) | 与 DeepMind Blog/学术源重叠面大,medium_signal;DeepMind 跑稳后再议 |
| Ollama Blog(`/blog/rss.xml` 200) | developer_tool 低频动态;工具 lane 已有 4 个 GitHub Releases,候补 |
| LangChain Blog(feed 200) | 框架厂商,营销/教程比重高,medium_noise;观察需求再议 |
| Interconnects(feed 200,全文,Nathan Lambert 的 RLHF/开源模型深度分析) | 英文长文分析型,与读者画像的资讯偏好偏离;Newsletter **二批**候补,视 P2 试点体感 |
| BAIR Blog(feed 200,伯克利 AI 实验室) | 学术长文低频;研究面暂由 HF Daily Papers 覆盖,二批候补 |
| Sebastian Raschka / One Useful Thing / Lilian Weng / Chip Huyen(feed 均 200) | Newsletter **二批**候补,视 P2 试点体感 |
| 爱范儿 ifanr(feed 200)/ 36氪(feed 200) | 泛科技/商业 firehose,`high_noise`;AI 面已由三家中文垂直媒体覆盖 |
| arXiv cs.AI/cs.CL/cs.LG 分类 feed | 论文 firehose;论文面由 HF Daily Papers(已策展)覆盖,读者画像中研究居末档 |
| LangChain / Transformers 等框架 Releases | 动态形(bulletin),工具 lane 已有 4 个 GitHub Releases;动态流候补 |
| TechCrunch AI / VentureBeat AI | 泛科技 firehose,`high_noise`,违背 focused 目录原则 |
| InfoQ 中文 | WAF/结构风险未验证 |
| **Meta AI Blog** | **无 RSS(`/blog/rss/`、`/blog/feed/`、`/feed.xml` 均实测 404)**,页面 JS 重 → 需走网页路线(BaseWebPageListFetcher + CrawlProfile,B 类方法论),工作量数倍于 RSS 源,**单独立项** |
| **机器之心** | ❌ **两次立项两次否决(2026-06 定案,见 `candidates/tier1_media_community_sources.md`)**:Aliyun WAF 出口 IP 级封锁,任何客户端伪装无效;没有住宅/轮换代理前**不再尝试**。中文媒体面由量子位/新智元/IT之家覆盖 |

## 3. 逐源拓展方法论

### 路线 R · 标准 RSS → `PresetRssFetcher`(本批全部 8 源)

对既有流程的复用与固化,每源按以下八步执行:

1. **候选记录先行**(`admission_workflow.md`):在 `docs/sources/candidates/` 建/改记录,填 v1.1 全字段(identity 四元组 + tier + tags + signal/noise/reliability)+ Target Coverage / Inclusion Reasons / Risks / Known Overlap / Validation Notes。
2. **feed 复核**(本方案 §2 已完成第一轮):确认状态码、条目结构(`<item>`/`<entry>`)、日期字段、**全文性**(`content:encoded` / Atom `<content>`)、更新频率;记入 Validation Notes。
3. **写 Preset 类**(`src/fetchers/impl/rss_fetcher.py` 追加,与现有 preset 同格式):声明 `source_id / feed_url / name / icon / description / category`(P1 官方源 `official`、The Decoder `media`、P2 `community`——三者均不在每日任务排除集 `{advanced, workflow}` 内)+ **identity 元数据全套类属性**(`test_fetcher_curation.py` 对默认可见源强制断言这些维度)。`content_shape` 不用声明(默认 `article`,形态分流自动进文章流)。
4. **detail 策略三档**(按 §2 各源标注):
   - 全文 feed(simonwillison/latent_space/interconnects)→ 不抓详情,零额外请求;
   - 摘要 feed(deepmind/mistral/hf_blog/the_decoder)→ `fetch_detail` 走 `article_extractor`(GenericRssFetcher 现成路径);
   - 提取不达标 → 按 [`crawl4ai-migration-nodes.md`](crawl4ai-migration-nodes.md) B 类方法论补 `CrawlProfile`(`profiles.py`),`compare_web_backends.py` 相似度 ≥0.8 为验收线;crawl4ai 未装时自动回退 legacy,不阻塞。
5. **入册白名单**:`ESSENTIAL_FETCHER_IDS`(`registry.py`)追加 8 个 id;同步 `curation_policy.md` 的 Default Essential Sources 表(每源一行 Candidate Reason)。
6. **测试**:`test_fetcher_curation.py` 白名单/元数据断言随白名单同步;`test_reader_shapes.py` 动态源快照**不动**(新源全为 article 形,快照只锁 bulletin 全集,天然通过);按 `test_rss_fetcher.py` 现有模式为代表性源补桩 feed 解析用例(至少覆盖:全文 feed 一例 + 摘要 feed 一例)。
7. **节点验证**(`node_audit_playbook.md` 方法):live 直驱 `_run` 抓 8 条 → 核对标题/日期/顺序/`has_content` 比例/正文无导航残渣;再从归档侧复查(SQL 抽查)。**验过才算完成,不验不进默认目录**。
8. **接入调度**:重跑 `scripts/ensure_daily_collection_job.py`(其选源逻辑动态取注册表非 advanced/workflow 类,新源自动纳入;既有任务的 `fetcher_ids_json` 是固化快照,必须重跑或在任务编辑器手动勾选);其后观察 `/api/source-health` 两个采集周期。

### 路线 W · 无 RSS 网页源(本批不做,方法论留档)

Meta AI Blog 类:`BaseWebPageListFetcher` 声明 `listing_url + article_url_patterns` → 详情走 `CrawlProfile`;完整方法论直接沿用 `crawl4ai-migration-nodes.md`(发现不迁详情迁、cleaned_html 陷阱、验收相似度线)。**单独立项,不混入本批**。

## 3.5 执行记录(2026-07-17,全批完成)

- **分工**:Preset 类/白名单/策展表/桩测试由 codex(gpt-5.6-terra)实现;候选记录 5 处文档由 opus 4.8 撰写;live 验证、提取调优与总验收由主线完成。
- **八步全过**:候选记录 ✓(含 vetted list rationale)→ feed 复核 ✓ → 8 个 Preset ✓(全套 curation 元数据)→ detail 三档 ✓ → `ESSENTIAL_FETCHER_IDS` + `curation_policy.md` ✓ → 测试 ✓(桩 feed 全文/摘要两路语义 + 元数据断言;全套 346 passed)→ live 直驱逐源验证 ✓(结果写回各候选记录 Validation Notes)→ `ensure_daily_collection_job` 重跑 ✓(每日任务已纳入 8 源)。
- **验证中发现并修复的三个提取问题**(均为源专属声明/覆写,未动通用提取器):
  1. The Decoder feed 摘要 288-530 字符超过通用 200 触发线,详情永不回填 → `default_detail_min_chars = 1200` + 剔除主题模板残句;
  2. HF Blog 通用提取混入页头控件与 **SSR 水合标记字面文本**(`[0`/`[-1`/`]`)→ 专用 `div.blog-content` 容器提取覆写(剔 `not-prose`/空锚点/标记文本节点),正文 15-17k 字符头尾干净;
  3. 轻噪声接受观察:Mistral 个别页头面包屑/尾部招聘链接;TestingCatalog 头部徽章图;Latent Space feed 含 [AINews] 日报流(频率偏高,语义与站内日报冲突低)。

## 4. 实施顺序与交付定义

- **一个迭代、两段提交**:P1(4 源)先行——官方/媒体源验证路径更长(detail 提取质量);P2(4 源)随后——全文 feed 几乎零验证成本。合并为一个 feature commit 亦可(体量 ~每源 30-50 行声明 + 元数据 + 候选记录)。
- **每源 DoD**:候选记录 ✓ + Preset 类含全套元数据 ✓ + 白名单/策展文档同步 ✓ + 测试过 ✓ + live 验证记录写回候选记录 Validation Notes ✓ + 进入每日任务 ✓。
- **批次验收**:阅读器「发现更多来源 · 文章」新增 8 个可订阅源;订阅后未读 backlog(最近 20 篇)正常;文章流正文质量目检(重点:deepmind/hf/the_decoder 的 detail 提取);`test_fetcher_curation` / 全套回归通过。
- **回滚路径**:单源不达标 → 从白名单摘除(不删类)转 Parking Lot 记原因;结构性失败(如 WAF)→ 按机器之心先例记录否决结论,防止重复立项。

## 3.6 用户抽检修复(2026-07-17 第二轮)

用户目检四项反馈的处置:

1. **The Decoder 零文章**:排查为用户手动触发时的瞬时网络失败(feed 请求三连败),非结构性——生产路径复现 3/3 成功,重抓后已落 10 篇。留观察;若复发再考虑 feed 请求退避加固。
2. **观察期机制(长期流程,新固化)**:新增节点批次统一 `category = "incubating"`——默认可见可订可手动抓,但**不进每日自动采集**(`ensure_daily_collection_job` 排除集加 `incubating`);节点管理卡显示「观察」徽标;质量验收转正 = 改回目标分类 + 更新测试断言 + 重跑调度脚本。流程写入 `curation_policy.md`「Incubation」节;本批 8 源已全部入观察期并移出每日任务。
3. **抽检质量修复**:
   - Mistral:`<article>` 内页头 hero/`mistral-atom-*` 控件(分享 tooltip、Thinking Summary 卡)/进度条被通用提取吞入 → 专属覆写(直接子级取最长块 + 剔自定义控件),正文题图保留、头尾干净;
   - 阮一峰(及全部全文 feed 源):`GenericRssFetcher` 对 feed 内容 `get_text` 拍平导致链接逐行断裂、图片丢失 → 新增类级开关 `feed_content_as_markdown`,全文源走 `node_to_markdown` 保结构(阮一峰单期 36 图/84 行内链接完整保留);
   - 修复后清库重抓 8 源(66 篇新格式落库)。
4. **日报生成逻辑适配**:记为待办(见 §5)。

## 5. 后续(非本批)

- **日报生成逻辑适配扩源(用户提出,待办)**:源数量与形态扩张后,`daily_brief` 的候选收集/打分/配额可能需要调整——候选池被高频源(HF Blog/TestingCatalog/[AINews])稀释、周刊类聚合内容(阮一峰)与逐条资讯的打分口径不同、`paper_cap` 类配额或需按源/形态细化;集中解决源相关问题后单独立项;
- Newsletter 二批(Raschka/Mollick/Weng/Huyen)视 P2 试点体感;
- Meta AI Blog 网页路线立项(B 类);
- RSSHub 上游评估(中期,见 reader-usability-plan §并行线 D);
- 自动摘要预生成(扩源后列表摘要行的规模化价值,见 reader-usability-plan §3.4)。
