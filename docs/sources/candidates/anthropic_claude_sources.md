# Anthropic / Claude Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Anthropic News

- status: `under_review`
- source_owner: `anthropic`
- source_brand: `anthropic`
- source_scope: `company`
- source_channel: `newsroom`
- source_url: `https://www.anthropic.com/news`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `api_platform`, `research_paper`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Anthropic company-level announcements, including new model releases and updates such as Opus, Sonnet, or future named model families; major Claude platform announcements; safety, research, enterprise, and API-relevant official news.

### Inclusion Reasons

This is the most direct official source for Anthropic model and company announcements. It should be considered the primary candidate for model-release tracking under the Anthropic owner.

### Risks / Open Questions

Some product-specific Claude updates may live on Claude-owned channels rather than the Anthropic newsroom. Article extraction quality should be validated before implementation.

### Known Overlap

May overlap with Claude Blog for Claude product announcements and with support release notes for user-facing Claude app changes.

### Validation Notes

Review whether the listing includes enough metadata for title, date, summary, and article URL extraction. Confirm whether category filtering is needed to prioritize model and product updates.

## Source: Claude Blog

- status: `under_review`
- source_owner: `anthropic`
- source_brand: `claude`
- source_scope: `product_family`
- source_channel: `blog`
- source_url: `https://claude.com/blog`
- provenance_tier: `tier0_primary`
- content_tags: `product_update`, `developer_tool`, `tutorial_or_practice`, `api_platform`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Claude product-family updates, Claude Code updates surfaced through Claude blog posts, agent workflows, applied Claude usage, product practice, and developer-facing Claude announcements.

### Inclusion Reasons

This source complements Anthropic News by focusing on Claude as a product family. It is likely to carry practical updates that do not rise to company-newsroom level.

### Risks / Open Questions

It may include tutorial or practice content that is useful but less urgent than release announcements. Filtering may be needed by category or title keywords.

### Known Overlap

May overlap with Anthropic News for major launches and with Claude Code Changelog for developer-tool updates.

### Validation Notes

Review available categories and whether Claude Code content can be filtered or tagged without losing broader Claude product updates.

## Source: Claude Code Changelog

- status: `under_review`
- source_owner: `anthropic`
- source_brand: `claude_code`
- source_scope: `developer_tool`
- source_channel: `changelog`
- source_url: `https://code.claude.com/docs/en/changelog`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Version-level Claude Code changes, including CLI features, permissions, agent behavior, integrations, bug fixes, security changes, and developer workflow updates.

### Inclusion Reasons

This is the most precise source for Claude Code update tracking. It should be treated separately from Anthropic News because it carries detailed tool-level changes.

### Risks / Open Questions

The source may be high-frequency and include low-impact patch notes. Ranking or compaction may be needed so minor fixes do not dominate the feed.

### Known Overlap

May overlap with Claude Blog when major Claude Code capabilities are announced in narrative form.

### Validation Notes

Confirm whether the docs page or its backing source is easiest to fetch and parse. Preserve version/date structure if implemented.

# Parking Lot

## Source: Claude Apps Release Notes

- status: `proposed`
- source_owner: `anthropic`
- source_brand: `claude`
- source_scope: `product_family`
- source_channel: `support_release_notes`
- source_url: `https://support.claude.com/en/articles/12138966-release-notes`
- provenance_tier: `tier0_primary`
- content_tags: `product_update`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Claude app, Claude desktop, Claude mobile, and user-facing product release notes.

### Inclusion Reasons

This source may catch user-facing Claude product changes that are too small for Anthropic News or Claude Blog.

### Risks / Open Questions

Support article structure may be less suitable for article-by-article ingestion. It may need special parsing if multiple release-note entries live in a single page.

### Known Overlap

May overlap with Claude Blog and Anthropic News for major product launches.

### Validation Notes

Review whether it should become an independent source, a detail supplement, or remain manual reference only.
