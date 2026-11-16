// 标题按词断行(issue #279 第五批):合并规则是纯函数,用固定的片段序列测;titleChunks 走运行环境自带的分词器,只测不变量。
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeSegments, needsGap, titleChunks } from '../src/utils/wordBreak.js';
import { punctParts } from '../src/utils/readerText.js';

const merge = (text) => mergeSegments(text.split('/'));

test('连续的单字并成一块;落单的单字接到前一块后面', () => {
  assert.deepEqual(merge('谷/歌/搜索/借助/Gemini'), ['谷歌', '搜索', '借助', 'Gemini']);
  assert.deepEqual(merge('英伟/达/可能/得/布局'), ['英伟达', '可能得', '布局']);
  assert.deepEqual(merge('黄/仁/勋/辞任/CEO'), ['黄仁勋', '辞任', 'CEO']);
  assert.deepEqual(merge('智能/体/工作/流'), ['智能体', '工作流']);
});

test('前缀与虚词带着后一段走;句首与标点后面落单的字也往后靠', () => {
  assert.deepEqual(merge('超/740/亿/美元'), ['超740亿', '美元']);
  assert.deepEqual(merge('团队/与/公司/合作'), ['团队', '与公司', '合作']);
  assert.deepEqual(merge('泄露/！/马/斯克/回应'), ['泄露！', '马斯克', '回应']);
  assert.deepEqual(merge('他/似乎/很/难/全身而退'), ['他似乎', '很难', '全身而退']);
});

test('收尾标点跟前一块,起首标点带着后一段;很长的一串单字不合并', () => {
  assert.deepEqual(merge('全球/解禁/！/再现/「/碳/」/时刻'), ['全球', '解禁！', '再现', '「碳」', '时刻']);
  assert.deepEqual(merge('发布/：/安全/研究'), ['发布：', '安全', '研究']);
  const long = merge('防止/在/通/话/者/话/未/说/完/时/打断');
  assert.ok(long.every((chunk) => chunk.length <= 3), long.join('|'));
  // 长串的头一个字多半是前一个词的后缀
  assert.equal(merge('百万/智能/体/组/团/上/岗')[1], '智能体');
});

test('空白跟着前一块;合并只移动边界,不增不减一个字', () => {
  for (const text of ['谷/歌/ /Gemini/ /4/ /发布/仅/四周/，/得分/便/超越', 'AI/:/AM/：/竞技/场/中/的/碳/、/Mistral', '「/源/神/」/！/MiniMax/开/源']) {
    assert.equal(merge(text).join(''), text.split('/').join(''), text);
  }
});

test('titleChunks:西文与空值不处理;中文标题拼回去与原文逐字相等', () => {
  assert.equal(titleChunks('Claude Meetings lets Anthropic join your calls'), null);
  assert.equal(titleChunks(''), null);
  for (const title of ['谷歌发布 Gemini 4 Hydrogen 模型：主打日常编程与智能体工作流', 'Fable 5.2深夜掀桌！从0造操作系统，性价比反超', '百度押中AI下一幕！百万智能体组团上岗，AI科学家昼夜狂奔']) {
    const chunks = titleChunks(title);
    if (chunks) assert.equal(chunks.join(''), title);   // 运行环境没有分词器时是 null
  }
});

test('块边界上汉字贴着字母数字才补间隙;分块后只有最后一块保留句末标点不收', () => {
  assert.equal(needsGap('押中', 'AI'), true);
  assert.equal(needsGap('AI', '下一幕'), true);
  assert.equal(needsGap('借助 ', 'Gemini'), false);
  assert.equal(needsGap('搜索', '借助'), false);
  assert.deepEqual(punctParts('解禁！', { keepTail: false }), [{ text: '解禁', kind: '' }, { text: '！', kind: 'close' }]);
  assert.deepEqual(punctParts('解禁！'), [{ text: '解禁！', kind: '' }]);
});

test('检视返修:开括号带着后面的内容;连续起首标点并在一块;连接符与前置符号不产生可折点', () => {
  assert.deepEqual(merge('发布/新/模型/(/GPT/-/5/)/加速/落地'), ['发布', '新模型', '(GPT-5)', '加速', '落地']);
  assert.deepEqual(merge('模型/（/「/超级/智能/」/）/正式/上线'), ['模型', '（「超级', '智能」）', '正式', '上线']);
  assert.deepEqual(merge('宝座/，/$/ANT/ /IPO/ /路/演/启动'), ['宝座，', '$ANT ', 'IPO ', '路演', '启动']);
  assert.deepEqual(merge('关注/#/人工/智能/话题'), ['关注', '#人工', '智能', '话题']);
  assert.deepEqual(merge('A/./I/./ /公司/发布'), ['A.I. ', '公司', '发布']);
  // 有空格隔开的不粘
  assert.deepEqual(merge('AI/:/ /AM/节目'), ['AI: ', 'AM', '节目']);
});


test('第五批补:一串单字紧跟在词后面、头一个是名词后缀时归前一块;落单的「谈」带着后一段走', () => {
  assert.deepEqual(merge('优秀/的/工程/师/要/爱上/问题'), ['优秀的', '工程师', '要爱上', '问题']);
  assert.deepEqual(merge('百万/智能/体/组/团/上线'), ['百万', '智能体', '组团', '上线']);
  assert.deepEqual(merge('准确/率/直/升'), ['准确率', '直升']);
  // 两个单字、头一个是后缀:整串并进前一块(少一个可折点,不会切错);第二个字是前缀、虚词时照旧带着后一段走
  assert.deepEqual(merge('安全/机/甲/上线'), ['安全机甲', '上线']);
  assert.deepEqual(merge('防御/方/也/需要'), ['防御方', '也需要']);
  // 检视返修:拿不准的后缀(官、力、端……)后面还有两个字以上时,整串并进前一块,不在它后面新增可折点——「官宣」不能被拆成「官 | 宣」
  assert.deepEqual(merge('苹果/官/宣/新/产品'), ['苹果官宣新', '产品']);
  assert.deepEqual(merge('微软/官/宣/新/计划'), ['微软官宣新', '计划']);
  assert.deepEqual(merge('技术/官/马/特/表示'), ['技术官马特', '表示']);
  for (const text of ['苹果/官/宣/新/产品', '微软/官/宣/新/计划', '发布/力/推/新/品']) {
    const parts = text.split('/');
    const chunks = merge(text);
    assert.equal(chunks.join(''), parts.join(''));
    assert.ok(!chunks.some((chunk) => chunk.endsWith(parts[1]) && chunk.length > 1 && chunks[chunks.indexOf(chunk) + 1]?.startsWith(parts[2])), `${chunks.join(' | ')}:后缀后面不该有可折点`);
  }
  // 句首、标点后面、西文后面的一串单字不算后缀
  assert.deepEqual(merge('体/验/全面/升级'), ['体验', '全面', '升级']);
  assert.deepEqual(merge('发布/，/机/构/回应'), ['发布，', '机构', '回应']);
  assert.deepEqual(merge('OpenAI/官/宣/新品'), ['OpenAI', '官宣', '新品']);
  // 头一个不是后缀:照旧并成一块
  assert.deepEqual(merge('发布/谷/歌/回应'), ['发布', '谷歌', '回应']);
  assert.deepEqual(merge('Tigges/ /谈/智能/体/可/解释'), ['Tigges ', '谈智能体', '可解释']);
});
