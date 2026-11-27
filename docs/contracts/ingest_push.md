# 收录推送接口(issue #262 波 1;推送绑定 issue #344)

一个公开的推送接口:调用方(主动式 Agent、自动化流程,或人手工调用)把发现的文章、推文、播客单集推进
本节点(通常是外网 authority)。两种调用方走同一个端点、同一套载荷与查重,出处的认法不同:

- **收录令牌**(Agent、自动化流程)只能往**它绑定的源**推;认不出出处、或出处没有绑给这个令牌的条目
  逐条拒收,不建源(issue #344,见「推送绑定」)。
- **管理员「单条收录」**由平台**认出出处**:认得的归到已有源,认不出的自动建**临时源**(源行来路
  `origin=ingest`;不进发现目录、不计入多源印证、默认不进公共日报,管理员可在运维「源管理」
  列入 / 下架 / 改名)。

「谁送来的」记在条目的 `ingest_actor`(令牌绑定的调用方名),「哪个入口」记在
`ingest_channel`(令牌推送为 `push`,管理员「单条收录」为 `manual`;旧的社交动态导入 `import` 已退役,
存量条目保留原值)。本接口是唯一的外部内容入口:知识台账的「单条收录」与外部推送走同一端点、同一套身份解析与查重。
收录的条目在知识台账里可见:来源列标「推送 · 调用方」/「手工 · 用户名」,详情有「来路」行,左栏「来路」分面可按
抓取 / 推送 / 手工筛(`GET /api/articles?ingest_channel=…`,分面 `ingest_channels`);`ingest_actor` 只对管理员透出。

## 鉴权

- 管理员在运维「自定源与收录 → 收录」签发**收录令牌**(`ding_` 前缀;明文只在签发时显示一次,
  库里只存 HMAC hash),每个令牌绑定一个调用方名 actor(写进条目来路与审计)和日额度(默认 500 条)。
- 请求头 `Authorization: Bearer ding_…`。令牌只放行推送端点,不能读任何数据。
- **管理员会话**也可直接调用(运维知识台账「单条收录」):通道记 `manual`、收录者记管理员本人、不计令牌额度,
  审计记在本人名下;读者 / 游客会话 403。请求带了令牌就只认令牌(无效即 401)。
- 采集面关闭的节点(`[runtime] role = reader`)403。其余节点不分角色(issue #263):有上游的节点照常接受收录,
  令牌、推送绑定与收录记录都只在签发它的那个节点,不随 Archive Sync 同步。
- 日额度只数推送通道(`channel=push`);管理员与令牌同名时,单条收录不占令牌额度。

## 推送绑定(令牌只能推它绑定的源)

推送绑定是源的一种获取方式,与抓取并列:抓取是平台去拿,推送是别人送来;一个源可以两种都有。绑定记在
`ingest_bindings`(源 × 令牌),只在收录入口所在的节点,不随 Archive Sync 同步。

- 管理端点(仅管理员,入审计):
  - `POST /api/admin/ingest/tokens/{id}/bindings` `{"source_id": "…"}` 绑定;`DELETE …/bindings/{source_id}` 解绑;
  - `POST /api/admin/ingest/tokens` 签发时可带 `source_ids`,有一个绑不了就不签发;
  - `GET /api/admin/ingest/tokens` 每个令牌带 `bindings`(`source_id` / `name` / `shape` / `open` / `last_pushed_at`);
  - `GET /api/fetchers`(源管理目录)每个源带 `push_bindings`(`actor` / `token_preview` / `is_active` / `last_pushed_at`)。
- 可以绑定的源:存在、不是读者自定源、不是抓取模板、不是公共日报、未退役。平台自己抓取的源也可以绑
  (又抓又推;同一条两条路都到时按「同一条社交帖再次到达」与链接合流处理)。已下架的源可以绑,
  推送仍被「已下架」拒收,恢复后生效。吊销的令牌不能再绑。
- `last_pushed_at` 是这个令牌最近一次有条目被这个源接受(新建、补全或命中重复)的时间。
- **升级注意**:升级到带推送绑定的版本后,此前签发的令牌都没有绑定,推送会被逐条拒收
  (「这个令牌还没有绑定来源」),直到管理员绑定。

### 登记一个不带抓取的源

`POST /api/admin/ingest/sources`(源管理页头「登记来源」):

| 字段 | 含义 |
|---|---|
| `kind` | `social_post` / `article` / `podcast_episode`,决定源的形态 |
| `name` | 展示名(必填) |
| 出处 | 社交 `handle`(可加 `user_id`);文章 `url` 或 `feed_url`;播客 `feed_url`(必填) |
| `source_id_suffix` | 不给出处时必填:源 id 是 `push_<后缀>`(小写字母、数字、下划线,3–40 位) |
| `avatar_url` / `homepage_url` / `description` | 呈现 |
| `listed` | 默认 `true`:观察期,进发现目录;`false`:临时源状态,待「列入」 |
| `token_id` | 可选:登记的同时绑定到这个令牌 |

- **定向源**(给了出处):id 沿用认出处的派生(`x_<账号>` / `web_<站点>` / `podcast_<主机>_<hash6>`),
  与抓取器的 id 公式一致。这个出处已经有源时回 409,`detail.source_id` 是已有的那个源——直接绑定它。
- **合集源**(不给出处):一个源里可以有很多出处的内容,例如「某 Agent 的推荐」。认法是结构性的:
  id 以 `push_` 开头且没有身份键;只有本端点能建。
- 登记的源行 `origin=ingest`、没有抓取绑定、`legacy_export=false`;此前建的临时源不用重新登记,直接绑定。

### 令牌推送怎么定出处

1. 带了 `source.source_id`:必须是这个令牌绑定的源,否则拒收「这个令牌没有绑定来源 X」。
2. 没带:按作者账号 / feed / 链接认(规则同下节),**只认这个令牌绑定的源**;认不出时,绑定里恰好有
   一个形态相符的合集源就进它;否则拒收「认不出这一条的出处;请给 source.source_id」。
3. 账号源(社交的定向源)只收这个账号的帖子:作者的 `user_id` 或账号名要与源的身份对得上
   (转推时顶层 `author` 是转推者,即这个账号),否则拒收「这条帖子的作者不是来源 X 的账号」。
   合集源不核对作者;文章源与播客源不核对链接所在的站点。
4. 目标校验同下节第 1 条(形态相符、未退役、未下架)。回执的 `source_created` 对令牌推送恒为 `false`。

## 端点

`POST /api/public/ingest/v1/items`

```json
{
  "items": [
    {
      "kind": "social_post",
      "post_id": "1900000000000000001",
      "content": "推文正文",
      "url": "https://x.com/new_lab/status/1900000000000000001",
      "publish_date": "2026-11-06T08:00:00Z",
      "lang": "en",
      "author": {"handle": "new_lab", "user_id": "9001", "name": "New Lab", "avatar_url": "https://…"},
      "media_urls": ["https://pbs.twimg.com/media/….jpg"],
      "quoted": {
        "url": "https://x.com/other/status/1899999999999999999",
        "content": "被引用帖的全文",
        "author": {"handle": "other", "name": "Other", "avatar_url": "https://…"},
        "media_urls": ["https://pbs.twimg.com/media/….jpg"]
      },
      "note": "收录理由(可选)"
    },
    {
      "kind": "article",
      "url": "https://blog.example.com/p/1",
      "title": "标题",
      "content": "正文(markdown,可选)",
      "summary": "摘要(可选)",
      "source": {"name": "Example Blog"},
      "extensions": {"author": "A", "tags": ["agents"]}
    },
    {
      "kind": "podcast_episode",
      "title": "EP1",
      "guid": "ep-1",
      "audio_url": "https://cdn.example.com/ep1.mp3",
      "summary": "简介",
      "duration_seconds": 3600,
      "source": {"feed_url": "https://pod.example.com/feed.xml", "name": "节目名"}
    }
  ]
}
```

- 每批 ≤ 100 条;`kind` ∈ `article` / `social_post` / `podcast_episode`。
- **出处描述 `source`**(三选一,社交帖不给时用 `author` 填):
  - `{"source_id": "…"}`:必须是已存在、接受推送的源;
  - `{"platform": "x", "handle": "…", "user_id": "…", "name": "…", "avatar_url": "…"}`;
  - `{"url" | "site" | "feed_url": "…", "name": "…"}`。
- 服务端决定、调用方不能写的字段:条目 id、`content_type`(按 `kind`)、来路(`ingest_channel=push`、
  `ingest_actor`=令牌 actor)。`extensions` 按 `kind` 走键白名单(文章:`author` / `site_name` /
  `summary` / `tags` / `language` / `image_url`;社交:`lang` / `tags` / `media_urls`;播客:`author` /
  `tags` / `image_url` / `episode` / `season`),其余键不入库。
- **没有被采用的内容写在该条回执的 `warnings` 里**(issue #341;见「回执」):不认识的字段、这一类条目不用的
  字段、白名单之外的扩展键、不是 http(s) 的链接。条目照常处理,不会因此被拒收。

## 社交帖的结构:配图、引用帖、被转推的原帖

读者面的推文卡片按这几个字段画;缺了就只剩一段文字,转推还会显示成转推者本人说了一句「RT @…」。
两种给法任选其一,入库的形状与平台自己抓取的同一条一致。

**结构化字段**(社交帖顶层):

| 字段 | 含义 |
|---|---|
| `media_urls` | 这条帖子自己的配图(http(s) 链接,至多 20 张;视频给封面图)。放在 `extensions.media_urls` 也认 |
| `quoted` | 被引用的帖子 |
| `reposted` | 被转推的原帖。顶层 `author` 始终是时间线账号(转推者),原作者写在 `reposted.author` |
| `lang` | 帖子语言(决定这条要不要翻译:中文与无文字内容不译,其余入库后自动译成中文) |

`quoted` / `reposted` 用与外层帖子同名的字段描述:`post_id`、`url`、`content`、`lang`、
`author`(`handle` / `name` / `avatar_url`)、`media_urls`。给了 `x.com/<账号>/status/<id>` 形式的 `url`
就不必再给 `post_id` 与 `author.handle`;外层帖子同理,只给帖子链接也能收。纯转推可以不给 `content`,
服务端按平台惯例补成「RT @原作者: 原文」。

```json
{
  "kind": "social_post",
  "url": "https://x.com/new_lab/status/1900000000000000002",
  "author": {"name": "New Lab", "avatar_url": "https://…"},
  "reposted": {
    "url": "https://x.com/other/status/1899999999999999999",
    "content": "原帖全文",
    "author": {"name": "Other", "avatar_url": "https://…"},
    "media_urls": ["https://pbs.twimg.com/media/….jpg"]
  }
}
```

**X API 原始 JSON**(手里就是 X API v2 的返回时用这个):`raw` 给一条帖子的 `data` 与它的 `includes`
(`users` / `tweets` / `media`;整页的 `includes` 原样给也行),服务端用时间线抓取器的同一个归一化函数处理。

```json
{"kind": "social_post", "raw": {"data": {"id": "1900…", "author_id": "9001", "text": "…", "…": "…"},
                               "includes": {"users": [], "tweets": [], "media": []}}}
```

- 帖子 id、作者、正文、发布时间、语言、配图、引用帖与原帖都取自 `raw`;同时给的 `content` / `lang` /
  `media_urls` / `quoted` / `reposted` 以 `raw` 为准(回执有提示)。`includes.users` 里没有作者时用顶层 `author`。
- 要拿到引用帖 / 原帖与配图,请求 X API 时带 `expansions=author_id,attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id`
  与对应的 `tweet.fields` / `user.fields` / `media.fields`。
- `raw` 只用来归一化,**不落库**(`raw_data` 留空);互动数字(`public_metrics`)不收。
- `raw` 里不是 http(s) 的配图与头像链接会被丢掉(回执有提示);作者对象的 `username` / `name` /
  `profile_image_url` 不是字符串的拒收该条。

不收的:互动数字、回复关系(结构化字段没有 `in_reply_to` / `conversation` 的写法;`raw` 里带的照常记下)。

## 身份解析(认出已有源)

管理员「单条收录」按下面四步认出处、认不出建临时源;令牌推送用同样的认法,但只认它绑定的源、不建源
(上一节)。

1. 显式 `source_id`:校验存在、非保留(读者自定源 `user_rss_*`、抓取模板 `generic_*`、公共日报、
   已下线源)、未退役、未下架,且源形态与 `kind` 一致(文章源只收 `article`、社交源只收
   `social_post`、播客源只收 `podcast_episode`;不符拒收,例如「该来源是社交源,不收文章」)。
2. 社交:身份键 `x:user_id:…` 优先、`x:handle:…`(小写)次之——命中预设(如 `Kimi_Moonshot` →
   `x_moonshot_ai`)、配置源或此前建的临时源;都不中则建 `x_<handle>`。
3. 网页:`feed_url` 规范地址 → 已有 RSS 源;条目 / 站点链接按「主机 + 路径前缀」匹配已有源
   (`anthropic.com/news/*` → `web_anthropic_news`);形态不同的源不算命中、继续往下找;x.com /
   twitter.com 的链接只收 `social_post`,按文章推直接拒收(不另建网页临时源,避免同一账号两个源);
   前缀不中时,该主机上恰好一个形态相同的已有源就认它(策展源常登记
   分类页或 feed 地址,如 `openai.com/news/rss.xml`,文章链接不在前缀下;多个候选、单页文档类源、
   多租户主机不兜底);都不中则按站点建 `web_<host>`。多租户主机
   (github.com、medium.com、huggingface.co、youtube.com、arxiv.org 等)按「主机 + 首段路径」建
   (`web_github_com_openai`)。
4. 播客:按节目 `feed_url` 认;都不中则建 `podcast_<host>_<hash6>` 临时播客源(只到元数据与简介
   初评,付费 ASR / TTS 不对临时源放开)。

## 条目 id 与去重

- 与抓取器同一公式:社交 `{source_id}_{post_id}`;文章 `{source_id}_{sha1(guid 或 url)[:16]}`;
  播客 `{前缀}_{sha1(guid 或单集链接 url)[:16]}`(前缀 = 收养过的播客源的 `entry_id_namespace`,否则
  `source_id`;**feed 里有 guid 就带上**,两样都没有的单集拒收)。
- 先抓后推:同一链接已在库里 → 见下一条,不新建。
- 先推后抓:社交帖与带 guid 的播客单集 id 公式与抓取器一致,天然合流(社交帖合流后以抓取版为准,
  见「同一条社交帖再次到达」);文章的抓取器 id 公式常不同
  (RSS 取 `entry.id`、列表页另算哈希),由存储层按链接合流——抓取入库新条目时,同一个源里已有同链接
  (原样 / 规范形 / 带尾斜杠;带锚点只认原样)的**收录条目**就按同一条处理(补正文 / 幂等跳过),不另建。
  多个条目共用页面链接的源不按链接合流:单页文档(changelog / release notes)、`docs_*` / `github_*`、
  播客单集,以及同源里已有同链接抓取条目的源。普通抓取条目之间的行为不变。
- 文章与社交帖:同一链接已在**别的源**里 → 不新建,返回 `duplicate_url` 与已有条目 id,只记一条收录记录;
  同一个源里已有同链接的条目(id 公式不同)→ 返回 `duplicate`。播客单集常共用节目页链接,不按链接去重。
  这是**认出同一条**(同一链接 = 同一页面 = 同一条目,归它的出处),不是内容去重。

## 同一条社交帖再次到达

收录时不完整的推文(缺引用帖、原帖、配图、作者名与头像)可以补全(issue #341):

| 已有的条目 | 再次到达的 | 结果 |
|---|---|---|
| 收录条目(推送 / 手工 / 旧导入) | 再次收录 | **只补不覆盖**:已有行为空、来者有值的才补——结构逐项补,已有行没有正文时补上正文(标题随之按新正文取);已有的正文、标题、配图与引用对象都不动。有补到 → `refreshed`,没有可补的 → `duplicate` |
| 收录条目 | 抓取器抓到同一条 | **以抓取版为准**:正文、标题、发布时间、链接与社交字段整体换成平台原样;来路(`push` 等)与收录记录保留 |
| 抓取入库的条目、已换成抓取版的条目 | 再次收录 | 不改,`duplicate` |

- 引用帖 / 原帖按整个对象补:已有的对象不替换,也不与来者逐项拼合。
- 补全不改到达时间(不会重新冒成新到的),修订号前进,内网随 Archive Sync 同步到;配图随即预取。
- 正文变了(给没有正文的条目补上正文、或换成抓取版的正文)时,分析评分重算,此前按旧正文生成的摘要与译文缓存
  一并去掉;只补结构时两者都不动。
- 存量的不完整推文:把完整载荷重推一遍即可。收录时把引用内容拼进了正文的,正文不会被改回去。

## 同源内容判重

同一个源里换了链接的同一篇(镜像地址、改版后的新 URL),收录时不再新建:

- 只在收录时做(推送与单条收录),抓取路径不动——同源反复发布的抓取条目照样保留。
- 只对**认到已有源**的 `article`,只和**同一个 `source_id`** 里已有的条目比;跨源一律不比
  (官方双发如 Google 博客 / DeepMind 博客、媒体转载各自保留,同一事件的归并由事件层与日报在消费端做)。
- 规范化正文(NFKC + casefold + 去标签、去标点空白)≥ 300 字才比:sha1 相同,或 64 位 SimHash(字符
  4-gram)海明距离 ≤ 3 且 5-gram Jaccard ≥ 0.8 复核。
- 不比:社交帖(`post_id` 即身份)、播客单集(guid 即身份)、新建的临时源、短正文、共用页面链接 / 模板化的源
  (单页文档、`docs_*` / `github_*`、GitHub 内容类型、模型卡 `hf_*`、trending)。
- 命中 → `duplicate_content`,返回已有条目 id 与 `source_id`,只记一条收录记录。
- 指纹存本机旁表 `article_fingerprints`(不进 Archive Sync):不在抓取入库时算,收录前对目标源现场补齐
  缺的与改过的(条目 `archive_updated_at` 变了即重算);某个源第一次被收录时补齐全源,上千条约数秒,在工作线程里跑。

## 回执

```json
{
  "status": "success",
  "actor": "research-agent",
  "counts": {"created": 2, "duplicate_url": 1},
  "results": [
    {"index": 0, "kind": "social_post", "status": "created", "id": "x_new_lab_19…", "source_id": "x_new_lab", "source_created": true},
    {"index": 1, "kind": "article", "status": "duplicate_url", "id": "rss_simonwillison_…", "source_id": "rss_simonwillison"},
    {"index": 2, "kind": "article", "status": "rejected", "reason": "文章需要链接"}
  ]
}
```

逐条的 `warnings`(字符串数组,有才出现)列出这一条里没有被采用的内容,例如:

```json
{"index": 0, "kind": "social_post", "status": "created", "id": "x_new_lab_19…", "source_id": "x_new_lab",
 "source_created": false,
 "warnings": ["未识别的字段:likes、quoted.text(已忽略)",
              "这些字段要放在条目顶层,不在 extensions 里:quoted",
              "media_urls 里有 1 项不是 http(s) 链接,已忽略"]}
```

`status`:`created` / `duplicate`(同一条已存在)/ `duplicate_url`(同一链接在别的源)/
`duplicate_content`(同一个源里已有同一内容)/ `refreshed`(已有条目被补正文、刷新元数据或补全社交帖的结构)/
`rejected`(`reason` 给原因,含「超出今日收录额度」)。

## 临时源在各环节的行为

临时源来自管理员「单条收录」遇到的未知出处,或登记时选了不进发现目录(`listed=false`)。

| 环节 | 行为 |
|---|---|
| 发现目录 / 批量订阅 | 不列;条目卡片上的来源名可进源页单独订阅,订阅者源栏归「收录」组 |
| 全站列表 / 搜索 / 详情 / 问答 | 正常可见 |
| 个人早报重大事件、榜单「必读」、事件榜发布主体 | 不计入独立来源数 |
| 公共日报 | 不进候选池;管理员「列入」(→ 观察期)后参与 |
| 分析 / 外发 AI | 照常;`kind_hint` 只来自服务端规则 |
| Archive Sync | 新版内网(`source-catalog-v1`)收到完整源行(名称 / 头像);旧版内网收不到这些行,条目照常同步,与改动前导入内容的表现相同 |

收录与管理操作都入审计(令牌推送记在 `ingest:<actor>` 名下,单条收录记在管理员本人名下)。

## 退役的旧入口

- `POST /api/articles`(知识台账旧「手工录入」):回 410,指向本接口。调用方自定条目 id、任意 `content_type`、
  任意扩展 JSON 三个后门随之取消;存量手工条目不迁移;通用录入不再提供手工日报(公共日报源是保留 id)。
- `POST /api/import/social-posts`(社交动态导入):已删除;外部社交内容按 `kind=social_post` 推送。
