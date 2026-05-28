# OpenAI / GPT / Codex Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: OpenAI News

- status: `implemented_core`
- source_owner: `openai`
- source_brand: `openai`
- source_scope: `company`
- source_channel: `newsroom`
- source_url: `https://openai.com/news/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `api_platform`, `research_paper`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

OpenAI company-level announcements, GPT-family model launches, Codex launches, major ChatGPT product announcements, API/platform milestones, research releases, and safety/system-card announcements.

### Inclusion Reasons

This is the broad official OpenAI source and should remain the primary candidate for model and company-level OpenAI signals.

### Risks / Open Questions

The newsroom mixes product, research, enterprise, policy, and customer-story content. Filtering may be needed to avoid lower-signal business/customer items.

### Known Overlap

Overlaps with OpenAI API Changelog for API model availability, ChatGPT Release Notes for app updates, and Codex Changelog for coding-agent details.

### Validation Notes

Prefer the existing OpenAI RSS feed if it provides sufficient metadata; otherwise validate `openai.com/news` article-list extraction and category filtering.

## Source: OpenAI API Changelog

- status: `under_review`
- source_owner: `openai`
- source_brand: `openai_api`
- source_scope: `api_platform`
- source_channel: `docs_changelog`
- source_url: `https://developers.openai.com/api/docs/changelog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

API model launches, model lifecycle changes, Responses API, tools, Agents SDK, Realtime, pricing-adjacent capability changes, and developer-facing platform updates.

### Inclusion Reasons

This is the most precise official source for OpenAI developer-platform availability and model lifecycle changes.

### Risks / Open Questions

Changelog entries may be dense and operational. Implementation may need date-heading parsing and item compaction.

### Known Overlap

Overlaps with OpenAI News for major launches and Codex Changelog for Codex-related API changes.

### Validation Notes

Implemented as `docs_openai_api_changelog` on 2026-05-28 using the redirected developer docs URL.

## Source: Codex Changelog

- status: `under_review`
- source_owner: `openai`
- source_brand: `codex`
- source_scope: `developer_tool`
- source_channel: `docs_changelog`
- source_url: `https://developers.openai.com/codex/changelog`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`, `model_release`, `api_platform`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Codex app, CLI, IDE extension, web, automations, GitHub integrations, security, sandboxing, and Codex model availability updates.

### Inclusion Reasons

This is the most focused official source for Codex update tracking and should not be folded into the generic OpenAI News source.

### Risks / Open Questions

The source may include small product updates or documentation-level changes. It may need grouping by release/date.

### Known Overlap

Overlaps with OpenAI News for major Codex launches and API Changelog for platform-level model availability.

### Validation Notes

The page is currently available under `developers.openai.com/codex/changelog` and exposes a Codex docs navigation tree plus changelog content.

## Source: ChatGPT Release Notes

- status: `under_review`
- source_owner: `openai`
- source_brand: `chatgpt`
- source_scope: `product_family`
- source_channel: `support_release_notes`
- source_url: `https://help.openai.com/en/articles/6825453-chatgpt-release-notes`
- provenance_tier: `tier0_primary`
- content_tags: `product_update`, `model_release`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

ChatGPT web, desktop, mobile, voice, canvas, projects, GPTs, agent, model-picker, and user-facing availability updates.

### Inclusion Reasons

This source catches user-facing ChatGPT changes that may not appear in the newsroom or API changelog.

### Risks / Open Questions

Support-article structure may require special parsing because many dated entries live on one page. It can also include minor UX changes.

### Known Overlap

Overlaps with OpenAI News for major product launches and API Changelog when model availability changes across both ChatGPT and API.

### Validation Notes

Revisit after first-pass horizontal comparison; may be valuable but lower priority than News, API Changelog, and Codex Changelog.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| OpenAI Developer Blog | `https://developers.openai.com/blog/` | Useful for tutorials and practice, but less direct than API/Codex changelogs for release tracking. |
| OpenAI Cookbook | `https://cookbook.openai.com/` | Strong implementation examples, but tutorial-heavy and not a release/news source. |
| OpenAI Codex GitHub Releases | `https://github.com/openai/codex/releases` | Useful if changelog misses CLI-level releases; defer because official Codex Changelog is more user-readable. |
| OpenAI Models Docs | `https://developers.openai.com/api/docs/models` | Current-state reference, not a chronological update source unless diffed. |
