# Feed Delivery API

This document records the current downstream delivery contract for LLM/agent consumers.

DoramiSourceArchive remains the source collection and archival hub. These APIs expose archived records without adding a reader-facing product surface.

Papers enriched from arXiv HTML are delivered as their preserved abstract across
JSON, Markdown, personalized Atom, MCP article/search/RAG tools, and public shares.
Full-text translation/summary caches are omitted from delivery metadata. Authenticated
reader views, administrator archive export, and authorized Archive Sync retain
the full archived reading content. See `docs/plans/issue-41-paper-fulltext.md` for scope.

Existing downstream jobs that already use `GET /api/articles` can continue doing so. The `/api/feed/*` endpoints are a cleaner recommended delivery contract for downstream consumers, not a forced migration path.

For personalized reader-layer delivery, prefer subscription endpoints documented in
`docs/contracts/reader_subscription.md`. They expose tokenized, per-subscription pulls at:

```http
GET /api/public/subscriptions/{subscription_id}/articles
Authorization: Bearer dsub_...
```

## JSON Pull Endpoint

`GET /api/feed/articles`

Returns archive records as downstream-friendly document objects:

```json
{
  "status": "success",
  "count": 1,
  "skip": 0,
  "limit": 100,
  "next_skip": null,
  "items": [
    {
      "id": "article_id",
      "title": "Article title",
      "url": "https://source.example/article",
      "content": "Article body or release notes",
      "metadata": {
        "id": "article_id",
        "title": "Article title",
        "source_url": "https://source.example/article",
        "source_id": "rss_openai_news",
        "content_type": "rss_article",
        "publish_date": "2026-05-12T00:00:00",
        "fetched_date": "2026-05-12T01:00:00",
        "has_content": true,
        "extensions": {}
      }
    }
  ]
}
```

### Query Parameters

| Parameter | Notes |
| --- | --- |
| `content_type` | Exact content type, such as `rss_article`, `web_article`, `github_release`, `social_post`, or `wechat_article`. |
| `content_types` | Comma-separated content types. |
| `source_id` | Exact source ID. |
| `source_ids` | Comma-separated source IDs. |
| `group_id` | Restrict delivery to records whose `source_id` belongs to a saved collector-side collection scope. |
| `job_id` | Restrict delivery to records whose `source_id` belongs to a saved collection job. |
| `job_run_id` | Restrict delivery to records first saved by one collection job run. |
| `fetch_run_id` | Restrict delivery to records first saved by one node-level fetch run. |
| `run_scope` | Restrict delivery by first-ingest scope: `ad_hoc`, `saved_job`, or `legacy_task`. |
| `publish_date_start` / `publish_date_end` | Source publish-time window. Date-only end values include the end of that day. |
| `fetched_date_start` / `fetched_date_end` | Archive ingestion-time window. Date-only end values include the end of that day. |
| `search` | Title substring filter. |
| `has_content` | Defaults to `true` so downstream pulls skip empty shell records. Pass `false` when deliberately inspecting empty records. |
| `include_content` | Defaults to `true`. Pass `false` for metadata-only pulls. |
| `skip` / `limit` | Offset pagination. `limit` is capped at 500. |

## Markdown Export Endpoint

`GET /api/feed/articles.md`

Returns the same filtered records as a Markdown batch. Each article is separated by `---` and starts with JSON frontmatter-like metadata.

This endpoint is capped at 200 records per request to keep exported documents reasonably sized.

## Known Consumers(已知下游消费方,改字段前先对照)

- **内网「神灯瞭望台」日报流水线**(运维者个人电脑上的独立脚本,每日 08:55):经 `dfeed_` 令牌拉公共日报记录,**只消费 `metadata.extensions.items`**(日报 map 段产物:`title_cn` / `classification` / `source` / `realm` / `source_url` / `summary` / `comment` 等由 `ScoredItem.to_reduce_dict` 产出的键),不读 `content` 正文——所以日报 reduce / 渲染故障对它无感,而 `items` 字段改名或删除会直接打断它;`classification` 取值对它是冻结契约。日报故障排查须分段定位(items 与 content 可各自独立好坏)。
- **IM 机器人 / Agent**:经 MCP 与 `dfeed_` 聚合 feed 消费,只依赖本契约的记录形状。
- 译文缓存(`translation_zh` / `translation_zh_social`)随 `metadata.extensions` 外传,与归档同步同口径。

## Current Status

- Uses the existing `articles` table only; no sync-state table has been added yet.
- `extensions_json` is parsed into `metadata.extensions` for structured downstream use.

## Follow-Up Work

- Add sync status per article or per downstream consumer.
- Add idempotent sync acknowledgement APIs if a consumer should report consumed article IDs.
- Exact `job_run_id` delivery only covers records first saved during that run. Duplicate records skipped by later runs retain their original lineage.
- Add standard time-window helpers such as `last_hours` or `since_cursor` if downstream jobs prefer cursor-style polling over timestamp filters.
- Add authentication or shared-token protection before exposing these endpoints outside a trusted local/private network.
