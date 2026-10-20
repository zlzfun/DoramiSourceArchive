import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  currentSocialTranslation,
  localDayKey,
  purgeExpiredSocialQuota,
  socialTextNeedsTranslation,
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

test('额度用尽跨日失效,当天保留', () => {
  const today = localDayKey(new Date(2026, 9, 21, 10));
  const map = {
    a: { status: 'quota', day: localDayKey(new Date(2026, 9, 20, 23)), sig: 's' },
    b: { status: 'quota', day: today, sig: 's' },
    c: { status: 'done', open: true, sig: 's' },
  };
  const purged = purgeExpiredSocialQuota(map, today);
  assert.deepEqual(Object.keys(purged), ['b', 'c']);
  const unchanged = { b: map.b, c: map.c };
  assert.equal(purgeExpiredSocialQuota(unchanged, today), unchanged);
});
