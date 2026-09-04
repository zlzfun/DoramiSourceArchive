import { request, qs } from './request';

const enc = encodeURIComponent;
export const PAGE_SIZE = 30;

// ---- 认证 / 运行时 ----
export function login(username, password) {
  return request('/auth/login', { method: 'POST', body: { username, password, return_token: true }, errorMsg: '登录失败' });
}
export function fetchSession() { return request('/auth/session', { errorMsg: '获取会话失败' }); }
export function fetchRuntime() { return request('/runtime', { errorMsg: '获取运行信息失败' }); }
export function logout() { return request('/auth/logout', { method: 'POST', errorMsg: '退出失败' }); }
export function changePassword(currentPassword, newPassword) {
  return request('/auth/change-password', {
    method: 'POST', body: { current_password: currentPassword, new_password: newPassword }, errorMsg: '修改密码失败',
  });
}

// ---- 文章列表 / 正文 ----
export function fetchArticles(filters = {}, limit = PAGE_SIZE, skip = 0) {
  return request(`/articles${qs({ ...filters, limit, skip, include_total: true, include_content: false })}`, { errorMsg: '获取文章列表失败' });
}
export function fetchSocialArticles(filters = {}, limit = PAGE_SIZE, skip = 0) {
  // 社交流全文直出且需 extensions(引用推/转推/图链)——只在 include_content=true 时随列表返回
  return request(`/articles${qs({ ...filters, limit, skip, include_total: true, include_content: true })}`, { errorMsg: '获取动态失败' });
}
export function renderArticle(id) {
  return request(`/reader/articles/${enc(id)}/render`, { errorMsg: '获取正文失败' });
}
export function recordArticleRead(id) {
  return request(`/reader/articles/${enc(id)}/read`, { method: 'POST', errorMsg: '' }).catch(() => null);
}
export function markArticleRead(id) { return request(`/reader/articles/${enc(id)}/mark-read`, { method: 'POST', errorMsg: '标记失败' }); }
export function markArticleUnread(id) { return request(`/reader/articles/${enc(id)}/mark-unread`, { method: 'POST', errorMsg: '标记失败' }); }
export function fetchUnreadCounts() { return request('/reader/unread-counts', { errorMsg: '获取未读失败' }); }
export function markAllRead(sourceId = null, shape = null) {
  const path = sourceId
    ? `/reader/sources/${enc(sourceId)}/mark-all-read`
    : `/reader/mark-all-read${shape ? `?shape=${enc(shape)}` : ''}`;
  return request(path, { method: 'POST', errorMsg: '标记全部已读失败' });
}

// ---- 收藏 ----
export function fetchFavorites(filters = {}, limit = PAGE_SIZE, skip = 0, includeContent = false) {
  return request(`/reader/favorites${qs({ ...filters, limit, skip, include_content: includeContent })}`, { errorMsg: '获取收藏失败' });
}
export function addFavorite(id) { return request(`/reader/favorites/${enc(id)}`, { method: 'POST', errorMsg: '收藏失败' }); }
export function removeFavorite(id) { return request(`/reader/favorites/${enc(id)}`, { method: 'DELETE', errorMsg: '取消收藏失败' }); }

// ---- 源目录 / 订阅 ----
export function fetchReaderSources() { return request('/reader/sources', { errorMsg: '获取来源失败' }); }
export function subscribeSource(id) { return request(`/reader/sources/${enc(id)}/subscribe`, { method: 'POST', errorMsg: '订阅失败' }); }
export function unsubscribeSource(id) { return request(`/reader/sources/${enc(id)}/subscribe`, { method: 'DELETE', errorMsg: '退订失败' }); }
export function fetchCollections() { return request('/reader/collections', { errorMsg: '获取合集失败' }); }
export function subscribeCollection(id) { return request(`/reader/collections/${enc(id)}/subscribe`, { method: 'POST', errorMsg: '订阅合集失败' }); }
export function unsubscribeCollection(id) { return request(`/reader/collections/${enc(id)}/subscribe`, { method: 'DELETE', errorMsg: '退订合集失败' }); }

// ---- 公告 / 反馈 ----
export function fetchAnnouncements() { return request('/reader/announcements', { errorMsg: '获取公告失败' }); }
export function dismissAnnouncement(id) { return request(`/reader/announcements/${enc(id)}/dismiss`, { method: 'POST', errorMsg: '' }).catch(() => null); }
export function submitFeedback(category, content) {
  return request('/reader/feedback', { method: 'POST', body: { category, content }, errorMsg: '提交反馈失败' });
}
export function fetchMyFeedback() { return request('/reader/feedback', { errorMsg: '获取反馈失败' }); }

// ---- 个人早报(P2) ----
export function fetchTodayPersonalBrief() { return request('/reader/briefs/today', { errorMsg: '获取早报失败' }); }
export function ensurePersonalBrief() { return request('/reader/briefs/today/ensure', { method: 'POST', errorMsg: '生成早报失败' }); }
