# Alibaba / Qwen / Model Studio Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Qwen Blog

- status: `under_review`
- source_owner: `alibaba`
- source_brand: `qwen`
- source_scope: `model_family`
- source_channel: `blog`
- source_url: `https://qwen.ai/blog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`, `developer_tool`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `blocked_or_fragile`

### Target Coverage

Qwen model-family announcements, including Qwen text, VL, image, code, reasoning, open-weight, and agent-oriented releases.

### Inclusion Reasons

This is the most direct Qwen brand source and should be the first Alibaba/Qwen candidate to validate.

### Risks / Open Questions

The public page appears client-rendered and may need embedded-data or API parsing. Existing project notes already use Qwen page-config extraction.

### Known Overlap

Overlaps with Alibaba Cloud Model Studio announcements for commercial API availability and with GitHub/Hugging Face for open model publication.

### Validation Notes

Validate the existing `qwen.ai` page-config approach before implementation.

## Source: Alibaba Cloud Model Studio Model Announcements

- status: `under_review`
- source_owner: `alibaba_cloud`
- source_brand: `model_studio`
- source_scope: `api_platform`
- source_channel: `docs_release_notes`
- source_url: `https://www.alibabacloud.com/help/en/model-studio/model-announcements`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Qwen commercial model availability, model snapshots, regional rollout, pricing or availability announcements, Model Studio/Bailian model update tables, and API-facing model lifecycle changes.

### Inclusion Reasons

This is the strongest structured source for Alibaba Cloud Model Studio/Bailian model availability and has explicit model-update tables.

### Risks / Open Questions

It may include regional platform details and pricing notices that are lower priority than model capability releases.

### Known Overlap

Overlaps with Qwen Blog for major Qwen launches and with Qwen GitHub/Hugging Face for open-source model publication.

### Validation Notes

The page exposes dated model-update rows and should be feasible to parse if table structure remains stable.

## Source: Qwen Code GitHub Releases

- status: `proposed`
- source_owner: `qwenlm`
- source_brand: `qwen_code`
- source_scope: `developer_tool`
- source_channel: `github_release`
- source_url: `https://github.com/QwenLM/qwen-code/releases`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Qwen Code CLI or coding-agent release versions, feature changes, provider/auth changes, and developer workflow updates.

### Inclusion Reasons

This is the most direct release stream for Qwen’s coding-agent/tooling layer.

### Risks / Open Questions

GitHub releases may be frequent and tool-specific; keep as proposed until Qwen Code is confirmed as a priority comparable to Codex, Claude Code, Cursor, or Antigravity.

### Known Overlap

May overlap with Qwen Blog if major Qwen Code launches are announced there.

### Validation Notes

Prefer GitHub Releases API during implementation.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| Qwen Research | `https://qwen.ai/research/` | Valuable for technical reports but may overlap with Qwen Blog and arXiv/model-card sources. |
| QwenLM GitHub org | `https://github.com/QwenLM` | Useful for open-source repo activity, but too broad for first-pass default candidates. |
| Qwen Hugging Face org | `https://huggingface.co/Qwen` | Direct model publication signal; defer until we decide whether HF org streams belong in the focused catalog. |
| Qwen Chat / Tongyi app | `https://chat.qwen.ai/` | Product surface, not a clear chronological update source. |
| Alibaba Cloud Bailian Chinese docs | `https://help.aliyun.com/zh/model-studio/` | Likely more complete for China-region users, but duplicate with English Model Studio announcements and may require separate parsing. |
