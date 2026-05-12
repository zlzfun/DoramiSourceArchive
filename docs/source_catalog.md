# DoramiSourceArchive Source Catalog

This document is the persistent source-coverage matrix for DoramiSourceArchive.
It exists to keep development aligned with the product direction: a broad, built-in, multi-type source archive hub rather than an RSS-only aggregator.

## Principles

- Built-in fetchers are the primary workflow. Users should mostly select useful system-provided sources, not discover and configure feeds themselves.
- RSS/Atom is one source type, not the architecture center.
- Each source family should have a reusable base fetcher when possible, with concrete built-in subclasses exposed through the dynamic fetcher registry.
- User-defined `SourceConfigRecord` remains an advanced extension surface.
- X/Twitter and authenticated/private sources require explicit implementation decisions before coding because API cost, account risk, and compliance tradeoffs differ sharply.

## Source Type Matrix

| Source Type | Current Status | Existing/Future Base | Notes |
| --- | --- | --- | --- |
| RSS/Atom feeds | Implemented | `GenericRssFetcher`, `PresetRssFetcher` | Useful for stable blogs, release feeds, papers, community/news feeds. Should not dominate future expansion. |
| Official website/blog/news pages | Partially implemented | `BaseWebPageListFetcher` | Needed for sources without stable RSS, such as Claude/Anthropic pages and Runway News. Current implementation captures list-page metadata and links, with optional article-detail full-text extraction via `fetch_detail`. |
| X/Twitter public posts | Design pending | TBD | AIHot relies heavily on X accounts. Requires a decision: official API, browser scraping, third-party mirror, or webhook/import bridge. |
| WeChat official accounts | Partially implemented | `BaseWechatGzhFetcher` | Existing concrete fetchers cover Chinese AI media/KOL accounts. Real runs depend on valid WeChat MP credentials. |
| GitHub releases/repos | Partially implemented | `PresetRssFetcher`, `GenericGitHubReleasesFetcher`, `PresetGitHubReleasesFetcher` | Release feeds work today, and GitHub Releases API fetchers preserve richer release metadata such as tags, authors, prerelease flags, and assets. GitHub repo activity/trending/issues remain later candidates. |
| Paper sources | Partially implemented through arXiv RSS | `PresetRssFetcher`; future arXiv API/Semantic Scholar fetchers | arXiv categories are covered through RSS. Richer paper metadata needs dedicated APIs. |
| Community/news aggregators | Partially implemented | RSS today; future site/API fetchers | Hacker News and tech news can start through feeds, but richer ranking/comments need dedicated fetchers. |
| Webhook/import bridges | Partially implemented | `DifyWebhookTrigger`; `/api/import/social-posts` | Useful as transitional path for X/Telegram/Discord/private channels when direct crawling is risky. |

## Implemented Built-In Sources

### RSS/Atom

| Source ID | Name | Category | Notes |
| --- | --- | --- | --- |
| `generic_rss` | 通用 RSS/Atom | advanced | Advanced generic source, not the main user workflow. |
| `rss_openai_news` | OpenAI News | official | Official OpenAI news/blog feed. |
| `rss_google_ai_blog` | Google AI Blog | official | Google AI technology blog feed. |
| `rss_google_deepmind_news` | Google DeepMind News | official | Google DeepMind news feed. |
| `rss_huggingface_blog` | Hugging Face Blog | official | Hugging Face blog feed. |
| `rss_microsoft_ai_blog` | Microsoft AI Blog | official | Microsoft AI blog feed. |
| `rss_nvidia_developer_blog` | NVIDIA Developer Blog | official | NVIDIA developer technical blog. |
| `rss_langchain_blog` | LangChain Blog | framework | LangChain/LangGraph/LangSmith blog. |
| `rss_github_blog` | GitHub Blog | developer_platform | GitHub official blog. |
| `rss_arxiv_cs_ai` | arXiv cs.AI | paper | AI papers. |
| `rss_arxiv_cs_cl` | arXiv cs.CL | paper | NLP/CL papers. |
| `rss_arxiv_cs_lg` | arXiv cs.LG | paper | Machine learning papers. |
| `rss_arxiv_cs_cv` | arXiv cs.CV | paper | Computer vision papers. |
| `rss_arxiv_stat_ml` | arXiv stat.ML | paper | Statistical machine learning papers. |
| `rss_arxiv_eess_iv` | arXiv eess.IV | paper | Image/video signal processing papers. |
| `rss_hn_ai` | Hacker News: AI | community | HN AI search feed. |
| `rss_dify_releases` | Dify Releases | product_update | GitHub releases Atom. |
| `rss_vllm_releases` | vLLM Releases | product_update | GitHub releases Atom. |
| `rss_langchain_releases` | LangChain Releases | product_update | GitHub releases Atom. |
| `rss_ollama_releases` | Ollama Releases | product_update | GitHub releases Atom. |
| `rss_transformers_releases` | Transformers Releases | product_update | GitHub releases Atom. |
| `rss_pytorch_releases` | PyTorch Releases | product_update | GitHub releases Atom. |
| `rss_llama_cpp_releases` | llama.cpp Releases | product_update | GitHub releases Atom. |

### WeChat

| Source ID | Name | Category | Notes |
| --- | --- | --- | --- |
| `wechat_jiqizhixin` | 机器之心 | wechat | Existing `BaseWechatGzhFetcher` subclass. |
| `wechat_qbitai` | 量子位 | wechat | Existing `BaseWechatGzhFetcher` subclass. |
| `wechat_xinzhiyuan` | 新智元 | wechat | Existing `BaseWechatGzhFetcher` subclass. |
| `wechat_ai_tech_review` | AI科技评论 | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |
| `wechat_infoq_ai` | AI前线 | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |
| `wechat_zhidx` | 智东西 | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |
| `wechat_founder_park` | Founder Park | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |
| `wechat_silicon_star` | 硅星人 | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |
| `wechat_xixiaoyao` | 夕小瑶科技说 | wechat | Built-in `BaseWechatGzhFetcher` subclass; requires WeChat MP credentials to run. |

### Workflow

| Source ID | Name | Category | Notes |
| --- | --- | --- | --- |
| `webhook_dify_workflow` | Dify 自动化日报编排 | workflow | Outbound workflow trigger, not an inbound content source. |

### Import Bridges

| Endpoint | Content Type | Notes |
| --- | --- | --- |
| `POST /api/import/social-posts` | `social_post` | Inbound bridge for X/Twitter or other social posts collected by external tools. Idempotent by `source_id + post_id`; recommended near-term path before direct X crawling. |

### Official Website / Blog / News Pages

| Source ID | Name | Category | Notes |
| --- | --- | --- | --- |
| `web_anthropic_news` | Anthropic News | official_web | Built on `BaseWebPageListFetcher`; captures list metadata and can optionally fetch article body text. Live detail extraction validated on 2026-05-12. |
| `web_claude_blog` | Claude Blog | official_web | Built on `BaseWebPageListFetcher`; captures list metadata and can optionally fetch article body text. |
| `web_runway_news` | Runway News | official_web | Built on `BaseWebPageListFetcher`; captures Runway News/Research metadata and can optionally fetch article body text. |
| `web_mistral_news` | Mistral AI News | official_web | Built on `BaseWebPageListFetcher`; captures Mistral News metadata and can optionally fetch article body text. |
| `web_stability_news` | Stability AI News | official_web | Built on `BaseWebPageListFetcher`; defaults `fetch_detail=true` because the list page does not expose reliable title/summary text. |
| `web_elevenlabs_blog` | ElevenLabs Blog | official_web | Built on `BaseWebPageListFetcher`; captures ElevenLabs Blog metadata and can optionally fetch article body text. |

### GitHub Releases API

| Source ID | Repository | Category | Notes |
| --- | --- | --- | --- |
| `generic_github_releases` | Runtime configured | advanced | Advanced generic GitHub Releases API source for user-configured repositories. |
| `github_dify_releases` | `langgenius/dify` | product_update | API-backed release metadata; richer counterpart to the existing Atom feed source. |
| `github_vllm_releases` | `vllm-project/vllm` | product_update | API-backed vLLM release metadata. |
| `github_ollama_releases` | `ollama/ollama` | product_update | API-backed Ollama release metadata. |
| `github_langchain_releases` | `langchain-ai/langchain` | product_update | API-backed LangChain release metadata. |
| `github_transformers_releases` | `huggingface/transformers` | product_update | API-backed Transformers release metadata. |
| `github_pytorch_releases` | `pytorch/pytorch` | product_update | API-backed PyTorch release metadata. |
| `github_llama_cpp_releases` | `ggml-org/llama.cpp` | product_update | API-backed llama.cpp release metadata. |
| `github_litellm_releases` | `BerriAI/litellm` | product_update | API-backed LiteLLM release metadata. Live fetch validated on 2026-05-12. |
| `github_open_webui_releases` | `open-webui/open-webui` | product_update | API-backed Open WebUI release metadata. |
| `github_comfyui_releases` | `comfyanonymous/ComfyUI` | product_update | API-backed ComfyUI release metadata. |
| `github_openai_agents_python_releases` | `openai/openai-agents-python` | product_update | API-backed OpenAI Agents SDK release metadata. Live fetch validated on 2026-05-12. |
| `github_claude_code_releases` | `anthropics/claude-code` | product_update | API-backed Claude Code release metadata. Live fetch validated on 2026-05-12. |

## Candidate Sources Inspired By AIHot

Sampled from AIHot public pages on 2026-05-12. These are planning candidates, not all confirmed source endpoints.

### X/Twitter Accounts

| Candidate | Priority | Rationale | Implementation Status |
| --- | --- | --- | --- |
| `OpenAI` | High | Official product/research/security announcements. | Pending X strategy decision. |
| `OpenAIDevs` | High | Developer/API/agent updates. | Pending X strategy decision. |
| `AnthropicAI` / `claudeai` / `ClaudeDevs` | High | Claude product, Claude Code, developer updates. | Pending X strategy decision. |
| `GoogleDeepMind`, `googleaidevs`, `GeminiApp` | High | Gemini/DeepMind/product/developer updates. | Pending X strategy decision. |
| `MSFTResearch` | Medium | Research and safety/benchmark updates. | Pending X strategy decision. |
| `Alibaba_Cloud`, `TencentHunyuan`, `MiniMax_AI`, `Kimi_Moonshot`, `SenseTime_AI` | High | Chinese and global model/product announcements. | Pending X strategy decision. |
| `OpenRouter`, `Replit`, `Cursor`, `LumaLabsAI`, `RunwayML`, `PixVerse_` | High | Product launches and tooling updates. | Pending X strategy decision. |
| `sama`, `karpathy`, `fchollet`, `swyx`, `SimonW`, `dotey`, `vista8`, `op7418`, `berryxia`, `shao__meng` | Medium | Important maker/researcher/KOL signal. | Pending X strategy decision. |

### Official Website / Blog / News Pages

| Candidate | Priority | Rationale | Implementation Status |
| --- | --- | --- | --- |
| Claude / Anthropic Blog and News | High | AIHot includes Claude Blog webpage items; previous RSS candidates were not stable. | Initial webpage fetchers implemented. |
| Runway News | High | AIHot includes Runway News webpage items. | Initial webpage fetcher implemented. |
| Mistral News / Blog | High | Important model/product source; RSS candidate failed validation. | Initial webpage fetcher implemented. |
| OpenAI News pages excluding customer stories | High | RSS exists, but source-specific filtering may require webpage logic. | RSS implemented; webpage refinement pending. |
| Stability AI News & Updates | Medium | Official image/video/audio generation model and enterprise updates. | Built-in webpage fetcher implemented. |
| ElevenLabs Blog | Medium | Official voice AI, agent, API, and enterprise updates. | Built-in webpage fetcher implemented. |
| Simon Willison blog | Medium | High-signal technical posts, sometimes not AI-only. | Candidate website/RSS evaluation pending. |
| Tomer Tunguz blog | Medium | VC/market analysis relevant to AI infrastructure. | Candidate website/RSS evaluation pending. |
| The Decoder / IT之家 / 36Kr AI / InfoQ AI | Medium | News coverage; may use RSS or webpage depending source stability. | Candidate evaluation pending. |
| Cohere Blog | Medium | Official enterprise AI/model blog; listing page is accessible but did not expose direct article links in the first static HTML check. | Candidate evaluation pending; may need RSS/API/embedded-data parsing. |
| Perplexity Blog / xAI News | Medium | Important product/model sources, but tested pages returned 403 to the current fetcher-style HTTP client. | Candidate evaluation pending; do not add as built-in webpage nodes until access strategy is clear. |

### WeChat Accounts

| Candidate | Priority | Rationale | Implementation Status |
| --- | --- | --- | --- |
| AI科技评论 | High | Chinese AI research/industry coverage. | Built-in subclass implemented. |
| InfoQ AI 前线 / InfoQ | High | Developer and industry reporting. | Built-in subclass implemented as `AI前线`. |
| 智东西 | Medium | AI hardware/industry coverage. | Built-in subclass implemented. |
| Founder Park | Medium | AI startup/product ecosystem. | Built-in subclass implemented. |
| 硅星人 | Medium | Silicon Valley and AI product coverage. | Built-in subclass implemented. |
| 夕小瑶科技说 | Medium | Technical interpretation and AI trends. | Built-in subclass implemented. |

### GitHub / Developer Ecosystem

| Candidate | Priority | Rationale | Implementation Status |
| --- | --- | --- | --- |
| Claude Code releases | High | AIHot tracks Claude Code releases. | Built-in GitHub Releases API fetcher implemented. |
| OpenAI Agents SDK releases | High | Developer/API/agent workflow updates. | Built-in GitHub Releases API fetcher implemented. |
| ComfyUI, LiteLLM, vLLM, llama.cpp, Ollama, LangChain, Dify, OpenWebUI | Medium | AI tooling release velocity. | Built-in GitHub Releases API fetchers implemented; some also have older Atom feed counterparts. |

## Immediate Next Development Slice

1. Add more built-in official webpage sources and source-specific filters where RSS is absent or too broad.
2. Decide the X/Twitter ingestion strategy before implementing direct source fetchers.
3. Add GitHub repo activity/trending/issues fetchers when needed.
4. Improve WeChat credential handling and account-name verification after more real-run data is available.

## Open Verification Items

| Item | Status | Owner Notes |
| --- | --- | --- |
| WeChat real-run validation for the 6 newly added accounts | Pending | `wechat_ai_tech_review`, `wechat_infoq_ai`, `wechat_zhidx`, `wechat_founder_park`, `wechat_silicon_star`, and `wechat_xixiaoyao` are registered and compile successfully, but have not been verified against the WeChat MP backend. A real run requires valid `src/.wechat_auth/wechat_config.json` credentials or a fresh QR login. Verify exact account-name matching, fakeid resolution, rate limiting behavior, and article body extraction before marking these sources production-ready. |
| Existing WeChat credential workflow hardening | Pending | Current flow can trigger QR login and depends on local/enterprise notification pieces. Before unattended scheduling, confirm credential refresh behavior, failure classification, and safe concurrency in the deployment environment. |
| Official webpage full-text extraction | Partially verified | `BaseWebPageListFetcher` supports optional `fetch_detail` and `detail_max_chars` parameters. Live extraction was verified against `web_anthropic_news`; remaining webpage sources should be spot-checked because HTML structure varies by site. |
| X/Twitter ingestion strategy | Pending decision | Direct X/Twitter fetchers are still blocked until `docs/x_twitter_ingestion_decision.md` is reviewed and one option is selected. The safe webhook/import bridge is now available for external collectors. |

## X/Twitter Decision Options

| Option | Pros | Cons |
| --- | --- | --- |
| Official X API | Most legitimate and stable if paid access is available. | Cost, quota, setup, API policy dependency. |
| Browser/session scraping | Can capture public UI when logged in. | Fragile, account risk, harder to run headless reliably. |
| Third-party mirrors/services | Fast to integrate if available. | Reliability, compliance, provenance concerns. |
| Webhook/import bridge | Safe transition: external tools push posts into DoramiSourceArchive. | Requires another collector outside this project. |

No X/Twitter implementation should be merged until the option is explicitly chosen.
