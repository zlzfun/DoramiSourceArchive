# Dify Delivery API

This document records the current downstream delivery contract for Dify/RAG/agent consumers.

DoramiSourceArchive remains the source collection and archival hub. These APIs expose archived records without adding a reader-facing product surface.

## JSON Pull Endpoint

`GET /api/dify/articles`

Returns archive records as Dify-friendly document objects:

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
        "is_vectorized": false,
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
| `publish_date_start` / `publish_date_end` | Source publish-time window. Date-only end values include the end of that day. |
| `fetched_date_start` / `fetched_date_end` | Archive ingestion-time window. Date-only end values include the end of that day. |
| `search` | Title substring filter. |
| `has_content` | Defaults to `true` so downstream pulls skip empty shell records. Pass `false` when deliberately inspecting empty records. |
| `include_content` | Defaults to `true`. Pass `false` for metadata-only pulls. |
| `skip` / `limit` | Offset pagination. `limit` is capped at 500. |

## Markdown Export Endpoint

`GET /api/dify/articles.md`

Returns the same filtered records as a Markdown batch. Each article is separated by `---` and starts with JSON frontmatter-like metadata.

This endpoint is capped at 200 records per request to keep exported documents reasonably sized.

## Current Status

- Implemented and verified on 2026-05-12 with FastAPI `TestClient`.
- Uses the existing `articles` table only; no sync-state table has been added yet.
- `extensions_json` is parsed into `metadata.extensions` for structured downstream use.

## Follow-Up Work

- Add Dify sync status per article or per downstream consumer.
- Add idempotent sync acknowledgement APIs if Dify should report consumed article IDs.
- Add standard time-window helpers such as `last_hours` or `since_cursor` if Dify jobs prefer cursor-style polling over timestamp filters.
- Add authentication or shared-token protection before exposing these endpoints outside a trusted local/private network.
