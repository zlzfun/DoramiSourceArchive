import Taro from '@tarojs/taro';

// 会话 = 后端签发的会话 token(Bearer 载体)+ 用户快照 + runtime 能力位。
// 存 Storage 同步读写(启动分流要用),内存镜像避免每请求读盘。
const TOKEN_KEY = 'dorami_session_token';
const USER_KEY = 'dorami_session_user';

let token = null;
let user = null;
let runtime = null;
const listeners = new Set();

function safeGet(key) {
  try { return Taro.getStorageSync(key) || null; } catch { return null; }
}
function safeSet(key, value) {
  try {
    if (value === null || value === undefined) Taro.removeStorageSync(key);
    else Taro.setStorageSync(key, value);
  } catch { /* 存储不可用时退化为仅内存 */ }
}
function emit() { listeners.forEach((fn) => fn()); }

export function getToken() {
  if (token === null) token = safeGet(TOKEN_KEY) || '';
  return token || '';
}
export function getUser() {
  if (user === null) user = safeGet(USER_KEY) || null;
  return user;
}
export function getRuntime() { return runtime; }
export function setRuntime(next) { runtime = next || null; emit(); }

export function setSession(nextToken, nextUser) {
  token = nextToken || '';
  user = nextUser || null;
  safeSet(TOKEN_KEY, token || null);
  safeSet(USER_KEY, user);
  emit();
}
export function setUser(nextUser) {
  user = nextUser || null;
  safeSet(USER_KEY, user);
  emit();
}
export function clearSession() {
  token = '';
  user = null;
  runtime = null;
  safeSet(TOKEN_KEY, null);
  safeSet(USER_KEY, null);
  emit();
}
export function isAuthenticated() { return Boolean(getToken()); }
export function subscribeSession(fn) { listeners.add(fn); return () => listeners.delete(fn); }
