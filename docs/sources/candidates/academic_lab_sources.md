# Academic Lab Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: BAIR Blog

- status: `proposed`
- source_owner: `uc_berkeley`
- source_brand: `BAIR`
- source_scope: `research_repository`
- source_channel: `blog`
- source_url: `https://bair.berkeley.edu/blog/feed.xml`
- provenance_tier: `tier0_primary`
- content_tags: `research_paper`
- signal_strength: `medium_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

The Berkeley AI Research (BAIR) lab blog: first-party, long-form write-ups of the lab's own research — accessible explainers of published work across ML, RL, robotics, and NLP.

### Inclusion Reasons

A `tier0_primary` academic first-party source with a durable full-text feed. Low-frequency but high-authority research long-form; complements the existing research lane (HF Daily Papers) with lab-authored narrative context around individual results.

### Risks / Open Questions

Low cadence and academic long-form leans toward the "deep read" end of the reader profile (which weights model/product news above long-form analysis). Admitted under the `incubating` observation window — dropped if it underperforms.

### Known Overlap

May cover papers also surfaced by HF Daily Papers, but as a first-party lab explainer rather than a paper-listing entry.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N): `https://bair.berkeley.edu/blog/feed.xml` returned HTTP 200, 10 entries, active (most recent 2026-07-07); full text carried in `description` (~13K chars/entry). A first 60KB-truncated probe misreported 0 entries; a full re-fetch corrected this. 2026-07-17 live `_run` validation: 2/2 full-text from feed description (21.5-22.4k chars, up to 33 images preserved via feed_content_as_markdown), no detail request. Minor known noise under observation: a stray 'twitter' share-widget word at body head.

# Parking Lot

| Source | URL | Reason |
|---|---|---|
| Google Research Blog | `https://research.google/blog/` | Overlaps with the DeepMind Blog; already recorded from the Google candidate-file perspective — see `google_gemini_antigravity_sources.md` for the Google-side record and overlap handling. |
