# AIHOT Source Gap Analysis

分析对象：

- AIHOT 非 X 信源清单：[docs/aihot-source-inventory.md](./aihot-source-inventory.md)
- 当前项目内置 fetchers：`src/fetchers/impl/*.py`
- 当前目标：聚焦最新 AI 应用相关资讯，包括新模型、OpenAI/Anthropic 等 AI 大厂动态、优秀 AI 实践和应用工具。

暂不考虑 X 推文信源。

## 当前已有覆盖

项目当前已覆盖的关键方向：

- 大厂官方动态：OpenAI News、Anthropic News、Claude Blog、Google AI Blog、Google DeepMind News、Mistral News。
- AI 应用与多模态产品：Runway News、Stability AI News、ElevenLabs Blog。
- 开发者与 Agent 工具：Claude Code Releases、OpenAI Agents SDK Releases、Dify、Open WebUI、ComfyUI、LiteLLM 等 GitHub Releases。
- 中文媒体与社区：机器之心、量子位、新智元、AI科技评论、AI前线、智东西等 WeChat 源。
- 论文/社区/工程基础流：arXiv、HN AI、Hugging Face Blog、GitHub Blog、NVIDIA Developer Blog 等。

所以，AIHOT 里与这些等价的来源不应重复添加，只在必要时做“更细粒度拆分”。例如 `web-anthropic-news`、`web-claude-blog`、`web-mistral-news`、`web-runway-news`、`rss-openai-news`、`rss-huggingface-blog`、`rss-github-blog` 已有等价覆盖。

## 最高价值补充

这些来源当前项目不具备，且高度贴合“新模型 / AI 大厂动态 / AI 应用产品 / 优秀实践”的主线。

| 推荐优先级 | AIHOT 来源 | 补充价值 | 建议实现 |
| --- | --- | --- | --- |
| P0 | `external-qwen-blog-retrieval` / `external-qwen-research`：Qwen Blog / Research | 通义千问是当前中文与全球开源模型生态的核心玩家，模型发布、能力更新、推理/多模态实践都很高频。 | 先做 Qwen 官方 Blog 网页/嵌入数据 fetcher；Research 可做独立 JSON/web source。 |
| P0 | `json-deepseek-github`：DeepSeek GitHub 新仓库 | DeepSeek 的新仓库、新模型代码和工具发布经常比新闻稿更早，是“最新模型/实践”的强信号。 | 用 GitHub API 做 org repo/new release watcher，不要只抓 release。 |
| P0 | `web-xai-news`：xAI News | Grok、xAI 模型、Grok 应用和 API 动态，属于一线模型厂官方源。 | 先验证网页抓取可行性；若 403，列入浏览器或外部导入策略。 |
| P0 | `external-moonshot-ai`：Moonshot AI / Kimi Blog | Kimi 是国内 AI 应用与模型能力更新的重要源，和产品应用目标高度匹配。 | VitePress/静态页面 fetcher，优先抓标题、发布时间、正文。 |
| P0 | `web-minimax-news`：MiniMax News | MiniMax 覆盖文本、语音、视频、Agent/应用产品，是国内多模态大厂关键源。 | WebPageListFetcher 子类，需验证列表页是否有嵌入数据。 |
| P0 | `web-cursor-blog`：Cursor Blog | Cursor 是 AI 编码应用标杆，能补足“优秀 AI 实践 / AI 应用工具”方向。 | WebPageListFetcher 或 RSS，如果官方 RSS 可用优先 RSS。 |
| P0 | `rss-openrouter-announcements`：OpenRouter Announcements | OpenRouter 的模型上线、路由、价格和 API 能力变化，对应用开发者很有用。 | RSS fetcher，默认 watch/core 之间；建议加关键词过滤降低平台公告噪声。 |

## 高价值第二批

这些也值得补，但相比 P0 要么领域稍窄，要么实现/去噪风险更高。

| 推荐优先级 | AIHOT 来源 | 补充价值 | 建议实现 |
| --- | --- | --- | --- |
| P1 | `json-seed-research-feed` / `json-seed-research-papers`：字节 Seed Research | 字节在模型、多模态、Agent 和应用侧都有前沿投入，Research feed 能补国内大厂研究发布。 | JSON/网页内嵌数据 fetcher；默认 watch，避免论文流过重。 |
| P1 | `json-zhipuai-research`：智谱研究 | 智谱/Z.ai 是国内模型厂和 Agent 产品重要玩家。 | JSON/网页内嵌数据 fetcher；优先模型/产品/Agent 关键词。 |
| P1 | `json-hunyuan-research`：腾讯混元 Research | 腾讯混元模型、应用和多模态能力更新值得跟踪。 | API/网页数据 fetcher；默认 watch。 |
| P1 | `json-inclusion-ai-github` / `json-inclusion-ai-hf`：蚂蚁 inclusionAI | 适合捕捉新开源仓库和 HuggingFace 新模型，模型生态信号早。 | GitHub org watcher + HuggingFace model watcher。 |
| P1 | `web-ant-ling-blog`：蚂蚁百灵 Developer Blog | 偏应用、Agent、开发者实践，和“优秀 AI 实践”匹配。 | WebPageListFetcher。 |
| P1 | `json-longcat-hf`：美团 LongCat HuggingFace 新模型 | 新模型发布信号，适合补国内开源模型覆盖。 | HuggingFace org/model watcher。 |
| P1 | `web-suno-blog`：Suno Blog | 音乐生成应用标杆，补足创作型 AI 应用。 | WebPageListFetcher/RSS。 |
| P1 | `external-midjourney-updates`：Midjourney Updates | 图像生成应用标杆，适合模型/产品更新跟踪。 | RSS 或网页 changelog source。 |
| P1 | `web-runway-changelog`：Runway Changelog | 我们已有 Runway News，但 changelog 更贴近产品能力变化。 | 新增独立 changelog fetcher，或扩展现有 Runway source 的栏目。 |
| P1 | `rss-hermes-desktop-releases`：Hermes Desktop Releases | AI Agent 桌面/个人 Agent 实践信号，和 openclaw 类“优秀实践”接近。 | GitHub Releases API fetcher；默认 watch。 |

## 可选补充

这些来源有价值，但更容易偏研究、评论、政策或泛媒体，需要明确过滤规则。

| 推荐优先级 | AIHOT 来源 | 价值与风险 |
| --- | --- | --- |
| P2 | `web-anthropic-engineering`、`web-anthropic-research`、`web-anthropic-circuits`、`rss-alignment-openai` | 适合补大厂研究、安全、工程复盘。与“应用资讯”不是完全重合，建议作为 watch，不进默认核心流。 |
| P2 | `rss-simon-willison`、`rss-lilian-weng-blog`、`rss-interconnects-ai`、`rss-ethan-mollick-blog`、`rss-gary-marcus` | 高信号个人/分析博客，但不是纯资讯源。需要强摘要和去噪。 |
| P2 | `rss-techcrunch-ai`、`rss-the-decoder`、`rss-the-verge-ai`、`rss-marktechpost`、`rss-arstechnica-ai`、`rss-artificialintelligence-news`、`rss-venturebeat-ai` | 能补海外媒体覆盖，但重复、标题党和泛行业内容较多。建议只选 2-3 个做媒体补盲。 |
| P2 | `rss-ithome`、`rss-hn-buzzing`、`json-hn-ai`、`json-hf-daily-papers` | AIHOT 使用频繁，但对当前目标容易过宽。我们已有 HN AI 和 arXiv，除非需要中文泛资讯/社区热度，否则不应优先。 |
| P2 | `rss-apple-ml-research`、`rss-apple-newsroom`、`web-meta-ai-blog`、`rss-meta-engineering`、`web-nvidia-ai-blog`、`rss-cloudflare-blog` | 大厂/平台信号有价值，但常偏研究、硬件、工程或泛平台。可以作为 watchlist。 |
| P3 | `rss-bair-blog`、`rss-cmu-ml-blog`、`web-berkeley-rdi-blog`、`web-lmsys-blog`、`rss-eleutherai-blog`、`rss-shunyu-yao-blog`、`rss-tomtunguz` | 偏研究/观点/市场分析。对“AI 应用资讯中枢”不是首要增量。 |

## 推荐落地顺序

1. P0 官方/产品源：Qwen、DeepSeek GitHub、xAI News、Kimi、MiniMax、Cursor、OpenRouter。
2. P1 国内模型生态：字节 Seed、智谱、腾讯混元、蚂蚁 inclusionAI/HF、蚂蚁百灵、美团 LongCat。
3. P1 创作与 Agent 应用：Suno、Midjourney、Runway Changelog、Hermes Desktop。
4. P2 研究/媒体源：只在有过滤策略后加入，避免把中枢重新拖回“泛 AI 信息流”。

## 结论

当前项目的欧美核心模型厂和若干 AI 应用源已经不弱；最大缺口不是“更多 RSS”，而是：

- 国内模型厂官方源不足：Qwen、DeepSeek、Kimi、MiniMax、Seed、智谱、混元、蚂蚁、LongCat。
- AI 应用产品源不足：Cursor、OpenRouter、Suno、Midjourney、Runway Changelog。
- 官方源粒度不足：Anthropic/Google/OpenAI 的研究、工程、安全子频道可以作为 watchlist 补强。

若只做一轮高性价比扩展，建议先实现 7 个 P0 源，再补 6-8 个 P1 源。这样新增信源会更贴近“最新 AI 应用和模型动态”，而不是变成泛媒体/论文聚合。
