/* 外壳交互:页面主题、样页画框缩放、变体切换、1:1 全尺寸查看 */
(function () {
  const root = document.documentElement;
  const KEY = 'dorami-refine2-theme';
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const pageDark = () => (root.dataset.theme ? root.dataset.theme === 'dark' : mq.matches);

  function paintThemeSwitch(v) {
    document.querySelectorAll('.theme button').forEach((b) => b.classList.toggle('on', b.dataset.t === (v || 'auto')));
  }
  function applyTheme(v) {
    if (v === 'light' || v === 'dark') root.dataset.theme = v; else delete root.dataset.theme;
    paintThemeSwitch(v);
  }
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { saved = null; }
  applyTheme(saved);

  // 样页默认跟随页面主题;用户在画框上手动切过亮暗后就不再跟随
  function syncModes() {
    const mode = pageDark() ? 'dark' : 'light';
    document.querySelectorAll('img[data-dark]').forEach((img) => { const want = mode === 'dark' ? img.dataset.dark : img.dataset.light; if (img.getAttribute('src') !== want) img.setAttribute('src', want); });
    document.querySelectorAll('[data-follow]').forEach((m) => { m.dataset.mode = mode; });
    document.querySelectorAll('.sw[data-attr="mode"]').forEach((sw) => {
      const first = document.getElementById((sw.dataset.for || '').split(',')[0]);
      if (!first) return;
      sw.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === first.dataset.mode));
    });
  }

  /* —— 缩放 —— */
  function fit(stage) {
    const sc = stage.querySelector(':scope > .frame-scale');
    if (!sc) return;
    const frame = stage.closest('.frame');
    const w = Number(stage.dataset.w) || 1440;
    sc.style.width = w + 'px';
    if (frame && frame.classList.contains('is-full')) { stage.style.height = ''; sc.style.transform = ''; return; }
    const k = Math.min(1, stage.clientWidth / w);
    sc.style.transform = `scale(${k})`;
    if (stage.classList.contains('thumb')) return; // 缩略图保持自身比例,只缩放
    const h = Number(stage.dataset.h) || sc.offsetHeight;
    stage.style.height = Math.ceil(h * k) + 'px';
  }
  const stages = () => document.querySelectorAll('.frame-stage, .thumb[data-w]');
  function fitAll() { stages().forEach(fit); }
  const ro = 'ResizeObserver' in window ? new ResizeObserver((entries) => {
    entries.forEach((en) => { const st = en.target.closest('.frame-stage, .thumb[data-w]') || en.target; fit(st); });
  }) : null;
  function observe() {
    stages().forEach((st) => {
      if (st.__obs || !ro) return;
      st.__obs = true; ro.observe(st);
      const sc = st.querySelector(':scope > .frame-scale');
      if (sc) ro.observe(sc);
    });
  }

  /* —— 切换 —— */
  document.addEventListener('click', (e) => {
    const t = e.target.closest('.theme button');
    if (t) {
      const v = t.dataset.t === 'auto' ? null : t.dataset.t;
      try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (err) { /* 存储不可用时只影响记忆 */ }
      applyTheme(v); syncModes(); fitAll();
      return;
    }
    const b = e.target.closest('.sw button');
    if (b) {
      const sw = b.closest('.sw');
      sw.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      const attr = sw.dataset.attr;
      (sw.dataset.for || '').split(',').filter(Boolean).forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (attr === 'mode') el.removeAttribute('data-follow');
        el.dataset[attr] = b.dataset.v;
        if (sw.dataset.render === 'brief' && window.DMR) window.DMR.brief(el, { layout: b.dataset.v });
      });
      // 同组联动:data-group 相同的分段一起切换显示状态
      if (sw.dataset.group) {
        document.querySelectorAll(`.sw[data-group="${sw.dataset.group}"]`).forEach((o) => {
          if (o === sw) return;
          o.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x.dataset.v === b.dataset.v));
          (o.dataset.for || '').split(',').filter(Boolean).forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.dataset[o.dataset.attr] = b.dataset.v;
          });
        });
      }
      requestAnimationFrame(fitAll);
      return;
    }
    const full = e.target.closest('[data-full]');
    if (full) {
      const frame = full.closest('.frame');
      const on = !frame.classList.contains('is-full');
      frame.classList.toggle('is-full', on);
      document.body.style.overflow = on ? 'hidden' : '';
      full.querySelector('span').textContent = on ? '退出全尺寸' : '1:1 查看';
      requestAnimationFrame(fitAll);
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const f = document.querySelector('.frame.is-full');
    if (f) f.querySelector('[data-full]').click();
  });
  mq.addEventListener('change', () => { if (!root.dataset.theme) { syncModes(); fitAll(); } });

  window.SITE = { fitAll, observe, syncModes, pageDark };
  window.addEventListener('DOMContentLoaded', () => { syncModes(); observe(); fitAll(); });
  window.addEventListener('load', () => { fitAll(); setTimeout(fitAll, 600); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
})();
