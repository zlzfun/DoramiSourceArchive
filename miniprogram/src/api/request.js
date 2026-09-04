import Taro from '@tarojs/taro';
import { API_BASE_URL } from '../config';
import { getToken, clearSession } from '../store/session';

/**
 * 统一请求封装(与 frontend/src/api.js 的 request() 同契约):
 * - 会话以 Authorization: Bearer 承载(后端 current_auth_session 的小程序载体,方案 §3.2);
 * - 非 /auth/ 路径遇 401 → 清会话 → 回登录页(带当前页路径以便登录后回落);
 * - 失败时抛 Error(message 取后端 detail,否则用 errorMsg)。
 */
let redirecting = false;

function currentPagePath() {
  const pages = Taro.getCurrentPages();
  const page = pages[pages.length - 1];
  if (!page) return '';
  const query = page.options && Object.keys(page.options).length
    ? `?${Object.entries(page.options).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`
    : '';
  return `/${page.route}${query}`;
}

export function goLogin({ replace = true } = {}) {
  if (redirecting) return;
  redirecting = true;
  const from = currentPagePath();
  const url = from && !from.startsWith('/pages/login/')
    ? `/pages/login/index?redirect=${encodeURIComponent(from)}`
    : '/pages/login/index';
  const nav = replace ? Taro.reLaunch : Taro.navigateTo;
  nav({ url }).finally(() => { redirecting = false; });
}

export async function request(path, { method = 'GET', body, errorMsg = '请求失败', headers = {}, raw = false } = {}) {
  const token = getToken();
  let res;
  try {
    res = await Taro.request({
      url: `${API_BASE_URL}${path}`,
      method,
      data: body,
      header: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        'X-Dorami-Client': 'miniprogram',
        ...headers,
      },
    });
  } catch (err) {
    throw new Error(errorMsg || '网络不可用');
  }
  if (res.statusCode === 401 && !path.startsWith('/auth/')) {
    clearSession();
    goLogin();
    throw new Error('登录已过期');
  }
  if (res.statusCode < 200 || res.statusCode >= 300) {
    const detail = res.data && res.data.detail;
    const msg = typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : errorMsg);
    const error = new Error(msg);
    error.status = res.statusCode;
    throw error;
  }
  return raw ? res : res.data;
}

// 把 filters 里的非空项拼成查询串(空串/null/undefined 跳过)。
export function qs(params = {}) {
  const parts = [];
  Object.entries(params).forEach(([k, v]) => {
    if (v === '' || v === null || v === undefined || v === false) return;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v === true ? 'true' : v)}`);
  });
  return parts.length ? `?${parts.join('&')}` : '';
}
