# Source Admission Workflow

This document defines the add-only source admission workflow.

## Rule

Do not modify frontend or backend implementation when considering a new source.

Before implementation, each source must first be recorded as a written candidate using the fields from [Source Classification Standard v1.1](./classification_standard.md).

Use two-stage curation:

1. Apply a first-pass reduction during vendor or product-family analysis so reviewers are not given a flat list of every possible URL.
2. Make final default-catalog decisions only after multiple vendors and product families have been compared horizontally.

The first pass should reduce confusion; it should not permanently reject useful sources too early.

## Vendor-Level Candidate Limit

For each vendor or product family, present at most 3-5 recommended review sources.

Prefer one source from each of these lanes when available:

| Lane | Purpose |
| --- | --- |
| Company / model official news | Major model releases, flagship capability changes, company-level AI announcements. |
| Product / application updates | User-facing AI product, app, workspace, coding tool, or agent platform changes. |
| Developer / API / release notes | API availability, model lifecycle, SDK/CLI/changelog, pricing, limits, and developer tooling changes. |

When a source is real but not recommended for immediate review, place it in a `Parking Lot` section instead of the main candidate list.

Use parking lot for:

- broad umbrella pages with high overlap
- narrow sources that duplicate a better source
- fragile or unvalidated pages
- low-priority user-facing release notes
- high-frequency patch streams that may need compaction

Each parking-lot source should still have a short reason so it can be revisited later.

## Steps

1. Create or update a candidate record under `docs/sources/candidates/`.
2. Split sources into `Recommended Review Sources` and `Parking Lot` before writing detailed records.
3. Fill identity metadata:
   - `source_owner`
   - `source_brand`
   - `source_scope`
   - `source_channel`
   - `source_url`
4. Fill classification metadata:
   - `provenance_tier`
   - `content_tags`
   - `signal_strength`
   - `noise_risk`
   - `fetch_reliability`
5. Write the admission notes:
   - target coverage
   - inclusion reasons
   - exclusion or postponement risks
   - known overlap with existing candidates
   - validation questions
6. Keep the candidate in documentation until a later unified development pass.

## Candidate Status

Use these status values:

| Status | Meaning |
| --- | --- |
| `proposed` | Candidate has been identified but not fully reviewed. |
| `under_review` | Candidate is being checked for scope, quality, overlap, and fetch reliability. |
| `accepted_for_build` | Candidate should be implemented in the next unified development pass. |
| `postponed` | Candidate is valuable but blocked by reliability, scope, or priority concerns. |
| `rejected` | Candidate should not be implemented under the current strategy. |

## Template

```markdown
# Recommended Review Sources

## Source: <display name>

- status:
- source_owner:
- source_brand:
- source_scope:
- source_channel:
- source_url:
- provenance_tier:
- content_tags:
- signal_strength:
- noise_risk:
- fetch_reliability:

### Target Coverage

### Inclusion Reasons

### Risks / Open Questions

### Known Overlap

### Validation Notes

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
```
