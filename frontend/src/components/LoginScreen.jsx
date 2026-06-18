import { useEffect, useRef, useState } from 'react';
import { Archive, ArrowRight, Bot, KeyRound, Loader2, Radar, Sparkles, User } from 'lucide-react';
import BrandLogoImage from './BrandLogoImage';
import { LOGO_COVER_EYES_PATH, LOGO_COVER_EYES_SRC_SET } from '../config';

function LoginBrandMark({ logoError, onLogoError, covering }) {
  return (
    <span className={`auth-logo-shell${covering ? ' is-covering' : ''}`}>
      <span className="auth-logo-halo" aria-hidden="true" />
      {!logoError ? (
        <>
          <BrandLogoImage
            displaySize={72}
            alt="哆啦美"
            className="auth-logo auth-logo-base"
            onError={onLogoError}
          />
          {/* 蒙眼彩蛋：密码框聚焦时叠加淡入，失焦淡出 */}
          <img
            src={LOGO_COVER_EYES_PATH}
            srcSet={LOGO_COVER_EYES_SRC_SET}
            sizes="72px"
            alt=""
            aria-hidden="true"
            width={72}
            height={72}
            decoding="async"
            className="auth-logo auth-logo-cover"
          />
        </>
      ) : (
        <span className="auth-logo auth-logo-fallback">
          <Bot className="h-7 w-7 text-white" />
        </span>
      )}
    </span>
  );
}

// 标题逐词浮现：保留换行结构，每个片段独立错峰入场
const TITLE_LINES = [
  [{ t: '让 ', delay: 560 }, { t: 'AI ', delay: 750 }, { t: '资讯', delay: 940 }],
  [{ t: '有处可栖。', accent: true, delay: 1130 }],
];

function seededFraction(index, salt) {
  const value = Math.sin((index + 1) * (salt + 11) * 91.731) * 43758.5453;
  return value - Math.floor(value);
}

const PARTICLES = Array.from({ length: 18 }, (_, index) => ({
  id: index,
  left: seededFraction(index, 1) * 100,
  top: seededFraction(index, 2) * 100,
  size: 1.5 + seededFraction(index, 3) * 2.5,
  delay: seededFraction(index, 4) * 8,
  duration: 9 + seededFraction(index, 5) * 10,
  depth: 0.3 + seededFraction(index, 6) * 1.4,
}));

// 第一幕「标题卡」停留时长，之后进入第二幕（文字归位 + 登录卡浮现）
const TITLE_HOLD_MS = 3600;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export default function LoginScreen({ logoError, onLogoError, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordFocused, setPasswordFocused] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 'title' = 第一幕（仅文字）；'ready' = 第二幕（登录卡浮现）
  const [phase, setPhase] = useState(() => (prefersReducedMotion() ? 'ready' : 'title'));
  const stageRef = useRef(null);

  // 第一幕 → 第二幕：定时推进；任意点击/按键可提前跳过开场
  useEffect(() => {
    if (phase !== 'title') return undefined;
    const advance = () => setPhase('ready');
    const timer = setTimeout(advance, TITLE_HOLD_MS);
    window.addEventListener('pointerdown', advance, { once: true });
    window.addEventListener('keydown', advance, { once: true });
    return () => {
      clearTimeout(timer);
      window.removeEventListener('pointerdown', advance);
      window.removeEventListener('keydown', advance);
    };
  }, [phase]);

  // 鼠标：背景景深视差（--px/--py，归一化）+ 光标聚光坐标（--mx/--my，像素）
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || prefersReducedMotion()) return undefined;

    let frame = 0;
    const handleMove = (event) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const rect = stage.getBoundingClientRect();
        const mx = event.clientX - rect.left;
        const my = event.clientY - rect.top;
        stage.style.setProperty('--px', (mx / rect.width - 0.5).toFixed(4));
        stage.style.setProperty('--py', (my / rect.height - 0.5).toFixed(4));
        stage.style.setProperty('--mx', `${mx.toFixed(1)}px`);
        stage.style.setProperty('--my', `${my.toFixed(1)}px`);
        stage.style.setProperty('--spot', '1');
      });
    };
    const reset = () => {
      stage.style.setProperty('--px', '0');
      stage.style.setProperty('--py', '0');
      stage.style.setProperty('--spot', '0');
    };
    stage.addEventListener('pointermove', handleMove);
    stage.addEventListener('pointerenter', () => stage.style.setProperty('--spot', '1'));
    stage.addEventListener('pointerleave', reset);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      stage.removeEventListener('pointermove', handleMove);
      stage.removeEventListener('pointerleave', reset);
    };
  }, []);

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
    <main className={`auth-stage is-${phase}`} ref={stageRef}>
      <div className="auth-bg" aria-hidden="true">
        <div className="auth-orb auth-orb-1">
          <span className="auth-orb-glow" />
        </div>
        <div className="auth-orb auth-orb-2">
          <span className="auth-orb-glow" />
        </div>
        <div className="auth-orb auth-orb-3">
          <span className="auth-orb-glow" />
        </div>
        <span className="auth-grid" />
        <span className="auth-grid-spot" />
        <span className="auth-beam" />
        <div className="auth-meteors">
          <span className="auth-meteor auth-meteor-1" />
          <span className="auth-meteor auth-meteor-2" />
          <span className="auth-meteor auth-meteor-3" />
        </div>
        <div className="auth-particles">
          {PARTICLES.map((p) => (
            <span
              key={p.id}
              className="auth-particle"
              style={{
                left: `${p.left}%`,
                top: `${p.top}%`,
                width: `${p.size}px`,
                height: `${p.size}px`,
                '--pd': p.depth,
                animationDelay: `${p.delay}s`,
                animationDuration: `${p.duration}s`,
              }}
            />
          ))}
        </div>
        <span className="auth-grain" />
        <span className="auth-spotlight" />
        <span className="auth-vignette" />
        <span className="auth-sweep" />
      </div>

      <div className="auth-shell">
        <section className="auth-hero">
          <div className="auth-brandrow auth-rise" style={{ '--d': '120ms' }}>
            <LoginBrandMark logoError={logoError} onLogoError={onLogoError} covering={passwordFocused} />
            <span className="auth-wordmark">DORAMI</span>
          </div>

          <p className="auth-eyebrow auth-rise" style={{ '--d': '260ms' }}>
            AI 资讯 · 聚合 · 订阅 · 检索
          </p>

          <h1 className="auth-title">
            {TITLE_LINES.map((line, li) => (
              <span className="auth-title-line" key={li}>
                {line.map((word, wi) => {
                  return (
                    <span
                      key={wi}
                      className={`auth-word${word.accent ? ' auth-title-accent' : ''}`}
                      style={{ '--d': `${word.delay}ms` }}
                    >
                      {word.t}
                    </span>
                  );
                })}
              </span>
            ))}
          </h1>

          <p className="auth-lede auth-rise" style={{ '--d': '900ms' }}>
            汇聚多源 AI 资讯，订阅、检索、随手可读——尽在一处。
          </p>

          <ul className="auth-feats">
            <li>
              <Radar />
              多源聚合
            </li>
            <li>
              <Archive />
              全文留存
            </li>
            <li>
              <Sparkles />
              语义检索
            </li>
          </ul>
        </section>

        <div className="auth-card-slot">
          <section className="auth-card">
            <span className="auth-card-ring" aria-hidden="true" />
            <span className="auth-card-glow" aria-hidden="true" />
            <form onSubmit={handleSubmit} className="auth-form">
              <p className="auth-card-eyebrow">ACCOUNT ACCESS</p>
              <h2 className="auth-card-title">欢迎回来</h2>
              <p className="auth-card-sub">登录账号，继续浏览你的 AI 资讯。</p>

              <label className="auth-field">
                <span className="auth-field-label">账号</span>
                <span className="auth-input-wrap">
                  <User className="auth-input-icon" />
                  <input
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    autoComplete="username"
                    className="auth-input"
                    placeholder="输入登录账号"
                  />
                </span>
              </label>

              <label className="auth-field">
                <span className="auth-field-label">密码</span>
                <span className="auth-input-wrap">
                  <KeyRound className="auth-input-icon" />
                  <input
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    onFocus={() => setPasswordFocused(true)}
                    onBlur={() => setPasswordFocused(false)}
                    type="password"
                    autoComplete="current-password"
                    className="auth-input"
                    placeholder="输入登录密码"
                  />
                </span>
              </label>

              <div className="auth-error-slot">
                {error && <p className="auth-error">{error}</p>}
              </div>

              <button type="submit" disabled={isSubmitting} className="auth-submit">
                <span className="auth-submit-gloss" aria-hidden="true" />
                {isSubmitting ? <Loader2 className="animate-spin" /> : null}
                <span>{isSubmitting ? '正在登录' : '登录'}</span>
                {!isSubmitting ? <ArrowRight /> : null}
              </button>

              <p className="auth-foot">受 HMAC 会话保护 · 哆啦美</p>
            </form>
          </section>
        </div>
      </div>
    </main>
  );
}
