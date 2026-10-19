/* 播客区样页(issue #199)共享数据:单集事实取自真实库(Interconnects 2026-10-17 一期),
   简介导读文字取自生产截图;「精品导读稿」正文为示意(该期在生产上尚未生成),样页说明里已标注。 */
window.PP = window.PP || { options: {} };

PP.episode = {
  source: 'Interconnects',
  title: 'Meta shakes up the frontiers, open and closed',
  date: '2026-10-17 22:06',
  readMinutes: 26,
  readCount: 1,
  durationSeconds: 704,          // 原节目 11:44,署名行四舍五入写「12 分钟」
  guideDurationSeconds: 236,     // 精品导读音频(示意值)3:56
  cover: 'assets/cover-interconnects.jpg',
  sourceUrl: 'https://www.interconnects.ai/p/meta-shakes-up-the-frontiers-open',
  host: 'interconnects.ai',
  tags: ['开源与开放权重 AI', 'Meta', '大语言模型', 'AI 智能体', 'Hugging Face', 'Muse Spark Open'],
  score: '8.6',
  scoreTier: 'is-score-3',
  summary: 'Interconnects 播客单集讨论 Meta 发布开放权重模型 Muse Spark Open（816B 总参数/42B 激活、100 万 token 上下文、原生多模态、面向编码与智能体），称其为当前最强的美国开源模型，使美国开源模型与中国开源模型的差距大致缩小一半。节目同时分析 Meta 此前发布的闭源模型 Muse Wonder 系列，评估 Meta 在 OpenAI、Anthropic、Gemini、xAI 之间的位置，并批评 Muse Spark Open 的自定义许可证含「Built with Muse」标识、月收入超 2000 万美元需单独授权等限制。',
  basisNote: '简介初评 · AI 基于节目简介的初步评估，尚未分析完整音频，仅用于辅助筛选',
  showNotes: [
    'This has been a week where the balance of American open-weight models took a step change back toward the frontier. Meta took the lead of this charge with Muse Spark Open, which is now the strongest American-made open model by quite some margin, but the addition of Reflection’s first model Beam is also relevant. Without these two models, there would be 20+ open-weight models built and released by Chinese companies better than the best Western models.',
    'In many ways, the gap between the best American and Chinese models has been reduced roughly by half. We’ve gone from the best American open models being largely irrelevant to consumers who require the cutting edge of performance, to them being behind but relevant.',
    'This paints the picture of the cut-throat, competitive nature of the frontier model business. Reflection and Mistral announced their model scores and Meta dropped the weights of a smarter model before these other labs could ship theirs.',
  ],
  // 示意:精品导读稿(中文博客 · AI 整理)。真实产物为 digest_blog_zh 分页 markdown。
  guide: [
    ['h3', '一周之内，美国开源模型重回牌桌'],
    ['p', 'Meta 发布开放权重模型 Muse Spark Open，被主持人视为目前最强的美国开源模型；Reflection 的首个模型 Beam 也在同周亮相。主持人的判断是：美国与中国最好的开源模型之间的差距，大约缩小了一半。'],
    ['h3', '闭源的 Muse Wonder 与 Meta 的位置'],
    ['p', '节目把 Muse Spark Open 放回 Meta 此前的闭源 Muse Wonder 系列里看，讨论 Meta 在 OpenAI、Anthropic、Gemini、xAI 之间处在什么位置。'],
    ['h3', '许可证里的限制'],
    ['p', '自定义许可证要求标注「Built with Muse」，月收入超过 2000 万美元的使用方需要单独授权。主持人认为这类条款会带来战略风险。'],
  ],
};

/* 状态全集:与 PodcastAudioPanel / ReaderTab 的真实分支一一对应(不发明后端没有的状态)。
   bodyMode=guide 时,阅读窗正文换成导读稿,AI 卡 / 节目简介 / 逐字稿收起(现网 podcastGuideActive 口径)。 */
PP.states = [
  { id: 's1', label: '原节目 · 可点播', note: '最常见:只有原节目,AI 与点播开着', guideAudio: false, guideText: false, ondemand: 'available', variant: 'original' },
  { id: 's2', label: '导读生成中', note: '点播已提交(queued / summarizing / synthesizing)', guideAudio: false, guideText: false, ondemand: 'generating', variant: 'original' },
  { id: 's3', label: '导读就绪 · 听原节目', note: '精品导读音频已生成,当前选中原节目', guideAudio: true, guideText: true, ondemand: 'done', variant: 'original' },
  { id: 's4', label: '导读就绪 · 听导读', note: '选中精品导读,正文换成导读稿', guideAudio: true, guideText: true, ondemand: 'done', variant: 'digest' },
  { id: 's5', label: '导读稿可读 · 音频未生成', note: '只有中文导读文字,可再点播音频', guideAudio: false, guideText: true, ondemand: 'audio', variant: 'original' },
  { id: 's6', label: '点播未开放', note: 'AI 关闭或本部署不能点播:只有原节目', guideAudio: false, guideText: false, ondemand: 'off', variant: 'original' },
];

PP.stateById = (id) => PP.states.find((s) => s.id === id) || PP.states[0];
PP.bodyMode = (st) => ((st.variant === 'digest' && st.guideAudio) || (st.guideText && !st.guideAudio) ? 'guide' : 'normal');

PP.fmt = {
  clock(sec) {
    const s = Math.max(0, Math.round(sec));
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  },
  minutes(sec) {
    const m = Math.max(1, Math.round(sec / 60));
    return m >= 60 ? `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分钟` : ''}` : `${m} 分钟`;
  },
};

/* lucide 图标(与前端 lucide-react 同一套路径),24 视框、描边 2;调用方用 CSS 控尺寸与颜色 */
const svg = (inner, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${inner}</svg>`;
PP.icons = {
  play: svg('<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>'),
  pause: svg('<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>'),
  back15: svg('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'),
  fwd15: svg('<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'),
  headphones: svg('<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>'),
  sparkles: svg('<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>'),
  // AI 身份四角星(与 .reader-ai-layer-title::before 同一路径)
  spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2c.6 5.4 2.6 7.4 10 10-7.4 2.6-9.4 4.6-10 10-.6-5.4-2.6-7.4-10-10 7.4-2.6 9.4-4.6 10-10Z"/></svg>',
  external: svg('<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
  loader: svg('<path d="M21 12a9 9 0 1 1-6.219-8.56"/>', 'class="animate-spin"'),
  chevronDown: svg('<path d="m6 9 6 6 6-6"/>'),
  x: svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
  arrowLeft: svg('<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>'),
  star: svg('<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/>'),
  share: svg('<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>'),
  more: svg('<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>'),
};
