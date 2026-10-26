// 标题选择(方案 §3.1):所有标题展示位共用这一个纯函数——列表、详情、上一篇 / 下一篇、手机页。
// 只看「默认显示中文标题」开关与译名是否有效,与正文语言、翻译任务完全无关:
// 正文切回原文不会带走中文标题,标题开关也不会触发正文翻译。译名未就绪时直接回退原标题,
// 不画骨架、不报错、不给标题翻译按钮。
export function displayTitleOf(article, { zhTitle, titleState }) {
  const original = article?.title || '（无标题）';
  const state = titleState || article?.title_state;
  const zh = article?.title_zh;
  if (zhTitle && state === 'ready' && zh && zh !== article.title) {
    return { text: zh, original: article.title, translated: true };
  }
  return { text: original, original: null, translated: false };
}

// 偏好:登录账户在服务端保存、跨设备一致;样页用独立 localStorage 键模拟,不碰现网键
export const ZH_TITLE_KEY = 'dorami-r2-preview:zh-title';

export function readZhTitlePref() {
  try {
    return localStorage.getItem(ZH_TITLE_KEY) !== 'off';
  } catch {
    return true;
  }
}

export function writeZhTitlePref(on) {
  try {
    localStorage.setItem(ZH_TITLE_KEY, on ? 'on' : 'off');
  } catch {
    /* 存储不可用:本页内仍按当前选择显示 */
  }
}
