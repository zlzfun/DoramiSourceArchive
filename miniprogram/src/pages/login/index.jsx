import { useState } from 'react';
import Taro, { useRouter } from '@tarojs/taro';
import { View, Text, Input } from '@tarojs/components';
import { login } from '../../api';
import { setSession } from '../../store/session';
import { resetReaderStore } from '../../store/reader';
import { bootstrapSession } from '../../features/bootstrap';
import './index.scss';

const TAB_PAGES = ['/pages/feed/article/index', '/pages/feed/podcast/index', '/pages/feed/bulletin/index', '/pages/feed/social/index', '/pages/me/index'];

/** 登录门:账号密码 → session_token(Bearer)→ 引导会话 → 回 redirect(转发卡片落地)或文章流。 */
export default function LoginPage() {
  const { params } = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    if (!username.trim() || !password) { setErr('请输入账号与密码'); return; }
    setBusy(true); setErr('');
    try {
      const res = await login(username.trim(), password);
      if (!res || !res.session_token) throw new Error('后端未返回会话令牌(请升级后端到含小程序载体的版本)');
      setSession(res.session_token, res.user);
      resetReaderStore();
      await bootstrapSession({ force: true });
      const redirect = params.redirect ? decodeURIComponent(params.redirect) : '';
      const bare = redirect.split('?')[0];
      if (redirect && TAB_PAGES.includes(bare)) Taro.switchTab({ url: bare });
      else if (redirect && redirect.startsWith('/pages/')) Taro.redirectTo({ url: redirect }).catch(() => Taro.switchTab({ url: '/pages/feed/article/index' }));
      else Taro.switchTab({ url: '/pages/feed/article/index' });
    } catch (e) {
      setErr(e.message || '登录失败');
    } finally { setBusy(false); }
  };

  return (
    <View className="login">
      <View className="login-brand">哆啦美</View>
      <View className="login-sub">AI 资讯阅读器 · 请使用管理员为你创建的账号登录</View>
      <Input className="login-field" placeholder="账号" value={username} onInput={(e) => setUsername(e.detail.value)} confirmType="next" />
      <Input className="login-field" placeholder="密码" password value={password} onInput={(e) => setPassword(e.detail.value)} confirmType="go" onConfirm={submit} />
      <View className="login-err"><Text>{err}</Text></View>
      <View className={`btn is-primary is-block login-btn ${busy ? 'is-disabled' : ''}`} onClick={busy ? undefined : submit}>{busy ? '登录中…' : '登录'}</View>
      <View className="login-hint">没有账号?请联系管理员开通。</View>
    </View>
  );
}
