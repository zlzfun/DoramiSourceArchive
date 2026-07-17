# NVIDIA Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: NVIDIA GenAI Blog

- status: `proposed`
- source_owner: `nvidia`
- source_brand: `NVIDIA`
- source_scope: `company`
- source_channel: `blog`
- source_url: `https://blogs.nvidia.com/blog/category/generative-ai/feed/`
- provenance_tier: `tier0_primary`
- content_tags: `product_update`, `api_platform`, `tutorial_or_practice`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

NVIDIA's generative-AI blog category: first-party posts on NVIDIA's inference/hardware stack, platform and API updates, developer tooling, and applied practice around the company's AI ecosystem.

### Inclusion Reasons

A `tier0_primary` vendor source occupying a niche no current source covers: the hardware + inference-stack ecosystem viewpoint. First-party product/platform updates from a major AI infrastructure vendor.

### Risks / Open Questions

Corporate-blog marketing lean (hence `medium_noise`). Observation window will watch the marketing-copy share; if the proportion of 营销稿 runs too high, remove it from the catalog (keep the fetcher class, move the record to Parking Lot with the reason). Admitted under the `incubating` observation window.

### Known Overlap

May overlap with tier0 vendor product-release channels and tier1 media on major AI-hardware/platform events; distinct value is NVIDIA's first-party framing of its own inference/hardware stack.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N3): `https://blogs.nvidia.com/blog/category/generative-ai/feed/` returned HTTP 200, 9 entries, active; `content:encoded` carries full text. Detail策略: full-text feed route (`feed_content_as_markdown`), no detail request. 2026-07-17 live `_run` validation: 2/2 full-text from feed (6.8-7.1k chars, headings preserved via feed_content_as_markdown), no detail request. Body images absent from feed content (acceptable; revisit if reader feedback asks).

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
