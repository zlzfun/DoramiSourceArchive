# Apple Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Apple Machine Learning Research

- status: `proposed`
- source_owner: `apple`
- source_brand: `Apple MLR`
- source_scope: `company`
- source_channel: `blog`
- source_url: `https://machinelearning.apple.com/rss.xml`
- provenance_tier: `tier0_primary`
- content_tags: `research_paper`, `model_release`
- signal_strength: `medium_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Apple's first-party machine learning research portal: original write-ups of the company's on-device / foundation-model work and basic research, published by the authors themselves — low-frequency, high-quality technical posts.

### Inclusion Reasons

Fills a real vacuum in the tier0 vendor matrix: Apple is a major AI vendor with **no** first-party source in the current catalog. This is the one-party original channel for Apple's on-device models and foundational research — low cadence but high quality, and it duplicates nobody.

### Risks / Open Questions

Low publishing cadence and research long-form leans toward the "deep read" end of the reader profile (which weights model/product news above long-form analysis). Admitted under the `incubating` observation window — dropped if it underperforms.

### Known Overlap

Papers may also surface via HF Daily Papers or arXiv, but here as a first-party Apple lab write-up rather than a paper-listing entry.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N3): `https://machinelearning.apple.com/rss.xml` returned HTTP 200, 10 entries, summary feed, active (most recent 2026-07-16). Detail策略: detail backfill route (static site, extraction verified at implementation). 2026-07-17 live `_run` validation: feed summaries run ~600 chars, above the generic 200-char detail trigger — preset sets `default_detail_min_chars = 1500` per the wave3 methodology rule (3rd occurrence of this pattern after The Decoder / Lil'Log); with that, 2/2 backfill via `html_selector` (3.8-4.3k chars, images+headings). Minor known noise under observation: a short research-area/content-type meta prefix at body head.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
