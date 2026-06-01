# Google / Gemini / Antigravity Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Google Blog Gemini Models

- status: `implemented_core`
- source_owner: `google`
- source_brand: `gemini`
- source_scope: `model_family`
- source_channel: `blog_category_rss`
- source_url: `https://blog.google/innovation-and-ai/models-and-research/gemini-models/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public_rss`

### Target Coverage

Gemini-family model announcements and model capability updates, including Pro, Flash, multimodal, reasoning, image, audio, robotics, and other Gemini model variants.

### Inclusion Reasons

This is the most focused Google Blog category for Gemini model announcements. It should be considered ahead of the broader Google Blog AI page.

### Risks / Open Questions

Gemma may not be fully covered here because it has separate open-model documentation and release notes.

### Known Overlap

Overlaps with Google DeepMind News and Gemini API release notes for model launches.

### Validation Notes

Implemented as `rss_google_gemini_models` on 2026-05-28 using the category RSS endpoint: `https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/`.

## Source: Gemini API Release Notes

- status: `under_review`
- source_owner: `google`
- source_brand: `gemini_api`
- source_scope: `api_platform`
- source_channel: `docs_changelog`
- source_url: `https://ai.google.dev/gemini-api/docs/changelog`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `api_platform`, `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Gemini API model releases, model deprecations, API capability changes, AI Studio updates, managed agents, Antigravity Agent availability, pricing or billing-related API changes, and developer-facing platform updates.

### Inclusion Reasons

This is the most precise official source for Gemini API availability and lifecycle changes. It captures details that broader blogs may omit.

### Risks / Open Questions

It is a changelog-style page, so minor operational updates may create noise. The source may need entry-level parsing by date and bullet item.

### Known Overlap

Overlaps with Google DeepMind News and Gemini Models for major model launches, and with Developer Tools blog for developer-facing announcements.

### Validation Notes

Implemented as `docs_gemini_api_changelog` (devsite date-heading splitter; 121 dated records). Removed from the default catalog on 2026-06-02: the changelog is operational/noisy (the "Risks" above) and overlaps with `rss_google_gemini_models` for the launches that matter, so it was judged redundant rather than low-value. The fetcher class was deleted; the shared `DevsiteReleaseNotesFetcher` base is kept (Gemma still uses it). Note `docs_gemma_release_notes` was deliberately **kept** — it is low-frequency but every entry is a real open-model release, and it is Google's only dedicated open-model source. Restore from git history if a dedicated Gemini API changelog node is wanted again.

## Source: Gemma Release Notes

- status: `under_review`
- source_owner: `google`
- source_brand: `gemma`
- source_scope: `open_model_family`
- source_channel: `docs_release_notes`
- source_url: `https://ai.google.dev/gemma/docs/releases`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `api_platform`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Gemma open-model family releases, updates, variant releases, and related developer availability changes.

### Inclusion Reasons

This is the most direct focused source for Gemma release tracking and avoids relying only on broader Google or DeepMind blog posts.

### Risks / Open Questions

Release notes may be updated in-place and require diff-style parsing if individual entries lack article URLs.

### Known Overlap

Overlaps with Google DeepMind News, Gemini API release notes, and Google Blog AI when Gemma launches are announced broadly.

### Validation Notes

Validate whether release entries have dates, headings, and stable anchors.

## Source: Google Antigravity Blog

- status: `under_review`
- source_owner: `google`
- source_brand: `antigravity`
- source_scope: `developer_tool`
- source_channel: `blog`
- source_url: `https://antigravity.google/blog`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`, `api_platform`, `tutorial_or_practice`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Google Antigravity product announcements, version updates, agentic IDE capabilities, Gemini model integrations, coding-agent workflows, and Antigravity-specific developer updates.

### Inclusion Reasons

This is the most focused official source for Antigravity as an AI application and developer tool.

### Risks / Open Questions

The public page may be client-rendered or sparse depending on implementation. It may need embedded-data parsing.

### Known Overlap

Overlaps with Google DeepMind News, Google Blog Developer Tools, and Gemini API release notes for large Antigravity announcements.

### Validation Notes

Validate whether listing entries and article detail pages are accessible through standard HTTP fetching.

# Parking Lot

## Source: Google DeepMind News

- status: `proposed`
- source_owner: `google_deepmind`
- source_brand: `google_deepmind`
- source_scope: `ai_lab`
- source_channel: `newsroom_blog`
- source_url: `https://deepmind.google/blog/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `research_paper`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Google DeepMind model and research updates, including Gemini, Gemma, Veo, Imagen, Genie, Gemini Robotics, and other frontier model families.

### Inclusion Reasons

This is a broad official source for Google DeepMind model and research announcements.

### Risks / Open Questions

Placed in parking lot because it is broad and overlaps with more focused Gemini Models, Gemma Release Notes, and Gemini API Release Notes candidates.

### Known Overlap

Likely overlaps with Google Blog AI, Google Blog Gemini Models, Gemini API release notes, and Gemma release docs.

### Validation Notes

Revisit if focused sources miss important DeepMind model families such as Veo, Imagen, Genie, Robotics, or Alpha-series science models.

## Source: Google Blog AI

- status: `proposed`
- source_owner: `google`
- source_brand: `google_ai`
- source_scope: `ai_portfolio`
- source_channel: `blog_category`
- source_url: `https://blog.google/innovation-and-ai/technology/ai/`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `api_platform`, `market_news`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Broad Google AI announcements across models, Gemini app, developer tools, AI Studio, Google Labs, Search AI features, and product integrations.

### Inclusion Reasons

Useful as a high-level official umbrella source for Google AI updates.

### Risks / Open Questions

Placed in parking lot because the scope is broad and the duplicate risk is high against more focused sources.

### Known Overlap

Likely overlaps heavily with Google DeepMind News, Gemini Models, Gemini App, and Developer Tools category pages.

### Validation Notes

Revisit as a gap-coverage source if focused Google sources miss too many important announcements.

## Source: Google Blog Developer Tools

- status: `proposed`
- source_owner: `google`
- source_brand: `google_developer_tools`
- source_scope: `developer_tools`
- source_channel: `blog_category`
- source_url: `https://blog.google/innovation-and-ai/technology/developers-tools/`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `api_platform`, `product_update`, `tutorial_or_practice`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Google AI developer-tool updates, including Antigravity, AI Studio, Gemini API, coding agents, developer workflow features, and I/O developer announcements.

### Inclusion Reasons

This source can capture application/developer-tool narratives that may not be present in API changelogs or model release pages.

### Risks / Open Questions

The page may include developer tools outside the desired Gemini/Antigravity focus, so filtering is likely required.

### Known Overlap

Overlaps with Gemini API release notes, Gemini CLI GitHub releases, and Antigravity Blog.

### Validation Notes

Review whether the category page can be filtered by title, product, or article tags before admission.

## Source: Gemini App Release Notes

- status: `proposed`
- source_owner: `google`
- source_brand: `gemini`
- source_scope: `product_family`
- source_channel: `release_notes`
- source_url: `https://gemini.google/release-notes/`
- provenance_tier: `tier0_primary`
- content_tags: `product_update`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

User-facing Gemini app updates, feature releases, app behavior changes, and product improvements.

### Inclusion Reasons

This source may capture Gemini application changes that do not appear in model/API sources.

### Risks / Open Questions

It may not cover developer-tool updates such as Gemini CLI or Antigravity. Structure may require special parsing if many release entries live on a single page.

### Known Overlap

May overlap with Google Blog AI and Gemini app blog-category posts.

### Validation Notes

Review if the page has stable dates and individual entries suitable for archival ingestion.

## Source: Gemini CLI GitHub Releases

- status: `proposed`
- source_owner: `google-gemini`
- source_brand: `gemini_cli`
- source_scope: `developer_tool`
- source_channel: `github_release`
- source_url: `https://github.com/google-gemini/gemini-cli/releases`
- provenance_tier: `tier0_primary`
- content_tags: `developer_tool`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Gemini CLI release versions, feature changes, bug fixes, preview and stable releases, and developer workflow updates.

### Inclusion Reasons

This is the most direct source for Gemini CLI version-level tracking.

### Risks / Open Questions

GitHub releases can be frequent and include small patch updates. It may require compaction or filtering to avoid high update volume.

### Known Overlap

May overlap with Google Blog Developer Tools for larger Gemini CLI announcements.

### Validation Notes

The GitHub Releases API should be preferred over scraping the HTML page during implementation.
