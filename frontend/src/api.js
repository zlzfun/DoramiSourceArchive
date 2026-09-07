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

// 自助偏好(v3.19 多管理员波):目前仅 default_surface(管理员登录后默认落地界面)。
export function updateOwnPreferences(payload) {
  return request('/auth/preferences', { method: 'POST', body: payload, errorMsg: '保存偏好失败' });
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

// 批量账户操作(v3.41 账户管理 V2):updates = { role? | is_active? | ai_beta_enabled? }。
// 后端原子语义:任一账户不存在或整批后活跃管理员归零 → 整批 400 回滚。
export function batchUpdateAccounts(usernames, updates) {
  return request('/accounts/batch', {
    method: 'POST',
    body: { usernames, ...updates },
    errorMsg: '批量更新账户失败',
  });
}

// ==================== 运维管理（仅管理员） ====================
export function fetchAdminOverview() {
  return request('/admin/overview', { errorMsg: '获取运维概览失败' });
}

// 规模化波:服务端分页 + 用户名搜索;响应 {items, total, summary}(summary 聚合全量,不受分页/搜索影响)。
export function fetchAdminAccounts(
  days = 30,
  { skip = 0, limit = 15, q = '', role = '', status = '', ai = '', sort = '', order = '' } = {},
) {
  // role/status/ai 组合过滤与 sort/order 服务端生效(v3.41 账户管理 V2);空值不入参。
  const params = withFilters(new URLSearchParams({ days, skip, limit }), {
    q: q.trim(), role, status, ai, sort, order,
  });
  return request(`/admin/accounts?${params.toString()}`, { errorMsg: '获取账户列表失败' });
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

// 管理操作审计(v3.19 多管理员波):中间件对管理面写操作逐条落行,按时间倒序;服务端分页。
// v3.42(M11):operator 操作者子串 / q 跨摘要·目标·路径子串 / status ∈ ok|denied,
// 全部服务端生效并与时间窗/分页叠加。
export function fetchAdminAuditLog(days = 30, { skip = 0, limit = 15, operator = '', q = '', status = '' } = {}) {
  const params = withFilters(new URLSearchParams({ days, skip, limit }), {
    operator: operator.trim(), q: q.trim(), status,
  });
  return request(`/admin/audit-log?${params}`, { errorMsg: '获取操作审计失败' });
}

// ── 媒体库（图床） ──
// 正文外链图统一改经后端代理取图（命中本地缓存回文件，失败 302 回源降级）。
// 非 http(s) 地址（data: URI、相对路径）原样返回不代理。
export function mediaProxyUrl(src) {
  if (typeof src !== 'string' || !/^https?:\/\//i.test(src)) return src;
  return `${API_BASE_URL}/media/proxy?url=${encodeURIComponent(src)}`;
}

export function fetchMediaStats() {
  return request('/admin/media/stats', { errorMsg: '获取媒体库统计失败' });
}

// year 传自然年取该年切片(年份切换轨);缺省为近 days 天滚动窗。响应恒带 years 可用年份列表。
export function fetchMediaHeatmap(days = 365, year = null) {
  const params = withFilters(new URLSearchParams({ days }), { year });
  return request(`/admin/media/heatmap?${params.toString()}`, { errorMsg: '获取媒体热点图失败' });
}

export function fetchMediaDay(date) {
  return request(`/admin/media/days/${enc(date)}`, { errorMsg: '获取当日媒体明细失败' });
}

// 单篇定点重抓：强制绕过失败退避冷却，返回该篇刷新后的逐图状态。
export function prefetchArticleMedia(articleId) {
  return request(`/admin/media/articles/${enc(articleId)}/prefetch`, {
    method: 'POST', errorMsg: '重抓图片失败',
  });
}
// （全量回填端点 /admin/media/backfill 保留在后端作脚本化应急通道,前端入口已撤——
//  生产只做随抓预取;存量补录走热点图抽屉的单篇定点重抓。）

// ── 读者面源可见性（管理面隐藏节点） ──
export function fetchSourceVisibility() {
  return request('/admin/source-visibility', { errorMsg: '获取源可见性失败' });
}

export function setSourceVisibility(sourceId, hidden) {
  return request(`/admin/source-visibility/${enc(sourceId)}`, {
    method: 'POST', body: { hidden }, errorMsg: '更新源可见性失败',
  });
}

export function getAiBetaGlobal() {
  return request('/admin/ai-beta/global', { errorMsg: '获取 AI 全局开关失败' });
}

export function setAiBetaGlobal(enabled) {
  return request('/admin/ai-beta/global', { method: 'POST', body: { enabled }, errorMsg: '更新 AI 全局开关失败' });
}

// 读者面 AI 全局日 token 预算（0 = 不限）：与总闸同端点，两字段独立可改。
export function setAiDailyTokenBudget(budget) {
  return request('/admin/ai-beta/global', { method: 'POST', body: { daily_token_budget: budget }, errorMsg: '更新 AI 日预算失败' });
}

// 新账号 AI 默认值：只影响此后新建账户的逐账户开关初值；与总闸同端点。
export function setAiBetaNewUserDefault(enabled) {
  return request('/admin/ai-beta/global', { method: 'POST', body: { new_user_default: enabled }, errorMsg: '更新新账号 AI 默认值失败' });
}

// ── 阅读器 AI（用户面：翻译 / 问答） ──
export function translateArticle(articleId) {
  return request('/reader/ai/translate', { method: 'POST', body: { article_id: articleId }, errorMsg: '翻译失败，请稍后重试' });
}

// 生成/读取文章的中文要点摘要(后端缓存于 extensions_json.summary_zh,幂等)
export function summarizeArticle(articleId) {
  return request('/reader/ai/summarize', { method: 'POST', body: { article_id: articleId }, errorMsg: '摘要生成失败，请稍后重试' });
}

export function askReaderAi({ question, scope = 'article', articleId = null, history = [], askId = null }) {
  return request('/reader/ai/ask', {
    method: 'POST',
    body: { question, scope, article_id: articleId, history, ask_id: askId },
    errorMsg: '提问失败，请稍后重试',
  });
}

// ask 阶段进度轮询(阶段化等待态):未知/已完成的 ask_id 返回 { stage: null }
export function fetchAskProgress(askId) {
  return request(`/reader/ai/ask/progress?ask_id=${encodeURIComponent(askId)}`, {
    errorMsg: '获取进度失败',
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
  const { includeContent, includeExtensions, ...fetchOptions } = options;
  const params = new URLSearchParams({ limit, skip });
  if (includeTotal) params.append('include_total', 'true');
  if (includeContent !== undefined) params.append('include_content', includeContent ? 'true' : 'false');
  if (includeExtensions !== undefined) params.append('include_extensions', includeExtensions ? 'true' : 'false');
  withFilters(params, filters);
  return request(`/articles?${params}`, { ...fetchOptions, errorMsg: '获取文章列表失败' });
}

export function fetchArticle(id, options = {}) {
  return request(`/articles/${enc(id)}`, { ...options, errorMsg: '获取文章详情失败' });
}

export function fetchArticleAnalysis(id, options = {}) {
  return request(`/articles/${enc(id)}/analysis`, { ...options, errorMsg: '获取文章分析失败' });
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

// ==================== 运行参数 ====================
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

// v3.42(M09):响应改 { items, total }——total = 当前过滤组合下总数,消费方据此
// 诚实呈现截断;filters 支持 days(时间窗)/status/trigger_type 等 SQL 端过滤。
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

// ==================== 后台任务轮询 ====================
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

export function fetchBackgroundJob(jobId) {
  return request(`/jobs/${jobId}`, { errorMsg: '任务状态查询失败' });
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

// ==================== X API（社交源采集）====================
// bearer_token 只写不回显（后端脱敏，与 llm 的 api_key 同规矩）；
// quota 是按量付费的开销读数（按返回资源计费，非请求次数）。
export function getXApiConfig() {
  return request('/x-api/config', { errorMsg: '获取 X API 配置失败' });
}

export function saveXApiConfig(payload) {
  return request('/x-api/config', { method: 'POST', body: payload, errorMsg: '保存 X API 配置失败' });
}

export function testXApiConfig() {
  return request('/x-api/config/test', { method: 'POST', errorMsg: 'X API 连接测试失败' });
}

// 设置柜凭据区补充:部署级 env-only 机密(GITHUB_TOKEN)的存在性,只回布尔。
export function fetchCredentialsOverview() {
  return request('/admin/credentials', { errorMsg: '获取凭据概览失败' });
}

export function getXApiQuota() {
  return request('/x-api/quota', { errorMsg: '获取 X API 用量失败' });
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

// ==================== MCP ====================
// 走统一封装:后端未就绪/网关 502 时抛可读错误(此前裸 r.json() 对 HTML 错误页
// 会抛 SyntaxError);调用方各有 catch 兜底降级,行为不变。
export const fetchMcpStatus = () => request('/mcp/status', { errorMsg: '获取 MCP 状态失败' });

export const toggleMcp = () => request('/mcp/toggle', { method: 'POST', errorMsg: '切换 MCP 状态失败' });

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

/* ── 文章分享 ──
   站内深链纯前端拼装(见 utils/shareLink.js),不经后端;此处三个接口只服务
   「公开链接」这一档:签发 / 我的分享列表 / 撤销。 */
export function createArticleShare(articleId, expiresInDays) {
  return request(`/reader/articles/${enc(articleId)}/share`, {
    method: 'POST',
    body: { expires_in_days: expiresInDays ?? null },
    errorMsg: '生成分享链接失败',
  });
}

export function fetchArticleShares(articleId) {
  const params = articleId ? `?article_id=${enc(articleId)}` : '';
  return request(`/reader/shares${params}`, { errorMsg: '获取分享链接失败' });
}

export function revokeArticleShare(shareId) {
  return request(`/reader/shares/${enc(shareId)}`, { method: 'DELETE', errorMsg: '撤销分享链接失败' });
}

// 公开只读页取数:免登录端点,不带会话;失败由调用方渲染失效态,不触发全局登录过期事件。
export async function fetchSharedArticle(token) {
  const res = await fetch(`${API_BASE_URL}/public/share/${enc(token)}`);
  if (!res.ok) await handleApiError(res, '分享链接无效或已失效');
  return res.json();
}

export function fetchPublicShareGlobal() {
  return request('/admin/public-share', { errorMsg: '获取分享总闸失败' });
}

export function updatePublicShareGlobal(enabled) {
  return request('/admin/public-share', { method: 'POST', body: { enabled }, errorMsg: '更新分享总闸失败' });
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

// ==================== 源合集(策展合集) ====================

export function fetchReaderCollections() {
  return request('/reader/collections', { errorMsg: '获取合集目录失败' });
}

export function subscribeCollection(collectionId) {
  return request(`/reader/collections/${enc(collectionId)}/subscribe`, { method: 'POST', errorMsg: '订阅合集失败' });
}

export function unsubscribeCollection(collectionId) {
  return request(`/reader/collections/${enc(collectionId)}/subscribe`, { method: 'DELETE', errorMsg: '退订合集失败' });
}

// ==================== 用户自定源(v3.40) ====================

export function previewCustomSource(url) {
  return request('/reader/custom-sources/preview', { method: 'POST', body: { url }, errorMsg: '预览失败' });
}

export function createCustomSource(url, name) {
  return request('/reader/custom-sources', {
    method: 'POST', body: name ? { url, name } : { url }, errorMsg: '添加自定源失败',
  });
}

export function fetchCustomSources() {
  return request('/reader/custom-sources', { errorMsg: '获取自定源失败' });
}

export function removeCustomSource(sourceId) {
  return request(`/reader/custom-sources/${enc(sourceId)}`, { method: 'DELETE', errorMsg: '移除自定源失败' });
}

// ==================== 我的早报 / 显式兴趣 ====================
export function fetchInterestCatalog(options = {}) {
  return request('/reader/interests/catalog', { ...options, errorMsg: '获取兴趣目录失败' });
}

export function fetchInterests(options = {}) {
  return request('/reader/interests', { ...options, errorMsg: '获取我的兴趣失败' });
}

export function saveInterests(items, { completeOnboarding = false } = {}) {
  return request('/reader/interests', {
    method: 'PUT', body: { items, complete_onboarding: completeOnboarding }, errorMsg: '保存兴趣设置失败',
  });
}

export function ensurePersonalBrief() {
  return request('/reader/briefs/today/ensure', { method: 'POST', errorMsg: '准备今日早报失败' });
}

export function rebuildPersonalBrief() {
  return request('/reader/briefs/today/rebuild', { method: 'POST', errorMsg: '重新编排今日早报失败' });
}

export function fetchTodayPersonalBrief(options = {}) {
  return request('/reader/briefs/today', { ...options, errorMsg: '获取今日早报失败' });
}

export function fetchPersonalBrief(reportDate, revision = null, options = {}) {
  const query = revision == null ? '' : `?revision=${enc(revision)}`;
  return request(`/reader/briefs/${enc(reportDate)}${query}`, { ...options, errorMsg: '获取历史早报失败' });
}

export function fetchPersonalBriefs(limit = 30, options = {}) {
  return request(`/reader/briefs?limit=${enc(limit)}`, { ...options, errorMsg: '获取历史早报失败' });
}

// ==================== CMS taxonomy 治理（仅管理员） ====================
export function fetchCmsTags(filters = {}, options = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  return request(`/admin/cms-tags${query ? `?${query}` : ''}`, { ...options, errorMsg: '获取 CMS 标签失败' });
}

export function createCmsTag(payload) {
  return request('/admin/cms-tags', { method: 'POST', body: payload, errorMsg: '创建 CMS 标签失败' });
}

export function updateCmsTag(tagId, payload) {
  return request(`/admin/cms-tags/${enc(tagId)}`, { method: 'PATCH', body: payload, errorMsg: '更新 CMS 标签失败' });
}

export function addCmsTagAlias(tagId, payload) {
  return request(`/admin/cms-tags/${enc(tagId)}/aliases`, {
    method: 'POST', body: payload, errorMsg: '新增标签 Alias 失败',
  });
}

export function deleteCmsTagAlias(tagId, aliasId, reason = '') {
  return request(`/admin/cms-tags/${enc(tagId)}/aliases/${enc(aliasId)}?reason=${enc(reason)}`, {
    method: 'DELETE', errorMsg: '删除标签 Alias 失败',
  });
}

export function fetchCmsTagCandidates(filters = {}, options = {}) {
  const query = withFilters(new URLSearchParams(), filters).toString();
  return request(`/admin/cms-tag-candidates${query ? `?${query}` : ''}`, { ...options, errorMsg: '获取标签候选失败' });
}

export function deleteCmsTagCandidate(candidateId, reason) {
  return request(`/admin/cms-tag-candidates/${enc(candidateId)}?reason=${enc(reason)}`, {
    method: 'DELETE', errorMsg: '删除标签候选失败',
  });
}

export function activateCmsTagCandidate(candidateId, payload) {
  return request(`/admin/cms-tag-candidates/${enc(candidateId)}/activate`, {
    method: 'POST', body: payload, errorMsg: '激活标签候选失败',
  });
}

export function reclassifyCmsTagCandidate(candidateId, kind, reason) {
  return request(`/admin/cms-tag-candidates/${enc(candidateId)}`, {
    method: 'PATCH', body: { kind, reason }, errorMsg: '纠正 Candidate 分面失败',
  });
}

export function resolveCmsTagCandidate(candidateId, targetTagId, reason) {
  return request(`/admin/cms-tag-candidates/${enc(candidateId)}/resolve`, {
    method: 'POST', body: { target_tag_id: targetTagId, reason }, errorMsg: '归并 Candidate 失败',
  });
}

export function fetchTaxonomyState(options = {}) {
  return request('/admin/taxonomy/state', { ...options, errorMsg: '获取 taxonomy 发布状态失败' });
}

export function fetchInterestCatalogPolicy(options = {}) {
  return request('/admin/taxonomy/interest-catalog-policy', { ...options, errorMsg: '获取兴趣目录策略失败' });
}

export function updateInterestCatalogPolicy(payload) {
  return request('/admin/taxonomy/interest-catalog-policy', {
    method: 'PATCH', body: payload, errorMsg: '更新兴趣目录策略失败',
  });
}

export function backfillCmsTagAliases(reason) {
  return request('/admin/taxonomy/aliases/backfill', {
    method: 'POST', body: { reason }, errorMsg: '同步规范名 Alias 失败',
  });
}

export function publishTaxonomyV1(changeSummary) {
  return request('/admin/taxonomy/v1/publish', {
    method: 'POST',
    body: { confirmation: 'PUBLISH TAXONOMY V1', change_summary: changeSummary },
    errorMsg: '发布 taxonomy v1 失败',
  });
}

export function rejectCmsTagCandidate(candidateId, reason) {
  return request(`/admin/cms-tag-candidates/${enc(candidateId)}/reject`, {
    method: 'POST', body: { reason }, errorMsg: '拒绝标签候选失败',
  });
}

export function mergeCmsTag(tagId, targetTagId, reason) {
  return request(`/admin/cms-tags/${enc(tagId)}/merge`, {
    method: 'POST', body: { target_tag_id: targetTagId, reason }, errorMsg: '合并 CMS 标签失败',
  });
}

export function deprecateCmsTag(tagId, replacementId, reason) {
  return request(`/admin/cms-tags/${enc(tagId)}/deprecate`, {
    method: 'POST', body: { replacement_id: replacementId || null, reason }, errorMsg: '废弃 CMS 标签失败',
  });
}

export function retagCmsTag(tagId, days = 7) {
  return request(`/admin/cms-tags/${enc(tagId)}/retag`, {
    method: 'POST', body: { days, article_ids: [] }, errorMsg: '创建重标任务失败',
  });
}

export function fetchAnalysisConfig(options = {}) {
  return request('/admin/analysis/config', { ...options, errorMsg: '获取分析功能配置失败' });
}

export function updateAnalysisConfig(payload) {
  return request('/admin/analysis/config', { method: 'PUT', body: payload, errorMsg: '更新分析功能配置失败' });
}

export function fetchAnalysisMetrics(days = 7, options = {}) {
  return request(`/admin/analysis/metrics?days=${enc(days)}`, { ...options, errorMsg: '获取分析观测指标失败' });
}

export function estimateFullAnalysisBackfill(payload, options = {}) {
  return request('/admin/analysis/backfills/estimate', {
    ...options, method: 'POST', body: payload, errorMsg: '估算历史分析范围失败',
  });
}

export function createFullAnalysisBackfill(payload) {
  return request('/admin/analysis/backfills', {
    method: 'POST', body: { ...payload, confirmation: 'RUN FULL ANALYSIS' }, errorMsg: '创建历史分析回填失败',
  });
}

export function fetchFullAnalysisBackfills(options = {}) {
  return request('/admin/analysis/backfills', { ...options, errorMsg: '获取历史分析任务失败' });
}

export function pauseFullAnalysisBackfill(jobId) {
  return request(`/admin/analysis/backfills/${enc(jobId)}/pause`, { method: 'POST', errorMsg: '暂停历史分析失败' });
}

export function resumeFullAnalysisBackfill(jobId) {
  return request(`/admin/analysis/backfills/${enc(jobId)}/resume`, { method: 'POST', errorMsg: '继续历史分析失败' });
}

export function cancelFullAnalysisBackfill(jobId) {
  return request(`/admin/analysis/backfills/${enc(jobId)}/cancel`, { method: 'POST', errorMsg: '取消历史分析失败' });
}

export function retryFullAnalysisBackfill(jobId) {
  return request(`/admin/analysis/backfills/${enc(jobId)}/retry-failed`, { method: 'POST', errorMsg: '重试失败文章失败' });
}

export function fetchAdminUserSources() {
  return request('/admin/user-sources', { errorMsg: '获取用户自定源失败' });
}

export function setAdminUserSourcesConfig(config) {
  return request('/admin/user-sources/config', { method: 'POST', body: config, errorMsg: '保存自定源配置失败' });
}

export function toggleAdminUserSource(sourceId, isActive) {
  return request(`/admin/user-sources/${enc(sourceId)}/toggle`, {
    method: 'POST', body: { is_active: isActive }, errorMsg: '切换自定源状态失败',
  });
}

export function deleteAdminUserSource(sourceId) {
  return request(`/admin/user-sources/${enc(sourceId)}`, { method: 'DELETE', errorMsg: '删除自定源失败' });
}

// 记录一次主动阅读（fire-and-forget：失败静默，不阻断阅读）。
export function recordArticleRead(articleId) {
  // fire-and-forget 但解析响应:成功时带回 read_count(全站累计阅读数)供阅读窗就地刷新
  return apiFetch(`${API_BASE_URL}/reader/articles/${enc(articleId)}/read`, { method: 'POST' })
    .then((res) => (res.ok ? res.json() : null))
    .catch(() => null);
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
export function markAllRead(sourceId = null, shape = null) {
  // shape=article|bulletin|social|podcast:全部标读只作用于当前内容容器
  const path = sourceId
    ? `/reader/sources/${enc(sourceId)}/mark-all-read`
    : `/reader/mark-all-read${shape ? `?shape=${enc(shape)}` : ''}`;
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

// ==================== 反馈与建议（v3.18 互通波） ====================
export function submitFeedback(category, content) {
  return request('/reader/feedback', { method: 'POST', body: { category, content }, errorMsg: '提交反馈失败' });
}

export function fetchMyFeedback() {
  return request('/reader/feedback', { errorMsg: '获取反馈列表失败' });
}

export function withdrawFeedback(id) {
  return request(`/reader/feedback/${id}`, { method: 'DELETE', errorMsg: '撤回反馈失败' });
}

// 未读回复角标:管理员回复后读者侧的轻通知(轮询计数 + 打开反馈页即清)。
export function fetchFeedbackUnreadCount() {
  return request('/reader/feedback/unread-count', { errorMsg: '获取未读回复数失败' });
}

// 进入反馈页视为已读(fire-and-forget:失败静默,角标下轮轮询自然校准)。
export function markFeedbackSeen() {
  return apiFetch(`${API_BASE_URL}/reader/feedback/mark-seen`, { method: 'POST' })
    .catch(() => {});
}

// 规模化波:服务端分页;响应 {items, total, counts}(total = 当前过滤组合下总数)。
// v3.42(M17)增 category 分类过滤与 q 检索(跨正文/提交者用户名),SQL 端生效。
export function fetchAdminFeedback(status = null, { skip = 0, limit = 10, category = '', q = '' } = {}) {
  const params = withFilters(new URLSearchParams({ skip, limit }), {
    status: status || '', category, q: q.trim(),
  });
  return request(`/admin/feedback?${params}`, { errorMsg: '获取反馈收件箱失败' });
}

export function updateFeedbackStatus(id, status, adminNote = undefined) {
  const body = { status };
  if (adminNote !== undefined) body.admin_note = adminNote;
  return request(`/admin/feedback/${id}/status`, { method: 'POST', body, errorMsg: '处理反馈失败' });
}

// ==================== 公告（v3.18 互通波） ====================
export function fetchReaderAnnouncements() {
  return request('/reader/announcements', { errorMsg: '获取公告失败' });
}

// 关闭公告为一次性动作，失败静默（下次会话还会出现，无害）。
export function dismissAnnouncement(id) {
  return apiFetch(`${API_BASE_URL}/reader/announcements/${id}/dismiss`, { method: 'POST' })
    .catch(() => {});
}

// v3.42(M17):skip/limit 服务端分页,响应带 total;默认参数保持全量语义。
export function fetchAdminAnnouncements({ skip = 0, limit = 200 } = {}) {
  return request(`/admin/announcements?skip=${enc(skip)}&limit=${enc(limit)}`, { errorMsg: '获取公告列表失败' });
}

export function createAnnouncement(payload) {
  return request('/admin/announcements', { method: 'POST', body: payload, errorMsg: '发布公告失败' });
}

export function updateAnnouncement(id, payload) {
  return request(`/admin/announcements/${id}`, { method: 'PUT', body: payload, errorMsg: '更新公告失败' });
}

export function toggleAnnouncement(id) {
  return request(`/admin/announcements/${id}/toggle`, { method: 'POST', errorMsg: '切换公告状态失败' });
}

export function deleteAnnouncement(id) {
  return request(`/admin/announcements/${id}`, { method: 'DELETE', errorMsg: '删除公告失败' });
}

// ==================== 远程内容同步（v3.18 互通波，admin） ====================
export function testRemoteSync(baseUrl, username, password, protocol = 'v2') {
  return request('/admin/remote-sync/test', {
    method: 'POST',
    body: { base_url: baseUrl, username, password, protocol },
    errorMsg: '远端连接测试失败',
  });
}

// 提交后台拉取任务，返回 { job_id }；调用方用 fetchBackgroundJob 轮询进度
// （processed/total 逐条推进），不在这里 pollJob 到终态。
export function startRemoteSync(baseUrl, username, password, options = {}) {
  const { fetchedDateStart, sourceIds, protocol = 'v2' } = options;
  const body = { base_url: baseUrl, username, password, protocol };
  if (fetchedDateStart) body.fetched_date_start = fetchedDateStart;
  if (sourceIds && sourceIds.length) body.source_ids = sourceIds;
  return request('/admin/remote-sync/start', { method: 'POST', body, errorMsg: '启动远程同步失败' });
}

export function fetchRemoteSyncStatus() {
  return request('/admin/remote-sync/status', { errorMsg: '获取远程同步状态失败' });
}

// 定时同步(v3.19.2):凭据只写不回显——GET 永不含 password,仅 password_set;
// POST 的 password 传空串表示保留已存密码。
export function fetchRemoteSyncSchedule() {
  return request('/admin/remote-sync/schedule', { errorMsg: '获取定时同步配置失败' });
}

export function saveRemoteSyncSchedule(payload) {
  return request('/admin/remote-sync/schedule', {
    method: 'POST',
    body: payload,
    errorMsg: '保存定时同步配置失败',
  });
}
