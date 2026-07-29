/**
 * 分享链接拼装（两档分享的 URL 单一事实来源）
 *
 * · 站内深链 `#/reader/a/{articleId}` —— 收到的人登录后直达该篇。零外泄、不落库，
 *   适合发给同事内部传阅。
 * · 公开链接 `#/s/{token}` —— 免登录只读页，令牌由后端签发（见 services/article_share.py）。
 *
 * 两者都用 hash 段而非 path 段：前端是纯静态托管 + hash 路由，用 path 会在刷新时
 * 打到 nginx 的 404 而不是回到 index.html。
 */

function origin() {
  if (typeof window === 'undefined') return '';
  return `${window.location.origin}${window.location.pathname}`.replace(/\/+$/, '');
}

/** 站内深链：需登录，落到阅读器的该篇文章。 */
export function articleDeepLink(articleId) {
  if (!articleId) return '';
  return `${origin()}/#/reader/a/${encodeURIComponent(articleId)}`;
}

/** 公开链接：免登录只读页。 */
export function publicShareLink(token) {
  if (!token) return '';
  return `${origin()}/#/s/${encodeURIComponent(token)}`;
}

/** 从 hash 解析公开分享令牌；不是分享页则返回 ''（登录门前调用，见 App.jsx）。 */
export function shareTokenFromHash(hash) {
  const match = /^#\/s\/([^/?#]+)/.exec(String(hash || ''));
  return match ? decodeURIComponent(match[1]) : '';
}

/** 从 hash 解析站内深链的文章 ID；不是深链则返回 ''。 */
export function deepLinkArticleId(hash) {
  const match = /^#\/reader\/a\/([^/?#]+)/.exec(String(hash || ''));
  return match ? decodeURIComponent(match[1]) : '';
}

/** 有效期档位：与后端 SHARE_EXPIRY_CHOICES 对齐（null = 永久）。 */
export const SHARE_EXPIRY_OPTIONS = [
  { days: 7, label: '7 天' },
  { days: 30, label: '30 天' },
  { days: null, label: '永久' },
];

/** 剩余有效期人话；已撤销/已过期各有独立措辞，避免读者以为链接还能用。 */
export function shareStatusText(share) {
  if (!share) return '';
  if (share.revoked_at) return '已撤销';
  if (!share.expires_at) return '永久有效';
  const remain = new Date(share.expires_at).getTime() - Date.now();
  if (Number.isNaN(remain)) return '有效期未知';
  if (remain <= 0) return '已过期';
  const days = Math.ceil(remain / 86400000);
  return days > 1 ? `${days} 天后过期` : '不到 1 天后过期';
}
