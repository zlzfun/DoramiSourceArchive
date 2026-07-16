import { API_BASE_URL } from './config';

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options,
  });
  const requestUrl = typeof url === 'string' ? url : '';
  if (response.status === 401 && !requestUrl.includes('/auth/') && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('dorami-auth-expired'));
  }
  return response;
}

async function handleApiError(response, defaultMsg) {
  let msg = defaultMsg;
  try {
    const data = await response.json();
    if (data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
  } catch { /* use defaultMsg */ }
  throw new Error(msg);
}

// 统一 JSON 请求封装：收敛遍布各接口的 `apiFetch → if(!ok) handleApiError → res.json()` 样板。
// - body 有值时自动带 Content-Type + JSON 序列化（GET 无 body 则不加头）。
// - 其余 fetch 选项（如 AbortController 的 signal）经 ...opts 透传。
// - path 需含查询串；非 JSON 响应（text/ndjson）、fire-and-forget、失败静默返回默认值的
//   接口不走本封装，见文件末尾各定制实现。
async function request(path, { method = 'GET', body, errorMsg, ...opts } = {}) {
  const res = await apiFetch(`${API_BASE_URL}${path}`, {
    method,
    ...(body !== undefined && {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    ...opts,
  });
  if (!res.ok) await handleApiError(res, errorMsg);
  return res.json();
}

// 把 filters 对象里的非空项追加到 URLSearchParams（空串/null/undefined 跳过）。
function withFilters(params, filters = {}) {
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  return params;
}

const enc = encodeURIComponent;

// ==================== 认证 ====================
export function loginAdmin(username, password) {
  return request('/auth/login', { method: 'POST', body: { username, password }, errorMsg: '登录失败' });
}

export async function fetchAuthSession() {
  // 未登录是常态，不抛错：!ok 时返回匿名会话形状。
  const res = await apiFetch(`${API_BASE_URL}/auth/session`);
  if (!res.ok) return { authenticated: false, user: null };
  return res.json();
}

export function fetchRuntimeInfo() {
  return request('/runtime', { errorMsg: '获取运行角色失败' });
}

export function logoutAdmin() {
  return request('/auth/logout', { method: 'POST', errorMsg: '退出登录失败' });
}

export function changeOwnPassword(currentPassword, newPassword) {
  return request('/auth/change-password', {
    method: 'POST',
    body: { current_password: currentPassword, new_password: newPassword },
    errorMsg: '修改密码失败',
  });
}

export function updateAvatar(avatar) {
  return request('/auth/avatar', { method: 'POST', body: { avatar }, errorMsg: '更新头像失败' });
}

// ==================== 账户管理（仅管理员） ====================
export function fetchAccounts() {
  return request('/accounts', { errorMsg: '获取账户列表失败' });
}

export function createAccount(payload) {
  return request('/accounts', { method: 'POST', body: payload, errorMsg: '创建账户失败' });
}

export function updateAccount(username, payload) {
  return request(`/accounts/${enc(username)}`, { method: 'PUT', body: payload, errorMsg: '更新账户失败' });
}

export function resetAccountPassword(username, newPassword) {
  return request(`/accounts/${enc(username)}/reset-password`, {
    method: 'POST',
    body: { new_password: newPassword },
    errorMsg: '重置密码失败',
  });
}

export function deleteAccount(username) {
  return request(`/accounts/${enc(username)}`, { method: 'DELETE', errorMsg: '删除账户失败' });
}

// ==================== 运维管理（仅管理员） ====================
export function fetchAdminOverview() {
  return request('/admin/overview', { errorMsg: '获取运维概览失败' });
}

export function fetchAdminAccounts(days = 30) {
  return request(`/admin/accounts?days=${enc(days)}`, { errorMsg: '获取账户列表失败' });
}

export function fetchAccountActivity(username, days = 30) {
  return request(`/admin/accounts/${enc(username)}/activity?days=${enc(days)}`, { errorMsg: '获取用户活动详情失败' });
}

export function fetchAiUsage(days = 30) {
  return request(`/admin/ai-usage?days=${enc(days)}`, { errorMsg: '获取 AI 用量失败' });
}

export function fetchAdminContent(top = 12) {
  return request(`/admin/content?top=${enc(top)}`, { errorMsg: '获取内容看板失败' });
}

export function getAiBetaGlobal() {
  return request('/admin/ai-beta/global', { errorMsg: '获取 AI 全局开关失败' });
}

export function setAiBetaGlobal(enabled) {
  return request('/admin/ai-beta/global', { method: 'POST', body: { enabled }, errorMsg: '更新 AI 全局开关失败' });
}

// ── 阅读器 AI（用户面：翻译 / 问答） ──
export function translateArticle(articleId) {
  return request('/reader/ai/translate', { method: 'POST', body: { article_id: articleId }, errorMsg: '翻译失败，请稍后重试' });
}

// 生成/读取文章的中文要点摘要(后端缓存于 extensions_json.summary_zh,幂等)
export function summarizeArticle(articleId) {
  return request('/reader/ai/summarize', { method: 'POST', body: { article_id: articleId }, errorMsg: '摘要生成失败，请稍后重试' });
}

export function askReaderAi({ question, scope = 'article', articleId = null, history = [] }) {
  return request('/reader/ai/ask', {
    method: 'POST',
    body: { question, scope, article_id: articleId, history },
    errorMsg: '提问失败，请稍后重试',
  });
}

// ==================== 抓取器 / 数据源健康 ====================
export function fetchFetchers() {
  return request('/fetchers', { errorMsg: '获取抓取器注册表失败' });
}

export function fetchSourceHealth() {
  return request('/source-health', { errorMsg: '获取数据源健康状态失败' });
}

// ==================== 文章 ====================
export function fetchArticles(filters = {}, limit = 100, skip = 0, includeTotal = false, options = {}) {
  const { includeContent, ...fetchOptions } = options;
  const params = new URLSearchParams({ limit, skip });
  if (includeTotal) params.append('include_total', 'true');
  if (includeContent !== undefined) params.append('include_content', includeContent ? 'true' : 'false');
  withFilters(params, filters);
  return request(`/articles?${params}`, { ...fetchOptions, errorMsg: '获取文章列表失败' });
}

export function fetchArticle(id, options = {}) {
  return request(`/articles/${enc(id)}`, { ...options, errorMsg: '获取文章详情失败' });
}

// 分面目录：content_type / source_id 的全量 group-by 计数（{total, content_types, source_ids}，计数降序）。
// 台账分面栏的单一数据源——选项来自全量归档而非当前页。
export function fetchArticleFacets(filters = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  return request(`/articles/facets${query ? `?${query}` : ''}`, { errorMsg: '获取分面统计失败' });
}

export function deleteArticle(id) {
  return request(`/articles/${enc(id)}`, { method: 'DELETE', errorMsg: '删除失败' });
}

export function batchDeleteArticles(ids) {
  return request('/articles/batch-delete', { method: 'POST', body: { ids }, errorMsg: '批量删除失败' });
}

export function updateArticle(id, data) {
  return request(`/articles/${enc(id)}`, { method: 'PUT', body: data, errorMsg: '更新失败' });
}

export function createArticle(payload) {
  return request('/articles', { method: 'POST', body: payload, errorMsg: '录入失败' });
}

// ==================== 向量化（单篇 / 批量） ====================
export function vectorizeArticle(id) {
  return request(`/vectorize/${enc(id)}`, { method: 'POST', errorMsg: '向量化失败' });
}

export function batchVectorizeArticles(ids) {
  return request('/vectorize/batch', { method: 'POST', body: { ids }, errorMsg: '批量向量化失败' });
}

function runQuery(options = {}) {
  const params = new URLSearchParams();
  if (options.testLimit !== undefined && options.testLimit !== null) {
    params.append('test_limit', options.testLimit);
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

export function triggerFetch(fetcherId, params, options = {}) {
  return request(`/fetch/${fetcherId}${runQuery(options)}`, { method: 'POST', body: params, errorMsg: `[${fetcherId}] 抓取失败` });
}

export async function triggerBatchFetch(items, options = {}) {
  // 批量抓取已改为后台任务：提交拿 job_id，轮询 /api/jobs/{id} 取聚合结果
  //（字段与旧同步接口一致，调用方语义不变）。细粒度进度仍由调用方轮询
  // /api/fetch-runs/running-progress 驱动，与此互补。
  const { job_id: jobId } = await request(`/fetch/batch${runQuery(options)}`, { method: 'POST', body: { items }, errorMsg: '批量抓取失败' });
  return pollJob(jobId, { defaultError: '批量抓取失败' });
}

export async function fetchRunningProgress() {
  // 进度轮询：!ok 时静默返回空对象，不打断轮询循环。
  const res = await apiFetch(`${API_BASE_URL}/fetch-runs/running-progress`);
  if (!res.ok) return {};
  return res.json();
}

export function fetchFetchRuns(filters = {}, limit = 100) {
  const params = withFilters(new URLSearchParams({ limit }), filters);
  return request(`/fetch-runs?${params}`, { errorMsg: '获取抓取运行历史失败' });
}

// 每日聚合统计(A 波):runs 按 day×job×scope 状态分列,articles 按 day×source 计数。
// 运行页点阵/总账条精确化、台账 7 日趋势、节点行收录 mini 柱共用。
export function fetchDailyStats(days = 30) {
  return request(`/stats/daily?days=${days}`, { errorMsg: '获取每日统计失败' });
}

// ==================== 采集任务（Collection Jobs） ====================
// （采集范围 node-groups 与旧版定时任务 /api/tasks 已退役——实体简化阶段 2，
// 存量数据由后端 Alembic 迁移内联/转换为采集任务。）
export function fetchCollectionJobs(filters = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  return request(`/collection-jobs${query ? `?${query}` : ''}`, { errorMsg: '获取采集任务失败' });
}

export function createCollectionJob(data) {
  return request('/collection-jobs', { method: 'POST', body: data, errorMsg: '创建采集任务失败' });
}

export function updateCollectionJob(id, data) {
  return request(`/collection-jobs/${id}`, { method: 'PUT', body: data, errorMsg: '更新采集任务失败' });
}

export function deleteCollectionJob(id) {
  return request(`/collection-jobs/${id}`, { method: 'DELETE', errorMsg: '删除采集任务失败' });
}

export async function runCollectionJob(id, options = {}) {
  // 采集任务运行已改为后台任务：提交拿 job_id，轮询 /api/jobs/{id} 取聚合结果。
  // 细粒度进度仍由调用方轮询 /api/fetch-runs/running-progress 驱动，与此互补。
  const { job_id: jobId } = await request(`/collection-jobs/${id}/run${runQuery(options)}`, { method: 'POST', errorMsg: '触发采集任务失败' });
  return pollJob(jobId, { defaultError: '触发采集任务失败' });
}

export function fetchCollectionJobRuns(filters = {}, limit = 100) {
  const params = withFilters(new URLSearchParams({ limit }), filters);
  return request(`/collection-job-runs?${params}`, { errorMsg: '获取采集运行历史失败' });
}

// ==================== 数据源配置（Source Configs） ====================
export function fetchSourceConfigs(filters = {}, limit = 100) {
  const params = withFilters(new URLSearchParams({ limit }), filters);
  return request(`/source-configs?${params}`, { errorMsg: '获取数据源配置失败' });
}

export function createSourceConfig(data) {
  return request('/source-configs', { method: 'POST', body: data, errorMsg: '创建数据源失败' });
}

export function updateSourceConfig(sourceId, data) {
  return request(`/source-configs/${enc(sourceId)}`, { method: 'PUT', body: data, errorMsg: '更新数据源失败' });
}

export function toggleSourceConfig(sourceId, isActive) {
  return request(`/source-configs/${enc(sourceId)}/toggle`, { method: 'POST', body: { is_active: isActive }, errorMsg: '切换数据源状态失败' });
}

export function deleteSourceConfig(sourceId) {
  return request(`/source-configs/${enc(sourceId)}`, { method: 'DELETE', errorMsg: '删除数据源失败' });
}

export function fetchSourceConfigNow(sourceId, params = {}) {
  return request(`/source-configs/${enc(sourceId)}/fetch`, { method: 'POST', body: { params }, errorMsg: '触发数据源抓取失败' });
}

export async function fetchActiveRssSources(params = {}) {
  // 后台任务化：提交拿 job_id，轮询 /api/jobs/{id} 取聚合结果。
  const { job_id: jobId } = await request('/source-configs/fetch-active-rss', { method: 'POST', body: { params }, errorMsg: '批量触发 RSS 抓取失败' });
  return pollJob(jobId, { defaultError: '批量触发 RSS 抓取失败' });
}

// ===== AI 自定义节点（URL → 分析 → 预览 → 固化）=====
export function analyzeSourceUrl(url) {
  return request('/source-builder/analyze', { method: 'POST', body: { url }, errorMsg: '分析 URL 失败' });
}

export function previewSourceConfig(config) {
  return request('/source-builder/preview', { method: 'POST', body: config, errorMsg: '试抓预览失败' });
}

// ==================== 向量 / RAG 检索 ====================
export function fetchVectorStats() {
  return request('/vector/stats', { errorMsg: '获取向量统计失败' });
}

export function vectorSearch(query, topK = 5, options = {}) {
  return request('/vector/search', { method: 'POST', body: { query, top_k: topK, ...options }, errorMsg: '检索失败' });
}

export function ragContext(query, topK = 5, options = {}) {
  return request('/rag/context', { method: 'POST', body: { query, top_k: topK, ...options }, errorMsg: 'RAG 上下文检索失败' });
}

export function ragSimilar(articleId, topK = 5) {
  return request(`/rag/similar/${enc(articleId)}?top_k=${topK}`, { errorMsg: '相似文章检索失败' });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// 轮询后台任务直到终态；成功时 resolve 其 result（字段与旧同步接口一致），
// 失败/超时抛错。让调用方（组件）的 await + success(data) 逻辑保持不变。
async function pollJob(jobId, { intervalMs = 1500, timeoutMs = 60 * 60 * 1000, defaultError } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await apiFetch(`${API_BASE_URL}/jobs/${jobId}`);
    if (!res.ok) await handleApiError(res, defaultError || '任务状态查询失败');
    const job = await res.json();
    if (job.status === 'succeeded') return job.result || {};
    if (job.status === 'failed') throw new Error(job.error || defaultError || '任务执行失败');
    await sleep(intervalMs);
  }
  throw new Error('任务超时，请稍后在任务列表查看结果');
}

export async function vectorizeAllPending() {
  const { job_id: jobId } = await request('/vectorize/all-pending', { method: 'POST', errorMsg: '全量向量化失败' });
  return pollJob(jobId, { defaultError: '全量向量化失败' });
}

export async function reindexAll() {
  const { job_id: jobId } = await request('/vector/reindex-all', { method: 'POST', errorMsg: '全量重索引失败' });
  return pollJob(jobId, { defaultError: '全量重索引失败' });
}

export function fetchBackgroundJob(jobId) {
  return request(`/jobs/${jobId}`, { errorMsg: '任务状态查询失败' });
}

export function fetchSubscribedVectorStats() {
  return request('/vector/subscribed-stats', { errorMsg: '获取订阅向量统计失败' });
}

export function getAutoVectorize() {
  return request('/vector/auto-vectorize', { errorMsg: '获取自动向量化配置失败' });
}

export function setAutoVectorize(enabled) {
  return request('/vector/auto-vectorize', { method: 'POST', body: { enabled }, errorMsg: '设置自动向量化失败' });
}

// ==================== 大模型配置 & 每日日报 ====================
export function getLLMConfig() {
  return request('/llm/config', { errorMsg: '获取大模型配置失败' });
}

export function saveLLMConfig(payload) {
  return request('/llm/config', { method: 'POST', body: payload, errorMsg: '保存大模型配置失败' });
}

export function testLLMConfig() {
  return request('/llm/config/test', { method: 'POST', errorMsg: '大模型连接测试失败' });
}

export function getDailyBriefConfig() {
  return request('/daily-brief/config', { errorMsg: '获取日报配置失败' });
}

export function saveDailyBriefConfig(payload) {
  return request('/daily-brief/config', { method: 'POST', body: payload, errorMsg: '保存日报配置失败' });
}

export function getDailyBriefPipeline() {
  return request('/daily-brief/pipeline', { errorMsg: '获取日报生成管线失败' });
}

export function getDailyBriefProgress() {
  return request('/daily-brief/progress', { errorMsg: '获取日报进度失败' });
}

export async function generateDailyBrief(payload = {}) {
  // 生成已改为后台任务：提交拿 job_id，再轮询 /api/jobs/{id} 取最终结果（result）。
  // 细粒度阶段动画仍由调用方轮询 /api/daily-brief/progress 驱动，与此互补。
  const { job_id: jobId } = await request('/daily-brief/generate', { method: 'POST', body: payload, errorMsg: '生成日报失败' });
  return pollJob(jobId, { defaultError: '生成日报失败' });
}

// ==================== 归档同步（导出/导入，非 JSON 响应） ====================
export async function exportArchiveArticles(filters = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  const res = await apiFetch(`${API_BASE_URL}/archive/export/articles.jsonl${query ? `?${query}` : ''}`);
  if (!res.ok) await handleApiError(res, '导出归档包失败');
  return res.text();
}

export async function importArchiveArticlesJsonl(rawText) {
  const res = await apiFetch(`${API_BASE_URL}/archive/import/articles.jsonl`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8' },
    body: rawText,
  });
  if (!res.ok) await handleApiError(res, '导入归档包失败');
  return res.json();
}

// ==================== MCP（无 ok 校验，容忍 502/未就绪） ====================
export const fetchMcpStatus = () =>
  apiFetch(`${API_BASE_URL}/mcp/status`).then(r => r.json());

export const toggleMcp = () =>
  apiFetch(`${API_BASE_URL}/mcp/toggle`, { method: 'POST' }).then(r => r.json());

// ==================== 订阅 / 阅读器 ====================
export function fetchSubscriptions(filters = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  return request(`/subscriptions${query ? `?${query}` : ''}`, { errorMsg: '获取订阅源失败' });
}

export function fetchReaderSources() {
  return request('/reader/sources', { errorMsg: '获取内容源目录失败' });
}

export function fetchFavorites(filters = {}, limit = 100, skip = 0, options = {}) {
  const { includeContent, ...fetchOptions } = options;
  const params = new URLSearchParams({ limit, skip });
  if (includeContent !== undefined) params.append('include_content', includeContent ? 'true' : 'false');
  withFilters(params, filters);
  return request(`/reader/favorites?${params}`, { ...fetchOptions, errorMsg: '获取收藏列表失败' });
}

export function addFavorite(articleId) {
  return request(`/reader/favorites/${enc(articleId)}`, { method: 'POST', errorMsg: '收藏失败' });
}

export function removeFavorite(articleId) {
  return request(`/reader/favorites/${enc(articleId)}`, { method: 'DELETE', errorMsg: '取消收藏失败' });
}

export function fetchFeedToken() {
  return request('/reader/feed-token', { errorMsg: '获取聚合接口令牌失败' });
}

export function rotateFeedToken() {
  return request('/reader/feed-token/rotate', { method: 'POST', errorMsg: '生成聚合接口令牌失败' });
}

export function subscribeSource(sourceId) {
  return request(`/reader/sources/${enc(sourceId)}/subscribe`, { method: 'POST', errorMsg: '订阅失败' });
}

export function unsubscribeSource(sourceId) {
  return request(`/reader/sources/${enc(sourceId)}/subscribe`, { method: 'DELETE', errorMsg: '取消订阅失败' });
}

// 记录一次主动阅读（fire-and-forget：失败静默，不阻断阅读）。
export function recordArticleRead(articleId) {
  return apiFetch(`${API_BASE_URL}/reader/articles/${enc(articleId)}/read`, { method: 'POST' })
    .catch(() => {});
}

// ==================== 未读体系 ====================
export function fetchUnreadCounts(options = {}) {
  return request('/reader/unread-counts', { ...options, errorMsg: '获取未读统计失败' });
}

// 手动单篇标读/标未读(显式覆盖;不同于 recordArticleRead,不累计阅读计量)
export function markArticleRead(articleId) {
  return request(`/reader/articles/${enc(articleId)}/mark-read`, { method: 'POST', errorMsg: '标为已读失败' });
}

export function markArticleUnread(articleId) {
  return request(`/reader/articles/${enc(articleId)}/mark-unread`, { method: 'POST', errorMsg: '标为未读失败' });
}

// sourceId 为空 = 全部订阅源标为已读；返回更新后的 {by_source, total}。
export function markAllRead(sourceId = null) {
  const path = sourceId ? `/reader/sources/${enc(sourceId)}/mark-all-read` : '/reader/mark-all-read';
  return request(path, { method: 'POST', errorMsg: '标记已读失败' });
}

export function createSubscription(data) {
  return request('/subscriptions', { method: 'POST', body: data, errorMsg: '创建订阅源失败' });
}

export function updateSubscription(id, data) {
  return request(`/subscriptions/${id}`, { method: 'PUT', body: data, errorMsg: '更新订阅源失败' });
}

export function rotateSubscriptionToken(id) {
  return request(`/subscriptions/${id}/rotate-token`, { method: 'POST', errorMsg: '轮换订阅令牌失败' });
}

export function deleteSubscription(id) {
  return request(`/subscriptions/${id}`, { method: 'DELETE', errorMsg: '删除订阅源失败' });
}
