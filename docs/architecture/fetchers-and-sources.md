# 抓取器与来源:注册发现、网页后端、配置源与运行跟踪

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:[`sources/curation_policy.md`](../sources/curation_policy.md) · [`sources/node_audit_playbook.md`](../sources/node_audit_playbook.md) · [`archive/crawl4ai-feasibility.md`](../archive/crawl4ai-feasibility.md)

## Plugin-based fetcher discovery

`FetcherRegistry` (`src/fetchers/registry.py`) auto-scans `src/fetchers/impl/` for `BaseFetcher` subclasses at import time. Any new fetcher placed there with `source_id`, `content_type`, `name`, `icon`, and `description` class attributes is automatically registered and surfaced in the frontend. The frontend dynamically renders parameter forms based on `get_parameter_schema()`, whose return format is:
```python
[{"field": "limit", "label": "单次获取上限", "type": "number", "default": 5}]
```

Three fetcher base classes cover the major source types:
- `BaseFetcher` — base for all fetchers; provides `_safe_get`/`_safe_post` with retries
- `BaseWebPageListFetcher` (`webpage_fetcher.py`) — scrapes an HTML listing page; subclasses declare `listing_url`, `article_url_patterns`, and optionally set `fetch_detail=True` to extract article body from the detail page. Optional knobs: `drop_empty_content=True` discards entries with no body (nav/footer junk), and `max_listing_pages` + a `_next_listing_page_url()` override paginate the listing (e.g. Cursor's `/changelog/page/N`) to accumulate enough entries for `limit`
- `GenericGitHubReleasesFetcher` (`github_release_fetcher.py`) — hits the GitHub Releases API; `PresetGitHubReleasesFetcher` subclasses hard-code `owner`/`repo` as built-in sources

## Optional crawl4ai Web Content backend (`src/fetchers/web_content/`)

A `WebContentBackend` abstraction (`backend.py`) unifies "given an article URL → clean body". Two impls: `LegacyArticleExtractorBackend` (httpx, the default/baseline) and `Crawl4AIContentBackend` (headless-browser via the optional `crawl4ai` extra — **not** a default dep). The crawl4ai backend renders + scopes by a per-site `CrawlProfile` (`profiles.py`: `target_elements`/`excluded_selector`/`wait_for`), then runs the project's own `node_to_markdown` over `cleaned_html` (consistent image/lazy-load handling between both paths); it also exposes `render_html()` (raw rendered DOM, anchors intact — used by C-class single-page-split fetchers' segmenter fallback and OpenAI's Cloudflare path) and `extract(url, profile=...)` (explicit profile injection). It's strictly opt-in: a fetcher sets `web_backend_enabled=True`, `BaseFetcher.fetch()` lazily starts/stops the browser, and `_web_backend_detail()` routes detail extraction through it — **falling back to legacy httpx whenever crawl4ai is absent / no profile matches / extraction fails**. So the default environment (no crawl4ai installed) behaves exactly as before. Migrated B-class detail nodes (`web_anthropic_news`, `web_ithome_ai`, `web_qbitai`, `web_claude_blog`; `web_aiera` 自 issue #79 起改走 WordPress REST 列表接口取正文,不再经浏览器后端,其 `aiera-article` Profile 已删) and `rss_openai_news` (crawl4ai-first CF bypass, Playwright fallback, summary last) use it.

## Config-driven web fetcher (`generic_web`, the "中级目标")

`ConfigurableWebFetcher` (`configurable_web_fetcher.py`) is the single, config-driven web fetcher — the `GenericRssFetcher` analogue for web sources. Adding a new website = writing a `SourceConfigRecord` (config), not a new Python subclass. It reads all source identity/config from runtime params (`listing_url`, `article_url_patterns`, detail `CrawlProfile`, optional `listing_css` CSS schema, governance metadata) and delegates discovery to `BaseWebPageListFetcher`'s heuristics (anchor + embedded-JSON), with the optional CSS schema as a precise fallback. `resolve_source_fetcher_id` routes `source_type` web/webpage → `generic_web` (rss/atom → `generic_rss`); `POST /api/source-configs/fetch-active-web` batch-triggers active web sources. Validated to reproduce existing dedicated nodes (discovery URL-set identical, detail byte-identical when given the same profile). **Frontend entry is gated off by the `is_template` flag** (all five `generic_*` fetchers carry `is_template = True`; `App.jsx` filters them from the node catalog — decided 2026-07: generic parameter-driven nodes stay backend-only as source-configs/source_builder execution substrate and as code templates; the sanctioned way to add a source is a hardened preset fetcher).

## AI node onboarding (`source_builder`, the "高级目标")

`src/services/source_builder.py` turns an arbitrary listing-page URL into a固化 config node: `analyze_url()` detects page type (rss/web/json), collects HTML structural signals, produces a heuristic baseline config, then (when LLM is configured via `daily_brief.resolve_llm_config`) refines it via LLM and analyzes a sample article page to propose the detail `CrawlProfile`; `preview_config()` trial-runs `generic_web`/`generic_rss` for a no-persist sample. Endpoints `POST /api/source-builder/analyze|preview` (collector-gated); save reuses `POST /api/source-configs`. LLM/crawl4ai are both optional (graceful degrade to heuristic/legacy). **Frontend entry (`CustomNodeBuilder.jsx` + the FetchTab "AI 自定义节点" panel) is currently gated off** via `ENABLE_CUSTOM_NODE_BUILDER=false` — backend-only for now.

## Playwright browser-rendered detail (Cloudflare bypass)

Most fetchers are pure httpx, but a few sources gate their article bodies behind a Cloudflare Managed Challenge that only a real browser can pass (httpx gets a 403 challenge shell). `src/fetchers/impl/playwright_renderer.py` provides `PlaywrightRenderer`, an async context manager that lazily launches a headless Chromium for the duration of one fetch run, then renders each blocked article: it opens a fresh page per article, throttles requests, polls until the challenge clears and the body text appears, retries, and returns `""` on any failure so the caller degrades gracefully. Currently only `OpenAINewsRssFetcher` uses it — it overrides `_detail_for_url` to prefer the rendered body and fall back to the RSS summary when rendering fails (`openai.com` is the one audited source behind this challenge). Playwright is an opt-in path: when a node needs no detail fetch, no browser is started. (Note: the legacy WeChat Official Account Playwright login fetcher has been removed; only the `WechatArticleContent` type and the `wechat_article` display label remain for historical archived data.)

## Fetch run tracking

Every fetcher execution (manual or scheduled) writes a `FetchRunRecord` and upserts a `SourceStateRecord`. The state record is the authoritative health/cursor store per source; `build_fetcher_health_from_state()` in `app.py` derives the `/api/source-health` response from it, falling back to aggregating raw `FetchRunRecord` rows when no state exists.
