import { useCallback, useEffect, useRef, useState } from 'react';
import { Archive, ArrowRight, Bot, KeyRound, Loader2, Radar, Sparkles, User } from 'lucide-react';
import BrandLogoImage from './BrandLogoImage';
import { LOGO_COVER_EYES_PATH, LOGO_COVER_EYES_SRC_SET } from '../config';

function LoginBrandMark({ logoError, onLogoError, covering }) {
  // 蒙眼彩蛋图（30KB）移出首屏关键路径，但改为「基图加载完成后、浏览器空闲时预取」，
  // 而非「聚焦密码框才下载」——后者在海外高延迟节点会让闭眼滞后甚至来不及呈现。
  // 预取排在 hero 头像之后、不与首屏关键资源争抢带宽，聚焦时闭眼即时呈现。
  const [coverArmed, setCoverArmed] = useState(false);
  const idleRef = useRef(0);
  // 聚焦即需要：兜底，若预取尚未触发则立刻挂载
  useEffect(() => { if (covering) setCoverArmed(true); }, [covering]);
  const warmCover = useCallback(() => {
    if (idleRef.current || coverArmed) return;
    const w = window;
    idleRef.current = w.requestIdleCallback
      ? w.requestIdleCallback(() => setCoverArmed(true), { timeout: 2000 })
      : setTimeout(() => setCoverArmed(true), 600);
  }, [coverArmed]);
  useEffect(() => () => {
    const w = window;
    if (!idleRef.current) return;
    if (w.cancelIdleCallback) w.cancelIdleCallback(idleRef.current);
    else clearTimeout(idleRef.current);
  }, []);
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
            fadeInOnLoad
            onLoaded={warmCover}
          />
          {/* 蒙眼彩蛋：密码框聚焦时叠加淡入，失焦淡出（首次聚焦后才挂载） */}
          {coverArmed && (
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
          )}
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

// 第三幕：标题左侧稳定若干秒后，变幻为品牌名
const BRAND_LINES = [
  { accent: true, chars: ['哆', '啦', '美'] },
  { accent: false, chars: ['AI', '资', '讯', '平', '台'] },
];

// 第一幕「标题卡」停留时长，之后进入第二幕（文字归位 + 登录卡浮现）
const TITLE_HOLD_MS = 3600;
// 第二幕稳定后 → 第三幕（标题变幻为品牌名）的间隔
const BRAND_DELAY_MS = 2800;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// —— 信号星座（三维摄像机）：横向鼠标移动=偏航转头，纵向=镜头推进/后拉。
//    运动全部由鼠标移动量注入、靠摩擦衰减，手停即冻结（并停掉 RAF）。 ——
function mountConstellation(canvas) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return () => {};
  const reduce = prefersReducedMotion();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  let W = 0, H = 0, cx = 0, cy = 0, XR = 0, YR = 0;
  let nodes = [];
  let running = false, rafId = 0, last;

  // 三维投影 + 光标灵敏度
  const FOCAL = 560, ZNEAR = 150, ZFAR = 1650, ZMID = (ZNEAR + ZFAR) / 2, ZSPAN = ZFAR - ZNEAR, ZCLIP = 50;
  const LINK = 150, LINK2 = LINK * LINK;
  const KYAW = 0.00085, KDOLLY = 2.4, FRIC = 0.9, YAWMAX = 0.55, VZMAX = 780;
  const cam = { yaw: 0, yawV: 0, vz: 0, phase: 0, lx: 0, ly: 0, has: false };
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

  const reseat = (n) => { n.x = (Math.random() * 2 - 1) * XR; n.y = (Math.random() * 2 - 1) * YR; };

  function build() {
    const count = Math.round(Math.min(190, Math.max(90, (W * H) / 13000)));
    nodes = [];
    for (let i = 0; i < count; i++) {
      nodes.push({
        x: (Math.random() * 2 - 1) * XR, y: (Math.random() * 2 - 1) * YR, z: ZNEAR + Math.random() * ZSPAN,
        r0: 0.8 + Math.random() * 1.4,
        f1: 0.5 + Math.random() * 1.3, f2: 0.5 + Math.random() * 1.3,
        p1: Math.random() * 6.2832, p2: Math.random() * 6.2832, amp: 10 + Math.random() * 24,
        sx: 0, sy: 0, s: 0, a: 0, vis: false,
      });
    }
  }

  function resize() {
    W = window.innerWidth; H = window.innerHeight; cx = W / 2; cy = H / 2; XR = W * 0.95; YR = H * 0.95;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
    drawStatic();
  }

  function drawStatic() {
    // 静止一帧（yaw=0、当前深度）：reduced-motion / 首帧 / 冻结态
    for (const n of nodes) {
      const zc = n.z; if (zc <= ZCLIP) { n.vis = false; continue; }
      const s = FOCAL / zc; n.sx = cx + n.x * s; n.sy = cy + n.y * s; n.s = s;
      n.a = clamp01((zc - ZCLIP) / (ZNEAR * 1.1)) * clamp01((ZFAR - zc) / (ZFAR * 0.55)); n.vis = true;
    }
    ctx.clearRect(0, 0, W, H); ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]; if (!a.vis) continue;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]; if (!b.vis) continue;
        const dx = a.sx - b.sx, dy = a.sy - b.sy, d2 = dx * dx + dy * dy;
        if (d2 < LINK2) {
          const al = (1 - Math.sqrt(d2) / LINK) * 0.4 * Math.min(a.a, b.a);
          ctx.strokeStyle = 'rgba(123,118,236,' + al.toFixed(3) + ')';
          ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
        }
      }
    }
    for (const n of nodes) {
      if (!n.vis) continue;
      ctx.fillStyle = 'rgba(150,146,248,' + (0.35 + 0.6 * n.a).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(n.sx, n.sy, n.r0 * Math.min(n.s, 2.6), 0, 6.28); ctx.fill();
    }
  }

  function tick(now) {
    const dt = Math.min(0.05, last == null ? 0 : (now - last) / 1000); last = now;

    cam.yawV *= FRIC; cam.vz *= FRIC;
    if (Math.abs(cam.yawV) < 1e-4) cam.yawV = 0;
    if (Math.abs(cam.vz) < 0.4) cam.vz = 0;
    cam.yaw += cam.yawV * dt;
    cam.phase += (Math.abs(cam.yawV) * 1.4 + Math.abs(cam.vz) * 0.0016) * dt;

    const cyaw = Math.cos(cam.yaw), syaw = Math.sin(cam.yaw);
    ctx.clearRect(0, 0, W, H);

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      n.z -= cam.vz * dt;
      if (n.z < ZNEAR) { n.z += ZSPAN; reseat(n); } else if (n.z > ZFAR) { n.z -= ZSPAN; reseat(n); }
      const wx = n.x + Math.sin(cam.phase * n.f1 + n.p1) * n.amp;
      const wy = n.y + Math.cos(cam.phase * n.f2 + n.p2) * n.amp;
      const zr = n.z - ZMID;
      const xr = wx * cyaw - zr * syaw;
      const zc = wx * syaw + zr * cyaw + ZMID;
      if (zc <= ZCLIP) { n.vis = false; continue; }
      const s = FOCAL / zc;
      n.sx = cx + xr * s; n.sy = cy + wy * s; n.s = s;
      n.a = clamp01((zc - ZCLIP) / (ZNEAR * 1.1)) * clamp01((ZFAR - zc) / (ZFAR * 0.55));
      n.vis = true;
    }

    ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]; if (!a.vis) continue;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]; if (!b.vis) continue;
        const dx = a.sx - b.sx, dy = a.sy - b.sy, d2 = dx * dx + dy * dy;
        if (d2 < LINK2) {
          const al = (1 - Math.sqrt(d2) / LINK) * 0.42 * Math.min(a.a, b.a);
          if (al < 0.01) continue;
          ctx.strokeStyle = 'rgba(123,118,236,' + al.toFixed(3) + ')';
          ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
        }
      }
    }
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i]; if (!n.vis || n.a <= 0) continue;
      const R = n.r0 * Math.min(n.s, 2.6);
      ctx.fillStyle = 'rgba(150,146,248,' + (0.35 + 0.6 * n.a).toFixed(3) + ')';
      ctx.shadowColor = 'rgba(143,139,246,.9)'; ctx.shadowBlur = 6 * Math.min(n.s, 2);
      ctx.beginPath(); ctx.arc(n.sx, n.sy, R, 0, 6.2832); ctx.fill();
    }
    ctx.shadowBlur = 0;

    if (cam.yawV === 0 && cam.vz === 0) { running = false; rafId = 0; }
    else rafId = requestAnimationFrame(tick);
  }

  function wake() {
    if (running || reduce) return;
    running = true; last = undefined; rafId = requestAnimationFrame(tick);
  }

  const onMove = (e) => {
    if (cam.has) {
      const dx = e.clientX - cam.lx, dy = e.clientY - cam.ly;
      cam.yawV = Math.max(-YAWMAX, Math.min(YAWMAX, cam.yawV + dx * KYAW));
      cam.vz = Math.max(-VZMAX, Math.min(VZMAX, cam.vz - dy * KDOLLY)); // 上移(dy<0)→推进
    }
    cam.lx = e.clientX; cam.ly = e.clientY; cam.has = true;
    wake();
  };
  const onLeave = () => { cam.has = false; };

  resize();
  window.addEventListener('resize', resize);
  if (!reduce) {
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerleave', onLeave);
  }

  return () => {
    window.removeEventListener('resize', resize);
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerleave', onLeave);
    if (rafId) cancelAnimationFrame(rafId);
  };
}

export default function LoginScreen({ logoError, onLogoError, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordFocused, setPasswordFocused] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 'title' = 第一幕（居中强调）；'ready' = 第二幕（登录卡浮现）
  const [phase, setPhase] = useState(() => (prefersReducedMotion() ? 'ready' : 'title'));
  // 第三幕：标题变幻为品牌名
  const [branded, setBranded] = useState(false);
  const netRef = useRef(null);

  // 背景：三维摄像机星座（挂载即绘制，随鼠标运动变幻）
  useEffect(() => {
    if (!netRef.current) return undefined;
    return mountConstellation(netRef.current);
  }, []);

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

  // 第二幕稳定后 → 隔若干秒触发第三幕：主标题变幻为品牌名
  useEffect(() => {
    if (phase !== 'ready' || branded) return undefined;
    const timer = setTimeout(() => setBranded(true), BRAND_DELAY_MS);
    return () => clearTimeout(timer);
  }, [phase, branded]);

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
    <main className={`auth-stage is-${phase}`}>
      <div className="auth-scene-inner" aria-hidden="true">
        <div className="auth-bg" />
        <canvas className="auth-net" ref={netRef} />
        <span className="auth-grain" />
        <span className="auth-vignette" />
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

          <h1
            className={`auth-title auth-title-swap${branded ? ' is-branded' : ''}`}
            aria-label="哆啦美 · AI 资讯平台——让 AI 资讯有处可栖"
          >
            {/* 原标题：稳定后整体轻抬淡出 */}
            <span className="auth-title-orig" aria-hidden="true">
              {TITLE_LINES.map((line, li) => (
                <span className="auth-title-line" key={li}>
                  {line.map((word, wi) => (
                    <span
                      key={wi}
                      className={`auth-word${word.accent ? ' auth-title-accent' : ''}`}
                      style={{ '--d': `${word.delay}ms` }}
                    >
                      {word.t}
                    </span>
                  ))}
                </span>
              ))}
            </span>
            {/* 品牌名：逐字自下缓升、模糊转清 */}
            <span className="auth-title-brand" aria-hidden="true">
              {(() => {
                let k = 0;
                return BRAND_LINES.map((line, li) => (
                  <span className="auth-title-line" key={li}>
                    {line.chars.map((ch) => {
                      const idx = k++;
                      return (
                        <span
                          key={idx}
                          className={`auth-brandchar${line.accent ? ' is-accent' : ''}`}
                          style={{ '--d': `${260 + idx * 55}ms` }}
                        >
                          {ch}
                        </span>
                      );
                    })}
                  </span>
                ));
              })()}
            </span>
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

      <p className="auth-hint">按 <b>任意键</b> 进入</p>
    </main>
  );
}
