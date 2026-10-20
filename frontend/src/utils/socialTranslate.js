// 推文翻译判定(issue #84):这条推文卡上有没有需要译成中文的文字,决定底栏「✦ 译为中文」出不出现。
// 后端 services/reader_ai.social_text_needs_translation / social_translation_sources 是同一规则,
// 两侧共用 tests/fixtures/social_translate_cases.json 一份用例(test/socialTranslate.test.mjs)。
import { looksChinese } from './readerText.js';

// X 的「无语言内容」判定码:纯链接 / 纯表情 / 纯媒体链接 / 纯提及 / 纯话题等
const NO_LANGUAGE_CODES = new Set(['zxx', 'art', 'qme', 'qam', 'qht', 'qst', 'qct']);
const URL_RE = /https?:\/\/\S+/g;
const LETTER_RE = /\p{L}/u;

// 去掉链接后没有任何字母 → 否;X 判为中文或无语言内容 → 否;其它明确语言码 → 是;
// und / 缺失 → 退回中文启发式(引用推、转推的存量记录没有存 lang)。
export function socialTextNeedsTranslation(text, lang) {
  const stripped = String(text || '').replace(URL_RE, ' ');
  if (!LETTER_RE.test(stripped)) return false;
  const code = String(lang || '').trim().toLowerCase();
  if (code.startsWith('zh') || NO_LANGUAGE_CODES.has(code)) return false;
  if (code && code !== 'und') return true;
  return !looksChinese(stripped);
}

function parseExtensions(article) {
  try {
    const ext = JSON.parse(article?.extensions_json || '{}');
    return ext && typeof ext === 'object' ? ext : {};
  } catch {
    return {};
  }
}

// 需要翻译的两段:{ text, quoted } 各为布尔。转推的正文是原推全文 reposted.text,语言取 reposted.lang、
// 缺失用顶层 lang;引用推语言取 quoted.lang、缺失走启发式。
export function socialTranslationParts(article, ext = parseExtensions(article)) {
  const reposted = ext.reposted && typeof ext.reposted === 'object' ? ext.reposted : null;
  const quoted = ext.quoted && typeof ext.quoted === 'object' ? ext.quoted : null;
  const mainText = reposted ? (reposted.text || article?.content || '') : (article?.content || '');
  const mainLang = reposted ? (reposted.lang || ext.lang) : ext.lang;
  return {
    text: socialTextNeedsTranslation(mainText, mainLang),
    quoted: Boolean(quoted) && socialTextNeedsTranslation(quoted.text, quoted.lang),
  };
}

export function socialTranslatable(article, ext) {
  const parts = socialTranslationParts(article, ext);
  return parts.text || parts.quoted;
}
