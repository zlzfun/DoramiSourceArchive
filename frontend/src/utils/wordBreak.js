// 标题按词断行(issue #279 第五批,桌面大标题):中文之间处处可以折行,标题常被折在词中间(「性 / 价比」「智能 / 体」)。
// 这里把标题切成「不该从中间折开的块」,显示层在块与块之间放 <wbr>,配合 word-break: keep-all,行只在块之间折。
//
// 分词用浏览器自带的 Intl.Segmenter(不加依赖、不走后端)。它的词典对科技标题偏碎——人名、公司名、新词
// 常被切成单字(「谷 / 歌」「英伟 / 达」「黄 / 仁 / 勋」)——所以再过一遍合并规则:
//   · 连续的单字并成一块(2–4 个;更长的一串多半是整句被切碎,不合并,回到逐字可折,只有头一个字照落单的规则走);
//   · 落单的单字接到前一块后面(后缀、助词:「英伟|达」「漏洞|的」),除非它是前缀或虚词(「超|740 亿」「与|团队」),那就带着后一段走;
//   · 收尾标点跟前一块(行首不放标点),起首标点带着后一段走(行尾不留开引号、开括号),连续的起首标点并在一起;
//   · 字母数字之间的连接符不产生可折点(GPT-5);写在前面的符号($、#、@)带着后面的内容走。
// 只合并、不改字:块拼回去与原文逐字相等,否则放弃(返回 null,标题原样显示)。切错的代价只是少了几个可折点。
const CJK = /[㐀-鿿]/;
const OPENERS = '「『（《“‘〈【［([{$¥€£#@';   // 起首标点,以及写在前面的符号($100、#话题、@账号):都带着后面的内容走
const JOINERS = "-‑–.·/'’+&_:";              // 字母数字之间的连接符(GPT-5、A.I.、v2/v3):后面不是可折点
const ALNUM_TAIL = /[A-Za-z0-9]$/;
const PUNCT_ONLY = /^[\p{P}\p{S}]+$/u;
const LEADING = new Set('超新最大小全非反多跨再不可已将被让向从在与和为因并及或而是要还也都就才又很更太把对以给仅便曾正刚只能你我他她它这那其');
const MAX_RUN = 4;

const isSingle = (seg) => seg.length === 1 && CJK.test(seg);

/** 合并规则本体(纯函数,入参是分词器给的片段序列)。 */
export function mergeSegments(segments) {
  // 第一遍:连续的单字先并起来
  const tokens = [];
  for (let i = 0; i < segments.length; i += 1) {
    if (!isSingle(segments[i])) { tokens.push({ text: segments[i], kind: 'word' }); continue; }
    let j = i;
    while (j + 1 < segments.length && isSingle(segments[j + 1])) j += 1;
    const run = segments.slice(i, j + 1);
    if (run.length === 1) tokens.push({ text: run[0], kind: 'lone' });
    else if (run.length <= MAX_RUN) tokens.push({ text: run.join(''), kind: 'word' });
    else run.forEach((ch, index) => tokens.push({ text: ch, kind: index === 0 ? 'lone' : 'free' }));   // 头一个字仍按落单处理(多半是前一个词的后缀:「智能|体组团上岗」)
    i = j;
  }
  // 第二遍:落单的字与标点各归其位
  const chunks = [];
  let glueNext = false;   // 上一块在等后面的内容(起首标点、前缀单字、连接符)
  let joinPending = false;   // 这次等待是连接符引起的(遇到空格就作废)
  const last = () => chunks[chunks.length - 1] || '';
  const append = (text) => { chunks[chunks.length - 1] += text; };
  for (const token of tokens) {
    const { text, kind } = token;
    if (!text.trim()) {
      if (chunks.length) append(text); else chunks.push(text);
      if (joinPending) { glueNext = false; joinPending = false; }   // 连接符后面隔了空格:不算连着
      continue;
    }
    if (PUNCT_ONLY.test(text)) {
      const opens = OPENERS.includes(text[text.length - 1]);
      if (glueNext) {
        // 前一块还在等内容(连续的起首标点「（「」、前缀字后面的引号):标点也接上,继续等
        append(text);
      } else if (opens || !chunks.length) {
        chunks.push(text);
        glueNext = true;
      } else {
        // 连接符紧跟在字母数字后面:把后一段粘上(GPT-5 不在连字符后面折)
        const joins = JOINERS.includes(text) && ALNUM_TAIL.test(last());
        append(text);
        glueNext = joins;
        joinPending = joins;
      }
      continue;
    }
    if (glueNext) { append(text); glueNext = kind === 'lone' && LEADING.has(text); joinPending = false; continue; }
    if (kind === 'lone') {
      const prev = last();
      const afterBreak = !prev || PUNCT_ONLY.test(prev.trim().slice(-1));
      if (LEADING.has(text) || afterBreak) { chunks.push(text); glueNext = true; } else append(text);
      continue;
    }
    chunks.push(text);
  }
  return chunks;
}

let segmenter;
const cache = new Map();

/** 标题 → 不该从中间折开的块;没有中文、环境不支持分词、或合并结果对不上原文时返回 null。 */
export function titleChunks(text) {
  const src = String(text || '');
  if (!src || !CJK.test(src)) return null;
  if (cache.has(src)) return cache.get(src);
  let chunks = null;
  try {
    if (segmenter === undefined) segmenter = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter('zh', { granularity: 'word' }) : null;
    if (segmenter) {
      const merged = mergeSegments([...segmenter.segment(src)].map((part) => part.segment));
      if (merged.length > 1 && merged.join('') === src) chunks = merged;
    }
  } catch { chunks = null; }
  if (cache.size > 600) cache.clear();
  cache.set(src, chunks);
  return chunks;
}

const ALNUM = /[A-Za-z0-9]/;
/** 两块之间是不是「汉字贴着字母数字」:<wbr> 会让浏览器的中西文自动间隙失效,显示层要在这里补一小截。 */
export function needsGap(prev, next) {
  const a = prev.slice(-1);
  const b = next[0];
  return (CJK.test(a) && ALNUM.test(b)) || (ALNUM.test(a) && CJK.test(b));
}
