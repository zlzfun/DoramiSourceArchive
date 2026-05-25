# AIHOT Source Inventory

采集对象：[AIHOT 全部 AI 动态](https://aihot.virxact.com/all)

采集时间：2026-05-21（Asia/Shanghai）

方法：抓取公开分页中的频道筛选页，并从页面内嵌的条目数据里提取每条动态的 `source.id`、`source.name`、`source.kind`。本清单记录主来源，不把“关联讨论”中的来源名字符串计入主清单。

## 一手信源

共识别 56 个主来源。

| ID | 名称 | 类型 |
| --- | --- | --- |
| `web-karpathy-blog` | Andrej Karpathy：Blog（网页） | `web_list` |
| `web-anthropic-engineering` | Anthropic：Engineering（事故复盘 + 工程实践 · 网页） | `web_list` |
| `web-anthropic-news` | Anthropic：Newsroom（网页） | `web_list` |
| `web-anthropic-research` | Anthropic：Research（发表成果 · 网页） | `web_list` |
| `web-anthropic-circuits` | Anthropic：Transformer Circuits（可解释性研究） | `web_list` |
| `rss-apple-ml-research` | Apple Machine Learning Research（RSS） | `rss` |
| `rss-apple-newsroom` | Apple：Newsroom（RSS） | `rss` |
| `rss-bair-blog` | BAIR：Berkeley AI Research Blog | `rss` |
| `web-berkeley-rdi-blog` | Berkeley RDI：Blog（AI 安全与评测） | `web_list` |
| `rss-cmu-ml-blog` | CMU：Machine Learning Blog | `rss` |
| `rss-claude-code` | Claude Code：GitHub Releases（RSS） | `rss` |
| `web-claude-blog` | Claude：Blog（网页） | `web_list` |
| `rss-cloudflare-blog` | Cloudflare Blog | `rss` |
| `web-cursor-blog` | Cursor Blog | `web_list` |
| `web-dario-amodei-blog` | Dario Amodei：Blog（网页） | `web_list` |
| `json-deepseek-github` | DeepSeek：GitHub 新仓库 | `json_list` |
| `rss-eleutherai-blog` | EleutherAI：Blog | `rss` |
| `rss-ethan-mollick-blog` | Ethan Mollick：One Useful Thing（RSS） | `rss` |
| `rss-gary-marcus` | Gary Marcus：The Road to AI We Can Trust（RSS） | `rss` |
| `rss-github-blog` | GitHub Blog | `rss` |
| `web-google-blog-models-research` | Google Blog：AI（RSS） | `rss` |
| `web-deepmind-blog` | Google DeepMind：Blog（RSS） | `rss` |
| `rss-google-developers` | Google Developers Blog（RSS） | `rss` |
| `web-google-research-blog` | Google Research：Blog（网页） | `web_list` |
| `rss-hermes-desktop-releases` | Hermes Desktop：GitHub Releases（RSS） | `rss` |
| `rss-huggingface-blog` | Hugging Face：Blog（RSS） | `rss` |
| `web-lmsys-blog` | LMSYS：Blog（Chatbot Arena 团队） | `web_list` |
| `rss-lilian-weng-blog` | Lilian Weng：Lil'Log（RSS） | `rss` |
| `web-meta-ai-blog` | Meta AI：Blog（网页） | `web_list` |
| `rss-meta-engineering` | Meta Engineering Blog（RSS） | `rss` |
| `external-midjourney-updates` | Midjourney：Updates（RSS） | `rss` |
| `web-minimax-news` | MiniMax：News（网页） | `web_list` |
| `web-mistral-news` | Mistral AI：News（网页） | `web_list` |
| `external-moonshot-ai` | Moonshot AI：Kimi Blog（VitePress） | `web_list` |
| `web-nvidia-ai-blog` | NVIDIA AI Blog | `web_list` |
| `rss-interconnects-ai` | Nathan Lambert：Interconnects（RSS） | `rss` |
| `rss-alignment-openai` | OpenAI：Alignment 研究博客（RSS） | `rss` |
| `rss-openai-news` | OpenAI：官网动态（RSS · 排除企业/客户案例） | `rss` |
| `rss-openrouter-announcements` | OpenRouter：Announcements（RSS） | `rss` |
| `external-qwen-blog-retrieval` | Qwen：Blog Retrieval（API） | `json_list` |
| `external-qwen-research` | Qwen：Research（API） | `json_list` |
| `web-runway-changelog` | Runway：Changelog（网页） | `web_list` |
| `web-runway-news` | Runway：News（网页） | `web_list` |
| `rss-sam-altman-blog` | Sam Altman：Blog（RSS） | `rss` |
| `rss-shunyu-yao-blog` | Shunyu Yao：Blog（RSS） | `rss` |
| `web-suno-blog` | Suno：Blog（网页） | `web_list` |
| `rss-tomtunguz` | Tomer Tunguz 博客（VC 分析） | `rss` |
| `web-xai-news` | xAI：News（网页） | `web_list` |
| `json-seed-research-feed` | 字节 Seed：Research Feed（网页内嵌数据） | `json_list` |
| `json-seed-research-papers` | 字节 Seed：Research Papers（网页内嵌数据） | `json_list` |
| `json-zhipuai-research` | 智谱：研究（网页内嵌数据） | `json_list` |
| `json-longcat-hf` | 美团 LongCat：HuggingFace 新模型 | `json_list` |
| `json-hunyuan-research` | 腾讯混元：Research（API） | `json_list` |
| `json-inclusion-ai-github` | 蚂蚁 inclusionAI：GitHub 新仓库 | `json_list` |
| `json-inclusion-ai-hf` | 蚂蚁 inclusionAI：HuggingFace 新模型 | `json_list` |
| `web-ant-ling-blog` | 蚂蚁百灵：Developer Blog（网页） | `web_list` |

## 资讯

共识别 46 个主来源。

| ID | 名称 | 类型 |
| --- | --- | --- |
| `web-anthropic-news` | Anthropic：Newsroom（网页） | `web_list` |
| `web-anthropic-research` | Anthropic：Research（发表成果 · 网页） | `web_list` |
| `web-anthropic-circuits` | Anthropic：Transformer Circuits（可解释性研究） | `web_list` |
| `rss-apple-ml-research` | Apple Machine Learning Research（RSS） | `rss` |
| `rss-apple-newsroom` | Apple：Newsroom（RSS） | `rss` |
| `rss-arstechnica-ai` | Ars Technica：AI（RSS） | `rss` |
| `rss-artificialintelligence-news` | Artificial Intelligence News（RSS） | `rss` |
| `rss-bair-blog` | BAIR：Berkeley AI Research Blog | `rss` |
| `web-berkeley-rdi-blog` | Berkeley RDI：Blog（AI 安全与评测） | `web_list` |
| `rss-cmu-ml-blog` | CMU：Machine Learning Blog | `rss` |
| `rss-claude-code` | Claude Code：GitHub Releases（RSS） | `rss` |
| `web-claude-blog` | Claude：Blog（网页） | `web_list` |
| `rss-cloudflare-blog` | Cloudflare Blog | `rss` |
| `web-cursor-blog` | Cursor Blog | `web_list` |
| `web-dataguidance-ai` | DataGuidance：Artificial Intelligence（网页） | `web_list` |
| `rss-gary-marcus` | Gary Marcus：The Road to AI We Can Trust（RSS） | `rss` |
| `rss-github-blog` | GitHub Blog | `rss` |
| `web-google-blog-models-research` | Google Blog：AI（RSS） | `rss` |
| `web-deepmind-blog` | Google DeepMind：Blog（RSS） | `rss` |
| `rss-google-developers` | Google Developers Blog（RSS） | `rss` |
| `web-google-research-blog` | Google Research：Blog（网页） | `web_list` |
| `rss-hn-buzzing` | Hacker News 热门（buzzing.cc 中文翻译） | `rss` |
| `json-hn-ai` | Hacker News：AI 热帖 | `json_list` |
| `rss-hermes-desktop-releases` | Hermes Desktop：GitHub Releases（RSS） | `rss` |
| `rss-huggingface-blog` | Hugging Face：Blog（RSS） | `rss` |
| `json-hf-daily-papers` | HuggingFace Daily Papers（社区热门论文） | `json_list` |
| `rss-ithome` | IT之家（RSS） | `rss` |
| `rss-marktechpost` | MarkTechPost（RSS） | `rss` |
| `web-nvidia-ai-blog` | NVIDIA AI Blog | `web_list` |
| `rss-interconnects-ai` | Nathan Lambert：Interconnects（RSS） | `rss` |
| `rss-alignment-openai` | OpenAI：Alignment 研究博客（RSS） | `rss` |
| `rss-openai-news` | OpenAI：官网动态（RSS · 排除企业/客户案例） | `rss` |
| `rss-openrouter-announcements` | OpenRouter：Announcements（RSS） | `rss` |
| `external-qwen-blog-retrieval` | Qwen：Blog Retrieval（API） | `json_list` |
| `web-runway-changelog` | Runway：Changelog（网页） | `web_list` |
| `web-runway-news` | Runway：News（网页） | `web_list` |
| `rss-simon-willison` | Simon Willison 博客 | `rss` |
| `rss-techcrunch-ai` | TechCrunch：AI（RSS） | `rss` |
| `rss-the-decoder` | The Decoder：AI News（RSS） | `rss` |
| `rss-the-verge-ai` | The Verge：AI（RSS） | `rss` |
| `rss-tomtunguz` | Tomer Tunguz 博客（VC 分析） | `rss` |
| `rss-venturebeat-ai` | VentureBeat：AI（RSS） | `rss` |
| `web-xai-news` | xAI：News（网页） | `web_list` |
| `json-zhipuai-research` | 智谱：研究（网页内嵌数据） | `json_list` |
| `json-inclusion-ai-github` | 蚂蚁 inclusionAI：GitHub 新仓库 | `json_list` |
| `json-inclusion-ai-hf` | 蚂蚁 inclusionAI：HuggingFace 新模型 | `json_list` |

## 推文

共识别 91 个主来源。

| ID | 名称 | 类型 |
| --- | --- | --- |
| `x-account-aisafetymemes` | X：AI Safety Memes (@AISafetyMemes) | `x_search` |
| `x-account-akhaliq` | X：AK (@_akhaliq) | `x_search` |
| `x-account-karpathy` | X：Andrej Karpathy (@karpathy) | `x_search` |
| `x-account-anthropic` | X：Anthropic (@AnthropicAI) | `x_search` |
| `x-account-artificialanlys` | X：Artificial Analysis (@ArtificialAnlys) | `x_search` |
| `x-account-astronaut1216` | X：Astronaut (@Astronaut_1216) | `x_search` |
| `x-account-berryxia` | X：Berry Xia (@berryxia) | `x_search` |
| `x-account-bcherny` | X：Boris Cherny (@bcherny) | `x_search` |
| `x-account-chatgpt-app` | X：ChatGPT (@ChatGPTapp) | `x_search` |
| `x-account-claudeai` | X：Claude (@claudeai) | `x_search` |
| `x-account-claudedevs` | X：Claude Devs (@ClaudeDevs) | `x_search` |
| `x-account-deedydas` | X：Deedy Das (@deedydas) | `x_search` |
| `x-account-hassabis` | X：Demis Hassabis (@demishassabis) | `x_search` |
| `x-account-elonmusk` | X：Elon Musk (@elonmusk, xAI) | `x_search` |
| `x-account-elvis-saravia` | X：Elvis Saravia (@omarsar0, DAIR.AI) | `x_search` |
| `x-account-emad-mostaque` | X：Emad Mostaque (@EMostaque) | `x_search` |
| `x-account-epochai` | X：Epoch AI (@EpochAIResearch) | `x_search` |
| `x-account-ericmitchellai` | X：Eric Mitchell (@ericmitchellai) | `x_search` |
| `x-account-emollick` | X：Ethan Mollick (@emollick) | `x_search` |
| `x-account-francois-chollet` | X：Francois Chollet (@fchollet) | `x_search` |
| `x-account-geminiapp` | X：Gemini (@GeminiApp) | `x_search` |
| `x-account-google-ai` | X：Google AI (@GoogleAI) | `x_search` |
| `x-account-googleaidevs` | X：Google AI for Developers (@googleaidevs) | `x_search` |
| `x-account-deepmind` | X：Google DeepMind (@GoogleDeepMind) | `x_search` |
| `x-account-gdb` | X：Greg Brockman (@gdb) | `x_search` |
| `x-account-jeffdean` | X：Jeff Dean (@JeffDean) | `x_search` |
| `x-account-josh-woodward` | X：Josh Woodward (@joshwoodward, Google Labs VP) | `x_search` |
| `x-account-kimmonismus` | X：Kim (@kimmonismus) | `x_search` |
| `x-account-kimi-moonshot` | X：Kimi.ai (@Kimi_Moonshot) | `x_search` |
| `x-account-krea` | X：Krea AI (@krea_ai) | `x_search` |
| `x-account-lilian-weng` | X：Lilian Weng (@lilianweng) | `x_search` |
| `x-account-luma-labs` | X：Luma AI (@LumaLabsAI) | `x_search` |
| `x-account-marc-andreessen` | X：Marc Andreessen (@pmarca, a16z) | `x_search` |
| `x-account-msft-research` | X：Microsoft Research (@MSFTResearch) | `x_search` |
| `x-account-midjourney` | X：Midjourney (@midjourney) | `x_search` |
| `x-account-minimax` | X：MiniMax (@MiniMax_AI) | `x_search` |
| `x-account-nvidia-ai-dev` | X：NVIDIA AI Developer (@NVIDIAAIDev) | `x_search` |
| `x-account-natolambert` | X：Nathan Lambert (@natolambert) | `x_search` |
| `x-account-polynoamial` | X：Noam Brown (@polynoamial) | `x_search` |
| `x-account-notebooklm` | X：NotebookLM (@NotebookLM) | `x_search` |
| `x-account-openai` | X：OpenAI (@OpenAI) | `x_search` |
| `x-account-openaidevs` | X：OpenAI Developers (@OpenAIDevs) | `x_search` |
| `x-account-openclaw` | X：OpenClaw (@openclaw) | `x_search` |
| `x-account-openrouter` | X：OpenRouter (@OpenRouter) | `x_search` |
| `x-account-oran-ge` | X：Oran Ge (@oran_ge) | `x_search` |
| `x-account-perplexity` | X：Perplexity (@perplexity_ai) | `x_search` |
| `x-account-steipete` | X：Peter Steinberger (@steipete) | `x_search` |
| `x-account-pixverse` | X：PixVerse (@PixVerse_) | `x_search` |
| `x-account-replit` | X：Replit (@Replit) | `x_search` |
| `x-account-rohanpaulai` | X：Rohan Paul (@rohanpaul_ai) | `x_search` |
| `x-account-runwayml` | X：Runway (@runwayml) | `x_search` |
| `x-account-sama` | X：Sam Altman (@sama) | `x_search` |
| `x-account-satyanadella` | X：Satya Nadella (@satyanadella) | `x_search` |
| `x-account-semianalysis` | X：SemiAnalysis (@SemiAnalysis_) | `x_search` |
| `x-account-sundarpichai` | X：Sundar Pichai (@sundarpichai) | `x_search` |
| `x-account-suno` | X：Suno (@suno) | `x_search` |
| `x-account-testingcatalog` | X：Testing Catalog (@testingcatalog) | `x_search` |
| `x-account-trq212` | X：Thariq (@trq212) | `x_search` |
| `x-account-thsottiaux` | X：Tibo (@thsottiaux) | `x_search` |
| `x-account-viggleai` | X：Viggle AI (@ViggleAI) | `x_search` |
| `x-account-vista8` | X：Vista (@vista8) | `x_search` |
| `x-account-thexpin` | X：X.PIN (@thexpin) | `x_search` |
| `x-account-ylecun` | X：Yann LeCun (@ylecun) | `x_search` |
| `x-account-yuchenjuw` | X：Yuchen Jin (@Yuchenj_UW) | `x_search` |
| `x-account-zho` | X：ZHO (@ZHO_ZHO_ZHO) | `x_search` |
| `x-account-cb-doge` | X：cb_doge (@cb_doge) | `x_search` |
| `x-account-thdxr` | X：dax (@thdxr) | `x_search` |
| `x-account-karminski3` | X：karminski (@karminski3) | `x_search` |
| `x-account-opencode` | X：opencode (@opencode) | `x_search` |
| `x-account-swyx` | X：swyx (@swyx) | `x_search` |
| `x-account-xai` | X：xAI (@xai) | `x_search` |
| `x-account-huawei-cloud` | X：华为云 (@HuaweiCloud1) | `x_search` |
| `x-account-kling` | X：可灵 Kling AI (@Kling_ai) | `x_search` |
| `x-account-sensetime` | X：商汤 SenseTime (@SenseTime_AI) | `x_search` |
| `x-account-dotey` | X：宝玉 (@dotey) | `x_search` |
| `x-account-xiaohu` | X：小互 (@xiaohu) | `x_search` |
| `x-account-frxiaobei` | X：小北 (@frxiaobei) | `x_search` |
| `x-account-xiaomi-mimo` | X：小米 MiMo (@XiaomiMiMo) | `x_search` |
| `x-account-zai-org` | X：智谱 Z.ai (@Zai_org) | `x_search` |
| `x-account-op7418` | X：歸藏 (@op7418) | `x_search` |
| `x-account-hongming` | X：洪明 (@hongming731) | `x_search` |
| `x-account-baidu` | X：百度 Baidu (@Baidu_Inc) | `x_search` |
| `x-account-siliconflow` | X：硅基流动 SiliconFlow (@SiliconFlowAI) | `x_search` |
| `x-account-tencent-hunyuan` | X：腾讯混元 (@TencentHunyuan) | `x_search` |
| `x-account-antling-agi` | X：蚂蚁百灵 (@AntLingAGI) | `x_search` |
| `x-account-saining-xie` | X：谢赛宁 (@sainingxie) | `x_search` |
| `x-account-alibaba-qwen` | X：通义千问 / Qwen (@Alibaba_Qwen) | `x_search` |
| `x-account-shao-meng` | X：邵猛 (@shao__meng) | `x_search` |
| `x-account-stepfun` | X：阶跃星辰 StepFun (@StepFun_ai) | `x_search` |
| `x-account-ayi-ai-notes` | X：阿易 AI Notes (@AYi_AInotes) | `x_search` |
| `x-account-alibaba-cloud` | X：阿里云 / Alibaba Cloud (@alibaba_cloud) | `x_search` |
