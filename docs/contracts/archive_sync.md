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
- `reader` and `all` can import, but import requires an `admin` login account
  because it mutates the whole archive.
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
| `fetched_date_start` / `fetched_date_end` | Archive change window: `archive_updated_at`, falling back to first-ingest `fetched_date` for older records. The parameter name is retained for compatibility. Use this for incremental sync cursors. |
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
{"kind":"article","schema_version":"articles-jsonl-v1","checksum":"sha256...","article":{"id":"article_id","title":"Article title","content_type":"rss_article","source_id":"rss_openai_news","source_url":"https://example.test/article","publish_date":"2026-05-25T00:00:00","fetched_date":"2026-05-25T01:00:00","archive_updated_at":"2026-05-25T01:00:00","fetch_run_id":1,"job_id":2,"job_run_id":3,"source_group_id":4,"run_scope":"saved_job","has_content":true,"content":"Article body","extensions":{}}}
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
  reader backfills the content.
- Existing Podcast episodes merge a strict publisher-owned metadata allowlist when
  the incoming `archive_updated_at` is not older. Reader-derived fields such as
  Chinese summaries, processing state, and condensed-audio URLs are preserved.

`fetched_date` remains the immutable first-ingest time used by reader ordering,
unread watermarks, and Daily Brief collection. `archive_updated_at` changes when a
faithful archive record is refreshed and is the effective incremental-sync cursor.
Older v1 payloads without the additive field remain valid and fall back to
`fetched_date`.

Derived indexes (the reader-side FTS5 full-text index) are rebuilt locally by
triggers on insert/update and are intentionally not part of the sync payload.
(历史注记:v3.31 前此处还有 `is_vectorized` 重置语义,已随向量层退役删除——
该字段从未进入线格式,新旧版本部署间同步互通不受影响。)

## Preserved Fields

The sync payload preserves:

- `id`
- `title`
- `content_type`
- `source_id`
- `source_url`
- `publish_date`
- `fetched_date`
- `archive_updated_at` (additive v1 field; optional for older producers)
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

## V2: production split-deployment replication

V1 remains supported for compatibility. New internal deployments use V2, which
is a record-level protocol rather than a database-directory copy. This is
important because the internal database owns users, subscriptions, interests,
manual/rule tags, and personal briefs; those records must never be overwritten
by the external deployment.

Once a node has installed the V2 consumer fence or received any V2 authority-owned
row, the legacy V1 import endpoint and V1 scheduler are rejected with HTTP 409.
This prevents an old article-only writer from bypassing V2 authority and tombstone
semantics.

### Direction and authority

- Platform/public sources are collected and analysed by the external Dorami.
- User-added RSS is collected and analysed by the internal Dorami, which may call
  an external MaaS directly. Its article body is not copied to external Dorami.
- Taxonomy and Candidate review are governed by external Dorami.
- `authority_id` is a persisted UUID (or explicit
  `DORAMI_ARCHIVE_AUTHORITY_ID`), not a runtime `collector/reader/all` role or a
  hostname. A changed authority ID requires an operator-approved checkpoint reset.
- Imported articles carry `analysis_authority_id`; every queue, backfill, claim,
  manual fetch and worker-commit boundary rejects non-local authority. Thus two
  deployments may both remain `role=all` without analysing a public article twice.
- The receiver persists a separate v2-consumer fence before its first manual or
  scheduled pull is queued. This closes the pre-authority upgrade window without
  inventing an authority ID: public collection/analysis is quiesced immediately,
  while internal custom RSS remains locally collected (credentialed feeds remain
  excluded from MaaS analysis).
- A legacy schedule with `source_ids` but no `protocol` is exposed as
  `migration_required` and is effectively disabled until an administrator
  explicitly saves v1 or v2. V1 is never inferred as an active compatibility mode.

### Streams and readiness fence

The receiver pulls the following independent streams in this exact order:

1. `sources`
2. `taxonomy`
3. `articles`
4. `analyses`
5. `media`
6. `source_states`

`source_states` is last by design. All non-Taxonomy streams use the same committed
transaction-revision snapshot, so publishing terminal readiness cannot outrun
the matching article, analysis, or media generation. A stream checkpoint advances
only after its terminal page has committed; the media checkpoint additionally
waits for every declared binary to pass byte-size and SHA-256 verification.

Endpoints:

```http
GET  /api/archive/v2/export/{stream}.jsonl?since=&snapshot=&after=&limit=
POST /api/archive/v2/import/{stream}.jsonl
POST /api/archive/v2/presence
GET  /api/archive/v2/media/{url_hash}
POST /api/archive/v2/candidate-evidence.jsonl
```

The `/v2/` endpoint now emits an `archive-sync-v3` manifest containing `stream`,
`authority_id`, an exclusive `since` watermark, fixed `snapshot`, opaque keyset
`after`/`next_cursor`, `complete`, and `count`. Every record has an identity,
integer revision, `operation=upsert|tombstone`, canonical-JSON SHA-256 checksum,
and payload. The complete page is validated before its single database transaction
starts. Offset pagination is not used. A failed later page leaves the checkpoint
unchanged, so already committed pages are safely replayed.

SQLite triggers allocate revisions from one monotonic clock in the same transaction
as the business write. They cover ORM and bulk SQL paths. A rollback rolls the clock
and entity state back too; a write that has not flushed cannot be hidden behind an
exported snapshot. Article content changes advance both the Article and its Analysis
entity state, and LLM assignment changes advance Analysis. Pages use numeric
`(revision, identity)` ordering and every continuation repeats the fixed snapshot.

Every manifest advertises `transaction-revision-tombstone-v1` and
`authoritative-presence-v1`. Before installing its consumer fence or rebasing any
data, the receiver verifies the exact schema, complete capability set, authority,
a published Taxonomy version, and an enabled local Media store.
On the first v3 launch it atomically discards incomparable timestamp checkpoints and
performs an authority-scoped rebase: eligible public Sources become temporarily
inactive, their SourceState readiness is cleared, and prior entity-state revisions
are discarded. Articles, Analyses, reader counters, local manual/rule assignments,
and Media remain intact while the first authoritative snapshot is fetched. This
keeps a failed first pull from destroying a still-readable local archive.
After the terminal full Article and Analysis pages, applied upsert states identify
receiver-only candidates. The receiver sends only those identities to the authority's
bounded `/presence` endpoint, validates every chunk, and then prunes only identities
confirmed absent in one local transaction. This protects an existing row whose
revision moved beyond the fixed page snapshot during pagination; the next incremental
run imports its newer value. Any presence failure leaves all candidates and the
checkpoint untouched. Matching/present rows retain reader-local counters and manual
tag overlays.
The same protocol epoch is idempotent; an authority change fails closed for manual
operator review. Deploy the external producer first, then the internal receiver.
Only one remote-sync job may be queued or running on a receiver at a time. A receiver
therefore consumes one configured authority serially; a restarted process marks
orphaned queued/running jobs failed before scheduling a replacement.

The first run is full; later runs use each stream's completed snapshot as its
exclusive lower watermark. V2 intentionally rejects `source_ids`: a filtered
article set cannot be made consistent with global taxonomy, analysis, media and
source terminal state.

Prototype compatibility limit: if a Source had already been physically deleted
on the producer before transaction revisions were deployed, no historical
tombstone exists to replay. First-time rebase leaves such receiver-only legacy
SourceConfig rows inactive and fenced from collection instead of attempting an
automatic physical purge. Operators may remove those inactive metadata rows after
verification; this does not affect Article history. Reader hidden-source settings
are likewise intentionally local to each deployment in this version.

### Analysis and taxonomy merge

Articles may arrive before analysis. For an authority handoff with unchanged
content, the old successful result remains readable with an updating state until
the external revision arrives. A changed content hash hides the stale result.
Analysis import requires an exact article content hash. The consumer's retained
entity-state revision rejects stale replays even after a tombstone, so an older
upsert cannot resurrect deleted state.

Deletion preserves the existing product distinction: toggling a Source writes
`is_active=false` and replicates that reversible state, while deleting a Source is
a physical delete and emits a tombstone. Article, Analysis, and SourceState physical
deletes also emit tombstones and are physically applied on the receiver; normal
database cascades therefore remove Article-owned Analysis and tag assignments.
Reader source visibility (`reader_hidden_source_ids`) remains node-local operational
state and is not a deletion or replication signal. Media deletion is not propagated.
The receiver's daily local GC removes only remote-owned media
that has exceeded a seven-day grace window and is no longer referenced by any
Article; content-addressed bytes remain until the final URL record sharing that file
is gone. Locally fetched media and negative-cache records are outside this GC.

Taxonomy identities and article assignments cross the wire by stable `tag.code`,
never numeric database IDs. Incoming LLM assignments replace only prior LLM
assignments. Internal `manual`, `rule`, and `migration` overlays are preserved and
win primary-tag conflicts. Once a taxonomy authority snapshot is imported, the
internal admin taxonomy endpoints remain readable but reject mutations; governance
continues only on the external authority.

### Media and Candidate evidence

The media manifest covers images referenced by article bodies, social media, and
Podcast `image_url`/`cover_url`. Original Podcast audio is deliberately excluded.
The producer serves only cache entries still referenced by public articles. The
receiver downloads only manifest-declared files and verifies the byte limit,
exact size, content hash, image magic, declared MIME and extension before atomic
installation. Media responses use `X-Content-Type-Options: nosniff`; a newer
manifest revision can replace an already cached binary without using the receiver
clock as its authority revision.

After pulling, internal Dorami may upload minimized Candidate evidence containing
only label, facet kind, confidence, an opaque article fingerprint, source
provenance ID, and prompt version. Content, summaries, article IDs, URLs, and
context excerpts are rejected. Such evidence enters the external review pool
with `user_added_source` risk provenance but is stored separately from automatic
activation statistics and is shown through a redacted allow-listed review payload.
Uploads are paged snapshot replacements per submitting authority: old evidence is
withdrawn only after a terminal page commits, so an interrupted upload cannot erase
the last complete review snapshot.
