/* 全局播放条样页 · 社交流与发现页数据:取自本机沙箱库(GET /api/articles?shape=social、GET /api/reader/sources)的真实推文与来源目录。
   头像与配图经沙箱媒体库代理取回、缩小后存在 assets/ppg-social/;Kimi 的头像上游已失效(两种尺寸都 404),
   按现网 SocialAvatar 的降级规则显示首字母。转推按现网契约:头像 / 名字跟原作者走,正文用原推全文。 */
window.PP = window.PP || { options: {} };
PP.social = {
 "accounts": [
  {
   "source_id": "x_openai",
   "name": "X · OpenAI",
   "author_name": "OpenAI",
   "handle": "OpenAI",
   "avatar": "assets/ppg-social/avatar-openai.jpg",
   "description": "OpenAI 官方账号；作为 X 与官网新闻时效/重复率对照组。",
   "subscribers": 8
  },
  {
   "source_id": "x_alibaba_qwen",
   "name": "X · Qwen",
   "author_name": "Qwen",
   "handle": "Alibaba_Qwen",
   "avatar": "assets/ppg-social/avatar-alibaba_qwen.jpg",
   "description": "通义千问官方模型、开源与产品动态。",
   "subscribers": 0
  },
  {
   "source_id": "x_sama",
   "name": "X · Sam Altman",
   "author_name": "Sam Altman",
   "handle": "sama",
   "avatar": "assets/ppg-social/avatar-sama.jpg",
   "description": "Sam Altman 的 OpenAI 战略、发布口风与一手动态。",
   "subscribers": 2
  },
  {
   "source_id": "x_karpathy",
   "name": "X · Andrej Karpathy",
   "author_name": "Andrej Karpathy",
   "handle": "karpathy",
   "avatar": "assets/ppg-social/avatar-karpathy.jpg",
   "description": "Andrej Karpathy 的模型训练、Agent 与工程观察。",
   "subscribers": 4
  },
  {
   "source_id": "x_moonshot_ai",
   "name": "X · Moonshot AI",
   "author_name": "Kimi.ai",
   "handle": "Kimi_Moonshot",
   "avatar": "",
   "description": "Moonshot AI / Kimi 官方模型与开源动态。",
   "subscribers": 2
  }
 ],
 "posts": [
  {
   "id": "x_openai_2080378182469857576",
   "source_id": "x_openai",
   "url": "https://x.com/OpenAI/status/2080378182469857576",
   "date": "2026-07-23T19:43:04.000Z",
   "author_name": "OpenAI",
   "handle": "OpenAI",
   "avatar": "assets/ppg-social/avatar-openai.jpg",
   "text": "ChatGPT Voice is now in the desktop app.\n\nControl your computer and direct multiple agents running  in ChatGPT Work or Codex, using just your voice. \n\nIt's powered by GPT-Live, so it can speak, listen, and coordinate work in the app at the same time.\n\nRolling out globally today on macOS and Windows to Plus, Pro, Business, Edu, and Enterprise plans.",
   "media": [
    "assets/ppg-social/media-2080378182469857576-0.jpg"
   ]
  },
  {
   "id": "x_alibaba_qwen_2080270065547809133",
   "source_id": "x_alibaba_qwen",
   "url": "https://x.com/Alibaba_Qwen/status/2080270065547809133",
   "date": "2026-07-23T12:33:27.000Z",
   "author_name": "Qwen",
   "handle": "Alibaba_Qwen",
   "avatar": "assets/ppg-social/avatar-alibaba_qwen.jpg",
   "text": "Introducing the Qwen-Audio-3.0-TTS.\n\nOur latest text-to-speech model, in two flavors:\n• Flash: real-time interaction\n• Plus: high-quality generation\n\nWhat's new:\n• Fine-grained inline tags-steer [whisper], [angry], [breaths] & [laughs]\n• Free-style natural-language control-“read this slowly, like a bedtime story”\n• 16 languages\n• Clean output even from noisy reference audio\n• One-pass long-form up to 3 min\n\nnow #1 on the Artificial Analysis TTS Leaderboard.\n\nBlog: https://t.co/ACqTI2wGHI\nAPI: https://t.co/Xh2Y19noVx",
   "media": [
    "assets/ppg-social/media-2080270065547809133-0.jpg",
    "assets/ppg-social/media-2080270065547809133-1.jpg"
   ]
  },
  {
   "id": "x_sama_2080392429484843295",
   "source_id": "x_sama",
   "url": "https://x.com/sama/status/2080392429484843295",
   "date": "2026-07-23T20:39:41.000Z",
   "author_name": "Karan Singhal",
   "handle": "thekaransinghal",
   "avatar": "",
   "text": "We're rolling out Health in ChatGPT to all U.S. users. ♥️\n\nMore than 300 million people come to ChatGPT every week with health questions. Today, ChatGPT can bring together the health info you choose to connect to make conversations more personal and useful. https://t.co/LlLlWyiAAi",
   "media": [],
   "reposter": "Sam Altman"
  },
  {
   "id": "x_sama_2079661132302995790",
   "source_id": "x_sama",
   "url": "https://x.com/sama/status/2079661132302995790",
   "date": "2026-07-21T20:13:46.000Z",
   "author_name": "Sam Altman",
   "handle": "sama",
   "avatar": "assets/ppg-social/avatar-sama.jpg",
   "text": "we had a significant security incident during evaluation of our models. we are sharing what we have learned so far. thanks to @huggingface for the partnership on this.\n\nhttps://t.co/2o2VfR6PIa",
   "media": []
  },
  {
   "id": "x_karpathy_2079610838143623371",
   "source_id": "x_karpathy",
   "url": "https://x.com/karpathy/status/2079610838143623371",
   "date": "2026-07-21T16:53:55.000Z",
   "author_name": "Andrej Karpathy",
   "handle": "karpathy",
   "avatar": "assets/ppg-social/avatar-karpathy.jpg",
   "text": "One pattern I find useful for working with LLMs is a nice long ramble session. Sometimes the LLM needs more bits to understand what you're trying to achieve, but you're too lazy to type them. In these cases I like to lean back, switch to /voice and just ramble for like 10 minutes, total mess, anything goes, full stream of consciousness. Sometimes I declare it up top, something like \"switching to speech recognition sorry for any typos...\". Sometimes I turn it into a small interview of a few turns. But I find that the LLMs are somehow very good at reconstructing long incoherent rambles and often their echo of your own tangle of thoughts comes out quite a bit cleaner than what you started with. The result is that you improve the mind meld and have to correct things less from that point on.",
   "media": []
  },
  {
   "id": "x_alibaba_qwen_2079906336381509659",
   "source_id": "x_alibaba_qwen",
   "url": "https://x.com/Alibaba_Qwen/status/2079906336381509659",
   "date": "2026-07-22T12:28:08.000Z",
   "author_name": "Qwen",
   "handle": "Alibaba_Qwen",
   "avatar": "assets/ppg-social/avatar-alibaba_qwen.jpg",
   "text": "🎨 Meet Qwen-Image-3.0 — the third generation of our foundational image generation model.\n\nIf 1.0 was about \"Precision,\" and 2.0 added \"Variety, Completeness, Beauty & Authenticity,\" then 3.0 comes down to a single word: Real (实).\n\nThree dimensions of \"Real\":\n📰 Rich Content — prompts up to 4.5k tokens. One-pass generation of complex layouts: newspapers, storyboards, exam papers — even a 3×3 infographic grid or picture-in-picture-in-picture UIs.\n🔬 Authentic Details — text legible down to 10px, full LaTeX paper pages, pores, hair strands & near-photographic skin texture.\n🌏 Deep Knowledge — native rendering in 12 languages, 100+ art styles, realistic UIs (web / games / livestreams), plus world knowledge & live web retrieval.\n\nNot just \"good-looking\" — genuinely useful. Image generation as a real productivity tool for design, content, education & e-commerce.\n\nGo create 🏃🎨\n\n💬Qwen Chat: https://t.co/941HmITJ2W\n📝Blog: https://t.co/5mnS4uI9Ar",
   "media": [
    "assets/ppg-social/media-2079906336381509659-0.jpg"
   ]
  },
  {
   "id": "x_moonshot_ai_2078855608565207130",
   "source_id": "x_moonshot_ai",
   "url": "https://x.com/Kimi_Moonshot/status/2078855608565207130",
   "date": "2026-07-19T14:52:55.000Z",
   "author_name": "Kimi.ai",
   "handle": "Kimi_Moonshot",
   "avatar": "",
   "text": "Kimi K3 has received far more love than we expected, and our GPUs are feeling it.\n\nOver the past 48 hours, demand has pushed close to the limits of our current capacity. To protect the experience of existing subscribers, we're temporarily pausing new subscriptions and prioritizing compute for current members. Existing subscribed users are not affected.\n\nWe're adding capacity as fast as we can and will reopen new subscription spots in batches.\n\nGoing forward, we'll also split membership into two more focused plans: Kimi Membership for Kimi Web, App, and Work; and Kimi Code Membership for coding workflows. This will help us match compute more precisely and keep the experience stable.\n\nThank you for your patience and understanding!",
   "media": []
  },
  {
   "id": "x_moonshot_ai_2078488385141031161",
   "source_id": "x_moonshot_ai",
   "url": "https://x.com/Kimi_Moonshot/status/2078488385141031161",
   "date": "2026-07-18T14:33:42.000Z",
   "author_name": "Kimi.ai",
   "handle": "Kimi_Moonshot",
   "avatar": "",
   "text": "Rebuilt a 3D globe dashboard.\nhttps://t.co/kPyQsEyIG0",
   "media": [],
   "quoted": {
    "author_name": "Kai",
    "handle": "hqmank",
    "text": "Everyone keeps talking about how good Kimi K3 is.\n\nSo I gave it one of my favorite frontend tests:\n\none prompt + one reference image.\n\nThe task: rebuild a 3D globe dashboard. 🌎\n\nThe result was almost too good.\n\nIt did not just build a working page.\n\nThe texture, lighting, spacing, depth, glass panels, and overall layout were all remarkably close to the reference.\n\nSome parts felt nearly pixel-perfect.\n\nMy honest first impression: for this kind of frontend work, Kimi K3 might be slightly better than Fable 5.\n\nAll at Sonnet prices.",
    "url": "https://x.com/hqmank/status/2078104317027094907"
   }
  }
 ]
};
PP.discover = [
 {
  "source_id": "podcast_interconnects",
  "name": "Interconnects",
  "shape": "podcast",
  "description": "开放模型、强化学习与 AI 产业分析。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_interconnects.jpg",
  "subscribed": true
 },
 {
  "source_id": "podcast_cognitive_revolution",
  "name": "The Cognitive Revolution",
  "shape": "podcast",
  "description": "AI 技术进展及其现实应用访谈。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_cognitive_revolution.jpg",
  "subscribed": true
 },
 {
  "source_id": "podcast_42chapter",
  "name": "42章经",
  "shape": "podcast",
  "description": "创业、投资与科技趋势中文访谈。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_42chapter.jpg",
  "subscribed": true
 },
 {
  "source_id": "podcast_asi_pill",
  "name": "ASI pill",
  "shape": "podcast",
  "description": "AI 新闻与观点型节目。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_asi_pill.jpg",
  "subscribed": true
 },
 {
  "source_id": "podcast_talk_python",
  "name": "Talk Python To Me",
  "shape": "podcast",
  "description": "Python 生态、数据与软件工程访谈。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_talk_python.jpg",
  "subscribed": true
 },
 {
  "source_id": "podcast_latent_space",
  "name": "Latent Space",
  "shape": "podcast",
  "description": "面向 AI 工程师的模型、Agent 与基础设施访谈。",
  "subscribers": 1,
  "cover": "assets/covers/podcast_latent_space.jpg",
  "subscribed": true
 },
 {
  "source_id": "web_ithome_ai",
  "name": "IT之家 AI",
  "shape": "article",
  "description": "IT之家智能时代频道的 AI 模型、产品、智能体和产业资讯。",
  "subscribers": 9,
  "cover": "",
  "subscribed": true
 },
 {
  "source_id": "x_openai",
  "name": "X · OpenAI",
  "shape": "social",
  "description": "OpenAI 官方账号；作为 X 与官网新闻时效/重复率对照组。",
  "subscribers": 8,
  "cover": "assets/ppg-social/avatar-openai.jpg",
  "subscribed": true
 },
 {
  "source_id": "x_alibaba_qwen",
  "name": "X · Qwen",
  "shape": "social",
  "description": "通义千问官方模型、开源与产品动态。",
  "subscribers": 0,
  "cover": "assets/ppg-social/avatar-alibaba_qwen.jpg",
  "subscribed": false
 },
 {
  "source_id": "x_sama",
  "name": "X · Sam Altman",
  "shape": "social",
  "description": "Sam Altman 的 OpenAI 战略、发布口风与一手动态。",
  "subscribers": 2,
  "cover": "assets/ppg-social/avatar-sama.jpg",
  "subscribed": false
 },
 {
  "source_id": "x_karpathy",
  "name": "X · Andrej Karpathy",
  "shape": "social",
  "description": "Andrej Karpathy 的模型训练、Agent 与工程观察。",
  "subscribers": 4,
  "cover": "assets/ppg-social/avatar-karpathy.jpg",
  "subscribed": false
 },
 {
  "source_id": "x_moonshot_ai",
  "name": "X · Moonshot AI",
  "shape": "social",
  "description": "Moonshot AI / Kimi 官方模型与开源动态。",
  "subscribers": 2,
  "cover": "",
  "subscribed": false
 }
];
