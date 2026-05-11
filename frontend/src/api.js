const API_BASE_URL = '/api';

async function handleApiError(response, defaultMsg) {
  let msg = defaultMsg;
  try {
    const data = await response.json();
    if (data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
  } catch { /* use defaultMsg */ }
  throw new Error(msg);
}

export async function fetchFetchers() {
  const res = await fetch(`${API_BASE_URL}/fetchers`);
  if (!res.ok) await handleApiError(res, '获取抓取器注册表失败');
  return res.json();
}

export async function fetchSourceHealth() {
  const res = await fetch(`${API_BASE_URL}/source-health`);
  if (!res.ok) await handleApiError(res, '获取数据源健康状态失败');
  return res.json();
}

export async function fetchArticles(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await fetch(`${API_BASE_URL}/articles?${params}`);
  if (!res.ok) await handleApiError(res, '获取文章列表失败');
  return res.json();
}

export async function deleteArticle(id) {
  const res = await fetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除失败');
  return res.json();
}

export async function batchDeleteArticles(ids) {
  const res = await fetch(`${API_BASE_URL}/articles/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) await handleApiError(res, '批量删除失败');
  return res.json();
}

export async function updateArticle(id, data) {
  const res = await fetch(`${API_BASE_URL}/articles/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新失败');
  return res.json();
}

export async function createArticle(payload) {
  const res = await fetch(`${API_BASE_URL}/articles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await handleApiError(res, '录入失败');
  return res.json();
}

export async function vectorizeArticle(id) {
  const res = await fetch(`${API_BASE_URL}/vectorize/${encodeURIComponent(id)}`, { method: 'POST' });
  if (!res.ok) await handleApiError(res, '向量化失败');
  return res.json();
}

export async function batchVectorizeArticles(ids) {
  const res = await fetch(`${API_BASE_URL}/vectorize/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) await handleApiError(res, '批量向量化失败');
  return res.json();
}

export async function triggerFetch(fetcherId, params) {
  const res = await fetch(`${API_BASE_URL}/fetch/${fetcherId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) await handleApiError(res, `[${fetcherId}] 抓取失败`);
  return res.json();
}

export async function fetchTasks() {
  const res = await fetch(`${API_BASE_URL}/tasks`);
  if (!res.ok) await handleApiError(res, '获取任务列表失败');
  return res.json();
}

export async function fetchFetchRuns(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await fetch(`${API_BASE_URL}/fetch-runs?${params}`);
  if (!res.ok) await handleApiError(res, '获取抓取运行历史失败');
  return res.json();
}

export async function fetchSourceConfigs(filters = {}, limit = 100) {
  const params = new URLSearchParams({ limit });
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) params.append(k, v);
  });
  const res = await fetch(`${API_BASE_URL}/source-configs?${params}`);
  if (!res.ok) await handleApiError(res, '获取数据源配置失败');
  return res.json();
}

export async function createSourceConfig(data) {
  const res = await fetch(`${API_BASE_URL}/source-configs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建数据源失败');
  return res.json();
}

export async function updateSourceConfig(sourceId, data) {
  const res = await fetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '更新数据源失败');
  return res.json();
}

export async function toggleSourceConfig(sourceId, isActive) {
  const res = await fetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  });
  if (!res.ok) await handleApiError(res, '切换数据源状态失败');
  return res.json();
}

export async function deleteSourceConfig(sourceId) {
  const res = await fetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除数据源失败');
  return res.json();
}

export async function fetchSourceConfigNow(sourceId, params = {}) {
  const res = await fetch(`${API_BASE_URL}/source-configs/${encodeURIComponent(sourceId)}/fetch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params }),
  });
  if (!res.ok) await handleApiError(res, '触发数据源抓取失败');
  return res.json();
}

export async function fetchActiveRssSources(params = {}) {
  const res = await fetch(`${API_BASE_URL}/source-configs/fetch-active-rss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params }),
  });
  if (!res.ok) await handleApiError(res, '批量触发 RSS 抓取失败');
  return res.json();
}

export async function createTask(data) {
  const res = await fetch(`${API_BASE_URL}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) await handleApiError(res, '创建任务失败');
  return res.json();
}

export async function deleteTask(id) {
  const res = await fetch(`${API_BASE_URL}/tasks/${id}`, { method: 'DELETE' });
  if (!res.ok) await handleApiError(res, '删除任务失败');
  return res.json();
}

export async function fetchVectorStats() {
  const res = await fetch(`${API_BASE_URL}/vector/stats`);
  if (!res.ok) await handleApiError(res, '获取向量统计失败');
  return res.json();
}

export async function vectorSearch(query, topK = 5) {
  const res = await fetch(`${API_BASE_URL}/vector/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK }),
  });
  if (!res.ok) await handleApiError(res, '检索失败');
  return res.json();
}
