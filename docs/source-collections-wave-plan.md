# 源合集波：发现页策展合集(设计方案)

> 状态:已实现(v3.38.0,2026-08-25;三项拍板经用户委托按推荐落定,样页环节随委托豁免、
> 目检在真实 UI 上做)。实施记录见 §6。
> 缘起:源扩容波次制下,每一波本身就是一个策展包(如 HN 博客 2025 七源),但这层
> 「为什么这批源值得一起看」的叙事只存在于 git 历史,读者看不到。合集把它显式化,
> 并提供一键批量订阅。先例:Folo Lists / OPML bundle。

## 0. 定调(复杂度阀门,全案唯一前提)

**合集是目录呈现层的策展视图 + 批量动作,不是新的订阅实体。**

- 订阅粒度仍是 `source_id`;feed/MCP/未读/隐藏/内容交付链路**全部不感知合集**。
- 「订阅合集」= 一次性批量订阅其**当前**成员(复刻 `ensure_default_subscriptions`
  的循环范式:建 `ReaderSubscriptionRecord` + `init_cursor_with_backlog`,单事务一次 commit)。
- 合集后续新增成员**不会**自动推给已订阅者(无绑定记录)。「跟随合集」的持久绑定语义
  留作观察后的二期——真到那步才需要 membership 表,并要重算订阅域,本波明确不做。
- 推论(诚实呈现即可,不是缺陷):某源同属多个合集时,退订合集 A 会把该源退掉
  (无法区分「经由哪个合集订的」);退订确认框如实列出将退订的源。

## 1. 策展存储:代码注册表(拍板项 A)

`src/services/source_collections.py`,字面量注册表——与 preset fetcher「代码即记录」
同路线(成员本来就是要改代码+部署才能加的 preset 源,合集与源同一 commit,git 即审计):

```python
@dataclass(frozen=True)
class SourceCollection:
    collection_id: str       # kebab 稳定标识,如 "hn-popular-blogs-2025"
    name: str                # 「2025 人气独立博客」
    description: str         # 内容式一句话(取名规范沿 node_audit_playbook)
    provenance_note: str     # 策展来源注,可含 URL(如 Karpathy 分享的 Evan Schwartz gist)
    source_ids: tuple[str, ...]  # 成员,有序;允许同一源出现在多个合集

SOURCE_COLLECTIONS: tuple[SourceCollection, ...] = (...)
```

- 演进路径(记录不实现):若将来需要运营时热调整,换 KV JSON + 管理面 CRUD,
  照 `source_visibility.py` 五元素范式(KEY 常量/宽容读/幂等写/sorted 归一/返回名单)。
- 校验测试:注册表内 `collection_id` 唯一、`source_ids` 非空且无重复;成员 id
  存在于 registry ∪ SOURCE_FRIENDLY_NAMES(防改名/退役后名单腐烂)。

**首发合集**:`hn-popular-blogs-2025`(rss_sean_goedecke / rss_giles_thomas /
rss_max_woolf / rss_geohot / rss_geoffrey_litt / rss_martin_alderson / rss_anil_dash),
provenance_note 注明:Karpathy 分享了 Evan Schwartz 创建的 2025 最受欢迎 RSS 列表
(https://gist.github.com/emschwartz/e6d2bf860ccc367fe37ff953ba6de66b),基于该列表拓展。
可选第二枚(让 seg 切换不显得空):「前沿实验室官方」之类既有源重组,拍板时定。

## 2. API(reader 前缀天然门控,零中间件改动)

- `GET /api/reader/collections` → `{"collections": [{collection_id, name, description,
  provenance_note, source_ids}]}`。**轻载荷**:subscribed 态/成员卡数据由前端与已持有的
  `GET /api/reader/sources` 目录 join,不重复下发。
- `POST /api/reader/collections/{id}/subscribe` → 批量订阅成员。逐源沿用两条既有纪律:
  隐藏源与目录中不存在的成员**跳过**(不整体 404),幂等基线用
  `resolve_subscribed_source_ids(include_hidden=True)`;整批单事务一次 commit;
  返回 `{status, collection_id, added: [...], skipped: [...], subscribed_source_ids}`。
- `DELETE /api/reader/collections/{id}/subscribe` → 批量退订当前成员(复用单源退订逻辑
  逐一执行:剔 filters、清水位)。未知 collection_id → 404。
- 命名纪律:`collection` 在本项目已被「采集任务 collection-jobs」占用——后端模块名
  一律 `source_collections`,router 归入 `routers/reader.py`(路径域 `/api/reader/*`
  与 `/api/collection-jobs` 不混淆),UI 中文词「合集」。

## 3. 前端

### 3.1 发现页(桌面)

- 头部工具条左端加二段 seg **「源 ⇄ 合集」**(现有控件顺序变为:视图 seg → 搜索 →
  形态 seg → 排序;后两者仅「源」视图显示,搜索在合集视图过滤合集名/描述/成员名)。
- **合集卡**:名称 + 描述 + provenance note(faint 小字,URL 白名单渲染为链接)+
  成员头像堆叠(前 5 个 avatar/LogoMark 叠排)+ meta「n 源 · 已订阅 m」+ 订阅按钮三态:
  未订「订阅全部 (n)」/ 部分「订阅其余 k 个」/ 全订「已全部订阅」(hover 转退订)。
- 点卡进入**合集详情子视图**(面包屑返回合集列表):顶部合集头(名称/note/批量按钮),
  成员复用现有源卡格渲染(单源仍可独立订阅/预览)。
- 隐藏源:前端与 `discoverSources`(已滤 hidden)join,隐藏成员自然从计数与展开中消失,
  零新逻辑。join 不到目录的成员 id 不渲染(与后端 skipped 口径一致)。
- 数据/回调下沉 `useReaderState`:`collections` 加载(与 sources 并行拉取)、
  `handleSubscribeCollection`/`handleUnsubscribeCollection`(沿 handleSubscribe 范式:
  pinning → api → 用响应 `subscribed_source_ids` 覆写 Set → 刷未读 → toast)。
- 退订合集走确认框,列出将退订的源名(见 §0 推论)。

### 3.2 移动端

发现页已是 m-page 全屏层,内部同构:顶部同款二段 seg,合集详情在层内就地推进
(返回键经 useLayerHistory 已接管的 discover 层,详情态作为层内子态,返回先退详情)。

### 3.3 样页

实现前出 `docs/design/dorami-collections-quiet.html`(合集列表 + 合集详情 + 按钮三态
+ 退订确认框),目检拍板后动代码——工作节奏照旧。

## 4. 测试

- `tests/test_source_collections.py`:注册表校验(id 唯一/成员存在);GET 形状与门控;
  批量订阅端到端(新订+已订跳过+隐藏跳过+未知成员跳过、水位建立、单 commit 幂等重放);
  批量退订(清水位、他源不受累);未知合集 404;未登录/角色门控。

## 5. 分工与节奏

单波可完成(版本 MINOR bump):

1. 样页 → 拍板(含首发合集名单与是否配第二枚);
2. 后端:注册表 + 三端点 + 测试;
3. 前端:DiscoverPage seg/合集卡/详情 + useReaderState 批量回调 + 移动端 + CSS(token 化);
4. 收尾:版本 bump + tag + deploy,L1 索引登记本文档。

## 6. 实施记录(2026-08-25,v3.38.0)

与方案的差异注记(其余按方案原样落地):

- **样页豁免**:三项拍板经用户委托按推荐落定,`dorami-collections-quiet.html` 不再单出,
  目检直接在实现后的真实 UI 上做(§3.3 的样页环节仅此波豁免,不改工作节奏惯例)。
- **第二枚合集拍板**:`frontier-labs-official`(前沿实验室官方,8 源:Anthropic 新闻/
  OpenAI News/DeepMind 博客/Meta AI 博客/Mistral 新闻/Qwen 博客/Kimi Research/
  MiniMax Research)——现役官方源重组,让「合集」seg 切过去不显得空。
- **存量源组合扩容(用户敲定 ABC 三枚,2026-08-25)**:构思纪律=合集须回答
  「为什么一起看」且不复刻角色/形态两根既有轴,最有价值的是跨形态跨角色主题切面。
  `cn-open-models` 国产开源模型动态(13 源:Qwen/DeepSeek/智谱/Kimi/MiniMax/字节 Seed
  六家的博客·HF 权重·仓库·X 官号跨三形态,原案「中国大模型力量」经用户改名)、
  `ai-coding-tools` AI 编程工具动态(6 源:agent 编程工具官方 Changelog/Releases)、
  `ai-deep-writing` AI 深度写作(7 源:领域深度作者,与 HN 人气博客合集成对)。
  被毙候选记录:X 官方账号(≈社交容器全集,复刻形态轴零增量)、榜单与评测(复刻角色组);
  暂缓候选:中文 AI 资讯(增量偏薄)、研究前沿(与既有两枚交叉过多)、哆啦美精选入门
  (日报是注册表外特殊源,需给护栏测试开豁免口)。合集视图现共 5 枚。
- **provenance_note 渲染**:复用公告白名单渲染器 `utils/announcementText.jsx`
  (仅 **加粗** 与 [文字](http(s) 链接),零依赖零注入面),注册表 note 按该子集书写。
- **批量端点响应**比方案更细:`added` / `already_subscribed` / `unavailable` 三份名单
  (方案原文 added+skipped 两份),前端 toast 按 added 数措辞。
- **退订确认**:实现为按钮两击确认(首点入「确认退订 n 个源?」危险色态,4s 自动回落,
  title 列出成员名)——与轨底退出防呆同语汇,不引入模态确认框。
- **批量后的前端校准**:不做逐源 subscriber_count 乐观调整(涉及多源),直接重拉
  `GET /api/reader/sources` 校准订阅态与计数。
- **SourceCard 提取**:发现页单源卡自分组网格 JSX 提取为组件,源视图与合集详情共用
  (合集详情固定 showArticleChip——成员可能三形态混排)。
- 测试:`tests/test_source_collections.py` 六用例(注册表护栏/目录形状与门控/订阅端到端
  含幂等重放与水位/隐藏成员跳过/未知合集 404/退订不殃及合集外订阅)。

## 7. 观察期出口(记录,不承诺)

- 合集数量多了以后:合集视图内分组(按主题/年份)。
- 「跟随合集」持久绑定(见 §0)——触发条件:用户反馈「合集加了新源我没收到」成为真实诉求。
- KV + 管理面 CRUD(见 §1)——触发条件:出现非代码路径的策展者。
