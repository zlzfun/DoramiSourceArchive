// 字体地基护栏(issue #184 / #176 W0a):样式表里的字重只用 400 / 600(宋体海报头条 900),字号不小于 12px;
// 未分层的 button / input { font: inherit } 不得回潮(它会压过层内对按钮、输入框写的字号字重);
// 字体随构建同源发出,不引外部字体域名(公司内网访问不到 Google Fonts)。
// 豁免:登录与品牌演出(.auth-* / .app-arrive*)、@font-face 描述符、来源字母徽标(.logo-mark*)、▸ / 排序箭头这类图形字符。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const SHEETS = { 'index.css': read('../src/index.css'), 'mobile.css': read('../src/mobile.css') };
const EXEMPT = /\.auth-|\.app-arrive|@font-face|\.logo-mark|\.scope-note summary::before|\.acct-sort-arrow/;

// 逐行追踪当前规则的选择器(含逗号续行),把每条字重 / 字号声明连同它所属的选择器交出来
function declarations(css) {
  const out = [];
  let selector = '';
  let pending = '';
  let inComment = false;
  css.split('\n').forEach((line, index) => {
    let text = line;
    if (inComment) {
      const end = text.indexOf('*/');
      if (end < 0) return;
      text = text.slice(end + 2);
      inComment = false;
    }
    text = text.replace(/\/\*.*?\*\//g, '');
    const open = text.indexOf('/*');
    if (open >= 0) {
      inComment = true;
      text = text.slice(0, open);
    }
    const stripped = text.trim();
    if (!stripped) return;
    if (stripped.includes('{')) {
      selector = `${pending} ${stripped.split('{')[0]}`.trim();
      pending = '';
    } else if (stripped.endsWith(',') && !stripped.includes(':')) {
      pending += ` ${stripped}`;
    }
    for (const match of stripped.matchAll(/font-weight:\s*(\d{3})/g)) {
      out.push({ kind: 'weight', value: Number(match[1]), selector, line: index + 1 });
    }
    for (const match of stripped.matchAll(/font:\s*(\d{3})\s/g)) {
      out.push({ kind: 'weight', value: Number(match[1]), selector, line: index + 1 });
    }
    for (const match of stripped.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
      out.push({ kind: 'size', value: Number(match[1]), selector, line: index + 1 });
    }
  });
  return out;
}

test('字重只用 400 / 600(宋体海报头条 900)', () => {
  const bad = [];
  for (const [name, css] of Object.entries(SHEETS)) {
    for (const decl of declarations(css)) {
      if (decl.kind !== 'weight' || EXEMPT.test(decl.selector)) continue;
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
      if (decl.kind !== 'size' || EXEMPT.test(decl.selector)) continue;
      if (decl.value < 12) bad.push(`${name}:${decl.line} ${decl.selector} → ${decl.value}px`);
    }
  }
  assert.deepEqual(bad, []);
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

test('字体 token:标题宋体先 Dorami Serif、回退不落中易宋体;分数字体不再依赖 Charter', () => {
  const css = SHEETS['index.css'];
  const serif = /--font-serif:\s*([^;]+);/.exec(css)?.[1] || '';
  assert.match(serif, /^"Dorami Serif"/);
  assert.doesNotMatch(serif, /SimSun|"宋体"|NSimSun/);
  const score = /--font-score:\s*([^;]+);/.exec(css)?.[1] || '';
  assert.match(score, /^"Dorami Serif"/);
  assert.doesNotMatch(css, /font-family:\s*Charter/);
});
