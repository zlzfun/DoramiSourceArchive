# DeepSeek Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: DeepSeek API Change Log

- status: `under_review`
- source_owner: `deepseek`
- source_brand: `deepseek`
- source_scope: `api_platform`
- source_channel: `docs_changelog`
- source_url: `https://api-docs.deepseek.com/updates/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

DeepSeek API model releases, model upgrades, API compatibility changes, deprecations, and platform notices.

### Inclusion Reasons

This is the clearest official DeepSeek chronological source and should be the first DeepSeek source to validate.

### Risks / Open Questions

May miss open-source repo/model-card updates if those are published before API changelog entries.

### Known Overlap

Overlaps with DeepSeek GitHub organization and Hugging Face model pages.

### Validation Notes

Existing source gap notes already identify DeepSeek GitHub repo activity as an early signal; compare changelog latency against GitHub activity.

Implemented as `docs_deepseek_api_changelog` and audited on 2026-06-02. The page is a Docusaurus doc (`<article>` container) segmented by `<h2>` date headings (`Date: YYYY-MM-DD`, id `date-...`), each section holding `<h3>` model names + body. The generic single-page fetcher had mashed all 17 releases into one undated blob; the fetcher now subclasses `DevsiteReleaseNotesFetcher` (same h2-date-heading family) and overrides date parsing (`Date:` prefix + ISO) and segmentation (`<article>` container, title built from the section's `<h3>` model names; same-day variants joined). Docusaurus injects zero-width spaces into headings, so `_clean_text` strips them before the date regex. Live run: 17 entries, 0 empty dates, newest-first, 2024-05 → 2026-04, anchored per `<h2>` id.

## Source: DeepSeek GitHub Organization

- status: `under_review`
- source_owner: `deepseek-ai`
- source_brand: `deepseek`
- source_scope: `open_model_family`
- source_channel: `github_repository_activity`
- source_url: `https://github.com/deepseek-ai`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `developer_tool`, `research_paper`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

New DeepSeek repositories, model/tool code releases, and early open-source publication signals.

### Inclusion Reasons

DeepSeek often exposes model or tool signals through GitHub before broader explainers. The project already has a GitHub organization fetcher candidate.

### Risks / Open Questions

Repository creation is not the same as release quality. Ranking and deduplication against API changelog/Hugging Face are needed.

### Known Overlap

Overlaps with Hugging Face organization and API changelog.

### Validation Notes

Prefer GitHub API organization-repository fetching during implementation.

## Source: DeepSeek Hugging Face Organization

- status: `proposed`
- source_owner: `deepseek-ai`
- source_brand: `deepseek`
- source_scope: `open_model_family`
- source_channel: `model_repository`
- source_url: `https://huggingface.co/deepseek-ai`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

DeepSeek public model uploads, model cards, and open-weight availability changes.

### Inclusion Reasons

This is a direct publication surface for open models and can complement GitHub/API changelog.

### Risks / Open Questions

HF org streams may be noisy or duplicate GitHub/API announcements. Decide later whether model-repository org streams belong in the focused catalog.

### Known Overlap

Overlaps with DeepSeek GitHub organization and API changelog.

### Validation Notes

Use only if HF model-release timing proves materially earlier than API changelog or GitHub signals.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| DeepSeek homepage | `https://www.deepseek.com/` | Product/home page, not a reliable chronological source. |
| DeepSeek papers on arXiv | varies | Primary papers are valuable but should be handled through paper-source policy rather than vendor feed. |
