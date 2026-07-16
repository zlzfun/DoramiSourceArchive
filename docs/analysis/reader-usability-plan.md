# 用户面可用性提升路线(B→A→C 主线)

> 性质:**迭代计划 + 执行记录**(分迭代,含逐项状态;动工后逐项打勾)。
> 依据:[`folo-vs-dorami-reader.md`](folo-vs-dorami-reader.md) 的 Folo 对照分析。
> 主线:**迭代 1(B 未读体系)→ 迭代 2(A 形态分流)→ 迭代 3(C AI 摘要+日报置顶)**;方向 D(源扩容)为并行线;E/F 为后续体验波。
> 日期:2026-07-16。状态图例:☐ 未动工 / ◐ 进行中 / ☑ 完成。

## 0. 总纲

三个迭代各自独立可交付(每个迭代结束用户面都有完整可感知的提升),但顺序有依赖理由:B 先补「阅读循环」的心脏(未读/新内容),A 在其上做视图分流(动态视图同样需要未读),C 兑现 AI 差异化(摘要卡片在分流后的文章视图中才有干净的落点)。

**全程不变式**(每个迭代都要遵守):

- 每个模型变更配套 Alembic migration(`tests/test_migrations.py` 的 drift 测试强制,见 CLAUDE.md「Database migrations」)。
- 阅读器可见文案不得泄漏内部架构词(归档/采集/分发/层),遵守 `docs/frontend/conventions.md`。
- 新增读者面端点走 reader 前缀 gating(`READER_API_PREFIXES`);计量类写入绝不阻断阅读主流程(异常吞掉,与 `record_read` 同约定)。
- 前端复用 `frontend/src/index.css` 的 token 与角色类,不手写 px/hex。

---

## 迭代 1 · B:未读体系(阅读循环的心脏) —— ☑ 已完成(2026-07-16)

**目标**:用户打开阅读器能立刻回答「哪些是新的、上次看到哪」——左栏源级未读计数、列表未读标记、打开即标读、全部标读、只看未读;顺带获得「有 N 篇新文章」的新内容感知。

### 1.1 数据模型(`src/models/db.py` + Alembic)

- ☑ 新表 `ReaderArticleReadStateRecord`(`reader_article_read_states`):复合主键 `owner_username` × `article_id`(同 `ReaderFavoriteRecord` 惯例),`read_at`。与 `ReaderReadRecord`(admin 观测聚合)职责分离:那张管「读了多少」,这张管「哪篇读过」。
- ☑ 新表 `ReaderReadCursorRecord`(`reader_read_cursors`):复合主键 `owner_username` × `source_id`,`mark_read_before`(ISO 时间水位)。独立小表(未复用 `ReaderSubscriptionRecord` 加列,SQL 直查更干净)。
- ☑ 未读判定 = `fetched_date > mark_read_before` 且不在 read_states 中(基准 fetched_date,补抓不弹未读)。
- ☑ Alembic 迁移 `b2e62d70289c`(临时库 autogenerate;按仓库惯例加了「表已存在即跳过」幂等守卫,同 jobs 表迁移);drift 测试通过。

### 1.2 后端(新服务 `src/services/reader_state.py` + `routers/reader.py` + `routers/articles.py`)

- ☑ `POST /api/reader/articles/{id}/read` 双写:同一请求先挂逐篇已读(不自 commit)再累计量(一并提交);异常吞掉不阻断阅读。
- ☑ `GET /api/reader/unread-counts`:`{by_source, total}`,单条 group-by SQL + not-in 已读子查询;**兼职水位校准入口**——对缺水位的存量订阅懒初始化为当下(升级后首访未读从 0 起算,免去迁移回填)。
- ☑ `POST /api/reader/sources/{source_id}/mark-all-read` + `POST /api/reader/mark-all-read`:推进水位并清理被覆盖的 read_states 行;**只清 `fetched_date <= 新水位` 的行**(水位之后到达且已读的文章,清了会复活为未读——测试抓出后修正);响应即返回更新后统计,前端免二次拉取。
- ☑ `GET /api/articles` 增加 `unread_only=true`(按订阅源水位 or_ 条件 + not-in 已读;无可判定源显式空集)与 `with_unread=true`(页级未读标记,只读现有水位不写库)——后者是计划外补充,让列表条目能渲染未读圆点。
- ☑ 订阅初始化水位 = 订阅时刻(`reset_cursor`);**退订清水位行**(再订阅重新起算,退订期间的文章不算未读)。

### 1.3 前端(`ReaderTab.jsx` + `api.js` + `index.css`)

- ☑ 左栏:源行未读徽标(品牌蓝淡底芯片,99+ 封顶,0 隐藏);「我的订阅」行总未读徽标。
- ☑ 中栏:文章卡左缘未读圆点(`article.unread` ∧ 本会话未点开;读过即消,不改标题字重——静默仪器单一标记);列表头「只看未读」(CircleDot)与「全部标读」(CheckCheck)动作对,收藏视图下隐藏;只看未读空态文案「没有未读文章,都读完啦」。
- ☑ 打开文章乐观标读:圆点即消 + 对应源计数-1(`readIdsLocal` ref+state 双持,`selectArticle` 依赖零增)。
- ☑ 新内容感知:60s 轻轮询(`document.hidden` 时跳过);「有 N 篇新文章 · 点击刷新」sticky 提示条——N 为相邻两次轮询同视图未读数的**正增量累计**(打开文章的减量不触发;切视图/刷新列表归零),不自动插入打断阅读。
- ☑ 暗色适配(`[data-theme="dark"]` 徽标底色覆盖);ESLint + 生产构建通过。

### 1.4 测试与验收

- ☑ `tests/test_reader_unread.py` 10 例全过:双写、订阅水位/懒初始化(经 `/api/subscriptions` 高级路径造无水位订阅)、打开清未读、再订阅重新起算、**双账号未读隔离**、单源/全局全部标读(含 read_states 清理边界)、`unread_only`/`with_unread`、无订阅空集、未登录门禁。
- ☑ drift 测试通过;全套回归 334 passed——5 个失败经 stash 基线比对确认为 main 预存在的测试顺序脆弱(`test_mcp` × 4 + `test_route_authz_audit`,「Event loop is closed」类,任何前置 TestClient 生命周期均可触发,与本迭代无关)。
- ☑ 验收:用户目检发现 3 问题,修复见 1.5。

### 1.5 目检修复(2026-07-16,用户人工测试反馈)

- ☑ **「只看未读」空空如也**:根因是水位初始化为「当下」——升级存量/新订阅首访未读一律 0,与用户直觉(没读过 = 未读)相悖。改为 **backlog 语义(Folo 式)**:订阅初始化与存量懒初始化统一取「该源第 K+1 新文章的 fetched_date」为水位(`INIT_UNREAD_BACKLOG=20`),最近 K 篇成为未读积压,不足 K+1 篇则空水位=全部未读。再订阅同样重新起算 backlog。顺带修出一个真 bug:`reset_cursor` 的 `watermark or now` 把空字符串水位(=全部未读,合法值)误当缺省回落到当下——已改为显式 `is not None` 判别。
- ☑ **列表头按钮随篇数横跳**:`.reader-list-count` 定宽(`min-width: 52px`)+ 右对齐,数字位数变化不再挤压左侧动作按钮。
- ☑ **无法手动标读/未读(误触不可撤销)**:`reader_article_read_states` 加 `is_read` 列——行=逐篇显式覆盖(True 已读/False 未读),无行=水位裁决;`is_read=False` 即使被水位盖过也算未读(撤销覆盖)。新端点 `POST /api/reader/articles/{id}/mark-read|mark-unread`(**不计阅读计量**,404 于文章不存在,区别于静默的 `/read`);阅读窗格头部新增「标为未读/标为已读」切换(乐观更新+失败回滚,联动计数与圆点,并校正轮询基线防误报新文章)。前端本地态由 `readIdsLocal`(Set)升级为 `readOverrides`(Map id→bool)。迁移 `b2e62d70289c` 就地扩充:建表含 `is_read`,守卫升级为「表缺则建、列缺则补」(覆盖目检期间 create_all 已建出旧形状表的开发库;`server_default=1` 使存量行回填为显式已读),新增迁移测试覆盖该收养路径。测试 16 例(含手动标读三例、backlog 三例)+ 全套 334 passed。

---

## 迭代 2 · A:内容形态分流(「动态」与「文章」分离) —— ☑ 已完成(2026-07-16)

**目标**:changelog/Release/仓库/模型监控类内容从文章流剥离,进独立「动态」视图(紧凑条目、只扫不读);文章流回归纯阅读内容。现有 23 个源全部各得其所。

### 2.1 形态标记(后端)

- ☑ `BaseFetcher.content_shape: str = "article"`(`article` | `bulletin`),注册表 `get_all_metadata()` 透出为 `shape` 字段。
- ☑ 置 `bulletin` 共 15 个 source_id:GitHub Releases 基类+预设 ×4、仓库/模型监控基类+预设 ×4、changelog/发布说明预设 ×7(逐类显式标记,**不动 `SinglePageDocumentFetcher` 基类**——Seed Research 与 HF Daily Papers 同基类但拍板保持文章形;HN AI 亦保持文章形)。
- ☑ 形态透出:`api/sources.py` 新增 `source_shape()`(注册表优先)+ `BULLETIN_CONTENT_TYPES`(github_release/github_repository/hf_model/huggingface_model 兜底)+ `bulletin_registry_source_ids()`;`GET /api/reader/sources` 每源带 `shape`(含未注册历史源兜底、主 content_type 重解析时同步刷新;日报=article)。
- ☑ `GET /api/articles?shape=` 过滤:`source_id IN 注册表动态源 ∪ content_type IN 兜底集`,article=取反;非法值忽略;与 `subscribed_scope`/`unread_only` 可组合。`SourceConfigRecord` 自定义源默认 article(config 字段留待后续)。

### 2.2 阅读器视图(前端)

- ☑ 视图轴:中栏列表头 `.mini-seg` **文章 / 动态** 分段(仅「我的订阅」聚合流显示,默认文章);单源视图不设轴——源形态同质,由其 `shape` 直接决定卡片密度。搜索/收藏/未读在两视图均生效。
- ☑ 动态条目卡 `.reader-bulletin-card`:单行标题+脚注(源·相对时间),无摘要,密度约为文章卡两倍;点击仍进右栏阅读(changelog 正文有价值);未读圆点/收藏星照常。
- ☑ 左栏分组:订阅区拆「文章来源 / 动态来源」两个 band(空组不渲染);「发现更多来源」内按形态分「文章 / 动态」子分组(`.reader-subgroup-label`)。
- ☑ 未读拆分:「我的订阅」行主徽标只算**文章形**未读(`unreadByShape`);动态未读弱化为「动态来源」组头小计;新内容提示条的视图口径同步按形态轴计算(文章/动态两个流独立检测增量)。`unreadTotal` 状态被 `unreadByShape` 取代移除。

### 2.3 测试与验收

- ☑ `tests/test_reader_shapes.py` 5 例:内置节点形态**快照断言**(锁死 15 个动态源全集,新节点必须显式归类)、`source_shape` 注册优先/兜底、sources payload 透出、`shape=` 过滤(含非法值忽略)、与 `subscribed_scope=only` 组合。全套回归 339 passed(5 失败仍为 main 预存在顺序脆弱)。
- ◐ 验收:浏览器端实机目检(分段切换、左栏分组、动态卡密度、未读拆分)待用户过一遍。

---

## 迭代 3 · C:AI 摘要前置 + 日报置顶(兑现 AI 差异化) —— ☑ 已完成(2026-07-16)

**目标**:打开文章即见(或一键即得)中文 AI 总结卡片;列表卡摘要不再是无信息量的正文截断;每日日报成为「我的订阅」顶部的一等公民入口。

### 3.1 摘要服务(`src/services/reader_ai.py` + `src/llm/prompts.py`)

- ☑ `summarize_article()`:镜像 `translate_article()` 全部约定——同 `resolve_llm_config()`,缓存 `extensions_json.summary_zh`(幂等、不碰向量状态);输入正文截断 12000 字省 token;prompt(`SUMMARIZE_SYSTEM_PROMPT`)2~3 句/160 字内、硬信息优先、纯文本无引导语。
- ☑ 端点 `POST /api/reader/ai/summarize`(入参形状同 translate),`_require_reader_ai()` 同门禁;usage 计量 purpose=`summarize` 归属登录读者;轻量计数列不扩(`record_ai_usage` 对未知 kind 只刷新 `ai_last_used_at`,精确统计走 `AiUsageRecord`)。
- ☑ `serialize_article_list_item` 恒带轻字段 `summary_zh`(解析自 extensions_json;列表与详情两路都有)。

### 3.2 阅读器呈现(前端)

- ☑ 右栏正文顶部「AI 总结」卡(`.reader-ai-summary`):缓存即显;无缓存给低调「生成本文要点总结」入口(MVP 不自动生成控成本);soft 底+品牌蓝小节标,静默克制无渐变;生成后同步刷新列表条目摘要行。会话级 Map 缓存 + 服务端缓存双层。
- ☑ 中栏文章卡摘要行:`summary_zh` 优先于 `content_preview` 截断,无摘要维持现状(动态视图无摘要行,不受影响)。
- ☑ `aiEnabled` 关闭时总结卡整体隐藏(与翻译/问答同通路)。

### 3.3 日报置顶(前端为主)

- ☑ 「我的订阅·文章」流顶部日报卡(`.reader-brief-card`):挂载时独立拉 `source_id=dorami_daily_brief` 最新一篇(不依赖订阅关系),Sparkles+「AI 资讯日报」标签+相对时间+标题一行;点击进右栏阅读;无日报整卡隐藏;**搜索/只看未读过滤时让位**;动态流/单源/收藏视图不显示。
- ☑ 日报卡本身不参与未读计数(若用户订阅了日报源,流内条目未读照常,两者不冲突)。

### 3.4 后续可选(本迭代不做,记录备选)

- ☐ 订阅源新文章**自动**预生成摘要(admin 总闸 + 成本核算后再立项;可挂 `run_fetcher_with_tracking` 钩子,同 `auto_vectorize` 模式)。
- ☐ 摘要进入 feed/MCP 交付面(`/api/feed/articles` 的 extensions 已会带出缓存字段,确认即可)。

### 3.5 测试与验收

- ☑ `tests/test_reader_ai.py` +4 例:summarize 缓存幂等(二次不调 LLM、extensions_json 落盘、向量状态不动、列表轻字段透出)、门禁 403、空正文 400、usage 归属(purpose=summarize×读者名);日报卡数据通路(source_id 取最新一期读者可达)。全套回归 343 passed(5 失败仍为 main 预存在顺序脆弱)。
- ◐ 验收:浏览器实机目检(总结卡观感、列表摘要行、日报卡)待用户过一遍——需已配置 LLM 且账号开启 AI Beta。

---

## 并行线 D · 源扩容(纯内容/配置工作,不阻塞主线)

- ☐ 短期:扩 curated 文章型预设源一批(候选:Google DeepMind Blog、Meta AI Blog、Mistral News、机器之心、Simon Willison、Latent Space 等——以「有 RSS/结构稳定/文章型」为准入,逐个核实后加 `PresetRssFetcher`)。
- ☐ 中期:评估 RSSHub 作为 `generic_rss` 上游(自建实例 or 公共实例;一举覆盖微信公号/微博等无 RSS 源)——**单独立项**,涉及部署与稳定性治理。
- ☐ 远期:`source_builder` 以「admin 审核入库」受控启用(前端 gate 已留好 `ENABLE_CUSTOM_NODE_BUILDER`)。

## 后续波 E/F(主线收官后立项)

- E 体验细节:键盘导航(j/k/m/s/v)、无正文条目「抓取全文」按钮(按需走 `article_extractor`/crawl4ai 回填)、预计阅读时长、列表日期分割线、移动端单栏响应式。
- F 语义搜索:阅读器搜索框加「语义」模式切换(`/api/vector/search` 对 user 已硬 scope 到订阅,RAG 开启时显示)。
