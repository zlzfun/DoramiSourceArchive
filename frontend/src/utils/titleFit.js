// 标题挑折行点(issue #279 第五批补,桌面大标题)。
//
// 按词断行(utils/wordBreak)只保证「不从词中间折」,折在哪两个词之间仍由浏览器的均衡折行(text-wrap: balance)定——
// 它只看各行是否等宽,不认停顿:「……辞去 / 英伟达 CEO 职务,市值 / 蒸发 1.4 万亿美元」,逗号后面甩下一个词。
// 这里在量好每块宽度之后自己挑:行数取逐行铺满时的最少行数,在所有排得下的分法里取代价最小的一种——
//   · 行宽沿用所在标题原来的风格:均衡折行(text-wrap: balance)的标题各行尽量等宽;其余的照旧先铺满前面的行,
//     只是末行不能太短;
//   · 折在停顿处有奖励:逗号、冒号、分号、问号叹号、破折号、竖线、两段汉字之间的空格(强);引号括号的外侧、顿号(弱);
//   · 把一个分句折开时,折点离分句任一端太近要罚(行尾甩一个词、行首只剩一个词);
//   · 有些地方折开要罚:数字与紧跟的量词单位之间(1.4 | 万亿)、连着的西文词之间(Gemini 4 | Hydrogen,多半是一个名字)、
//     把「如何」「是否」「以及」这类引出下文的词留在行尾。
// 可折的位置比分块多一种:块里面的空格(「让 Anthropic 的」是一块,但浏览器在空格处照样能折)。这里把块按空格再切成「片」,
// 片与片之间也能折,只是要罚——否则算出来的最少行数会比浏览器多一行。
// 纯函数,不碰 DOM;宽度由显示层(components/DisplayTitle + hooks/useTitleFit)量好传进来。
const STRONG_WIDE = '，；：。！？｜】';    // 全角停顿:出现即算(】是栏目名的收尾:【特辑】后面就是正题)
const STRONG_ASCII = ',;:!?|';             // 半角停顿:后面跟着空格才算(1,000、10:30 不是停顿)
const DASHES = '—–';                       // 破折号:前后都是停顿
const DASH_ONLY = /^\s*[—–]+\s*$/;
const TRAILING_DASH = /^(\s*\S*[^\s—–A-Za-z0-9])([—–]+\s*)$/;   // 前面不是字母数字的收尾破折号
const CLOSERS = '」』）》”’"\')]〉';
const OPENERS = '「『（《“‘〈【［([';
const CJK = /[㐀-鿿]/;
const NUMBER_TAIL = /[0-9][0-9.,]*$/;
// 紧跟在数字后面的量词、单位的头一个字(1.4 万亿、217 条、3 天、400 年);版本号后面的正文不算(5.2 | 深夜)
const UNIT_HEAD = /^[万亿千百兆个位条款项名人次年月日天周时分秒季倍成元美欧块度级代台辆颗只种类家座套份篇张页组支批轮场届期号层米克吨岁]/;
const within = (set, ch) => ch !== '' && set.includes(ch);   // ''.includes 恒真:空字符不算在集合里

/**
 * 分块 → 片:每块按空格再切开(空格跟着前一片);收尾的破折号单独成片(分块时它跟着前一块,但在它前面、后面折都自然;
 * 字母数字后面的短横不算——1–2、GPT–5 是连在一起的)。
 * glued = 这一片不是所在块的第一片、也不挨着破折号(它前面的折点在块里面,折开要罚)。片拼回去与分块逐字相等。
 */
export function titleUnits(chunks) {
  const units = [];
  chunks.forEach((chunk) => {
    let afterDash = false;
    (chunk.match(/\s*\S+\s*/g) || [chunk]).forEach((piece, index) => {
      const dashed = piece.match(TRAILING_DASH);
      const glued = index > 0 && !afterDash && !DASH_ONLY.test(piece);
      if (dashed) {
        units.push({ text: dashed[1], glued }, { text: dashed[2], glued: false });
      } else {
        units.push({ text: piece, glued });
      }
      afterDash = Boolean(dashed) || DASH_ONLY.test(piece);
    });
  });
  return units;
}

/** 片 chunk 后面(下一片是 next)的停顿级别:2 强、1 弱、0 没有。 */
export function stopAfter(chunk, next = '') {
  const body = chunk.trimEnd();
  const spaced = body.length < chunk.length;
  let core = body;
  while (core && CLOSERS.includes(core[core.length - 1])) core = core.slice(0, -1);
  const tail = core[core.length - 1] || '';
  if (within(STRONG_WIDE, tail) || within(DASHES, tail) || within(DASHES, next[0] || '')) return 2;
  if (spaced && (within(STRONG_ASCII, tail) || tail === '-')) return 2;
  if (spaced && CJK.test(tail) && CJK.test(next[0] || '')) return 2;   // 两段汉字之间的空格:标题里当逗号用
  if (core.length < body.length) return 1;                              // 收尾的引号、括号
  if (tail === '、') return 1;
  if (within(OPENERS, next[0] || '')) return 1;                         // 后一片以引号、括号起头
  return 0;
}

const NUMBER = 12;     // 数字与紧跟的量词、单位之间折开(中间没有空格:1.4 | 万亿)
const LATIN = 10;      // 连着的西文词之间折开(Gemini 4 | Hydrogen)
const TAIL_WORD = 3;   // 把引出下文的词留在行尾
const HEAD_PARTICLE = 6;   // 行首是「的」「之」(之名、的 AI):它们该跟着前面
const ALNUM = /[A-Za-z0-9]/;
// 引出下文的两字词:疑问、连接、介引。单字的(与、和、在、让……)分块时已经带着后一段走了(utils/wordBreak 的 LEADING)
const TAIL_WORDS = ['如何', '为何', '是否', '能否', '怎样', '怎么', '以及', '并且', '而且', '但是', '因为', '通过', '关于', '对于', '成为', '作为', '用于', '来自', '正在', '即将'];

/** 在片 chunk 与下一片 next 之间折开的额外代价(0 = 没有讲究)。 */
export function bondAfter(chunk, next = '') {
  if (!next) return 0;
  const body = chunk.trimEnd();
  const spaced = body.length < chunk.length;
  const tail = body[body.length - 1] || '';
  if (!spaced && NUMBER_TAIL.test(body) && UNIT_HEAD.test(next)) return NUMBER;
  if (spaced && ALNUM.test(tail) && ALNUM.test(next[0])) return LATIN;
  if ('的之'.includes(next[0])) return HEAD_PARTICLE;
  if (TAIL_WORDS.some((word) => body.endsWith(word))) return TAIL_WORD;
  return 0;
}

const RAG = 100;       // 均衡:行宽偏离平均值的代价 = RAG × (偏差 / 行宽上限)²,每行一项
const SLACK = 40;      // 铺满:前面各行没排满的代价 = SLACK × (空出的 / 行宽上限)²
const WIDOW = 12;      // 铺满:末行不足行宽三成的代价上限
const WIDOW_AT = 0.3;
const STOP_STRONG = 8; // 折在强停顿处的奖励
const STOP_WEAK = 3;   // 折在弱停顿处的奖励
const STRAND = 24;     // 把分句折开、折点离分句一端太近的代价上限
const STRAND_EM = 4;   // 「太近」的尺度:离分句一端不足 4 个字宽
const GLUE = 10;       // 在块里面的空格处折开的代价(前缀、虚词本该带着后一段走)
const MAX_LINES = 12;

/**
 * 挑折行点。
 * @param starts / ends 每片在「不折行排成一行」时的左右边位置(同一单位,通常是 px)
 * @param stops 每片后面的停顿级别(stopAfter)
 * @param bonds 在每片后面折开的额外代价(bondAfter)
 * @param glued 每片是不是所在块的后续片(titleUnits)
 * @param limit 一行最多排多宽;em 一个字宽(同一单位)
 * @param balance 各行尽量等宽(true),还是先铺满前面的行(false)
 * @returns 各行(第一行除外)的起始片下标,升序;一行排得下返回 [];排不了(某一片比行还宽、行数过多)返回 null,交回浏览器。
 */
export function pickBreaks({ starts, ends, stops, bonds = [], glued = [], limit, em, balance = true }) {
  const n = starts.length;
  if (!n || !(limit > 0) || !(em > 0)) return null;
  const width = (i, j) => ends[j - 1] - starts[i];
  if (width(0, n) <= limit) return [];
  for (let i = 0; i < n; i += 1) if (width(i, i + 1) > limit) return null;

  // 行数:逐行铺满得到的最少行数
  let lines = 1;
  for (let i = 0, j = 1; j <= n; j += 1) {
    if (j < n && width(i, j + 1) > limit) { lines += 1; i = j; }
  }
  if (lines > MAX_LINES) return null;

  // 每个折点(在片 b 之前折)的代价
  const reach = Math.min(STRAND_EM * em, limit * 0.4);
  const clauseStart = new Array(n).fill(0);   // 片 b 所在分句的第一片
  const clauseEnd = new Array(n).fill(n);     // 片 b 所在分句的末片之后
  for (let b = 1; b < n; b += 1) clauseStart[b] = stops[b - 1] === 2 ? b : clauseStart[b - 1];
  for (let b = n - 2; b >= 0; b -= 1) clauseEnd[b] = stops[b] === 2 ? b + 1 : clauseEnd[b + 1];
  const breakCost = new Array(n).fill(0);
  for (let b = 1; b < n; b += 1) {
    if (stops[b - 1] === 2) { breakCost[b] = -STOP_STRONG; continue; }
    const before = width(clauseStart[b - 1], b);
    const after = width(b, clauseEnd[b]);
    const near = Math.max(0, 1 - Math.min(before, after) / reach);
    breakCost[b] = STRAND * near * near - (stops[b - 1] === 1 ? STOP_WEAK : 0) + (bonds[b - 1] || 0) + (glued[b] ? GLUE : 0);
  }

  // 动态规划:best[k][j] = 前 j 片排成 k 行的最小代价
  const mean = width(0, n) / lines;
  const lineCost = (i, j) => {
    const w = width(i, j);
    if (balance) { const off = (w - mean) / limit; return RAG * off * off; }
    if (j < n) { const slack = Math.max(0, limit - w) / limit; return SLACK * slack * slack; }
    const short = Math.max(0, 1 - w / limit / WIDOW_AT);
    return WIDOW * short * short;
  };
  const best = Array.from({ length: lines + 1 }, () => new Array(n + 1).fill(Infinity));
  const from = Array.from({ length: lines + 1 }, () => new Array(n + 1).fill(-1));
  best[0][0] = 0;
  for (let k = 1; k <= lines; k += 1) {
    for (let j = k; j <= n; j += 1) {
      for (let i = j - 1; i >= k - 1; i -= 1) {
        if (width(i, j) > limit) break;
        if (best[k - 1][i] === Infinity) continue;
        const cost = best[k - 1][i] + lineCost(i, j) + (i > 0 ? breakCost[i] : 0);
        if (cost < best[k][j]) { best[k][j] = cost; from[k][j] = i; }
      }
    }
  }
  if (best[lines][n] === Infinity) return null;
  const heads = [];
  for (let k = lines, j = n; k > 1; k -= 1) { j = from[k][j]; heads.unshift(j); }
  return heads;
}
