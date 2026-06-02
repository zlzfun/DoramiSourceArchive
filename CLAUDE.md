# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (Python)

```bash
# Install dependencies (use uv, the project uses uv.lock)
uv sync

# Run the backend server (starts on http://127.0.0.1:8088, hot-reload enabled)
python src/main.py

# API docs available at http://127.0.0.1:8088/docs
```

### Frontend (React + Vite + Tailwind CSS v4)

```bash
cd frontend
npm install
npm run dev      # Start dev server (port 5173, proxies /api → backend)
npm run build    # Production build
npm run lint     # ESLint
```

Data is stored in the `data/` directory (SQLite `cms_data.db` and ChromaDB `chroma_db/`).

## Architecture Overview

**DoramiSourceArchive** (哆啦美·归档中枢) is an AI content aggregation CMS with RAG capabilities. It fetches content from multiple sources, stores it in SQLite, and builds a vector index in ChromaDB for semantic search. It splits into two cooperating layers — a **collector/archive** side (fetching, archival, vectorization) and a **reader/distribution** side (per-user subscriptions, semantic search, tokenized feed/MCP delivery) — gated by runtime role and login account role (see *Runtime roles & dual-axis access control*).

### Core Data Flow

```
Fetcher → DataPipeline → DatabaseStorage (SQLite via SQLModel)
                                    ↓ (explicit separate step)
                         ChromaVectorStorage (ChromaDB + sentence-transformers)
```

**Important**: `DataPipeline` is initialised with only `db_sink` — vectorization into ChromaDB is a separate, **admin-managed** step (see *Vectorization is admin-managed* below). By default it must be triggered explicitly (`POST /api/vectorize/{article_id}`, batch, or `all-pending`); when the `auto_vectorize` setting is on, each fetch run's newly-saved articles are vectorized automatically via the `run_fetcher_with_tracking` hook.

### Key Design Decisions

**Dual-dimension content identity**: Every piece of content carries both `content_type` (data shape — `arxiv`, `wechat_article`, `tech_conference`, etc.) and `source_id` (which channel produced it — `wechat_jiqizhixin`, `webhook_dify_workflow`, etc.).

**Plugin-based fetcher discovery**: `FetcherRegistry` (`src/fetchers/registry.py`) auto-scans `src/fetchers/impl/` for `BaseFetcher` subclasses at import time. Any new fetcher placed there with `source_id`, `content_type`, `name`, `icon`, and `description` class attributes is automatically registered and surfaced in the frontend. The frontend dynamically renders parameter forms based on `get_parameter_schema()`, whose return format is:
```python
[{"field": "limit", "label": "单次获取上限", "type": "number", "default": 5}]
```

Three fetcher base classes cover the major source types:
- `BaseFetcher` — base for all fetchers; provides `_safe_get`/`_safe_post` with retries
- `BaseWebPageListFetcher` (`webpage_fetcher.py`) — scrapes an HTML listing page; subclasses declare `listing_url`, `article_url_patterns`, and optionally set `fetch_detail=True` to extract article body from the detail page. Optional knobs: `drop_empty_content=True` discards entries with no body (nav/footer junk), and `max_listing_pages` + a `_next_listing_page_url()` override paginate the listing (e.g. Cursor's `/changelog/page/N`) to accumulate enough entries for `limit`
- `GenericGitHubReleasesFetcher` (`github_release_fetcher.py`) — hits the GitHub Releases API; `PresetGitHubReleasesFetcher` subclasses hard-code `owner`/`repo` as built-in sources

**`extensions_json` serialization pattern**: `serialize_to_metadata()` splits a content object's fields into base fields (from `BaseContent`) and subclass-specific extension fields. The extensions are serialised as a JSON string into the `ArticleRecord.extensions_json` column. When reconstructing for vectorization, a `GenericContent` object is used since the ORM only stores the flat record.

**Playwright browser-rendered detail (Cloudflare bypass)**: Most fetchers are pure httpx, but a few sources gate their article bodies behind a Cloudflare Managed Challenge that only a real browser can pass (httpx gets a 403 challenge shell). `src/fetchers/impl/playwright_renderer.py` provides `PlaywrightRenderer`, an async context manager that lazily launches a headless Chromium for the duration of one fetch run, then renders each blocked article: it opens a fresh page per article, throttles requests, polls until the challenge clears and the body text appears, retries, and returns `""` on any failure so the caller degrades gracefully. Currently only `OpenAINewsRssFetcher` uses it — it overrides `_detail_for_url` to prefer the rendered body and fall back to the RSS summary when rendering fails (`openai.com` is the one audited source behind this challenge). Playwright is an opt-in path: when a node needs no detail fetch, no browser is started. (Note: the legacy WeChat Official Account Playwright login fetcher has been removed; only the `WechatArticleContent` type and the `wechat_article` display label remain for historical archived data.)

**Vector chunking & cleaning**: Text is cleaned via `clean_text()` (HTML stripping, HN boilerplate removal, arxiv prefix removal) then split into paragraph-aware 800-char chunks with 150-char overlap. Articles with `< 30` usable characters are indexed with a header-only chunk. Metadata headers (source name, date, title) are prepended to every chunk to support temporal and source queries. Each chunk is an independent ChromaDB document linked by `parent_id` metadata. Semantic search fetches `top_k * 4` raw chunks then deduplicates by `parent_id`.

**Embedding model**: Default is `BAAI/bge-m3` (multilingual, supports Chinese queries against English documents). Override with `LOCAL_MODEL_PATH`. Changing models requires `POST /api/vector/reindex-all` to rebuild the collection from scratch.

**RAG is opt-in and lazy-loaded**: The entire vector/RAG subsystem is gated by `[rag] enabled` (default `false`, override `DORAMI_RAG_ENABLED`). When off, `vector_sink` is `None` and no embedding-model weights ever load, keeping startup fast and the server runnable on low-memory hosts. Even when enabled, `ChromaVectorStorage` defers chromadb client / embedding-fn / collection creation to first use via `_ensure_collection()` (mirroring the lazy `_ensure_reranker()` cross-encoder). All `/api/vector*`, `/api/vectorize*`, `/api/rag*`, and the auto-vectorize toggle go through `require_vector_sink()` (503 when disabled); article CRUD skips vector purge when off; MCP semantic-search tools return a structured "RAG disabled" result instead of failing. `rag_enabled` is exposed in `GET /api/runtime`, and the frontend hides 向量雷达, the vector-build column/toggles, and greys out RAG MCP tools when off.

**Fetch run tracking**: Every fetcher execution (manual or scheduled) writes a `FetchRunRecord` and upserts a `SourceStateRecord`. The state record is the authoritative health/cursor store per source; `build_fetcher_health_from_state()` in `app.py` derives the `/api/source-health` response from it, falling back to aggregating raw `FetchRunRecord` rows when no state exists.

**Runtime roles & dual-axis access control**: Surfaces gate on two independent axes (`src/api/app.py`): the deployment **runtime role** (`[runtime] role` = `collector` | `reader` | `all`) and the **login account role** (`admin` | `user`, from `[auth] admin_users` / `user_users`). `collector` surfaces (节点管理/任务运行, article CRUD, vectorization build/manage) require an `admin` account; `reader` surfaces (订阅分发/向量雷达/接入集成, subscription delivery, semantic search) are open to any logged-in account, except archive import, which is admin-only because it mutates the whole archive. So **`admin` is a superuser (collector + reader); a `user` account is a restricted reader**. `disabled_runtime_surface()` enforces this per request via `COLLECTOR_API_PREFIXES` / `READER_API_PREFIXES` (reader-prefix matches short-circuit, so `/api/vector/*` can split: `search`/`stats`/`subscribed-stats` → reader, everything else → collector). The frontend mirrors it through `runtime_capabilities()` → `collector_enabled` / `reader_enabled` / `account_role` per session.

**Reader subscription & distribution layer**: Reader accounts build a personalized subscription scope over already-archived records (it never triggers fetching). One-click subscribe (`POST`/`DELETE /api/reader/sources/{source_id}/subscribe`) creates/removes a per-user, single-source `ReaderSubscriptionRecord` (owned via `owner_username`). "我订阅" = the union of `source_id`s across a user's active subscriptions; for a `user` account it hard-scopes that user's vector/RAG/MCP retrieval and drives the 知识台账 browse filter. Downstream consumers pull via tokens (HMAC-SHA256, stored only as hashes): a per-subscription token (`dsub_`) or the per-user **aggregated feed token** (`dfeed_`, one row per user in `ReaderFeedTokenRecord`) used at `GET /api/public/feed/articles[.md]` — a single endpoint covering all the user's subscribed sources with publish-time/source/type filters. Full contract in `docs/reader_subscription_contract.md`.

**Vectorization is admin-managed**: The ChromaDB collection is shared/global, so building it is a collector/admin concern (one user vectorizing a source's article would affect every subscriber of that source). `user` accounts cannot trigger or select vectorization — they only consume via hard-scoped retrieval and a read-only coverage ratio (`GET /api/vector/subscribed-stats`). Admin manages it from 知识台账: per-article / batch / `all-pending` build, `reindex-all`, and an `auto_vectorize` toggle (`GET`/`POST /api/vector/auto-vectorize`, persisted in `AppSettingRecord`). The `admin` superuser's own retrieval is **not** subscription-scoped (it searches the whole archive); only the restricted `user` role is scoped.

### Project Structure

```
src/
├── main.py                  # Entry point: starts uvicorn with reload=True
├── api/app.py               # FastAPI app — all REST endpoints + APScheduler init
├── models/
│   ├── content.py           # Dataclass content models (BaseContent + subtypes)
│   └── db.py                # SQLModel ORM tables: ArticleRecord, FetchTaskRecord,
│                            #   FetchRunRecord, SourceStateRecord, SourceConfigRecord,
│                            #   NodeGroupRecord, CollectionJobRecord, CollectionJobRunRecord,
│                            #   ReaderSubscriptionRecord, ReaderFeedTokenRecord, AppSettingRecord
├── fetchers/
│   ├── base.py              # BaseFetcher: httpx client, retries, template method
│   ├── registry.py          # FetcherRegistry singleton — auto-discovers impl/ on import
│   └── impl/
│       ├── rss_fetcher.py               # GenericRssFetcher + PresetRssFetcher (23+ built-in RSS sources); OpenAINewsRssFetcher renders detail via Playwright (CF bypass); HackerNewsAiRssFetcher applies a configurable min_points/min_comments hnrss threshold to de-noise the q=AI firehose and is treated as a discovery source (external-link posts degrade to title+URL+discussion+heat with no body; only Ask/Show/Tell self-posts keep a body; external detail fetch off by default)
│       ├── github_release_fetcher.py    # GenericGitHubReleasesFetcher + preset subclasses (13 built-in)
│       ├── repository_model_fetcher.py  # GitHub repo + HuggingFace model fetchers (content_type=github_repository / huggingface_model); GitHub repo fetcher backfills a cleaned README excerpt when a repo has no description (dedup-gated, GITHUB_TOKEN-aware)
│       ├── webpage_fetcher.py           # BaseWebPageListFetcher + preset subclasses (6 built-in)
│       ├── curated_core_fetcher.py      # Curated AI-source presets: SinglePageDocumentFetcher (changelogs/release notes) + per-site BaseWebPageListFetcher/BaseFetcher subclasses (量子位, 新智元, HF Daily Papers, etc.)
│       ├── article_extractor.py         # Shared HTML→article-body extractor (helper module, not a fetcher); used by webpage/rss fetchers to backfill detail
│       ├── playwright_renderer.py       # PlaywrightRenderer: headless-Chromium detail rendering for Cloudflare-challenged sources (used by OpenAINewsRssFetcher)
│       └── webhook_trigger.py           # Outbound Dify workflow trigger (not an inbound content source)
├── mcp_server.py            # build_mcp_app(): FastMCP streamable-HTTP server, mounted at /mcp by app.py
├── pipeline/core.py         # DataPipeline: drives fetcher → broadcasts to registered storages
└── storage/
    ├── base.py              # BaseStorage abstract class
    └── impl/
        ├── db_storage.py    # SQLite storage (also exposes mark_as_vectorized/unvectorized)
        └── vector_storage.py # ChromaDB storage with chunking + sentence-transformers

frontend/src/
├── api.js                   # All fetch() calls to the backend (single source of truth)
├── App.jsx                  # Root: login gate + tab routing; tabs filtered by runtime capabilities
└── components/
    ├── LoginScreen.jsx      # Account login
    ├── DataTab.jsx          # 知识台账: article list, filters, CRUD; admin-only vector build column + auto-vectorize toggle
    ├── FetchTab.jsx         # 节点管理: fetcher catalog/triggers + node-group management (collector)
    ├── FetchRunsTab.jsx     # 任务与运行: scheduled tasks + fetch-run history (collector)
    ├── SubscriptionTab.jsx  # 订阅分发: source catalog one-click subscribe + aggregated feed token/docs (reader)
    ├── VectorTab.jsx        # 向量雷达: semantic search + RAG context export, hard-scoped for user (reader)
    ├── MCPTab.jsx           # 接入集成: MCP server status + integration snippets (reader; greys out RAG tools when rag_enabled is false)
    ├── SettingsModal.jsx    # Account/runtime settings + admin maintenance actions
    ├── ManualAddModal.jsx   # Manual article entry form
    ├── ArticleDetailModal.jsx
    ├── DateRangePicker.jsx
    └── Toast.jsx
```

### Key Endpoints

**Articles**
- `GET /api/articles` — list/query (filters: `content_type`, `source_id`, `is_vectorized`, `has_content`, `search`, `publish_date_start/end`, `fetched_date_start/end`, `subscribed_scope` = `off`|`only`|`prioritize`, `skip`, `limit`)
- `POST /api/articles` — manual entry
- `PUT /api/articles/{id}` — update (editing `content` or `title` resets `is_vectorized` and purges vector chunks)
- `DELETE /api/articles/{id}` — delete (also purges vector chunks if vectorized)
- `POST /api/articles/batch-delete`

**Feed Delivery** (recommended contract for downstream LLM/RAG consumers)
- `GET /api/feed/articles` — delivery-shaped JSON with `metadata.extensions` parsed; supports `content_types` (CSV), `source_ids` (CSV), `has_content`, `include_content`; `limit` capped at 500
- `GET /api/feed/articles.md` — same filtered records as a Markdown batch; capped at 200 records

**Import Bridge**
- `POST /api/import/social-posts` — ingest external social posts (X/Twitter, etc.) as `social_post` content; idempotent by `source_id + post_id`

**Fetchers & Tasks**
- `GET /api/fetchers` — list all discovered fetchers with parameter schemas
- `POST /api/fetch/{fetcher_id}` — trigger a specific fetcher (also writes `FetchRunRecord` and updates `SourceStateRecord`)
- `GET/POST/DELETE /api/tasks` — cron-scheduled task CRUD (APScheduler)

**Source Configs** (user-defined source definitions, advanced extension surface)
- `GET /api/source-configs` — list all source configs
- `POST /api/source-configs` — create a new source config
- `PUT /api/source-configs/{source_id}` — update a source config
- `POST /api/source-configs/{source_id}/toggle` — enable/disable a source
- `DELETE /api/source-configs/{source_id}` — delete a source config
- `POST /api/source-configs/{source_id}/fetch` — trigger fetch for a specific source config
- `POST /api/source-configs/fetch-active-rss` — trigger all active RSS source configs

**Monitoring & Observability**
- `GET /api/source-health` — per-fetcher health summary (derived from `SourceStateRecord`, falls back to `FetchRunRecord` aggregation); sorted by category then name
- `GET /api/source-states` — raw `SourceStateRecord` rows (filterable by `status`, `fetcher_id`)
- `GET /api/fetch-runs` — paginated fetch run history
- `GET /api/fetch-runs/{run_id}` — single run detail

**Vectorization** — build/manage endpoints are **collector (admin)** gated; `search`/`stats`/`subscribed-stats` are **reader** gated
- `POST /api/vectorize/{article_id}` — vectorize single article (admin)
- `POST /api/vectorize/batch`, `POST /api/vectorize/all-pending` (admin)
- `GET`/`POST /api/vector/auto-vectorize` — read/set the `auto_vectorize` (vectorize-after-fetch) toggle (admin)
- `POST /api/vector/reindex-all` — delete and rebuild entire ChromaDB collection, then re-vectorize all articles (admin)
- `DELETE /api/vector/{article_id}` / `POST /api/vector/batch-delete` — purge chunks, reset `is_vectorized` (admin)
- `POST /api/vector/search` — semantic search; for a `user` account, results are hard-scoped to subscribed sources
- `GET /api/vector/stats` — total chunk count; `GET /api/vector/subscribed-stats` — current user's read-only coverage ratio

**RAG**
- `POST /api/rag/context` — assemble ranked context string for downstream LLM apps (`user` account is subscription-scoped); body: `RagContextQuery` (`query`, `top_k`, `max_chars`, `score_threshold`, `content_type`, `source_id`, `publish_date_gte`, `context_separator`)
- `GET /api/rag/similar/{article_id}` — find semantically similar articles by re-querying with the article's own text

**Reader Subscriptions & Personal Feed** (reader surface)
- `GET /api/reader/sources` — subscribable source catalog (registry ∪ archived ∪ subscribed; enriched name/description/icon; `subscribed` flag)
- `POST`/`DELETE /api/reader/sources/{source_id}/subscribe` — one-click subscribe / unsubscribe (per-user)
- `GET/POST/PUT/DELETE /api/subscriptions` + `POST /api/subscriptions/{id}/rotate-token` — subscription lifecycle (owner-scoped); REST-only advanced/custom path
- `GET /api/reader/feed-token` + `POST /api/reader/feed-token/rotate` — the per-user aggregated feed token (`dfeed_`)
- `GET /api/public/feed/articles[.md]` — token-auth aggregated pull across all the user's subscribed sources (filters: `publish_date_start/end`, `content_types`, `source_ids`, `search`, `include_content`); per-subscription pulls at `GET /api/public/subscriptions/{id}/articles` and `POST .../vector/search`

**MCP** (reader surface)
- `/mcp` — FastMCP streamable-HTTP server (`build_mcp_app`); tools accept an optional `subscription_token` (`dsub_` or `dfeed_`) to scope results to that subscription / the user's whole subscription union

### Tests

Unit tests live directly under `tests/` as `test_*.py` (covering `rss_fetcher`, `webpage_fetcher`, `article_extractor`, `fetcher_curation`, `mcp`, `runtime_role`, `subscriptions`, and `rag_disabled` — `runtime_role`/`subscriptions` exercise the dual-role gating, subscriptions, aggregated feed, and admin/user vectorization split; `rag_disabled` verifies the `vector_sink`-is-`None` path returns 503 / "RAG disabled"). Each file self-bootstraps `sys.path` to `src/` so imports resolve without an editable install. Run with pytest:

```bash
.venv/bin/python -m pytest tests/test_rss_fetcher.py
.venv/bin/python -m pytest tests/                       # whole suite (excludes tests/rag/, which is its own harness)
```

`pyproject.toml` does not configure pytest, so discovery uses pytest defaults; pass an explicit path/`-k` filter when targeting a subset.

### RAG Evaluation

Offline evaluation harness in `tests/rag/`:

```bash
# Run all test cases against the live ChromaDB (requires data/ to be populated)
.venv/bin/python tests/rag/evaluate.py

# Only run cases tagged with a specific capability flag
.venv/bin/python tests/rag/evaluate.py --tag-filter T6

# Preview test cases without running retrieval
.venv/bin/python tests/rag/evaluate.py --dry-run
```

Test sets are versioned JSON files (`testset_v1.json`, etc.) with 25+ cases across categories:  
A (by source), B (by product/tool), C (semantic), D (metadata/empty content), E (temporal), F (cross-lingual).  
Results are saved to `tests/rag/results/eval_<timestamp>.json` (gitignored).

### Configuration (`config/backend.ini`)

Loaded by `src/config.py` into the `settings` singleton (read live in `app.py`; tests monkeypatch it).

- `[runtime] role` — `all` (local all-in-one, default) | `collector` (external collection/archive) | `reader` (intranet distribution). Also overridable via `DORAMI_RUNTIME_ROLE`.
- `[auth] admin_users` / `user_users` — comma-separated `username:password` pairs. `admin` accounts are collector+reader superusers; `user` accounts are reader-only. A username may not appear in both. `[auth] secret` salts the session and subscription/feed token HMACs.
- `[rag] enabled` — `false` (default) | `true`. Master switch for the vector/RAG subsystem; when off no embedding model loads. Overridable via `DORAMI_RAG_ENABLED`. See *RAG is opt-in and lazy-loaded*.

### Environment Variables

| Variable | Purpose |
|---|---|
| `HF_ENDPOINT` | HuggingFace mirror (defaults to `https://hf-mirror.com` in `main.py`) |
| `LOCAL_MODEL_PATH` | Path to local sentence-transformers model for offline embedding; defaults to `BAAI/bge-m3` |
| `DORAMI_RUNTIME_ROLE` | Override `[runtime] role` (`all`/`collector`/`reader`) |
| `DORAMI_RAG_ENABLED` | Override `[rag] enabled` (`1`/`true`/`yes`/`on` to enable the vector/RAG subsystem) |
| `GITHUB_TOKEN` / `GH_TOKEN` | Optional GitHub API token for the GitHub repo fetchers; raises the rate limit (60→5000/hr) for repo listing + README backfill |
