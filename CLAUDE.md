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

#### Database migrations (Alembic)

Schema evolution is versioned via **Alembic** (`alembic/`, config `alembic.ini`). `alembic/env.py` uses `SQLModel.metadata` (import `models.db`) as the autogenerate target and reads the DB URL from `settings.storage.database_url` (unless a URL is injected programmatically — see `src/storage/migrations.py`). SQLite has no native `ALTER`, so `render_as_batch=True`.

- **Runtime bootstrap is still `create_all()`** — `DatabaseStorage.__init__` builds tables from metadata for fresh/in-memory DBs (fast, and what the tests rely on). Alembic is the authoritative mechanism for **evolving existing file DBs** and CI/ops.
- **The invariant that keeps the two in sync**: `create_all()` (== metadata) must always equal `alembic upgrade head`. `tests/test_migrations.py::test_upgrade_head_has_no_drift_from_metadata` enforces this — so **every model change needs a matching migration** (or the drift test fails). Author changes as: edit `models/db.py` → `alembic revision --autogenerate -m "..."` → review.
- **Adopting Alembic on a legacy DB**: `storage.migrations.ensure_migrated(db_url)` handles "has tables but no `alembic_version`" by stamping the baseline (`5ee31a7c5393`) then `upgrade head` (avoids re-running baseline `create_table` on existing tables). `deploy.sh` calls it before starting the backend; run it manually for a dev DB.

```bash
.venv/bin/alembic upgrade head                       # apply migrations to the settings DB
.venv/bin/alembic revision --autogenerate -m "desc"  # generate a migration from model changes
.venv/bin/alembic check                              # report drift between models and the current DB
```

> Note: legacy DBs built by the old hand-written `_ensure_compatible_schema()` `ALTER TABLE ADD COLUMN` path are **missing the `index=True` indexes** those columns declare (raw ALTER never created them) — a real pre-existing drift that a follow-up migration repairs.

### Frontend (React + Vite + Tailwind CSS v4)

```bash
cd frontend
npm install
npm run dev      # Start dev server (port 5173, proxies /api → backend)
npm run build    # Production build
npm run lint     # ESLint
```

**Frontend design & implementation discipline**: before any frontend styling/UI change, follow [`docs/frontend/conventions.md`](docs/frontend/conventions.md) — the durable rules for 文案/accessibility/typography/color tokens/radius/elevation/motion/primary-action/dark-mode. The single source of truth for design tokens and role classes is `frontend/src/index.css` (`:root` tokens + `@layer components`): reuse `--r-*` radii, `--motion-*`, `--dorami-*` color/shadow tokens and the typography role classes (`.body-text`/`.micro-label`/etc.) instead of hand-writing `text-[Npx]`/`rounded-[Npx]`/hex. Work-area surfaces stay restrained; the brand/login cinematic motion is deliberately exempt.

Data is stored in the `data/` directory (SQLite `cms_data.db` and ChromaDB `chroma_db/`).

### Production deploy

`./deploy.sh` is the one-shot production deploy: it installs backend deps into a `venv/`, builds the frontend, copies `frontend/dist/*` into the Nginx html dir, then (re)starts the backend under PM2 via `ecosystem.config.js` (app `dorami-backend-v2`, `interpreter: ./venv/bin/python`, `script: src/main.py`) and reloads Nginx. It requires `config/production.ini` (the deploy points the backend at it via `DORAMI_CONFIG_FILE`); create it from `config/production.example.ini` first. The frontend is served as static assets by Nginx in production, **not** by Vite — only the backend runs as a process.

## Architecture Overview

**DoramiSourceArchive** (哆啦美·归档中枢) is an AI content aggregation CMS with RAG capabilities. It fetches content from multiple sources, stores it in SQLite, and builds a vector index in ChromaDB for semantic search. It splits into two cooperating layers — a **collector/archive** side (fetching, archival, vectorization) and a **reader/distribution** side (per-user subscriptions, semantic search, tokenized feed/MCP delivery) — gated primarily by login account role (`admin` superuser vs restricted `user` reader), with an optional deployment runtime-role axis for split deployments (see *Access control — login account role*).

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

**Optional crawl4ai Web Content backend (`src/fetchers/web_content/`)**: A `WebContentBackend` abstraction (`backend.py`) unifies "given an article URL → clean body". Two impls: `LegacyArticleExtractorBackend` (httpx, the default/baseline) and `Crawl4AIContentBackend` (headless-browser via the optional `crawl4ai` extra — **not** a default dep). The crawl4ai backend renders + scopes by a per-site `CrawlProfile` (`profiles.py`: `target_elements`/`excluded_selector`/`wait_for`), then runs the project's own `node_to_markdown` over `cleaned_html` (consistent image/lazy-load handling between both paths); it also exposes `render_html()` (raw rendered DOM, anchors intact — used by C-class single-page-split fetchers' segmenter fallback and OpenAI's Cloudflare path) and `extract(url, profile=...)` (explicit profile injection). It's strictly opt-in: a fetcher sets `web_backend_enabled=True`, `BaseFetcher.fetch()` lazily starts/stops the browser, and `_web_backend_detail()` routes detail extraction through it — **falling back to legacy httpx whenever crawl4ai is absent / no profile matches / extraction fails**. So the default environment (no crawl4ai installed) behaves exactly as before. Migrated B-class detail nodes (`web_anthropic_news`, `web_ithome_ai`, `web_qbitai`, `web_claude_blog`, `web_aiera`) and `rss_openai_news` (crawl4ai-first CF bypass, Playwright fallback, summary last) use it.

**Config-driven web fetcher (`generic_web`, the "中级目标")**: `ConfigurableWebFetcher` (`configurable_web_fetcher.py`) is the single, config-driven web fetcher — the `GenericRssFetcher` analogue for web sources. Adding a new website = writing a `SourceConfigRecord` (config), not a new Python subclass. It reads all source identity/config from runtime params (`listing_url`, `article_url_patterns`, detail `CrawlProfile`, optional `listing_css` CSS schema, governance metadata) and delegates discovery to `BaseWebPageListFetcher`'s heuristics (anchor + embedded-JSON), with the optional CSS schema as a precise fallback. `resolve_source_fetcher_id` routes `source_type` web/webpage → `generic_web` (rss/atom → `generic_rss`); `POST /api/source-configs/fetch-active-web` batch-triggers active web sources. Validated to reproduce existing dedicated nodes (discovery URL-set identical, detail byte-identical when given the same profile). **Frontend entry is currently gated off** (`App.jsx` filters `generic_web` from the node catalog) — backend-only for now.

**AI node onboarding (`source_builder`, the "高级目标")**: `src/services/source_builder.py` turns an arbitrary listing-page URL into a固化 config node: `analyze_url()` detects page type (rss/web/json), collects HTML structural signals, produces a heuristic baseline config, then (when LLM is configured via `daily_brief.resolve_llm_config`) refines it via LLM and analyzes a sample article page to propose the detail `CrawlProfile`; `preview_config()` trial-runs `generic_web`/`generic_rss` for a no-persist sample. Endpoints `POST /api/source-builder/analyze|preview` (collector-gated); save reuses `POST /api/source-configs`. LLM/crawl4ai are both optional (graceful degrade to heuristic/legacy). **Frontend entry (`CustomNodeBuilder.jsx` + the FetchTab "AI 自定义节点" panel) is currently gated off** via `ENABLE_CUSTOM_NODE_BUILDER=false` — backend-only for now.

**`extensions_json` serialization pattern**: `serialize_to_metadata()` splits a content object's fields into base fields (from `BaseContent`) and subclass-specific extension fields. The extensions are serialised as a JSON string into the `ArticleRecord.extensions_json` column. When reconstructing for vectorization, a `GenericContent` object is used since the ORM only stores the flat record.

**Playwright browser-rendered detail (Cloudflare bypass)**: Most fetchers are pure httpx, but a few sources gate their article bodies behind a Cloudflare Managed Challenge that only a real browser can pass (httpx gets a 403 challenge shell). `src/fetchers/impl/playwright_renderer.py` provides `PlaywrightRenderer`, an async context manager that lazily launches a headless Chromium for the duration of one fetch run, then renders each blocked article: it opens a fresh page per article, throttles requests, polls until the challenge clears and the body text appears, retries, and returns `""` on any failure so the caller degrades gracefully. Currently only `OpenAINewsRssFetcher` uses it — it overrides `_detail_for_url` to prefer the rendered body and fall back to the RSS summary when rendering fails (`openai.com` is the one audited source behind this challenge). Playwright is an opt-in path: when a node needs no detail fetch, no browser is started. (Note: the legacy WeChat Official Account Playwright login fetcher has been removed; only the `WechatArticleContent` type and the `wechat_article` display label remain for historical archived data.)

**Vector chunking & cleaning**: Text is cleaned via `clean_text()` (HTML stripping, HN boilerplate removal, arxiv prefix removal) then split into paragraph-aware 800-char chunks with 150-char overlap. Articles with `< 30` usable characters are indexed with a header-only chunk. Metadata headers (source name, date, title) are prepended to every chunk to support temporal and source queries. Each chunk is an independent ChromaDB document linked by `parent_id` metadata. Semantic search fetches `top_k * 4` raw chunks then deduplicates by `parent_id`.

**Embedding model**: Default is `BAAI/bge-m3` (multilingual, supports Chinese queries against English documents). Override with `LOCAL_MODEL_PATH`. Changing models requires `POST /api/vector/reindex-all` to rebuild the collection from scratch.

**RAG is opt-in and lazy-loaded**: The entire vector/RAG subsystem is gated by `[rag] enabled` (default `false`, override `DORAMI_RAG_ENABLED`). When off, `vector_sink` is `None` and no embedding-model weights ever load, keeping startup fast and the server runnable on low-memory hosts. Even when enabled, `ChromaVectorStorage` defers chromadb client / embedding-fn / collection creation to first use via `_ensure_collection()` (mirroring the lazy `_ensure_reranker()` cross-encoder). All `/api/vector*`, `/api/vectorize*`, `/api/rag*`, and the auto-vectorize toggle go through `require_vector_sink()` (503 when disabled); article CRUD skips vector purge when off; MCP semantic-search tools return a structured "RAG disabled" result instead of failing. `rag_enabled` is exposed in `GET /api/runtime`, and the frontend hides 向量雷达, the vector-build column/toggles, and greys out RAG MCP tools when off.

**Fetch run tracking**: Every fetcher execution (manual or scheduled) writes a `FetchRunRecord` and upserts a `SourceStateRecord`. The state record is the authoritative health/cursor store per source; `build_fetcher_health_from_state()` in `app.py` derives the `/api/source-health` response from it, falling back to aggregating raw `FetchRunRecord` rows when no state exists.

**Accounts are database-managed**: Login accounts live in the `users` ORM table (`UserRecord`), passwords stored as PBKDF2-HMAC-SHA256 hashes. `src/services/accounts.py` centralizes hashing/verify, user CRUD, the **sole-built-in-admin guard**, and `seed_users_if_empty`. `[auth] admin_users`/`user_users` in the ini are **first-boot seeds only** (seeded when the `users` table is empty); afterwards accounts are runtime-managed and editing the ini no longer affects existing accounts. **Admin is the system's single built-in account** — the only admin path is the first-boot seed: `create_user` rejects `role=admin`, `set_role` rejects both promoting a reader to admin and changing an admin's role, and an admin account can't be disabled or deleted. So runtime account management only ever creates/manages **readers**; everyone who needs to administer logs in through the one shared `admin`. Admin manages reader accounts under `/api/accounts` (list/create/update active/per-account AI toggle/reset-password/delete — admin-only via `account_admin_required()`, independent of the runtime axis); the admin account itself is **not listed** in `/api/admin/accounts` (readers only). Any logged-in user self-changes password via `POST /api/auth/change-password`. `login_admin` and `read_auth_token` validate against the DB (account must exist, be `is_active`, role must match the token), so disabling/deleting/role-changing a user revokes their existing cookie on the next request. `username` is the immutable identity (it keys `owner_username` on subscriptions/feed tokens — no rename; deleting a user cascades their subscriptions + feed token). The admin account-management UI lives in the **运维管理 Tab** (`AdminOpsTab`, see *Admin Ops console*); `SettingsModal` keeps only the self password-change available to every account.

**Access control — login account role (primary axis)**: In the default single-node `all` deployment the only axis that matters is the **login account role** (`admin` | `user`, stored per-account in the `users` table). **`admin` is a superuser** — collector surfaces (节点管理/任务运行, article CRUD, vectorization build/manage) plus every reader surface; **a `user` is a restricted reader** — reader surfaces only (subscription delivery, semantic search, MCP/接入集成, surfaced as the 阅读器 + 接入集成 tabs), open to any logged-in account except archive import (admin-only, it mutates the whole archive). `disabled_runtime_surface()` enforces this per request via `COLLECTOR_API_PREFIXES` / `READER_API_PREFIXES` (reader-prefix matches short-circuit, so `/api/vector/*` can split: `search`/`stats`/`subscribed-stats` → reader, everything else → collector). The frontend mirrors it through `runtime_capabilities()` → `collector_enabled` / `reader_enabled` / `account_role` per session.

> **Optional second axis — deployment runtime role** (`[runtime] role`, default `all`): only relevant for a *split deployment* where collection and distribution live on separate hosts (`collector` = collect/archive only, external network; `reader` = distribution only, intranet; they exchange data via *Archive Sync*). It ANDs with the account role (`collector_enabled = runtime allows collector AND account is admin`). For the default `all` single-node setup this axis is inert and `collector_enabled`/`reader_enabled` reduce to the account role above — you can ignore it unless you actually split the deployment.

**Reader subscription & distribution layer**: Reader accounts build a personalized subscription scope over already-archived records (it never triggers fetching). One-click subscribe (`POST`/`DELETE /api/reader/sources/{source_id}/subscribe`) creates/removes a per-user, single-source `ReaderSubscriptionRecord` (owned via `owner_username`). "我订阅" = the union of `source_id`s across a user's active subscriptions; for a `user` account it hard-scopes that user's vector/RAG/MCP retrieval and is the scope of the 阅读器 (the user's primary surface — its 我的订阅 view aggregates subscribed sources via `GET /api/articles?subscribed_scope=only`). Downstream consumers pull via tokens (HMAC-SHA256, stored only as hashes): a per-subscription token (`dsub_`) or the per-user **aggregated feed token** (`dfeed_`, one row per user in `ReaderFeedTokenRecord`) used at `GET /api/public/feed/articles[.md]` — a single endpoint covering all the user's subscribed sources with publish-time/source/type filters. Full contract in `docs/contracts/reader_subscription.md`.

**Daily Brief (每日 AI 资讯日报) — LLM map-reduce over the archive**: `src/services/daily_brief.py` orchestrates a scheduled/manual digest of already-archived articles: `collect_candidates` → `map_summarize` (per-article LLM summarize+score, `map_concurrency` in parallel) → `dedup_clusters` (one LLM call clusters same-event items; keeps the highest-scored representative, folds the rest's links into its `extra_sources`; degrades to no-op on LLM failure) → `select_top` (score + source/realm diversity + a `paper_cap` that limits 学术论文 share) → `reduce_to_markdown` (single LLM rollup; sections ordered 🚀 模型发布 first … 📄 学术论文 last, within-section by importance; output token cap raised to ≥8192 to avoid mid-report truncation) → idempotent write of a `daily_brief` content record (`source_id=dorami_daily_brief`, `content_type=daily_brief`). The map stage scores against a reader-interest profile (model/capability releases > AI apps/products > big-tech & industry news > novel research), penalizes 营销稿/软广 and downweights 车载/智能座舱. **Three-layer dedup**: ① a deterministic high-water cursor `daily_brief_cursor` (over `fetched_date`) that only advances after a successful write; ② the `dedup_clusters` stage collapses same-day same-event items before select; ③ the reduce step injects recent briefs' bodies so the LLM also collapses cross-day repeats semantically. (Export to the shendeng platform — `scripts/export_shendeng_daily_news.py` — collapses the rich `classification` back to shendeng's two categories: 学术论文 stays, everything else → 产业资讯.) All daily-brief run state/config (cron, top-N, last run, cursor, LLM overrides) lives in `AppSettingRecord` KV — **no new ORM table**. The scheduler registers exactly one APScheduler job `daily_brief`; `reload_daily_brief_schedule()` hot-adds/removes it on config change. **Manual generation (`POST /api/daily-brief/generate`) is a persistent background job** (阶段3): it validates `top_n` synchronously, then submits a `daily_brief`-type job via `services.jobs.launch` and returns `{status: "accepted", job_id}` immediately (no more minutes-long request); the frontend `generateDailyBrief` polls `GET /api/jobs/{job_id}` (shared `pollJob`) for the terminal result. The **scheduled** run (`execute_daily_brief_job`) still awaits `generate_daily_brief` directly on the scheduler thread (not an HTTP request; already tracked by its own run record). Fine-grained stage progress remains in-memory (`get_progress()`, polled by `GET /api/daily-brief/progress`, driving the DailyBriefFlow animation) — migrating that to DB is a later 阶段3 step. Daily-brief endpoints are **collector(admin)-gated** but the panel (`DailyBriefPanel`) is surfaced inside 接入集成 (`MCPTab`).

**LLM client (OpenAI-compatible)**: `src/llm/client.py` is a thin httpx wrapper over `{base_url}/chat/completions` covering OpenAI/DeepSeek/Kimi/智谱/通义/火山方舟/OpenRouter/Ollama/vLLM. `chat_completion()` does async completion + exponential-backoff retry + optional JSON mode; `parse_json_object()` robustly extracts a pure JSON object from model output (strips code fences, slices outer braces); `ping()` tests connectivity. It takes an `LLMConfig` and **never logs the api_key**. Config comes from `[llm]` in the ini (or `DORAMI_LLM_*` env), and can be overridden at runtime via admin settings (persisted to `AppSettingRecord` KV). Prompts live in `src/llm/prompts.py`. `LLMConfig.configured` is true only when `base_url`+`api_key`+`model` are all set.

**Collection Jobs are the single collection-scheduling entity**: A `CollectionJobRecord` is a savable, schedulable collection job that bundles multiple fetcher nodes (`fetcher_ids_json`), shared + per-node params, **one job-level cron** (per-node cron overrides retired 2026-07 by migration `d41acead77b0` — a job is "one set of nodes + one schedule"; want a different cadence, create another job; legacy overrides were faithfully split into separate jobs), and a `downstream_policy_json`. Running one writes a job-level `CollectionJobRunRecord` (`run_scope` = `ad_hoc`/`saved_job`/`legacy_task`) that **aggregates** the per-node `FetchRunRecord` rows it spawned (counts, child run IDs, partial-failure status). **Node groups (`NodeGroupRecord`, "采集范围") and legacy fetch tasks (`FetchTaskRecord`, `/api/tasks`) are fully retired** (实体简化, see `docs/analysis/entity-simplification-plan.md`): 阶段 1 removed the UI, 阶段 2 removed the tables/endpoints — the Alembic migration `8f6d93196258` inlines referenced groups into their jobs (faithful param-merge precedence), converts standalone/cron-bearing groups and legacy tasks into collection jobs (provenance via `legacy_task_id`), then drops `node_groups`/`fetch_tasks` and `collection_jobs.group_id`. Historical run/article rows keep their `task_id`/`group_id`/`source_group_id` columns for traceability (`run_scope=legacy_task` still renders in run history). (`pipeline/progress.py` exposes in-memory per-`fetcher_id` `{current,total}` counts surfaced by `GET /api/fetch-runs/running-progress`.)

**Archive Sync (collector → reader)**: A collector runtime (external network) exports faithful archive records as JSON Lines (`application/x-ndjson`) via `GET /api/archive/export/articles.jsonl`; an intranet reader runtime imports them via `POST /api/archive/import/articles.jsonl` **without performing any public fetch**. Import is admin-only (it mutates the whole archive). Full contract in `docs/contracts/archive_sync.md`.

**Downloadable Claude skill**: `src/api/skill_router.py` zips `src/skill_templates/dorami-daily-brief/` on the fly (templating `{BASE_URL}` into the live host) and serves it at `GET /api/skill/daily-brief`, so a user can install a ready-made Claude skill that talks to this deployment's feed/MCP endpoints.

**Vectorization is admin-managed**: The ChromaDB collection is shared/global, so building it is a collector/admin concern (one user vectorizing a source's article would affect every subscriber of that source). `user` accounts cannot trigger or select vectorization — they only consume via hard-scoped retrieval and a read-only coverage ratio (`GET /api/vector/subscribed-stats`). Admin manages it from 知识台账: per-article / batch / `all-pending` build, `reindex-all`, and an `auto_vectorize` toggle (`GET`/`POST /api/vector/auto-vectorize`, persisted in `AppSettingRecord`). The `admin` superuser's own retrieval is **not** subscription-scoped (it searches the whole archive); only the restricted `user` role is scoped.

**Persistent background jobs (阶段3)**: Long admin tasks (全量向量化 `vectorize/all-pending`, 全量重索引 `reindex-all`) submit-and-return a `job_id`; the frontend polls `GET /api/jobs/{job_id}`. `src/services/jobs.py` is the **DB-backed** state machine (`JobRecord` table, statuses `queued/running/succeeded/failed/cancelled`) that replaced the process-in-memory `background_jobs` — so job state/progress/result survive a restart and are cross-process queryable (foundation for the scheduler↔worker split). `launch(engine, type, work, *, created_by, payload)` spawns an `asyncio` task running `work(job)`; the `Job` handle's `set_total`/`advance` persist progress **throttled** (every `_FLUSH_EVERY=25` steps or `_FLUSH_INTERVAL=1s`) so per-item loops don't hammer the DB, while status transitions and `set_total` flush immediately. `get_job`/`list_jobs` read back a dict whose shape matches the old `to_dict` (epoch-float timestamps) so the poll contract is unchanged. (Live per-fetcher fetch progress still uses the in-memory `pipeline/progress.py`; migrating it to DB is a later 阶段3 step.)

**Vector index status (`index_status` enum)**: `ArticleRecord` carries both the legacy boolean `is_vectorized` **and** a richer `index_status` enum (`pending`/`indexing`/`indexed`/`failed`/`stale`, constants in `models/db.py`). `is_vectorized` is kept as a **backward-compatible derived bit** (`== "indexed"`) — the `?is_vectorized=` filter and every existing reader still work unchanged. The storage layer keeps the two in sync: `mark_as_vectorized`→`indexed`(+True), `mark_as_unvectorized`→`pending`(+False), and `set_index_status(id, status)` sets any state and syncs the bit (`True` only for `indexed`). Writers: new records default `pending`; `save()`'s body-backfill and a content/title edit (`PUT /api/articles`) → `stale`; the single-article vectorize endpoint sets `indexing` then `indexed`/`failed`; reconciliation's `flagged_but_absent` repair → `stale`. `stale`/`pending`/`failed` all have `is_vectorized=False` so `all-pending` still re-indexes them. Exposed in article payloads (`index_status`) and filterable via `GET /api/articles?index_status=`. Backfill migration (`8bba6f81b240`) sets existing `is_vectorized=1` rows → `indexed`. (Batch/all-pending flows leave `save()==False` as a skip — no `failed` mislabel — since that bool can't distinguish skip from failure without changing `vs.save`'s contract.)

**SQLite↔Chroma reconciliation**: The two stores stay consistent only by the "write SQLite → write Chroma → set `is_vectorized`" call ordering (no transaction, no cross-store audit), so any interrupted step or legacy leftover drifts them. `src/services/vector_reconcile.py` `reconcile(db_sink, vector_sink, repair=False)` aligns both sides' belief of "what's vectorized" (SQLite `is_vectorized` flags vs. distinct `parent_id`s actually present in Chroma via `VectorStorage.list_parent_ids()`) and classifies three drift buckets: **`flagged_but_absent`** (DB says indexed, no chunks → repair marks `index_status=stale`, `is_vectorized` False, so `all-pending` re-indexes), **`present_but_unflagged`** (chunks exist but flag is False → repair adopts, sets `indexed`), **`orphan_chunks`** (chunks whose article no longer exists in SQLite → repair purges). Admin-only via `GET`/`POST /api/vector/reconcile` (GET = dry-run report, POST = repair); needs `vector_sink` (503 when RAG off). Backend-only (no frontend entry yet). A **daily scheduled patrol** (`execute_vector_reconcile_job`, registered at 04:00 when the scheduler starts fresh and RAG is on) runs the reconcile **report-only** and logs a warning on drift (never auto-repairs — repair stays an explicit admin action).

**Reader-facing AI Beta (用户面翻译 + 问答助手)**: `src/services/reader_ai.py` gives the 阅读器 two LLM features over the **same global `resolve_llm_config()`** the Daily Brief uses: `translate_article()` (full-body → 简体中文, paragraph-split + concurrent, cached under `extensions_json.translation_zh` so it never re-translates nor resets `is_vectorized`) and `answer_question()` (multi-turn QA, context assembled by the API layer in three graceful-degrade tiers: current-article body → RAG semantic recall when `[rag] enabled` → recent subscribed articles). Endpoints `POST /api/reader/ai/translate|ask` are gated by `_require_reader_ai()`, which checks **global master switch AND per-account flag AND LLM configured** (else 403). Access is **two-layer**: a per-account `UserRecord.ai_beta_enabled` flag ⊕ a global master switch `ai_beta_global_enabled` (`AppSettingRecord` KV, default on) — the master switch is a kill/gray-out 总闸 that instantly suspends everyone without touching per-account flags; `_ai_capabilities()` ANDs the two into the `runtime.ai_beta_enabled` the frontend reads (so the 阅读器 entry auto-hides when either is off). Prompts (translate/QA, 哆啦美 persona) live in `src/llm/prompts.py`.

**Admin Ops console (运维管理 Tab)**: An **admin-only** top-level Tab (`AdminOpsTab.jsx`, gated by `account_role === 'admin'`) is the operational hub, laid out as a **Grafana-style monitoring 看板** (charts via `recharts`, themed through dorami tokens in `components/charts/DashboardCharts.jsx` + `chartUtils.js`; numbers hidden until hover-tooltip). It is split by a `.segmented-control` into three sub-pages (`sub` state: `ai` | `user` | `content`): **① AI** — the AI Beta master switch as a compact status-light + toggle in the panel header (`/api/admin/ai-beta/global`), the **global model config** (the LLM editor moved here from the Daily Brief panel since the model is shared infra; that panel now shows a read-only model-status chip), and an **AI usage dashboard** charted from `/api/admin/ai-usage` — a time-range dropdown (近 7/14/30/90 天) drives two daily multi-series **area charts** (每日调用次数 / 每日 tokens), each with an in-header 按用途/按用户 segmented toggle that re-pivots the series from `summarize.by_day_purpose` / `by_day_user`; series use a categorical palette (not just the brand token) for distinguishability; **② 用户** — a **windowed** reader-OPS board (migrated out of `SettingsModal`): a 近 7/14/30/90 天 time-range dropdown drives the whole sub-page (default 30) — top KPI tiles (读者数 / 窗口内登录过 / 窗口阅读 / 窗口 AI 调用) + an 活跃用户 Top rank bar with an **阅读 / 登录 segmented toggle** (re-ranks by the chosen activity dimension), then a list where **each row shows window metrics, not lifetime counters** (最近登录 + 窗口登录次数 + 窗口阅读次数 + 窗口 AI 调用 + 订阅数). The window fields (`reads` from `reader_activity.reads_by_user`, `logins` from `accounts.logins_by_user`, `ai_calls`/`ai_tokens` from `ai_usage.usage_by_user`, `logged_in_window` derived from `last_login_at`) come from `GET /api/admin/accounts?days=`. **Clicking a row opens a read-only per-user activity drawer** (`GET /api/admin/accounts/{username}/activity?days=`, powered by `ai_usage.summarize_user` + `reader_activity.summarize_user_reads` + `accounts.summarize_user_logins` + a per-user favorites-by-source join): an **expandable 登录 card** (count + click-to-reveal 最近 N 次登录时间 list) alongside 阅读/AI/订阅 snapshot tiles + a 各源 · 阅读/收藏 grouped-bar chart (`source_engagement` = reads ∪ favorites per source) + a 每日 AI 用量 stacked-area chart (调用/tokens toggle, purpose-stacked). Row-level management (create-reader/active/per-account AI toggle/reset-password/delete) stays inline (`stopPropagation` so it doesn't open the drawer) — no role-promotion UI, since admin is the sole built-in account and isn't listed. Windowed richness derives from `AiUsageRecord` + `ReaderReadRecord` + `LoginEventRecord`. (`ChartPanel` flex-centers its chart so a short rank chart sits vertically centered when a taller sibling stretches the row; `RankBars` takes an optional `bars` prop for grouped multi-series.) **③ 内容** — a charted content board (`/api/admin/content` → 各源收藏/订阅 rank bars + 收藏文章榜). (The old overview KPI stat board was removed as low-signal; `/api/admin/overview` still exists but the UI no longer calls it.) New admin endpoints live under the `/api/admin/*` prefix (`overview`, `accounts`, `accounts/{username}/activity`, `ai-usage`, `content`, `ai-beta/global`), all forced to admin via `account_admin_required()` (which matches `/api/accounts` **and** `/api/admin`). The shared recharts wrapper `MultiSeriesArea` takes a `dims` prop so the same stacked-area component serves both the AI-usage 按用途/按用户 toggle and the per-user 调用/tokens toggle.

**AI usage metering (token tracking)**: `src/llm/client.py` keeps `chat_completion()` returning a `str` but optionally surfaces the response `usage` (prompt/completion/total tokens): pass a `UsageMeta(purpose, username)` and the client hands token usage to a registered recorder callback (`set_usage_recorder`, wired in `app.py` to write the DB) — **metering never blocks the main flow** (recorder exceptions are swallowed; `ping()` passes no meta so connectivity tests aren't counted). `src/services/ai_usage.py` aggregates one row per `(day × username × purpose × model)` into the `AiUsageRecord` table (`record_usage` upserts/accumulates; `summarize` powers `GET /api/admin/ai-usage`). Purposes: `translate`/`ask` (attributed to the logged-in reader), `daily_brief_map|dedup|reduce` (attributed to the **admin who manually triggered** the run via `generate_daily_brief(triggered_by=...)`, else `"system"` for scheduled runs), `source_config`/`detail_profile`. Lightweight per-account counters (`UserRecord.ai_translate_count`/`ai_ask_count`/`last_login_at`) remain a separate cheap snapshot for the account row; `AiUsageRecord` is the token dashboard's source of truth.

**Reading metering (阅读计量)**: parallel to AI metering but for plain reads — `src/services/reader_activity.py` aggregates one row per `(day × username × source_id)` into `ReaderReadRecord` (`record_read` upserts/accumulates). A read is recorded when a reader **deliberately opens an article** in the 阅读器: `POST /api/reader/articles/{id}/read` (reader-gated, fire-and-forget, dedup'd frontend-side against consecutive re-clicks, silently ignores missing articles) resolves the article's `source_id` and bumps the aggregate. To keep the signal clean the reader **no longer auto-opens a default article** (auto-opens would be passive, not deliberate). `reads_by_user` (windowed per-user totals) feeds the admin account list / 活跃用户 Top; `summarize_user_reads` (total + by_source + by_day) feeds the per-user activity drawer's 各源阅读/收藏 board.

**Login metering (登录计量)**: `accounts.touch_login` writes a `LoginEventRecord` (raw event with precise timestamp) on every successful login **in addition to** updating the `UserRecord.last_login_at` snapshot — the event stream backs windowed login counts and the "最近若干次登录时间" list (logins are low-frequency, so raw retention is fine). `accounts.logins_by_user` (windowed per-user count) enriches the account list / 活跃用户 Top's 登录 dimension; `accounts.summarize_user_logins` (count + by_day + recent N timestamps) feeds the per-user drawer's expandable 最近登录 list.

### Project Structure

```
src/
├── main.py                  # Entry point: starts uvicorn with reload=True
├── config.py                # load_config() → settings singleton; reads DORAMI_CONFIG_FILE (else config/backend.ini)
├── api/
│   ├── app.py               # FastAPI app — all REST endpoints + APScheduler init
│   └── skill_router.py      # GET /api/skill/daily-brief: zips src/skill_templates/dorami-daily-brief on the fly
├── llm/
│   ├── client.py            # OpenAI-compatible chat_completion + parse_json_object + ping (httpx; never logs api_key); optional UsageMeta + set_usage_recorder surface token usage without changing the str return
│   └── prompts.py           # Daily-brief map/reduce + reader-AI translate/QA (哆啦美 persona) prompt templates
├── services/
│   ├── daily_brief.py       # Daily-brief map-reduce orchestration + same-event dedup_clusters + paper_cap + cursor dedup + in-memory progress; usage attributed to triggered_by (manual admin) else "system"
│   ├── reader_ai.py         # 用户面 AI Beta: translate_article (cached zh translation) + answer_question (multi-turn QA, 3-tier context); shares resolve_llm_config
│   ├── accounts.py          # Account CRUD + PBKDF2 + sole-built-in-admin guard (no admin create/promote; admin immutable) + seed; login/AI-usage 埋点 (touch_login writes LoginEventRecord + last_login_at; record_ai_usage) + login aggregation (logins_by_user/summarize_user_logins) + AI Beta global master switch
│   ├── ai_usage.py          # AI token metering: record_usage upserts AiUsageRecord per (day×user×purpose×model); summarize / usage_by_user / summarize_user → admin dashboards
│   ├── reader_activity.py   # 阅读计量: record_read upserts ReaderReadRecord per (day×user×source_id); reads_by_user / summarize_user_reads → admin user-OPS board
│   └── source_builder.py    # AI node onboarding: URL → detect type + signals + (LLM) config + detail-profile → preview (frontend gated off; backend only)
├── models/
│   ├── content.py           # Dataclass content models (BaseContent + subtypes)
│   └── db.py                # SQLModel ORM tables: ArticleRecord,
│                            #   FetchRunRecord, SourceStateRecord, SourceConfigRecord,
│                            #   CollectionJobRecord, CollectionJobRunRecord,
│                            #   ReaderSubscriptionRecord, ReaderFeedTokenRecord, AppSettingRecord,
│                            #   UserRecord (+ai_beta_enabled/last_login_at/ai_*_count 埋点列), AiUsageRecord, ReaderReadRecord, LoginEventRecord,
│                            #   JobRecord (阶段3 持久化后台任务状态机)
├── fetchers/
│   ├── base.py              # BaseFetcher: httpx client, retries, template method
│   ├── registry.py          # FetcherRegistry singleton — auto-discovers impl/ on import
│   └── impl/
│       ├── rss_fetcher.py               # GenericRssFetcher + PresetRssFetcher (23+ built-in RSS sources); OpenAINewsRssFetcher renders detail via Playwright (CF bypass); HackerNewsAiRssFetcher applies a configurable min_points/min_comments hnrss threshold to de-noise the q=AI firehose and is treated as a discovery source (external-link posts degrade to title+URL+discussion+heat with no body; only Ask/Show/Tell self-posts keep a body; external detail fetch off by default)
│       ├── github_release_fetcher.py    # GenericGitHubReleasesFetcher + preset subclasses (13 built-in)
│       ├── repository_model_fetcher.py  # GitHub repo + HuggingFace model fetchers (content_type=github_repository / huggingface_model); GitHub repo fetcher backfills a cleaned README excerpt when a repo has no description (dedup-gated, GITHUB_TOKEN-aware)
│       ├── webpage_fetcher.py           # BaseWebPageListFetcher + preset subclasses (6 built-in)
│       ├── curated_core_fetcher.py      # Curated AI-source presets: SinglePageDocumentFetcher (changelogs/release notes) + per-site BaseWebPageListFetcher/BaseFetcher subclasses (量子位, 新智元, HF Daily Papers, etc.)
│       ├── configurable_web_fetcher.py  # ConfigurableWebFetcher (generic_web): single config-driven web fetcher; params carry listing/patterns/detail-profile/listing_css (frontend gated off; backend only)
│       ├── article_extractor.py         # Shared HTML→article-body extractor (helper module, not a fetcher); used by webpage/rss fetchers to backfill detail
│       ├── playwright_renderer.py       # PlaywrightRenderer: headless-Chromium detail rendering for Cloudflare-challenged sources (used by OpenAINewsRssFetcher)
│       └── webhook_trigger.py           # Outbound Dify workflow trigger (not an inbound content source)
├── fetchers/web_content/    # Optional crawl4ai Web Content backend: backend.py (WebContentBackend ABC + DetailResult), legacy_backend.py (httpx baseline), crawl4ai_backend.py (browser, opt-in extra), profiles.py (per-site CrawlProfile), compare.py (bypass A/B)
├── mcp_server.py            # build_mcp_app(): FastMCP streamable-HTTP server, mounted at /mcp by app.py
├── pipeline/
│   ├── core.py              # DataPipeline: drives fetcher → broadcasts to registered storages
│   └── progress.py          # In-memory per-fetcher {current,total} run progress (polled by frontend)
└── storage/
    ├── base.py              # BaseStorage abstract class
    └── impl/
        ├── db_storage.py    # SQLite storage (also exposes mark_as_vectorized/unvectorized)
        └── vector_storage.py # ChromaDB storage with chunking + sentence-transformers

frontend/src/
├── api.js                   # All fetch() calls to the backend (single source of truth)
├── App.jsx                  # Root: login gate + tab routing; tabs filtered by runtime capabilities AND account_role (a `user` sees only 阅读器/接入集成; admin keeps the full collector+reader tab set + the admin-only 运维管理 tab)
└── components/
    ├── LoginScreen.jsx      # Account login
    ├── ReaderTab.jsx        # 阅读器: the user-only three-pane reader (subscribed-source list → article list → reading pane); left sidebar manages subscriptions; keyword search via GET /api/articles; **no default article auto-open** — the reading pane stays on a 「选择一篇文章」 prompt until the user deliberately clicks one (which fire-and-forget records a read via POST /api/reader/articles/{id}/read, dedup'd against consecutive re-clicks); AI Beta entries (译为中文 toggle + 哆啦美 QA 浮层) shown when runtime ai_beta_enabled && llm_configured — the 基于本文 QA scope is greyed/disabled (auto-falls back to 基于我的订阅) when no article is selected
    ├── AdminOpsTab.jsx     # 运维管理 (admin-only): Grafana 式看板 — KPI + AI Beta 总闸 & global model config + AI usage charts (recharts, time-range dropdown) + content board charts + reader-account management; calls /api/admin/* + /api/llm/config
    │   └── charts/         # DashboardCharts.jsx (token 化 recharts 包装件 + ThemedTooltip) + chartUtils.js (配色常量 + fillDailySeries 零填充)
    ├── DataTab.jsx          # 知识台账: article list, filters, CRUD; admin-facing (hidden for `user`); admin-only vector build column + auto-vectorize toggle
    ├── FetchTab.jsx         # 节点管理: fetcher catalog/triggers (collector); node-group (采集范围) management removed in 实体简化阶段 1
    ├── FetchRunsTab.jsx     # 任务与运行: scheduled tasks + fetch-run history (collector)
    ├── VectorTab.jsx        # 向量雷达: semantic search + RAG context export (reader surface, but admin-facing — hidden for `user`, who searches via the 阅读器)
    ├── MCPTab.jsx           # 接入集成: MCP server status + integration snippets + 个人聚合接口 (the dfeed_ feed token, via FeedAccessSection) (reader; greys out RAG tools when rag_enabled is false)
    ├── FeedAccessSection.jsx # 个人聚合接口 block embedded in 接入集成: aggregated feed endpoint + dfeed_ token get/rotate + curl docs
    ├── DailyBriefPanel.jsx   # 每日 AI 资讯日报: config + manual generate + run history; embedded in 接入集成 (admin-managed). Model config moved to 运维管理 — shows a read-only model-status chip only
    ├── DailyBriefFlow.jsx    # Animated map-reduce stage visualization for the daily-brief generation progress
    ├── SettingsModal.jsx    # Account/runtime settings + admin maintenance actions
    ├── ManualAddModal.jsx   # Manual article entry form
    ├── ArticleDetailModal.jsx
    ├── DateRangePicker.jsx
    └── Toast.jsx
```

**User layer is a reader, not a console**: A `user` (restricted reader) account logs into a single 阅读器 (`ReaderTab`) plus 接入集成 (`MCPTab`) — the standalone 订阅分发 tab was removed. The reader is scoped to the user's subscriptions: the default 我的订阅 view aggregates all subscribed sources via `GET /api/articles?subscribed_scope=only`, and the left sidebar is the subscription manager (star to unsubscribe, 发现更多来源 to one-click subscribe). So **subscription = the user's reading list (what the reader shows) + the downstream feed/MCP delivery scope**. The aggregated feed token (`dfeed_`) lives in 接入集成. Admin is unaffected (no reader; keeps 知识台账/向量雷达/etc.); the `/api/subscriptions` REST lifecycle remains as an advanced/automation path with no dedicated UI.

### Key Endpoints

**Articles**
- `GET /api/articles` — list/query (filters: `content_type`, `source_id`, `is_vectorized`, `index_status` (pending/indexing/indexed/failed/stale), `has_content`, `search`, `publish_date_start/end`, `fetched_date_start/end`, `subscribed_scope` = `off`|`only`|`prioritize`, `skip`, `limit`)
- `POST /api/articles` — manual entry
- `PUT /api/articles/{id}` — update (editing `content` or `title` resets `is_vectorized` and purges vector chunks)
- `DELETE /api/articles/{id}` — delete (also purges vector chunks if vectorized)
- `POST /api/articles/batch-delete`

**Feed Delivery** (recommended contract for downstream LLM/RAG consumers)
- `GET /api/feed/articles` — delivery-shaped JSON with `metadata.extensions` parsed; supports `content_types` (CSV), `source_ids` (CSV), `has_content`, `include_content`; `limit` capped at 500
- `GET /api/feed/articles.md` — same filtered records as a Markdown batch; capped at 200 records

**Import Bridge**
- `POST /api/import/social-posts` — ingest external social posts (X/Twitter, etc.) as `social_post` content; idempotent by `source_id + post_id`

**Archive Sync** (collector → reader, see *Archive Sync* above)
- `GET /api/archive/export/articles.jsonl` — collector exports archive records as JSON Lines
- `POST /api/archive/import/articles.jsonl` — reader imports them (admin-only; no public fetch)

**Daily Brief** (collector/admin-gated; surfaced in 接入集成)
- `GET`/`POST /api/daily-brief/config` — read/set cron, top-N, LLM overrides (persisted in `AppSettingRecord` KV)
- `POST /api/daily-brief/generate` — submit a background digest job; returns `{status, job_id}` (poll `GET /api/jobs/{job_id}` for the result)
- `GET /api/daily-brief/runs` — recent run history; `GET /api/daily-brief/progress` — live in-memory generation progress; `GET /api/daily-brief/pipeline` — stage/pipeline view
- `GET /api/skill/daily-brief` — download the templated `dorami-daily-brief` Claude skill zip

**Collection Jobs** (the single scheduling entity; see *Collection Jobs* above)
- `GET/POST/PUT/DELETE /api/collection-jobs` — savable, schedulable multi-node job CRUD
- `POST /api/collection-jobs/{job_id}/run` — submit a background job (阶段3) that runs the collection job (still writes an aggregating `CollectionJobRunRecord`); returns `{status, job_id}`, poll `GET /api/jobs/{job_id}` for the aggregate (validation 404/400 stays synchronous)
- `GET /api/collection-job-runs` + `GET /api/collection-job-runs/{job_run_id}` — job-level run history
- `GET /api/fetch-runs/running-progress` — in-memory per-fetcher live progress
- (retired: `/api/node-groups*`, `/api/tasks*`, `/api/collection-jobs/migrate-legacy-tasks` — 实体简化阶段 2; existing data auto-migrated by Alembic `8f6d93196258`)

**Fetchers**
- `GET /api/fetchers` — list all discovered fetchers with parameter schemas
- `POST /api/fetch/{fetcher_id}` — trigger a specific fetcher (also writes `FetchRunRecord` and updates `SourceStateRecord`)

**Source Configs** (user-defined source definitions, advanced extension surface)
- `GET /api/source-configs` — list all source configs
- `POST /api/source-configs` — create a new source config
- `PUT /api/source-configs/{source_id}` — update a source config
- `POST /api/source-configs/{source_id}/toggle` — enable/disable a source
- `DELETE /api/source-configs/{source_id}` — delete a source config
- `POST /api/source-configs/{source_id}/fetch` — trigger fetch for a specific source config
- `POST /api/source-configs/fetch-active-rss` — trigger all active RSS source configs (阶段3: background job, returns `{status, job_id}`)
- `POST /api/source-configs/fetch-active-web` — trigger all active web/webpage source configs (via `generic_web`; 阶段3: background job, returns `{status, job_id}`)

**Source Builder** (AI node onboarding, collector-gated; frontend entry currently gated off — backend only)
- `POST /api/source-builder/analyze` — body `{url}`; detect page type + analyze + (LLM) propose a `SourceConfigCreate`-shaped node config (+ sample-article detail `CrawlProfile`)
- `POST /api/source-builder/preview` — body = proposed config; trial-run `generic_web`/`generic_rss` and return sample entries (no persist). Save via `POST /api/source-configs`.

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
- `GET`/`POST /api/vector/reconcile` — SQLite↔Chroma index reconciliation (admin); GET reports drift only, POST also repairs (see *SQLite↔Chroma reconciliation*)
- `DELETE /api/vector/{article_id}` / `POST /api/vector/batch-delete` — purge chunks, reset `is_vectorized` (admin)
- `POST /api/vector/search` — semantic search; for a `user` account, results are hard-scoped to subscribed sources
- `GET /api/vector/stats` — total chunk count; `GET /api/vector/subscribed-stats` — current user's read-only coverage ratio

**RAG**
- `POST /api/rag/context` — assemble ranked context string for downstream LLM apps (`user` account is subscription-scoped); body: `RagContextQuery` (`query`, `top_k`, `max_chars`, `score_threshold`, `content_type`, `source_id`, `publish_date_gte`, `context_separator`)
- `GET /api/rag/similar/{article_id}` — find semantically similar articles by re-querying with the article's own text

**Reader Subscriptions & Personal Feed** (reader surface)
- `GET /api/reader/sources` — subscribable source catalog (registry ∪ archived ∪ subscribed; enriched name/description/icon; `subscribed` flag)
- `POST`/`DELETE /api/reader/sources/{source_id}/subscribe` — one-click subscribe / unsubscribe (per-user)
- `POST /api/reader/articles/{article_id}/read` — record one deliberate article open (reader-gated, fire-and-forget; bumps `ReaderReadRecord` by the article's source)
- `GET/POST/PUT/DELETE /api/subscriptions` + `POST /api/subscriptions/{id}/rotate-token` — subscription lifecycle (owner-scoped); REST-only advanced/custom path
- `GET /api/reader/feed-token` + `POST /api/reader/feed-token/rotate` — the per-user aggregated feed token (`dfeed_`)
- `GET /api/public/feed/articles[.md]` — token-auth aggregated pull across all the user's subscribed sources (filters: `publish_date_start/end`, `content_types`, `source_ids`, `search`, `include_content`); per-subscription pulls at `GET /api/public/subscriptions/{id}/articles` and `POST .../vector/search`

**MCP** (reader surface)
- `/mcp` — FastMCP streamable-HTTP server (`build_mcp_app`); tools accept an optional `subscription_token` (`dsub_` or `dfeed_`) to scope results to that subscription / the user's whole subscription union

**Reader AI Beta** (reader surface; gated by global master switch + per-account flag + LLM configured)
- `POST /api/reader/ai/translate` — translate an article body to 简体中文 (cached in `extensions_json`)
- `POST /api/reader/ai/ask` — multi-turn QA over current-article / subscription context (RAG when enabled)

**Admin Ops** (admin-only via `account_admin_required`, `/api/admin/*` prefix)
- `GET /api/admin/overview` — account/archive/AI stat board + recent logins
- `GET /api/admin/accounts?days=` — account list enriched with `subscription_count` + **窗口指标** (`reads`, `logins`, `ai_calls`/`ai_tokens` over the last `days`, `logged_in_window`) + 埋点 fields
- `GET /api/admin/accounts/{username}/activity?days=` — per-reader activity detail: windowed AI usage (`ai_usage.summarize_user`) + reads (`reader_activity.summarize_user_reads`) + logins (`accounts.summarize_user_logins`: count/by_day/recent) + `source_engagement` (per-source reads ∪ favorites, friendly-named) + `favorites_total` + account/subscription snapshot
- `GET /api/admin/ai-usage?days=` — AI usage dashboard (calls + tokens by 用途/用户/日期)
- `GET`/`POST /api/admin/ai-beta/global` — read/set the AI Beta global master switch
- `GET`/`POST /api/llm/config` + `POST /api/llm/config/test` — global model config (shared by Daily Brief + reader AI; edited from 运维管理)

### Tests

Unit tests live directly under `tests/` as `test_*.py`. Fetcher/extraction: `rss_fetcher`, `webpage_fetcher`, `github_release_fetcher`, `repository_model_fetcher`, `ithome_web_fetcher`, `article_extractor`, `fetcher_curation`, `fetch_concurrency`, `fetch_failures`, `progress`. Platform/role: `mcp`, `runtime_role`, `subscriptions`, `rag_disabled` (`runtime_role`/`subscriptions` exercise the dual-role gating, subscriptions, aggregated feed, and admin/user vectorization split; `rag_disabled` verifies the `vector_sink`-is-`None` path returns 503 / "RAG disabled"). Daily-brief/LLM/sync: `daily_brief`, `llm_client`, `ensure_daily_collection_job`, `archive_sync`, `shendeng_export` (`daily_brief` also covers AI-usage attribution to the triggering admin vs `system`). Accounts/admin-ops: `accounts`, `admin_ops` (`admin_ops` covers login/AI 埋点, AI Beta global master switch + 熔断, `/api/admin/*` aggregation & admin-gating, and AI token metering — `record_usage` accumulation, `summarize`, recorder gating / ping-excluded; plus windowed per-user OPS — `usage_by_user`/`summarize_user` aggregation and the `/api/admin/accounts?days=` window fields + `/api/admin/accounts/{u}/activity` detail/404/admin-gating; and reading metering — `reader_activity` `record_read`/`reads_by_user`/`summarize_user_reads` aggregation + the `POST /api/reader/articles/{id}/read` endpoint records/ignores-missing; and login metering — `accounts` `touch_login` event-write + `logins_by_user`/`summarize_user_logins` aggregation, plus the activity endpoint's `logins`/`source_engagement`/`favorites_total`). Data layer (阶段2): `migrations` (Alembic baseline: fresh-DB `upgrade head` has zero drift from `SQLModel.metadata`, legacy-DB baseline adoption via `ensure_migrated`, index-reconcile migration restores dropped declared indexes, and the 实体简化 retirement migration — group-inline merge semantics / standalone-group & legacy-task conversion / table+column drops), `vector_reconcile` (SQLite↔Chroma drift classification + repair, and the `GET/POST /api/vector/reconcile` admin-gated endpoints). Jobs (阶段3): `jobs` (persistent `JobRecord` state machine — launch/run-to-terminal, success-result/failure-error persistence, progress flush, `get_job`/`list_jobs` read-back incl. survives a new engine = restart, and the `/api/vectorize/all-pending` → `/api/jobs/{id}` end-to-end); `collection_run_job` (collection-job run submits a background job); `index_status` (enum state transitions in the storage layer + `save()` stale semantics + `GET /api/articles?index_status=` filter/payload); `migrations` also covers the `index_status` backfill migration. Each file self-bootstraps `sys.path` to `src/` so imports resolve without an editable install. Run with pytest:

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

Loaded by `src/config.py` (`load_config()`) into the `settings` singleton (read live in `app.py`; tests monkeypatch it). The config file path is `DORAMI_CONFIG_FILE` if set (production uses `config/production.ini`), else `config/backend.ini`.

- `[runtime] role` — keep the default `all` (single-node all-in-one) unless you run a *split deployment*: `collector` (external collection/archive) | `reader` (intranet distribution). Inert for `all`; see *Access control — login account role*. Overridable via `DORAMI_RUNTIME_ROLE`.
- `[auth] admin_users` / `user_users` — comma-separated `username:password` pairs, **first-boot seeds only** (seeded into the `users` table when it's empty; afterwards accounts are DB-managed — see *Accounts are database-managed*). `admin` accounts are collector+reader superusers; `user` accounts are reader-only. `[auth] secret` salts the session and subscription/feed token HMACs (keep it stable — changing it invalidates issued sessions/tokens). When unset it falls back to a **non-password-derived** local key (`database_url` + fixed salt — no longer mixes in the seed passwords). **Startup security validation** (`api/security_checks.py`, run in `lifespan`) grades config by posture: in a production posture (`[auth] cookie_secure = true`, i.e. HTTPS) it **refuses to boot** on critical misconfig — an unset/placeholder `secret`, or `[cors] allow_origins=*` together with `allow_credentials=true`; in dev posture (`cookie_secure=false`) the same issues are logged as warnings only. It also warns when `[network] disable_ca_bundle=true` in production.
- `[rag] enabled` — `false` (default) | `true`. Master switch for the vector/RAG subsystem; when off no embedding model loads. Overridable via `DORAMI_RAG_ENABLED`. See *RAG is opt-in and lazy-loaded*.
- `[llm] base_url` / `api_key` / `model` (+ `timeout_seconds`, `temperature`, `max_tokens`, `map_concurrency`) — OpenAI-compatible LLM for the Daily Brief. Empty by default (Daily Brief is inert until configured). Overridable via `DORAMI_LLM_BASE_URL` / `DORAMI_LLM_API_KEY` / `DORAMI_LLM_MODEL` and at runtime via admin settings (KV). See *LLM client*.

### Environment Variables

| Variable | Purpose |
|---|---|
| `HF_ENDPOINT` | HuggingFace mirror (defaults to `https://hf-mirror.com` in `main.py`) |
| `LOCAL_MODEL_PATH` | Path to local sentence-transformers model for offline embedding; defaults to `BAAI/bge-m3` |
| `DORAMI_RUNTIME_ROLE` | Override `[runtime] role` (`all`/`collector`/`reader`) |
| `DORAMI_RAG_ENABLED` | Override `[rag] enabled` (`1`/`true`/`yes`/`on` to enable the vector/RAG subsystem) |
| `DORAMI_CONFIG_FILE` | Path to the ini config file to load (defaults to `config/backend.ini`); production deploy sets it to `config/production.ini` |
| `DORAMI_LLM_BASE_URL` / `DORAMI_LLM_API_KEY` / `DORAMI_LLM_MODEL` | Override the `[llm]` OpenAI-compatible client config (used by the Daily Brief) |
| `GITHUB_TOKEN` / `GH_TOKEN` | Optional GitHub API token for the GitHub repo fetchers; raises the rate limit (60→5000/hr) for repo listing + README backfill |
