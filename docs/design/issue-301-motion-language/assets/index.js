/* 哆啦美 · 动效样页 —— 总览页脚本:对照台(每格是一份循环演示的原型)、放大、逐场景选择与汇总。
   场景与各方向的参数行直接读 motion-core.js / dir-*.js / presence.js 注册的数据,不另存一份。 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const V = '1119c';
  const W = 1440; const H = 810;
  const DIRS = ['0', 'a', 'b', 'c', 'd'];
  const LABEL = { 0: '现网', a: 'A 精密', b: 'B 连续', c: 'C 光', d: 'D 刊物', s: '选定稿' };
  const LB_DIRS = [...DIRS, 's'];
  /* 2026-11-18 用户逐场景选定的来处;选定稿(dir-s)就是按这张表合成的 */
  const FINAL = { press: 'a', ind: 'b', pop: 'a', open: 'a', nav: 'a', fold: 'b', qa: 'c', brief: 'b', ai: 'a', toast: 'a', theme: 'a', scroll: 'b', event: 'b', sub: 'b' };
  const FAMILY = { a: '精密', b: '连续', c: '光' };
  const P_LABEL = { off: '不做', on: '做' };
  const root = document.documentElement;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 存不了就只在本页有效 */ } },
  };
  let theme = store.get('motion-theme', 'dark');
  let picks = store.get('motion-picks', {});
  const scenes = M.scenes;
  const order = M.sceneOrder; const pOrder = M.presenceScenes || [];

  const src = (scene, dir, p, speed) => `app.html?dir=${dir}&demo=${scene}${p ? `&p=${p}` : ''}${speed && speed !== 1 ? `&speed=${speed}` : ''}&v=${V}#${theme}.brief.bare`;
  const fit = (vp) => {
    const f = $('iframe', vp); if (!f) return;
    const [x, y, w] = vp._crop; const s = vp.clientWidth / w;
    f.style.transform = `translate(${-x * s}px, ${-y * s}px) scale(${s})`;
  };
  const ro = new ResizeObserver((es) => es.forEach((e) => fit(e.target)));
  /* 只让视口附近的格子活着:进来才装 iframe,走远了拆掉 */
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    const vp = e.target;
    if (e.isIntersecting) { if (!$('iframe', vp)) { const f = document.createElement('iframe'); f.src = vp._src(); f.setAttribute('tabindex', '-1'); f.setAttribute('aria-hidden', 'true'); vp.appendChild(f); fit(vp); } } else { const f = $('iframe', vp); if (f) vp.removeChild(f); }
  }), { rootMargin: '80px 0px' });

  function cell(scene, key, label, note, srcFn) {
    const sc = scenes[scene]; const [, , w, h] = sc.crop;
    const el = document.createElement('div'); el.className = 'cell'; el.dataset.scene = scene; el.dataset.key = key;
    el.innerHTML = `<div class="vp" style="aspect-ratio:${w} / ${h}"></div><button class="pick" type="button">${label}</button><p class="cnote">${note || ''}</p>`;
    const vp = $('.vp', el); vp._crop = sc.crop; vp._src = srcFn;
    ro.observe(vp); io.observe(vp);
    vp.addEventListener('click', () => openLb(scene, key));
    $('.pick', el).addEventListener('click', () => { if (picks[scene] === key) delete picks[scene]; else picks[scene] = key; store.set('motion-picks', picks); syncPicks(); });
    return el;
  }
  function fcell(scene, key, label, srcFn) {
    const sc = scenes[scene]; const [, , w, h] = sc.crop;
    const el = document.createElement('div'); el.className = 'fcell';
    el.innerHTML = `<div class="vp" style="aspect-ratio:${w} / ${h}"></div><span>${label}</span>`;
    const vp = $('.vp', el); vp._crop = sc.crop; vp._src = srcFn;
    ro.observe(vp); io.observe(vp);
    vp.addEventListener('click', () => openLb(scene, key));
    return el;
  }
  function buildFinal() {
    const host = $('#final-rows'); if (!host) return; host.innerHTML = '';
    const def = M.dirs.s || {};
    [...order, 'p-immersive'].forEach((scene) => {
      const sc = scenes[scene]; if (!sc) return; const isP = scene.startsWith('p-');
      const pair = document.createElement('div'); pair.className = `pair ${sc.crop[2] <= 720 ? 'half' : ''}`; pair.id = `final-${scene}`;
      const note = isP ? P_NOTE.immersive[1] : (def.notes || {})[scene] || '';
      const fam = isP ? '退场' : FAMILY[FINAL[scene]];
      pair.innerHTML = `<div class="pair-h"><h3>${sc.title}</h3><em>${fam}</em><p>${note.replace(/^(精密|连续|光):/, '')}</p></div><div class="pair-b"></div>`;
      const b = $('.pair-b', pair);
      b.appendChild(fcell(scene, isP ? 'off' : '0', '现网', () => src(scene, '0')));
      b.appendChild(fcell(scene, isP ? 'on' : 's', '选定稿', () => src(scene, 's')));
      host.appendChild(pair);
    });
    const tk = $('#final-tokens');
    if (tk) tk.innerHTML = `<table><thead><tr><th>名字</th><th>取值</th><th>用在</th></tr></thead><tbody>${(def.tokens || []).map((t) => `<tr><td><code>${t.name}</code></td><td>${t.value}</td><td>${t.use || ''}</td></tr>`).join('')}</tbody></table>`;
  }
  function build() {
    const host = $('#cmp-rows'); host.innerHTML = '';
    order.forEach((scene) => {
      const sc = scenes[scene];
      const h = document.createElement('div'); h.className = 'row-h'; h.id = `row-${scene}`; h.innerHTML = `<h3>${sc.title}</h3><i>${scene}</i>`; host.appendChild(h);
      const row = document.createElement('div'); row.className = `cells ${sc.crop[2] > 600 ? 'wide' : ''}`;
      DIRS.forEach((d) => { const def = M.dirs[d] || {}; row.appendChild(cell(scene, d, LABEL[d], (def.notes || {})[scene], () => src(scene, d))); });
      host.appendChild(row);
    });
    const ph = $('#p-rows'); ph.innerHTML = '';
    pOrder.forEach((scene) => {
      const sc = scenes[scene]; const opt = scene.replace('p-', '');
      const h = document.createElement('div'); h.className = 'row-h'; h.id = `row-${scene}`; h.innerHTML = `<h3>${sc.title}</h3><i>${scene}</i>`; ph.appendChild(h);
      const row = document.createElement('div'); row.className = 'cells two';
      row.appendChild(cell(scene, 'off', P_LABEL.off, P_NOTE[opt][0], () => src(scene, 'a')));
      row.appendChild(cell(scene, 'on', P_LABEL.on, P_NOTE[opt][1], () => src(scene, 'a', opt)));
      ph.appendChild(row);
    });
    syncPicks();
  }
  const P_NOTE = {
    immersive: ['导轨、来源栏、列表始终同样亮', '往下读过一屏后三栏降到 30%(600ms);指针碰到、回滚 24px、按键即回(160ms)'],
    rowact: ['行上没有动作,收藏与标读要先点开文章', '悬停或焦点落入时右下淡入两枚钮(100ms);已收藏的星常显在分数左侧'],
    peek: ['收起后只能点栏名下拉来换来源', '指针在列表左缘停 140ms,栏从导轨后滑出盖住列表(240ms);移开 320ms 后收回'],
    bar: ['吸顶栏始终在', '往下读 60px 后上收(320ms);回滚 16px 或指针靠近窗顶即回(220ms)'],
  };

  function syncPicks() {
    $$('.cell').forEach((c) => c.classList.toggle('picked', picks[c.dataset.scene] === c.dataset.key));
    const all = [...order, ...pOrder]; const n = all.filter((s) => picks[s] != null).length;
    $('#pick-n').textContent = `${n} / ${all.length}`;
  }
  function pickText() {
    const lines = ['动效样页 · 逐场景选择'];
    order.forEach((s) => lines.push(`${scenes[s].title}:${picks[s] != null ? LABEL[picks[s]] : '未选'}`));
    pOrder.forEach((s) => lines.push(`${scenes[s].title}:${picks[s] != null ? P_LABEL[picks[s]] : '未选'}`));
    return lines.join('\n');
  }
  $('#pick-copy').addEventListener('click', async (e) => {
    const t = pickText();
    try { await navigator.clipboard.writeText(t); e.target.textContent = '已复制'; } catch { window.prompt('复制下面的文字', t); }
    setTimeout(() => { e.target.textContent = '复制选择'; }, 1600);
  });
  $('#pick-clear').addEventListener('click', () => { picks = {}; store.set('motion-picks', picks); syncPicks(); });
  $$('[data-pickall]').forEach((b) => b.addEventListener('click', () => { order.forEach((s) => { picks[s] = b.dataset.pickall; }); store.set('motion-picks', picks); syncPicks(); }));
  const REC = { press: 'a', ind: 'a', pop: 'b', open: 'b', nav: 'a', fold: 'a', qa: 'c', brief: 'd', ai: 'c', toast: 'a', theme: 'c', scroll: 'b', event: 'b', sub: 'a', 'p-immersive': 'on', 'p-rowact': 'on', 'p-peek': 'on', 'p-bar': 'off' };
  const rec = $('#pick-rec'); if (rec) rec.addEventListener('click', () => { picks = { ...REC }; store.set('motion-picks', picks); syncPicks(); });

  /* 放大:同一场景里切方向、调速度 */
  const lb = $('#lb'); let lbState = null;
  function drawLb() {
    const { scene, key, speed } = lbState; const sc = scenes[scene]; const isP = scene.startsWith('p-');
    const [x, y, w, h] = sc.crop;
    const s = Math.min((innerWidth * 0.92) / w, (innerHeight * 0.92 - 110) / h, 1.25);
    const vp = $('.lb-vp', lb); vp.style.width = `${w * s}px`; vp.style.height = `${h * s}px`;
    const dir = isP ? 'a' : key; const p = isP && key === 'on' ? scene.replace('p-', '') : '';
    vp.innerHTML = `<iframe src="${src(scene, dir, p, speed)}" style="transform: translate(${-x * s}px, ${-y * s}px) scale(${s})"></iframe>`;
    $('#lb-title').textContent = sc.title;
    $('#lb-dirs').innerHTML = (isP ? ['off', 'on'] : LB_DIRS).map((k) => `<button class="${k === key ? 'on' : ''}" data-k="${k}">${isP ? P_LABEL[k] : LABEL[k]}</button>`).join('');
    $$('#lb-speed button').forEach((b) => b.classList.toggle('on', Number(b.dataset.s) === speed));
    $('#lb-note').textContent = isP ? P_NOTE[scene.replace('p-', '')][key === 'on' ? 1 : 0] : ((M.dirs[key] || {}).notes || {})[scene] || '';
    $('#lb-open').href = `app.html?dir=${dir}${p ? `&p=${p}` : ''}#${theme}.${sc.view}`;
  }
  function openLb(scene, key) { lbState = { scene, key, speed: 1 }; drawLb(); lb.showModal(); }
  lb.addEventListener('click', (e) => {
    if (e.target === lb) { lb.close(); return; }
    const k = e.target.closest('#lb-dirs button'); if (k) { lbState.key = k.dataset.k; drawLb(); return; }
    const s = e.target.closest('#lb-speed button'); if (s) { lbState.speed = Number(s.dataset.s); drawLb(); return; }
    if (e.target.closest('#lb-close')) lb.close();
  });
  lb.addEventListener('close', () => { $('.lb-vp', lb).innerHTML = ''; });

  /* 明暗:页面与所有演示一起换 */
  function setTheme(t) {
    theme = t; store.set('motion-theme', t); root.dataset.theme = t;
    $$('#theme-seg button').forEach((b) => b.classList.toggle('on', b.dataset.theme === t));
    $$('.vp iframe').forEach((f) => { f.src = f.parentNode._src(); });
  }
  $$('#theme-seg button').forEach((b) => b.addEventListener('click', () => setTheme(b.dataset.theme)));

  /* 各方向的 token 表 */
  function tokens() {
    const host = $('#tokens'); if (!host) return;
    host.innerHTML = DIRS.map((d) => {
      const def = M.dirs[d] || {}; const rows = (def.tokens || []).map((t) => `<tr><td><code>${t.name}</code></td><td>${t.value}</td><td>${t.use || ''}</td></tr>`).join('');
      return rows ? `<h3>${LABEL[d]}</h3><table><thead><tr><th>名字</th><th>取值</th><th>用在</th></tr></thead><tbody>${rows}</tbody></table>` : '';
    }).join('');
  }

  /* 「已选」条只属于第一轮的对照:滚到逐场景对照 / 在场与退场时才出现 */
  const bar = $('.picks'); const seen = new Set();
  const barIo = new IntersectionObserver((es) => { es.forEach((e) => { if (e.isIntersecting) seen.add(e.target.id); else seen.delete(e.target.id); }); bar.classList.toggle('on', seen.size > 0); });
  ['cmp', 'presence'].forEach((id) => { const el = document.getElementById(id); if (el) barIo.observe(el); });

  root.dataset.theme = theme;
  buildFinal(); build(); tokens(); setTheme(theme);
})();
