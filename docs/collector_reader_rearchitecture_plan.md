# Collector / Reader Re-architecture Plan

## Background

DoramiSourceArchive is currently developed and verified on a personal external-network
machine. When the same project runs on an isolated company intranet server, many
fetching nodes fail because of network boundary differences such as proxy
authentication errors, source-side access denial, and SSL-related failures.

The root issue is not a single fetcher defect. It is that public-network collection
and intranet consumption are coupled into one runtime. The project should therefore
separate the environment that performs broad automated fetching from the environment
that serves archived content to internal users and downstream applications.

## Target Shape

The project will evolve into two runtime roles while remaining in one repository at
the beginning:

- `collector`: the external-network collection and archival layer.
- `reader`: the intranet-facing distribution and subscription layer.

The external collector owns fetching, scheduling, fetch observability, raw archival
records, source health, and export/sync packaging. The internal reader owns content
browsing, user-specific content ranges, Dify delivery, MCP access, search/RAG
surfaces, authentication for consumers, and archive import.

Downstream applications such as Dify workflows, agents, daily brief generation, and
other content orchestration systems stay outside this project and consume the reader
layer only.

## Terminology

To avoid ambiguity, the collection side and the distribution side should not share
the same "node group" abstraction.

- Collector-side grouping should be named around collection execution, for example
  `CollectionJob`, `CollectionRun`, and task node scope.
- Reader-side grouping should be named around consumption, for example
  `Subscription`, `ContentSource`, or `DeliveryProfile`.

The preferred product language is:

- Collector layer: "采集任务".
- Reader layer: "订阅源".

The existing `NodeGroupRecord` capability should be reviewed and either folded into
`CollectionJobRecord` or retained only as an internal migration compatibility shape.
It should not remain a shared user-facing concept across both layers.

## Development Rules

Each development stage should start from its own branch using the `codex/` prefix.
At the end of a stage, work should stop for human review and optional review by
another AI assistant before merge. Stages should be small enough that their branch
can be reviewed independently.

Stage branches should not silently absorb unrelated local changes. Existing user
changes must be preserved and left untouched unless they are explicitly part of the
stage.

After a stage is reviewed and accepted, it can be merged back before the next stage
starts from a fresh branch.

## Stage Plan

### Stage 0: Written Plan

Create this architecture and execution plan so future human or AI contributors can
understand why the project is being split, what the target roles are, and how later
stages should proceed.

Deliverable:

- A repository document describing the background, target architecture, terminology,
  stages, and review gates.

Stop condition:

- Stop after the document is committed and report for review.

### Stage 1: Runtime Role Boundary

Add an explicit runtime role setting, initially supporting `collector`, `reader`, and
possibly `all` for local development compatibility.

Expected changes:

- Add configuration for the active runtime role.
- Gate scheduler startup and fetch-related APIs behind collector-capable roles.
- Gate Dify/MCP/content delivery APIs behind reader-capable roles.
- Keep local development behavior compatible unless a role is explicitly selected.

Review focus:

- No functional regression for current local development.
- Clear behavior when a role disables an API surface.

### Stage 2: Collector Terminology and Collection Model Cleanup

Reduce ambiguity around "node groups" in the collector layer.

Expected changes:

- Update collector-facing UI language from node grouping toward collection jobs or
  collection task scopes.
- Decide whether `NodeGroupRecord` should be folded into `CollectionJobRecord`,
  retained as a compatibility table, or hidden from the main UI.
- Keep database migrations conservative and avoid destructive schema changes unless
  there is a separate migration plan.

Review focus:

- Product language is clear.
- Existing saved jobs and historical lineage remain understandable.

### Stage 3: Archive Sync Contract

Define and implement the first collector-to-reader archive sync path.

Expected changes:

- Add an export format such as JSONL bundle or SQLite bundle from the collector.
- Add reader-side import that is idempotent.
- Preserve archive identity and lineage fields such as article ID, source ID,
  content type, publish date, fetched date, fetch run ID, job ID, job run ID, and
  source group or task scope where applicable.
- Include checksum or equivalent content integrity metadata.

Review focus:

- Sync can be repeated safely.
- Reader import does not trigger public-network fetches.
- The export/import format is documented enough for external automation.

### Stage 4: Reader Subscription Layer

Introduce user-facing content consumption scopes.

Expected changes:

- Add reader-side subscription or content source profiles.
- Allow profiles to filter by source, content type, category, date window, keyword,
  job lineage, or other archived metadata.
- Add per-profile delivery settings for Dify/API/MCP usage.
- Add consumer access tokens distinct from admin login.

Review focus:

- Reader concepts do not expose collector implementation details unnecessarily.
- Downstream consumers can get a stable personalized endpoint or MCP surface.

### Stage 5: UI Role Split and Deployment Polish

Make the role separation visible and deployable.

Expected changes:

- Collector UI focuses on fetching, scheduling, observability, data quality, and
  archive export.
- Reader UI focuses on browsing, subscription setup, search, delivery endpoints, and
  MCP/Dify instructions.
- Add deployment examples for external collector and intranet reader.

Review focus:

- UI does not mix collector operations into the intranet reader role.
- Deployment instructions match the intended network boundary.

## Non-goals for the Initial Refactor

- Do not rewrite all fetchers.
- Do not make the intranet reader perform public-network crawling.
- Do not treat vector storage as the primary sync format.
- Do not split into separate repositories until the runtime boundary is proven.
- Do not introduce broad multi-tenant complexity before basic subscriptions and
  consumer tokens are working.

## Open Decisions

- Whether `all` should remain a supported long-term role or only a development
  compatibility mode.
- Whether the first sync mechanism should be HTTP pull, file bundle import, or both.
- Whether reader-side MCP should expose one global archive surface or per-subscription
  tools filtered by token/profile.
- How much historical `NodeGroupRecord` data should be migrated versus hidden behind
  compatibility APIs.
