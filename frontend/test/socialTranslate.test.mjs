import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { socialTextNeedsTranslation, socialTranslatable, socialTranslationParts } from '../src/utils/socialTranslate.js';

// 与后端 tests/test_social_translate.py 共用同一份用例:两侧判定规则漂移即双双失败
const CASES = JSON.parse(readFileSync(new URL('../../tests/fixtures/social_translate_cases.json', import.meta.url), 'utf8'));

test('推文判定与后端共用用例逐条一致', () => {
  for (const [text, lang, expected] of CASES.needs_translation) {
    assert.equal(socialTextNeedsTranslation(text, lang), expected, `${JSON.stringify(text)} / ${lang}`);
  }
});

const article = (content, ext) => ({ content, extensions_json: JSON.stringify(ext) });

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
});
