# Tier1 Media / Community / Daily Paper Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: 机器之心 Website

- status: `removed`
- source_owner: `jiqizhixin`
- source_brand: `机器之心`
- source_scope: `ai_media`
- source_channel: `website_reader_proxy`
- source_url: `https://www.jiqizhixin.com/`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `research_paper`, `model_release`, `product_update`, `tutorial_or_practice`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `reader_proxy_sitemap`

### Target Coverage

Chinese AI media coverage of model launches, research papers, AI applications, enterprise AI, open-source models, and major vendor updates.

### Inclusion Reasons

机器之心 is a high-recognition Chinese AI media source. The website route is preferable to WeChat for first-pass validation because it is public and does not require authenticated WeChat fetching.

### Risks / Open Questions

The site has broad editorial coverage and can duplicate tier0 official sources. Filtering and deduplication are required before default admission.

### Known Overlap

Overlaps with official vendor sources, arXiv/paper sources, and other Chinese AI media.

### Validation Notes

2026-05-28 recovery pass implemented this as `web_jiqizhixin`. Direct article/RSS HTTP still routes to a data-service page or server error outside a browser, but the public sitemap exposed original article URLs and `r.jina.ai` could render those pages as markdown. The fetcher enumerated recent `jiqizhixin.com/articles/...` URLs from `https://www.jiqizhixin.com/shared/sitemap.xml.gz`, skipped article-library fallbacks, and stored the original article URL plus reader-proxy metadata.

**Removed 2026-06-02 (the audit's WAF read was wrong / transient).** The audit
recorded `web_jiqizhixin` producing **zero** records, attributing it to
`sitemap.xml.gz` itself being behind an Aliyun WAF JS-challenge page. The node was
deleted on structural-unfitness + redundancy grounds.

**Re-investigated then re-discarded 2026-06-16. Do not re-attempt without a rotating egress IP.**
A re-audit first overturned the sitemap finding: `https://www.jiqizhixin.com/shared/sitemap.xml.gz`
*is* reachable over plain httpx — a real gzip sitemap with ~29k `/articles/{date}` URLs
(doubled `http://host/https://host/...` prefix normalized via regex). The article **body**
is JS-rendered into `div.detail__info-body` and a real browser renders it fine (siblings
`detail__progress-wrapper` "0%" / `home__list-wrapper` "展开列表" are UI noise to exclude).
A working fetcher was built (httpx sitemap discovery + reused headless-Chromium
`PlaywrightRenderer` for the body) and verified pulling real recent articles.

It was re-discarded because the blocker is **exit-IP rate-limiting**, not page structure.
机器之心's Aliyun WAF tracks IP reputation: after ~10–20 requests in a session it starts
**302-redirecting everything** from that IP (sitemap, `/rss`, `/articles/...`) to a
`/data-service` landing page — HTTP 200 with no real content. Effects observed: a 10-article
manual run got 8 then the IP flipped; subsequent runs returned the landing page for the
sitemap itself (0 candidates). This is **IP-based, not fingerprint/headless detection** —
confirmed by hitting the flagged IP with httpx, Scrapling `Fetcher` (curl_cffi Chrome *and*
Safari impersonation), plain headless+headful Chromium, and Scrapling `StealthyFetcher`
(patchright stealth + `solve_cloudflare`, which logged `No Cloudflare challenge found` since
it's Aliyun not CF): **all** got the `/data-service` page. No client-side stealth can change
the server's opinion of the IP; only a clean/rotating egress IP would. Not worth the cost —
`web_ithome_ai` + `web_qbitai` already cover Chinese AI media. Nuance for any future attempt:
fingerprint *does* matter at the first gate (curl_cffi passed the WAF before the IP was
flagged where httpx couldn't), so a viable build would need **curl_cffi-grade fingerprinting
for discovery _and_ a residential/rotating proxy** to survive sustained use.

## Source: 量子位 Website

- status: `under_review`
- source_owner: `qbitai`
- source_brand: `量子位`
- source_scope: `ai_media`
- source_channel: `website`
- source_url: `https://www.qbitai.com/`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `model_release`, `product_update`, `research_paper`, `opinion`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Chinese AI news and analysis across model releases, AI companies, applications, research, products, and industry movement.

### Inclusion Reasons

量子位 has large reach and can provide Chinese-language synthesis and fast coverage of domestic and global AI events.

### Risks / Open Questions

Likely duplicate with other tier1 media and official tier0 sources. Headline style and broad coverage may need ranking.

### Known Overlap

Overlaps with 机器之心, 新智元, IT之家 AI, and official vendor sources.

### Validation Notes

Live review confirmed `https://www.qbitai.com/` is reachable and presents as a public website. Validate article metadata extraction later.

## Source: 新智元 Website

- status: `under_review`
- source_owner: `aiera`
- source_brand: `新智元`
- source_scope: `ai_media`
- source_channel: `website`
- source_url: `https://aiera.com.cn/`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `model_release`, `product_update`, `opinion`, `research_paper`
- signal_strength: `medium_signal`
- noise_risk: `high_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Chinese AI media coverage of frontier models, global vendors, AI industry movements, product launches, and AGI/ASI narratives.

### Inclusion Reasons

新智元 can surface fast Chinese summaries and commentary around global AI model/application events.

### Risks / Open Questions

Higher clickbait/interpretation risk than official or more technical sources. Should be filtered and deduplicated aggressively if admitted.

### Known Overlap

Overlaps with all major tier0 vendor sources and Chinese AI media.

### Validation Notes

Implemented as `web_aiera` on 2026-05-28. The RSS feed currently returns a WordPress 500 error, so the implementation uses the public homepage with strict year-prefixed article URL matching and detail extraction.

## Source: IT之家 AI-Related Content

- status: `implemented_core`
- source_owner: `ithome`
- source_brand: `IT之家`
- source_scope: `tech_media`
- source_channel: `website_category`
- source_url: `https://next.ithome.com/ai`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `product_update`, `model_release`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public_website_category`

### Target Coverage

Fast Chinese tech-news coverage of AI model/product launches, domestic AI companies, consumer AI features, and developer/tooling events.

### Inclusion Reasons

IT之家 is fast and broad. It may be useful as a Chinese-language AI product/news firewatch source.

### Risks / Open Questions

The broad RSS is intentionally avoided because it mixes unrelated IT之家 sections. The AI category page is narrower, but still includes both model/product updates and broader AI industry coverage.

### Known Overlap

Overlaps with tier0 official sources, Chinese AI media, and general tech news.

### Validation Notes

Implemented as `web_ithome_ai` on 2026-05-28 using `https://next.ithome.com/ai`. The fetcher parses the category listing HTML (`#list ul.bl > li`) instead of the broad RSS feed, preserving listing timestamps, tags, summaries, and article URLs.

## Source: Hacker News AI Search Feed

- status: `implemented_core`
- source_owner: `ycombinator`
- source_brand: `hacker_news`
- source_scope: `tech_community`
- source_channel: `rss_search_feed`
- source_url: `https://hnrss.org/newest?q=AI&points=10`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `developer_tool`, `product_update`, `opinion`
- signal_strength: `medium_signal`
- noise_risk: `high_noise`
- fetch_reliability: `stable_public`

### Target Coverage

AI-related Hacker News submissions, developer-community discussions, product launches, open-source tools, research links, and market commentary.

### Inclusion Reasons

HN can surface developer-relevant tools and technical discussions faster than media, especially for open-source and infrastructure topics.

### Risks / Open Questions

Search query `AI` is broad and noisy. Needs stricter scoring, minimum points/comments, or a different HNRSS query.

### Known Overlap

Overlaps with official project releases, GitHub releases, and media sources.

### Validation Notes

Audited 2026-06-02. The node was structurally fine (per-submission rows, real
dates, newest-first) but `rss_hn_ai` pulled the raw `https://hnrss.org/newest?q=AI`
firehose, so it was dominated by 0-engagement noise (hiring posts, 0-point
self-promo, weakly-AI-related forum questions). hnrss supports `points`/`comments`
numeric thresholds, so `HackerNewsAiRssFetcher` now applies a configurable
`min_points` (default 10) / `min_comments` (default 0) floor — only community-
upvoted/discussed submissions pass. `min_points=0, min_comments=0` restores the
original unfiltered query. With the default `points=10` the feed collapses to
front-page-worthy AI stories, so the source is admitted (ranking/filtering added
as the doc required).

Same-day follow-up: HN is a link aggregator, not a content platform, so it is
now treated as a discovery source. External-link posts (`link != comments`) keep
title + external URL + discussion URL + community heat (`hn_points` /
`hn_num_comments`) but no body (`has_content=False`); only self-posts
(Ask/Show/Tell HN, `link == comments`) keep the author's text as the body.
External detail fetch is disabled by default (the linked body lives on arbitrary
third-party domains that are slow/unreliable to scrape) but can be re-enabled
per run.

## Source: Hugging Face Daily Papers

- status: `under_review`
- source_owner: `huggingface`
- source_brand: `daily_papers`
- source_scope: `research_community`
- source_channel: `paper_ranking`
- source_url: `https://huggingface.co/papers`
- provenance_tier: `tier1_curated`
- content_tags: `research_paper`, `model_release`, `developer_tool`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Community-ranked AI papers, trending research, model papers, benchmarks, agent papers, and implementation-linked papers.

### Inclusion Reasons

This is more curated than raw arXiv feeds and can surface important papers without ingesting broad paper firehoses.

### Risks / Open Questions

Still paper-heavy and not always directly actionable for model/product tracking. It may need a separate research lane rather than default news lane admission.

### Known Overlap

Overlaps with arXiv sources, vendor research blogs, and tier1 media summaries.

### Validation Notes

Validate whether the Daily Papers page provides stable dates/ranking and whether an API or RSS-like endpoint exists.

Implemented as `web_huggingface_daily_papers` and audited 2026-06-02. It was a `SinglePageDocumentFetcher`, so the whole page (47 paper cards) collapsed into **one** article. The page carries a hydration blob — `<div data-target="DailyPapers" data-props="{…}">` — whose JSON has a `dailyPapers` array; each entry's `paper` object holds `id` (arxiv), `title`, `summary` (abstract), `publishedAt`, `upvotes`, `authors`, `ai_keywords`, `githubRepo`. Rewrote the fetcher to parse that JSON and split per paper: title, abstract as body, `paper.publishedAt` as the date, `https://huggingface.co/papers/{id}` as the URL, sorted newest-first — no per-paper detail requests. Live run: 40 papers (limit), 0 empty dates/bodies, newest-first, upvotes/keywords in raw_data.

## Source: The Decoder

- status: `proposed`
- source_owner: `the_decoder`
- source_brand: `THE DECODER`
- source_scope: `ai_media`
- source_channel: `blog`
- source_url: `https://the-decoder.com/feed/`
- provenance_tier: `tier1_curated`
- content_tags: `market_news`, `model_release`, `product_update`
- signal_strength: `medium_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

English-language AI-focused reporting on model releases, product updates, and industry/market news, written as edited articles rather than raw discussion links.

### Inclusion Reasons

The current tier1 media roster is all Chinese (量子位 / 新智元 / IT之家), leaving English AI vertical media uncovered. The Decoder is the tier1 example named in classification standard v1.1, is AI-focused with no general-tech noise, and provides written reporting.

### Risks / Open Questions

WordPress summary feed → detail must be backfilled via `article_extractor`. As a tier1 curated source it can duplicate tier0 official launches; value is edited synthesis and gap coverage, so it should stay lower priority than direct official sources.

### Known Overlap

Moderate event-level overlap with `rss_hn_ai` (Hacker News AI): HN is a discovery source where most posts carry no body, whereas The Decoder provides finished reporting — complementary rather than duplicative. Also overlaps with tier0 vendor sources on major launches.

### Validation Notes

2026-07-17 feed probe: `https://the-decoder.com/feed/` returned HTTP 200 with 10 items (WordPress summary feed), updated as of the probe date. Detail backfill via `article_extractor`. 2026-07-17 live `_run` validation: feed summaries run 288-530 chars, above the generic 200-char detail trigger — preset raises `default_detail_min_chars` to 1200; with that, 2/2 entries backfill full bodies (2.4-6.6k chars) and the theme's literal template string (`H1 Heading ~ same to H2 in feed`) is stripped. Author byline remains at head (acceptable).

## Source: TestingCatalog

- status: `proposed`
- source_owner: `testingcatalog`
- source_brand: `TestingCatalog`
- source_scope: `ai_media`
- source_channel: `blog`
- source_url: `https://www.testingcatalog.com/rss/`
- provenance_tier: `tier1_curated`
- content_tags: `product_update`, `model_release`
- signal_strength: `high_signal`
- noise_risk: `medium_noise`
- fetch_reliability: `stable_public`

### Target Coverage

Close tracking of AI product feature rollouts, staged/gradual releases, and leaks across ChatGPT / Gemini / Claude and other AI apps — short, factual, product-movement items.

### Inclusion Reasons

Hits the reader profile's second tier ("AI applications / product updates"), which the current catalog covers most weakly. Items are short, factual, and cheap to digest with the existing auto-translate / summarization path.

### Risks / Open Questions

The feed carries **unconfirmed leaks (rumor) content**, giving it `medium_noise`. Removal condition: if noise exceeds expectations during the observation window, remove it from the `ESSENTIAL_FETCHER_IDS` whitelist (keep the fetcher class, move the record to Parking Lot with the reason). Ghost-site summary feed → detail backfill via `article_extractor`.

### Known Overlap

Overlaps with tier0 vendor product-release channels and with The Decoder on product-launch events; distinct value is early/granular rollout and leak coverage the official sources omit.

### Validation Notes

2026-07-17 feed probe: `https://www.testingcatalog.com/rss/` returned HTTP 200 (Ghost summary feed), updated as of the probe date. Detail backfill via `article_extractor` (SSR site). 2026-07-17 live `_run` validation: 2/2 entries with detail backfill (3.0-6.1k chars). Minor known noise: a `Google Preferred Source` badge image at head; acceptable, revisit if it bothers readers.

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| 机器之心 WeChat | WeChat account `机器之心` | Likely high coverage, but requires WeChat credential flow and authenticated scraping. |
| 量子位 WeChat | WeChat account `量子位` | Likely core distribution channel, but requires authenticated WeChat fetching. |
| 新智元 WeChat | WeChat account `AI_era` | Likely core distribution channel, but requires authenticated WeChat fetching and higher noise filtering. |
| IT之家 broad RSS/Homepage | `https://www.ithome.com/rss/` / `https://www.ithome.com/` | Too broad now that `https://next.ithome.com/ai` is available. |
| Hacker News Algolia search | `https://hn.algolia.com/?q=AI` | Useful for manual search/API exploration, but HNRSS is simpler as a first pass. |
| Raw arXiv AI/CL/LG/CV feeds | varies | Primary sources, but too broad for the focused catalog without ranking. |
