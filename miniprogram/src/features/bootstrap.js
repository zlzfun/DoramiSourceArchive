import { fetchSession, fetchRuntime } from '../api';
import { setUser, setRuntime, clearSession, isAuthenticated } from '../store/session';
import { loadSources, refreshUnread, loadFavoriteIds } from '../store/reader';
import { goLogin } from '../api/request';

/**
 * 登录后/启动时的会话引导:验会话 → 拉 runtime 能力位 → 预热源目录/未读/收藏。
 * 401 由 request 层统一回登录门,这里只吞掉其余错误(离线也能进页面看空态)。
 */
let inflight = null;
let lastOkAt = 0;
const REFRESH_INTERVAL_MS = 60 * 1000;

export function bootstrapSession({ force = false } = {}) {
  if (!isAuthenticated()) { goLogin(); return Promise.resolve(false); }
  if (inflight) return inflight;
  if (!force && lastOkAt && Date.now() - lastOkAt < REFRESH_INTERVAL_MS) return Promise.resolve(true);
  inflight = (async () => {
    try {
      const session = await fetchSession();
      if (!session || !session.authenticated) { clearSession(); goLogin(); return false; }
      setUser(session.user);
      const [runtime] = await Promise.all([
        fetchRuntime().catch(() => null),
        loadSources().catch(() => null),
        refreshUnread().catch(() => null),
        loadFavoriteIds().catch(() => null),
      ]);
      if (runtime) setRuntime(runtime);
      lastOkAt = Date.now();
      return true;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}
