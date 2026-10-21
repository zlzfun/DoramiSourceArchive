import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  currentSocialTranslation,
  nextSocialQuotaExpiry,
  purgeExpiredSocialQuota,
  socialQuotaActive,
  socialQuotaUntil,
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
