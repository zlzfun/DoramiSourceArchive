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
// 跃迁转场时序：闪光起始 / 完成切换。切换须落在闪光全亮帧(闪光起始 + 300ms 过渡),
// 界面切换藏在幕布背后,由 App 的抵达幕(app-arrive)接力「白光→主题幕布→显影」——不露硬切。
// 加速曲线是由慢到快的(见 tick 内 ramp),前段是温和的推进感,后段才爆发,故整段稍长。
const WARP_FLASH_MS = 1500;
const WARP_DONE_MS = 1820;

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

// —— 信号星座（三维摄像机）：横向鼠标移动=偏航转头，纵向=镜头推进/后拉。
//    鼠标注入的运动靠摩擦衰减；无输入时保留极缓的环境漂移（缓慢自转 + 星点正弦漂移，
//    星座恒动、永不完全静止）。reduced-motion 时不启动循环，只画静止一帧。
//    返回控制器：kick（失败震镜）/ setVeil（密码隐私幕）/ startWarp（登录成功跃迁）。 ——
function mountConstellation(canvas) {
  const noop = () => {};
  const ctx = canvas.getContext('2d');
  if (!ctx) return { cleanup: noop, kick: noop, setVeil: noop, startWarp: noop };
  const reduce = prefersReducedMotion();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  let W = 0, H = 0, cx = 0, cy = 0, XR = 0, YR = 0;
  let nodes = [];
  let running = false, rafId = 0, last;
  let veil = false;      // 密码聚焦：连线几乎隐去、星点减暗（与蒙眼 logo 同一套哑剧）
  let warp = null;       // 跃迁状态 { t, v }：镜头指数加速推轨，星点拉成径向光轨
  let shake = null;      // 失败震镜 { t, dur, amp, freq }：偏航衰减正弦抖动 + 垂直颠簸

  // 三维投影 + 光标灵敏度
  const FOCAL = 560, ZNEAR = 150, ZFAR = 1650, ZMID = (ZNEAR + ZFAR) / 2, ZSPAN = ZFAR - ZNEAR, ZCLIP = 50;
  const LINK = 150, LINK2 = LINK * LINK;
  // FRIC 定义为「每 1/60 秒」的速度保留率——帧内按 dt 归一化，120Hz 与 60Hz 手感一致
  const KYAW = 0.00085, KDOLLY = 2.4, FRIC = 0.9, YAWMAX = 0.55, VZMAX = 780;
  // 环境漂移:无输入时的缓慢自转角速度 / 星点正弦漂移相位推进速度
  const AMB_YAW = 0.028, AMB_PHASE = 0.16;
  // 星等色温：大多数靛蓝，少数琥珀（收藏星的暖）与青白
  const TONES = [
    { core: '150,146,248', glow: '143,139,246' },
    { core: '244,199,138', glow: '240,182,112' },
    { core: '186,226,248', glow: '176,220,250' },
  ];
  const cam = { yaw: 0, yawV: 0, vz: 0, phase: 0, lx: 0, ly: 0, has: false };
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

  const reseat = (n) => { n.x = (Math.random() * 2 - 1) * XR; n.y = (Math.random() * 2 - 1) * YR; };

  function build() {
    const count = Math.round(Math.min(190, Math.max(90, (W * H) / 13000)));
    nodes = [];
    for (let i = 0; i < count; i++) {
      const roll = Math.random();
      nodes.push({
        x: (Math.random() * 2 - 1) * XR, y: (Math.random() * 2 - 1) * YR, z: ZNEAR + Math.random() * ZSPAN,
        r0: 0.8 + Math.random() * 1.4,
        tone: roll < 0.08 ? 1 : roll < 0.14 ? 2 : 0,
        f1: 0.5 + Math.random() * 1.3, f2: 0.5 + Math.random() * 1.3,
        p1: Math.random() * 6.2832, p2: Math.random() * 6.2832, amp: 10 + Math.random() * 24,
        sx: 0, sy: 0, s: 0, a: 0, vis: false,
      });
    }
  }

  // 单一绘制路径：静止帧（首帧/冻结/reduced-motion）与运动帧同样带辉光，观感无缝
  function paint() {
    // 震镜叠加：偏航摆动 + 垂直颠簸，二次衰减包络（只影响本帧取景，不累积进 cam.yaw）
    // alertK 同步驱动琥珀星的「警报闪红」：震得最猛时最红最亮，随包络一起复原
    let yawOff = 0, cyOff = 0, alertK = 0;
    if (shake) {
      const k = 1 - shake.t / shake.dur;
      const env = k * k;
      yawOff = shake.dir * shake.amp * Math.sin(shake.t * shake.freq * 6.2832) * env;
      cyOff = H * 0.008 * Math.sin(shake.t * shake.freq * 6.2832 * 1.35 + 1.2) * env;
      alertK = k; // 警报闪红用线性衰减:比抖动本身(k²)退得慢,红得更持久醒目
    }
    const cyaw = Math.cos(cam.yaw + yawOff), syaw = Math.sin(cam.yaw + yawOff);
    // 撞击提亮:震镜瞬间连线与星点整体加亮,且临时掀开密码隐私幕
    // (输错密码多发生在密码框聚焦、外围已变暗时——撞击必须冲破暗幕才可感),随包络回落
    const warpFade = warp ? Math.max(0, 1 - warp.t * 1.4) : 1;
    const linkFade = Math.max(veil ? 0.12 : 1, alertK) * (1 + 0.8 * alertK) * warpFade;
    const starFade = Math.max(veil ? 0.45 : 1, alertK) * (1 + 0.35 * alertK);
    const streak = warp ? Math.min(1, warp.v / 9000) : 0;

    ctx.clearRect(0, 0, W, H);

    for (const n of nodes) {
      const wx = n.x + Math.sin(cam.phase * n.f1 + n.p1) * n.amp;
      const wy = n.y + Math.cos(cam.phase * n.f2 + n.p2) * n.amp;
      const zr = n.z - ZMID;
      const xr = wx * cyaw - zr * syaw;
      const zc = wx * syaw + zr * cyaw + ZMID;
      if (zc <= ZCLIP) { n.vis = false; continue; }
      const s = FOCAL / zc;
      n.sx = cx + xr * s; n.sy = cy + cyOff + wy * s; n.s = s;
      n.a = clamp01((zc - ZCLIP) / (ZNEAR * 1.1)) * clamp01((ZFAR - zc) / (ZFAR * 0.55));
      n.vis = true;
    }

    if (linkFade > 0.01) {
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]; if (!a.vis) continue;
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]; if (!b.vis) continue;
          const dx = a.sx - b.sx, dy = a.sy - b.sy, d2 = dx * dx + dy * dy;
          if (d2 >= LINK2) continue;
          const al = (1 - Math.sqrt(d2) / LINK) * 0.42 * Math.min(a.a, b.a) * linkFade;
          if (al < 0.01) continue;
          ctx.strokeStyle = 'rgba(123,118,236,' + al.toFixed(3) + ')';
          ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy); ctx.stroke();
        }
      }
    }

    for (const n of nodes) {
      if (!n.vis || n.a <= 0) continue;
      const tone = TONES[n.tone];
      const R = n.r0 * Math.min(n.s, 2.6);
      const alpha = Math.min(1, (0.35 + 0.6 * n.a) * starFade);

      if (streak > 0.02) {
        // 跃迁光轨：沿离心方向拉线，长度随投影比例与离心距离增长
        const dx = n.sx - cx, dy = n.sy - cy;
        const d = Math.hypot(dx, dy) || 1;
        const len = Math.min(160, n.s * streak * (18 + d * 0.16));
        ctx.strokeStyle = 'rgba(' + tone.core + ',' + (alpha * 0.9).toFixed(3) + ')';
        ctx.lineWidth = Math.max(0.8, R * 0.9);
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(n.sx - (dx / d) * len, n.sy - (dy / d) * len);
        ctx.lineTo(n.sx, n.sy);
        ctx.stroke();
        continue;
      }

      // 失败警报：琥珀星短暂变红变亮（颜色/透明度/半径/辉光都随 alertK 抬升再复原）
      let core = tone.core, glowC = tone.glow, drawA = alpha, drawR = R;
      if (alertK > 0 && n.tone === 1) {
        const r = Math.round(244 + (255 - 244) * alertK);
        const g = Math.round(199 + (58 - 199) * alertK);
        const b = Math.round(138 + (50 - 138) * alertK);
        core = r + ',' + g + ',' + b;
        glowC = '255,72,58';
        drawA = Math.min(1, alpha * (1 + 1.4 * alertK));
        drawR = R * (1 + 0.75 * alertK);
      }
      ctx.fillStyle = 'rgba(' + core + ',' + drawA.toFixed(3) + ')';
      ctx.shadowColor = 'rgba(' + glowC + ',.95)';
      ctx.shadowBlur = (6 + (n.tone === 1 ? 10 * alertK : 0)) * Math.min(n.s, 2);
      ctx.beginPath(); ctx.arc(n.sx, n.sy, drawR, 0, 6.2832); ctx.fill();
    }
    ctx.shadowBlur = 0;
  }

  function tick(now) {
    const dt = Math.min(0.05, last == null ? 0 : (now - last) / 1000); last = now;

    const decay = Math.pow(FRIC, dt * 60);
    cam.yawV *= decay; cam.vz *= decay;
    if (Math.abs(cam.yawV) < 1e-4) cam.yawV = 0;
    if (Math.abs(cam.vz) < 0.4 && !warp) cam.vz = 0;

    if (warp) {
      warp.t += dt;
      // 由慢到快:前 0.9s 温和线性推进(ramp 压低加速度),之后加速度平方放大 + 指数增益爆发
      const ramp = Math.min(1, warp.t / 0.9);
      warp.v = Math.min(30000, (warp.v + (1400 + 15000 * ramp * ramp) * dt) * (1 + 2.2 * ramp * dt));
      cam.vz = warp.v;
    }

    if (shake) {
      shake.t += dt;
      if (shake.t >= shake.dur) shake = null;
    }

    // 环境漂移:输入速度之外恒有一份极缓的自转与相位推进——星座永不完全静止
    cam.yaw += (cam.yawV + AMB_YAW) * dt;
    cam.phase += (Math.abs(cam.yawV) * 1.4 + Math.abs(cam.vz) * 0.0016 + AMB_PHASE) * dt;

    for (const n of nodes) {
      n.z -= cam.vz * dt;
      if (n.z < ZNEAR) { n.z += ZSPAN; reseat(n); } else if (n.z > ZFAR) { n.z -= ZSPAN; reseat(n); }
    }

    paint();

    // 环境漂移恒动,不再冻结停帧(reduced-motion 时整个循环从未启动;后台标签页 rAF 自动挂起)
    rafId = requestAnimationFrame(tick);
  }

  function wake() {
    if (running || reduce) return;
    running = true; last = undefined; rafId = requestAnimationFrame(tick);
  }

  function resize() {
    W = window.innerWidth; H = window.innerHeight; cx = W / 2; cy = H / 2; XR = W * 0.95; YR = H * 0.95;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
    paint();
  }

  const onMove = (e) => {
    if (cam.has && !warp) {
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
  wake(); // 环境漂移:挂载即启动恒动循环(reduce 时 wake 内部直接返回,保持静止一帧)

  return {
    cleanup() {
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerleave', onLeave);
      if (rafId) cancelAnimationFrame(rafId);
    },
    // 登录失败：飞船被撞偏——低频约一个回摆(衰减正弦) + 同向残余冲量,
    // 冲量经摩擦积分(v0/6.32)为约 0.24 rad ≈ 14° 的「永久」航向偏移:
    // 震定后镜头明确望向新方向,星座构图整体改变
    kick() {
      if (reduce) return;
      const dir = Math.random() < 0.5 ? -1 : 1;
      shake = { t: 0, dur: 0.85, amp: 0.07, freq: 1.6, dir };
      cam.yawV += dir * 1.5;
      wake();
    },
    setVeil(v) {
      veil = v;
      if (!running) paint();
    },
    // 登录成功：镜头指数加速推轨，星点拉成光轨（组件侧按时序叠加闪光、完成切换）
    startWarp() {
      if (reduce) return;
      warp = { t: 0, v: Math.max(cam.vz, 320) };
      wake();
    },
  };
}

export default function LoginScreen({ logoError, onLogoError, onLogin, onLoginComplete }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordFocused, setPasswordFocused] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 'title' = 第一幕（居中强调）；'ready' = 第二幕（登录卡浮现）
  const [phase, setPhase] = useState(() => (prefersReducedMotion() ? 'ready' : 'title'));
  // 第三幕：标题变幻为品牌名
  const [branded, setBranded] = useState(false);
  // 终幕：登录成功的跃迁转场（前景退场 + 星点光轨 + 闪光）
  const [warping, setWarping] = useState(false);
  const [flashing, setFlashing] = useState(false);
  // 登录失败：卡片摇头（动画结束自行复位）
  const [shaking, setShaking] = useState(false);
  const netRef = useRef(null);
  const netApiRef = useRef(null);
  const cardRef = useRef(null);
  const warpTimersRef = useRef([]);

  // 背景：三维摄像机星座（挂载即绘制，随鼠标运动变幻）
  useEffect(() => {
    if (!netRef.current) return undefined;
    const api = mountConstellation(netRef.current);
    netApiRef.current = api;
    return () => { api.cleanup(); netApiRef.current = null; };
  }, []);

  // 跃迁计时器统一清理（组件卸载时）
  useEffect(() => () => { warpTimersRef.current.forEach(clearTimeout); }, []);

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
  // reduced-motion 直接呈现品牌名终态，不让用户干等
  useEffect(() => {
    if (phase !== 'ready' || branded) return undefined;
    if (prefersReducedMotion()) { setBranded(true); return undefined; }
    const timer = setTimeout(() => setBranded(true), BRAND_DELAY_MS);
    return () => clearTimeout(timer);
  }, [phase, branded]);

  // 密码聚焦：logo 蒙眼 + 星座隐私幕（连线隐去、星点减暗）同一套哑剧
  useEffect(() => {
    netApiRef.current?.setVeil(passwordFocused);
  }, [passwordFocused]);

  // 登录卡 3D 倾斜跟随：透视容器内 ±3° 内轻微转动，高光线/靛晕反向微移
  useEffect(() => {
    if (phase !== 'ready' || warping || prefersReducedMotion()) return undefined;
    const onMove = (e) => {
      const card = cardRef.current;
      if (!card) return;
      const r = card.getBoundingClientRect();
      if (!r.width) return;
      const nx = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (r.width * 1.4)));
      const ny = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / (r.height * 1.4)));
      card.style.setProperty('--tilt-x', (nx * 3).toFixed(2));
      card.style.setProperty('--tilt-y', (-ny * 2.4).toFixed(2));
    };
    const reset = () => {
      cardRef.current?.style.setProperty('--tilt-x', '0');
      cardRef.current?.style.setProperty('--tilt-y', '0');
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerleave', reset);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerleave', reset);
      reset();
    };
  }, [phase, warping]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (isSubmitting || warping) return;
    setError('');
    setIsSubmitting(true);
    try {
      const session = await onLogin(username, password);
      // 成功：播跃迁转场后再切主界面（reduced-motion 立即切换）
      if (prefersReducedMotion() || !netApiRef.current) {
        onLoginComplete?.(session);
        return;
      }
      setWarping(true);
      netApiRef.current.startWarp();
      warpTimersRef.current.push(
        setTimeout(() => setFlashing(true), WARP_FLASH_MS),
        setTimeout(() => onLoginComplete?.(session, { cinematic: true }), WARP_DONE_MS),
      );
    } catch (err) {
      setError(err.message || '登录失败');
      setIsSubmitting(false);
      // 失败的物理感：星空震一下 + 卡片摇头
      netApiRef.current?.kick();
      setShaking(true);
    }
  };

  return (
    <main className={`auth-stage is-${phase}${warping ? ' is-warping' : ''}`}>
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
          <section
            className={`auth-card${shaking ? ' is-shaking' : ''}`}
            ref={cardRef}
            onAnimationEnd={() => setShaking(false)}
          >
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

              <button type="submit" disabled={isSubmitting || warping} className="auth-submit">
                <span className="auth-submit-gloss" aria-hidden="true" />
                {isSubmitting ? <Loader2 className="animate-spin" /> : null}
                <span>{warping ? '正在进入' : isSubmitting ? '正在登录' : '登录'}</span>
                {!isSubmitting ? <ArrowRight /> : null}
              </button>

              <p className="auth-foot">受 HMAC 会话保护 · 哆啦美</p>
            </form>
          </section>
        </div>
      </div>

      <p className="auth-hint">按 <b>任意键</b> 进入</p>

      {/* 跃迁闪光：登录成功转场的最后一拍 */}
      <div className={`auth-warp-flash${flashing ? ' is-on' : ''}`} aria-hidden="true" />
    </main>
  );
}
