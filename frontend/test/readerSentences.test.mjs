import test from 'node:test';
import assert from 'node:assert/strict';
import { splitSentences } from '../src/utils/readerText.js';

test('速读按句分段:只在全角句末标点处断开,原文一字不改', () => {
  const text = 'Anthropic 于 11 月 9 日发布 Claude Fable 5.2 与 Claude Mythos 5.2，两者基于同一底层模型。Fable 5.2 定价为每百万 Token 输入 8 美元、输出 40 美元，较 5.1 下降 20%。Fable 5.2 已在 AWS、Google Cloud 和 Microsoft Azure 开放。';
  const parts = splitSentences(text);
  assert.equal(parts.length, 3);
  assert.equal(parts.join(''), text);
  // 小数点、版本号里的半角句点不是句末
  assert.ok(parts[0].includes('Fable 5.2 与 Claude Mythos 5.2'));
});

test('太短的句子并回上一段;句末的引号、括号跟着上一句走', () => {
  assert.deepEqual(
    splitSentences('这是一句足够长的话,讲的是模型发布的事情。同时开源。'),
    ['这是一句足够长的话,讲的是模型发布的事情。同时开源。'],
  );
  assert.deepEqual(
    splitSentences('他说:「这款模型的推理成本比上一代低了一半。」随后公司公布了完整的定价与上线时间表。'),
    ['他说:「这款模型的推理成本比上一代低了一半。」', '随后公司公布了完整的定价与上线时间表。'],
  );
});

test('没有句末标点的文本(英文摘要)整段保留;原文自带换行的按换行分;空值给空数组', () => {
  const en = 'Meta released Muse Ember. It targets low-cost agent tasks, priced at $0.2 per million tokens.';
  assert.deepEqual(splitSentences(en), [en]);
  assert.deepEqual(splitSentences('第一段的内容在这里写得足够长一些。\n\n第二段。'), ['第一段的内容在这里写得足够长一些。', '第二段。']);
  assert.deepEqual(splitSentences(''), []);
  assert.deepEqual(splitSentences(null), []);
});

test('句末后面的半角闭合符跟着上一句;半角引号只有成对时才算闭合;段首独立的标点不丢', () => {
  const closing = '发布会上他说了一句"这是今年最重要的一次模型更新。"随后团队演示了完整的端到端工作流程。';
  assert.deepEqual(splitSentences(closing), ['发布会上他说了一句"这是今年最重要的一次模型更新。"', '随后团队演示了完整的端到端工作流程。']);
  const paren = '新模型已经对全部付费用户开放(含团队版与企业版。)免费用户要等到下个月才能用上。';
  assert.deepEqual(splitSentences(paren), ['新模型已经对全部付费用户开放(含团队版与企业版。)', '免费用户要等到下个月才能用上。']);
  // 句末后面是下一句的开引号(这一句里没有未闭合的引号):不抢过来
  const opening = '公司公布了新一代模型的全部技术细节。"我们用了三年时间"是负责人的原话,他没有回避延期。';
  assert.equal(splitSentences(opening)[0], '公司公布了新一代模型的全部技术细节。');
  for (const text of [closing, paren, opening, '！开头就是一个感叹号的句子也要完整保留下来。后面还有第二句话跟着它。', '连续的句末标点?!！？都跟着上一句走,不单独成段。然后是下一句的内容。']) {
    assert.equal(splitSentences(text).join(''), text, text.slice(0, 12));
  }
});

