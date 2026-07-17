# Hugging Face Platform Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: Hugging Face Blog

- status: `proposed`
- source_owner: `huggingface`
- source_brand: `huggingface`
- source_scope: `company`
- source_channel: `blog`
- source_url: `https://huggingface.co/blog/feed.xml`
- provenance_tier: `tier0_primary`
- content_tags: `model_release`, `product_update`, `tutorial_or_practice`, `research_paper`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Hugging Face official and community blog posts: model and dataset launches, platform/product updates, applied tutorials and practice guides, and research write-ups from the open-model ecosystem hub.

### Inclusion Reasons

Hugging Face is the central publishing venue of the open-model ecosystem. The blog captures both official platform announcements and ecosystem model/tooling posts, filling a first-party gap in the current catalog.

### Risks / Open Questions

The feed is large (~196 items) and mixes official posts with **community-authored articles**, giving it `medium_noise`. Mitigation: keep `default_limit` small (8–12) and observe the community-article share over the first collection cycles; if noise exceeds expectations, add an official-author filter (or remove from the whitelist) as a follow-up. This observation-and-tightening plan should be tracked against this record.

### Known Overlap

Complements the existing `web_huggingface_daily_papers` node rather than duplicating it: Daily Papers is a community-ranked paper leaderboard (research lane), whereas the blog carries official + community narrative posts (model releases, product updates, tutorials). The two cover different content shapes on the same platform.

### Validation Notes

2026-07-17 feed probe: `https://huggingface.co/blog/feed.xml` returned HTTP 200 with ~196 items (including community articles — high volume), updated as of the probe date; the blog is server-side rendered so detail backfill via `article_extractor` is expected to succeed. 2026-07-17 live `_run` validation: 2/2 entries via the preset's dedicated `div.blog-content` extraction (`hf_blog_content`, 15-17k chars) — `not-prose` widgets (upvote/avatars), empty/numeric anchors and HF's literal SSR hydration marker text nodes (`[0`/`[-1`/`]`) are stripped; body clean head-to-tail.
