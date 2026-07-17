# Personal / Newsletter Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

## Vetted List Rationale

Classification standard §6.4 keeps `tier2_personal_social` out of built-in direct fetching "until there is a vetted list and a safe import or collection strategy." This file is that **first vetted list** for the tier. The sources admitted here are deliberately narrow: they are individual publications distributed through **standard RSS/Atom feeds** — public, `stable_public`, collectable unattended with plain HTTP — which is categorically different from the fragmented social content (X/Twitter threads, Reddit posts, KOL fragments) that §6.4 means to keep out. Each is a durable personal blog / newsletter with stable titles and dates, not an account firehose.

Selection also follows the demand-side calibration in `docs/analysis/source-expansion-plan.md` §1.1: the reader profile (fixed by the `daily_brief` interest scoring) weights model/capability releases and AI application/product news above long-form analysis, and the Folo Chinese-reader subscription survey identifies 阮一峰·科技爱好者周刊 as the highest-recurrence personal source. Accordingly the recommended set is trimmed to a small pilot — one Chinese greatest-common-denominator weekly plus two high-recognition English full-text feeds that the existing translate + AI-summary path can digest at near-zero fetch cost — with the remaining long-form English analysts held in the Parking Lot as a second batch pending the pilot's reception.

# Recommended Review Sources

## Source: 阮一峰 · 科技爱好者周刊

- status: `proposed`
- source_owner: `ruanyifeng`
- source_brand: `科技爱好者周刊`
- source_scope: `personal_commentary`
- source_channel: `blog`
- source_url: `https://www.ruanyifeng.com/blog/atom.xml`
- provenance_tier: `tier2_personal_social`
- content_tags: `market_news`, `developer_tool`, `tutorial_or_practice`, `opinion`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Weekly Chinese tech digest with high recent AI density: tools, industry news, developer resources, and commentary curated into a single weekly issue.

### Inclusion Reasons

The greatest common denominator among Chinese technical readers (highest recurrence in Folo Chinese subscription lists). The weekly-digest form is inherently "already curated by someone else," giving low-noise aggregation; Chinese-native, so no translation cost.

### Risks / Open Questions

Broad tech scope rather than pure AI (accepted — each weekly issue spans many topics and readers skim the non-AI sections; this breadth is exactly what makes the format popular).

### Known Overlap

Low tier0 overlap; may occasionally reference the same launches as vendor sources but from a digest/commentary angle.

### Validation Notes

2026-07-17 feed probe: `https://www.ruanyifeng.com/blog/atom.xml` returned HTTP 200, Atom **full-text** content; weekly cadence, most recent 2026-07-10. Full-text feed, no detail fetch. 2026-07-17 live `_run` validation: 2/2 entries full-text from feed (5.5-6.2k chars), no detail request issued; weekly cadence confirmed. 2026-07-17 2nd pass: feed HTML now converted via `node_to_markdown` (`feed_content_as_markdown`) — headings/inline links/images preserved (36 images, 84 links in one issue) instead of get_text flattening.

## Source: Simon Willison's Weblog

- status: `proposed`
- source_owner: `simonwillison`
- source_brand: `Simon Willison`
- source_scope: `personal_commentary`
- source_channel: `blog`
- source_url: `https://simonwillison.net/atom/entries/`
- provenance_tier: `tier2_personal_social`
- content_tags: `developer_tool`, `tutorial_or_practice`, `opinion`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Long-form posts on LLM application and tooling practice, developer workflows, and applied-AI commentary from one of the most-cited individual blogs in the space.

### Inclusion Reasons

Among the most-referenced personal blogs on LLM application/tooling. The `/atom/entries/` feed carries full text at zero fetch cost.

### Risks / Open Questions

The **entries** feed (`/atom/entries/`) is the deliberate choice over the site's "everything" feed: everything mixes in short blogmark/quote items that would add low-value noise, whereas `/atom/entries/` restricts to full articles. The everything feed probed HTTP 200; `/atom/entries/` is same-domain/same-structure and should be re-verified at implementation.

### Known Overlap

May cover the same LLM tooling launches as vendor developer-tool sources, but from a hands-on practitioner angle.

### Validation Notes

2026-07-17 feed probe: the "everything" feed returned HTTP 200; the selected `https://simonwillison.net/atom/entries/` is same-domain/same-structure Atom with full text, to be re-verified at implementation. Full-text feed, no detail fetch. 2026-07-17 live `_run` validation: 2/2 entries full-text from the `/atom/entries/` feed (3.9-14.4k chars), no detail request issued.

## Source: Latent Space

- status: `proposed`
- source_owner: `latent_space`
- source_brand: `Latent Space`
- source_scope: `personal_commentary`
- source_channel: `newsletter`
- source_url: `https://www.latent.space/feed`
- provenance_tier: `tier2_personal_social`
- content_tags: `opinion`, `market_news`, `tutorial_or_practice`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Leading AI-engineering newsletter/podcast: interviews, industry analysis, and long-form show notes for podcast episodes.

### Inclusion Reasons

A top newsletter/podcast in the AI-engineering community; interviews and analysis (podcast show notes are also full-length written pieces). Substack `content:encoded` provides full text at zero fetch cost.

### Risks / Open Questions

Long-form English analysis leans toward the "deep read" end of the reader profile; paired here with the Chinese weekly and Simon Willison as a small pilot, with reception to be reviewed before a second Newsletter batch.

### Known Overlap

Overlaps with other AI-engineering commentary and industry-analysis sources on major events.

### Validation Notes

2026-07-17 feed probe: `https://www.latent.space/feed` returned HTTP 200, Substack `content:encoded` **full-text**, updated as of the probe date. Full-text feed, no detail fetch. 2026-07-17 live `_run` validation: 2/2 entries full-text from feed (5.5-16.1k chars), no detail request issued. Note: the feed also carries [AINews] daily-digest posts, raising cadence; low semantic clash with our own daily brief, keep under observation.

# Parking Lot

| Source | URL | Reason |
|---|---|---|
| Interconnects (Nathan Lambert) | `https://www.interconnects.ai/feed` | Full-text feed (HTTP 200); deep RLHF/open-model analysis, but long-form English analysis leans away from the reader profile's news preference — Newsletter **second batch**, pending P2 pilot reception. |
| BAIR Blog | `https://bair.berkeley.edu/blog/feed.xml` | Full-text feed (HTTP 200); low-frequency academic long-form — research lane currently covered by HF Daily Papers, second-batch candidate. |
| Sebastian Raschka | `https://magazine.sebastianraschka.com/feed` | Feed HTTP 200; Newsletter **second batch**, pending P2 pilot reception. |
| One Useful Thing (Ethan Mollick) | `https://www.oneusefulthing.org/feed` | Feed HTTP 200; Newsletter **second batch**, pending P2 pilot reception. |
| Lilian Weng | `https://lilianweng.github.io/index.xml` | Feed HTTP 200; Newsletter **second batch**, pending P2 pilot reception. |
| Chip Huyen | `https://huyenchip.com/feed.xml` | Feed HTTP 200; Newsletter **second batch**, pending P2 pilot reception. |
