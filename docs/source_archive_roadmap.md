# DoramiSourceArchive Development Roadmap

## Product Direction

DoramiSourceArchive is an AI news/source collection and archiving hub for Dify, agents, and private knowledge workflows.
It should prioritize broad source coverage, reliable fetching, clean archival data, traceability, and downstream delivery.

This project should not compete first as a public reader-facing news product. Public reading pages, themes, read state, and growth-oriented UI are intentionally lower priority.

## Operating Principles

- Keep humans in the loop: announce the next development stage before starting it, and summarize progress after finishing it.
- Commit in small checkpoints after each verified stage.
- Verify each implemented slice before starting a larger new feature.
- Persist decisions and progress here so another coding assistant can take over without relying on conversation history.

## Current Phase

Phase 1: fetching observability and multi-type source-infrastructure foundation.

The current goal is to keep fetch execution visible and auditable while expanding from RSS/Atom into a broad built-in fetcher catalog: official web pages, WeChat, X/Twitter, GitHub, papers, and community/news sources.

## Backlog

### Epic 1: Source Coverage Expansion

- [x] Add a persistent source coverage matrix.
- [x] Add a generic RSS/Atom fetcher.
- [x] Add a first batch of built-in RSS/Atom source fetchers.
- [x] Add a second batch of verified built-in RSS/Atom source fetchers.
- [x] Add official website/blog/news page fetcher foundation.
- [x] Add first built-in webpage sources for official AI company news.
- [x] Draft X/Twitter ingestion decision note.
- [ ] Decide X/Twitter ingestion strategy before implementation.
- [ ] Add first built-in X/Twitter source fetchers after strategy decision.
- [x] Expand built-in WeChat account fetchers.
- [ ] Validate newly added WeChat fetchers with real WeChat MP credentials.
- [ ] Add more official AI company sources.
- [x] Add more model/product update sources.
- [x] Add initial paper sources.
- [x] Add initial GitHub release sources.
- [x] Add richer GitHub Releases API fetchers for structured release metadata.
- [x] Add initial community/news sources.
- [ ] Convert hard-coded WeChat account fetchers toward configurable source entries.

### Epic 2: Source Configuration

- [x] Add a SourceConfig table.
- [x] Support generic fetchers driven by source configuration.
- [x] Add backend APIs for source management.
- [ ] Add source grouping by type.
- [ ] Support source import/export as JSON or YAML.

### Epic 3: Fetch Stability and Observability

- [x] Add persistent fetch run records.
- [x] Add a fetch run history UI.
- [x] Store incremental cursors per source.
- [x] Add basic failure classification.
- [x] Add source health states.
- [ ] Add alert notifications for important failures.

### Epic 4: Archive Data Quality

- [ ] Improve deduplication beyond primary IDs.
- [ ] Normalize URLs.
- [ ] Add full-text extraction for summary-only sources.
  - [x] Add optional article-detail extraction for official webpage fetchers.
- [ ] Preserve raw source payloads.
- [ ] Standardize content formats.
- [ ] Archive media metadata.
- [ ] Add language and region markers.

### Epic 5: Dify Delivery

- [x] Add Dify pull APIs with time/source/status filters.
- [ ] Track Dify sync status per article.
- [x] Add batch JSON/Markdown export.
- [x] Add social post webhook/import bridge for safe X/Twitter-adjacent ingestion.
- [ ] Enhance webhook payloads to include new content batches.
- [ ] Add standard time-window slices.
- [ ] Make downstream sync idempotent.

### Epic 6: Admin Console Enhancements

- [ ] Add a source management tab as an advanced configuration surface.
- [x] Add a fetch run history tab.
- [ ] Add a data quality check tab.
- [ ] Add a Dify sync tab.
- [ ] Extend article filters with source type, sync status, full-text status, and duplicate status.

## Decision Log

- 2026-05-11: Keep the product positioned as source collection and archival infrastructure for Dify/agents/private knowledge systems, not as a public reader-facing AI news portal.
- 2026-05-11: Start implementation with fetch run observability before adding generic RSS/source configuration, because source expansion without run history would be hard to debug.
- 2026-05-11: Store source configuration primarily in SQLite through `SourceConfigRecord`; JSON/YAML import/export remains a later convenience layer.
- 2026-05-11: Restore the original fetcher-registry-first product direction. Built-in fetchers should be the primary user workflow; user-defined source configuration remains an advanced/parallel capability and should not dominate the main UI.
- 2026-05-12: Avoid treating RSS/Atom as the architecture center. RSS is one source family; the main source-expansion direction is a multi-type built-in fetcher catalog covering official web pages, WeChat, X/Twitter, GitHub, papers, and community/news sources.
- 2026-05-12: X/Twitter ingestion requires an explicit strategy decision before implementation because official API access, browser/session scraping, third-party mirrors, and webhook/import bridges have different reliability, cost, and account-risk tradeoffs.
- 2026-05-12: Newly added WeChat account subclasses are registry-validated only; they are not considered production-verified until a real run with valid WeChat MP credentials confirms account-name matching, fakeid resolution, rate-limit behavior, and body extraction.

## Progress Log

- 2026-05-11: Created this roadmap/progress document as the persistent handoff anchor.
- 2026-05-11: Added `FetchRunRecord`, fetch run creation/completion helpers, manual/scheduled run recording, `/api/fetch-runs` list/detail APIs, and pipeline result counters.
- 2026-05-11: Verified with `python3 -m compileall src` and a lightweight pipeline counter behavior test using a stubbed HTTP client.
- 2026-05-11: Added a `FetchRunsTab` admin page, wired `/api/fetch-runs` into the frontend API client, and exposed it through the main navigation as `运行历史`.
- 2026-05-11: Verified frontend build with `npm run build` by temporarily linking the original project `frontend/node_modules`; verified backend syntax with the original project `.venv` Python.
- 2026-05-11: Ran `npm run lint`; remaining failures are existing lint debt in `DataTab.jsx`, `DateRangePicker.jsx`, `FetchTab.jsx`, and `VectorTab.jsx`. The new `FetchRunsTab.jsx` no longer reports lint errors.
- 2026-05-11: Added `SourceConfigRecord` with stable source IDs, source type/category, optional bound fetcher, activity flag, scheduling hints, and JSON params.
- 2026-05-11: Added `/api/source-configs` CRUD APIs, toggle endpoint, filtering by type/category/activity/search, and response serialization that exposes parsed `params`.
- 2026-05-11: Verified backend syntax with `.venv` Python and validated `SourceConfigRecord` create/query behavior against an in-memory SQLite database.
- 2026-05-11: Added a `SourcesTab` admin page for listing, filtering, creating, editing, enabling/disabling, and deleting `SourceConfigRecord` entries.
- 2026-05-11: Verified frontend build with `npm run build` by temporarily linking the original project `frontend/node_modules`; `npm run lint` still fails only on existing lint debt outside the newly added source-management component.
- 2026-05-11: Added `GenericRssFetcher` (`generic_rss`) for single-feed RSS/Atom ingestion with runtime source identity, stable item IDs, HTML-to-text cleanup, feed metadata, tags, media URL extraction, and raw entry traces.
- 2026-05-11: Verified RSS fetcher registration and offline parsing behavior with a local RSS fixture; verified backend syntax with `.venv` Python.
- 2026-05-11: Added SourceConfig-triggered fetch APIs for single-source execution and batch execution of all active RSS/Atom sources, reusing the same fetch run tracking path.
- 2026-05-11: Added source-management UI actions to trigger a single source or batch-trigger active RSS sources from the admin console.
- 2026-05-11: Verified backend syntax with `.venv` Python and frontend build with `npm run build`; lint still fails only on pre-existing component debt.
- 2026-05-11: Cleaned frontend lint baseline by removing unused imports/functions and disabling `react-hooks/set-state-in-effect` for the current data-loading style. `npm run lint` now exits successfully with one remaining exhaustive-deps warning in `DataTab.jsx`.
- 2026-05-11: Hid the `SourcesTab` from the main navigation after product review. SourceConfig code remains available as an advanced foundation, but the primary UI flow is again the dynamic built-in fetcher registry under `节点与调度`.
- 2026-05-11: Added a `PresetRssFetcher` base class and the first built-in RSS/Atom source catalog: OpenAI News, Hugging Face Blog, LangChain Blog, GitHub Blog, arXiv cs.AI/cs.CL/cs.LG/cs.CV, Hacker News AI search, Dify releases, and vLLM releases. These appear through the existing dynamic fetcher registry rather than the advanced source-configuration UI.
- 2026-05-11: Exposed fetcher `category` metadata through `/api/fetchers` and updated the `节点与调度` page with category filters, search, type badges, and fetcher descriptions so the built-in catalog remains usable as source coverage grows.
- 2026-05-11: Validated candidate official feeds with live HTTP/feed parsing before adding them. Anthropic, Mistral, and IBM Research RSS candidates were not added because the tested URLs returned 404 or no feed entries.
- 2026-05-11: Added a second built-in source batch: Google AI Blog, Google DeepMind News, Microsoft AI Blog, NVIDIA Developer Blog, arXiv stat.ML/eess.IV, and GitHub release feeds for Ollama, Transformers, PyTorch, llama.cpp, and LangChain.
- 2026-05-11: Added `/api/source-health`, deriving per-fetcher health from `FetchRunRecord` without a new table, and surfaced health badges, latest run time, latest saved count, and consecutive failures on the `节点与调度` cards.
- 2026-05-12: Added `SourceStateRecord` for persistent per-source health and conservative incremental cursors. Fetch execution now marks a source running at start, updates healthy/failing state at completion, records latest content ID/date as cursor metadata, tracks success/failure counters, and exposes `/api/source-states` for handoff/debugging. Cursor recording is intentionally passive for now and does not skip older feed entries yet.
- 2026-05-12: Added `docs/source_catalog.md` as the persistent multi-type source coverage matrix. It records implemented sources, AIHot-inspired candidates, immediate next slices, and the pending X/Twitter ingestion decision so future work does not drift back into RSS-only expansion.
- 2026-05-12: Added `WebPageArticleContent`, `BaseWebPageListFetcher`, and first built-in official webpage sources: Anthropic News, Claude Blog, Runway News, and Mistral AI News. These capture list-page metadata and article links as `web_article` entries; full article extraction remains a later archive data-quality task.
- 2026-05-12: Seeded the original project worktree database at `/Users/zhuliuzi/PycharmProjects/DoramiSourceArchive/data/cms_data.db` with 70 sample articles for parallel RAG development: 54 `rss_article` records and 16 `web_article` records across OpenAI, Hugging Face, Google AI, Google DeepMind, Microsoft AI, NVIDIA, arXiv, Hacker News, Dify/vLLM/Ollama releases, Anthropic News, Claude Blog, Runway News, and Mistral AI News.
- 2026-05-12: Expanded built-in WeChat fetcher subclasses from 3 to 9 accounts by adding AI科技评论、AI前线、智东西、Founder Park、硅星人、夕小瑶科技说. These appear in the dynamic registry but real runs still require valid WeChat MP credentials.
- 2026-05-12: Added `docs/x_twitter_ingestion_decision.md` and a clear open verification section in `docs/source_catalog.md`. The recommended X/Twitter near-term path is a webhook/import bridge, with official X API as a later option if credentials and costs are acceptable.
- 2026-05-12: Added `SocialPostContent` and `POST /api/import/social-posts` as a safe social-post import bridge. External collectors can now push normalized X/Twitter-like posts into the archive idempotently without direct X crawling.
- 2026-05-12: Enabled HTTP redirect following for the shared `BaseFetcher` client after the Google AI feed moved from `/technology/ai/rss/` to `/innovation-and-ai/technology/ai/rss/`.
- 2026-05-12: Added optional article-detail extraction to `BaseWebPageListFetcher` through `fetch_detail` and `detail_max_chars`, and fixed the fetcher parameter UI so boolean parameters render as checkboxes. Verified with an offline HTML fixture and a live `web_anthropic_news` detail run.
- 2026-05-12: Added two more verified official webpage sources: `web_stability_news` and `web_elevenlabs_blog`. Candidate checks found that Cohere's static list page does not expose direct article links yet, while Perplexity Blog and xAI News returned 403 to the current HTTP client, so those remain documented candidates rather than built-in nodes.
- 2026-05-12: Added per-source webpage defaults for detail extraction. `web_stability_news` now defaults `fetch_detail=true` with a lower default limit because its list page requires detail pages for usable titles and content.
- 2026-05-12: Added `GitHubReleaseContent`, a generic GitHub Releases API fetcher, and 12 built-in API-backed release sources covering Dify, vLLM, Ollama, LangChain, Transformers, PyTorch, llama.cpp, LiteLLM, Open WebUI, ComfyUI, OpenAI Agents SDK, and Claude Code. Verified registry discovery, offline serialization, and live API fetches for Claude Code, OpenAI Agents SDK, and LiteLLM.
- 2026-05-12: Added Dify delivery endpoints: `GET /api/dify/articles` for JSON pulls and `GET /api/dify/articles.md` for Markdown batch export. Both support source/content/date/search filters, default to content-bearing records, and are documented in `docs/dify_delivery.md`. Verified with FastAPI `TestClient`.
