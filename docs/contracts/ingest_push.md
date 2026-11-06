# 收录推送接口(issue #262 波 1)

一个公开的推送接口:调用方(主动式 Agent、自动化流程,或人手工调用)把发现的文章、推文、播客单集推进
外网 authority。平台负责**认出出处**:认得的归到已有源,认不出的自动建**临时源**(源行来路
`origin=ingest`;不进发现目录、不计入多源印证、默认不进公共日报,管理员可在运维「自定源与收录 → 收录」
列入 / 下架 / 改名)。「谁送来的」记在条目的 `ingest_actor`(令牌绑定的调用方名),「哪个入口」记在
`ingest_channel`(本接口为 `push`,另有管理员手工录入 `manual`、社交动态导入 `import`)。

## 鉴权

- 管理员在运维「自定源与收录 → 收录」签发**收录令牌**(`ding_` 前缀;明文只在签发时显示一次,
  库里只存 HMAC hash),每个令牌绑定一个调用方名 actor(写进条目来路与审计)和日额度(默认 500 条)。
- 请求头 `Authorization: Bearer ding_…`。令牌只放行推送端点,不能读任何数据。
- 内网 replica 一律 409:只推外网 authority。

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
      "author": {"handle": "new_lab", "user_id": "9001", "name": "New Lab", "avatar_url": "https://…"},
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
  `tags` / `image_url` / `episode` / `season`),其余键丢弃。

## 身份解析(认出已有源)

1. 显式 `source_id`:校验存在、非保留(读者自定源 `user_rss_*`、抓取模板 `generic_*`、公共日报、
   已下线源)、非同步行、未退役、未下架,且源形态与 `kind` 一致(文章源只收 `article`、社交源只收
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
- 先推后抓:社交帖与带 guid 的播客单集 id 公式与抓取器一致,天然合流;文章的抓取器 id 公式常不同
  (RSS 取 `entry.id`、列表页另算哈希),由存储层按链接合流——抓取入库新条目时,同一个源里已有同链接
  (原样 / 规范形 / 带尾斜杠;带锚点只认原样)的**收录条目**就按同一条处理(补正文 / 幂等跳过),不另建。
  多个条目共用页面链接的源不按链接合流:单页文档(changelog / release notes)、`docs_*` / `github_*`、
  播客单集,以及同源里已有同链接抓取条目的源。普通抓取条目之间的行为不变。
- 文章与社交帖:同一链接已在**别的源**里 → 不新建,返回 `duplicate_url` 与已有条目 id,只记一条收录记录;
  同一个源里已有同链接的条目(id 公式不同)→ 返回 `duplicate`。播客单集常共用节目页链接,不按链接去重。

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

`status`:`created` / `duplicate`(同一条已存在)/ `duplicate_url`(同一链接在别的源)/ `refreshed`
(已有条目被补正文或刷新元数据)/ `rejected`(`reason` 给原因,含「超出今日收录额度」)。

## 临时源在各环节的行为

| 环节 | 行为 |
|---|---|
| 发现目录 / 批量订阅 | 不列;条目卡片上的来源名可进源页单独订阅,订阅者源栏归「收录」组 |
| 全站列表 / 搜索 / 详情 / 问答 | 正常可见 |
| 个人早报重大事件、榜单「必读」、事件榜发布主体 | 不计入独立来源数 |
| 公共日报 | 不进候选池;管理员「列入」(→ 观察期)后参与 |
| 分析 / 外发 AI | 照常;`kind_hint` 只来自服务端规则 |
| Archive Sync | 新版内网(`source-catalog-v1`)收到完整源行(名称 / 头像);旧版内网收不到这些行,条目照常同步,与改动前导入内容的表现相同 |

收录与管理操作都入审计(推送记在 `ingest:<actor>` 名下)。
