import { useState } from 'react';
import { Bot, KeyRound, Loader2, LogIn, User } from 'lucide-react';
import { LOGO_PATH } from '../config';

function LoginBrandMark({ logoError, onLogoError }) {
  return !logoError ? (
    <img src={LOGO_PATH} alt="Logo" className="h-14 w-14 rounded-[14px] object-contain shadow-sm" onError={onLogoError} />
  ) : (
    <div className="brand-mark flex h-14 w-14 items-center justify-center rounded-[14px]">
      <Bot className="h-7 w-7 text-white" />
    </div>
  );
}

export default function LoginScreen({ logoError, onLogoError, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(err.message || '登录失败');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="login-screen flex min-h-screen items-center justify-center px-5 py-10">
      <section className="login-panel grid w-full max-w-[980px] overflow-hidden rounded-[22px] border border-indigo-100/80 bg-white/86 shadow-[0_30px_80px_rgba(79,70,229,0.16)] backdrop-blur-xl lg:grid-cols-[1.02fr_0.98fr]">
        <div className="login-brief relative min-h-[360px] overflow-hidden bg-slate-950 p-8 text-white sm:p-10">
          <div className="relative z-10 flex h-full flex-col justify-between">
            <div>
              <LoginBrandMark logoError={logoError} onLogoError={onLogoError} />
              <h1 className="mt-7 text-[30px] font-black leading-tight sm:text-[36px]">哆啦美·归档中枢</h1>
              <p className="mt-3 max-w-[420px] text-sm font-semibold leading-7 text-indigo-100/86">
                AI 资讯抓取、归档与集成控制台。
              </p>
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col justify-center p-8 sm:p-10">
          <div className="mb-8">
            <p className="text-xs font-black uppercase tracking-[0.18em] text-indigo-500">Archive Access</p>
            <h2 className="mt-3 text-[26px] font-black leading-tight text-slate-950">登录管理台</h2>
          </div>

          <label className="mb-5 block">
            <span className="mb-2 block text-sm font-extrabold text-slate-700">账号</span>
            <span className="login-input-wrap flex items-center gap-3 rounded-[12px] border border-indigo-100 bg-white px-4">
              <User className="h-4.5 w-4.5 shrink-0 text-indigo-500" />
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                className="h-12 min-w-0 flex-1 border-0 bg-transparent text-sm font-bold text-slate-900 outline-none placeholder:text-slate-400"
                placeholder="输入登录账号"
              />
            </span>
          </label>

          <label className="mb-4 block">
            <span className="mb-2 block text-sm font-extrabold text-slate-700">密码</span>
            <span className="login-input-wrap flex items-center gap-3 rounded-[12px] border border-indigo-100 bg-white px-4">
              <KeyRound className="h-4.5 w-4.5 shrink-0 text-indigo-500" />
              <input
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                autoComplete="current-password"
                className="h-12 min-w-0 flex-1 border-0 bg-transparent text-sm font-bold text-slate-900 outline-none placeholder:text-slate-400"
                placeholder="输入登录密码"
              />
            </span>
          </label>

          <div className="min-h-7">
            {error && <p className="text-sm font-bold text-rose-600">{error}</p>}
          </div>

          <button type="submit" disabled={isSubmitting} className="action-button action-button-primary mt-3 h-12 w-full text-[15px]">
            {isSubmitting ? <Loader2 className="animate-spin" /> : <LogIn />}
            {isSubmitting ? '正在登录' : '登录'}
          </button>
        </form>
      </section>
    </main>
  );
}
