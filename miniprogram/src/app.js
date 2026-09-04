import Taro, { useLaunch } from '@tarojs/taro';
import { isAuthenticated } from './store/session';
import { bootstrapSession } from './features/bootstrap';
import './app.scss';

/**
 * 应用根:启动时校验会话——有 token 就用 /api/auth/session 验一次(过期即回登录门),
 * 没 token 直接去登录页。tabBar 首页是文章流,登录门在其之上 reLaunch。
 */
export default function App({ children }) {
  useLaunch((options) => {
    // 转发卡片落地:path 已由微信按 options.path/query 打开对应页面,
    // 未登录时页面自身会经 request 的 401 回落登录门(带 redirect)。
    if (!isAuthenticated()) {
      const target = options && options.path ? `/${options.path}` : '';
      const query = options && options.query && Object.keys(options.query).length
        ? `?${Object.entries(options.query).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`
        : '';
      const redirect = target && !target.startsWith('/pages/login/') ? `?redirect=${encodeURIComponent(target + query)}` : '';
      Taro.reLaunch({ url: `/pages/login/index${redirect}` });
      return;
    }
    bootstrapSession().catch(() => {});
  });
  return children;
}
