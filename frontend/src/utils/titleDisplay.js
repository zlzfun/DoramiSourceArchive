// 标题语言(issue #218):一处决定「这一条现在显示哪个标题」。
// 服务端只在译名的指纹仍对得上当前原题时才给 title_zh,原题恒在 title;前端不再猜语言,
// 只看「默认显示中文标题」偏好与有没有可用译名。个人早报冻结快照里的 title / title_zh 同样走这里(只选不改)。
// 偏好与正文语言无关:正文切到中文不会顺带把标题换成中文,反之亦然。
import { looksChinese } from './readerText.js';

export const NO_TITLE = '（无标题）';

/** 可用的中文标题:非空且与原题不同;否则空串(待译 / 原题本就是中文 / 无译名)。 */
export function chineseTitleOf(record) {
  const zh = typeof record?.title_zh === 'string' ? record.title_zh.trim() : '';
  const original = typeof record?.title === 'string' ? record.title.trim() : '';
  return zh && zh !== original ? zh : '';
}

/**
 * 显示用的标题对:title = 主标题;orig = 主标题用了中文译名时附带的原题(阅读页小字),否则空串。
 * 偏好关或暂无译名 → 原题,orig 为空。
 */
export function titleView(record, preferChinese, fallback = NO_TITLE) {
  const original = typeof record?.title === 'string' && record.title.trim() ? record.title : '';
  const zh = preferChinese ? chineseTitleOf(record) : '';
  if (zh) return { title: zh, orig: original };
  return { title: original || fallback, orig: '' };
}

export function displayTitle(record, preferChinese, fallback = NO_TITLE) {
  return titleView(record, preferChinese, fallback).title;
}

/** 正文任务带回的中文标题回填成 title_zh:非空、不同于原题、原题不是中文才算数;否则 null。 */
export function titleZhFromResult(record, resultTitle) {
  const zh = typeof resultTitle === 'string' ? resultTitle.trim() : '';
  const original = typeof record?.title === 'string' ? record.title.trim() : '';
  if (!zh || !original || zh === original) return null;
  return looksChinese(original) ? null : zh;
}

// ── 偏好来源 ──
// 登录账号:服务端偏好(login / session 载荷的 prefer_chinese_titles),缺省视为开,显式 false 才关。
// 游客:只存本机浏览器,永不调偏好接口、永不写游客共享账号。
export const GUEST_TITLE_PREF_KEY = 'dorami-guest-chinese-titles';

export function accountTitlePreference(user) {
  return user?.prefer_chinese_titles !== false;
}

export function readGuestTitlePreference(storage) {
  try {
    return storage?.getItem(GUEST_TITLE_PREF_KEY) !== 'off';
  } catch {
    return true;
  }
}

export function writeGuestTitlePreference(storage, preferChinese) {
  try {
    storage?.setItem(GUEST_TITLE_PREF_KEY, preferChinese ? 'on' : 'off');
  } catch {
    /* 存储不可用:本页生命周期内仍生效 */
  }
}
