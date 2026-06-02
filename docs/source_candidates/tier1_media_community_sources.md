# Tier1 Media / Community / Daily Paper Source Candidates

These records are documentation-only. They do not imply implementation or default-catalog admission until a later unified development pass.

# Recommended Review Sources

## Source: 机器之心 Website

- status: `implemented_core`
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

2026-05-28 recovery pass implemented this as `web_jiqizhixin`. Direct article/RSS HTTP still routes to a data-service page or server error outside a browser, but the public sitemap exposes original article URLs and `r.jina.ai` can render those pages as markdown. The fetcher therefore enumerates recent `jiqizhixin.com/articles/...` URLs from `https://www.jiqizhixin.com/shared/sitemap.xml.gz`, skips article-library fallbacks, and stores the original article URL plus reader-proxy metadata.

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

- status: `under_review`
- source_owner: `ycombinator`
- source_brand: `hacker_news`
- source_scope: `tech_community`
- source_channel: `rss_search_feed`
- source_url: `https://hnrss.org/newest?q=AI`
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

Existing project has `rss_hn_ai`; keep it hidden unless ranking/filtering is added.

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

# Parking Lot

| Source | URL | Reason |
| --- | --- | --- |
| 机器之心 WeChat | WeChat account `机器之心` | Likely high coverage, but requires WeChat credential flow and authenticated scraping. |
| 量子位 WeChat | WeChat account `量子位` | Likely core distribution channel, but requires authenticated WeChat fetching. |
| 新智元 WeChat | WeChat account `AI_era` | Likely core distribution channel, but requires authenticated WeChat fetching and higher noise filtering. |
| IT之家 broad RSS/Homepage | `https://www.ithome.com/rss/` / `https://www.ithome.com/` | Too broad now that `https://next.ithome.com/ai` is available. |
| Hacker News Algolia search | `https://hn.algolia.com/?q=AI` | Useful for manual search/API exploration, but HNRSS is simpler as a first pass. |
| Raw arXiv AI/CL/LG/CV feeds | varies | Primary sources, but too broad for the focused catalog without ranking. |
