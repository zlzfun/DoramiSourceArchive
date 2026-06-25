# DoramiSourceArchive Docs

Reference documentation for the archive. (A whole-repo navigation map lives in
the repo-root [`README.md`](../README.md); architecture and dev commands live in
the repo-root `CLAUDE.md`; this folder holds the durable contracts, standards,
and source/node knowledge.)

## Deployment & configuration

- [configuration.md](./configuration.md) — `config/backend.ini`, login account roles (admin / user), and the optional split-deployment runtime roles (collector / reader / all).

## Contracts (downstream integration)

API and data contracts for systems that consume the archive.

- [contracts/feed_delivery.md](./contracts/feed_delivery.md) — `/api/feed/*` JSON + Markdown delivery for LLM/RAG consumers.
- [contracts/archive_sync.md](./contracts/archive_sync.md) — collector → reader JSONL export/import contract (identity, lineage, checksum).
- [contracts/reader_subscription.md](./contracts/reader_subscription.md) — reader subscriptions, one-click subscribe, aggregated feed token, tokenized pull endpoints.

## Frontend (design & implementation discipline)

- [frontend/conventions.md](./frontend/conventions.md) — **前端开发纪律**：文案/可访问性/排版/颜色令牌/圆角/高程/动效/主操作/暗色预留的约定与自检清单（借鉴 Geist，token 单一事实来源在 `frontend/src/index.css`）。

## Sources & nodes (curation and maintenance)

How sources are classified, admitted, audited, and maintained.

- [sources/classification_standard.md](./sources/classification_standard.md) — the v1.1 identity + classification metadata every source carries.
- [sources/curation_policy.md](./sources/curation_policy.md) — what may be default-visible; the `ESSENTIAL_FETCHER_IDS` policy.
- [sources/admission_workflow.md](./sources/admission_workflow.md) — the add-only workflow for proposing and admitting a new source.
- [sources/node_audit_playbook.md](./sources/node_audit_playbook.md) — **how to verify a node is healthy and fix it when it isn't** (inspection steps, quality checks, failure-pattern catalog, deletion criteria).
- [sources/node_catalog_and_risks.md](./sources/node_catalog_and_risks.md) — **the current node catalog**: each node's special adaptation and its stability risk.
- [sources/candidates/](./sources/candidates/) — per-vendor candidate source records (recommended + parking lot), with validation notes.

## Analysis & history

- [analysis/horizon-vs-dorami.md](./analysis/horizon-vs-dorami.md) — fetch/daily-briefing principle comparison against [Thysrael/Horizon](https://github.com/Thysrael/Horizon).
- [archive/](./archive/) — landed or superseded planning docs kept for provenance (e.g. `frontend-optimization-plan.md`, a historical snapshot of the frontend-polish effort).
