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
| Official website/blog/news pages | Partially implemented | `BaseWebPageListFetcher` | Needed for sources without stable RSS, such as Claude/Anthropic pages and Runway News. Current implementation captures list-page metadata and links; full-text extraction is a later data-quality task. |
| X/Twitter public posts | Design pending | TBD | AIHot relies heavily on X accounts. Requires a decision: official API, browser scraping, third-party mirror, or webhook/import bridge. |
| WeChat official accounts | Partially implemented | `BaseWechatGzhFetcher` | Existing concrete fetchers cover Chinese AI media/KOL accounts. Real runs depend on valid WeChat MP credentials. |
| GitHub releases/repos | Partially implemented through Atom | `PresetRssFetcher` for releases; future GitHub API fetcher for richer repo data | Release feeds work today. GitHub API/trending/stars/issues can be a later non-RSS fetcher family. |
| Paper sources | Partially implemented through arXiv RSS | `PresetRssFetcher`; future arXiv API/Semantic Scholar fetchers | arXiv categories are covered through RSS. Richer paper metadata needs dedicated APIs. |
| Community/news aggregators | Partially implemented | RSS today; future site/API fetchers | Hacker News and tech news can start through feeds, but richer ranking/comments need dedicated fetchers. |
| Webhook/import bridges | Partially implemented | `DifyWebhookTrigger`; future inbound APIs | Useful as transitional path for X/Telegram/Discord/private channels when direct crawling is risky. |

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

### Official Website / Blog / News Pages

| Source ID | Name | Category | Notes |
| --- | --- | --- | --- |
| `web_anthropic_news` | Anthropic News | official_web | Built on `BaseWebPageListFetcher`; captures Anthropic News list-page metadata and article links. |
| `web_claude_blog` | Claude Blog | official_web | Built on `BaseWebPageListFetcher`; captures Claude Blog list-page metadata and article links. |
| `web_runway_news` | Runway News | official_web | Built on `BaseWebPageListFetcher`; captures Runway News/Research list-page metadata and article links. |
| `web_mistral_news` | Mistral AI News | official_web | Built on `BaseWebPageListFetcher`; captures Mistral News list-page metadata and article links. |

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
| Simon Willison blog | Medium | High-signal technical posts, sometimes not AI-only. | Candidate website/RSS evaluation pending. |
| Tomer Tunguz blog | Medium | VC/market analysis relevant to AI infrastructure. | Candidate website/RSS evaluation pending. |
| The Decoder / IT之家 / 36Kr AI / InfoQ AI | Medium | News coverage; may use RSS or webpage depending source stability. | Candidate evaluation pending. |

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
| Claude Code releases | High | AIHot tracks Claude Code releases. | Pending release Atom or GitHub API source. |
| Codex / OpenAI developer tooling repos | High | Developer workflow updates. | Pending repo selection. |
| ComfyUI, LiteLLM, vLLM, llama.cpp, Ollama, LangChain, Dify, OpenWebUI | Medium | AI tooling release velocity. | Some releases implemented; expand repo set later. |

## Immediate Next Development Slice

1. Improve website/blog/news fetchers with optional article-detail full-text extraction.
2. Prepare an X/Twitter fetcher decision note before implementation.
3. Add richer GitHub API fetchers for repos/trending/issues when needed.
4. Improve WeChat credential handling and account-name verification after more real-run data is available.

## X/Twitter Decision Options

| Option | Pros | Cons |
| --- | --- | --- |
| Official X API | Most legitimate and stable if paid access is available. | Cost, quota, setup, API policy dependency. |
| Browser/session scraping | Can capture public UI when logged in. | Fragile, account risk, harder to run headless reliably. |
| Third-party mirrors/services | Fast to integrate if available. | Reliability, compliance, provenance concerns. |
| Webhook/import bridge | Safe transition: external tools push posts into DoramiSourceArchive. | Requires another collector outside this project. |

No X/Twitter implementation should be merged until the option is explicitly chosen.
