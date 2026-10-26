import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { updateOwnPreferences } from '../api';
import { accountTitlePreference, readGuestTitlePreference, writeGuestTitlePreference } from '../utils/titleDisplay';

// 「默认显示中文标题」偏好的 context(issue #218)。App 持有(useTitlePreferenceState)并下发;
// 阅读器各标题位与设置分区都从这里读,切换后所有位置同一帧统一。
export const TitlePreferenceContext = createContext(null);

const DEFAULT_TITLE_PREFERENCE = Object.freeze({
  preferChinese: true,
  setPreferChinese() {},
});

/** 没有 Provider 时(单独渲染的组件 / 测试)按默认开处理,调用方不必判空。 */
export function useTitlePreference() {
  return useContext(TitlePreferenceContext) || DEFAULT_TITLE_PREFERENCE;
}

// 隐私模式下访问 localStorage 本身可能抛错
function localStore() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * App 用:登录账号 = 服务端偏好(login / session 载荷的 prefer_chinese_titles,缺省视为开),点下即乐观生效;
 * 保存串行:同一时刻只有一个 POST /api/auth/preferences 在途,其间的连点只记下最新选择,上一个请求结束再发——
 * 服务端按发出顺序落库,最后保存的就是读者最后的选择。最后一次也失败才回滚到服务端最后确认的值并提示。
 * 请求在途时换了账号(登出 / 换号登录),旧请求的响应不再写进新账号,也不再继续替旧账号发排队的选择。
 * 游客只存本机浏览器,永不调偏好接口、不写游客共享账号。
 */
export function useTitlePreferenceState({ isGuest, user, onUserUpdated, showToast }) {
  const [guestPrefer, setGuestPrefer] = useState(() => readGuestTitlePreference(localStore()));
  const owner = isGuest ? '' : String(user?.username || '');
  const ownerRef = useRef(owner);
  useLayoutEffect(() => { ownerRef.current = owner; }, [owner]);
  // owner = 这一串选择属于哪个账号;desired = 待发的最新选择;base = 服务端最后确认的值(失败回滚到它)
  const queue = useRef({ owner: '', desired: null, base: true, running: false });
  const preferChinese = isGuest ? guestPrefer : accountTitlePreference(user);

  const drain = useCallback(async () => {
    const q = queue.current;
    q.running = true;
    while (q.desired !== null) {
      const sender = q.owner;
      if (sender !== ownerRef.current) {
        q.desired = null; // 旧账号排着的选择不再发
        break;
      }
      const value = q.desired;
      q.desired = null;
      try {
        const data = await updateOwnPreferences({ prefer_chinese_titles: value });
        if (sender !== ownerRef.current || q.owner !== sender) continue; // 账号已换:旧响应不写新账号
        const saved = typeof data?.prefer_chinese_titles === 'boolean' ? data.prefer_chinese_titles : value;
        q.base = saved;
        // 还有更新的选择在排队时保持乐观显示,由最后一次请求定终值
        if (q.desired === null) onUserUpdated({ prefer_chinese_titles: saved });
      } catch (error) {
        if (sender !== ownerRef.current || q.owner !== sender) continue;
        if (q.desired === null) {
          onUserUpdated({ prefer_chinese_titles: q.base });
          showToast(error?.message || '保存失败，请稍后重试', 'error');
        }
      }
    }
    q.running = false;
  }, [onUserUpdated, showToast]);

  const setPreferChinese = useCallback((next) => {
    const value = Boolean(next);
    if (isGuest) {
      writeGuestTitlePreference(localStore(), value);
      setGuestPrefer(value);
      return;
    }
    const q = queue.current;
    // 没有在途保存(或换了账号)时,眼前的值就是服务端已确认的值
    if (!q.running || q.owner !== owner) q.base = preferChinese;
    q.owner = owner;
    q.desired = value;
    onUserUpdated({ prefer_chinese_titles: value });
    if (!q.running) drain();
  }, [drain, isGuest, onUserUpdated, owner, preferChinese]);

  return useMemo(() => ({ preferChinese, setPreferChinese }), [preferChinese, setPreferChinese]);
}
