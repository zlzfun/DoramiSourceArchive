// 推文翻译判定(issue #84):这条推文卡上有没有需要译成中文的文字,决定底栏「✦ 译为中文」出不出现。
// 后端 services/reader_ai.social_text_needs_translation / social_translation_sources 是同一规则,
// 两侧共用 tests/fixtures/social_translate_cases.json 一份用例(test/socialTranslate.test.mjs)。
import { looksChinese } from './readerText.js';

// X 的「无语言内容」判定码:纯链接 / 纯表情 / 纯媒体链接 / 纯提及 / 纯话题等
const NO_LANGUAGE_CODES = new Set(['zxx', 'art', 'qme', 'qam', 'qht', 'qst', 'qct']);
// 链接到第一个空白或尖括号为止(与后端实际翻译时的链接保护同一分隔规则);空白集合与后端逐字同一份(各语言的 \s 不等价,检视 R1-04)
// eslint-disable-next-line no-control-regex -- \x1c-\x1f 是 Python \s 含、JS \s 不含的分隔符,有意列入以对齐后端
const URL_RE = /https?:\/\/[^\t\n\v\f\r \x1c-\x1f\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff<>]+/gu;
const LANG_JUNK_RE = /[^a-z0-9-]/g;
const LETTER_RE = /\p{L}/u;

// 去掉链接后没有任何字母 → 否;X 判为中文或无语言内容 → 否;其它明确语言码 → 是;
// und / 缺失 → 退回中文启发式(引用推、转推的存量记录没有存 lang)。
export function socialTextNeedsTranslation(text, lang) {
  const stripped = String(text || '').replace(URL_RE, ' ');
  if (!LETTER_RE.test(stripped)) return false;
  // 语言码只留 [a-z0-9-](X 给的是 ASCII BCP47);不依赖 trim / strip 各自的空白集合
  const code = String(lang || '').toLowerCase().replace(LANG_JUNK_RE, '');
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

// 需要翻译的原文 { text?, quoted? }(不需要的一侧不出现),与后端 social_translation_sources 同口径:
// 转推的正文是原推全文 reposted.text,语言取 reposted.lang、缺失用顶层 lang;引用推语言取 quoted.lang、缺失走启发式。
export function socialTranslationSources(article, ext = parseExtensions(article)) {
  const reposted = ext.reposted && typeof ext.reposted === 'object' ? ext.reposted : null;
  const quoted = ext.quoted && typeof ext.quoted === 'object' ? ext.quoted : null;
  const mainText = String(reposted ? (reposted.text || article?.content || '') : (article?.content || ''));
  const mainLang = reposted ? (reposted.lang || ext.lang) : ext.lang;
  const sources = {};
  if (socialTextNeedsTranslation(mainText, mainLang)) sources.text = mainText.trim();
  if (quoted && socialTextNeedsTranslation(quoted.text, quoted.lang)) sources.quoted = String(quoted.text || '').trim();
  return sources;
}

export function socialTranslationParts(article, ext) {
  const sources = socialTranslationSources(article, ext);
  return { text: 'text' in sources, quoted: 'quoted' in sources };
}

export function socialTranslatable(article, ext) {
  return Object.keys(socialTranslationSources(article, ext)).length > 0;
}

// 原文签名(检视 R1-02):逐卡状态记下请求时的签名,原文变了(重新加载拿到新正文 / 新引用)旧译文即作废。
// 口径同后端指纹——需要翻译的那几段原文;lang 只经「要不要翻译」影响签名,存量补 lang 不会无谓作废。
export function socialSignatureOf(sources) {
  return sources && Object.keys(sources).length ? JSON.stringify(sources) : '';
}

export function socialTranslationSignature(article, ext) {
  return socialSignatureOf(socialTranslationSources(article, ext));
}

// 当前卡片该用的翻译状态:签名对不上(原文已变)就当没有
export function currentSocialTranslation(entry, signature) {
  return entry && signature && entry.sig === signature ? entry : null;
}

// 额度用尽(429)的恢复时刻(毫秒):按服务端 Retry-After(距服务端下一个零点的秒数)算,不猜服务端时区;
// 缺失时退回本地零点(检视 R1-03 / 复检)
export function socialQuotaUntil(retryAfterSeconds, now = Date.now()) {
  if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds >= 0) return now + retryAfterSeconds * 1000;
  const day = new Date(now);
  return new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1).getTime();
}

export function socialQuotaActive(entry, now = Date.now()) {
  return entry?.status === 'quota' && entry.until > now;
}

// 清掉已到恢复时刻的 quota 条目;没有可清的原样返回同一对象(setState 不触发重渲)
export function purgeExpiredSocialQuota(map, now = Date.now()) {
  let changed = false;
  const next = {};
  for (const [id, entry] of Object.entries(map || {})) {
    if (entry?.status === 'quota' && !socialQuotaActive(entry, now)) {
      changed = true;
      continue;
    }
    next[id] = entry;
  }
  return changed ? next : map;
}

// 最早的恢复时刻,供定时清理;没有 quota 条目返回 null
export function nextSocialQuotaExpiry(map) {
  const times = Object.values(map || {})
    .filter((entry) => entry?.status === 'quota' && Number.isFinite(entry.until))
    .map((entry) => entry.until);
  return times.length ? Math.min(...times) : null;
}
