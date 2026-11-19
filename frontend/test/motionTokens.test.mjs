// 动效语言的 token 与共用弹簧取样(issue #301,conventions §7)。
// 守的三件事:① index.css 里五档弹簧的 linear() 与落定时长逐字等于 utils/spring.js 取样的结果(改参数要两边一起改);
// ② 取样工具抽成共用之后,播放条的弹簧曲线一个点都没变;③ 曲线与时长档都在、取值是拍板的那一套。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { MOTION_SPRINGS, SPRING_FALLBACK, motionSpring, spring, springCurve, springEasing, springSamples } from '../src/utils/spring.js';
import * as playerSpring from '../src/utils/playerSpring.js';

const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');
// 只看第一个 :root 块里的定义(@supports 降级块里的同名变量是 var(--motion-ease),不参与比对)
const rootBlock = css.slice(css.indexOf(':root {'), css.indexOf('@supports not (transition-timing-function: linear(0, 1))'));
const token = (name) => {
  const m = rootBlock.match(new RegExp(`\\n\\s*${name}:\\s*([^;]+);`));
  return m ? m[1].trim() : null;
};

test('五档弹簧:index.css 的 linear() 与落定时长等于 utils/spring.js 取样的结果', () => {
  assert.deepEqual(Object.keys(MOTION_SPRINGS), ['snappy', 'glide', 'move', 'settle', 'pop']);
  for (const [name, def] of Object.entries(MOTION_SPRINGS)) {
    const curve = springCurve(def);
    assert.equal(token(`--motion-spring-${name}`), `linear(${curve.samples.join(', ')})`, `--motion-spring-${name} 与取样不一致`);
    assert.equal(token(`--motion-spring-${name}-dur`), `${curve.duration}ms`, `--motion-spring-${name}-dur 与落定时长不一致`);
  }
});

test('五档弹簧:不支持 linear() 的浏览器有降级块,五档都退回 --motion-ease', () => {
  const start = css.indexOf('@supports not (transition-timing-function: linear(0, 1))');
  assert.ok(start > 0);
  const block = css.slice(start, css.indexOf('\n}\n', start));
  for (const name of Object.keys(MOTION_SPRINGS)) {
    assert.match(block, new RegExp(`--motion-spring-${name}:\\s*var\\(--motion-ease\\);`));
  }
});

test('曲线与时长档:取值是拍板的那一套', () => {
  assert.equal(token('--motion-ease'), 'cubic-bezier(0.22, 1, 0.36, 1)');
  assert.equal(token('--motion-ease-drawer'), 'cubic-bezier(0.32, 0.72, 0, 1)');
  assert.equal(token('--motion-ease-focus'), 'cubic-bezier(0.2, 0.8, 0.2, 1)');
  const durations = { '--motion-press': '120ms', '--motion-hover-in': '100ms', '--motion-hover-out': '180ms', '--motion-fast': '150ms', '--motion-base': '200ms', '--motion-medium': '240ms', '--motion-slow': '300ms', '--motion-drawer': '360ms' };
  for (const [name, value] of Object.entries(durations)) assert.equal(token(name), value, name);
});

test('springCurve:临界阻尼不过冲、带回弹的会过冲,末点恒为 1,落定时长随参数变', () => {
  const snappy = springCurve(MOTION_SPRINGS.snappy);
  assert.equal(snappy.samples[0], 0);
  assert.equal(snappy.samples.at(-1), 1);
  assert.ok(Math.max(...snappy.samples) <= 1, 'bounce 0 不应过冲');
  const pop = springCurve(MOTION_SPRINGS.pop);
  assert.ok(Math.max(...pop.samples) > 1.1, 'bounce 0.45 应有明显过冲');
  assert.equal(pop.samples.at(-1), 1);
  // 落定 = 偏离终值不超过 0.1% 的最后一刻:倒数第二点已在 0.1% 之内上下
  assert.ok(Math.abs(pop.samples.at(-2) - 1) < 0.005);
  assert.ok(springCurve({ duration: 0.6, bounce: 0 }).duration > snappy.duration);
  // 取样密度约每帧一点,封顶 64
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
  const head = (s, n) => s.slice('linear('.length, -1).split(', ').slice(0, n).map(Number);
  const tail = (s, n) => s.slice('linear('.length, -1).split(', ').slice(-n).map(Number);
  assert.deepEqual(head(e.morph, 6), [0, 0.0086, 0.032, 0.0667, 0.1098, 0.1589]);
  assert.deepEqual(tail(e.morph, 3), [1.0037, 1.0033, 1]);
  assert.equal(e.morph.split(', ').length, 57);
  assert.deepEqual(head(e.spread, 6), [0, 0.0796, 0.1565, 0.23, 0.2997, 0.3654]);
  assert.deepEqual(tail(e.spread, 3), [1.0009, 1.0009, 1]);
  assert.equal(e.spread.split(', ').length, 57);
  assert.equal(e.out, 'cubic-bezier(.4, 0, 1, 1)');
});
