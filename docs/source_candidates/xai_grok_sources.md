# xAI / Grok Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: xAI News

- status: `under_review`
- source_owner: `xai`
- source_brand: `grok`
- source_scope: `company_product_family`
- source_channel: `newsroom`
- source_url: `https://x.ai/news`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `api_platform`, `developer_tool`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Grok model launches, Grok Build, Grok Imagine, Grok Voice, API products, enterprise/government announcements, and major xAI product updates.

### Inclusion Reasons

This is the best official xAI chronological source and now exposes a readable list of posts including Grok model, API, voice, image/video, and coding-agent updates.

### Risks / Open Questions

Past fetch attempts saw access issues; revalidate current HTTP accessibility before implementation.

### Known Overlap

Overlaps with xAI docs release notes and model docs for API-level changes.

### Validation Notes

The page includes a current all-posts list and should be tested with the project’s fetcher headers.

## Source: xAI Developer Release Notes

- status: `under_review`
- source_owner: `xai`
- source_brand: `xai_api`
- source_scope: `api_platform`
- source_channel: `docs_changelog`
- source_url: `https://docs.x.ai/developers/release-notes`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

xAI API model availability, model retirements, Grok API capability changes, Voice/Imagine API changes, migration notices, and developer-facing release notes.

### Inclusion Reasons

This should be the most precise xAI developer-platform update source if the release-notes page has stable entries.

### Risks / Open Questions

The page is valid and exposes dated release-note sections. Implementation still needs date-heading and item parsing.

### Known Overlap

Overlaps with xAI News for major launches.

### Validation Notes

Live review confirmed dated release notes including Grok Build, Custom Voices, Voice API, image/video generation, and model/API updates.

## Source: xAI Models Docs

- status: `proposed`
- source_owner: `xai`
- source_brand: `grok`
- source_scope: `api_platform`
- source_channel: `docs_reference`
- source_url: `https://docs.x.ai/developers/models`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`
- signal_strength: `medium_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Current Grok model catalog, Grok Build, Grok Imagine, Grok Voice API, pricing/context information, and model selection guidance.

### Inclusion Reasons

Good current-state source for model availability and API surfaces.

### Risks / Open Questions

It is a reference page rather than a chronological update source, so it may require diffing.

### Known Overlap

Overlaps with xAI News and Developer Release Notes.

### Validation Notes

Use as a supplement or diff source, not a first-pass standalone feed.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Grok product site | `https://grok.com/` | Product surface, not a clean update source. |
| xAI X account | `https://x.com/xai` | High-signal but tier2/social and subject to X ingestion strategy. |
| Grok X account | `https://x.com/grok` | High-signal but tier2/social and not suitable for direct built-in fetching yet. |
| xAI Status | `https://status.x.ai/` | Operational status, not product/model news. |
