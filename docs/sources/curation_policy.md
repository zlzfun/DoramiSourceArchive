# Source Curation Policy

This document defines the default source catalog policy for DoramiSourceArchive.

## Goal

Keep the default collector catalog small and high-signal. The first-screen "focused" catalog grows only when a source is deliberately admitted into the additive whitelist. Historical concrete preset sources should be removed from code when they are not part of the admitted batch; the system should not rely on hiding old preset nodes as the long-term cleanup strategy.

## Selection Rules

A source can be default-visible only if it meets most of these rules:

- Official or near-official source from a top AI model, product, or developer platform.
- Directly useful for tracking new models, AI applications, agent products, or developer-facing AI capabilities.
- Low duplicate rate against another default-visible source.
- Stable enough to run unattended with the current fetcher.
- Broad enough to matter beyond a narrow vertical, unless it represents a category-defining AI application.

Sources stay outside the focused catalog when they are:

- Generic extension entry points.
- Import bridge endpoints rather than built-in inbound content sources.
- Future candidates that have not yet been recorded, validated, and admitted.

Historical concrete preset nodes, workflow trigger nodes, broad firehoses, framework/infrastructure release streams, and unvalidated WeChat presets are removed from the built-in fetcher implementation in this branch.

## Incubation (观察期)

Every newly admitted source batch starts in the `incubating` fetcher category, regardless of its target category (official/media/community/...). While incubating:

- The source is default-visible, subscribable, and manually fetchable — but **excluded from the daily auto-collection job** (`scripts/ensure_daily_collection_job.py` excludes the `incubating` category alongside `advanced`/`workflow`).
- The 节点管理 board shows an 「观察」 badge on incubating nodes for focused review.
- Promotion (转正): after fetch-quality review over a few manual/observed runs (body cleanliness, dates/order, dedup, no boilerplate leakage — see `node_audit_playbook.md`), change the class `category` back to the target category noted in its code comment, update the category snapshot assertion in `tests/test_rss_fetcher.py`, and re-run `ensure_daily_collection_job.py` to admit it into the daily schedule.
- Demotion: a source that fails review is removed from `ESSENTIAL_FETCHER_IDS` and recorded in its candidate file's Parking Lot with the reason (the class may stay for a later retry).

## Default Essential Sources

The current default-visible catalog is the admitted first additive batch. Each source should keep the v1.0 dimensions in code metadata: source owner, brand, scope, channel, base URL, provenance tier, content tags, signal strength, noise risk, and fetch reliability.

| Source ID | Candidate Reason |
| --- | --- |
| `rss_openai_news` | OpenAI official model, product, Codex, and agent news. |
| `docs_openai_codex_changelog` | Codex CLI, IDE, agent, and automation changelog. |
| `web_anthropic_news` | Anthropic company, model, safety, and enterprise announcements. |
| `web_claude_blog` | Claude product, Claude Code, and agent updates. |
| `docs_claude_code_changelog` | Claude Code version-level tool updates. |
| `rss_deepmind_blog` | Google DeepMind official model, research, and product announcements. |
| `rss_apple_mlr` | Apple Machine Learning Research papers and technical articles. |
| `docs_gemma_release_notes` | Gemma open model release notes. |
| `rss_mistral_news` | Mistral official model, API, and product newsroom updates. |
| `rss_nvidia_genai` | NVIDIA generative AI product, platform, and practice updates. |
| `rss_hf_blog` | Hugging Face model, product, practice, and research blog updates. |
| `docs_xai_release_notes` | xAI developer release notes (Grok model/API/product updates), split per release entry. |
| `web_qwen_blog` | Qwen model, product, multimodal, and agent updates. |
| `docs_deepseek_api_changelog` | DeepSeek API and model changelog. |
| `github_deepseek_repositories` | Early DeepSeek model/tool signals from public repositories. |
| `hf_deepseek_models` | DeepSeek model releases on Hugging Face. |
| `docs_zai_new_released` | Z.ai/GLM model, API, and product release notes. |
| `web_bytedance_seed_research` | ByteDance Seed research papers/tech reports, split per publication. |
| `web_qbitai` | Tier1 Chinese AI media coverage from 量子位. |
| `rss_the_decoder` | Focused English AI model, market, and product reporting from The Decoder. |
| `rss_hn_ai` | Developer community AI discussion signal from Hacker News, filtered to community-upvoted submissions (min points/comments threshold). |
| `rss_reddit_localllama` | Daily community-upvoted open-model signal from r/LocalLLaMA. |
| `rss_testingcatalog` | Focused tracking of AI product launches and changes from TestingCatalog. |
| `web_huggingface_daily_papers` | Hugging Face daily papers as curated research signal, split per paper (title/abstract/date). |
| `github_trending_daily` | GitHub site-wide daily trending repositories as open-source heat discovery signal (first-time-on-board stream). |
| `rss_ruanyifeng` | Chinese weekly technology digest with high-signal AI coverage from 阮一峰. |
| `rss_simonwillison` | Full-text LLM tool and developer-practice essays from Simon Willison. |
| `rss_latent_space` | Full-text AI engineering interviews and industry analysis from Latent Space. |
| `rss_interconnects` | Full-text analysis of RLHF and open models from Nathan Lambert. |
| `rss_raschka` | Full-text LLM research interpretation and practice from Sebastian Raschka. |
| `rss_oneusefulthing` | Full-text insight on AI application and work practices from Ethan Mollick. |
| `rss_lilianweng` | Deep technical essays on AI research and practice from Lilian Weng. |
| `rss_bair_blog` | Primary research blog posts from the Berkeley Artificial Intelligence Research lab. |
| `web_cursor_changelog` | Cursor product changelog for agent coding practice. |
| `github_opencode_releases` | OpenCode release updates. |
| `github_openclaw_releases` | OpenClaw release updates. |
| `github_hermes_agent_releases` | Hermes Agent release updates. |

## 社交平台账号(X 社交波,v3.12 · 观察期)

2026-07-20 admitted a first batch of 8 X (Twitter) accounts as `incubating` sources;
**2 removed 2026-07-21 to compress cost** (`x_ai_at_meta` low-freq / `x_openrouter`
high-freq cost driver). Now 6: `x_deepseek_ai` / `x_alibaba_qwen` / `x_moonshot_ai` /
`x_karpathy` / `x_sama` / `x_openai`. They follow the same
incubation rules above. Two things differ from web/RSS sources:

- **They cost real money** (X API v2 pay-per-use, budget-guarded — see CLAUDE.md
  *社交媒体流*). Promotion/demotion therefore weighs cost-per-signal, not just quality.
- **Selection favoured zero-coverage and sentinel accounts** over official accounts that
  merely duplicate an existing RSS/web source — the X增量 is timeliness, format and
  non-announcement content, not new coverage.

The full V0 roster with per-account rationale, the rejected candidates with reasons,
and the observation-window exit criteria live in
[`docs/social-x-wave-plan.md`](../social-x-wave-plan.md) §1 / §5.

## Candidate Queue

Future candidates should be recorded first in `docs/sources/candidates/` with the same dimensions, then implemented and admitted only when they pass validation.

## Operational Rule

`src/fetchers/registry.py` exposes `ESSENTIAL_FETCHER_IDS`. A source must not become default-visible unless it is added to that whitelist and has a clear curation reason. The only registered non-essential fetchers should be generic advanced capabilities that support user-defined source configs.
