# Reader Subscription Contract

Stage 4 introduces the first reader-side subscription layer.

A subscription is a named content consumption scope over already imported archive
records. It is not a collector fetch scope and it never triggers public-network
fetching. Subscriptions expose a tokenized pull endpoint for
downstream applications.

## Admin APIs

Admin-managed subscription APIs require the existing admin session and are enabled
only for `reader` and `all` runtime roles.

This `/api/subscriptions` lifecycle (create/edit a custom multi-source subscription,
rotate its `dsub_` token) is an advanced/automation path with **no dedicated UI** — the
standalone `订阅分发` tab was removed when the user layer became the 阅读器. Day-to-day
users add/remove sources from the 阅读器 sidebar (see *Source Catalog and One-Click
Subscribe*) and manage the aggregated `dfeed_` token in 接入集成. Plaintext tokens are
still shown only on create or rotate.

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
  "description": "Reader-side source for downstream workflows.",
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

## Ownership

Subscriptions are owned by the login account that created them (`owner_username`).
A `user` account only lists and manages its own subscriptions; reading, editing,
rotating, or deleting another user's subscription returns `404`. Public consumer
delivery is authorized by the subscription token alone and is independent of owner
scoping, so an issued token keeps working regardless of who calls it.

## Source Catalog and One-Click Subscribe

The reader console builds subscriptions from a source catalog rather than free text:

```http
GET /api/reader/sources
```

The catalog is the union of three sets: every **registered fetcher source**, every
`source_id` that already has **archived articles**, and every `source_id` the user has
already **subscribed** to. Including registered sources with a zero article `count` means
a brand-new source can be subscribed *before* it has produced anything, so the user
receives its future output. **Decommissioned fetcher nodes** (listed in
`DECOMMISSIONED_FETCHER_IDS` — classes removed from `impl/` during node audits but whose
historical archive lingers) are excluded from the archived-articles set, so a removed
node does not silently reappear in the reader catalog after node management has been
slimmed; the only exception is a user who is *already* subscribed to one, who still sees
it so they can unsubscribe. Legitimate unregistered import sources (e.g. `social_post`)
are *not* on that list and remain subscribable. Each entry carries a friendly `name` and `description`/`icon`
(enriched from the fetcher registry, falling back to `SOURCE_FRIENDLY_NAMES` then the raw
id), primary `content_type`, a grouping `category`, article `count`, a `registered` flag,
and a `subscribed` flag for the current user. The 阅读器 left sidebar renders these as the
subscription manager: subscribed sources pinned at the top (star to unsubscribe) and the
rest under a collapsible "发现更多来源" group with a one-click subscribe `+`. Selecting a
subscribed source reads its articles inline in the reader; the 我的订阅 entry aggregates
all of them.

One-click subscribe/unsubscribe hides all delivery complexity — the toggle just adds or
removes a source from the user's subscriptions:

```http
POST   /api/reader/sources/{source_id}/subscribe
DELETE /api/reader/sources/{source_id}/subscribe
```

- **Subscribe** is idempotent: if the source is not yet in any of the user's
  subscriptions, it creates a single-source subscription named after the source with a
  default delivery policy and a fresh token (the plaintext token is *not* surfaced here —
  reveal one later via rotate). If already subscribed, it is a no-op.
- **Unsubscribe** removes the `source_id` from every one of the user's subscriptions and
  deletes any subscription left with no sources.
- Both return `{subscribed, source_id, subscribed_source_ids}` so the catalog can
  reconcile every tile's `subscribed` flag from the authoritative union.

The user-facing surface is deliberately minimal: the 阅读器 sidebar handles subscribe /
unsubscribe (one source per click), and 接入集成's 个人聚合接口 block copies the pull URL
and rotates/reveals the `dfeed_` token. It does **not** expose a filter/delivery-policy
editor — tuning a subscription's name, delivery limits, content scope, or building a custom
multi-source subscription is an admin/automation concern handled directly through the
`POST`/`PUT /api/subscriptions/{id}` lifecycle below, not through the reader UI.

### Retrieval is hard-scoped to subscriptions (user side)

"我订阅" resolves to the union of `source_id`s across the user's active subscriptions.

`POST /api/vector/search` and `POST /api/rag/context` restrict semantic retrieval to the
restricted `user` account's subscribed sources — there is no whole-archive opt-out for that
role. A requested `source_id` is only honored if it falls inside that set; a `user` with no
subscriptions gets an empty result (`scoped: true`). The `admin` superuser (and the
no-auth case) is **not** scoped — admin searches the whole archive. `GET
/api/vector/subscribed-stats` exposes the `user`'s read-only coverage
(`subscribed_source_count`, `total`, `vectorized`, `pending`); 向量雷达 itself is now
admin-facing (a `user` searches through the 阅读器's keyword search instead).

The same applies to MCP via token: `search_articles` / `browse_articles` accept a token
(`dsub_` single subscription, or `dfeed_` the user's whole subscription union) that scopes
results; the tokenless endpoint remains the global archive surface for trusted integrations.

**Admin exception (2026-07)**: the `admin` account does not subscribe (subscriptions are a
reader concept), so its `dfeed_` token is **not** narrowed by subscriptions — the personal
feed endpoints (`GET /api/public/feed/articles[.md]`) return the whole archive, an explicit
`source_ids` filter is honored verbatim (no subscription intersection), and the MCP scope
resolver returns `[]` ("unrestricted") for an admin feed token. This mirrors the existing
rule that admin's own session retrieval is never subscription-scoped.

The 阅读器 is the user's browse surface; its 我的订阅 view is backed by
`GET /api/articles?subscribed_scope=only` (the same `subscribed_scope=only|prioritize`
filter also powers admin's 知识台账 lens; `off` is the default).

### Vectorization is an admin (collector) concern, not user-facing

Because the vector collection is shared, "what gets vectorized" is a global decision and
cannot belong to any single user (one user vectorizing an article would affect every other
subscriber of that source). Vectorization is therefore managed only on collector/admin
surfaces; user (`reader`) accounts cannot trigger or select it — they only consume via the
hard-scoped retrieval above.

- `GET` / `POST /api/vector/auto-vectorize` (`{enabled}`) — admin toggle for "auto-vectorize
  newly fetched articles". When on, `run_fetcher_with_tracking` vectorizes each run's newly
  saved articles (best-effort; failures never abort the fetch).
- `POST /api/vectorize/{id}`, `POST /api/vectorize/batch`, `POST /api/vectorize/all-pending`,
  `POST /api/vector/reindex-all` — admin manual build/maintenance, surfaced in the admin
  knowledge ledger.

All `/api/vectorize/*` and `/api/vector/*` paths are collector-gated **except** the
read-only `/api/vector/search`, `/api/vector/stats`, `/api/vector/subscribed-stats`, which
stay reader-gated. A `reader` account calling a build/manage endpoint gets `403`.

## Personal Aggregated Feed (primary consumer surface)

Most consumers want one endpoint covering **all** of a user's subscribed sources rather
than juggling one token per source. The personal feed provides exactly that, authorized
by a per-user feed token (independent of any single subscription):

```http
GET /api/public/feed/articles
GET /api/public/feed/articles.md
Authorization: Bearer dfeed_...
```

The token is managed by the logged-in reader:

```http
GET  /api/reader/feed-token          # status + preview (never echoes plaintext)
POST /api/reader/feed-token/rotate   # create/rotate; returns plaintext once
```

The feed resolves the union of the caller's active subscribed `source_id`s and returns
articles across all of them, **ordered by publish date (newest first)**. Supported
filters:

| Parameter | Notes |
| --- | --- |
| `publish_date_start` / `publish_date_end` | Source publish-time window (`YYYY-MM-DD`). The primary filter for daily-brief style consumers. |
| `content_type` / `content_types` | Single or comma-separated content types. |
| `source_ids` | Comma-separated subset; **intersected** with the user's subscribed sources, so a token can never pull an unsubscribed source. |
| `search` | Title substring filter. |
| `has_content` | Defaults to `true`. |
| `include_content` | Defaults to `true`; `false` for metadata-only pulls (`.json` only). |
| `skip` / `limit` | Offset pagination. `limit` capped at 500 (JSON) / 200 (Markdown). |

**Archive (fetched) time is intentionally not exposed on this user-facing surface** — it
is an internal ingestion detail. Consumers filter by *publish* time. (The collector-side
`GET /api/feed/articles` still accepts `fetched_date_*` for operational/incremental use.)

With no active subscriptions the feed returns an empty result rather than the whole
archive.

## Per-Subscription Consumer Endpoints

Each subscription also exposes its own token-scoped endpoint (used internally per
one-click source subscription; available for consumers who want isolated per-source
tokens). Downstream consumers use the subscription token, not the admin cookie:

```http
GET /api/public/subscriptions/{subscription_id}/articles
Authorization: Bearer dsub_...
```

Prefer the `Authorization: Bearer` header. For tools that cannot set headers, the
endpoint also accepts `?token=dsub_...`, but query-string tokens may be captured by
access logs, proxy logs, browser history, or referrers. Treat query-token usage as a
lower-security fallback and rotate those tokens more aggressively.

Query parameters:

| Parameter | Notes |
| --- | --- |
| `skip` | Offset pagination. |
| `limit` | Optional request limit, capped by the subscription `max_limit`. |

Response shape matches the existing feed article delivery shape:

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

### Tokenized semantic search

```http
POST /api/public/subscriptions/{subscription_id}/vector/search
Authorization: Bearer dsub_...
Content-Type: application/json

{"query": "agent frameworks", "top_k": 5, "rerank": false}
```

Runs semantic search constrained to the subscription's `source_ids` (and its single
`content_type` when set). The response includes `scoped_source_ids` and ranked
`results`. This is the per-subscription, personalized retrieval surface for downstream
agents over HTTP.

### Per-subscription MCP scope

The reader MCP server (`/mcp`) has **no login session on its transport** — a token is the
only authorization signal it carries. Its content-returning tools (`search_articles`,
`browse_articles`, `get_article`, and `get_rag_context`) therefore **require** a
`subscription_token` argument: results are constrained to that token's sources, giving each
consumer a personalized MCP view through one shared MCP endpoint. A missing, invalid, or
inactive token makes the tool return an error instead of any data — there is no unscoped
global surface over MCP. `list_sources` is the only exception: it returns just the source
catalog (ids/types, no article bodies) and needs no token, so a consumer can discover what
to subscribe to. Admins searching the whole archive use the in-app surfaces (向量雷达 /
知识台账), not MCP; to debug MCP they pass their own `dfeed_` token like any reader.

A token is either a per-subscription `dsub_` token or the per-user aggregated `dfeed_` token
(obtainable from 接入集成 → 访问令牌 / `GET /api/reader/feed-token`).

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

Two token families exist, both independent of admin/login sessions and stored only as
salted HMAC-SHA256 hashes; reads expose only a short suffix preview such as `...abc123`,
and rotation invalidates the previous token.

- **Per-subscription tokens** (`dsub_` prefix) authorize one subscription's endpoint.
- **Per-user feed tokens** (`dfeed_` prefix) authorize the personal aggregated feed across
  all of that user's subscribed sources. One row per user (`reader_feed_tokens`); rotating
  replaces it in place.

Inactive subscriptions return no consumer data, and the feed returns nothing for a user
with no active subscriptions.

## Current Limits

- Per-user / per-subscription MCP scoping is delivered via a token tool argument on the
  shared reader MCP (`dfeed_` for the user's whole subscription union, `dsub_` for one
  subscription), not as separate per-user MCP servers/endpoints. The tokenless MCP endpoint
  still exposes the global archive for trusted integrations.
- Tokenized semantic search scopes by `source_id` (and a single `content_type`); it does
  not yet apply the subscription's date-window or `content_types` (plural) filters.
- Subscription filtering depends on archive records already present in the reader DB;
  it does not sync missing collector metadata by itself.
