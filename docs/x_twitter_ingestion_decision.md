# X/Twitter Ingestion Decision Note

Date: 2026-05-12

## Context

AIHot relies heavily on X/Twitter accounts for timely AI product, research, and ecosystem signals.
DoramiSourceArchive should eventually cover this source family, but X/Twitter is materially different from RSS, official web pages, WeChat, and GitHub:

- Access may require paid API plans or authenticated sessions.
- Browser scraping is fragile and can create account risk.
- Third-party mirrors can disappear or violate provenance/compliance expectations.
- Tweet content often needs deduplication, thread handling, quote/repost filtering, media metadata, and author identity normalization.

Therefore, X/Twitter fetchers must not be implemented until an ingestion strategy is explicitly selected.

## Candidate Account Groups

These candidates are tracked in `docs/source_catalog.md` and are inspired by AIHot's public source mix.

| Group | Examples | Priority |
| --- | --- | --- |
| Official labs and model providers | OpenAI, AnthropicAI, claudeai, GoogleDeepMind, GeminiApp, MistralAI, Alibaba_Cloud, TencentHunyuan, MiniMax_AI, Kimi_Moonshot | High |
| Developer/platform channels | OpenAIDevs, googleaidevs, OpenRouter, Replit, Cursor, RunwayML, LumaLabsAI, PixVerse_ | High |
| Research and engineering organizations | MSFTResearch, EpochAIResearch | Medium |
| Individuals/KOLs | sama, karpathy, fchollet, swyx, SimonW, dotey, vista8, op7418, berryxia, shao__meng | Medium |

## Options

### Option A: Official X API

Use X API credentials to fetch user timelines and selected fields.

Pros:
- Most legitimate and stable when API access is available.
- Better structured data: IDs, timestamps, authors, media, metrics, conversation/thread IDs.
- Easier to do incremental cursors correctly.

Cons:
- May require paid access.
- Rate limits and plan limitations may constrain coverage.
- Requires secrets management and deployment configuration.

Implementation shape:
- Add `XPostContent`.
- Add `BaseXTimelineFetcher` with bearer token configuration through environment variables.
- Concrete built-in subclasses define account handle/user ID and filtering defaults.
- Store tweet ID as stable content ID and use `SourceStateRecord.last_cursor_value` as since-id.

Risk level: Low-to-medium if paid API access is acceptable.

### Option B: Browser/session scraping

Use a logged-in browser session to read public timelines.

Pros:
- Can work without official API plan.
- May capture what a human can see.

Cons:
- Fragile DOM and anti-bot behavior.
- Account lock/rate-limit risk.
- Hard to run reliably in unattended server jobs.
- More difficult to prove provenance and retry safely.

Implementation shape:
- Requires browser automation and session storage.
- Should be limited to manual or supervised runs at first.
- Needs very conservative rate limiting.

Risk level: High.

### Option C: Third-party mirrors or scraping services

Fetch from Nitter-like mirrors, paid scraping APIs, or other proxy services.

Pros:
- Fast to integrate if a reliable provider is chosen.
- Can avoid holding X credentials locally.

Cons:
- Reliability varies sharply.
- Compliance/provenance concerns.
- Paid services add vendor dependency.
- Mirror availability changes frequently.

Implementation shape:
- Add provider-specific fetcher after choosing a provider.
- Treat provider as a first-class source dependency in docs and health checks.

Risk level: Medium-to-high.

### Option D: Webhook/import bridge first

Do not crawl X/Twitter directly inside DoramiSourceArchive. Instead, expose an inbound import endpoint or file import format, and let an external collector push normalized posts into the archive.

Pros:
- Lowest immediate account/compliance risk inside this project.
- Lets DoramiSourceArchive focus on archive, dedupe, health, and Dify/RAG delivery.
- Works with any external collector the user chooses later.
- Useful for other private/authenticated channels too.

Cons:
- Requires a separate collector outside this repo.
- Not a complete "built-in fetcher" by itself.
- Needs a normalized schema and idempotent import behavior.

Implementation shape:
- Add `SocialPostContent` or `XPostContent`.
- Add `/api/import/social-posts` with batch idempotent writes.
- Add optional JSONL import for offline dumps.
- Add docs for expected payload format.

Risk level: Low.

## Recommendation

Recommended near-term path:

1. Implement Option D first as a safe ingestion bridge.
2. Keep X/Twitter account candidates in `docs/source_catalog.md`.
3. If official X API credentials are available and acceptable, implement Option A afterward.
4. Avoid browser/session scraping as a default production path.

This preserves the product direction: DoramiSourceArchive remains the archive and source hub, while avoiding premature account/API risk.

## Decision Status

Current status: Pending user decision.

No X/Twitter crawler/fetcher implementation should be merged until the user chooses one of:

- `official_x_api`
- `browser_session_scraping`
- `third_party_provider`
- `webhook_import_bridge`

The recommended default is `webhook_import_bridge`.
