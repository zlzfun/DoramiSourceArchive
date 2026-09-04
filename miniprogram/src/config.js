// 后端 origin 单一来源:.env.development / .env.production 的 TARO_APP_API_ORIGIN。
export const API_ORIGIN = String(process.env.TARO_APP_API_ORIGIN || 'http://127.0.0.1:8088').replace(/\/+$/, '');
export const API_BASE_URL = `${API_ORIGIN}/api`;

// 服务端渲染正文里的签名图链是站内相对路径(/api/public/media?…),客户端拼 origin。
export function absolutizeMediaPath(path) {
  if (!path) return '';
  return path.startsWith('/') ? `${API_ORIGIN}${path}` : path;
}
