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
receives its future output. **Decommissioned fetcher nodes** (the `[[tombstone]]` entries in
`src/sources/catalog/system.toml` — classes removed from `impl/` during node audits but whose
historical archive lingers) are excluded from the archived-articles set, so a removed
node does not silently reappear in the reader catalog after node management has been
slimmed; the only exception is a user who is *already* subscribed to one, who still sees
it so they can unsubscribe. Legitimate unregistered import sources (e.g. `social_post`)
are *not* on that list and remain subscribable. Each entry carries a friendly `name` and `description`/`icon`
(enriched from the fetcher registry, falling back to the built-in names of the system source
and tombstones in the catalog's `system.toml`, then the raw id), primary `content_type`, a grouping `category` (the Chinese name of the data structure, from the same
vocabulary as `content_type_label` — issue #178), article `count`, a `registered` flag,
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
unsubscribe (one source per click), and 设置 → 接入集成 → 聚合接口 rotates/reveals the
`dfeed_` token and lists every HTTP endpoint that token can call, each with a copyable URL,
an example and its parameters (issue #315). It does **not** expose a filter/delivery-policy
editor — tuning a subscription's name, delivery limits, content scope, or building a custom
multi-source subscription is an admin/automation concern handled directly through the
`POST`/`PUT /api/subscriptions/{id}` lifecycle below, not through the reader UI.

### Retrieval is hard-scoped to subscriptions (user side)

"我订阅" resolves to the union of `source_id`s across the user's active subscriptions.

Retrieval surfaces (the 阅读器's search, the QA assistant's subscription scope, and the
tokenized endpoints below) restrict a restricted `user` account to its subscribed sources —
there is no whole-archive opt-out for that role. A `user` with no subscriptions gets an
empty result. (v3.31 起检索为 SQLite FTS5 全文检索;向量/RAG 端点已退役,见
`docs/archive/rag-retirement-plan.md`。)

The same applies to MCP via token: `search_articles` / `browse_articles` accept a token
(`dsub_` single subscription, or `dfeed_` the user's whole subscription union) that scopes
results; the tokenless endpoint remains the global archive surface for trusted integrations.

**Admin exception (2026-07)**: the `admin` account does not subscribe (subscriptions are a
reader concept), so its `dfeed_` token is **not** narrowed by subscriptions — the personal
feed endpoints (`GET /api/public/feed/articles[.md|.xml]`) return the whole archive, an explicit
`source_ids` filter is honored verbatim (no subscription intersection), and the MCP scope
resolver returns `[]` ("unrestricted") for an admin feed token. This mirrors the existing
rule that admin's own session retrieval is never subscription-scoped.

The 阅读器 is the user's browse surface; its 我的订阅 view is backed by
`GET /api/articles?subscribed_scope=only` (the same `subscribed_scope=only|prioritize`
filter also powers admin's 知识台账 lens; `off` is the default).

## Personal Aggregated Feed (primary consumer surface)

Most consumers want one endpoint covering **all** of a user's subscribed sources rather
than juggling one token per source. The personal feed provides exactly that, authorized
by a per-user feed token (independent of any single subscription):

```http
GET /api/public/feed/articles        # JSON
GET /api/public/feed/articles.md     # Markdown
GET /api/public/feed/articles.xml    # Atom 1.0 (application/atom+xml), for feed readers
Authorization: Bearer dfeed_...
```

The three are the same pull in three formats. Feed readers usually cannot send an
`Authorization` header, so the Atom variant is typically subscribed to as
`.../articles.xml?token=dfeed_...` — the one place 设置 → 聚合接口 shows the token in the URL;
the query-token caveats under *Per-Subscription Consumer Endpoints* apply.

The token is managed by the logged-in reader:

```http
GET  /api/reader/feed-token          # status + preview (never echoes plaintext)
POST /api/reader/feed-token/rotate   # create/rotate; returns plaintext once
```

The status response also reports how the **current** token has been used (issue #312):
`last_used_at` (last successful call, any time; `null` if never), `usage_calls` (successful
calls in the last `usage_window_days` = 30 days). Rotating starts a new token, so both reset.

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
| `include_content` | Defaults to `true`. JSON: `false` omits the body (metadata-only pull). Atom: `false` replaces each entry's `<content>` with a `<summary>` holding the first 280 characters of the body. The Markdown export does not take it. |
| `skip` / `limit` | Offset pagination. `limit` capped at 500 (JSON) / 200 (Markdown, Atom). |

**Archive (fetched) time is intentionally not exposed on this user-facing surface** — it
is an internal ingestion detail. Consumers filter by *publish* time. (The collector-side
`GET /api/feed/articles` still accepts `fetched_date_*` for operational/incremental use.)

With no active subscriptions the feed returns an empty result rather than the whole
archive.

## Hot Events Board (site-wide, same feed token)

The 榜单 page's 热点事件 board is also delivered by token (issue #310), so a script does not
need a login or guest cookie:

```http
GET /api/public/feed/events
GET /api/public/feed/events/{event_id}
Authorization: Bearer dfeed_...
```

Only the per-user feed token authorizes these (`Authorization: Bearer`, or `?token=` with the
caveats described under *Per-Subscription Consumer Endpoints*). A `dsub_` subscription token
or a login cookie is not accepted. A missing or invalid token returns `401` before any
parameter-range check; a malformed value (`event_id=abc`) is still a `422`.

**The board is site-wide, not subscription-scoped.** Every valid feed token — reader or
admin — receives the board that `GET /api/reader/events` shows every reader and guest; the
token only decides whether the caller may pull. Both surfaces call
`services/event_board.read_board` / `read_event`, so they cannot drift:

- Only reader-visible members count. Members from hidden, user-private and credentialed
  sources are dropped at read time, and title, summary, publishers and counts are computed
  from what is left.
- An event needs at least two visible publishers to rank. Order is publisher count ↓, then
  latest member first-seen ↓. At most 10 events, never padded.
- The board is computed per node from that node's visible sources and its own 07:00
  (Asia/Shanghai) rank snapshot, so two deployments can rank the same event differently.

| Parameter | Notes |
| --- | --- |
| `window_hours` | Events first seen within this window. Default `168` (7 days); `48` is the reader's 近 2 天 view. 1–336, otherwise `400`. |
| `limit` | 1–10, default 10, otherwise `400`. List only. |
| `include_members` | List only. `true` adds `members` to every item (same shape and order as the detail endpoint), so one request returns the board with all its reports. Default `false`. |

List response — the reader board payload plus the feed family's `status` / `count`:

```json
{
  "status": "success",
  "count": 1,
  "generated_at": "2026-11-19T02:00:00+00:00",
  "window_hours": 168,
  "snapshot_date": "2026-11-19",
  "empty_reason": null,
  "items": [
    {
      "event_id": 123,
      "rank": 1,
      "previous_rank": 2,
      "title": "Event title",
      "title_lang": "zh",
      "summary": "Summary of the representative report",
      "publisher_count": 3,
      "member_count": 4,
      "first_seen_at": "2026-11-18T09:12:00+00:00",
      "first_report_at": "2026-11-18T08:30:00+00:00",
      "latest_at": "2026-11-19T01:40:00+00:00",
      "latest": {
        "article_id": "…", "title": "…", "title_zh": null,
        "source_id": "…", "source_name": "…", "published_at": "…"
      },
      "is_new": false,
      "is_recent": true,
      "representative": {
        "article_id": "…", "title": "…", "source_id": "…", "source_name": "…",
        "source_role": "official", "url": "https://…", "publish_date": "…"
      },
      "publishers": [{"key": "…", "name": "…", "count": 2, "source_id": "…"}],
      "story": {"follows_event_id": null, "follows_title": null}
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `generated_at` | When this response was computed (UTC). The board is computed on request, not cached. |
| `snapshot_date` | Date of the daily snapshot `previous_rank` is compared against: the most recent one for the same window generated before now. `null` when none exists yet. |
| `empty_reason` | `"no_events"` when `items` is empty, otherwise `null`. |
| `rank` | Position on this window's board right now. Always set in the list; in the detail response it is `null` when the event is not currently on the board. |
| `previous_rank` | Position on the snapshot named by `snapshot_date`. `null` when the event was not on that snapshot, or when no snapshot exists. Looked up independently of `rank`. |
| `title` / `title_lang` | Display title computed from visible members; `title_lang` is `zh` or `en`. |
| `summary` | Analysis summary of the representative report; may be empty. |
| `publisher_count` / `member_count` | Distinct visible publishers / visible reports. Several channels of one owner count as one publisher; provisional (push-ingested) sources are members but not publishers. |
| `first_seen_at` / `latest_at` | When the event's first / latest member was first seen (ingest time, not publish time). |
| `first_report_at`, `latest` | Earliest publish time among members, and the most recently published report. |
| `is_new` / `is_recent` | Event first seen within 6 hours / 48 hours. |
| `representative` | The report shown for the event (official > non-social > score > earlier). `source_role` is `official`, `media`, `personal` or `leaderboard`. |
| `publishers[]` | One entry per publisher: `key`, display `name`, report `count`, a representative `source_id`. |
| `story` | The earlier event this one follows, when that event has visible members; otherwise both fields are `null`. |

With `include_members=true`, and always in the detail response, `members` lists the visible
reports ordered by `report_at` descending, ties broken by `article_id` ascending:

```json
{
  "article_id": "…", "title": "…", "title_zh": null,
  "source_id": "…", "source_name": "…", "source_role": "media", "content_shape": "article",
  "url": "https://…", "publish_date": "…", "report_at": "…", "published_at": "…",
  "publisher_key": "…", "is_representative": false, "summary": "…"
}
```

`content_shape` is `article`, `social` or `podcast`; `title_zh` is the cached translated
title when there is one. Timestamps are ISO-8601 strings as archived.

A member carries three different times, and the list is ordered by the second one:

| Field | Meaning |
| --- | --- |
| `publish_date` | Publish time as archived from the source, unmodified. It can be missing, unparseable, or later than the time the report was ingested. |
| `report_at` | Grouping time: `publish_date` clamped into the 72 hours before the report was first seen (the first-seen time when `publish_date` is missing). `members` is sorted by this; a stored `report_at` that cannot be parsed falls back to `publish_date` for ordering. |
| `published_at` | Display publish time: `publish_date` when it parses and is not later than the report's first-seen time, otherwise `report_at`. The item's `first_report_at` and `latest` are picked by this. |

Because `report_at` is clamped, a report published long ago but ingested recently sorts as
recent. Do not read `members[0]` as the most recently published report: use the item's
`latest`, or sort `members` by `published_at`.

Detail response:

```json
{"status": "success", "event": {}}
```

`event` carries every list item field plus `members`, `followups`
(`[{event_id, title, first_seen_at}]`, later events that follow this one) and `merged_into`.
`rank` refers to the `window_hours` board and is `null` for an event not currently on it — a
single-publisher event can be fetched by id but never ranks. `previous_rank` is looked up
separately in the same window's snapshot, so an event that ranked on that snapshot and has
since dropped off the board returns `rank: null` with a non-null `previous_rank`. An id that
was merged away returns the surviving event with `merged_into` set to its id. An unknown id,
or an event with no visible member, returns `404`.

A deployment that predates these endpoints answers `404`; a node running with
`[runtime] role=collector` answers `403` regardless of version.

**Stability.** These field sets are a delivery contract: additive changes only. They are
pinned by `tests/test_events_api.py` (`ITEM_KEYS`, `MEMBER_KEYS`, `DETAIL_KEYS`,
`FEED_BOARD_KEYS`), which fails when a reader-side change adds, renames or drops a key.

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

### Tokenized full-text search

```http
POST /api/public/subscriptions/{subscription_id}/vector/search
Authorization: Bearer dsub_...
Content-Type: application/json

{"query": "agent frameworks", "top_k": 5}
```

Runs FTS5 full-text search (title + body, Chinese & English substrings; v3.31 起换芯,
路径为历史兼容保留) constrained to the subscription's `source_ids` (and its single
`content_type` when set). The response includes `scoped_source_ids` and `results`
ordered by publish date desc. Legacy body fields `score_threshold` / `rerank` are
accepted and ignored. This is the per-subscription, personalized retrieval surface
for downstream agents over HTTP.

### Per-subscription MCP scope

The reader MCP server (`/mcp`) has **no login session on its transport** — a token is the
only authorization signal it carries. Its content-returning tools (`search_articles`,
`browse_articles`, `get_article`, and `get_rag_context`) therefore **require** a
`subscription_token` argument: results are constrained to that token's sources, giving each
consumer a personalized MCP view through one shared MCP endpoint. A missing, invalid, or
inactive token makes the tool return an error instead of any data — there is no unscoped
global surface over MCP. `list_sources` is the only exception: it returns just the source
catalog (ids/names/types and last-fetch status, no article bodies; the fetcher `category` key was
dropped in issue #178 because it leaked the internal admission state) and needs no token, so a consumer can discover what
to subscribe to. Admins searching the whole archive use the in-app surfaces (知识台账检索),
not MCP; to debug MCP they pass their own `dfeed_` token like any reader.

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
  all of that user's subscribed sources, and the site-wide hot events board. One row per
  user (`reader_feed_tokens`); rotating replaces it in place.

Inactive subscriptions return no consumer data, and the feed returns nothing for a user
with no active subscriptions.

## Call Metering (issue #312)

Every **successful** call made with a `dfeed_` or `dsub_` token is counted, per day, per
token, per endpoint: the aggregated feed (JSON / Markdown / Atom), the hot events board and
event detail, the per-subscription pull and search, and the four content MCP tools. What is
stored is the call count, the number of items returned and the time of the last call — not the
request parameters, not the returned content, not the caller's IP. Administrators see the
totals per account and per token; a reader sees the usage of their own current token in
接入集成 → 聚合接口.

- Requests that fail validation (400) or hit a missing event (404) are not calls.
- A token that fails authentication is not recorded, with one exception: a token that used to
  be valid (rotated or belonging to a removed subscription) gets a "rejected after retirement"
  count under its old record, so an administrator can tell that something is still using it.
- Metering never changes a response and never fails a request.
- Counts are per node; they are not carried by Archive Sync.

## Current Limits

- Per-user / per-subscription MCP scoping is delivered via a token tool argument on the
  shared reader MCP (`dfeed_` for the user's whole subscription union, `dsub_` for one
  subscription), not as separate per-user MCP servers/endpoints. The tokenless MCP endpoint
  still exposes the global archive for trusted integrations.
- Tokenized semantic search scopes by `source_id` (and a single `content_type`); it does
  not yet apply the subscription's date-window or `content_types` (plural) filters.
- Subscription filtering depends on archive records already present in the reader DB;
  it does not sync missing collector metadata by itself.
