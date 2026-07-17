# Mistral Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Mistral AI News

- status: `proposed`
- source_owner: `mistral`
- source_brand: `mistral`
- source_scope: `company`
- source_channel: `newsroom`
- source_url: `https://mistral.ai/rss.xml`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `api_platform`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Mistral company-level announcements: open-weight and commercial model releases (Mistral / Magistral / Mixtral families), product and platform updates, and API/developer-platform changes.

### Inclusion Reasons

Mistral is the representative European frontier lab and the current vendor matrix has no Mistral coverage at all. Its open-weight model releases are high-value to readers, and Mistral is explicitly named in the demand-side calibration (Folo Chinese-reader subscription structure) alongside OpenAI/Anthropic official blogs.

### Risks / Open Questions

The feed carries summaries only; detail must be backfilled via `fetch_detail` through `article_extractor`, with a `CrawlProfile` (B-class methodology) as a fallback if extraction quality is insufficient. Note that `/feed.xml` and `/news/feed.xml` both return 404 — `https://mistral.ai/rss.xml` is the authoritative feed.

### Known Overlap

May overlap with tier1 AI media reporting on Mistral launches, but as the sole first-party Mistral source there is no tier0 duplication.

### Validation Notes

2026-07-17 feed probe: `https://mistral.ai/rss.xml` returned HTTP 200. Sibling paths `https://mistral.ai/feed.xml` and `https://mistral.ai/news/feed.xml` both returned 404, so the `rss.xml` path is authoritative. 2026-07-17 live `_run` validation: 2/2 entries with detail backfill (6.7-9.0k chars). Head/tail noise fixed 2026-07-17 (2nd pass): dedicated `_detail_for_url` override picks the longest direct child of `<article>` and strips `mistral-atom-*` custom elements (share tooltips, Thinking Summary card, hero header, progress bar); re-fetched clean.

# Parking Lot

| Source | URL | Reason |
|---|---|---|
| Mistral docs / changelog | `https://docs.mistral.ai/` | Bulletin-shape (changelog) candidate, not yet feed-validated; deferred as a dynamic-shape follow-up once the News node is stable. |
