# DoramiSourceArchive Source Catalog

This catalog describes the current source implementation after the additive source reset.

## Principles

- The focused catalog is built by adding reviewed sources one by one.
- Historical concrete preset sources have been removed from the fetcher implementation instead of being kept as hidden nodes.
- Small generic fetcher capabilities remain as advanced infrastructure for configurable sources, but they are not treated as curated sources.
- Every curated source should carry the v1.0 dimensions: `source_owner`, `source_brand`, `source_scope`, `source_channel`, `base_url`, `provenance_tier`, `content_tags`, `signal_strength`, `noise_risk`, and `fetch_reliability`.

## Current Registry Shape

| Group | Count | Notes |
| --- | ---: | --- |
| Recommended review sources | 30 | Default-visible through `ESSENTIAL_FETCHER_IDS`. |
| Generic advanced fetchers | 4 | Runtime-configured RSS, GitHub releases, GitHub repositories, and Hugging Face models. |
| Historical concrete preset sources | 0 | Removed from `src/fetchers/impl`; not merely hidden. |

## Recommended Review Sources

### OpenAI / ChatGPT / Codex

| Source ID | Base URL |
| --- | --- |
| `rss_openai_news` | `https://openai.com/news/rss.xml` |
| `docs_openai_api_changelog` | `https://developers.openai.com/api/docs/changelog` |
| `docs_openai_codex_changelog` | `https://developers.openai.com/codex/changelog` |

### Anthropic / Claude

| Source ID | Base URL |
| --- | --- |
| `web_anthropic_news` | `https://www.anthropic.com/news` |
| `web_claude_blog` | `https://claude.com/blog` |
| `docs_claude_code_changelog` | `https://code.claude.com/docs/en/changelog` |

### Google / Gemini / Gemma / Antigravity

| Source ID | Base URL |
| --- | --- |
| `rss_google_gemini_models` | `https://blog.google/innovation-and-ai/models-and-research/gemini-models/` |
| `docs_gemini_api_changelog` | `https://ai.google.dev/gemini-api/docs/changelog` |
| `docs_gemma_release_notes` | `https://ai.google.dev/gemma/docs/releases` |

### xAI / Grok

| Source ID | Base URL |
| --- | --- |
| `docs_xai_release_notes` | `https://docs.x.ai/developers/release-notes` |
| `docs_xai_models` | `https://docs.x.ai/developers/models` |

### Alibaba / Qwen

| Source ID | Base URL |
| --- | --- |
| `web_qwen_blog` | `https://qwen.ai/blog` |
| `docs_alibaba_model_studio_announcements` | `https://www.alibabacloud.com/help/en/model-studio/model-announcements` |
| `github_qwen_code_releases` | `https://github.com/QwenLM/qwen-code/releases` |

### DeepSeek

| Source ID | Base URL |
| --- | --- |
| `docs_deepseek_api_changelog` | `https://api-docs.deepseek.com/updates/` |
| `github_deepseek_repositories` | `https://github.com/deepseek-ai` |
| `hf_deepseek_models` | `https://huggingface.co/deepseek-ai` |

### Zhipu / Z.ai / GLM

| Source ID | Base URL |
| --- | --- |
| `docs_zai_new_released` | `https://docs.z.ai/release-notes/new-released` |

### ByteDance Seed

| Source ID | Base URL |
| --- | --- |
| `web_bytedance_seed_models` | `https://seed.bytedance.com/en/models` |
| `web_bytedance_seed_research` | `https://seed.bytedance.com/en/research` |

### Tier1 Media / Community / Research Signal

| Source ID | Base URL |
| --- | --- |
| `web_qbitai` | `https://www.qbitai.com/` |
| `web_aiera` | `https://aiera.com.cn/` |
| `web_jiqizhixin` | `https://www.jiqizhixin.com/` |
| `web_ithome_ai` | `https://next.ithome.com/ai` |
| `rss_hn_ai` | `https://hnrss.org/newest?q=AI` |
| `web_huggingface_daily_papers` | `https://huggingface.co/papers` |

### Agent Coding Tools

| Source ID | Base URL |
| --- | --- |
| `web_cursor_changelog` | `https://cursor.com/changelog` |
| `github_opencode_releases` | `https://github.com/opencode-ai/opencode/releases` |
| `github_openclaw_releases` | `https://github.com/openclaw/openclaw/releases` |
| `github_hermes_agent_releases` | `https://github.com/NousResearch/hermes-agent/releases` |

## Generic Advanced Fetchers

| Source ID | Purpose |
| --- | --- |
| `generic_rss` | Runtime-configured RSS/Atom ingestion for user-defined source configs. |
| `generic_github_releases` | Runtime-configured GitHub Releases ingestion. |
| `generic_github_repositories` | Runtime-configured GitHub organization/user repository ingestion. |
| `generic_huggingface_models` | Runtime-configured Hugging Face author/org model ingestion. |

## Verification Notes

- Backend metadata must expose the v1.0 dimensions and `base_url` for each registry item.
- The focused catalog should contain exactly `ESSENTIAL_FETCHER_IDS`; generic advanced fetchers must remain hidden.
- Spot validation on 2026-05-27 confirmed content extraction for representative source types: RSS, official webpage, single-page docs, GitHub releases, and Qwen JSON-backed blog entries.
- Recovery pass on 2026-05-28 restored OpenAI API Changelog, Google Blog Gemini Models, 新智元 Website, IT之家 AI category page, and 机器之心 Website. 机器之心 direct article/RSS HTTP still routes to a data-service/error page, so its fetcher enumerates original article URLs from the public sitemap and reads article正文 through the `r.jina.ai` reader proxy while preserving the original `jiqizhixin.com` source URL.
- Sources still parked after the recovery pass: ChatGPT Release Notes and xAI News return Cloudflare challenge/block pages to the HTTP fetcher; Antigravity Blog exposes a public SPA shell and bundled list data but no stable article正文 endpoint for the current fetcher stack.
