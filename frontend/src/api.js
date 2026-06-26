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

export async function loginAdmin(username, password) {
  const res = await apiFetch(`${API_BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) await handleApiError(res, '登录失败');
  return res.json();
}

export async function fetchAuthSession() {
  const res = await apiFetch(`${API_BASE_URL}/auth/session`);
  if (!res.ok) return { authenticated: false, user: null };
  return res.json();
}

export async function fetchRuntimeInfo() {
  const res = await apiFetch(`${API_BASE_URL}/runtime`);
  if (!res.ok) await handleApiError(res, '获取运行角色失败');
  return res.json();
}

export async function logoutAdmin() {
  const res = await apiFetch(`${API_BASE_URL}/auth/logout`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '退出登录失败');
  return res.json();
}

export async function changeOwnPassword(currentPassword, newPassword) {
  const res = await apiFetch(`${API_BASE_URL}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (!res.ok) await handleApiError(res, '修改密码失败');
  return res.json();
}

export async function updateAvatar(avatar) {
  const res = await apiFetch(`${API_BASE_URL}/auth/avatar`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ avatar }),
  });
  if (!res.ok) await handleApiError(res, '更新头像失败');
  return res.json();
}

// ==================== 账户管理（仅管理员） ====================
export async function fetchAccounts() {
  const res = await apiFetch(`${API_BASE_URL}/accounts`);
  if (!res.ok) await handleApiError(res, '获取账户列表失败');
  return res.json();
}

export async function createAccount(payload) {
  const res = await apiFetch(`${API_BASE_URL}/accounts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '创建账户失败');
  return res.json();
}

export async function updateAccount(username, payload) {
  const res = await apiFetch(`${API_BASE_URL}/accounts/${encodeURIComponent(username)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '更新账户失败');
  return res.json();
}

export async function resetAccountPassword(username, newPassword) {
  const res = await apiFetch(`${API_BASE_URL}/accounts/${encodeURIComponent(username)}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (!res.ok) await handleApiError(res, '重置密码失败');
  return res.json();
}

export async function deleteAccount(username) {
  const res = await apiFetch(`${API_BASE_URL}/accounts/${encodeURIComponent(username)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除账户失败');
  return res.json();
}

export async function fetchFetchers() {
  const res = await apiFetch(`${API_BASE_URL}/fetchers`);
  if (!res.ok) await handleApiError(res, '获取抓取器注册表失败');
  return res.json();
}

export async function fetchSourceHealth() {
  const res = await apiFetch(`${API_BASE_URL}/source-health`);
  if (!res.ok) await handleApiError(res, '获取数据源健康状态失败');
  return res.json();
}

export async function fetchArticles(filters = {}, limit = 100, skip = 0, includeTotal = false, options = {}) {
  const { includeContent, ...fetchOptions } = options;
  const params = new URLSearchParams({ limit, skip });
  if (includeTotal) params.append('include_total', 'true');
  if (includeContent !== undefined) params.append('include_content', includeContent ? 'true' : 'false');
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await apiFetch(`${API_BASE_URL}/articles?${params}`, fetchOptions);
  if (!res.ok) await handleApiError(res, '获取文章列表失败');
  return res.json();
}

export async function fetchArticle(id, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, options);
  if (!res.ok) await handleApiError(res, '获取文章详情失败');
  return res.json();
}

export async function deleteArticle(id) {
  const res = await apiFetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除失败');
  return res.json();
}

export async function batchDeleteArticles(ids) {
  const res = await apiFetch(`${API_BASE_URL}/articles/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) await handleApiError(res, '批量删除失败');
  return res.json();
}

export async function updateArticle(id, data) {
  const res = await apiFetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新失败');
  return res.json();
}

export async function createArticle(payload) {
  const res = await apiFetch(`${API_BASE_URL}/articles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '录入失败');
  return res.json();
}

export async function vectorizeArticle(id) {
  const res = await apiFetch(`${API_BASE_URL}/vectorize/${encodeURIComponent(id)}`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '向量化失败');
  return res.json();
}

export async function batchVectorizeArticles(ids) {
  const res = await apiFetch(`${API_BASE_URL}/vectorize/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) await handleApiError(res, '批量向量化失败');
  return res.json();
}

function runQuery(options = {}) {
  const params = new URLSearchParams();
  if (options.testLimit !== undefined && options.testLimit !== null) {
    params.append('test_limit', options.testLimit);
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

export async function triggerFetch(fetcherId, params, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/fetch/${fetcherId}${runQuery(options)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) await handleApiError(res, `[${fetcherId}] 抓取失败`);
  return res.json();
}

export async function triggerBatchFetch(items, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/fetch/batch${runQuery(options)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) await handleApiError(res, '批量抓取失败');
  return res.json();
}

export async function fetchRunningProgress() {
  const res = await apiFetch(`${API_BASE_URL}/fetch-runs/running-progress`);
  if (!res.ok) return {};
  return res.json();
}

export async function fetchTasks() {
  const res = await apiFetch(`${API_BASE_URL}/tasks`);
  if (!res.ok) await handleApiError(res, '获取任务列表失败');
  return res.json();
}

export async function fetchFetchRuns(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await apiFetch(`${API_BASE_URL}/fetch-runs?${params}`);
  if (!res.ok) await handleApiError(res, '获取抓取运行历史失败');
  return res.json();
}

export async function fetchNodeGroups(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const query = params.toString();
  const res = await apiFetch(`${API_BASE_URL}/node-groups${query ? `?${query}` : ''}`);
  if (!res.ok) await handleApiError(res, '获取采集范围失败');
  return res.json();
}

export async function createNodeGroup(data) {
  const res = await apiFetch(`${API_BASE_URL}/node-groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建采集范围失败');
  return res.json();
}

export async function updateNodeGroup(id, data) {
  const res = await apiFetch(`${API_BASE_URL}/node-groups/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新采集范围失败');
  return res.json();
}

export async function deleteNodeGroup(id) {
  const res = await apiFetch(`${API_BASE_URL}/node-groups/${id}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除采集范围失败');
  return res.json();
}

export async function runNodeGroup(id, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/node-groups/${id}/fetch${runQuery(options)}`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '触发采集范围失败');
  return res.json();
}

export async function fetchCollectionJobs(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const query = params.toString();
  const res = await apiFetch(`${API_BASE_URL}/collection-jobs${query ? `?${query}` : ''}`);
  if (!res.ok) await handleApiError(res, '获取采集任务失败');
  return res.json();
}

export async function createCollectionJob(data) {
  const res = await apiFetch(`${API_BASE_URL}/collection-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建采集任务失败');
  return res.json();
}

export async function updateCollectionJob(id, data) {
  const res = await apiFetch(`${API_BASE_URL}/collection-jobs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新采集任务失败');
  return res.json();
}

export async function deleteCollectionJob(id) {
  const res = await apiFetch(`${API_BASE_URL}/collection-jobs/${id}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除采集任务失败');
  return res.json();
}

export async function runCollectionJob(id, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/collection-jobs/${id}/run${runQuery(options)}`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '触发采集任务失败');
  return res.json();
}

export async function fetchCollectionJobRuns(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await apiFetch(`${API_BASE_URL}/collection-job-runs?${params}`);
  if (!res.ok) await handleApiError(res, '获取采集运行历史失败');
  return res.json();
}

export async function fetchSourceConfigs(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await apiFetch(`${API_BASE_URL}/source-configs?${params}`);
  if (!res.ok) await handleApiError(res, '获取数据源配置失败');
  return res.json();
}

export async function createSourceConfig(data) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建数据源失败');
  return res.json();
}

export async function updateSourceConfig(sourceId, data) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新数据源失败');
  return res.json();
}

export async function toggleSourceConfig(sourceId, isActive) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!res.ok) await handleApiError(res, '切换数据源状态失败');
  return res.json();
}

export async function deleteSourceConfig(sourceId) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除数据源失败');
  return res.json();
}

export async function fetchSourceConfigNow(sourceId, params = {}) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}/fetch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params }),
  });
  if (!res.ok) await handleApiError(res, '触发数据源抓取失败');
  return res.json();
}

export async function fetchActiveRssSources(params = {}) {
  const res = await apiFetch(`${API_BASE_URL}/source-configs/fetch-active-rss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params }),
  });
  if (!res.ok) await handleApiError(res, '批量触发 RSS 抓取失败');
  return res.json();
}

// ===== AI 自定义节点（URL → 分析 → 预览 → 固化）=====

export async function analyzeSourceUrl(url) {
  const res = await apiFetch(`${API_BASE_URL}/source-builder/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) await handleApiError(res, '分析 URL 失败');
  return res.json();
}

export async function previewSourceConfig(config) {
  const res = await apiFetch(`${API_BASE_URL}/source-builder/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  if (!res.ok) await handleApiError(res, '试抓预览失败');
  return res.json();
}

export async function createTask(data) {
  const res = await apiFetch(`${API_BASE_URL}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建任务失败');
  return res.json();
}

export async function deleteTask(id) {
  const res = await apiFetch(`${API_BASE_URL}/tasks/${id}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除任务失败');
  return res.json();
}

export async function fetchVectorStats() {
  const res = await apiFetch(`${API_BASE_URL}/vector/stats`);
  if (!res.ok) await handleApiError(res, '获取向量统计失败');
  return res.json();
}

export async function vectorSearch(query, topK = 5, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/vector/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK, ...options }),
  });
  if (!res.ok) await handleApiError(res, '检索失败');
  return res.json();
}

export async function ragContext(query, topK = 5, options = {}) {
  const res = await apiFetch(`${API_BASE_URL}/rag/context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK, ...options }),
  });
  if (!res.ok) await handleApiError(res, 'RAG 上下文检索失败');
  return res.json();
}

export async function ragSimilar(articleId, topK = 5) {
  const res = await apiFetch(`${API_BASE_URL}/rag/similar/${encodeURIComponent(articleId)}?top_k=${topK}`);
  if (!res.ok) await handleApiError(res, '相似文章检索失败');
  return res.json();
}

export async function vectorizeAllPending() {
  const res = await apiFetch(`${API_BASE_URL}/vectorize/all-pending`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '全量向量化失败');
  return res.json();
}

export async function reindexAll() {
  const res = await apiFetch(`${API_BASE_URL}/vector/reindex-all`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '全量重索引失败');
  return res.json();
}

export async function fetchSubscribedVectorStats() {
  const res = await apiFetch(`${API_BASE_URL}/vector/subscribed-stats`);
  if (!res.ok) await handleApiError(res, '获取订阅向量统计失败');
  return res.json();
}

export async function getAutoVectorize() {
  const res = await apiFetch(`${API_BASE_URL}/vector/auto-vectorize`);
  if (!res.ok) await handleApiError(res, '获取自动向量化配置失败');
  return res.json();
}

export async function setAutoVectorize(enabled) {
  const res = await apiFetch(`${API_BASE_URL}/vector/auto-vectorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) await handleApiError(res, '设置自动向量化失败');
  return res.json();
}

// ==================== 大模型配置 & 每日日报 ====================

export async function getLLMConfig() {
  const res = await apiFetch(`${API_BASE_URL}/llm/config`);
  if (!res.ok) await handleApiError(res, '获取大模型配置失败');
  return res.json();
}

export async function saveLLMConfig(payload) {
  const res = await apiFetch(`${API_BASE_URL}/llm/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '保存大模型配置失败');
  return res.json();
}

export async function testLLMConfig() {
  const res = await apiFetch(`${API_BASE_URL}/llm/config/test`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '大模型连接测试失败');
  return res.json();
}

export async function getDailyBriefConfig() {
  const res = await apiFetch(`${API_BASE_URL}/daily-brief/config`);
  if (!res.ok) await handleApiError(res, '获取日报配置失败');
  return res.json();
}

export async function saveDailyBriefConfig(payload) {
  const res = await apiFetch(`${API_BASE_URL}/daily-brief/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '保存日报配置失败');
  return res.json();
}

export async function getDailyBriefPipeline() {
  const res = await apiFetch(`${API_BASE_URL}/daily-brief/pipeline`);
  if (!res.ok) await handleApiError(res, '获取日报生成管线失败');
  return res.json();
}

export async function getDailyBriefProgress() {
  const res = await apiFetch(`${API_BASE_URL}/daily-brief/progress`);
  if (!res.ok) await handleApiError(res, '获取日报进度失败');
  return res.json();
}

export async function generateDailyBrief(payload = {}) {
  const res = await apiFetch(`${API_BASE_URL}/daily-brief/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '生成日报失败');
  return res.json();
}

export async function exportArchiveArticles(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const query = params.toString();
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

export const fetchMcpStatus = () =>
  apiFetch(`${API_BASE_URL}/mcp/status`).then(r => r.json());

export const toggleMcp = () =>
  apiFetch(`${API_BASE_URL}/mcp/toggle`, { method: 'POST' }).then(r => r.json());

export async function fetchSubscriptions(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const query = params.toString();
  const res = await apiFetch(`${API_BASE_URL}/subscriptions${query ? `?${query}` : ''}`);
  if (!res.ok) await handleApiError(res, '获取订阅源失败');
  return res.json();
}

export async function fetchReaderSources() {
  const res = await apiFetch(`${API_BASE_URL}/reader/sources`);
  if (!res.ok) await handleApiError(res, '获取内容源目录失败');
  return res.json();
}

export async function fetchFavorites(filters = {}, limit = 100, skip = 0, options = {}) {
  const { includeContent, ...fetchOptions } = options;
  const params = new URLSearchParams({ limit, skip });
  if (includeContent !== undefined) params.append('include_content', includeContent ? 'true' : 'false');
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await apiFetch(`${API_BASE_URL}/reader/favorites?${params}`, fetchOptions);
  if (!res.ok) await handleApiError(res, '获取收藏列表失败');
  return res.json();
}

export async function addFavorite(articleId) {
  const res = await apiFetch(`${API_BASE_URL}/reader/favorites/${encodeURIComponent(articleId)}`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '收藏失败');
  return res.json();
}

export async function removeFavorite(articleId) {
  const res = await apiFetch(`${API_BASE_URL}/reader/favorites/${encodeURIComponent(articleId)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '取消收藏失败');
  return res.json();
}

export async function fetchFeedToken() {
  const res = await apiFetch(`${API_BASE_URL}/reader/feed-token`);
  if (!res.ok) await handleApiError(res, '获取聚合接口令牌失败');
  return res.json();
}

export async function rotateFeedToken() {
  const res = await apiFetch(`${API_BASE_URL}/reader/feed-token/rotate`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '生成聚合接口令牌失败');
  return res.json();
}

export async function subscribeSource(sourceId) {
  const res = await apiFetch(`${API_BASE_URL}/reader/sources/${encodeURIComponent(sourceId)}/subscribe`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '订阅失败');
  return res.json();
}

export async function unsubscribeSource(sourceId) {
  const res = await apiFetch(`${API_BASE_URL}/reader/sources/${encodeURIComponent(sourceId)}/subscribe`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '取消订阅失败');
  return res.json();
}

export async function createSubscription(data) {
  const res = await apiFetch(`${API_BASE_URL}/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建订阅源失败');
  return res.json();
}

export async function updateSubscription(id, data) {
  const res = await apiFetch(`${API_BASE_URL}/subscriptions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新订阅源失败');
  return res.json();
}

export async function rotateSubscriptionToken(id) {
  const res = await apiFetch(`${API_BASE_URL}/subscriptions/${id}/rotate-token`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '轮换订阅令牌失败');
  return res.json();
}

export async function deleteSubscription(id) {
  const res = await apiFetch(`${API_BASE_URL}/subscriptions/${id}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除订阅源失败');
  return res.json();
}
