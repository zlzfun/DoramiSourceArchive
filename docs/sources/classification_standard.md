# Source Classification Standard v1.1

This document defines the first source-classification standard for the data-source refinement project.

The goal is to separate source identity, source admission, and source implementation. A source should first be described by identity metadata, then classified by provenance, content value, noise risk, and fetch reliability. Only then should it be considered for the focused default catalog.

## 0. Source Identity Metadata

Identity metadata answers: who publishes this source, what brand or topic does it represent, and through which channel is it delivered?

These fields are descriptive, not decisive. They help group, filter, and reason about sources, but they do not determine admission by themselves. Values should remain broad and practical; avoid overfitting them to one company or one source family.

| Field | Meaning | Examples |
| --- | --- | --- |
| `source_owner` | Organization, person, platform, or account that publishes or operates the source. | `anthropic`, `jiqizhixin`, `arxiv`, `anthropics`, `sama` |
| `source_brand` | Public-facing brand, product family, account name, or community name. | `claude`, `claude_code`, `机器之心`, `arxiv`, `Sam Altman` |
| `source_scope` | Broad coverage area of the source. | `company`, `product_family`, `developer_tool`, `ai_media`, `research_repository`, `personal_commentary` |
| `source_channel` | Delivery channel or source format. | `newsroom`, `blog`, `docs`, `changelog`, `support_release_notes`, `github_release`, `wechat_official_account`, `paper_index`, `x_account` |
| `source_url` | Base URL, API endpoint, feed URL, or account identifier used to locate the source. | `https://www.anthropic.com/news`, `https://claude.com/blog`, GitHub repo URL, WeChat account identifier |

Identity fields may be approximate or partially empty when the source does not map cleanly to all fields.

Examples:

| Source | `source_owner` | `source_brand` | `source_scope` | `source_channel` |
| --- | --- | --- | --- | --- |
| Anthropic News | `anthropic` | `anthropic` | `company` | `newsroom` |
| Claude Blog | `anthropic` | `claude` | `product_family` | `blog` |
| Claude Code Changelog | `anthropic` | `claude_code` | `developer_tool` | `changelog` |
| 机器之心 WeChat | `jiqizhixin` | `机器之心` | `ai_media` | `wechat_official_account` |
| arXiv cs.AI | `arxiv` | `arxiv` | `research_repository` | `paper_index` |
| Claude Code GitHub Releases | `anthropics` | `claude_code` | `developer_tool` | `github_release` |
| Sam Altman X account | `sama` | `Sam Altman` | `personal_commentary` | `x_account` |

## 1. Provenance Tier

The primary axis is how close the source is to the original event or artifact.

| Tier | Name | Definition | Examples |
| --- | --- | --- | --- |
| `tier0_primary` | Primary / direct sources | Official or first-party sources, or original artifacts published by the creator. | Company newsrooms, official blogs, official docs, release notes, changelogs, GitHub releases, GitHub repository activity, arXiv paper pages, model or dataset pages published by the author. |
| `tier1_curated` | Curated / editorial sources | Established media, platforms, communities, or public accounts that summarize, report, rank, or explain primary-source events for an audience. | IT之家 AI news, 机器之心, 量子位, InfoQ, The Decoder, Hacker News AI discussions, Hugging Face Daily Papers. |
| `tier2_personal_social` | Personal / social commentary | Individual commentary, social posts, personal blogs, KOL threads, and fragmented forum discussion. | X/Twitter accounts, personal blogs, Substack commentary, Reddit threads, individual analyst posts. |

Tier answers: how close is this source to the original fact?

Tier does not answer: how useful, stable, or important is this source?

## 2. Content Type

Each source should declare one or more content-type tags. These tags explain why the source is useful and how downstream filtering should treat it.

Recommended tags:

| Tag | Meaning |
| --- | --- |
| `model_release` | New model launch, model upgrade, model retirement, model availability change. |
| `product_update` | Product capability, app, workflow, or user-facing feature update. |
| `api_platform` | API, SDK, developer platform, pricing, rate limit, infrastructure, or platform capability update. |
| `research_paper` | Original paper, preprint, technical report, benchmark, or system card. |
| `developer_tool` | Coding agent, CLI, IDE, framework, release notes, changelog, or developer workflow tool. |
| `market_news` | Business, funding, partnerships, adoption, regulation, or industry movement. |
| `opinion` | Interpretation, critique, personal analysis, predictions, or subjective commentary. |
| `tutorial_or_practice` | Best practices, implementation guides, case studies, or applied workflow examples. |

## 3. Signal Strength

Signal strength estimates how likely the source is to produce items worth reading or archiving.

| Level | Definition |
| --- | --- |
| `high_signal` | Most items are directly relevant to model, product, AI application, agent, or developer-platform tracking. |
| `medium_signal` | Some items are valuable, but the source needs filtering by topic, keyword, category, or deduplication. |
| `low_signal` | Useful only for long-tail discovery or occasional manual search; should not enter default schedules. |

Signal strength is separate from provenance. A primary GitHub release stream can be noisy; a curated media source can still surface high-value summaries.

## 4. Noise And Duplication Risk

Noise risk estimates the operational cost of ingesting the source.

| Level | Definition |
| --- | --- |
| `low_noise` | Narrow scope, little duplication, stable titles and metadata. |
| `medium_noise` | Some irrelevant or repeated items; requires filtering or ranking. |
| `high_noise` | Broad firehose, heavy duplication, clickbait, mixed topics, or many low-value updates. |

Tier1 sources often duplicate tier0 sources. Their main value is explanation, localization, aggregation, or gap coverage, so they should usually be lower priority than direct official sources.

## 5. Fetch Reliability

Fetch reliability describes whether the source can be collected safely and repeatedly.

| Level | Definition |
| --- | --- |
| `stable_public` | Public source that works with standard HTTP/API fetching and can run unattended. |
| `needs_auth` | Requires login, API key, cookie, token, or account-bound credentials. |
| `blocked_or_fragile` | Publicly visible but blocked, rate-limited, client-rendered, unstable, or likely to break under automation. |
| `external_import_only` | Should be ingested through a webhook/import bridge rather than direct fetching. |

High-value sources with weak fetch reliability should not be admitted to the default catalog until the access strategy is explicit.

## 6. Admission Workflow

The focused default catalog starts from zero and grows additively.

Recommended order:

1. Admit `tier0_primary + high_signal + stable_public` sources first.
2. Add selected `tier0_primary + medium_signal` sources when their noise can be controlled.
3. Add a small number of `tier1_curated` sources only for gap coverage, localization, or synthesis value.
4. Keep `tier2_personal_social` out of built-in direct fetching until there is a vetted list and a safe import or collection strategy.

Every admitted source should record:

- identity metadata when available
- provenance tier
- content-type tags
- signal strength
- noise/duplication risk
- fetch reliability
- reason for admission
- reason for exclusion, if rejected or postponed

## 7. Anthropic Example

| Source | Classification |
| --- | --- |
| Anthropic News | `source_owner=anthropic`, `source_brand=anthropic`, `source_scope=company`, `source_channel=newsroom`, `tier0_primary`, `model_release`, `product_update`, `high_signal`, `low_noise`, `stable_public` |
| Claude Blog | `source_owner=anthropic`, `source_brand=claude`, `source_scope=product_family`, `source_channel=blog`, `tier0_primary`, `product_update`, `developer_tool`, `tutorial_or_practice`, `high_signal`, `medium_noise`, `stable_public` |
| Claude Code Changelog | `source_owner=anthropic`, `source_brand=claude_code`, `source_scope=developer_tool`, `source_channel=changelog`, `tier0_primary`, `developer_tool`, `product_update`, `high_signal`, `medium_noise`, `stable_public` |
| Claude Apps Release Notes | `source_owner=anthropic`, `source_brand=claude`, `source_scope=product_family`, `source_channel=support_release_notes`, `tier0_primary`, `product_update`, `medium_signal`, `medium_noise`, `stable_public` |

## 8. Tier1 Media Example

| Source | Classification |
| --- | --- |
| 机器之心 WeChat | `source_owner=jiqizhixin`, `source_brand=机器之心`, `source_scope=ai_media`, `source_channel=wechat_official_account`, `tier1_curated`, `market_news`, `research_paper`, `product_update`, `tutorial_or_practice`, `medium_signal`, `medium_noise`, `needs_auth` |
