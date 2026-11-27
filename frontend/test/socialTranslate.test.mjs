import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  currentSocialTranslation,
  nextSocialQuotaExpiry,
  purgeExpiredSocialQuota,
  socialCachedZh,
  socialCardView,
  socialOriginalTexts,
  socialQuotaActive,
  socialQuotaUntil,
  socialTextNeedsTranslation,
  socialToggledEntry,
  socialTranslatable,
  socialTranslationParts,
  socialTranslationSignature,
} from '../src/utils/socialTranslate.js';

// 与后端 tests/test_social_translate.py 共用同一份用例:两侧判定规则漂移即双双失败
const CASES = JSON.parse(readFileSync(new URL('../../tests/fixtures/social_translate_cases.json', import.meta.url), 'utf8'));

test('推文判定与后端共用用例逐条一致', () => {
  for (const [text, lang, expected] of CASES.needs_translation) {
    assert.equal(socialTextNeedsTranslation(text, lang), expected, `${JSON.stringify(text)} / ${JSON.stringify(lang)}`);
  }
});

test('长文本按码点采样,与后端切片同口径', () => {
  for (const { parts, lang, expected } of CASES.generated) {
    const text = parts.map(([piece, count]) => piece.repeat(count)).join('');
    assert.equal(socialTextNeedsTranslation(text, lang), expected, JSON.stringify(parts));
  }
});

const article = (content, ext, id = 'p1') => ({ id, content, extensions_json: JSON.stringify(ext) });

test('转推按原推全文判定,只有链接的转推不出入口', () => {
  assert.deepEqual(
    socialTranslationParts(article('RT @OpenAI: ChatGPT Voice is now in…', { lang: 'en', reposted: { text: 'ChatGPT Voice is now in the desktop app.' } })),
    { text: true, quoted: false },
  );
  assert.equal(
    socialTranslatable(article('RT @trq212: https://t.co/R6exTuF7P8', { lang: 'zxx', reposted: { text: 'https://t.co/R6exTuF7P8' } })),
    false,
  );
});

test('主推只有表情、引用推英文时照样出入口,只译引用推', () => {
  const parts = socialTranslationParts(article('🚀🚀 https://t.co/NlNLasmvAf', {
    lang: 'art', quoted: { text: 'Day 0 on Qoder: Qwen3.8-Max-Preview is live.' },
  }));
  assert.deepEqual(parts, { text: false, quoted: true });
});

test('中文推文和坏数据都不出入口', () => {
  assert.equal(socialTranslatable(article('今天发布了新模型', { lang: 'zh' })), false);
  assert.equal(socialTranslatable({ content: '', extensions_json: 'not json' }), false);
  assert.equal(socialTranslationSignature({ content: '', extensions_json: '{}' }), '');
});

test('原文变了旧译文作废:同 ID 换正文或换引用后签名不同', () => {
  const before = article('it is good now!', { lang: 'en', quoted: { text: 'memory update', lang: 'en' } });
  const sig = socialTranslationSignature(before);
  const entry = { status: 'done', open: true, text: '现在好了!', quoted: '记忆更新', sig };
  assert.equal(currentSocialTranslation(entry, sig), entry);

  const newBody = article('it is great now!', { lang: 'en', quoted: { text: 'memory update', lang: 'en' } });
  const newQuote = article('it is good now!', { lang: 'en', quoted: { text: 'memory update v2', lang: 'en' } });
  assert.equal(currentSocialTranslation(entry, socialTranslationSignature(newBody)), null);
  assert.equal(currentSocialTranslation(entry, socialTranslationSignature(newQuote)), null);
  // 只补 lang、原文不变:签名不变,不无谓作废
  const langFilled = article('it is good now!', { lang: 'en', quoted: { text: 'memory update', lang: 'en' } });
  assert.equal(socialTranslationSignature(langFilled), sig);
  assert.equal(currentSocialTranslation(undefined, sig), null);
});

test('额度用尽按服务端 Retry-After 恢复,不看浏览器日期', () => {
  const now = Date.UTC(2026, 9, 21, 15, 0, 0);
  // 服务端说 1 小时后零点(例如浏览器在东京、服务端在上海):恢复时刻就是 1 小时后,不是浏览器的下一个零点
  assert.equal(socialQuotaUntil(3600, now), now + 3600 * 1000);
  // 零点前发出、零点后才回来的 429:服务端算出的剩余秒数很小,恢复时刻几乎就是现在
  assert.equal(socialQuotaUntil(1, now), now + 1000);
  // 缺 Retry-After 时退回本地零点(在 now 之后、24 小时之内)
  const fallback = socialQuotaUntil(undefined, now);
  assert.ok(fallback > now && fallback <= now + 24 * 3600 * 1000);
});

test('到恢复时刻清掉 quota,未到的保留,并给出最早恢复时刻', () => {
  const now = 1_000_000;
  const map = {
    a: { status: 'quota', until: now - 1, sig: 's' },
    b: { status: 'quota', until: now + 60_000, sig: 's' },
    c: { status: 'done', open: true, sig: 's' },
  };
  assert.equal(socialQuotaActive(map.a, now), false);
  assert.equal(socialQuotaActive(map.b, now), true);
  const purged = purgeExpiredSocialQuota(map, now);
  assert.deepEqual(Object.keys(purged), ['b', 'c']);
  assert.equal(purgeExpiredSocialQuota(purged, now), purged);
  assert.equal(nextSocialQuotaExpiry(map), now - 1);
  assert.equal(nextSocialQuotaExpiry({ c: map.c }), null);
});

// ── 卡片显示哪种语言(issue #349)──

const ZH = { text: '新模型发布了', quoted: null };
const done = (open, extra = {}) => ({ status: 'done', open, text: '会话里翻出来的译文', quoted: null, ...extra });

test('随列表带回的译文:坏形状、全空都当没有', () => {
  assert.deepEqual(socialCachedZh({ social_zh: { text: ' 译文 ', quoted: null } }), { text: ' 译文 ', quoted: null });
  assert.deepEqual(socialCachedZh({ social_zh: { text: null, quoted: '引用译文' } }), { text: null, quoted: '引用译文' });
  for (const bad of [undefined, null, 'x', {}, { text: '', quoted: '  ' }, { text: 1, quoted: [] }]) {
    assert.equal(socialCachedZh({ social_zh: bad }), null, JSON.stringify(bad));
  }
});

test('有译文:开着偏好直接显示中文,关着显示原文,换语言都不需要 AI', () => {
  assert.deepEqual(
    socialCardView({ cached: ZH, preferChinese: true }),
    { zh: ZH, showZh: true, status: null, action: 'original' },
  );
  assert.deepEqual(
    socialCardView({ cached: ZH, preferChinese: false, aiEnabled: false, translatable: true }),
    { zh: ZH, showZh: false, status: null, action: 'chinese' },
  );
});

test('读者在这张卡上切过,就听这张卡的,不再跟偏好', () => {
  assert.equal(socialCardView({ cached: ZH, entry: done(false), preferChinese: true }).showZh, false);
  assert.equal(socialCardView({ cached: ZH, entry: done(false), preferChinese: true }).action, 'chinese');
  assert.equal(socialCardView({ cached: ZH, entry: done(true), preferChinese: false }).showZh, true);
  // 会话里翻出来的译文优先于随列表带回的
  assert.equal(socialCardView({ cached: ZH, entry: done(true), preferChinese: true }).zh.text, '会话里翻出来的译文');
  // 没有列表译文、只靠手动翻译的:关着偏好也照样显示刚翻出来的中文
  assert.equal(socialCardView({ entry: done(true), preferChinese: false }).showZh, true);
});

test('没有译文:AI 可用且需要翻译才给「译为中文」,否则底栏不出现', () => {
  const none = { zh: null, showZh: false, status: null };
  assert.deepEqual(socialCardView({ aiEnabled: true, translatable: true }), { ...none, action: 'request' });
  assert.deepEqual(socialCardView({ aiEnabled: false, translatable: true }), { ...none, action: null });
  assert.deepEqual(socialCardView({ aiEnabled: true, translatable: false }), { ...none, action: null });
});

test('向 AI 要译文的过渡态:翻译中 / 额度用尽不可再点,失败可重试,额度恢复后回到可点', () => {
  const base = { aiEnabled: true, translatable: true };
  assert.deepEqual(
    socialCardView({ ...base, entry: { status: 'loading' } }),
    { zh: null, showZh: false, status: 'loading', action: null },
  );
  assert.deepEqual(
    socialCardView({ ...base, entry: { status: 'error' } }),
    { zh: null, showZh: false, status: 'error', action: 'request' },
  );
  const active = socialCardView({ ...base, entry: { status: 'quota', until: Date.now() + 60_000 } });
  assert.equal(active.status, 'quota');
  assert.equal(active.action, null);
  const expired = socialCardView({ ...base, entry: { status: 'quota', until: Date.now() - 1 } });
  assert.equal(expired.status, null);
  assert.equal(expired.action, 'request');
  // 请求还在途、列表刷新带回了译文:直接按偏好显示,不再停在过渡态
  assert.equal(socialCardView({ ...base, cached: ZH, entry: { status: 'loading' } }).showZh, true);
});

test('只有引用推需要翻译:主推保持原文,引用推换成中文', () => {
  const view = socialCardView({ cached: { text: null, quoted: '引用推的译文' }, preferChinese: true });
  assert.equal(view.showZh, true);
  assert.equal(view.zh.text, null);
  assert.equal(view.zh.quoted, '引用推的译文');
});

test('搜索:搜的词只在原文里时这张卡默认显示原文,译文里也有就照常显示中文', () => {
  const original = { text: 'We shipped a new agents SDK for Claude.', quoted: '' };
  const zh = { text: '我们发布了新的智能体 SDK,支持 Claude。', quoted: null };
  const base = { cached: zh, preferChinese: true, original };
  assert.equal(socialCardView({ ...base, searchQuery: 'Agents' }).showZh, false);
  assert.equal(socialCardView({ ...base, searchQuery: 'Agents' }).action, 'chinese');
  assert.equal(socialCardView({ ...base, searchQuery: 'claude' }).showZh, true);
  assert.equal(socialCardView({ ...base, searchQuery: '智能体' }).showZh, true);
  assert.equal(socialCardView({ ...base, searchQuery: '   ' }).showZh, true);
  // 读者手动切到中文之后不再被搜索词拉回原文
  assert.equal(socialCardView({ ...base, searchQuery: 'Agents', entry: done(true) }).showZh, true);
  // 主推没有译文(本来就是中文)、只译了引用推:搜到主推里的词不算「只在原文里」
  assert.equal(socialCardView({
    cached: { text: null, quoted: '引用的译文' }, preferChinese: true, searchQuery: '发布',
    original: { text: '今天发布了', quoted: 'the quoted post' },
  }).showZh, true);
});

test('卡片上的两段原文:转推取原推全文,没有引用推给空串', () => {
  assert.deepEqual(
    socialOriginalTexts(article('RT @OpenAI: ChatGPT Voice is now in…', { reposted: { text: 'ChatGPT Voice is now in the desktop app.' } })),
    { text: 'ChatGPT Voice is now in the desktop app.', quoted: '' },
  );
  assert.deepEqual(
    socialOriginalTexts(article(' main ', { quoted: { text: ' quoted ' } })),
    { text: 'main', quoted: 'quoted' },
  );
});

test('换语言的开关只认调用方给的视图:同一条推文在两个列表里默认语言不同,各自一点就翻', () => {
  const original = { text: 'We shipped a new agents SDK.', quoted: '' };
  const zh = { text: '我们发布了新的智能体 SDK。', quoted: null };
  // 搜索结果里这张卡显示原文,订阅推入页(没有搜索)里显示中文
  const inSearch = socialCardView({ cached: zh, preferChinese: true, original, searchQuery: 'agents' });
  const inFlow = socialCardView({ cached: zh, preferChinese: true, original, searchQuery: '' });
  assert.equal(inSearch.showZh, false);
  assert.equal(inFlow.showZh, true);
  // 各自点一下:搜索结果里换成中文,推入页里换成原文——开关不替调用方重新推导
  assert.deepEqual(socialToggledEntry(inSearch, 'sig'), { status: 'done', open: true, sig: 'sig', text: zh.text, quoted: null });
  assert.deepEqual(socialToggledEntry(inFlow, 'sig'), { status: 'done', open: false, sig: 'sig', text: zh.text, quoted: null });
  // 点过之后两处都听这张卡的
  const entry = socialToggledEntry(inFlow, 'sig');
  assert.equal(socialCardView({ cached: zh, entry, preferChinese: true, original, searchQuery: 'agents' }).showZh, false);
  assert.equal(socialCardView({ cached: zh, entry, preferChinese: true, original, searchQuery: '' }).showZh, false);
  // 没有译文的视图不产生本地条目(要走请求)
  assert.equal(socialToggledEntry(socialCardView({ aiEnabled: true, translatable: true }), 'sig'), null);
  assert.equal(socialToggledEntry(null, 'sig'), null);
});
