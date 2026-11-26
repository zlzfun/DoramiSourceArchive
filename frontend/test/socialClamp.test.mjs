// issue #346「显示更多」点了没有展开任何内容:按钮按字数出、截断按行数截,两套判定对不上。
// 这里守判定口径(纯函数)和三处接线(社交卡 / 手机壳末行渐隐 / 量的时机);真排版下的行为由沙箱 Playwright 走查。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { SOCIAL_CLAMP_CHARS, clampCuts, socialClampCandidate, socialMoreVisible } from '../src/utils/socialClamp.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.resolve(here, '../src', rel), 'utf8');

test('字数只决定进不进折叠态', () => {
  assert.equal(socialClampCandidate('a'.repeat(SOCIAL_CLAMP_CHARS)), false);
  assert.equal(socialClampCandidate('a'.repeat(SOCIAL_CLAMP_CHARS + 1)), true);
  assert.equal(socialClampCandidate(''), false);
  assert.equal(socialClampCandidate(undefined), false);
});

test('截没截掉内容看盒子:内容高超过可见高才算,取整差 1px 不算', () => {
  // 桌面 7 行 × 25.5 = 178.5,取整后 179;8 行 204
  assert.equal(clampCuts({ scrollHeight: 204, clientHeight: 179 }), true);
  assert.equal(clampCuts({ scrollHeight: 179, clientHeight: 179 }), false);
  assert.equal(clampCuts({ scrollHeight: 153, clientHeight: 153 }), false);
  assert.equal(clampCuts({ scrollHeight: 180, clientHeight: 179 }), false);
});

test('按钮:折叠时只在确实截断时出现,展开后一直在,不进折叠态的永远没有', () => {
  // 字数过线但排得下:没有按钮(修复前这里是一个点了没反应的「显示更多」)
  assert.equal(socialMoreVisible({ candidate: true, expanded: false, cut: false }), false);
  assert.equal(socialMoreVisible({ candidate: true, expanded: false, cut: true }), true);
  // 展开之后量不到截断(已经不限高),「收起」靠 expanded 留着
  assert.equal(socialMoreVisible({ candidate: true, expanded: true, cut: true }), true);
  assert.equal(socialMoreVisible({ candidate: true, expanded: true, cut: false }), true);
  for (const expanded of [false, true]) {
    for (const cut of [false, true]) assert.equal(socialMoreVisible({ candidate: false, expanded, cut }), false);
  }
});

test('社交卡:按钮走量出来的结果,不再直接比字数', () => {
  const src = read('components/SocialFlow.jsx');
  assert.match(src, /socialMoreVisible\(\{ candidate, expanded, cut \}\) && \(\s*<button type="button" className="social-more"/);
  assert.match(src, /const \[textRef, cut\] = useClampCut\(clamped, text\);/);
  // 字数门槛只剩 utils/socialClamp 一处;组件里再出现「text.length > 某个数」就是又把按钮绑回了字数
  assert.doesNotMatch(src, /text\.length\s*>/);
  assert.doesNotMatch(src, /CLAMP_CHARS/);
});

test('手机壳:末行渐隐只跟着 .is-cut,限高本身不带遮罩', () => {
  // 先去注释:规则上方的说明里也写着 .is-cut,不去掉会被当成选择器的一部分
  const css = read('mobile.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(([, selector, body]) => ({ selector: selector.trim(), body }))
    .filter(({ selector }) => /\.social-text/.test(selector));
  const masked = rules.filter(({ body }) => /(^|[\s;])(-webkit-)?mask\s*:/.test(body));
  assert.ok(masked.length > 0, '找不到社交正文的渐隐规则');
  for (const { selector } of masked) assert.match(selector, /\.is-cut\b/, selector);
  // 长按预览卡同一条规则:一两行的短推文不能被淡掉
  assert.ok(masked.some(({ selector }) => selector.includes('.m-peek-text')));
  assert.match(read('components/SocialFlow.jsx'), /className=\{`social-text m-peek-text\$\{cut \? ' is-cut' : ''\}`\}/);
});

test('量的时机:挂载当场量,尺寸变化推到下一帧,没排版不下结论', () => {
  const src = read('hooks/useClampCut.js');
  assert.match(src, /useLayoutEffect\(/);
  // 观察器回调里不直接读高度(WebKit 会报 ResizeObserver loop),只排一帧
  const callback = src.match(/new ResizeObserver\(\(\) => \{([\s\S]*?)\}\);/);
  assert.ok(callback, '找不到尺寸观察器回调');
  assert.match(callback[1], /requestAnimationFrame\(measure\)/);
  assert.doesNotMatch(callback[1], /scrollHeight|clientHeight|clampCuts|setCut/);
  assert.match(src, /if \(el\.clientHeight > 0\) setCut\(clampCuts\(el\)\);/);
});
