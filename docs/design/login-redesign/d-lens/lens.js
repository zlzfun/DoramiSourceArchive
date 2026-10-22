/* 哆啦美 · 登录页重设计样页 · 方向 D「透镜 Lens」
 *
 * 画面是一张全屏 WebGL 画布:珍珠底 + 失焦的文字场(产品能力短句的排印) + 清晰的题词
 * + 一枚液态玻璃透镜(折射放大 / 边缘色散 / 菲涅尔高光 / 柔影与焦散)。
 * 文字场先用 Canvas 2D 排好:清晰版按 DPR × 放大率渲染(透镜里看到的就是它),
 * 另渲一张半分辨率版交给 GPU 做两级高斯模糊(透镜外看到的)。片元着色器按透镜的 SDF
 * 决定每个像素看到「失焦」还是「折射后的清晰」。
 * 无 WebGL → DOM 降级(圆角裁切 + 放大的清晰副本 + CSS 玻璃边),同一套物理驱动。
 * 动效档位读 <html data-motion>,监听 sample:motion;减弱档 = 透镜静止在「看清」上的完成态。
 */
(() => {
  'use strict';

  const root = document.documentElement;
  const params = new URLSearchParams(location.search);
  const $ = (s) => document.querySelector(s);
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const prog = (t, a, b) => clamp((t - a) / (b - a), 0, 1);
  const out3 = (t) => 1 - (1 - t) ** 3;
  const in3 = (t) => t * t * t;
  const io3 = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  const now = () => performance.now();

  const FONT = {
    sans: '-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
    serif: '"Dorami Serif", "Songti SC", "Microsoft YaHei UI", "Microsoft YaHei", serif',
    latin: '"Dorami Latin Serif", "Dorami Serif", Georgia, serif',
  };
  const INK = (a) => `rgba(22, 25, 34, ${a})`;
  const HI_INK = '#4745b3';
  const HI_MARK = 'rgba(82, 82, 197, 0.15)';
  const PEARL = '#eceef1';

  // ───────── 本地时间(客户端真实可得,允许真用) ─────────
  const WEEK = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  const today = new Date();
  const dateCN = `${today.getFullYear()} 年 ${today.getMonth() + 1} 月 ${today.getDate()} 日`;
  const weekCN = WEEK[today.getDay()];
  const greeting = (h) => (h < 5 ? '夜深了' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : h < 23 ? '晚上好' : '夜深了');
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { tz = ''; }

  // ───────── 文字场文案:只用产品能力与品牌语句;不是新闻标题,不含任何计数 ─────────
  const CARDS = {
    brief: { h: '个人早报', en: 'Personal Brief', b: ['每天清晨，一份', { hi: '只属于你' }, '的早报：按你的兴趣挑选，重大事件、深度阅读、播客精选，各占一栏。', { en: 'Every morning, a brief of your own.' }] },
    translate: { h: 'AI 翻译', en: 'Translate', b: ['外文不再是门槛。一键译成中文，', { hi: '原文就在旁边' }, '，随时对照着读。', { en: 'Any language, with the original beside it.' }] },
    ask: { h: '问答', en: 'Ask', b: ['有问题，直接问。回答', { hi: '附带出处' }, '，每一句都能找到原文。', { en: 'Every answer cites its sources.' }] },
    podcast: { h: '播客', en: 'Podcasts', b: ['先读节目要点，再决定听不听；想听的，就在阅读器里接着听。', { en: 'Skim the show before you press play.' }] },
    interest: { h: '兴趣', en: 'Interests', b: ['兴趣随时修改，明天的早报', { hi: '跟着你变' }, '。', { en: 'Revise your interests; tomorrow’s brief follows.' }] },
    daily: { h: '公共日报', en: 'The Daily', b: ['一份写给所有人的日报：去掉重复，', { hi: '只留要点' }, '。', { en: 'Duplicates gone, essentials kept.' }] },
    search: { h: '搜索', en: 'Search', b: ['读过的、还没读的，都能搜到。', { en: 'Find what you read, and what you missed.' }] },
    social: { h: '社交流', en: 'Social', b: ['值得一看的帖子，也收进同一个阅读器。', { en: 'Posts worth reading, in the same quiet place.' }] },
    summary: { h: '摘要', en: 'Summary', b: ['长文先看摘要，再决定要不要读完；', { hi: '省下的时间' }, '，留给真正想读的。'] },
    saved: { h: '收藏', en: 'Saved', b: ['遇到好文章，先收起来，改天慢慢读。', { en: 'Keep the good ones for a slower day.' }] },
    share: { h: '分享', en: 'Share', b: ['读到好东西，一个链接就能分享给朋友。', { en: 'Share a good read with a single link.' }] },
    guide: { h: '导读', en: 'Guided Read', b: ['长文太长？导读', { hi: '替你划出重点' }, '。', { en: 'A guided path through the long reads.' }] },
    clear: { h: '看清', en: 'See clearly', b: ['信息很多，重点很少。哆啦美替你读完，', { hi: '再替你看清' }, '。', { en: 'So much to read, so little that matters.' }] },
  };
  const DISPLAY = {
    早报: ['BRIEF', 'a brief of your own'],
    翻译: ['TRANSLATE', 'the original stays beside it'],
    问答: ['ASK', 'answers that cite their sources'],
    播客: ['LISTEN', 'read it before you play it'],
    兴趣: ['INTERESTS', 'yours to revise'],
    看清: ['SEE CLEARLY', 'brought into focus'],
    摘要: ['SUMMARY', 'the long read, made short'],
  };
  const QUOTES = [
    { q: 'Less noise.\nMore signal.', cn: '少一点噪音，多一点信号。' },
    { q: 'Read less,\nknow more.', cn: '读得更少，知道得更多。' },
    { q: 'See clearly.', cn: '替你读完，替你看清。' },
  ];
  const PARAS = [
    ['早报在清晨送达，兴趣决定它的样子。', { en: 'A morning brief, shaped by your interests.' }, '外文一键译成中文，原文留在旁边。', { en: 'Translate, and keep the original.' }, '有问题就问，回答附带出处。', '播客先读要点，再决定听不听。', '读不完的交给摘要，', { hi: '值得读的' }, '放到眼前。'],
    ['一个阅读器，装下早报、日报、播客与社交流。', { en: 'One reader for briefs, dailies, podcasts and posts.' }, '兴趣随时修改，明天的早报跟着变。遇到好文章先收藏，读到好东西就分享。', { en: 'Save it, share it, read it slowly.' }, '信息很多，重点很少——', { hi: '哆啦美替你看清' }, '。'],
  ];
  const INDEX = [['个人早报', 'Brief'], ['公共日报', 'Daily'], ['AI 翻译', 'Translate'], ['问答', 'Ask'], ['播客', 'Listen'], ['兴趣', 'Interests'], ['搜索', 'Search']];
  // 瀑布流的投放顺序:卡片 / 展示字 / 目录 / 引语 / 段落 / 彩蛋 交替,保证模糊后的灰度节奏有疏有密
  const SEQ = [
    ['card', 'brief', 30], ['disp', '早报'], ['card', 'translate', 22], ['index'], ['card', 'ask', 26],
    ['quote', 0], ['para', 0], ['disp', '翻译'], ['card', 'podcast', 22], ['egg'], ['card', 'clear', 34],
    ['card', 'interest', 22], ['disp', '问答'], ['card', 'daily', 26], ['quote', 1], ['card', 'search', 20],
    ['para', 1], ['disp', '播客'], ['card', 'social', 22], ['card', 'summary', 26], ['card', 'saved', 20],
    ['disp', '兴趣'], ['card', 'share', 22], ['card', 'guide', 26], ['quote', 2], ['disp', '看清'], ['disp', '摘要'],
  ];

  const SWAP = ['search', 'saved', 'share', 'social', 'podcast', 'guide'];

  // ───────── 排版引擎(Canvas 2D):中西混排、两端对齐、标点悬挂、中西文间距 ─────────
  const mctx = document.createElement('canvas').getContext('2d');
  const RX = /(\p{Script=Han})|([A-Za-z0-9’'.,;:!?&+/–-]+)|(\s+)|(\S)/gu;
  const CLOSE = '，。、：；！？）」』》”’…';
  const OPEN = '（「『《“‘';
  const measure = (s, font) => { mctx.font = font; return mctx.measureText(s).width; };
  const T = (s, x, y, font, color, extra) => Object.assign({ k: 't', s, x, y, font, color }, extra || null);
  const shifted = (ops, dx, dy) => ops.map((o) => {
    const c = Object.assign({}, o);
    if ('x' in c) c.x += dx;
    if ('y' in c) c.y += dy;
    if ('x1' in c) { c.x1 += dx; c.x2 += dx; }
    if ('y1' in c) { c.y1 += dy; c.y2 += dy; }
    return c;
  });

  function para(runs, width, o) {
    const size = o.size, enSize = o.en || size + 1, em = size;
    const toks = [];
    for (const r of runs) {
      const style = typeof r === 'string' ? 'cn' : r.hi ? 'hi' : 'en';
      const text = typeof r === 'string' ? r : r.hi || r.en;
      const font = style === 'en' ? `italic 400 ${enSize}px ${FONT.latin}` : `${style === 'hi' ? 600 : 400} ${size}px ${FONT.sans}`;
      for (const m of text.matchAll(RX)) {
        let s = m[0], kind;
        if (m[1]) kind = 'cjk';
        else if (m[2]) kind = 'lat';
        else if (m[3]) { kind = 'sp'; s = ' '; }
        else kind = CLOSE.includes(s) ? 'close' : OPEN.includes(s) ? 'open' : 'sym';
        toks.push({ s, kind, style, font, w: measure(s, font), g: 0 });
      }
    }
    const gapOf = (a, b) => (a && ((a.kind === 'cjk' && b.kind === 'lat') || (a.kind === 'lat' && b.kind === 'cjk')) ? em * 0.25 : 0);
    const lines = [];
    let cur = [], curW = 0;
    for (const t of toks) {
      if (t.kind === 'sp' && (!cur.length || cur[cur.length - 1].kind === 'sp')) continue;
      const g = gapOf(cur[cur.length - 1], t);
      if (t.kind !== 'sp' && cur.length && curW + g + t.w > width) {
        if (t.kind === 'close') { t.g = g; t.hang = true; cur.push(t); curW += g + t.w; continue; }
        const carry = [];
        while (cur.length && cur[cur.length - 1].kind === 'open') carry.unshift(cur.pop());
        while (cur.length && cur[cur.length - 1].kind === 'sp') cur.pop();
        if (cur.length) lines.push(cur);
        cur = carry;
        curW = carry.reduce((s, c) => s + c.w, 0);
        t.g = gapOf(cur[cur.length - 1], t);
        cur.push(t);
        curW += t.g + t.w;
        continue;
      }
      t.g = g;
      cur.push(t);
      curW += g + t.w;
    }
    while (cur.length && cur[cur.length - 1].kind === 'sp') cur.pop();
    if (cur.length) lines.push(cur);

    const ops = [], marks = [], his = [];
    lines.forEach((ln, li) => {
      const last = li === lines.length - 1;
      const base = li * o.lh + Math.round(size * 0.98);
      // 两端对齐:有西文空格的行先撑空格;纯中文行均分到字间,撑开超过 0.3em 就放弃(齐左)
      let natural = 0, slots = 0, spaces = 0;
      ln.forEach((t, i) => { if (t.hang) return; natural += (i ? t.g : 0) + t.w; if (i) { slots++; if (t.kind === 'sp') spaces++; } });
      const extra = last ? 0 : Math.max(0, width - natural);
      let per = 0, perSp = 0;
      if (extra > 0) {
        if (spaces && extra / spaces <= em * 0.9) perSp = extra / spaces;
        else if (slots && extra / slots <= em * 0.3) per = extra / slots;
      }
      let x = 0, hiStart = null, hiEnd = 0;
      const flush = () => {
        if (hiStart === null) return;
        marks.push({ k: 'r', x: hiStart - 2, y: base - size * 0.9, w: hiEnd - hiStart + 4, h: size * 1.18, r: 2.5, color: HI_MARK });
        his.push({ x: (hiStart + hiEnd) / 2, y: base - size * 0.32 });
        hiStart = null;
      };
      ln.forEach((t, i) => {
        if (i) x += t.g + (t.hang ? 0 : per + (t.kind === 'sp' ? perSp : 0));
        if (t.kind !== 'sp') {
          const color = t.style === 'hi' ? HI_INK : t.style === 'en' ? (o.enColor || INK(0.6)) : (o.color || INK(0.66));
          ops.push(T(t.s, x, base, t.font, color));
          if (t.style === 'hi') { if (hiStart === null) hiStart = x; hiEnd = x + t.w; } else flush();
        }
        x += t.w;
      });
      flush();
    });
    return { ops: marks.concat(ops), h: lines.length * o.lh, his };
  }

  // ───────── 文字块 ─────────
  const BODY = { size: 12, lh: 19, en: 13, color: INK(0.56), enColor: INK(0.5) };

  function blockCard(card, cw, size) {
    const ops = [];
    const heavy = size >= 30;
    let y = Math.round(size * 0.9);
    ops.push(T(card.h, 0, y, `600 ${size}px ${FONT.serif}`, INK(heavy ? 0.76 : 0.7)));
    const enSize = Math.max(13, Math.round(size * 0.44));
    y += Math.round(size * 0.26) + enSize + 2;
    ops.push(T(card.en, 0, y, `italic 400 ${enSize}px ${FONT.latin}`, INK(0.5)));
    y += 11;
    ops.push({ k: 'l', x1: 0, y1: y + 0.5, x2: Math.min(34, cw * 0.2), y2: y + 0.5, color: INK(0.32), w: 1 });
    y += 10;
    const p = para(card.b, cw, BODY);
    ops.push(...shifted(p.ops, 0, y));
    const pois = p.his.map((h) => ({ x: h.x, y: h.y + y }));
    return { ops, h: y + p.h, pois };
  }

  function blockDisplay(word, cw) {
    const [kick, line] = DISPLAY[word];
    const size = Math.min(104, Math.floor(cw / (word.length + 0.06)));
    const ops = [];
    ops.push(T(kick, 0, 12, `600 12px ${FONT.sans}`, INK(0.42), { track: 0.22 }));
    const base = 18 + Math.round(size * 0.9);
    // 展示字只是水印:模糊后是一团很淡的云,透镜扫过才看出是个字
    ops.push(T(word, -size * 0.03, base, `600 ${size}px ${FONT.serif}`, INK(0.15)));
    const y = base + Math.round(size * 0.16) + 16;
    ops.push(T(line, 0, y, `italic 400 15px ${FONT.latin}`, INK(0.5)));
    return { ops, h: y + 5, pois: [] };
  }

  function blockQuote(q, cw) {
    const size = cw > 190 ? 26 : 22, lh = Math.round(size * 1.16);
    const ops = [];
    let y = size;
    for (const ln of q.q.split('\n')) { ops.push(T(ln, 0, y, `italic 400 ${size}px ${FONT.latin}`, INK(0.62))); y += lh; }
    y += 6;
    ops.push(T('—— ' + q.cn, 0, y, `400 12px ${FONT.sans}`, INK(0.56)));
    return { ops, h: y + 6, pois: [] };
  }

  function blockIndex(cw) {
    const ops = [];
    let y = 12;
    ops.push(T('INDEX · 目录', 0, y, `600 12px ${FONT.sans}`, INK(0.5), { track: 0.2 }));
    y += 9;
    ops.push({ k: 'l', x1: 0, y1: y + 0.5, x2: cw, y2: y + 0.5, color: INK(0.24), w: 0.75 });
    y += 4;
    const fc = `400 13px ${FONT.sans}`, fe = `italic 400 14px ${FONT.latin}`;
    for (const [cn, en] of INDEX) {
      y += 23;
      const wc = measure(cn, fc), we = measure(en, fe);
      ops.push(T(cn, 0, y, fc, INK(0.72)));
      ops.push(T(en, cw - we, y, fe, INK(0.58)));
      ops.push({ k: 'd', x1: wc + 8, x2: cw - we - 8, y: y - 4, color: INK(0.34) });
    }
    return { ops, h: y + 7, pois: [] };
  }

  function blockPara(runs, cw) {
    const ops = [T('§', 0, 13, `italic 400 15px ${FONT.latin}`, INK(0.4))];
    const p = para(runs, cw, BODY);
    ops.push(...shifted(p.ops, 0, 22));
    return { ops, h: 22 + p.h, pois: p.his.map((h) => ({ x: h.x, y: h.y + 22 })) };
  }

  function blockEgg(cw, img) {
    const ops = [];
    const iw = Math.min(cw * 0.8, 104);
    const ih = img && img.naturalWidth ? iw * img.naturalHeight / img.naturalWidth : iw * 1.25;
    const ix = Math.round((cw - iw) / 2);
    if (img && img.naturalWidth) ops.push({ k: 'i', img, x: ix, y: 0, w: iw, h: ih, alpha: 0.42 });
    let y = ih + 18;
    ops.push(T('哆啦美 · Dorami', 0, y + 2, `600 15px ${FONT.serif}`, INK(0.8)));
    y += 12;
    const p = para(['今天是 ' + dateCN + '，' + weekCN + '。', { hi: '你找到我了' }, '。', { en: 'You found me.' }], cw, BODY);
    ops.push(...shifted(p.ops, 0, y));
    return { ops, h: y + p.h, pois: [], egg: { x: ix + iw / 2, y: ih * 0.42 } };
  }

  function build(spec, cw, img) {
    switch (spec[0]) {
      case 'card': return blockCard(CARDS[spec[1]], cw, spec[2]);
      case 'disp': return blockDisplay(spec[1], cw);
      case 'quote': return blockQuote(QUOTES[spec[1]], cw);
      case 'index': return blockIndex(cw);
      case 'para': return blockPara(PARAS[spec[1]], cw);
      case 'egg': return blockEgg(cw, img);
      default: return { ops: [], h: 0, pois: [] };
    }
  }

  const hit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

  // 瀑布流铺满全屏;彩蛋块避开表单与题词,且必须完整落在可视区里
  function layoutField(W, H, mobile, avoid, img, avoidEgg) {
    const m = mobile ? 16 : Math.round(clamp(W * 0.03, 26, 52));
    const g = mobile ? 18 : Math.round(clamp(W * 0.019, 20, 32));
    const cols = mobile ? 2 : W >= 1720 ? 8 : W >= 1380 ? 6 : W >= 1120 ? 5 : W >= 860 ? 4 : 3;
    const cw = (W - 2 * m - g * (cols - 1)) / cols;
    const stagger = mobile ? [0, 26] : [0, 38, 14, 52, 24, 6, 44, 18];
    const colY = Array.from({ length: cols }, (_, i) => m + stagger[i % stagger.length]);
    const ops = [];
    const pois = [];
    let egg = null;
    for (let i = 1; i < cols; i++) {
      const x = Math.round(m + i * (cw + g) - g / 2) + 0.5;
      ops.push({ k: 'l', x1: x, y1: m, x2: x, y2: H + 40, color: INK(0.07), w: 0.75 });
    }
    let si = 0, swapI = 0;
    // 彩蛋块:每一轮都试着放进当前最矮的那一列,直到找到一个完整可见、又不挡表单 / 题词 / 透镜落点的位置
    const eggBlk = img && img.naturalWidth ? build(['egg'], cw, img) : null;
    const eggAvoid = avoid.concat(avoidEgg || []);
    const eggTop = mobile ? 70 : H * 0.34;
    const eggBottom = mobile ? (avoid[0] ? avoid[0].y + 40 : H) : H - 30;
    for (let guard = 0; guard < 800; guard++) {
      let ci = 0;
      for (let i = 1; i < cols; i++) if (colY[i] < colY[ci]) ci = i;
      if (colY[ci] > H + 10) break;
      if (eggBlk && !egg) {
        const r = { x: m + ci * (cw + g), y: colY[ci], w: cw, h: eggBlk.h };
        if (r.y >= eggTop && r.y + r.h <= eggBottom && !eggAvoid.some((a) => hit(r, a))) {
          ops.push(...shifted(eggBlk.ops, r.x, r.y));
          egg = { x: r.x + eggBlk.egg.x, y: r.y + eggBlk.egg.y };
          colY[ci] = r.y + eggBlk.h + (mobile ? 20 : 28);
          continue;
        }
      }
      const spec = SEQ[si % SEQ.length];
      si++;
      if (spec[0] === 'egg') continue;
      const x = m + ci * (cw + g), y = colY[ci];
      let blk = build(spec, cw, img);
      if (spec[0] === 'disp' && avoid.some((a) => hit({ x, y, w: cw, h: blk.h }, a))) {
        blk = build(['card', SWAP[swapI++ % SWAP.length], 22], cw, img);
      }
      ops.push(...shifted(blk.ops, x, y));
      for (const p of blk.pois) pois.push({ x: x + p.x, y: y + p.y });
      colY[ci] = y + blk.h + (mobile ? 20 : 28);
    }
    return { ops, pois, egg };
  }

  // 题词:画进画布(而不是 DOM),这样透镜能折射放大它
  function layoutHero(rect, mobile, W) {
    const F = Math.round(mobile ? clamp(W * 0.118, 38, 50) : clamp(W * 0.052, 44, 84));
    const ops = [];
    const kick = 'AI 资讯阅读器 · DORAMI';
    ops.push(T(kick, 2, 12, `600 12px ${FONT.sans}`, INK(0.66), { track: 0.26 }));
    const hf = `600 ${F}px ${FONT.serif}`;
    const x0 = -Math.round(F * 0.03);
    const y1 = 12 + Math.round(F * 0.42) + Math.round(F * 0.88);
    ops.push(T('信息如潮，', x0, y1, hf, INK(0.92)));
    const y2 = y1 + Math.round(F * 1.14);
    const w1 = measure('替你', hf), w2 = measure('看清', hf);
    ops.push(T('替你', x0, y2, hf, INK(0.92)));
    ops.push(T('看清', x0 + w1, y2, hf, HI_INK));
    ops.push(T('。', x0 + w1 + w2, y2, hf, INK(0.92)));
    const enS = Math.round(clamp(F * 0.22, 15, 21));
    const ef = `italic 400 ${enS}px ${FONT.latin}`;
    let y3 = y2 + Math.round(F * 0.42) + enS;
    const en = 'Everything worth reading, brought into focus.';
    if (measure(en, ef) <= rect.width) ops.push(T(en, 0, y3, ef, INK(0.7)));
    else {
      ops.push(T('Everything worth reading,', 0, y3, ef, INK(0.7)));
      y3 += Math.round(enS * 1.3);
      ops.push(T('brought into focus.', 0, y3, ef, INK(0.7)));
    }
    const h = y3 + Math.round(enS * 0.45);
    const w = Math.max(measure('信息如潮，', hf), w1 + w2 + measure('。', hf)) + 4;
    return { ops, h, w, F, read: { x: x0 + w1 + w2 * 0.5, y: y2 - F * 0.37 }, start: { x: x0 + w1 + w2 * 0.5 - F * 0.42, y: y2 - F * 0.3 } };
  }

  // ───────── 绘制 ops 到 2D 画布 ─────────
  function drawOps(ctx, ops, scale, dx, dy) {
    ctx.setTransform(scale, 0, 0, scale, -dx * scale, -dy * scale);
    ctx.textBaseline = 'alphabetic';
    for (const o of ops) {
      if (o.k === 't') {
        ctx.font = o.font;
        ctx.fillStyle = o.color;
        if (o.track) {
          const sz = parseFloat(o.font.match(/(\d+(?:\.\d+)?)px/)[1]);
          let x = o.x;
          for (const ch of o.s) { ctx.fillText(ch, x, o.y); x += ctx.measureText(ch).width + sz * o.track; }
        } else ctx.fillText(o.s, o.x, o.y);
      } else if (o.k === 'r') {
        ctx.fillStyle = o.color;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(o.x, o.y, o.w, o.h, o.r); else ctx.rect(o.x, o.y, o.w, o.h);
        ctx.fill();
      } else if (o.k === 'l') {
        ctx.strokeStyle = o.color;
        ctx.lineWidth = o.w;
        ctx.beginPath();
        ctx.moveTo(o.x1, o.y1);
        ctx.lineTo(o.x2, o.y2);
        ctx.stroke();
      } else if (o.k === 'd') {
        ctx.fillStyle = o.color;
        for (let x = o.x1; x <= o.x2; x += 4.5) { ctx.beginPath(); ctx.arc(x, o.y, 0.7, 0, 6.2832); ctx.fill(); }
      } else if (o.k === 'i') {
        ctx.globalAlpha = o.alpha;
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(o.img, o.x, o.y, o.w, o.h);
        ctx.globalAlpha = 1;
      }
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  // ───────── WebGL ─────────
  const VS = `
attribute vec2 aPos;
varying vec2 vUv;
void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`;

  const FS_BLUR = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uSrc;
uniform vec2 uStep;
void main() {
  vec2 o1 = uStep * 1.3846153846;
  vec2 o2 = uStep * 3.2307692308;
  vec4 c = texture2D(uSrc, vUv) * 0.2270270270;
  c += (texture2D(uSrc, vUv + o1) + texture2D(uSrc, vUv - o1)) * 0.3162162162;
  c += (texture2D(uSrc, vUv + o2) + texture2D(uSrc, vUv - o2)) * 0.0702702703;
  gl_FragColor = c;
}`;

  const FS_COPY = `
precision mediump float;
varying vec2 vUv;
uniform sampler2D uSrc;
void main() { gl_FragColor = texture2D(uSrc, vUv); }`;

  const FS_MAIN = `
precision highp float;
varying vec2 vUv;
uniform vec2 uRes;
uniform float uPx;
uniform sampler2D uField;
uniform sampler2D uBlurA;
uniform sampler2D uBlurB;
uniform sampler2D uOver;
uniform vec4 uOverRect;
uniform float uOverIn;
uniform float uFieldIn;
uniform float uFocus;
uniform vec2 uC;
uniform vec2 uHS;
uniform float uRR;
uniform mat2 uInv;
uniform float uOn;
uniform float uMag;
uniform float uBevel;
uniform float uBend;
uniform float uDisp;
uniform float uShadow;
uniform float uSlab;
uniform float uFog;
uniform float uCaus;
uniform float uTime;
uniform float uErr;
uniform float uWhite;
uniform float uEgg;
uniform vec4 uClrA;
uniform vec4 uClrB;
uniform vec2 uClrK;
uniform vec2 uLight;
uniform float uInk;

const vec3 AI_A = vec3(0.247, 0.482, 0.965);
const vec3 AI_B = vec3(0.545, 0.322, 0.957);
const vec3 READER = vec3(0.957, 0.965, 0.976);

float sdRR(vec2 p, vec2 b, float r) {
  r = min(r, min(b.x, b.y));
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
vec3 pearl(vec2 p) {
  vec2 uv = p / uRes;
  vec3 c = vec3(0.925, 0.932, 0.944);
  vec2 a = uv - vec2(0.06, 0.02);
  c += vec3(0.024, 0.014, 0.010) * exp(-dot(a, a) * 3.0);
  vec2 b = uv - vec2(0.98, 1.04);
  c += vec3(-0.006, 0.011, 0.017) * exp(-dot(b, b) * 2.4);
  vec2 d = uv - vec2(0.76, 0.42);
  c += vec3(0.011, 0.007, 0.026) * exp(-dot(d, d) * 4.0);
  c += 0.012 * (0.5 - uv.y);
  vec2 v = uv - 0.5;
  c *= 1.0 - 0.11 * dot(v, v);
  c += (hash(floor(p / uPx)) - 0.5) * 0.011;
  return c;
}
float clearing(vec2 p) {
  float a = sdRR(p - (uClrA.xy + uClrA.zw * 0.5), uClrA.zw * 0.5, 48.0);
  float b = sdRR(p - (uClrB.xy + uClrB.zw * 0.5), uClrB.zw * 0.5, 48.0);
  float ma = 1.0 - smoothstep(-60.0, 70.0, a);
  float mb = 1.0 - smoothstep(-44.0, 46.0, b);
  return 1.0 - max(ma * uClrK.x, mb * uClrK.y);
}
vec4 overlay(vec2 p) {
  vec2 uv = (p - uOverRect.xy) / uOverRect.zw;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return vec4(0.0);
  return texture2D(uOver, uv) * uOverIn;
}
vec4 blurField(vec2 p) {
  vec2 uv = p / uRes;
  vec4 a = texture2D(uBlurA, uv);
  vec4 b = texture2D(uBlurB, uv);
  vec2 d = (uv - vec2(0.40, 0.46)) * vec2(1.0, 1.3);
  float dof = smoothstep(0.24, 0.86, length(d)) * 0.7;
  return mix(b, mix(a, b, dof), uFieldIn);
}
vec3 comp(vec2 p, vec4 f) {
  float k = clearing(p) * uFieldIn;
  f = min(f * uInk, vec4(1.0));
  vec3 c = pearl(p);
  c = c * (1.0 - f.a * k) + f.rgb * k;
  vec4 o = overlay(p);
  return c * (1.0 - o.a) + o.rgb;
}
vec3 compOut(vec2 p, vec4 f) {
  float k = clearing(p) * uFieldIn;
  vec3 c = pearl(p);
  c = c * (1.0 - f.a * k) + f.rgb * k;
  vec4 o = overlay(p);
  return c * (1.0 - o.a) + o.rgb;
}
vec3 sceneSharp(vec2 p) {
  // a convex lens focuses; once it flattens into a slab behind the form it is just a plate of glass,
  // so the page behind it stays as soft as the rest (only the rounded bevel bends it)
  vec4 s = texture2D(uField, p / uRes);
  float fk = uFocus * (1.0 - uSlab);
  if (fk < 0.999) s = mix(blurField(p), s, fk);
  return comp(p, s);
}
// condensation droplets: one candidate per 11px cell; returns (coverage, glint, shade)
vec3 droplet(vec2 p) {
  vec2 g = p / 11.0;
  vec2 i = floor(g);
  vec2 f = fract(g);
  float on = step(0.42, hash(i + 0.37));
  vec2 c = vec2(hash(i + 3.1), hash(i + 7.7)) * 0.56 + 0.22;
  float r = mix(0.09, 0.27, hash(i + 1.7) * hash(i + 5.3));
  vec2 dv = f - c;
  float d = length(dv);
  float cov = on * (1.0 - smoothstep(r - 0.05, r + 0.02, d));
  float glint = on * exp(-dot(dv + vec2(r * 0.38), dv + vec2(r * 0.38)) / (r * r * 0.07));
  float shade = cov * smoothstep(0.0, r, dot(dv, vec2(0.6, 0.8)));
  return vec3(cov, glint, shade);
}
float caustic(vec2 p, float t) {
  float c = 0.0;
  for (int i = 0; i < 2; i++) {
    float fi = float(i);
    vec2 w = p * (0.0085 + fi * 0.006) + vec2(t * (0.16 + fi * 0.1), -t * 0.12);
    w += 0.42 * vec2(sin(w.y * 2.3 + t * 0.8), sin(w.x * 2.0 - t * 0.7));
    float v = abs(sin(w.x * 2.4) * sin(w.y * 2.1));
    c += pow(1.0 - v, 5.0) * (0.7 - fi * 0.3);
  }
  return c;
}
void main() {
  vec2 p = vec2(vUv.x, 1.0 - vUv.y) * uRes;
  vec3 col = compOut(p, blurField(p));
  if (uOn > 0.0) {
    vec2 rel = p - uC;
    vec2 q = uInv * rel;
    float d = sdRR(q, uHS, uRR);
    float mn = min(uHS.x, uHS.y);

    // shadow + caustic cast on the page
    vec2 so = vec2(mn * 0.02, 6.0 + mn * mix(0.11, 0.03, uSlab));
    float bw = mix(10.0 + mn * 0.32, 30.0, uSlab);
    float ds = sdRR(q - so, uHS * 0.985, uRR);
    float sh = (1.0 - smoothstep(-bw, bw, ds)) * uShadow;
    vec2 cq = (q - vec2(mn * 0.08, mn * 0.30)) / max(uHS * vec2(0.46, 0.28), vec2(1.0));
    float ca = exp(-dot(cq, cq) * 2.0) * (1.0 - uSlab) * 0.11;
    float outside = smoothstep(-2.0, 8.0, d);
    col = col * (1.0 - sh * outside) + vec3(1.0, 0.985, 0.95) * ca * uOn;

    if (d < 1.5) {
      vec2 e = vec2(0.5, 0.0);
      vec2 g = vec2(sdRR(q + e.xy, uHS, uRR) - sdRR(q - e.xy, uHS, uRR), sdRR(q + e.yx, uHS, uRR) - sdRR(q - e.yx, uHS, uRR));
      vec2 n = g / max(length(g), 1e-4);
      float depth = max(-d, 0.0);
      float u = clamp(depth / max(uBevel, 1.0), 0.0, 1.0);
      float rim = 1.0 - u;
      float hgt = sqrt(max(1.0 - rim * rim, 0.0));
      float slope = rim / max(hgt, 0.34);

      // refraction: magnify about the center + bend inward along the bevel;
      // dispersion only rides on the bevel bend, so color fringes live in the rim
      vec2 magOff = rel * (1.0 / uMag - 1.0);
      vec2 bendOff = -n * slope * uBend;
      vec3 inner;
      if (uDisp > 0.0) {
        float k = uDisp * rim * rim * rim;
        inner.r = sceneSharp(p + magOff + bendOff * (1.0 - k)).r;
        inner.g = sceneSharp(p + magOff + bendOff).g;
        inner.b = sceneSharp(p + magOff + bendOff * (1.0 + k)).b;
      } else {
        inner = sceneSharp(p + magOff + bendOff);
      }

      // condensation (password focus): frosted, with fine droplets
      if (uFog > 0.001) {
        vec2 qn = q / max(uHS, vec2(1.0));
        float nz = vnoise(p * 0.016) * 0.65 + vnoise(p * 0.045 + 7.0) * 0.35;
        float front = uFog * 1.55 - (0.5 - qn.y * 0.5) * 0.55 - nz * 0.45;
        float fm = smoothstep(0.0, 0.3, front);
        vec3 frost = compOut(p, texture2D(uBlurB, p / uRes));
        frost = mix(frost, vec3(0.952, 0.957, 0.968), 0.6);
        // droplets clear the frost where they sit, catch a glint, and shade their far side
        vec3 dr = droplet(q) * smoothstep(0.35, 0.9, fm);
        float clearDrop = dr.x * 0.55;
        inner = mix(inner, frost, fm * (0.97 - clearDrop));
        inner += dr.y * 0.10 - dr.z * 0.03;
      }

      // glass body: a touch brighter and cooler than paper
      inner = inner * vec3(0.990, 0.994, 1.0) + 0.016;
      // AI-identity hue (blue -> violet) breathing in the rim, visor hues for the easter egg
      float tA = clamp(0.5 + 0.5 * dot(n, vec2(0.7071, 0.7071)), 0.0, 1.0);
      vec3 ai = mix(AI_A, AI_B, tA);
      vec3 visor = mix(vec3(0.70, 0.46, 0.98), vec3(0.42, 0.86, 0.96), tA);
      ai = mix(ai, visor, uEgg);
      inner = mix(inner, inner * 0.80 + ai * 0.20, pow(rim, 3.5) * (0.5 + 0.35 * uEgg));

      // loading: caustic light circulating inside the glass
      if (uCaus > 0.001) {
        // light refracted by the glass pools into a slow caustic net; a broad band of light carries it across
        vec2 qn = q / max(uHS, vec2(1.0));
        float band = fract(uTime * 0.62);
        float x = (qn.x * 0.8 + qn.y * 0.35) * 0.5 + 0.5;
        float bd = (x - (band * 1.7 - 0.35)) / 0.22;
        float sweep = exp(-bd * bd);
        float cs = caustic(q, uTime);
        vec3 ct = mix(AI_A, AI_B, clamp(x, 0.0, 1.0));
        vec3 light = mix(vec3(1.0, 0.99, 0.97), ct, 0.35);
        inner += light * uCaus * (0.05 * cs + sweep * (0.05 + 0.13 * cs)) * (0.4 + 0.6 * u);
      }

      // lighting: specular, fresnel, softbox reflection, rim lines
      vec3 N = normalize(vec3(n * slope * 0.85, 1.0));
      vec3 L = normalize(vec3(-0.45 + uLight.x, -0.80 + uLight.y, 0.70));
      vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
      float spec = pow(max(dot(N, H), 0.0), mix(90.0, 160.0, uSlab));
      float fres = pow(1.0 - N.z, 3.2);
      inner += fres * 0.16 + spec * mix(0.55, 0.16, uSlab);
      vec2 sb = q / max(uHS, vec2(1.0)) - vec2(-0.30 + uLight.x * 0.30, -0.60 + uLight.y * 0.24);
      sb = vec2(sb.x * 0.86 + sb.y * 0.5, -sb.x * 0.5 + sb.y * 0.86);
      inner += exp(-(sb.x * sb.x * 14.0 + sb.y * sb.y * 110.0)) * mix(0.085, 0.035, uSlab);
      float edge = 1.0 - smoothstep(0.0, 1.6, depth);
      float facing = dot(n, vec2(-0.5521, -0.8338));
      // key light catches the top-left lip; light leaving the glass glints on the bottom-right lip
      inner += edge * (0.46 * max(facing, 0.0) + 0.06 + 0.16 * max(-facing, 0.0));
      float db = (depth - 3.0) / 1.6;
      inner += exp(-db * db) * (0.06 * max(facing, 0.0) + 0.035 * max(-facing, 0.0));
      float dk = (depth - 7.0) / 4.0;
      inner *= 1.0 - exp(-dk * dk) * 0.035 * max(-facing, 0.0);

      // error: a warm flush in the rim
      inner = mix(inner, inner * vec3(1.03, 0.86, 0.87) + vec3(0.07, 0.0, 0.01), uErr * pow(rim, 1.6));

      float aa = smoothstep(uPx * 1.25, -uPx * 1.25, d);
      col = mix(col, inner, aa * uOn);
    }
  }
  col = mix(col, READER, uWhite);
  gl_FragColor = vec4(col, 1.0);
}`;

  const GL = {
    gl: null, cv: null, P: {}, tex: {}, rt: {}, maxTex: 4096, buf: null,
    init(cv) {
      // 软件渲染(SwiftShader / 关了硬件加速)时宁可走 DOM 降级,也不跑全屏着色器
      const opts = { alpha: false, antialias: false, depth: false, stencil: false, premultipliedAlpha: false, preserveDrawingBuffer: false, failIfMajorPerformanceCaveat: true };
      let gl = null;
      try { gl = cv.getContext('webgl', opts) || cv.getContext('experimental-webgl', opts); } catch (e) { gl = null; }
      if (!gl) return false;
      const hp = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
      if (!hp || hp.precision < 16) return false;
      this.gl = gl;
      this.cv = cv;
      this.maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE) || 4096;
      try {
        this.P.main = this.program(FS_MAIN);
        this.P.blur = this.program(FS_BLUR);
        this.P.copy = this.program(FS_COPY);
      } catch (e) {
        console.warn('[d-lens] WebGL 着色器不可用,走降级路径:', e && e.message);
        return false;
      }
      this.buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      for (const k of ['field', 'small', 'over']) this.tex[k] = this.texture();
      cv.addEventListener('webglcontextlost', (e) => { e.preventDefault(); S.glLost = true; }, false);
      return true;
    },
    program(fs) {
      const gl = this.gl;
      const sh = (type, src) => {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'compile');
        return s;
      };
      const p = gl.createProgram();
      gl.attachShader(p, sh(gl.VERTEX_SHADER, VS));
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
      gl.bindAttribLocation(p, 0, 'aPos');
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) || 'link');
      const loc = {};
      const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < n; i++) {
        const info = gl.getActiveUniform(p, i);
        loc[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(p, info.name);
      }
      return { p, loc };
    },
    texture() {
      const gl = this.gl;
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
      return t;
    },
    target(w, h, old) {
      const gl = this.gl;
      if (old) { gl.deleteTexture(old.tex); gl.deleteFramebuffer(old.fb); }
      const tex = this.texture();
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      const fb = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      return { tex, fb, w, h };
    },
    upload(t, canvas) {
      const gl = this.gl;
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    },
    pass(prog, src, dst, setup) {
      const gl = this.gl;
      gl.bindFramebuffer(gl.FRAMEBUFFER, dst ? dst.fb : null);
      gl.viewport(0, 0, dst ? dst.w : this.cv.width, dst ? dst.h : this.cv.height);
      gl.useProgram(prog.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, src);
      gl.uniform1i(prog.loc.uSrc, 0);
      if (setup) setup(prog.loc);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    // 半分辨率 → 横竖两遍高斯 = blurA(约 3.5px);再降到四分之一 → 两遍 = blurB(约 8px)
    buildBlur(small, w2, h2) {
      const { P } = this;
      this.upload(this.tex.small, small);
      const w4 = Math.max(1, Math.round(w2 / 2)), h4 = Math.max(1, Math.round(h2 / 2));
      this.rt.a1 = this.target(w2, h2, this.rt.a1);
      this.rt.a2 = this.target(w2, h2, this.rt.a2);
      this.rt.b1 = this.target(w4, h4, this.rt.b1);
      this.rt.b2 = this.target(w4, h4, this.rt.b2);
      const blur = (src, dst, dx, dy, sw, sh) => this.pass(P.blur, src, dst, (l) => this.gl.uniform2f(l.uStep, dx / sw, dy / sh));
      blur(this.tex.small, this.rt.a1, 1, 0, w2, h2);
      blur(this.rt.a1.tex, this.rt.a2, 0, 1, w2, h2);
      this.pass(P.copy, this.rt.a2.tex, this.rt.b1);
      blur(this.rt.b1.tex, this.rt.b2, 1, 0, w4, h4);
      blur(this.rt.b2.tex, this.rt.b1, 0, 1, w4, h4);
    },
    draw(u) {
      const gl = this.gl;
      const { p, loc } = this.P.main;
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, this.cv.width, this.cv.height);
      gl.useProgram(p);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
      gl.enableVertexAttribArray(0);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      const bindT = (unit, t, name) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t); gl.uniform1i(loc[name], unit); };
      bindT(0, this.tex.field, 'uField');
      bindT(1, this.rt.a2.tex, 'uBlurA');
      bindT(2, this.rt.b1.tex, 'uBlurB');
      bindT(3, this.tex.over, 'uOver');
      const f1 = (n, v) => loc[n] && gl.uniform1f(loc[n], v);
      const f2 = (n, a, b) => loc[n] && gl.uniform2f(loc[n], a, b);
      const f4 = (n, v) => loc[n] && gl.uniform4f(loc[n], v[0], v[1], v[2], v[3]);
      f2('uRes', S.W, S.H);
      f1('uPx', 1 / S.rs);
      f4('uOverRect', S.overRect);
      f1('uOverIn', u.overIn);
      f1('uFieldIn', u.fieldIn);
      f1('uFocus', u.focus);
      f2('uC', u.cx, u.cy);
      f2('uHS', Math.max(u.hw, 0.001), Math.max(u.hh, 0.001));
      f1('uRR', u.rr);
      if (loc.uInv) gl.uniformMatrix2fv(loc.uInv, false, u.inv);
      f1('uOn', u.on);
      f1('uMag', u.mag);
      f1('uBevel', u.bevel);
      f1('uBend', u.bend);
      f1('uDisp', u.disp);
      f1('uShadow', u.shadow);
      f1('uSlab', u.slab);
      f1('uFog', u.fog);
      f1('uCaus', u.caus);
      f1('uTime', u.time);
      f1('uErr', u.err);
      f1('uWhite', u.white);
      f1('uEgg', u.egg);
      f4('uClrA', S.clrA);
      f4('uClrB', S.clrB);
      f2('uClrK', S.clrK[0], S.clrK[1]);
      f2('uLight', u.lx, u.ly);
      f1('uInk', 1.32);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
  };

  // ───────── 降级路径(无 WebGL):DOM 透镜 ─────────
  const FB = {
    on: false, el: null, blur: null, over: null, shadow: null, lens: null, sharp: null,
    init() {
      this.on = true;
      const el = $('#fb');
      el.hidden = false;
      this.el = el;
      const mk = (tag, cls) => { const n = document.createElement(tag); n.className = cls; return n; };
      this.blur = mk('canvas', 'fb-blur');
      this.over = mk('canvas', 'fb-over');
      this.shadow = mk('div', 'fb-shadow');
      this.lens = mk('div', 'fb-lens');
      this.sharp = mk('canvas', 'fb-sharp');
      this.lens.append(this.sharp, mk('div', 'fb-fog'), mk('div', 'fb-caus'), mk('div', 'fb-glass'));
      el.append(this.blur, this.over, this.shadow, this.lens);
      $('#gl').style.display = 'none';
    },
    clearMask(ctx, scale) {
      // 与着色器同一套「留白」:题词与表单身后的字变淡,用一叠渐大的圆角矩形近似 smoothstep
      ctx.save();
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.globalCompositeOperation = 'destination-out';
      const rects = [[S.clrA, S.clrK[0], 70], [S.clrB, S.clrK[1], 90]];
      for (const [r, k, spread] of rects) {
        const steps = 10;
        for (let i = 0; i < steps; i++) {
          const g = spread - (i / (steps - 1)) * (spread + 30);
          ctx.fillStyle = `rgba(0,0,0,${(k / steps).toFixed(3)})`;
          ctx.beginPath();
          const x = r[0] - g, y = r[1] - g, w = r[2] + 2 * g, h = r[3] + 2 * g;
          if (w <= 0 || h <= 0) continue;
          if (ctx.roundRect) ctx.roundRect(x, y, w, h, Math.max(4, 48 + g)); else ctx.rect(x, y, w, h);
          ctx.fill();
        }
      }
      ctx.restore();
    },
    build(field, hero) {
      const { W, H } = S;
      const sc = Math.min(S.dpr, 1.5);
      const sz = (c, w, h, s) => { c.width = Math.round(w * s); c.height = Math.round(h * s); c.style.width = w + 'px'; c.style.height = h + 'px'; };
      sz(this.blur, W, H, sc);
      const b = this.blur.getContext('2d');
      drawOps(b, field.ops, sc, 0, 0);
      this.clearMask(b, sc);
      const [ox, oy, ow, oh] = S.overRect;
      sz(this.over, ow, oh, S.dpr);
      this.over.style.transform = `translate(${ox}px, ${oy}px)`;
      drawOps(this.over.getContext('2d'), hero.ops, S.dpr, -hero.dx, -hero.dy);
      const ss = Math.min(S.dpr * 1.4, 3);
      sz(this.sharp, W, H, ss);
      const c = this.sharp.getContext('2d');
      c.fillStyle = PEARL;
      c.fillRect(0, 0, this.sharp.width, this.sharp.height);
      const tmp = document.createElement('canvas');
      tmp.width = this.sharp.width;
      tmp.height = this.sharp.height;
      const t = tmp.getContext('2d');
      drawOps(t, field.ops, ss, 0, 0);
      this.clearMask(t, ss);
      c.drawImage(tmp, 0, 0);
      drawOps(c, hero.ops, ss, -(ox + hero.dx), -(oy + hero.dy));
    },
    draw(u) {
      const hw = Math.max(0, u.hw), hh = Math.max(0, u.hh);
      const st = this.lens.style;
      st.width = 2 * hw + 'px';
      st.height = 2 * hh + 'px';
      st.borderRadius = Math.min(u.rr, hw, hh) + 'px';
      st.transform = `translate(${u.cx - hw}px, ${u.cy - hh}px)`;
      st.opacity = u.on;
      this.sharp.style.transform = `translate(${hw - u.cx * u.mag}px, ${hh - u.cy * u.mag}px) scale(${u.mag})`;
      const sh = this.shadow.style;
      const mn = Math.min(hw, hh);
      sh.width = 2 * hw * 1.08 + 'px';
      sh.height = 2 * hh * 1.08 + 'px';
      sh.borderRadius = Math.min(u.rr, hw, hh) * 1.08 + 'px';
      sh.transform = `translate(${u.cx - hw * 1.08}px, ${u.cy - hh * 1.08 + 6 + mn * 0.1}px)`;
      sh.opacity = u.on * (1 - u.slab * 0.4) * (1 - u.white);
      this.lens.classList.toggle('fog', u.fog > 0.5);
      this.lens.classList.toggle('caus', u.caus > 0.05);
      this.over.style.opacity = u.overIn;
      this.blur.style.opacity = u.fieldIn;
      this.el.style.opacity = 1 - u.white;
    },
  };

  // ───────── 状态与物理 ─────────
  class Spring {
    constructor(x, k, z) { this.x = x; this.v = 0; this.to = x; this.k = k; this.z = z; }
    set(k, z) { this.k = k; this.z = z; }
    step(dt) { const c = 2 * Math.sqrt(this.k) * this.z; this.v += (this.k * (this.to - this.x) - c * this.v) * dt; this.x += this.v * dt; }
    snap(v) { if (v !== undefined) this.to = v; this.x = this.to; this.v = 0; }
    rest(eps) { return Math.abs(this.to - this.x) < eps && Math.abs(this.v) < eps * 10; }
  }

  const S = {
    ready: false, gl: false, glLost: false,
    reduced: root.dataset.motion === 'reduce',
    W: 0, H: 0, dpr: 1, rs: 1, ts: 1, mobile: false,
    t0: 0, intro: false, born: false, glided: false, uiShown: false,
    phase: 'idle', focusDock: false, forceDock: false,
    fogT: 0, fog: 0, caus: 0, errGlow: 0, errAt: -1e9, sucAt: -1e9, sucFrom: null, landed: false,
    pointer: { x: 0, y: 0, active: false, last: -1e9 },
    lx: 0, ly: 0, tilt: { x: 0, y: 0 }, egg: 0,
    wander: { target: null, i: 0, timer: 0 },
    home: { x: 0, y: 0 }, start: { x: 0, y: 0 }, R: 150, mag: 1.38,
    pois: [], eggPos: null,
    overRect: [0, 0, 1, 1], clrA: [0, 0, 0, 0], clrB: [0, 0, 0, 0], clrK: [0.5, 0.92],
    pendingSkip: false, pendingState: null, raf: 0, last: 0, lastDraw: 0,
  };
  const SP = {
    x: new Spring(0, 170, 0.7), y: new Spring(0, 170, 0.7),
    hw: new Spring(0, 150, 0.58), hh: new Spring(0, 150, 0.58), rr: new Spring(0, 150, 0.58),
    mode: new Spring(0, 110, 0.82),
    jx: new Spring(0, 260, 0.3), jy: new Spring(0, 260, 0.3),
  };
  const ALL = Object.values(SP);

  const els = {
    gl: $('#gl'), form: $('#form'), auth: $('.auth'), user: $('#u'), pass: $('#p'), err: $('#err'),
    btn: $('#submit'), btnLabel: $('#submit-label'), hero: $('#hero'), landing: $('#landing'),
    greet: $('#greet'), date: $('#meta-date'), time: $('#meta-time'), tz: $('#meta-tz'),
  };
  const img = new Image();
  img.crossOrigin = 'anonymous'; // 在线预览时图片被重定向到另一域名,读像素 / 上传纹理需 CORS

  // 手机没有悬停:透镜默认吸附在表单身后(点按文字场可以把它暂时叫走)
  // 桌面:指针靠近表单时透镜先被「磁吸」过去(进入 12px / 离开 40px 的迟滞),点下去之前玻璃已经就位
  const isDocked = () => S.phase !== 'success' && (S.focusDock || S.forceDock || S.phase === 'loading' ||
    (S.hoverDock && !S.intro && !S.reduced) || (S.mobile && !S.detached && (!S.intro || S.introDock)));
  const followSprings = () => { SP.x.set(170, 0.7); SP.y.set(170, 0.7); };

  function dockRect() {
    const r = els.auth.getBoundingClientRect();
    const fr = els.form.getBoundingClientRect();
    const padX = S.mobile ? 12 : 30, padY = S.mobile ? 14 : 32;
    return { cx: r.left + r.width / 2, cy: fr.top + fr.height / 2, hw: r.width / 2 + padX, hh: fr.height / 2 + padY, rr: S.mobile ? 26 : 34 };
  }
  function setShape(hw, hh, rr) { SP.hw.to = hw; SP.hh.to = hh; SP.rr.to = rr; }

  // ───────── 布局 + 纹理重建 ─────────
  function rebuild() {
    const W = Math.max(320, innerWidth), H = Math.max(320, innerHeight);
    S.W = W;
    S.H = H;
    S.dpr = Math.min(window.devicePixelRatio || 1, 2);
    S.rs = S.dpr;
    S.mobile = W <= 720;
    S.R = S.mobile ? clamp(W * 0.25, 76, 112) : clamp(W * 0.105, 118, 168);
    S.mag = S.mobile ? 1.32 : 1.38;
    const maxTex = S.gl ? GL.maxTex : 8192;
    S.ts = Math.min(S.dpr * S.mag, Math.sqrt(9e6 / (W * H)), maxTex / Math.max(W, H));

    // 题词
    const hr = els.hero.getBoundingClientRect();
    const hero = layoutHero(hr, S.mobile, W);
    const pad = 24;
    const top = S.mobile ? hr.top + Math.max(0, (hr.height - hero.h) * 0.45) : hr.top + (hr.height - hero.h) / 2;
    S.overRect = [Math.round(hr.left - pad), Math.round(top - pad), Math.ceil(Math.max(hero.w, hr.width) + pad * 2), Math.ceil(hero.h + pad * 2)];
    hero.dx = pad;
    hero.dy = pad;
    S.read = { x: S.overRect[0] + pad + hero.read.x, y: S.overRect[1] + pad + hero.read.y };
    S.start = { x: S.overRect[0] + pad + hero.start.x, y: S.overRect[1] + pad + hero.start.y };
    S.clrA = [hr.left - 8, top - 4, hero.w + 16, hero.h + 8];
    const ar = els.auth.getBoundingClientRect();
    S.clrB = [ar.left, ar.top, ar.width, ar.height];

    // 文字场
    const avoid = [
      { x: ar.left - 70, y: ar.top - 60, w: ar.width + 140, h: ar.height + 120 },
      { x: S.clrA[0] - 20, y: S.clrA[1] - 20, w: S.clrA[2] + 40, h: S.clrA[3] + 40 },
    ];
    const heroR0 = S.clrA[0] + S.clrA[2];
    const gapX = (heroR0 + ar.left) / 2;
    const br = document.querySelector('.brand').getBoundingClientRect();
    const avoidEgg = [{ x: br.left - 20, y: br.top - 20, w: br.width + 40, h: br.height + 40 }];
    if (!S.mobile) avoidEgg.push({ x: gapX - S.R * 1.5, y: H * 0.53 - S.R * 1.5, w: S.R * 3, h: S.R * 3 });
    const field = layoutField(W, H, S.mobile, avoid, img, avoidEgg);
    S.eggPos = field.egg;

    // 透镜的「家」:桌面在题词与表单之间的空地,吸附到最近的一个荧光词上(静止时它正替你读着一句重点);
    // 手机默认吸附在表单身后,家只用于开场与点按后的回位
    const heroRight = S.clrA[0] + S.clrA[2];
    if (!S.mobile) {
      let hx = (heroRight + ar.left) / 2;
      if (ar.left - heroRight < 2 * S.R + 30) hx = heroRight + S.R * 0.8;
      let home = { x: hx, y: H * 0.53 };
      let best = null, bd = S.R * 1.1;
      for (const q of field.pois) {
        const d = Math.hypot(q.x - home.x, (q.y - home.y) * 1.3);
        if (d < bd && q.x - S.R > heroRight - S.R * 0.4 && q.x + S.R < ar.left + 10) { bd = d; best = q; }
      }
      if (best) home = { x: best.x, y: best.y };
      S.home = home;
    } else {
      S.home = S.read;
    }

    // 自走 / 巡读的停靠点:题词「看清」+ 文字场里的荧光词 + 彩蛋;避开表单
    const R = S.R;
    const ok = (p) => p.x > R * 0.7 && p.x < W - R * 0.7 && p.y > R * 0.75 && p.y < H - R * 0.75 &&
      !hit({ x: p.x - 10, y: p.y - 10, w: 20, h: 20 }, avoid[0]) && (!S.mobile || p.y < ar.top - R * 0.4);
    const cand = field.pois.filter(ok).filter((p) => Math.hypot(p.x - S.home.x, p.y - S.home.y) > 80);
    const tour = [S.home];
    let cur = S.home;
    const pool = cand.slice();
    if (S.eggPos && ok(S.eggPos)) pool.push(Object.assign({ egg: true }, S.eggPos));
    while (pool.length && tour.length < 7) {
      let bi = 0, bd = Infinity;
      pool.forEach((p, i) => { const d = Math.hypot(p.x - cur.x, p.y - cur.y); const score = Math.abs(d - (S.mobile ? 150 : 360)); if (score < bd) { bd = score; bi = i; } });
      cur = pool.splice(bi, 1)[0];
      tour.push(cur);
    }
    if (S.eggPos && ok(S.eggPos) && !tour.some((p) => p.egg)) tour.splice(Math.min(3, tour.length), 0, Object.assign({ egg: true }, S.eggPos));
    S.pois = tour;

    // 画布
    const fieldCv = document.createElement('canvas');
    const smallCv = document.createElement('canvas');
    const overCv = document.createElement('canvas');
    fieldCv.width = Math.round(W * S.ts);
    fieldCv.height = Math.round(H * S.ts);
    drawOps(fieldCv.getContext('2d'), field.ops, S.ts, 0, 0);
    const w2 = Math.max(1, Math.round(W / 2)), h2 = Math.max(1, Math.round(H / 2));
    smallCv.width = w2;
    smallCv.height = h2;
    drawOps(smallCv.getContext('2d'), field.ops, 0.5, 0, 0);
    const os = Math.min(S.ts, 3);
    overCv.width = Math.round(S.overRect[2] * os);
    overCv.height = Math.round(S.overRect[3] * os);
    drawOps(overCv.getContext('2d'), hero.ops, os, -pad, -pad);

    if (S.gl) {
      const cv = els.gl;
      cv.width = Math.round(W * S.rs);
      cv.height = Math.round(H * S.rs);
      GL.upload(GL.tex.field, fieldCv);
      GL.upload(GL.tex.over, overCv);
      GL.buildBlur(smallCv, w2, h2);
    } else if (FB.on) {
      FB.build(field, hero);
    }
  }

  // ───────── 帧循环:只在有东西在动时跑,静止即停 ─────────
  function kick() {
    if (!S.ready || S.raf || document.hidden) return;
    S.last = now();
    S.raf = requestAnimationFrame(frame);
  }

  function frame(t) {
    S.raf = 0;
    const dt = Math.min(0.05, Math.max(0, (t - S.last) / 1000));
    S.last = t;
    const busy = update(dt, t);
    const capped = S.mobile && !S.intro && S.phase === 'idle' && !isDocked();
    if (!capped || t - S.lastDraw > 30) { render(t); S.lastDraw = t; }
    if (busy && !document.hidden) S.raf = requestAnimationFrame(frame);
  }

  function update(dt, t) {
    let busy = false;
    const T0 = t - S.t0;
    S.u = S.u || {};
    const U = S.u;

    // 开场时间轴
    U.fieldIn = 1;
    U.focus = 1;
    U.overIn = 1;
    if (S.intro) {
      busy = true;
      U.fieldIn = out3(prog(T0, 0, 1100));
      U.focus = out3(prog(T0, 640, 1500));
      U.overIn = out3(prog(T0, 900, 1950));
      // 透镜在「看清」上凝成一滴,边读边轻轻漂移 → 桌面滑到空地落定 / 手机沉到表单身后
      if (!S.born && T0 >= 380) { S.born = true; setShape(S.R, S.R, S.R); }
      if (!S.read1 && T0 >= 700) { S.read1 = true; SP.x.set(16, 0.95); SP.y.set(16, 0.95); SP.x.to = S.read.x; SP.y.to = S.read.y; }
      if (!S.glided && T0 >= 2050) {
        S.glided = true;
        if (S.mobile) S.introDock = true;
        else { SP.x.set(34, 0.9); SP.y.set(34, 0.9); SP.x.to = S.home.x; SP.y.to = S.home.y; }
      }
      if (!S.uiShown && T0 >= 1650) showUI(false);
      if (T0 >= 3400) { S.intro = false; followSprings(); scheduleWander(7000); }
    }

    // 目标
    const docked = isDocked();
    if (S.phase === 'success') {
      busy = successTargets(t) || busy;
    } else if (!S.intro || S.introDock) {
      if (docked) {
        const r = dockRect();
        SP.x.to = r.cx;
        SP.y.to = r.cy;
        setShape(r.hw, r.hh, r.rr);
        SP.mode.to = 1;
        if (!S.docking) { S.docking = true; SP.x.set(95, 0.8); SP.y.set(95, 0.8); for (const k of ['hw', 'hh', 'rr']) SP[k].set(105, 0.6); }
      } else {
        const tgt = S.pointer.active && !S.mobile && !S.reduced ? S.pointer : S.wander.target || S.home;
        SP.x.to = tgt.x + S.tilt.x * 14;
        SP.y.to = tgt.y + S.tilt.y * 10;
        setShape(S.R, S.R, S.R);
        SP.mode.to = 0;
        if (S.docking) { S.docking = false; for (const k of ['hw', 'hh', 'rr']) SP[k].set(150, 0.58); if (!S.wander.target) followSprings(); }
      }
    }

    // 积分(固定子步长,弹簧稳定)
    if (S.reduced && S.phase !== 'success') {
      for (const s of ALL) s.snap();
      SP.jx.snap(0);
      SP.jy.snap(0);
    } else if (S.phase !== 'success') {
      let rem = dt;
      while (rem > 1e-6) {
        const h = Math.min(rem, 1 / 240);
        const jk = 0.00013 * (1 - 0.8 * SP.mode.x);
        SP.jx.to = clamp(SP.x.v * jk, -0.2, 0.2);
        SP.jy.to = clamp(SP.y.v * jk, -0.2, 0.2);
        for (const s of ALL) s.step(h);
        rem -= h;
      }
      if (!SP.x.rest(0.03) || !SP.y.rest(0.03) || !SP.hw.rest(0.03) || !SP.hh.rest(0.03) || !SP.rr.rest(0.03) ||
        !SP.mode.rest(0.0008) || !SP.jx.rest(0.0004) || !SP.jy.rest(0.0004)) busy = true;
    }

    // 起雾 / 焦散 / 错误余辉
    const fogRate = S.reduced ? 7 : S.fogT > S.fog ? 2.2 : 1.35;
    if (S.fog !== S.fogT) {
      S.fog = S.fogT > S.fog ? Math.min(S.fogT, S.fog + dt * fogRate) : Math.max(S.fogT, S.fog - dt * fogRate);
      busy = true;
    }
    const causT = S.phase === 'loading' || (S.phase === 'success' && t - S.sucAt < 500) ? 1 : 0;
    if (S.caus !== causT) {
      S.caus = causT > S.caus ? Math.min(1, S.caus + dt * 3.2) : Math.max(0, S.caus - dt * 2.4);
      busy = true;
    }
    if (S.caus > 0 && !S.reduced) busy = true;
    const errT = S.phase === 'error' && docked ? (S.reduced ? 0.45 : 0.16 + 0.84 * Math.exp(-(t - S.errAt) / 420)) : 0;
    if (Math.abs(S.errGlow - errT) > 0.002) { S.errGlow += (errT - S.errGlow) * Math.min(1, dt * 9); busy = true; } else S.errGlow = errT;

    // 错误:果冻抖动(透镜与表单同步)
    let shake = 0;
    const et = (t - S.errAt) / 1000;
    if (!S.reduced && S.phase === 'error' && et < 1.1) {
      shake = 11 * Math.exp(-et * 5.2) * Math.sin(et * Math.PI * 2 * 5.4);
      busy = true;
    }
    els.form.style.transform = shake ? `translateX(${shake.toFixed(2)}px)` : '';

    // 高光随指针 / 陀螺仪轻微偏转(玻璃被「拿在手里」的感觉)
    let tlx = S.tilt.x * 0.22, tly = S.tilt.y * 0.18;
    if (S.pointer.active && !S.mobile) {
      tlx += clamp((S.pointer.x - SP.x.x) / (S.W * 0.5), -1, 1) * 0.2;
      tly += clamp((S.pointer.y - SP.y.x) / (S.H * 0.5), -1, 1) * 0.16;
    }
    const lk = S.reduced ? 1 : Math.min(1, dt * 6);
    if (Math.abs(S.lx - tlx) + Math.abs(S.ly - tly) > 0.002) { S.lx += (tlx - S.lx) * lk; S.ly += (tly - S.ly) * lk; busy = true; }

    // 彩蛋:透镜停在小像上时,玻璃边缘换成哆啦美目镜的全息色
    let eggT = 0;
    if (S.eggPos && S.phase === 'idle' && !docked) {
      const d = Math.hypot(SP.x.x - S.eggPos.x, SP.y.x - S.eggPos.y);
      eggT = 1 - prog(d, S.R * 0.25, S.R * 0.75);
    }
    if (Math.abs(S.egg - eggT) > 0.003) { S.egg += (eggT - S.egg) * Math.min(1, dt * 4); busy = true; }

    // 组装 uniform
    if (S.phase !== 'success') {
      const slab = clamp(SP.mode.x, 0, 1);
      const mn = Math.max(1, Math.min(SP.hw.x, SP.hh.x));
      U.cx = SP.x.x + shake;
      U.cy = SP.y.x;
      U.hw = Math.max(0, SP.hw.x);
      U.hh = Math.max(0, SP.hh.x);
      U.rr = Math.max(0, SP.rr.x);
      U.mag = lerp(S.mag, 1.035, slab);
      U.bevel = lerp(mn * 0.8, S.mobile ? 22 : 30, slab);
      U.bend = lerp(mn * 0.04, S.mobile ? 3.2 : 4.4, slab);
      U.disp = lerp(0.055, 0.09, slab);
      U.shadow = lerp(0.16, 0.11, slab);
      U.slab = slab;
      U.white = 0;
    }
    const jx = SP.jx.x, jy = SP.jy.x;
    let s = Math.min(0.24, Math.hypot(jx, jy));
    const th = Math.atan2(jy, jx);
    if (S.phase === 'success' || S.reduced) s = 0;
    const a = 1 / (1 + s), b = 1 + s, c = Math.cos(th), sn = Math.sin(th);
    U.inv = [a * c * c + b * sn * sn, (a - b) * c * sn, (a - b) * c * sn, a * sn * sn + b * c * c];
    U.on = clamp(Math.min(U.hw, U.hh) / 8, 0, 1);
    U.fog = io3(clamp(S.fog, 0, 1));
    U.caus = S.caus;
    U.time = (t / 1000) % 1000;
    U.err = S.errGlow;
    U.egg = S.egg;
    U.lx = S.lx;
    U.ly = S.ly;
    return busy;
  }

  // 成功:板 → 圆 → 圆极速放大吞没全屏(透镜里的字被越放越大),最后白化成阅读器底色
  function successTargets(t) {
    const tau = (t - S.sucAt) / 1000;
    const U = S.u;
    const f = S.sucFrom;
    if (S.reduced) {
      Object.assign(U, { cx: f.cx, cy: f.cy, hw: f.hw, hh: f.hh, rr: f.rr, mag: f.mag, bevel: f.bevel, bend: f.bend, disp: f.disp, shadow: f.shadow, slab: f.slab });
      U.white = out3(prog(tau, 0, 0.45));
      if (tau > 0.3 && !S.landed) land();
      return tau < 0.6;
    }
    const a = out3(prog(tau, 0, 0.34));
    const b = in3(prog(tau, 0.2, 1.08));
    const far = Math.max(Math.hypot(f.cx, f.cy), Math.hypot(S.W - f.cx, f.cy), Math.hypot(f.cx, S.H - f.cy), Math.hypot(S.W - f.cx, S.H - f.cy)) * 1.08;
    const rc = lerp(Math.min(f.hw, f.hh) * 0.86, far, b);
    U.cx = f.cx;
    U.cy = f.cy;
    U.hw = lerp(f.hw, rc, a);
    U.hh = lerp(f.hh, rc, a);
    U.rr = lerp(f.rr, rc, a);
    U.mag = lerp(f.mag, 2.5, b);
    U.bevel = lerp(f.bevel, rc * 0.42, a);
    U.bend = lerp(f.bend, 2 + rc * 0.02, a);
    U.disp = lerp(f.disp, 0.3, a);
    U.shadow = lerp(f.shadow, 0, b);
    U.slab = lerp(f.slab, 0, a);
    U.white = io3(prog(tau, 0.74, 1.32));
    if (tau > 1.25 && !S.landed) land();
    return tau < 1.45;
  }

  function land() {
    S.landed = true;
    els.landing.classList.add('show');
  }

  function render(t) {
    if (!S.u) return;
    if (S.gl && !S.glLost) {
      GL.draw(S.u);
      if (!els.gl.classList.contains('ready')) els.gl.classList.add('ready');
    } else if (FB.on) FB.draw(S.u);
  }

  // ───────── 开场 / 跳过 / 重播 ─────────
  function showUI(instant) {
    S.uiShown = true;
    root.classList.remove('ui-pre');
    root.classList.add('ui-in');
    if (instant) {
      root.classList.add('ui-instant');
      requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('ui-instant')));
    }
  }

  function startIntro() {
    if (S.reduced) { skipIntro(true); return; }
    S.t0 = now();
    S.intro = true;
    S.born = false;
    S.read1 = false;
    S.glided = false;
    S.introDock = false;
    S.detached = false;
    S.uiShown = false;
    root.classList.remove('ui-in', 'ui-instant');
    root.classList.add('ui-pre');
    SP.x.set(170, 0.7);
    SP.y.set(170, 0.7);
    SP.x.snap(S.start.x);
    SP.y.snap(S.start.y);
    for (const k of ['hw', 'hh', 'rr']) SP[k].snap(0);
    SP.mode.snap(0);
    SP.jx.snap(0);
    SP.jy.snap(0);
    S.wander.target = null;
    kick();
  }

  function skipIntro(instant) {
    const wasIntro = S.intro;
    S.intro = false;
    S.born = S.read1 = S.glided = true;
    S.introDock = S.mobile;
    followSprings();
    if (instant || S.reduced) {
      if (isDocked()) {
        const r = dockRect();
        SP.x.snap(r.cx);
        SP.y.snap(r.cy);
        SP.hw.snap(r.hw);
        SP.hh.snap(r.hh);
        SP.rr.snap(r.rr);
        SP.mode.snap(1);
      } else {
        SP.x.snap(S.home.x);
        SP.y.snap(S.home.y);
        for (const k of ['hw', 'hh', 'rr']) SP[k].snap(S.R);
      }
      if (!isDocked()) SP.mode.snap(0);
      SP.jx.snap(0);
      SP.jy.snap(0);
    } else if (!isDocked()) {
      setShape(S.R, S.R, S.R);
    }
    if (!S.uiShown || instant) showUI(!!instant || S.reduced);
    if (wasIntro || instant) scheduleWander(7000);
    kick();
  }

  // ───────── 自走巡读:无人操作时,透镜自己去「读」那些荧光词,读完停住(停住即零开销) ─────────
  function scheduleWander(delay) {
    clearTimeout(S.wander.timer);
    if (S.reduced) return;
    S.wander.timer = setTimeout(stepWander, delay);
  }
  function stepWander() {
    if (S.reduced || !S.ready || S.mobile) return;
    if (S.intro || S.phase !== 'idle' || isDocked()) { scheduleWander(3000); return; }
    const idle = now() - S.pointer.last;
    if (!S.mobile && idle < 7000) { scheduleWander(7000 - idle + 60); return; }
    if (!S.pois.length) return;
    S.wander.i = (S.wander.i + 1) % S.pois.length;
    S.wander.target = S.pois[S.wander.i];
    S.pointer.active = false;
    SP.x.set(24, 0.94);
    SP.y.set(24, 0.94);
    kick();
    scheduleWander(S.wander.target.egg ? 6200 : S.mobile ? 4200 : 5200);
  }
  function goEgg() {
    if (!S.eggPos) return;
    if (S.intro) skipIntro(false);
    if (document.activeElement && els.form.contains(document.activeElement)) document.activeElement.blur();
    S.forceDock = false;
    S.focusDock = false;
    if (S.phase === 'error') setPhase('idle');
    S.pointer.active = false;
    S.pointer.last = now();
    S.wander.target = Object.assign({ egg: true }, S.eggPos);
    if (S.reduced) { SP.x.snap(S.eggPos.x); SP.y.snap(S.eggPos.y); } else { SP.x.set(32, 0.9); SP.y.set(32, 0.9); }
    scheduleWander(9000);
    kick();
  }

  // ───────── 表单 ─────────
  function setPhase(name) {
    const prev = S.phase;
    S.phase = name;
    root.classList.toggle('loading', name === 'loading');
    const busy = name === 'loading' || name === 'success';
    els.btn.disabled = busy;
    els.user.readOnly = busy;
    els.pass.readOnly = busy;
    els.btnLabel.textContent = name === 'loading' ? '登录中' : '登录';
    els.btn.setAttribute('aria-busy', String(name === 'loading'));
    if (name !== 'error') {
      els.err.textContent = '';
      els.user.removeAttribute('aria-invalid');
      els.pass.removeAttribute('aria-invalid');
    }
    if (name === 'error') {
      S.errAt = now();
      if (!S.reduced) { SP.jx.v += 2.4; SP.jy.v -= 0.6; }
    }
    if (name === 'success' && prev !== 'success') {
      S.sucAt = now();
      const u = S.u || {};
      S.sucFrom = { cx: u.cx, cy: u.cy, hw: u.hw, hh: u.hh, rr: u.rr, mag: u.mag, bevel: u.bevel, bend: u.bend, disp: u.disp, shadow: u.shadow, slab: u.slab };
      S.landed = false;
      root.classList.add('leaving');
      clearTimeout(S.wander.timer);
    }
    if (name !== 'success') {
      root.classList.remove('leaving');
      els.landing.classList.remove('show');
      S.landed = false;
    }
    kick();
  }

  function showError(msg, field) {
    S.forceDock = true;
    setPhase('error');
    els.err.textContent = msg;
    if (field === 'both') { els.user.setAttribute('aria-invalid', 'true'); els.pass.setAttribute('aria-invalid', 'true'); }
    else if (field) field.setAttribute('aria-invalid', 'true');
  }

  function setFog(on) {
    S.fogT = on ? 1 : 0;
    root.classList.toggle('shy', on);
    kick();
  }

  let pendingLogin = 0;
  els.form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (S.phase === 'loading' || S.phase === 'success') return;
    if (S.intro) skipIntro(false);
    const u = els.user.value.trim(), p = els.pass.value;
    if (!u || !p) { showError('请输入账号和密码', !u && !p ? 'both' : !u ? els.user : els.pass); return; }
    S.forceDock = true;
    setPhase('loading');
    clearTimeout(pendingLogin);
    pendingLogin = setTimeout(() => {
      if (S.phase !== 'loading') return;
      if (u === 'admin' && p === 'admin') setPhase('success');
      else showError('账号或密码错误', 'both');
    }, 900);
  });

  els.form.addEventListener('focusin', (e) => {
    S.focusDock = true;
    if (e.target === els.pass) setFog(true);
    if (S.intro) skipIntro(false);
    kick();
  });
  els.form.addEventListener('focusout', (e) => {
    if (e.target === els.pass) setFog(false);
    setTimeout(() => {
      if (!els.form.contains(document.activeElement)) { S.focusDock = false; kick(); }
    }, 90);
  });
  for (const inp of [els.user, els.pass]) {
    inp.addEventListener('input', () => {
      if (S.phase === 'error') setPhase('idle');
      inp.removeAttribute('aria-invalid');
      if (!S.reduced) {
        // 每敲一个字,玻璃轻轻一颤
        SP.jx.v += (Math.random() - 0.5) * 0.5;
        SP.jy.v += (Math.random() - 0.5) * 0.5;
        kick();
      }
    });
  }

  // ───────── 指针 / 跳过开场 / 陀螺仪 ─────────
  const inKit = (e) => e.target && e.target.closest && e.target.closest('[data-sample-kit]');
  addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch' || !S.ready) return;
    S.pointer.x = e.clientX;
    S.pointer.y = e.clientY;
    S.pointer.last = now();
    if (!S.mobile) {
      const r = els.auth.getBoundingClientRect();
      const pad = S.hoverDock ? 40 : 12;
      const inside = e.clientX > r.left - pad && e.clientX < r.right + pad && e.clientY > r.top - pad && e.clientY < r.bottom + pad;
      if (inside !== S.hoverDock) S.hoverDock = inside;
    }
    if (!S.pointer.active || S.wander.target) {
      S.pointer.active = true;
      S.wander.target = null;
      if (!S.intro && !isDocked()) followSprings();
    }
    kick();
  }, { passive: true });
  document.documentElement.addEventListener('pointerleave', () => { S.pointer.last = now(); if (S.hoverDock) { S.hoverDock = false; kick(); } });
  addEventListener('pointerdown', (e) => {
    if (inKit(e) || !S.ready) return;
    if (S.intro) skipIntro(false);
    const inForm = els.auth.contains(e.target);
    if (!inForm) {
      if (S.forceDock && S.phase !== 'loading') { S.forceDock = false; kick(); }
      if (e.pointerType === 'touch' && !S.reduced && !S.focusDock && !S.forceDock && S.phase === 'idle') {
        // 没有悬停:点哪里,透镜游过去替你看一眼,几秒后自己回到表单身后
        S.detached = true;
        S.wander.target = { x: e.clientX, y: e.clientY };
        SP.x.set(60, 0.82);
        SP.y.set(60, 0.82);
        clearTimeout(S.detachT);
        S.detachT = setTimeout(() => { S.detached = false; S.wander.target = null; kick(); }, 3600);
        kick();
      }
    }
  }, { passive: true });
  addEventListener('keydown', (e) => {
    if (inKit(e) || !S.ready) return;
    if (S.intro) skipIntro(false);
  });
  if (window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission !== 'function') {
    addEventListener('deviceorientation', (e) => {
      if (!S.mobile || S.reduced || e.gamma == null) return;
      const tx = clamp(e.gamma / 30, -1, 1), ty = clamp((e.beta - 40) / 30, -1, 1);
      if (Math.abs(tx - S.tilt.x) + Math.abs(ty - S.tilt.y) < 0.03) return;
      S.tilt.x = tx;
      S.tilt.y = ty;
      kick();
    });
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) kick(); });
  let rT = 0, lastW = innerWidth;
  addEventListener('resize', () => {
    clearTimeout(rT);
    // 手机键盘弹起只改高度:不重排文字场,免得题词跳动
    if (S.mobile && innerWidth === lastW) { kick(); return; }
    rT = setTimeout(() => {
      lastW = innerWidth;
      rebuild();
      if (!S.intro && !S.pointer.active && !isDocked()) { SP.x.snap(S.home.x); SP.y.snap(S.home.y); }
      for (const k of ['hw', 'hh', 'rr']) if (!isDocked() && S.phase !== 'success' && !S.intro) SP[k].snap(S.R);
      kick();
    }, 140);
  });
  addEventListener('sample:motion', (e) => {
    S.reduced = !!(e.detail && e.detail.reduced);
    if (S.reduced) {
      clearTimeout(S.wander.timer);
      S.wander.target = null;
      S.pointer.active = false;
      if (S.intro) skipIntro(true);
    } else scheduleWander(4000);
    kick();
  });

  // ───────── 本地时间文案 ─────────
  function tickClock() {
    const d = new Date();
    els.greet.textContent = `${weekCN} · ${greeting(d.getHours())}`;
    els.date.textContent = `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${WEEK[d.getDay()]}`;
    els.time.textContent = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    els.tz.textContent = tz;
    els.tz.hidden = !tz;
  }

  // ───────── 样页契约 ─────────
  function resetAll() {
    clearTimeout(pendingLogin);
    S.forceDock = false;
    setFog(false);
    S.fog = 0;
    if (document.activeElement && els.form.contains(document.activeElement)) document.activeElement.blur();
    S.focusDock = false;
    setPhase('idle');
  }
  window.LoginSample = {
    replay() {
      if (!S.ready) { S.pendingSkip = false; return; }
      resetAll();
      els.pass.value = '';
      startIntro();
    },
    skipIntro() {
      if (!S.ready) { S.pendingSkip = true; return; }
      skipIntro(true);
    },
    setState(name) {
      if (!S.ready) { S.pendingState = name; return; }
      if (S.intro) skipIntro(true);
      if (name === 'idle') { resetAll(); return; }
      if (name === 'loading') { clearTimeout(pendingLogin); S.forceDock = true; setPhase('loading'); return; }
      if (name === 'error') { clearTimeout(pendingLogin); showError('账号或密码错误', 'both'); return; }
      if (name === 'success') { clearTimeout(pendingLogin); setPhase('success'); }
    },
    extraControls: [
      { label: '找彩蛋', run: () => goEgg() },
      { label: params.get('gl') === '0' ? '恢复 WebGL' : '降级预览', run: () => {
        const q = new URLSearchParams(location.search);
        if (q.get('gl') === '0') q.delete('gl'); else q.set('gl', '0');
        location.search = q.toString();
      } },
    ],
  };

  // ───────── 启动 ─────────
  async function boot() {
    tickClock();
    setInterval(tickClock, 30000);
    const serifText = '信息如潮，替你看清。哆啦美 Dorami' + Object.values(CARDS).map((c) => c.h).join('') + Object.keys(DISPLAY).join('') + '欢迎回来';
    const wait = (p) => p.catch(() => null);
    const loads = [
      wait(document.fonts.load(`600 24px ${FONT.serif}`, serifText)),
      wait(document.fonts.load(`italic 400 20px ${FONT.latin}`, 'Everything worth reading')),
      new Promise((res) => { img.onload = res; img.onerror = res; img.src = 'assets/dorami-engraving.png'; }),
    ];
    await Promise.race([Promise.all(loads), new Promise((r) => setTimeout(r, 2200))]);
    S.gl = params.get('gl') !== '0' && GL.init(els.gl);
    if (!S.gl) FB.init();
    rebuild();
    S.ready = true;
    if (S.pendingSkip || S.reduced) skipIntro(true);
    else startIntro();
    if (S.pendingState) { const st = S.pendingState; S.pendingState = null; window.LoginSample.setState(st); }
    kick();
  }
  if (params.get('debug') === '1') window.__lens = { S, SP, GL, FB, render, update, FS_MAIN, VS };
  boot();
})();
