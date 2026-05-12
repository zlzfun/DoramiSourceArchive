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

Phase 1: fetching observability and source-infrastructure foundation.

The first goal is to make fetch execution visible and auditable before adding many new sources.

## Backlog

### Epic 1: Source Coverage Expansion

- [x] Add a generic RSS/Atom fetcher.
- [x] Add a first batch of built-in RSS/Atom source fetchers.
- [x] Add a second batch of verified built-in RSS/Atom source fetchers.
- [ ] Add more official AI company sources.
- [ ] Add more model/product update sources.
- [x] Add initial paper sources.
- [x] Add initial GitHub release sources.
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
- [ ] Preserve raw source payloads.
- [ ] Standardize content formats.
- [ ] Archive media metadata.
- [ ] Add language and region markers.

### Epic 5: Dify Delivery

- [ ] Add Dify pull APIs with time/source/status filters.
- [ ] Track Dify sync status per article.
- [ ] Add batch JSON/Markdown export.
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
