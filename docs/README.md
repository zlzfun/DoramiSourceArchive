# DoramiSourceArchive Docs

Reference documentation for the archive. (Architecture and dev commands live in
the repo-root `CLAUDE.md`; this folder holds the durable contracts, standards,
and source/node knowledge.)

## Deployment & configuration

- [configuration.md](./configuration.md) — `config/backend.ini`, runtime roles (collector / reader / all), and the two-tier collector+reader deployment.

## Contracts (downstream integration)

API and data contracts for systems that consume the archive.

- [contracts/feed_delivery.md](./contracts/feed_delivery.md) — `/api/feed/*` JSON + Markdown delivery for LLM/RAG consumers.
- [contracts/archive_sync.md](./contracts/archive_sync.md) — collector → reader JSONL export/import contract (identity, lineage, checksum).
- [contracts/reader_subscription.md](./contracts/reader_subscription.md) — reader subscriptions, one-click subscribe, aggregated feed token, tokenized pull endpoints.

## Sources & nodes (curation and maintenance)

How sources are classified, admitted, audited, and maintained.

- [sources/classification_standard.md](./sources/classification_standard.md) — the v1.1 identity + classification metadata every source carries.
- [sources/curation_policy.md](./sources/curation_policy.md) — what may be default-visible; the `ESSENTIAL_FETCHER_IDS` policy.
- [sources/admission_workflow.md](./sources/admission_workflow.md) — the add-only workflow for proposing and admitting a new source.
- [sources/node_audit_playbook.md](./sources/node_audit_playbook.md) — **how to verify a node is healthy and fix it when it isn't** (inspection steps, quality checks, failure-pattern catalog, deletion criteria).
- [sources/node_catalog_and_risks.md](./sources/node_catalog_and_risks.md) — **the current node catalog**: each node's special adaptation and its stability risk.
- [sources/candidates/](./sources/candidates/) — per-vendor candidate source records (recommended + parking lot), with validation notes.
