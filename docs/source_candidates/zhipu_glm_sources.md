# Zhipu / Z.ai / GLM Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Z.ai New Released

- status: `under_review`
- source_owner: `zai`
- source_brand: `glm`
- source_scope: `model_family`
- source_channel: `docs_release_notes`
- source_url: `https://docs.z.ai/release-notes/new-released`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

GLM model releases, multimodal releases, coding/agent updates, open-source model availability, and developer-platform changes.

### Inclusion Reasons

This is the most focused Z.ai/GLM release-note style source and should be the first source to validate.

### Risks / Open Questions

Need confirm whether the page has sufficient chronological granularity and stable anchors.

### Known Overlap

Overlaps with Z.ai blog and BigModel/Open Platform notices.

### Validation Notes

Existing project notes already parse `docs.z.ai/llms.txt`; compare that index with this release-note page.

## Source: Z.ai Blog

- status: `under_review`
- source_owner: `zai`
- source_brand: `glm`
- source_scope: `model_family`
- source_channel: `blog`
- source_url: `https://z.ai/blog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`, `developer_tool`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `blocked_or_fragile`

### Target Coverage

Narrative launch posts for GLM model releases, coding/reasoning model updates, open-source announcements, and product/app positioning.

### Inclusion Reasons

The blog is likely the richer explanation source when available.

### Risks / Open Questions

Previous project notes indicated `z.ai/blog` was not a reliable source and used docs index parsing instead.

### Known Overlap

Overlaps with Z.ai New Released.

### Validation Notes

Keep under review but do not prioritize implementation unless live validation shows a stable listing.

## Source: BigModel Open Platform

- status: `proposed`
- source_owner: `zhipu`
- source_brand: `bigmodel`
- source_scope: `api_platform`
- source_channel: `docs_console`
- source_url: `https://open.bigmodel.cn/`
- provenance_tier: `tier0_primary`
- content_tags: `api_platform`, `model_release`, `product_update`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

BigModel.cn API model availability, platform capabilities, developer notices, and commercial access changes.

### Inclusion Reasons

Useful for China-region API platform availability if Z.ai release notes do not capture BigModel-specific differences.

### Risks / Open Questions

May require console navigation or be less structured than docs release notes.

### Known Overlap

Overlaps with Z.ai New Released.

### Validation Notes

Treat as a later supplement, not a first-pass source.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Z.ai docs index | `https://docs.z.ai/llms.txt` | Useful machine-readable index; better as implementation support than reviewer-facing source. |
| Z.ai GitHub/HF model pages | varies | Direct model publication surfaces, but defer until model-repository streams are globally evaluated. |
