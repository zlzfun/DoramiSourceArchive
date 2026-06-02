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

## Default Essential Sources

The current default-visible catalog is the admitted first additive batch. Each source should keep the v1.0 dimensions in code metadata: source owner, brand, scope, channel, base URL, provenance tier, content tags, signal strength, noise risk, and fetch reliability.

| Source ID | Candidate Reason |
| --- | --- |
| `rss_openai_news` | OpenAI official model, product, Codex, and agent news. |
| `docs_openai_codex_changelog` | Codex CLI, IDE, agent, and automation changelog. |
| `web_anthropic_news` | Anthropic company, model, safety, and enterprise announcements. |
| `web_claude_blog` | Claude product, Claude Code, and agent updates. |
| `docs_claude_code_changelog` | Claude Code version-level tool updates. |
| `docs_gemma_release_notes` | Gemma open model release notes. |
| `docs_xai_release_notes` | xAI developer release notes (Grok model/API/product updates), split per release entry. |
| `web_qwen_blog` | Qwen model, product, multimodal, and agent updates. |
| `docs_deepseek_api_changelog` | DeepSeek API and model changelog. |
| `github_deepseek_repositories` | Early DeepSeek model/tool signals from public repositories. |
| `hf_deepseek_models` | DeepSeek model releases on Hugging Face. |
| `docs_zai_new_released` | Z.ai/GLM model, API, and product release notes. |
| `web_bytedance_seed_research` | ByteDance Seed research papers/tech reports, split per publication. |
| `web_qbitai` | Tier1 Chinese AI media coverage from 量子位. |
| `rss_hn_ai` | Developer community AI discussion signal from Hacker News, filtered to community-upvoted submissions (min points/comments threshold). |
| `web_huggingface_daily_papers` | Hugging Face daily papers as curated research signal, split per paper (title/abstract/date). |
| `web_cursor_changelog` | Cursor product changelog for agent coding practice. |
| `github_opencode_releases` | OpenCode release updates. |
| `github_openclaw_releases` | OpenClaw release updates. |
| `github_hermes_agent_releases` | Hermes Agent release updates. |

## Candidate Queue

Future candidates should be recorded first in `docs/sources/candidates/` with the same dimensions, then implemented and admitted only when they pass validation.

## Operational Rule

`src/fetchers/registry.py` exposes `ESSENTIAL_FETCHER_IDS`. A source must not become default-visible unless it is added to that whitelist and has a clear curation reason. The only registered non-essential fetchers should be generic advanced capabilities that support user-defined source configs.
