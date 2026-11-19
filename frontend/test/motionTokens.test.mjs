// 动效语言的 token 与共用弹簧取样(issue #301,conventions §7)。
// 守的三件事:① index.css 里五档弹簧的 linear() 与落定时长逐字等于 utils/spring.js 取样的结果(改参数要两边一起改);
// ② 取样工具抽成共用之后,播放条的弹簧曲线一个点都没变;③ 曲线与时长档都在、取值是拍板的那一套。
// 取 token 的办法:去注释后按花括号走一遍样式表,只认**顶层 `:root` 规则**里的声明,同名声明出现两次即报错——
// 挪进别的选择器、被注释掉、后面又被覆盖,这三种「看着还在、其实失效」的情形都会红(本文件末尾有反向用例)。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { MOTION_SPRINGS, SPRING_FALLBACK, motionSpring, spring, springCurve, springEasing, springSamples } from '../src/utils/spring.js';
import * as playerSpring from '../src/utils/playerSpring.js';

const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
const FALLBACK_AT = '@supports not (transition-timing-function: linear(0, 1))';

/** 去注释后按花括号走一遍,产出 { path: [各层前言], prop, value }。字符串里的花括号与分号不算。 */
function* declarations(text) {
  const src = text.replace(/\/\*[\s\S]*?\*\//g, '');
  const stack = [];
  let buf = '';
  const flush = function* flush() {
    const i = buf.indexOf(':');
    if (i > 0) {
      const prop = buf.slice(0, i).trim();
      if (/^-{0,2}[\w-]+$/.test(prop)) yield { path: [...stack], prop, value: buf.slice(i + 1).trim().replace(/\s+/g, ' ') };
    }
    buf = '';
  };
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < src.length && src[j] !== ch) j += src[j] === '\\' ? 2 : 1;
      buf += src.slice(i, j + 1);
      i = j;
    } else if (ch === '{') {
      stack.push(buf.trim().replace(/\s+/g, ' '));
      buf = '';
    } else if (ch === '}') {
      yield* flush();
      stack.pop();
    } else if (ch === ';') {
      yield* flush();
    } else {
      buf += ch;
    }
  }
}

/** 顶层 `:root` 里的自定义属性(name → 全部取值)与降级块里 `:root` 的自定义属性。 */
function motionCss(text) {
  const root = new Map();
  const fallback = new Map();
  for (const d of declarations(text)) {
    if (!d.prop.startsWith('--motion-')) continue;
    const bucket = d.path.length === 1 && d.path[0] === ':root' ? root
      : d.path.length === 2 && d.path[0] === FALLBACK_AT && d.path[1] === ':root' ? fallback
        : null;
    if (!bucket) continue;
    bucket.set(d.prop, [...(bucket.get(d.prop) || []), d.value]);
  }
  return { root, fallback };
}

const DURATIONS = { '--motion-press': '120ms', '--motion-hover-in': '100ms', '--motion-hover-out': '180ms', '--motion-fast': '150ms', '--motion-base': '200ms', '--motion-medium': '240ms', '--motion-slow': '300ms', '--motion-drawer': '360ms' };
const CURVES = { '--motion-ease': 'cubic-bezier(0.22, 1, 0.36, 1)', '--motion-ease-drawer': 'cubic-bezier(0.32, 0.72, 0, 1)', '--motion-ease-focus': 'cubic-bezier(0.2, 0.8, 0.2, 1)' };

/** 全部期望值逐条核对,返回问题清单(空 = 通过)。 */
function problems(text) {
  const { root, fallback } = motionCss(text);
  const out = [];
  const expect = (bucket, where, name, value) => {
    const got = bucket.get(name);
    if (!got) out.push(`${where} 缺 ${name}`);
    else if (got.length > 1) out.push(`${where} 的 ${name} 声明了 ${got.length} 次`);
    else if (got[0] !== value) out.push(`${where} 的 ${name} 取值不对`);
  };
  for (const [name, def] of Object.entries(MOTION_SPRINGS)) {
    const curve = springCurve(def);
    expect(root, ':root', `--motion-spring-${name}`, `linear(${curve.samples.join(', ')})`);
    expect(root, ':root', `--motion-spring-${name}-dur`, `${curve.duration}ms`);
    expect(fallback, '降级块', `--motion-spring-${name}`, 'var(--motion-ease)');
  }
  for (const [name, value] of Object.entries({ ...CURVES, ...DURATIONS })) expect(root, ':root', name, value);
  return out;
}

test('index.css 的动效 token:五档弹簧等于 utils/spring.js 取样的结果,降级块齐全,曲线与时长档是拍板的那一套', () => {
  assert.deepEqual(Object.keys(MOTION_SPRINGS), ['snappy', 'glide', 'move', 'settle', 'pop']);
  assert.deepEqual(problems(css), []);
});

test('守卫会咬人:改错取值、挪进别的选择器、注释掉、后面再覆盖一次,都报问题', () => {
  const dur = `--motion-spring-move-dur: ${springCurve(MOTION_SPRINGS.move).duration}ms;`;
  assert.ok(css.includes(dur));
  // ① 改错一个落定时长
  assert.deepEqual(problems(css.replace(dur, '--motion-spring-move-dur: 580ms;')), [':root 的 --motion-spring-move-dur 取值不对']);
  // ② 声明还在,但挪进了别的选择器(对 :root 已失效)
  assert.deepEqual(problems(css.replace(dur, `}\n.unused-motion { ${dur} }\n:root {`)), [':root 缺 --motion-spring-move-dur']);
  // ③ 降级块里的一档被注释掉
  const fb = '--motion-spring-move: var(--motion-ease);';
  assert.ok(css.includes(fb));
  assert.deepEqual(problems(css.replace(fb, `/* ${fb} */`)), ['降级块 缺 --motion-spring-move']);
  // ④ 后面又声明一次(样式表里后者胜,前面那条对的等于没写)
  assert.deepEqual(problems(`${css}\n:root { --motion-press: 300ms; }\n`), [':root 的 --motion-press 声明了 2 次']);
  // ⑤ 降级块整个不在
  assert.equal(problems(css.replace(FALLBACK_AT, '@supports (display: grid)')).length, 5);
});

test('springCurve:临界阻尼不过冲、带回弹的会过冲,末点恒为 1,落定时长随参数变', () => {
  const snappy = springCurve(MOTION_SPRINGS.snappy);
  assert.equal(snappy.samples[0], 0);
  assert.equal(snappy.samples.at(-1), 1);
  assert.ok(Math.max(...snappy.samples) <= 1, 'bounce 0 不应过冲');
  const pop = springCurve(MOTION_SPRINGS.pop);
  assert.ok(Math.max(...pop.samples) > 1.1, 'bounce 0.45 应有明显过冲');
  assert.equal(pop.samples.at(-1), 1);
  // 落定 = 偏离终值不超过 0.1% 的最后一刻:倒数第二点已在终值上下 0.5% 之内
  assert.ok(Math.abs(pop.samples.at(-2) - 1) < 0.005);
  assert.ok(springCurve({ duration: 0.6, bounce: 0 }).duration > snappy.duration);
  // 取样密度约每帧一点,封顶 64 段
  assert.ok(snappy.samples.length >= 17 && snappy.samples.length <= 65);
  assert.equal(springCurve({ duration: 3, bounce: 0.6 }).samples.length, 65);
});

test('spring / motionSpring:支持 linear() 给曲线,不支持退回三次贝塞尔;未知档名直接报错', () => {
  const yes = spring(MOTION_SPRINGS.glide, true);
  assert.match(yes.easing, /^linear\(0, /);
  assert.equal(yes.duration, springCurve(MOTION_SPRINGS.glide).duration);
  assert.equal(spring(MOTION_SPRINGS.glide, true), yes, '同参数走缓存');
  const no = spring(MOTION_SPRINGS.glide, false);
  assert.equal(no.easing, SPRING_FALLBACK);
  assert.equal(no.duration, yes.duration);
  assert.deepEqual(motionSpring('glide', true), yes);
  assert.throws(() => motionSpring('bouncy', true), /unknown motion spring/);
});

test('抽成共用之后播放条的弹簧没有变:再导出的是同一组函数,曲线逐点等于抽取前的取值', () => {
  assert.equal(playerSpring.springSamples, springSamples);
  assert.equal(playerSpring.springEasing, springEasing);
  assert.equal(playerSpring.SPRING_FALLBACK, SPRING_FALLBACK);
  const e = playerSpring.playerEasings(true);
  // 抽取前(main @ bb66fa43)在同一参数下的输出:首 6 点 + 末 3 点 + 点数,morph 与带初速度的 spread 各一条
  const points = (s) => s.slice('linear('.length, -1).split(', ').map(Number);
  assert.deepEqual(points(e.morph).slice(0, 6), [0, 0.0086, 0.032, 0.0667, 0.1098, 0.1589]);
  assert.deepEqual(points(e.morph).slice(-3), [1.0037, 1.0033, 1]);
  assert.equal(points(e.morph).length, 57);
  assert.deepEqual(points(e.spread).slice(0, 6), [0, 0.0796, 0.1565, 0.23, 0.2997, 0.3654]);
  assert.deepEqual(points(e.spread).slice(-3), [1.0009, 1.0009, 1]);
  assert.equal(points(e.spread).length, 57);
  assert.equal(e.out, 'cubic-bezier(.4, 0, 1, 1)');
});
