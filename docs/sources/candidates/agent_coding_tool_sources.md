# Agent / Coding Tool Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

## Tier Judgment

Cursor Changelog, OpenCode GitHub Releases, OpenClaw GitHub Releases, and Hermes Agent GitHub Releases are classified as `tier0_primary` because they are first-party release or changelog surfaces for their own products/projects.

That does not mean they should automatically enter the default catalog. Their admission should be decided by signal strength, noise risk, update frequency, and whether the final catalog has a dedicated agent/coding-tool lane. Open-source project release streams can be primary sources and still be lower-priority or watchlist-only.

# Recommended Review Sources

## Source: Cursor Changelog

- status: `under_review`
- source_owner: `cursor`
- source_brand: `cursor`
- source_scope: `developer_tool`
- source_channel: `changelog`
- source_url: `https://cursor.com/changelog`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`, `api_platform`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Cursor IDE/product releases, agents window, automations, Bugbot, canvases, editor-agent workflows, enterprise/team features, and AI coding product updates.

### Inclusion Reasons

Cursor is one of the strongest AI coding application benchmarks. The changelog is more direct and actionable than the blog for release tracking.

### Risks / Open Questions

May omit some minor builds or lag behind in-app releases. Still likely sufficient for focused catalog tracking.

### Known Overlap

Overlaps with Cursor Blog for major launches and tier1 media coverage.

### Validation Notes

Prefer changelog over blog as the first Cursor source. Existing project has `web_cursor_blog`, but the changelog may be the better default candidate.

Audited 2026-06-02: the listing matched nav/footer links (`/changelog/enterprise|pricing|community`) as articles — their detail pages 404 and they carry no body, so they landed as empty-content junk rows. Added those nav paths to `exclude_url_patterns` and set `drop_empty_content = True` (new opt-in flag on `BaseWebPageListFetcher`) so any empty-content entry is dropped before archiving.

Follow-up: the `/changelog` listing only shows ~5 recent entries; older ones live behind `/changelog/page/N` pagination, so `limit=20` was returning only 5. Added general listing pagination to `BaseWebPageListFetcher` (`max_listing_pages` + a `_next_listing_page_url` hook); Cursor sets `max_listing_pages=8` and follows the next `/changelog/page/N`. `limit=20` now yields 20 dated entries across pages 1–4.

## Source: OpenCode GitHub Releases

- status: `under_review`
- source_owner: `opencode`
- source_brand: `opencode`
- source_scope: `developer_tool`
- source_channel: `github_release`
- source_url: `https://github.com/anomalyco/opencode/releases`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`
- signal_strength: `medium_signal`
- noise_risk: `high_noise`
- fetch_reliability: `stable_public`

### Target Coverage

OpenCode release versions, terminal/IDE/desktop agent updates, provider integrations, model support, MCP/subagent features, and privacy/deployment changes.

### Inclusion Reasons

OpenCode is a prominent open-source coding agent and may be worth tracking as an agent-tool ecosystem signal.

### Risks / Open Questions

Release frequency appears very high; without compaction this can overwhelm the catalog. It should not be admitted before lower-frequency official model/product sources.

### Known Overlap

Overlaps with xAI News when Grok/OpenCode integrations are announced and with general coding-agent media coverage.

### Validation Notes

Live review confirmed the GitHub releases page is reachable. Prefer GitHub Releases API. Consider weekly compaction or only major releases if implemented.

Audited 2026-06-02: the implemented node tracked `opencode-ai/opencode`, which **stopped releasing at v0.0.55 (2025-06-27)** — the project moved (the old `sst/opencode` now 301-redirects) and the active repo is **`anomalyco/opencode`** (releasing v1.15.x). Re-pointed `owner`/`repo`/`source_url` to `anomalyco/opencode` to restore a live signal (kept the `github_opencode_releases` source_id).

## Source: OpenClaw GitHub Releases

- status: `under_review`
- source_owner: `openclaw`
- source_brand: `openclaw`
- source_scope: `developer_tool`
- source_channel: `github_release`
- source_url: `https://github.com/openclaw/openclaw/releases`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`, `api_platform`
- signal_strength: `medium_signal`
- noise_risk: `high_noise`
- fetch_reliability: `stable_public`

### Target Coverage

OpenClaw release notes, gateway/provider integrations, multi-channel agent behavior, plugins, safety/approval changes, and platform support.

### Inclusion Reasons

OpenClaw is part of the agent-tool landscape the user explicitly wants considered.

### Risks / Open Questions

Release notes are long and frequent. This should probably be parking-lot or compacted unless the project decides agent-tool releases are a top-level lane.

### Known Overlap

Overlaps with xAI/Google/OpenAI/Claude ecosystem updates when OpenClaw integrates new models.

### Validation Notes

Prefer GitHub Releases API. Consider tracking only stable releases or monthly summaries.

Audited 2026-06-02: OpenClaw ships several `-beta` prereleases per day (11 of the last 12 releases were prereleases, multiple per day) — extreme noise. Set `default_include_prereleases = False` so the node tracks **stable releases only** by default (param can re-enable betas). To keep stable history meaningful when betas crowd the feed, the generic releases fetcher now fetches `per_page=100` when prereleases are excluded, then emits up to `limit` stable ones. Verified: a fresh `limit=20` fetch returns 20 stable releases, 0 betas. (Betas seen after the fix are pre-fix archived rows; they persist until deleted from the ledger.)

## Source: Hermes Agent GitHub Releases

- status: `under_review`
- source_owner: `nousresearch`
- source_brand: `hermes_agent`
- source_scope: `developer_tool`
- source_channel: `github_release`
- source_url: `https://github.com/NousResearch/hermes-agent/releases`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`, `api_platform`
- signal_strength: `medium_signal`
- noise_risk: `high_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Hermes Agent releases, memory/self-improvement features, integrations, channels, skills/tools, local proxy/gateway features, and agent runtime updates.

### Inclusion Reasons

Hermes Agent is a notable open-source agent practice source and has existing project-side interest.

### Risks / Open Questions

Release notes can be large and implementation-heavy. It may be more valuable as a watchlist source than a default source.

### Known Overlap

Overlaps with xAI, Claude, OpenAI, and OpenClaw-related integration news.

### Validation Notes

Existing project already has a Hermes Agent releases fetcher candidate; keep hidden unless the agent-tool lane is accepted.

Audited 2026-06-02: works as intended — 12/12 recent releases are stable (no prereleases), already split per release with real dates. No fix needed. Note the bodies are large cumulative changelogs (52k–71k chars), which can read like "everything in one article" but are genuinely one-release-each; left untruncated.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Cursor Blog | `https://cursor.com/blog` | Valuable narrative source, but changelog is more direct for release tracking. |
| Cursor forum / Reddit | varies | Community signal only; tier2/social and not appropriate for direct built-in fetching. |
| OpenCode website | `https://opencode.ai/` or `https://dev.opencode.ai/` | Good product overview, but not a chronological update source. |
| OpenCode third-party changelogs | varies | Useful manual references, but not first-party sources. |
| OpenClaw third-party mirrors | varies | Avoid unless official GitHub releases are insufficient. |
| Hermes Agent docs site | `https://hermes-agent.nousresearch.com/docs` | Useful documentation, but release tracking should start from GitHub releases. |
