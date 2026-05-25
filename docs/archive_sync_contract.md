# Archive Sync Contract

Stage 3 adds the first collector-to-reader archive sync contract.

The sync format is JSON Lines (`application/x-ndjson`). It is designed to move
faithful archive records from an external-network `collector` runtime into an
intranet `reader` runtime without making the reader perform public-network fetches.

## Endpoints

Collector export:

```http
GET /api/archive/export/articles.jsonl
```

Reader import:

```http
POST /api/archive/import/articles.jsonl
Content-Type: application/x-ndjson
```

Runtime role gating:

- `collector` and `all` can export.
- `reader` and `all` can import.
- `reader` cannot access fetch-triggering collector APIs.

## Export Filters

The export endpoint supports the same article-scope filters used by downstream
delivery where possible:

| Parameter | Notes |
| --- | --- |
| `content_type` / `content_types` | Exact type or comma-separated types. |
| `source_id` / `source_ids` | Exact source or comma-separated sources. |
| `job_id` / `job_run_id` / `fetch_run_id` | Preserve and filter by collector lineage. |
| `run_scope` | `ad_hoc`, `saved_job`, or `legacy_task`. |
| `publish_date_start` / `publish_date_end` | Source publish-time window. |
| `fetched_date_start` / `fetched_date_end` | Collector ingest-time window. Use this for incremental sync cursors. |
| `search` | Title substring filter. |
| `has_content` | Optional content-bearing filter. |
| `skip` / `limit` | Offset pagination. `limit` is capped at 5000. |

## JSONL Shape

The first line is a manifest:

```json
{"kind":"manifest","schema_version":"articles-jsonl-v1","generated_at":"2026-05-25T12:00:00","content":"articles","count":1,"filters":{"fetched_date_start":"2026-05-25T00:00:00","limit":1000}}
```

Each later line is one article:

```json
{"kind":"article","schema_version":"articles-jsonl-v1","checksum":"sha256...","article":{"id":"article_id","title":"Article title","content_type":"rss_article","source_id":"rss_openai_news","source_url":"https://example.test/article","publish_date":"2026-05-25T00:00:00","fetched_date":"2026-05-25T01:00:00","fetch_run_id":1,"job_id":2,"job_run_id":3,"source_group_id":4,"run_scope":"saved_job","has_content":true,"content":"Article body","extensions":{}}}
```

The checksum is a SHA-256 hash of the canonical JSON representation of the `article`
object. Reader import rejects lines with checksum mismatches.

## Checksum Canonicalization

External producers that generate compatible article lines must calculate `checksum`
from the `article` object using these exact rules:

- Serialize JSON with keys sorted recursively by object key.
- Use compact separators: comma `,` and colon `:` with no surrounding spaces.
- Emit UTF-8 JSON directly; do not ASCII-escape non-ASCII text.
- Preserve JSON scalar types: booleans as `true`/`false`, integers as numbers,
  absent optional IDs as `null`, strings as strings.
- Use the exported article defaults: `content` is an empty string when absent, and
  `extensions` is an object, usually `{}`.

Python-compatible reference:

```python
import hashlib
import json

canonical = json.dumps(article, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

## Import Semantics

Import is idempotent by `article.id`.

- If the article ID does not exist, reader inserts the record.
- If the article ID already exists, reader skips it.
- If the existing reader record has no content and the incoming record has content,
  reader backfills the content and resets `is_vectorized` to `false`.

Reader import always sets `is_vectorized = false` for imported or backfilled records.
Vector storage is a reader-side derived index and is intentionally not the primary
sync payload.

## Preserved Fields

The sync payload preserves:

- `id`
- `title`
- `content_type`
- `source_id`
- `source_url`
- `publish_date`
- `fetched_date`
- `fetch_run_id`
- `job_id`
- `job_run_id`
- `source_group_id`
- `run_scope`
- `has_content`
- `content`
- `extensions`

These fields are sufficient for reader-side browsing, Dify delivery, MCP/RAG
retrieval, and later subscription filtering.

## Current Limits

- The first version syncs articles only.
- Collector-side collection scope definitions (`node_groups`) are not exported yet,
  so `group_id` filters on a physically separate reader require a later metadata
  sync extension.
- Source configs, fetch run records, collection job definitions, vector indexes, and
  binary media are not part of this first contract.
- Authentication remains the existing admin session; consumer tokens are a later
  reader subscription-layer task.
