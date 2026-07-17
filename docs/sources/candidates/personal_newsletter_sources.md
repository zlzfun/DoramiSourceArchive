# Personal / Newsletter Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

## Vetted List Rationale

Classification standard §6.4 keeps `tier2_personal_social` out of built-in direct fetching "until there is a vetted list and a safe import or collection strategy." This file is that **first vetted list** for the tier. The sources admitted here are deliberately narrow: they are individual publications distributed through **standard RSS/Atom feeds** — public, `stable_public`, collectable unattended with plain HTTP — which is categorically different from the fragmented social content (X/Twitter threads, Reddit posts, KOL fragments) that §6.4 means to keep out. Each is a durable personal blog / newsletter with stable titles and dates, not an account firehose.

Selection also follows the demand-side calibration in `docs/analysis/source-expansion-plan.md` §1.1: the reader profile (fixed by the `daily_brief` interest scoring) weights model/capability releases and AI application/product news above long-form analysis, and the Folo Chinese-reader subscription survey identifies 阮一峰·科技爱好者周刊 as the highest-recurrence personal source. Accordingly the recommended set is trimmed to a small pilot — one Chinese greatest-common-denominator weekly plus two high-recognition English full-text feeds that the existing translate + AI-summary path can digest at near-zero fetch cost — with the remaining long-form English analysts held in the Parking Lot as a second batch pending the pilot's reception.

The second batch (4 sources — Interconnects / Ahead of AI / One Useful Thing / Lil'Log) was started early on 2026-07-17: although the first-batch pilot's observation window is still short, the risk is contained by the `incubating` observation-window mechanism (an underperforming source is simply dropped).

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

## Source: Interconnects

- status: `proposed`
- source_owner: `nathan_lambert`
- source_brand: `Interconnects`
- source_scope: `personal_commentary`
- source_channel: `newsletter`
- source_url: `https://www.interconnects.ai/feed`
- provenance_tier: `tier2_personal_social`
- content_tags: `opinion`, `research_paper`, `model_release`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Deep RLHF / open-model analysis from Nathan Lambert (Ai2): post-training methods, open-weight model releases, and industry-direction commentary written from a hands-on research angle.

### Inclusion Reasons

Among the most-cited individual voices on RLHF and the open-model ecosystem; a durable Substack with stable titles/dates. The `/feed` endpoint carries full text at zero fetch cost, so the existing translate + AI-summary path can digest it directly.

### Risks / Open Questions

Long-form English analysis leans toward the "deep read" end of the reader profile (news/release preference). Admitted as part of the Newsletter second batch under the `incubating` observation window — dropped if it underperforms.

### Known Overlap

Overlaps with other AI-engineering commentary sources on major open-model launches, but from a post-training / research-methods angle rather than a news-report angle.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N): `https://www.interconnects.ai/feed` returned HTTP 200, Substack `content:encoded` **full-text**, active as of the build date. Full-text feed, no detail fetch; `feed_content_as_markdown=True` at implementation. 2026-07-17 live `_run` validation: 2/2 full-text from feed (8.4-12.6k chars, inline links preserved via feed_content_as_markdown), no detail request.

## Source: Ahead of AI

- status: `proposed`
- source_owner: `sebastian_raschka`
- source_brand: `Ahead of AI`
- source_scope: `personal_commentary`
- source_channel: `newsletter`
- source_url: `https://magazine.sebastianraschka.com/feed`
- provenance_tier: `tier2_personal_social`
- content_tags: `research_paper`, `tutorial_or_practice`, `opinion`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Sebastian Raschka's newsletter: paper walkthroughs, LLM implementation-and-training tutorials, and reasoned commentary on research trends, aimed at practitioners who build.

### Inclusion Reasons

A high-recognition English full-text feed pairing research coverage with hands-on tutorials; Substack `content:encoded` delivers full text at zero fetch cost, digestible by the existing translate + AI-summary path.

### Risks / Open Questions

Long-form English analysis leans toward the "deep read" end of the reader profile. Admitted as part of the Newsletter second batch under the `incubating` observation window — dropped if it underperforms.

### Known Overlap

May cover the same papers surfaced by HF Daily Papers, but as a curated walkthrough / tutorial rather than a paper-listing.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N): `https://magazine.sebastianraschka.com/feed` returned HTTP 200, Substack `content:encoded` **full-text**, active. Full-text feed, no detail fetch; `feed_content_as_markdown=True` at implementation. 2026-07-17 live `_run` validation: 2/2 full-text from feed (9.9-51.3k chars, up to 32 images preserved), no detail request.

## Source: One Useful Thing

- status: `proposed`
- source_owner: `ethan_mollick`
- source_brand: `One Useful Thing`
- source_scope: `personal_commentary`
- source_channel: `newsletter`
- source_url: `https://www.oneusefulthing.org/feed`
- provenance_tier: `tier2_personal_social`
- content_tags: `opinion`, `tutorial_or_practice`, `product_update`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Ethan Mollick's newsletter on the practical use of AI: applied experiments with frontier models, workflow guidance, and grounded commentary on what the tools can and can't do.

### Inclusion Reasons

A high-recognition English full-text feed close to the reader profile's applied/product interest; Substack `content:encoded` delivers full text at zero fetch cost, digestible by the existing translate + AI-summary path.

### Risks / Open Questions

Long-form English essays lean toward the "deep read" end of the reader profile. Admitted as part of the Newsletter second batch under the `incubating` observation window — dropped if it underperforms.

### Known Overlap

May reference the same model/product launches as vendor sources, but from an applied-use / hands-on angle.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N): `https://www.oneusefulthing.org/feed` returned HTTP 200, Substack `content:encoded` **full-text**, active. Full-text feed, no detail fetch; `feed_content_as_markdown=True` at implementation. 2026-07-17 live `_run` validation: 2/2 full-text from feed (10.3-12.3k chars, images preserved), no detail request.

## Source: Lil'Log

- status: `proposed`
- source_owner: `lilian_weng`
- source_brand: `Lil'Log`
- source_scope: `personal_commentary`
- source_channel: `blog`
- source_url: `https://lilianweng.github.io/index.xml`
- provenance_tier: `tier2_personal_social`
- content_tags: `research_paper`, `tutorial_or_practice`
- signal_strength: `high_signal`
- noise_risk: `low_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Lilian Weng's technical blog: in-depth, survey-style explainers on core ML/LLM topics (agents, hallucination, RL, diffusion, etc.), each a long reference-grade post.

### Inclusion Reasons

One of the most-referenced individual technical blogs for deep explainers; low-frequency but high durability (`stable_public` GitHub Pages). Complements the research lane with reference-grade long-form.

### Risks / Open Questions

Low cadence (recent post 2026-07-04) and the Hugo `index.xml` is a **summary-only feed**, so full body needs a detail backfill — extraction quality to be verified at implementation (GitHub Pages static site expected to extract cleanly). Long-form leans toward the "deep read" end of the reader profile. Admitted under the `incubating` observation window.

### Known Overlap

Reference explainers may cover topics also treated by paper sources, but as a synthesized survey rather than a single-paper item.

### Validation Notes

2026-07-17 feed probe (wave3 plan 轨道 N): `https://lilianweng.github.io/index.xml` returned HTTP 200, 40 entries, **summary feed** (Hugo `index.xml`), most recent 2026-07-04 (low-frequency long-form). **Detail backfill route** (summary feed → detail fetch for full body); extraction quality to be verified at implementation. 2026-07-17 live `_run` validation: feed summaries run 500-900 chars, above the generic 200-char detail trigger — preset raises `default_detail_min_chars` to 1500 (same fix as The Decoder); with that, 2/2 backfill full bodies via `json_ld` (34-40k chars, 40k = hard cap). Structure-loss fixed same day (user spot-check): the generic path hit the page's json_ld articleBody (plain text, no images/headings) — dedicated `_detail_for_url` override now extracts Hugo PaperMod's `div.post-content` via node_to_markdown (verified 16-18 images, 13-14 headings per post); re-fetched clean. Survey confirmed no other detail-backfilling source hits the json_ld path. Follow-up same day: in-body <table> elements were being scattered word-by-word by node_to_markdown's recursive fallback — fixed globally with a GFM-table branch (benefits every markdown-converting source); re-fetched again clean.

# Parking Lot

| Source | URL | Reason |
|---|---|---|
| Chip Huyen | `https://huyenchip.com/feed.xml` | 2026-07-17 re-probe: feed HTTP 200 but **most recent article 2025-01, dormant ~18 months**. Not admitted; revisit if updates resume. |
