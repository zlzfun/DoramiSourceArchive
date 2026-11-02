import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Archive, ArrowLeft, ArrowRight, Bot, KeyRound, Loader2, MessageSquareText, Radar, User } from 'lucide-react';
import BrandLogoImage from './BrandLogoImage';
import { LOGO_COVER_EYES_PATH, LOGO_COVER_EYES_SRC_SET } from '../config';
import { mountDeepSpace } from './login/deepSpace';

// 深空剧场 · 重制(issue #214)。主题沿用 v3.13「深空剧场」:三维星座 + 左题词右玻璃卡 + 三幕演出 + 蒙眼彩蛋 +
// 成功跃迁;背景改为 WebGL 场景引擎(login/deepSpace.js),演出按「跃迁抵达 → 登录卡浮现 → 品牌成形 →
// 跃迁离场」首尾呼应重新编排。剧本见 docs/design/dorami-login-constellation-script.md(v2 节)。
//
// 品牌豁免(2026-07-25 拍板,沿用):登录演出不随「减少动效」降级,连应用内开关也不认;
// index.css 的全局降级规则放行 .auth-stage / .app-arrive 子树。
//
// 手机竖屏是另一种形态(不是桌面卡的缩小版):上半屏是品牌区,登录表单是贴底的玻璃底页,按钮落在拇指区,
// 首屏不滚动;触屏聚焦输入框(软键盘弹起)时品牌区收成一枚 logo、底页上提贴住它;离场时底页沉下、
// logo 飞到屏幕中心并没入跃迁的消失点。

// 触屏(无悬停的粗指针):软键盘会占掉下半屏
const COARSE_QUERY = '(hover: none) and (pointer: coarse)';
const isCoarse = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  && window.matchMedia(COARSE_QUERY).matches;
// 失焦后稍等再展开:账号框 → 密码框的切换、点按钮的那一下都不该让版式来回跳
const KBD_RELEASE_MS = 260;

function LoginBrandMark({ logoError, onLogoError, covering }) {
  // 蒙眼彩蛋图移出首屏关键路径:基图加载完成后、浏览器空闲时预取(而非聚焦密码框才下载——
  // 海外高延迟节点会让闭眼滞后甚至来不及呈现);聚焦时若尚未预取则立即挂载兜底。
  const [coverArmed, setCoverArmed] = useState(false);
  const idleRef = useRef(0);
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
      {/* 轨道环:一颗小卫星绕 logo 运行 */}
      <span className="auth-logo-orbit" aria-hidden="true"><i /></span>
      {!logoError ? (
        <>
          <BrandLogoImage
            displaySize={64}
            alt="哆啦美"
            className="auth-logo auth-logo-base"
            onError={onLogoError}
            fadeInOnLoad
            onLoaded={warmCover}
          />
          {coverArmed && (
            <img
              src={LOGO_COVER_EYES_PATH}
              srcSet={LOGO_COVER_EYES_SRC_SET}
              sizes="64px"
              alt=""
              aria-hidden="true"
              width={64}
              height={64}
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

// 第一幕主标题:逐字显影(拉丁词整体一个单元,中西文之间留细空)。强调行逐字取渐变色阶。
const ACCENT_RAMP = ['#e2deff', '#c9c3ff', '#ada5fd', '#948af8', '#8174f2'];
const TITLE_LINES = [
  ['让', 'AI', '资', '讯'],
  ['有', '处', '可', '栖', '。'],
];
// 第三幕品牌名
const BRAND_LINES = [
  ['哆', '啦', '美'],
  ['AI', '资', '讯', '平', '台'],
];
const TITLE_START_MS = 700;
const TITLE_STEP_MS = 72;

// 第一幕停留时长(跃迁抵达约 2s + 星座逐笔连线),之后第二幕(题词归位 + 登录卡浮现)
const TITLE_HOLD_MS = 4000;
// 第二幕稳定后 → 第三幕(主标题变幻为品牌名)
const BRAND_DELAY_MS = 2600;
// 跃迁离场时序:幕布淡入起始 / 完成切换。切换落在幕布全亮帧(淡入起始 + 620ms 过渡,与
// .auth-warp-flash 的 transition 同步),由 App 的抵达幕(app-arrive)接力「静置 → 淡出显影」。
const WARP_FLASH_MS = 1500;
const WARP_DONE_MS = 2140;

function Glyphs({ lines, accentLine, startMs, stepMs, className, brand = false }) {
  let k = 0;
  return lines.map((line, li) => (
    <span className="auth-title-line" key={li}>
      {line.map((t, ci) => {
        const idx = k++;
        const accent = li === accentLine;
        const latin = /^[A-Za-z]+$/.test(t);
        const style = { '--d': `${startMs + idx * stepMs}ms`, '--o': `${idx * 38}ms` };
        if (accent) style.color = ACCENT_RAMP[Math.min(ACCENT_RAMP.length - 1, brand ? ci + 1 : ci)];
        return (
          <span
            key={ci}
            className={`${className}${accent ? ' is-accent' : ''}${latin ? ' is-latin' : ''}`}
            lang={latin ? 'en' : undefined}
            style={style}
          >
            {t}
          </span>
        );
      })}
    </span>
  ));
}

// 游客入口(issue #213)两组,重设计后保留:
// - guestEnabled + onGuestEnter:总闸开时登录按钮下方一行小字「游客登录」(目检拍板:不要整条次级按钮);
// - fromGuest + onBackToGuest:游客点「登录」时叠层呈现,开场直接落登录卡这一幕,卡顶「返回继续逛逛」。
export default function LoginScreen({
  logoError, onLogoError, onLogin, onLoginComplete,
  guestEnabled = false, onGuestEnter = null, fromGuest = false, onBackToGuest = null,
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [focus, setFocus] = useState(null);          // 'user' | 'pass' | null
  const [glint, setGlint] = useState({ user: 0, pass: 0 });
  const [capsOn, setCapsOn] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 'title' = 第一幕(标题卡居中);'ready' = 第二幕(登录卡浮现)
  const [phase, setPhase] = useState(fromGuest ? 'ready' : 'title');
  // 叠层登录(游客 → 账号)直接落品牌名,不再演一遍原标题
  const [branded, setBranded] = useState(fromGuest);
  const [guestEntering, setGuestEntering] = useState(false);
  const [warping, setWarping] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [shaking, setShaking] = useState(false);
  // 触屏键盘态:品牌区收起、底页上提(只在手机竖屏的样式里生效)
  const [kbd, setKbd] = useState(false);
  const canvasRef = useRef(null);
  const sceneRef = useRef(null);
  const shellRef = useRef(null);
  const heroRef = useRef(null);
  const ledeRef = useRef(null);
  const cardRef = useRef(null);
  const timersRef = useRef([]);
  const kbdTimerRef = useRef(0);
  const flipRef = useRef(null);
  const warpingRef = useRef(false);
  const passwordFocused = focus === 'pass';

  // 背景场景:挂载即运行;叠层登录(游客 → 账号)不演跃迁抵达
  useEffect(() => {
    if (!canvasRef.current) return undefined;
    const api = mountDeepSpace(canvasRef.current, { arrival: !fromGuest });
    sceneRef.current = api;
    return () => { api.cleanup(); sceneRef.current = null; };
  }, [fromGuest]);

  useEffect(() => () => {
    timersRef.current.forEach(clearTimeout);
    clearTimeout(kbdTimerRef.current);
  }, []);

  // 第一幕的题词居中:按真实版面量出位移(只用 transform,不改对齐方式——两幕之间没有对齐跳变)
  const measureHero = useCallback(() => {
    const hero = heroRef.current, shell = shellRef.current, lede = ledeRef.current;
    if (!hero || !shell) return;
    const sr = shell.getBoundingClientRect();
    const hx = sr.left + hero.offsetLeft + hero.offsetWidth / 2;
    // 题词可见部分 = 品牌行顶 → 引言底(手机上品牌区在半屏里垂直居中,顶部不一定是 0)
    const top = hero.firstElementChild ? hero.firstElementChild.offsetTop : 0;
    const bottom = lede ? lede.offsetTop + lede.offsetHeight : hero.offsetHeight;
    const hy = sr.top + hero.offsetTop + (top + bottom) / 2;
    hero.style.setProperty('--hero-dx', `${Math.round(window.innerWidth / 2 - hx)}px`);
    hero.style.setProperty('--hero-dy', `${Math.round(window.innerHeight / 2 - hy)}px`);
  }, []);
  useLayoutEffect(() => {
    if (phase !== 'title') return undefined;
    measureHero();
    window.addEventListener('resize', measureHero);
    return () => window.removeEventListener('resize', measureHero);
  }, [phase, measureHero]);

  // 背景靛辉锚在登录卡上
  useEffect(() => {
    const anchor = () => {
      const card = cardRef.current;
      if (!card || !sceneRef.current) return;
      const r = card.getBoundingClientRect();
      sceneRef.current.setAnchor(r.left + r.width / 2, r.top + r.height / 2, r.width, r.height);
    };
    anchor();
    const t = setTimeout(anchor, phase === 'ready' ? 1700 : 60);
    window.addEventListener('resize', anchor);
    return () => { clearTimeout(t); window.removeEventListener('resize', anchor); };
  }, [phase, kbd]);

  // 键盘态切换:先记下底页与 logo 的位置,版式切换后从旧位置补间过去(FLIP),不跳
  const switchKbd = useCallback((next) => {
    const slot = cardRef.current?.parentElement;
    const logo = heroRef.current?.querySelector('.auth-logo-shell');
    flipRef.current = {
      slot: slot ? slot.getBoundingClientRect() : null,
      logo: logo ? logo.getBoundingClientRect() : null,
    };
    setKbd(next);
  }, []);
  useLayoutEffect(() => {
    const prev = flipRef.current;
    flipRef.current = null;
    if (!prev) return;
    const ease = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
    const slot = cardRef.current?.parentElement;
    if (slot && prev.slot && typeof slot.animate === 'function') {
      const dy = prev.slot.top - slot.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) slot.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }], { duration: 420, easing: ease });
    }
    const logo = heroRef.current?.querySelector('.auth-logo-shell');
    if (logo && prev.logo && typeof logo.animate === 'function') {
      const r = logo.getBoundingClientRect();
      const dx = prev.logo.left + prev.logo.width / 2 - (r.left + r.width / 2);
      const dy = prev.logo.top + prev.logo.height / 2 - (r.top + r.height / 2);
      const k = r.width ? prev.logo.width / r.width : 1;
      if (Math.abs(dx) + Math.abs(dy) > 1 || Math.abs(k - 1) > 0.01) {
        logo.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${k})` }, { transform: 'none' }], { duration: 420, easing: ease });
      }
    }
  }, [kbd]);

  const onFieldFocus = (field) => {
    setFocus(field);
    clearTimeout(kbdTimerRef.current);
    if (!kbd && isCoarse()) switchKbd(true);
  };
  const onFieldBlur = (field) => {
    setFocus((f) => (f === field ? null : f));
    clearTimeout(kbdTimerRef.current);
    if (!kbd) return;
    kbdTimerRef.current = setTimeout(() => {
      // 跃迁离场途中不再展开(logo 正按收起态的位置飞向中心)
      const el = document.activeElement;
      if (warpingRef.current || (el && el.classList && el.classList.contains('auth-input'))) return;
      switchKbd(false);
    }, KBD_RELEASE_MS);
  };

  // 第一幕 → 第二幕:定时推进;任意点击 / 按键提前跳过
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

  // 第二幕稳定后 → 第三幕:主标题变幻为品牌名
  useEffect(() => {
    if (phase !== 'ready' || branded) return undefined;
    const timer = setTimeout(() => setBranded(true), BRAND_DELAY_MS);
    return () => clearTimeout(timer);
  }, [phase, branded]);

  // 密码聚焦:logo 蒙眼 + 星空失焦,同一出哑剧
  useEffect(() => {
    sceneRef.current?.setVeil(passwordFocused);
  }, [passwordFocused]);

  // 登录卡:指针附近的玻璃反光 + ±3° 倾斜跟随(rAF 节流,只写卡片自身的 CSS 变量)
  useEffect(() => {
    if (phase !== 'ready' || warping) return undefined;
    let raf = 0, ex = 0, ey = 0;
    const apply = () => {
      raf = 0;
      const card = cardRef.current;
      if (!card) return;
      const r = card.getBoundingClientRect();
      if (!r.width) return;
      const nx = Math.max(-1, Math.min(1, (ex - (r.left + r.width / 2)) / (r.width * 1.4)));
      const ny = Math.max(-1, Math.min(1, (ey - (r.top + r.height / 2)) / (r.height * 1.4)));
      const outX = Math.max(r.left - ex, 0, ex - r.right);
      const outY = Math.max(r.top - ey, 0, ey - r.bottom);
      const lit = Math.max(0, 1 - Math.hypot(outX, outY) / 260);
      card.style.setProperty('--px', `${Math.round(ex - r.left)}px`);
      card.style.setProperty('--py', `${Math.round(ey - r.top)}px`);
      card.style.setProperty('--lit', lit.toFixed(3));
      card.style.setProperty('--tilt-x', (nx * 3).toFixed(2));
      card.style.setProperty('--tilt-y', (-ny * 2.4).toFixed(2));
    };
    const onMove = (e) => {
      if (e.pointerType !== 'mouse') return;   // 触屏没有「指针悬在卡附近」这回事
      ex = e.clientX; ey = e.clientY;
      if (!raf) raf = requestAnimationFrame(apply);
    };
    const reset = () => {
      const card = cardRef.current;
      if (!card) return;
      card.style.setProperty('--tilt-x', '0');
      card.style.setProperty('--tilt-y', '0');
      card.style.setProperty('--lit', '0');
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    document.documentElement.addEventListener('pointerleave', reset);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('pointermove', onMove);
      document.documentElement.removeEventListener('pointerleave', reset);
      reset();
    };
  }, [phase, warping]);

  // 键入:输入框底线掠过一道光,星空里一道信号沿某个星座跑过
  const onType = (field, value, prev, set) => {
    set(value);
    if (value.length > prev.length) {
      setGlint((g) => ({ ...g, [field]: g[field] + 1 }));
      sceneRef.current?.ping();
    }
  };

  const onPasswordKey = (event) => {
    if (typeof event.getModifierState === 'function') setCapsOn(event.getModifierState('CapsLock'));
  };

  const handleGuestEnter = async () => {
    if (!onGuestEnter || guestEntering || isSubmitting || warping) return;
    setError('');
    setGuestEntering(true);
    try {
      await onGuestEnter();
    } catch (err) {
      setError(err.message || '进入游客浏览失败');
      setGuestEntering(false);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (isSubmitting || warping) return;
    setError('');
    setIsSubmitting(true);
    try {
      const session = await onLogin(username, password);
      const scene = sceneRef.current;
      // 无 WebGL(静止回退)时不演跃迁,立即切换
      if (!scene?.live) {
        onLoginComplete?.(session);
        return;
      }
      // 离场:收起软键盘;手机上 logo 要飞到屏幕中心(跃迁的消失点),先量出位移
      warpingRef.current = true;
      clearTimeout(kbdTimerRef.current);
      if (document.activeElement && typeof document.activeElement.blur === 'function') document.activeElement.blur();
      const logo = heroRef.current?.querySelector('.auth-logo-shell');
      if (logo) {
        const r = logo.getBoundingClientRect();
        logo.style.setProperty('--warp-dx', `${Math.round(window.innerWidth / 2 - (r.left + r.width / 2))}px`);
        logo.style.setProperty('--warp-dy', `${Math.round(window.innerHeight / 2 - (r.top + r.height / 2))}px`);
      }
      setWarping(true);
      scene.startWarp();
      timersRef.current.push(
        setTimeout(() => setFlashing(true), WARP_FLASH_MS),
        setTimeout(() => onLoginComplete?.(session, { cinematic: true }), WARP_DONE_MS),
      );
    } catch (err) {
      setError(err.message || '登录失败');
      setIsSubmitting(false);
      sceneRef.current?.kick();
      setShaking(true);
    }
  };

  const status = error
    ? <p className="auth-error">{error}</p>
    : capsOn && passwordFocused
      ? <p className="auth-caps">大写锁定已开启</p>
      : null;

  return (
    <main className={`auth-stage is-${phase}${branded ? ' is-branded' : ''}${fromGuest ? ' is-overlay' : ''}${kbd ? ' is-kbd' : ''}${warping ? ' is-warping' : ''}`}>
      <div className="auth-scene-inner" aria-hidden="true">
        <div className="auth-bg" />
        <canvas className="auth-net" ref={canvasRef} />
        <span className="auth-grain" />
        <span className="auth-vignette" />
      </div>

      <div className="auth-shell" ref={shellRef}>
        <section className="auth-hero" ref={heroRef}>
          <div className="auth-brandrow auth-rise" style={{ '--d': '240ms' }}>
            <LoginBrandMark logoError={logoError} onLogoError={onLogoError} covering={passwordFocused} />
            <span className="auth-wordmark" lang="en">DORAMI</span>
          </div>

          <p className="auth-eyebrow auth-rise" style={{ '--d': '460ms' }}>
            AI 资讯 · 聚合 · 订阅 · 检索
          </p>

          <h1 className="auth-title" aria-label="哆啦美 · AI 资讯平台——让 AI 资讯有处可栖">
            {/* 原标题:逐字显影;第三幕逐字散去 */}
            <span className="auth-title-orig" aria-hidden="true">
              <Glyphs lines={TITLE_LINES} accentLine={1} startMs={TITLE_START_MS} stepMs={TITLE_STEP_MS} className="auth-ch" />
            </span>
            {/* 品牌名:同位叠放,第三幕逐字凝聚、字距收拢 */}
            <span className="auth-title-brand" aria-hidden="true">
              <Glyphs lines={BRAND_LINES} accentLine={0} startMs={380} stepMs={60} className="auth-bch" brand />
            </span>
          </h1>

          <p className="auth-lede auth-rise" style={{ '--d': '1500ms' }} ref={ledeRef}>
            {/* 分句成块:手机上按分句换行,不把「检索」这类词拆到两行 */}
            <span>汇聚多源 AI 资讯，</span><span>订阅、检索、随手可读——尽在一处。</span>
          </p>

          <ul className="auth-feats">
            <li style={{ '--i': 0 }}><Radar aria-hidden="true" />多源聚合</li>
            <li style={{ '--i': 1 }}><Archive aria-hidden="true" />全文留存</li>
            <li style={{ '--i': 2 }}><MessageSquareText aria-hidden="true" />AI 问答</li>
          </ul>
        </section>

        <div className="auth-card-slot">
          <section
            className={`auth-card${shaking ? ' is-shaking' : ''}`}
            ref={cardRef}
            onAnimationEnd={(e) => { if (e.animationName === 'auth-card-shake') setShaking(false); }}
          >
            <span className="auth-card-sheen" aria-hidden="true" />
            <span className="auth-card-frame" aria-hidden="true" />
            <span className="auth-card-comet" aria-hidden="true" />
            <form onSubmit={handleSubmit} className="auth-form">
              {fromGuest && onBackToGuest && (
                <button type="button" className="guest-back" onClick={onBackToGuest} disabled={warping}>
                  <ArrowLeft aria-hidden="true" />
                  返回继续逛逛
                </button>
              )}
              <p className="auth-card-eyebrow" style={{ '--i': 0 }} lang="en">ACCOUNT ACCESS</p>
              <h2 className="auth-card-title" style={{ '--i': 1 }}>欢迎回来</h2>
              <p className="auth-card-sub" style={{ '--i': 2 }}>登录账号，继续浏览你的 AI 资讯。</p>

              <label className={`auth-field${focus === 'user' ? ' is-focus' : ''}`} style={{ '--i': 3 }}>
                <span className="auth-field-label">账号</span>
                <span className="auth-input-wrap">
                  <User className="auth-input-icon" aria-hidden="true" />
                  <input
                    value={username}
                    onChange={(e) => onType('user', e.target.value, username, setUsername)}
                    onFocus={() => onFieldFocus('user')}
                    onBlur={() => onFieldBlur('user')}
                    autoComplete="username"
                    autoCapitalize="none"
                    spellCheck={false}
                    className="auth-input"
                    placeholder="输入登录账号"
                  />
                  <span className="auth-input-line" aria-hidden="true" />
                  {glint.user > 0 && <span key={glint.user} className="auth-input-glint" aria-hidden="true" />}
                </span>
              </label>

              <label className={`auth-field${focus === 'pass' ? ' is-focus' : ''}`} style={{ '--i': 4 }}>
                <span className="auth-field-label">密码</span>
                <span className="auth-input-wrap">
                  <KeyRound className="auth-input-icon" aria-hidden="true" />
                  <input
                    value={password}
                    onChange={(e) => onType('pass', e.target.value, password, setPassword)}
                    onFocus={() => onFieldFocus('pass')}
                    onBlur={() => onFieldBlur('pass')}
                    onKeyDown={onPasswordKey}
                    onKeyUp={onPasswordKey}
                    type="password"
                    autoComplete="current-password"
                    className="auth-input"
                    placeholder="输入登录密码"
                  />
                  <span className="auth-input-line" aria-hidden="true" />
                  {glint.pass > 0 && <span key={glint.pass} className="auth-input-glint" aria-hidden="true" />}
                </span>
              </label>

              <div className="auth-error-slot" aria-live="polite">{status}</div>

              <button
                type="submit"
                disabled={isSubmitting || warping || guestEntering}
                className="auth-submit"
                style={{ '--i': 5 }}
              >
                <span className="auth-submit-gloss" aria-hidden="true" />
                {isSubmitting ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                <span>{warping ? '正在进入' : isSubmitting ? '正在登录' : '登录'}</span>
                {!isSubmitting ? <ArrowRight aria-hidden="true" /> : null}
              </button>

              {guestEnabled && onGuestEnter && !fromGuest && (
                <button
                  type="button"
                  className="guest-enter"
                  onClick={handleGuestEnter}
                  disabled={guestEntering || isSubmitting || warping}
                >
                  {guestEntering ? '正在进入…' : '游客登录'}
                </button>
              )}

              <p className="auth-foot">受 HMAC 会话保护 · 哆啦美</p>
            </form>
          </section>
        </div>
      </div>

      <p className="auth-hint">
        <span className="auth-hint-key">按 <b>任意键</b> 进入</span>
        <span className="auth-hint-touch">轻触 <b>任意处</b> 进入</span>
      </p>

      {/* 跃迁离场的最后一拍:平色主题幕布淡入,切换藏在幕布全亮帧背后 */}
      <div className={`auth-warp-flash${flashing ? ' is-on' : ''}`} aria-hidden="true" />
    </main>
  );
}
