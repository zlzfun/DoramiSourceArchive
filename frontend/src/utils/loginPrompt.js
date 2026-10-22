/**
 * 游客登录提示(issue #213):需要账号的操作对游客一律走这里,不发请求就弹「登录后即可…」。
 *
 * - 组件里调 `requestLogin(action, { name })`:派发 `dorami-login-required`,App 统一弹 LoginPrompt;
 *   登录后回到哪里不由调用点指定——App 取打开登录页那一刻眼前正在看的那篇,没有就落早报。
 * - 后端兜底:403 `login_required` / 429 `guest_quota_exhausted` 由 api.js 同一事件接住。
 * - 文案:标题说登录后能做什么,一句说明讲为什么要账号;按钮恒为「继续逛逛 | 登录」。
 */

export const LOGIN_REQUIRED_EVENT = 'dorami-login-required';

export const LOGIN_PROMPTS = {
  favorite: { icon: 'star', title: '登录后即可收藏', message: '收藏会保存到你的账号。' },
  favlist: { icon: 'star', title: '登录后可查看收藏', message: '收藏会保存到你的账号。' },
  subscribe: {
    icon: 'plus',
    title: '登录后即可订阅「{name}」',
    message: '游客看到的是一组默认来源；登录后按你自己的订阅编排早报与文章。',
  },
  subscribeAll: {
    icon: 'plus',
    title: '登录后即可订阅这些来源',
    message: '游客看到的是一组默认来源；登录后按你自己的订阅编排早报与文章。',
  },
  unsub: {
    icon: 'rss',
    title: '登录后即可调整订阅',
    message: '游客看到的是一组默认来源；登录后按你自己的订阅编排早报与文章。',
  },
  interest: { icon: 'tags', title: '登录后即可设置兴趣', message: '早报会按你的兴趣多挑几篇。' },
  addsrc: { icon: 'plus', title: '登录后即可添加来源', message: '可以添加自己的 RSS 源，或导入 OPML。' },
  feedback: { icon: 'message', title: '登录后即可反馈', message: '回复会出现在你的「反馈与建议」里。' },
  ai: { icon: 'sparkles', title: '游客今日的 AI 试用已用完', message: '登录后继续使用翻译、问答与速读。' },
  generic: { icon: 'login', title: '登录后可用', message: '游客可以随意阅读、试用 AI；这一步需要账号。' },
};

/** 文案查表:未知动作回落通用文案,`{name}` 缺省为「这个来源」。 */
export function loginPromptCopy(action, { name } = {}) {
  const copy = LOGIN_PROMPTS[action] || LOGIN_PROMPTS.generic;
  return {
    icon: copy.icon,
    title: copy.title.replace('{name}', name || '这个来源'),
    message: copy.message,
  };
}

/** 后端错误码 → 提示动作:403 login_required 用通用文案,429 guest_quota_exhausted 用 AI 额度文案。 */
export function actionForErrorCode(code) {
  if (code === 'guest_quota_exhausted') return 'ai';
  if (code === 'login_required') return 'generic';
  return null;
}

export function requestLogin(action, detail = {}) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(LOGIN_REQUIRED_EVENT, { detail: { action, ...detail } }));
}
