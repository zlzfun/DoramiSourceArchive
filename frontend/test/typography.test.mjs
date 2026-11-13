// 字体地基护栏(issue #184 / #176 W0a):样式表里的字重只用 400 / 600(宋体海报头条 900),字号不小于 12px;
// 未分层的 button / input { font: inherit } 不得回潮(它会压过层内对按钮、输入框写的字号字重);
// 字体随构建同源发出,不引外部字体域名(公司内网访问不到 Google Fonts)。
// 豁免:登录与品牌演出(.auth-* / .app-arrive*)、@font-face 描述符、来源字母徽标(.logo-mark*)、▸ / 排序箭头这类图形字符。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const SHEETS = { 'index.css': read('../src/index.css'), 'mobile.css': read('../src/mobile.css') };
// 豁免按「选择器」判断:一条规则的选择器列表里每一项都属豁免,这条规则(及嵌套在它里面的规则)才豁免——
// `.auth-title, .workspace-title` 不能因为带了 .auth- 就整条放行(codex R1-P3-01)。
const EXEMPT_SELECTOR = /\.auth-|\.app-arrive|\.logo-mark|\.scope-note summary::before|\.acct-sort-arrow/;

// 顶层逗号拆选择器列表(:is(a, b) 里的逗号不拆)
function splitSelectors(prelude) {
  const parts = [];
  let depth = 0;
  let current = '';
  for (const ch of prelude) {
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) { parts.push(current.trim()); current = ''; continue; }
    current += ch;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

const isExempt = (stack) => stack.some((prelude) => (
  prelude.startsWith('@font-face')
  || (!prelude.startsWith('@') && splitSelectors(prelude).every((sel) => EXEMPT_SELECTOR.test(sel)))
));

// 极简 CSS 扫描(零依赖):去注释(保留换行以维持行号)→ 逐字符走,引号与括号内的 ; { } 不计,
// 用规则块栈记住每条声明所在的选择器 / at-rule;同一行多条规则、嵌套 @media、font 简写都按声明逐条交出。
function declarations(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '));
  const out = [];
  const stack = [];
  let buf = '';
  let line = 1;
  let quote = null;
  let depth = 0;
  const flush = () => {
    const match = /^\s*([a-z-]+)\s*:\s*([\s\S]+?)\s*$/i.exec(buf);
    buf = '';
    if (!match) return;
    const prop = match[1].toLowerCase();
    const value = match[2].replace(/\s*!important\s*$/i, '');   // !important 不改变它是哪个字号
    // 嵌套规则(.a { &.on { … } } / .a { .b { … } })折叠成完整选择器,护栏按完整选择器判断归属
    const styleStack = stack.filter((p) => !p.startsWith('@'));
    const folded = styleStack.reduce((outer, inner) => (outer == null ? inner : splitSelectors(inner)
      .flatMap((sel) => splitSelectors(outer).map((parent) => (sel.includes('&') ? sel.replaceAll('&', parent) : `${parent} ${sel}`)))
      .join(', ')), null);
    const where = { selector: folded || stack.at(-1) || '', exempt: isExempt(stack), line };
    if (prop === 'font-weight' && /^\d{3}$/.test(value)) out.push({ kind: 'weight', value: Number(value), ...where });
    if (prop === 'font-size') {
      const px = /^(\d+(?:\.\d+)?)px$/.exec(value);
      if (px) out.push({ kind: 'size', value: Number(px[1]), ...where });
      else out.push({ kind: 'size-expr', value, ...where });   // calc / clamp / var / em…:交给读者面刻度护栏按精确例外核
    }
    if (prop === 'font') {
      const tokens = value.split(/\s+/);
      const weight = tokens.find((token) => /^\d{3}$/.test(token));
      const size = tokens.map((token) => /^(\d+(?:\.\d+)?)px(?:\/|$)/.exec(token)).find(Boolean);
      if (weight) out.push({ kind: 'weight', value: Number(weight), ...where });
      if (size) out.push({ kind: 'size', value: Number(size[1]), ...where });
    }
  };
  for (const ch of text) {
    if (ch === '\n') line += 1;
    if (quote) {
      buf += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; buf += ch; continue; }
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (depth > 0 || ch === '(' || ch === ')') { buf += ch; continue; }
    if (ch === '{') { stack.push(buf.trim()); buf = ''; continue; }
    if (ch === ';') { flush(); continue; }
    if (ch === '}') { flush(); stack.pop(); continue; }
    buf += ch;
  }
  return out;
}

test('扫描器自检:选择器列表、同行多规则、font 简写、嵌套豁免、注释与字符串', () => {
  const pick = (css) => declarations(css).map(({ kind, value, exempt }) => `${kind}:${value}:${exempt ? 'exempt' : 'checked'}`);
  assert.deepEqual(pick('.auth-title,\n.workspace-title { font-weight:700; }'), ['weight:700:checked']);
  assert.deepEqual(pick('.auth-title {font-weight:700;} .workspace-title {font-weight:800;}'), ['weight:700:exempt', 'weight:800:checked']);
  assert.deepEqual(pick('.workspace-label { font:600 10px/1.4 sans-serif; }'), ['weight:600:checked', 'size:10:checked']);
  assert.deepEqual(pick('.auth-title { @media (width < 400px) { font-weight:700; } }'), ['weight:700:exempt']);
  assert.deepEqual(pick('@font-face { font-family: X; font-weight: 700; }'), ['weight:700:exempt']);
  assert.deepEqual(pick('/* .x { font-size: 9px; } */ .y { content: "a;{"; font-size: 11px; }'), ['size:11:checked']);
  assert.deepEqual(pick('.z { background: url("data:image/svg+xml;utf8,<svg>{</svg>"); font-size: 13px; }'), ['size:13:checked']);
  assert.deepEqual(pick('.logo-mark-img, .reader-x { font-size: 9px; }'), ['size:9:checked']);
  assert.deepEqual(pick('.logo-mark-img { font-size: 9px; }'), ['size:9:exempt']);
});

test('字重只用 400 / 600(宋体海报头条 900)', () => {
  const bad = [];
  for (const [name, css] of Object.entries(SHEETS)) {
    for (const decl of declarations(css)) {
      if (decl.kind !== 'weight' || decl.exempt) continue;
      if (decl.value === 900 && /\.bp-title/.test(decl.selector)) continue;
      if (decl.value !== 400 && decl.value !== 600) bad.push(`${name}:${decl.line} ${decl.selector} → ${decl.value}`);
    }
  }
  assert.deepEqual(bad, []);
});

test('字号不小于 12px', () => {
  const bad = [];
  for (const [name, css] of Object.entries(SHEETS)) {
    for (const decl of declarations(css)) {
      if (decl.kind !== 'size' || decl.exempt) continue;
      if (decl.value < 12) bad.push(`${name}:${decl.line} ${decl.selector} → ${decl.value}px`);
    }
  }
  assert.deepEqual(bad, []);
});

// 读者面桌面的字号刻度(issue #279 第一批「并档」):index.css 里读者面的类只取 12 / 13 / 14 / 15 / 17 / 20 / 24 / 30 / 44。
// 现网实测曾有 17 级(12.5 / 13.5 / 16 / 16.5 / 21 / 22 / 23…),同级的东西好几个尺寸;刻度见 docs/frontend/conventions.md §3。
// READER_SCALE_PENDING 是棘轮:还没并档的在册例外,后续批次收口时从这里删;清单里的条目必须仍然存在,删了规则就得删清单。
// 不在此列:手机壳(mobile.css 有自己的刻度 --m-fs-*;index.css 里以 :where(.m-shell… 开头的是手机壳留值)、
// 早报海报头条(展示字号)、名字带 reader- / guest- 但其实是管理面在用的类。
const READER_SCALE = new Set([12, 13, 14, 15, 17, 20, 24, 30, 44]);
const READER_SELECTOR = /\.(reader-|bp-|rk-|social-|player-|listen-|podcast-|ranking-|markdown-body|guest-|brief-mast|interest-)/;
const MOBILE_KEEP = /^:where\(\.m-shell\b/;                          // 手机壳留值(紧跟被改的原规则,issue #279)
const ADMIN_OWNED = /\.reader-analysis-|\.guest-entry-/;             // 管理台文章详情 / 游客区,不属读者面桌面
const READER_DISPLAY = /^\.bp-title(\.|$|\s)/;                       // 早报海报头条:展示字号,刻度之外
const READER_SCALE_PENDING = new Map([
  // 第二批(版框与页头):事件页标题改宋体、兴趣页报头并入统一页头
  ['.rk-dtitle', [18]],
  ['.rk-event .rk-dtitle', [22]],
  ['.rk-event .rk-rep-title', [16]],
  ['.brief-mast-title', [32]],
  ['.interest-body .brief-mast-title', [32]],
  ['.interest-pick-name', [12.5]],
  ['.interest-search input', [12.5]],
  ['.interest-card-name', [13.5]],
  ['.interest-btn', [12.5]],
  ['.interest-onb-text', [12.5]],
]);
// 认不出的字号写法(calc / clamp / var / em…)一律要逐条登记:护栏算不出它落在哪一级,不登记就等于放行
const READER_SIZE_EXPR = new Map([
  ['.markdown-body code', ['0.9em']],                         // 行内代码跟随所在段落
  ['.listen-note', ['var(--text-xs)']],                       // 12px 的 token
  ['.bp-state-title', ['clamp(24px, 2.6cqi, 32px)']],         // 早报空态 / 失败态的标题:随容器伸缩的展示字号,上限 32 待并到 30
]);
const MOBILE_TOKEN = /var\(--m-fs-/;                            // 手机壳刻度的 token(播放器的手机形态写在 index.css 里)

function readerScaleViolations(css) {
  const bad = [];
  const seen = new Map();
  const mark = (key, value) => seen.set(key, (seen.get(key) || new Set()).add(value));
  for (const decl of declarations(css)) {
    if ((decl.kind !== 'size' && decl.kind !== 'size-expr') || decl.exempt) continue;
    for (const sel of splitSelectors(decl.selector)) {
      if (!READER_SELECTOR.test(sel) || MOBILE_KEEP.test(sel) || ADMIN_OWNED.test(sel) || READER_DISPLAY.test(sel)) continue;
      if (decl.kind === 'size') {
        if (READER_SCALE.has(decl.value)) continue;
        if (READER_SCALE_PENDING.get(sel)?.includes(decl.value)) { mark(`px ${sel}`, decl.value); continue; }
        bad.push(`${decl.line} ${sel} → ${decl.value}px`);
      } else {
        if (decl.value === 'inherit' || MOBILE_TOKEN.test(decl.value)) continue;
        if (READER_SIZE_EXPR.get(sel)?.includes(decl.value)) { mark(`expr ${sel}`, decl.value); continue; }
        bad.push(`${decl.line} ${sel} → ${decl.value}`);
      }
    }
  }
  return { bad, seen };
}

test('读者面桌面字号只取刻度上的值(在册例外逐批收口)', () => {
  const { bad, seen } = readerScaleViolations(SHEETS['index.css']);
  assert.deepEqual(bad, []);
  // 棘轮:清单里的例外都还在(收口后忘了删清单会在这里红)
  const stale = [];
  for (const [sel, values] of READER_SCALE_PENDING) for (const value of values) if (!seen.get(`px ${sel}`)?.has(value)) stale.push(`${sel} → ${value}px`);
  for (const [sel, values] of READER_SIZE_EXPR) for (const value of values) if (!seen.get(`expr ${sel}`)?.has(value)) stale.push(`${sel} → ${value}`);
  assert.deepEqual(stale, []);
});

test('读者面字号护栏自检:!important、嵌套、表达式都拦得住,手机壳留值与管理面的类不误伤', () => {
  const bad = (css) => readerScaleViolations(css).bad.map((line) => line.replace(/^\d+ /, ''));
  assert.deepEqual(bad('.reader-x { font-size: 13.5px; }'), ['.reader-x → 13.5px']);
  assert.deepEqual(bad('.reader-x { font-size: 13.5px !important; }'), ['.reader-x → 13.5px']);
  assert.deepEqual(bad('.reader-x { &.is-on { font-size: 13.5px; } }'), ['.reader-x.is-on → 13.5px']);
  assert.deepEqual(bad('.reader-x { .inner { font: 600 16px/1.4 sans-serif; } }'), ['.reader-x .inner → 16px']);
  assert.deepEqual(bad('@container (max-width: 680px) { .bp-x { font-size: 19px; } }'), ['.bp-x → 19px']);
  assert.deepEqual(bad('.reader-x { font-size: calc(13.5px); }'), ['.reader-x → calc(13.5px)']);
  assert.deepEqual(bad('.reader-x { font-size: clamp(24px, 2.6cqi, 32px); }'), ['.reader-x → clamp(24px, 2.6cqi, 32px)']);
  assert.deepEqual(bad('.reader-x { font-size: var(--some-size); }'), ['.reader-x → var(--some-size)']);
  assert.deepEqual(bad('.reader-x, .acct-y { font-size: 13.5px; }'), ['.reader-x → 13.5px']);
  // 不误伤
  assert.deepEqual(bad('.reader-x { font-size: 13px; } .reader-y { font-size: inherit; }'), []);
  assert.deepEqual(bad(':where(.m-shell, .m-page, .m-read, .m-sheet) .reader-x { font-size: 12.5px; }'), []);
  assert.deepEqual(bad('.reader-analysis-score strong { font-size: 22px; } .guest-entry-head { font-size: 12.5px; }'), []);
  assert.deepEqual(bad('.bp-title { font-size: 73px; } .acct-table { font-size: 12.5px; }'), []);
  assert.deepEqual(bad('.player-layer.is-mobile .player-sub { font-size: var(--m-fs-xs); }'), []);
});

test('未分层的 button / input { font: inherit } 不回潮', () => {
  // preflight 在 @layer base 里已有同一条;未分层的那条会压过层内组件规则与原子类
  assert.doesNotMatch(SHEETS['index.css'], /^button,\s*\ninput,\s*\nselect,\s*\ntextarea\s*\{\s*\n\s*font:\s*inherit;/m);
});

test('字体随构建同源发出:不引外部字体域名,宋体样式表与 nginx 长缓存在位', () => {
  const html = read('../index.html');
  const css = SHEETS['index.css'];
  for (const text of [html, css]) {
    assert.doesNotMatch(text, /fonts\.(googleapis|gstatic)\.com|fonts\.loli\.net|cdn\.jsdelivr\.net\/[^"']*font/);
  }
  assert.match(html, /href="\/fonts\/dorami-serif-v1\/dorami-serif\.css"/);
  const fontCss = read('../public/fonts/dorami-serif-v1/dorami-serif.css');
  assert.match(fontCss, /font-family:\s*"Dorami Serif"/);
  assert.match(fontCss, /font-weight:\s*600/);
  assert.match(fontCss, /font-weight:\s*900/);
  // 分片一律同目录相对引用(./dorami-serif-<字重>-<序号>.woff2),没有任何外部地址
  const urls = [...fontCss.matchAll(/url\(([^)]+)\)/g)].map((m) => m[1].replace(/["']/g, ''));
  assert.ok(urls.length >= 200);
  assert.deepEqual(urls.filter((url) => !/^\.\/dorami-serif-(600|900)-\d{3}\.woff2$/.test(url)), []);
  for (const conf of ['../../docker/nginx.conf', '../../deploy.sh']) {
    // deploy.sh 在 heredoc 里写 nginx 配置,$uri 转义成 \$uri
    assert.match(read(conf), /location \/fonts\/ \{\s*\n\s*expires 1y;\s*\n\s*try_files \\?\$uri =404;/);
  }
});

test('西文衬线随构建同源发出:Dorami Latin Serif 三个样式各两片,只引同目录的 woff2(目检返修)', () => {
  assert.match(read('../index.html'), /href="\/fonts\/dorami-latin-v1\/dorami-latin\.css"/);
  const fontCss = read('../public/fonts/dorami-latin-v1/dorami-latin.css');
  assert.doesNotMatch(fontCss, /https?:/);
  const faces = [...fontCss.matchAll(/@font-face\{([^}]*)\}/g)].map((m) => m[1]);
  assert.equal(faces.length, 6);
  faces.forEach((face) => assert.match(face, /font-family:"Dorami Latin Serif"/));
  const styles = new Set(faces.map((face) => `${/font-style:(\w+)/.exec(face)[1]} ${/font-weight:(\d+)/.exec(face)[1]}`));
  assert.deepEqual([...styles].sort(), ['italic 400', 'normal 600', 'normal 900']);
  const urls = faces.map((face) => /url\("([^"]+)"\)/.exec(face)[1]);
  assert.deepEqual(urls.filter((url) => !/^\.\/dorami-latin-(600|900|400i)-latin(-ext)?\.woff2$/.test(url)), []);
});

test('西文衬线 token 先 Dorami Latin Serif、中文落回 Dorami Serif;lang="en" 把宋体角色换成它', () => {
  const css = SHEETS['index.css'];
  const latin = /--font-serif-latin:\s*([^;]+);/.exec(css)?.[1] || '';
  assert.match(latin, /^"Dorami Latin Serif",\s*"Dorami Serif"/);
  assert.match(css, /\[lang="en"\]\s*\{\s*--font-serif:\s*var\(--font-serif-latin\);\s*\}/);
});

test('字体 token:标题宋体先 Dorami Serif、回退不落中易宋体;分数字体不再依赖 Charter', () => {
  const css = SHEETS['index.css'];
  const serif = /--font-serif:\s*([^;]+);/.exec(css)?.[1] || '';
  assert.match(serif, /^"Dorami Serif"/);
  assert.doesNotMatch(serif, /SimSun|"宋体"|NSimSun/);
  const score = /--font-score:\s*([^;]+);/.exec(css)?.[1] || '';
  assert.match(score, /^"Dorami Serif"/);
  assert.doesNotMatch(css, /font-family:\s*Charter/);
});
