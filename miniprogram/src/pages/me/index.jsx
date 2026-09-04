import Taro, { useDidShow } from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import { useEffect, useState } from 'react';
import { logout as apiLogout, changePassword, submitFeedback } from '../../api';
import { getUser, getRuntime, clearSession, subscribeSession } from '../../store/session';
import { useReaderStore, subscribedSources, setFilter, resetReaderStore } from '../../store/reader';
import { bootstrapSession } from '../../features/bootstrap';

/**
 * 我的页(MobileMePage 的翻译):账号卡 / 收藏 / 发现 / 反馈 / 改密 / 关于 / 退出(确认框)。
 * 主题跟随系统(小程序 darkmode),不提供三档切换。个人早报入口按 personal_digest_enabled 显示(P2 页面)。
 */
export default function MePage() {
  const store = useReaderStore();
  const [, tick] = useState(0);
  useEffect(() => subscribeSession(() => tick((n) => n + 1)), []);
  useDidShow(() => { bootstrapSession().catch(() => {}); });
  const user = getUser();
  const runtime = getRuntime();
  const subCount = subscribedSources().length;

  const goFavorites = () => { setFilter('article', { favOnly: true, sourceId: null, search: '' }); Taro.switchTab({ url: '/pages/feed/article/index' }); };
  const onFeedback = () => {
    Taro.showModal({ title: '反馈与建议', editable: true, placeholderText: '写下你的问题或建议…', confirmText: '提交' }).then(async (r) => {
      if (!r.confirm || !r.content || !r.content.trim()) return;
      try { await submitFeedback('suggestion', r.content.trim()); Taro.showToast({ title: '已提交,谢谢', icon: 'none' }); }
      catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
    });
  };
  const onChangePassword = () => {
    Taro.showModal({ title: '当前密码', editable: true, placeholderText: '输入当前密码' }).then((r1) => {
      if (!r1.confirm || !r1.content) return;
      Taro.showModal({ title: '新密码', editable: true, placeholderText: '至少 6 位' }).then(async (r2) => {
        if (!r2.confirm || !r2.content) return;
        try { await changePassword(r1.content, r2.content); Taro.showToast({ title: '密码已修改', icon: 'none' }); }
        catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
      });
    });
  };
  const onLogout = () => {
    Taro.showModal({ title: '退出登录?', content: '退出后需要重新输入账号密码。', confirmText: '退出', confirmColor: '#dc2626' }).then(async (r) => {
      if (!r.confirm) return;
      try { await apiLogout(); } catch { /* 本地清会话即可 */ }
      clearSession(); resetReaderStore();
      Taro.reLaunch({ url: '/pages/login/index' });
    });
  };
  return (
    <View style={{ paddingBottom: 'calc(24px + env(safe-area-inset-bottom))' }}>
      <View className="card-group" style={{ padding: '18px 16px', background: 'var(--rd-pane-bg)' }}>
        <View style={{ fontSize: '18px', fontWeight: 700 }}>{(user && user.username) || '—'}</View>
        <View style={{ fontSize: '12.5px', color: 'var(--dorami-faint)', marginTop: '4px' }}>已订阅 {subCount} 个来源 · 收藏 {store.favoriteIds.size} 篇</View>
      </View>
      <View className="card-group">
        <View className="row" onClick={goFavorites}><Text className="row-label">收藏</Text><Text className="row-chev">›</Text></View>
        <View className="row" onClick={() => Taro.navigateTo({ url: '/pages/discover/index' })}><Text className="row-label">发现更多来源</Text><Text className="row-chev">›</Text></View>
        {runtime && runtime.personal_digest_enabled && (
          <View className="row" onClick={() => Taro.showToast({ title: '个人早报下一版提供', icon: 'none' })}><Text className="row-label">我的早报</Text><Text className="row-meta">即将推出</Text></View>
        )}
      </View>
      <View className="card-group">
        <View className="row" onClick={onFeedback}><Text className="row-label">反馈与建议</Text><Text className="row-chev">›</Text></View>
        <View className="row" onClick={onChangePassword}><Text className="row-label">修改密码</Text><Text className="row-chev">›</Text></View>
        <View className="row"><Text className="row-label">关于</Text><Text className="row-meta">哆啦美 {runtime && runtime.version ? `v${runtime.version}` : ''} · 小程序 0.1.0</Text></View>
      </View>
      <View className="card-group">
        <View className="row is-danger" onClick={onLogout}><Text className="row-label">退出登录</Text></View>
      </View>
    </View>
  );
}
