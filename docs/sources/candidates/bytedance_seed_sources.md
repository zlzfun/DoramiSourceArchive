# ByteDance / Seed / Seedance Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: ByteDance Seed Blog

- status: `under_review`
- source_owner: `bytedance_seed`
- source_brand: `seed`
- source_scope: `model_family`
- source_channel: `blog`
- source_url: `https://seed.bytedance.com/en/blog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Seed model announcements, Seedance video-model updates, Seedream/image, voice, robotics, world-model, and applied research/product release posts.

### Inclusion Reasons

This is the cleanest official narrative source for ByteDance Seed model/product announcements.

### Risks / Open Questions

Validate whether `/en/blog` exposes a stable listing or if only individual blog URLs are easily discoverable.

### Known Overlap

Overlaps with Seed Models and Seed Research pages.

### Validation Notes

Live review confirmed `https://seed.bytedance.com/en/blog` is reachable and individual posts are available under `/en/blog/...`.

## Source: ByteDance Seed Models

- status: `under_review`
- source_owner: `bytedance_seed`
- source_brand: `seed`
- source_scope: `model_family`
- source_channel: `model_catalog`
- source_url: `https://seed.bytedance.com/en/models`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `research_paper`
- signal_strength: `medium_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Current Seed model catalog across Seedance, voice, robotics, multimodal, and other core model products.

### Inclusion Reasons

Good for mapping model families and current availability, especially outside text-only LLMs.

### Risks / Open Questions

It is a catalog rather than a chronological update source, so it may require diffing.

### Known Overlap

Overlaps with Seed Blog and Seed Research.

### Validation Notes

Use as a supplement or diff source unless it exposes update dates.

**Dropped on 2026-06-02.** Audit confirmed `web_bytedance_seed_models`
(`https://seed.bytedance.com/en/models`) is a static model catalog (Seed2.0 /
Seed1.8 / Seed1.6 … with descriptions) — no dates, no chronology, every fetch
yields the same single reference blob with `publish_date` falling back to fetch
time, failing the "primary chronological content with real publish dates"
standard (same verdict shape as `docs_xai_models`). Model-launch signal is better
served by the dated Seed Research publications and the Seed Blog. Deleted
`ByteDanceSeedModelsFetcher` and removed `web_bytedance_seed_models` from
`ESSENTIAL_FETCHER_IDS` (delete-the-class, per the registry invariant). Restore
from git history if a catalog diff source is wanted later.

## Source: ByteDance Seed Research

- status: `proposed`
- source_owner: `bytedance_seed`
- source_brand: `seed`
- source_scope: `research_lab`
- source_channel: `research_index`
- source_url: `https://seed.bytedance.com/en/research`
- provenance_tier: `tier0_primary`
- content_tags: `research_paper`, `model_release`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Seed research papers, technical reports, model cards, and research-driven releases.

### Inclusion Reasons

Important for research-backed releases and technical reports, including Seedance papers.

### Risks / Open Questions

May be too research-heavy for the core product/model update catalog.

### Known Overlap

Overlaps with Seed Blog for launches and arXiv for papers.

### Validation Notes

Existing project notes described Seed static output as sparse; revisit after blog/model pages are validated.

Implemented as `web_bytedance_seed_research` and audited on 2026-06-02. The page is JS-rendered but SSRs the Publications cards, so pure httpx parses them. Each paper is a `div.group.relative` card holding a date div (`Apr 22, 2026`), a title div (its direct text is the title), and a `div[class*="markdown"]` abstract (duplicated across responsive breakpoints — take the first). The generic single-page fetcher had mashed all paper titles into one undated 20k-char blob; the fetcher now splits per card with a date/title/abstract extractor (`_release_entries` / `_parse_pub_date`). Static HTML carries no per-paper link, so `source_url` falls back to the listing page. Live run: 6 publications, 0 empty dates, newest-first, real dates 2025-08 → 2026-04, each with a ~0.7–1.4k-char abstract body.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Volcengine Ark docs | `https://www.volcengine.com/product/ark` | Important API platform, but broad and commercial-platform heavy; needs separate Chinese cloud-platform pass. |
| Doubao app site | `https://www.doubao.com/` | Product surface, not a clear chronological update source. |
| Jimeng / Dreamina product pages | varies | Relevant for Seedance applications, but product-surface updates may duplicate Seed Blog. |
