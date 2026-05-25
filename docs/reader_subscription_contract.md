# Reader Subscription Contract

Stage 4 introduces the first reader-side subscription layer.

A subscription is a named content consumption scope over already imported archive
records. It is not a collector fetch scope and it never triggers public-network
fetching. Subscriptions expose a tokenized Dify-compatible pull endpoint for
downstream applications.

## Admin APIs

Admin-managed subscription APIs require the existing admin session and are enabled
only for `reader` and `all` runtime roles.

```http
GET    /api/subscriptions
GET    /api/subscriptions/{subscription_id}
POST   /api/subscriptions
PUT    /api/subscriptions/{subscription_id}
POST   /api/subscriptions/{subscription_id}/rotate-token
DELETE /api/subscriptions/{subscription_id}
```

Create payload:

```json
{
  "name": "OpenAI product updates",
  "description": "Reader-side source for downstream Dify workflows.",
  "filters": {
    "source_id": "rss_openai_news",
    "content_type": "rss_article",
    "has_content": true
  },
  "delivery_policy": {
    "include_content": true,
    "default_limit": 100,
    "max_limit": 500
  },
  "is_active": true
}
```

The create and rotate-token responses include the plaintext `token` exactly once.
Later reads expose only `token_preview`.

## Consumer Endpoint

Downstream consumers use the subscription token, not the admin cookie:

```http
GET /api/public/subscriptions/{subscription_id}/dify/articles
Authorization: Bearer dsub_...
```

For tools that cannot set headers, the endpoint also accepts `?token=dsub_...`.

Query parameters:

| Parameter | Notes |
| --- | --- |
| `skip` | Offset pagination. |
| `limit` | Optional request limit, capped by the subscription `max_limit`. |

Response shape matches the existing Dify article delivery shape:

```json
{
  "status": "success",
  "subscription": {"id": 1, "name": "OpenAI product updates"},
  "count": 1,
  "skip": 0,
  "limit": 100,
  "next_skip": null,
  "items": []
}
```

## Filters

Supported subscription filters:

- `content_type`
- `content_types`
- `source_id`
- `source_ids`
- `job_id`
- `job_run_id`
- `fetch_run_id`
- `run_scope`
- `publish_date_start`
- `publish_date_end`
- `fetched_date_start`
- `fetched_date_end`
- `search`
- `has_content`

## Token Model

Subscription tokens are independent of admin sessions.

- Tokens are generated with the `dsub_` prefix.
- Only a salted HMAC-SHA256 hash is stored.
- Token rotation invalidates the previous token.
- Inactive subscriptions return no consumer data.

## Current Limits

- This stage provides backend APIs and the tokenized Dify delivery surface only.
- Per-subscription MCP tools and reader UI management are future work.
- Subscription filtering depends on archive records already present in the reader DB;
  it does not sync missing collector metadata by itself.
