# Node Catalog, Adaptations & Risks

> ⚠️ **快照日期 2026-06-16**:此后源扩容 wave1–3(v3.2–v3.5,约 20 个新节点,多数 incubating)
> **未包含在本目录中**。节点现势以 `src/fetchers/impl/` 注册表与前端节点管理页为准;
> 本文对已列节点的「适配手法/风险评级」仍然有效。计划在 incubating 转正评审时一并补全(见 `docs/backlog.md`)。

The current built-in fetcher catalog: what each node is, **what special
adaptation it required** to capture clean records, and its **stability risk** —
how likely that adaptation is to break when the upstream source changes.

Risk reflects how much fragile, source-specific coupling a node carries:

- 🟢 **Low** — stable feed/API or plain list page; little custom parsing.
- 🟡 **Medium** — depends on a specific HTML structure / CSS class names / private JSON endpoint / a third-party reader proxy. Works now, but an upstream redesign can break it.
- 🔴 **High** — depends on a browser render or anti-bot bypass, or on a repo/endpoint that can move. Watch these first when something breaks.

For *how* to verify and fix a node, see [node_audit_playbook.md](./node_audit_playbook.md).
For *which* nodes are default-visible and *why*, see [curation_policy.md](./curation_policy.md).

## Registry shape

| Group | Count | Notes |
| --- | ---: | --- |
| Default-visible nodes | 23 | The exact set in `ESSENTIAL_FETCHER_IDS`. |
| Generic advanced fetchers | 4 | Runtime-configured RSS / GitHub releases / GitHub repos / HF models for user-defined source configs. Hidden by default. |
| Historical concrete presets | 0 | Removed from `src/fetchers/impl`, not merely hidden. |

## OpenAI / ChatGPT / Codex

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `rss_openai_news` | `openai.com/news/rss.xml` | Article bodies are behind a Cloudflare Managed Challenge → Playwright renders the detail page, degrading to the RSS summary on failure; strips a stray `Loading…` render placeholder | 🔴 High — browser-render path; breaks if Playwright is unavailable or CF changes |
| `docs_openai_codex_changelog` | `developers.openai.com/codex/changelog` | Per-entry changelog split (one record per release entry) | 🟡 Medium — depends on changelog page structure |

## Anthropic / Claude

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `web_anthropic_news` | `anthropic.com/news` | List page + scoped detail extraction | 🟡 Medium — list/detail selectors |
| `web_claude_blog` | `claude.com/blog` | List page + detail; no special hack needed | 🟢 Low |
| `docs_claude_code_changelog` | `code.claude.com/docs/en/changelog` | Per-version changelog split | 🟡 Medium — depends on changelog structure |

## Google / Gemini / Gemma

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `rss_google_gemini_models` | `blog.google/innovation-and-ai/models-and-research/gemini-models/` | Plain category RSS | 🟢 Low |
| `docs_gemma_release_notes` | `ai.google.dev/gemma/docs/releases` | Google devsite release-notes split by date heading | 🟡 Medium — devsite structure |

(The Google and OpenAI **API** changelogs were intentionally hidden as redundant — each brand's launches are already covered by the news/release nodes above. See the curation history.)

## xAI / Grok

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `docs_xai_release_notes` | `docs.x.ai/developers/release-notes` | Mintlify changelog-grid splitter: per-card date + heading, year inferred from the nearest month `<h2>`, abbreviated-month map | 🟡 Medium — tightly coupled to the Mintlify grid markup |

## Alibaba / Qwen

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `web_qwen_blog` | `qwen.ai/api/v2/article/retrieval` | Reads the site's private JSON API instead of scraping HTML | 🟡 Medium — undocumented API can change shape without notice |

## DeepSeek

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `docs_deepseek_api_changelog` | `api-docs.deepseek.com/updates/` | Docusaurus per-`<h2>` date-heading split; strips zero-width spaces that broke the date regex | 🟡 Medium — Docusaurus structure + ZWSP quirk |
| `github_deepseek_repositories` | `github.com/deepseek-ai` | Backfills a cleaned README excerpt when a repo Description is empty; dedup-gated, `GITHUB_TOKEN`-aware | 🟢 Low — GitHub API is stable; token only affects rate limit |
| `hf_deepseek_models` | `huggingface.co/deepseek-ai` | Model-card metadata records (short bodies are correct for a model-signal node) | 🟢 Low — HF API is stable |

## Zhipu / Z.ai / GLM

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `docs_zai_new_released` | `docs.z.ai/release-notes/new-released` | Release-notes split per entry | 🟡 Medium — release-notes page structure |

## ByteDance Seed

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `web_bytedance_seed_research` | `seed.bytedance.com/en/research` | Parses the JS-SSR research cards (`div.group.relative`): date / title / abstract per publication | 🟡 Medium — depends on front-end class names |

## Tier1 Media / Community / Research Signal

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `web_qbitai` | `qbitai.com` | Detail scoped to `div.content > div.article`, noise selectors decomposed (`.wx_img`/related/hot/footer) | 🟡 Medium — WordPress theme structure |
| `web_aiera` | `aiera.com.cn` | RSS 500s → falls back to homepage with year-prefixed article-URL matching + detail extraction | 🟡 Medium — homepage structure; no working feed |
| `web_ithome_ai` | `next.ithome.com/ai` | Category listing (`#list ul.bl > li`) + detail scoped to `.post_content` | 🟢 Low — stable category page; well-targeted selectors |
| `rss_hn_ai` | `hnrss.org/newest?q=AI` | Configurable `min_points`/`min_comments` threshold to de-noise the firehose; treated as a discovery source (external-link posts keep title+link+heat, no body; only Ask/Show/Tell self-posts keep a body) | 🟡 Medium — depends on hnrss query params |
| `web_huggingface_daily_papers` | `huggingface.co/papers` | Parses the `data-target="DailyPapers"` hydration JSON, one record per paper | 🟡 Medium — depends on the embedded hydration blob |

## Agent Coding Tools

| Source ID | Base URL | Special adaptation | Risk |
| --- | --- | --- | --- |
| `web_cursor_changelog` | `cursor.com/changelog` | Listing pagination (`max_listing_pages` + `_next_listing_page_url`), `drop_empty_content`, nav `exclude_url_patterns` | 🟡 Medium — changelog page + pagination structure |
| `github_opencode_releases` | `github.com/anomalyco/opencode/releases` | Re-pointed from the stale `opencode-ai/opencode` repo | 🔴 High — the canonical repo has moved before and can move again |
| `github_openclaw_releases` | `github.com/openclaw/openclaw/releases` | `default_include_prereleases = False`; `per_page=100` so stable releases aren't starved | 🟢 Low — GitHub API stable |
| `github_hermes_agent_releases` | `github.com/NousResearch/hermes-agent/releases` | Plain GitHub Releases | 🟢 Low — GitHub API stable |

## Generic advanced fetchers (hidden)

| Source ID | Purpose |
| --- | --- |
| `generic_rss` | Runtime-configured RSS/Atom ingestion for user-defined source configs. |
| `generic_github_releases` | Runtime-configured GitHub Releases ingestion. |
| `generic_github_repositories` | Runtime-configured GitHub org/user repository ingestion. |
| `generic_huggingface_models` | Runtime-configured Hugging Face author/org model ingestion. |

## Watch list — most likely to break

Concentrate periodic re-audits here (🔴, then the most coupled 🟡):

- `rss_openai_news` — Playwright render path; fails if the browser is unavailable or CF changes the challenge.
- `github_opencode_releases` — depends on the repo not moving again (it already did once).
- `web_qwen_blog` — private JSON API with no stability guarantee.
- The HTML-structure-coupled nodes (`web_qbitai`, `web_aiera`, `web_bytedance_seed_research`, `web_cursor_changelog`, `docs_xai_release_notes`, `web_huggingface_daily_papers`) all break on an upstream redesign; the playbook's failure-pattern table is the recovery guide.

> Removed node: `web_jiqizhixin` (机器之心) — **do not re-attempt.** Re-investigated
> 2026-06-16 (a working sitemap-discovery + Playwright-body-render fetcher was built and
> verified pulling real articles). It is technically restorable, but was re-discarded
> because 机器之心's Aliyun WAF **rate-limits by exit-IP reputation**: after ~10–20
> requests in a session the WAF starts 302-redirecting *everything* from that IP (sitemap,
> RSS, article pages) to a `/data-service` landing page (HTTP 200, no real content),
> producing intermittent total failures. This is **not** fingerprint/headless detection,
> so no client-side stealth defeats it — verified by testing httpx, curl_cffi
> (Chrome/Safari impersonation), plain headless+headful Chromium, and Scrapling's
> `StealthyFetcher` (patchright stealth + `solve_cloudflare`): once the IP is flagged, all
> of them get the `/data-service` page. The only real lever is changing the egress IP
> (residential/rotating proxy). Not worth the operational cost — Chinese AI media is
> already covered by `web_ithome_ai` + `web_qbitai`. (Fingerprint *does* matter at the
> first gate — curl_cffi passed the WAF before the IP was flagged where httpx didn't — so
> a future attempt would need both curl_cffi-grade fingerprinting **and** a clean rotating IP.)
