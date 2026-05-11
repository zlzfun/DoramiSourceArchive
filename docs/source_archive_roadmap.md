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

- [ ] Add a generic RSS/Atom fetcher.
- [ ] Add official AI company sources.
- [ ] Add model/product update sources.
- [ ] Add paper sources.
- [ ] Add GitHub release/trending sources.
- [ ] Add community/news sources.
- [ ] Convert hard-coded WeChat account fetchers toward configurable source entries.

### Epic 2: Source Configuration

- [ ] Add a SourceConfig table.
- [ ] Support generic fetchers driven by source configuration.
- [ ] Add backend APIs for source management.
- [ ] Add source grouping by type.
- [ ] Support source import/export as JSON or YAML.

### Epic 3: Fetch Stability and Observability

- [x] Add persistent fetch run records.
- [x] Add a fetch run history UI.
- [ ] Store incremental cursors per source.
- [ ] Improve retry and failure classification.
- [ ] Add source health states.
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

- [ ] Add a source management tab.
- [ ] Add a fetch run history tab.
- [ ] Add a data quality check tab.
- [ ] Add a Dify sync tab.
- [ ] Extend article filters with source type, sync status, full-text status, and duplicate status.

## Decision Log

- 2026-05-11: Keep the product positioned as source collection and archival infrastructure for Dify/agents/private knowledge systems, not as a public reader-facing AI news portal.
- 2026-05-11: Start implementation with fetch run observability before adding generic RSS/source configuration, because source expansion without run history would be hard to debug.

## Progress Log

- 2026-05-11: Created this roadmap/progress document as the persistent handoff anchor.
- 2026-05-11: Added `FetchRunRecord`, fetch run creation/completion helpers, manual/scheduled run recording, `/api/fetch-runs` list/detail APIs, and pipeline result counters.
- 2026-05-11: Verified with `python3 -m compileall src` and a lightweight pipeline counter behavior test using a stubbed HTTP client.
- 2026-05-11: Added a `FetchRunsTab` admin page, wired `/api/fetch-runs` into the frontend API client, and exposed it through the main navigation as `运行历史`.
- 2026-05-11: Verified frontend build with `npm run build` by temporarily linking the original project `frontend/node_modules`; verified backend syntax with the original project `.venv` Python.
- 2026-05-11: Ran `npm run lint`; remaining failures are existing lint debt in `DataTab.jsx`, `DateRangePicker.jsx`, `FetchTab.jsx`, and `VectorTab.jsx`. The new `FetchRunsTab.jsx` no longer reports lint errors.
