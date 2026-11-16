// 标题挑折行点(issue #279 第五批补):算法是纯函数,宽度用手写的数字测;显示层那一半(量宽度、行宽变了重算)只测结构约束。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { bondAfter, pickBreaks, stopAfter, titleUnits } from '../src/utils/titleFit.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, rel), 'utf8');

// 把一组片按给定宽度(字宽)排成一行,返回 pickBreaks 的入参;gaps[i] 是片 i 前面的空隙
function lay(texts, widths, { gaps = [], glued = [], ...rest } = {}) {
  const starts = [];
  const ends = [];
  let x = 0;
  widths.forEach((w, i) => { x += gaps[i] || 0; starts.push(x); x += w; ends.push(x); });
  return {
    starts, ends, em: 1, glued,
    stops: texts.map((t, i) => stopAfter(t, texts[i + 1])),
    bonds: texts.map((t, i) => bondAfter(t, texts[i + 1])),
    ...rest,
  };
}
const split = (texts, heads) => [0, ...heads].map((from, k) => texts.slice(from, heads[k] ?? texts.length).join('')).join(' / ');

test('停顿级别:全角停顿、带空格的半角停顿、破折号两侧、汉字间的空格是强停顿;引号括号外侧与顿号是弱停顿', () => {
  assert.equal(stopAfter('职务，', '市值'), 2);
  assert.equal(stopAfter('AI：', '马斯克'), 2);
  assert.equal(stopAfter('特辑】', '斯坦福'), 2);
  assert.equal(stopAfter('OpenAI: ', '发布'), 2);
  assert.equal(stopAfter('| ', '安德鲁'), 2);
  assert.equal(stopAfter('战略', '——'), 2);
  assert.equal(stopAfter('——', '对话'), 2);
  assert.equal(stopAfter('发布会 ', '新品'), 2);
  assert.equal(stopAfter('掀桌！」', '从'), 2, '引号里的叹号仍是强停顿');
  assert.equal(stopAfter('「碳」', '全球'), 1);
  assert.equal(stopAfter('漏洞、', '扫描'), 1);
  assert.equal(stopAfter('发布', '「超级'), 1);
  // 不是停顿:数字里的逗号冒号小数点、西文词之间的空格、汉字与西文之间的空格
  assert.equal(stopAfter('10:30', '开始'), 0);
  assert.equal(stopAfter('1,000', '个'), 0);
  assert.equal(stopAfter('1.4', '万亿'), 0);
  assert.equal(stopAfter('Business ', 'Hub'), 0);
  assert.equal(stopAfter('Hub ', '管理'), 0);
  assert.equal(stopAfter('美元', ''), 0);
  // 只有空格、只有收尾引号的片:不算停顿(空字符不在任何集合里)
  assert.equal(stopAfter('  ', '开头'), 0);
  assert.equal(stopAfter('', '开头'), 0);
});

test('折开要罚的地方:数字与单位、连着的西文词、行首的「的」「之」、行尾引出下文的词', () => {
  assert.ok(bondAfter('1.4', '万亿') > bondAfter('Gemini ', '4'));
  assert.ok(bondAfter('Gemini ', '4') > 0);
  assert.ok(bondAfter('4 ', 'Hydrogen') > 0);
  assert.ok(bondAfter('AI ', '之名') > 0);
  assert.ok(bondAfter('Anthropic ', '的 ') > 0);
  assert.ok(bondAfter('结果如何', '改变') > 0);
  assert.equal(bondAfter('1.4 ', '万亿'), 0 + bondAfter('x ', '万亿'), '隔了空格的数字不算数字加单位');
  assert.equal(bondAfter('蒸发', '1.4'), 0);
  // 版本号后面的正文不是「数字 + 单位」
  assert.equal(bondAfter('5.2', '深夜'), 0);
  assert.equal(bondAfter('GPT5', '发布'), 0);
  assert.ok(bondAfter('217', '条') > 0 && bondAfter('400', '年历史') > 0 && bondAfter('10', '个维度') > 0);
  assert.equal(bondAfter('丑闻', '辞去'), 0);
  assert.equal(bondAfter('美元', ''), 0);
});

test('分块 → 片:按空格再切、收尾破折号单独成片;拼回去逐字相等;块里面的折点带 glued', () => {
  const cases = [
    ['黄仁勋因', '间谍', '丑闻', '辞去', '英伟达', 'CEO', '职务，', '市值', '蒸发', '1.4', '万亿', '美元'],
    ['Claude ', 'Meetings ', '让 Anthropic 的 ', 'AI ', '加入'],
    ['美国的', 'ASI', '战略——', '对话', '兰德'],
    ['升级 —— 推出 ', 'ChatGPT '],
    ['在 1–2 天内', '交付'],
    ['  开头有空格', '的标题 '],
  ];
  for (const chunks of cases) assert.equal(titleUnits(chunks).map((unit) => unit.text).join(''), chunks.join(''));
  assert.deepEqual(titleUnits(['让 Anthropic 的 ', 'AI ']), [
    { text: '让 ', glued: false }, { text: 'Anthropic ', glued: true }, { text: '的 ', glued: true }, { text: 'AI ', glued: false },
  ]);
  // 破折号前后都不带罚;数字之间的短横不拆
  assert.deepEqual(titleUnits(['战略——', '对话']).map((unit) => [unit.text, unit.glued]), [['战略', false], ['——', false], ['对话', false]]);
  assert.deepEqual(titleUnits(['升级 —— 推出 ']).map((unit) => [unit.text, unit.glued]), [['升级 ', false], ['—— ', false], ['推出 ', false]]);
  assert.deepEqual(titleUnits(['在 1–2 天内']).map((unit) => unit.text), ['在 ', '1–2 ', '天内']);
  assert.deepEqual(titleUnits(['GPT–5 ']).map((unit) => unit.text), ['GPT–5 ']);
});

test('一行排得下不折;某一片比行还宽交回浏览器;入参不全返回 null', () => {
  assert.deepEqual(pickBreaks(lay(['你好', '世界'], [2, 2], { limit: 10 })), []);
  assert.equal(pickBreaks(lay(['短', 'Pneumonoultramicroscopic', '词'], [1, 14, 1], { limit: 10 })), null);
  assert.equal(pickBreaks(lay([], [], { limit: 10 })), null);
  assert.equal(pickBreaks(lay(['你好', '世界'], [2, 2], { limit: 0 })), null);
});

test('引出这次返修的那条头条:折在逗号后面,不把「市值」甩在行尾', () => {
  const texts = ['黄仁勋因', '间谍', '丑闻', '辞去', '英伟达', 'CEO', '职务，', '市值', '蒸发', '1.4', '万亿', '美元'];
  // 73px 头条里实测的宽度(字宽):CEO 两侧与 1.4 两侧各有八分之一字宽的间隙,逗号收窄后 0.58
  const input = lay(texts, [4, 2, 2, 2, 3, 2.16, 2.58, 2, 2, 1.42, 2, 2], { gaps: [0, 0, 0, 0, 0, 0.125, 0.125, 0, 0, 0.125, 0.125, 0], limit: 13.39 });
  assert.equal(split(texts, pickBreaks(input)), '黄仁勋因间谍丑闻 / 辞去英伟达CEO职务， / 市值蒸发1.4万亿美元');
  // 浏览器的均衡折行在这里给的是 10 / 9.99 / 7.67:逗号后面只带一个词
  assert.notEqual(split(texts, pickBreaks(input)).split(' / ')[1].slice(-2), '市值');
});

test('行数等于逐行铺满的最少行数,每行都排得下,折点升序(随机宽度)', () => {
  let seed = 7;
  const rand = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  for (let round = 0; round < 300; round += 1) {
    const n = 2 + Math.floor(rand() * 18);
    const widths = Array.from({ length: n }, () => 1 + Math.floor(rand() * 5));
    const texts = widths.map((w, i) => (rand() < 0.2 ? '词，' : `词${i}`));
    const limit = 6 + Math.floor(rand() * 12);
    for (const balance of [true, false]) {
      const input = lay(texts, widths, { limit, balance, glued: widths.map(() => rand() < 0.15) });
      const heads = pickBreaks(input);
      let greedy = 1;
      for (let i = 0, used = 0; i < n; i += 1) { if (used + widths[i] > limit) { greedy += 1; used = 0; } used += widths[i]; }
      if (greedy > 12) { assert.equal(heads, null, '行数过多:交回浏览器'); continue; }
      assert.ok(Array.isArray(heads));
      assert.equal(heads.length + 1, greedy, `行数 ${heads.length + 1} ≠ 逐行铺满的 ${greedy}`);
      const edges = [0, ...heads, n];
      for (let k = 0; k + 1 < edges.length; k += 1) {
        assert.ok(edges[k + 1] > edges[k]);
        assert.ok(input.ends[edges[k + 1] - 1] - input.starts[edges[k]] <= limit);
      }
    }
  }
});

test('停顿处优先,但不为它把行折得一长一短;分句不甩下一两个词', () => {
  // 两个差不多长的分句:折在逗号
  let texts = ['苹果', '发布', '新款', '电脑，', '搭载', '自研', '芯片'];
  assert.equal(split(texts, pickBreaks(lay(texts, [2, 2, 2, 3, 2, 2, 2], { limit: 12 }))), '苹果发布新款电脑， / 搭载自研芯片');
  // 冒号前面只有一个短词:不折在冒号(否则第一行只剩一个词)
  texts = ['快讯：', '某某', '公司', '宣布', '完成', '新一轮', '融资', '计划'];
  assert.equal(split(texts, pickBreaks(lay(texts, [3, 2, 2, 2, 2, 3, 2, 2], { limit: 12 }))).split(' / ')[0] === '快讯：', false);
  // 三行的标题,逗号在第一行中段:折在逗号,不把逗号后面那个词留在第一行行尾
  texts = ['甲乙', '丙丁，', '戊己', '庚辛', '壬癸', '子丑', '寅卯', '辰巳', '午未'];
  const lines = split(texts, pickBreaks(lay(texts, [2, 3, 2, 2, 2, 2, 2, 2, 2], { limit: 8 }))).split(' / ');
  assert.equal(lines.length, 3);
  assert.equal(lines[0], '甲乙丙丁，', lines.join(' / '));
});

test('数字与单位、连着的西文词不折开;块里面的空格只在保住行数时才折', () => {
  let texts = ['市值', '蒸发', '1.4', '万亿', '美元', '创下', '纪录'];
  let lines = split(texts, pickBreaks(lay(texts, [2, 2, 1.5, 2, 2, 2, 2], { limit: 8 }))).split(' / ');
  assert.ok(!lines.some((line) => line.endsWith('1.4')), lines.join(' / '));
  // 西文名字夹在三行标题里:不为了行宽更匀把名字拆开
  texts = ['谷歌', 'Gemini ', '4 ', 'Hydrogen', '发布', '仅', '四周，', '得分', '便', '超越', '更大的', 'Argon'];
  lines = split(texts, pickBreaks(lay(texts, [2, 3.6, 1, 4.6, 2, 1, 2.6, 2, 1, 2, 3, 3], { limit: 13.4 }))).split(' / ');
  assert.deepEqual(lines, ['谷歌Gemini 4 Hydrogen', '发布仅四周，', '得分便超越更大的Argon']);
  // 「让 Anthropic 的」是一块:有别的折法时不在块里折
  texts = ['新功能', '让 ', 'Anthropic ', '的 ', 'AI ', '加入', '通话'];
  const glued = [false, false, true, true, false, false, false];
  lines = split(texts, pickBreaks(lay(texts, [3, 1.2, 5, 1.2, 1.5, 2, 2], { limit: 11, glued }))).split(' / ');
  assert.ok(!lines.some((line) => line.endsWith('让 ') || line.endsWith('Anthropic ')), lines.join(' / '));
  // 只有块里面那一处排得下两行:照折,不多出一行
  texts = ['Claude ', 'Meetings ', '让 ', 'Anthropic ', '的 ', 'AI'];
  lines = split(texts, pickBreaks(lay(texts, [3.5, 4.5, 1.2, 5, 1.2, 1.2], { limit: 9.5, glued: [false, false, false, true, true, false] }))).split(' / ');
  assert.equal(lines.length, 2);
});

test('行宽风格:均衡的各行等宽;铺满的先排满前面的行,但末行不留得太短', () => {
  const texts = ['一一', '二二', '三三', '四四', '五五', '六六'];
  const widths = [2, 2, 2, 2, 2, 2];
  assert.equal(split(texts, pickBreaks(lay(texts, widths, { limit: 10, balance: true }))), '一一二二三三 / 四四五五六六');
  assert.equal(split(texts, pickBreaks(lay(texts, widths, { limit: 8, balance: false }))), '一一二二三三四四 / 五五六六');
  // 铺满到头末行只剩一个词(不足三成):挪一个词下来
  assert.equal(split(texts, pickBreaks(lay(texts, widths, { limit: 10, balance: false }))), '一一二二三三四四 / 五五六六');
});

test('显示层:折点不进文本、只在桌面的标题组件里用;手机壳没有这些标记', () => {
  const title = read('../src/components/DisplayTitle.jsx');
  assert.match(title, /const heads = useTitleFit\(ref, units\);/);
  assert.match(title, /heads\?\.includes\(index\) && <span className="ts-nl" aria-hidden="true" \/>/);
  assert.match(title, /index > 0 && !heads && !unit\.glued && <wbr \/>/, '指定折点之后不再放 <wbr>(它在 nowrap 里照样可折)');
  assert.ok(!/<br\b/.test(title), '不用 <br>:复制出来的标题不该带换行');
  const css = read('../src/index.css');
  assert.match(css, /\n\.ts-kw\.is-fit \{ white-space: nowrap; \}/);
  assert.match(css, /\n\.ts-nl::before \{ content: "\\A"; white-space: pre; \}/);
  assert.match(css, /\n\.ts-kw\.ts-ruler \{ position: absolute; [^}]*visibility: hidden; white-space: nowrap;/);
  assert.match(css, /\.ts-ruler :is\(wbr, \.ts-nl\), \.ts-kw\.is-probe \.ts-nl \{ display: none; \}/);
  const hook = read('../src/hooks/useTitleFit.js');
  assert.match(hook, /ruler\.remove\(\);/, '量宽度的克隆当场移除');
  assert.match(hook, /width <= last\.limit \+ 0\.5 && width >= last\.longest - 1/, '盒宽还落在「最长一行 ~ 上次行宽」之间不重算');
  assert.ok(!hook.includes('flushSync'), '观察回调里不同步改排版(否则触发 ResizeObserver 回环)');
  // 只有 DisplayTitle 用这只钩子;手机壳的样式与组件里没有这些标记
  const srcDir = path.join(here, '../src');
  const users = [];
  const walk = (dir) => readdirSync(dir, { withFileTypes: true }).forEach((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.jsx?$/.test(entry.name) && readFileSync(full, 'utf8').includes('useTitleFit(')) users.push(path.relative(srcDir, full));
  });
  walk(srcDir);
  assert.deepEqual(users.sort(), ['components/DisplayTitle.jsx', 'hooks/useTitleFit.js']);
  assert.ok(!/\.ts-(nl|ruler|w)\b|is-fit|is-probe/.test(read('../src/mobile.css')));
});
