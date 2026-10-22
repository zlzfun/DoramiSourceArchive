/* 游客模式样页(issue #213)· 画面渲染与交互
   底稿是现网 DOM 快照(assets/snap/*.html,2026-10-22 在本机库沙箱里以读者 / 管理员身份抓取),
   样式是同一版前端编译产物(assets/app.css)。本波提案只有两处:assets/guest.css 与本文件里
   带 guest- 前缀的片段——登录提示的外形照抄现网 ConfirmDialog,早报说明行沿用 .bp-notice,
   运维总闸行沿用 .ai-switchboard,抽屉沿用 .ledger-drawer。
   参数:scene=deeplink|login|prompt|brief|discover|share|ai|settings|admin  device=desktop|mobile
        action=favorite|favlist|subscribe|unsub|interest|addsrc|publink|feedback|ai(prompt 场景)
        from=guest + back=<scene>(login 场景)  toast=on|off(deeplink)  view=menu|page(share)
        drawer=open(admin)  theme=light|dark  palette=porcelain|graphite|paper */
(function () {
  'use strict';

  const q = new URLSearchParams(location.search);
  const SCENES = ['deeplink', 'login', 'prompt', 'brief', 'discover', 'share', 'ai', 'settings', 'admin'];
  const S = {
    scene: SCENES.includes(q.get('scene')) ? q.get('scene') : 'deeplink',
    device: q.get('device') === 'mobile' ? 'mobile' : 'desktop',
    action: q.get('action') || 'favorite',
    from: q.get('from') === 'guest' ? 'guest' : '',
    back: SCENES.includes(q.get('back')) ? q.get('back') : 'deeplink',
    toast: q.get('toast') !== 'off',
    view: q.get('view') === 'page' ? 'page' : 'menu',
    drawer: q.get('drawer') === 'open',
  };
  const MOBILE = S.device === 'mobile';

  const SNAP = {
    deeplink: ['d-article', 'm-article'],
    login: ['d-login', 'm-login'],
    prompt: ['d-article', 'm-article'],
    brief: ['d-brief', 'm-brief'],
    discover: ['d-discover', 'm-discover'],
    share: S.view === 'page' ? ['d-sharepage', 'm-sharepage'] : ['d-share', 'm-share'],
    ai: ['d-ai', 'm-article'],
    settings: ['d-settings', 'm-me'],
    admin: ['d-admin-users', null],
  };

  // ── lucide 图标(与现网同一套路径) ──
  const svg = (paths, cls = '') => `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${cls}" aria-hidden="true">${paths}</svg>`;
  const P = {
    logIn: '<path d="m10 17 5-5-5-5"/><path d="M15 12H3"/><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>',
    star: '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/>',
    rss: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    tags: '<path d="M13.172 2a2 2 0 0 1 1.414.586l6.71 6.71a2.4 2.4 0 0 1 0 3.408l-4.592 4.592a2.4 2.4 0 0 1-3.408 0l-6.71-6.71A2 2 0 0 1 6 9.172V3a1 1 0 0 1 1-1z"/><path d="M2 7v6.172a2 2 0 0 0 .586 1.414l6.71 6.71a2.4 2.4 0 0 0 3.191.193"/><circle cx="10.5" cy="6.5" r=".5" fill="currentColor"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    msg: '<path d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"/>',
    sparkles: '<path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/><path d="M20 2v4"/><path d="M22 4h-4"/><circle cx="4" cy="20" r="2"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    user: '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    arrowR: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    arrowL: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    gear: '<path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"/><circle cx="12" cy="12" r="3"/>',
  };

  // ── 登录提示文案:标题说「登录后能做什么」,一句说明讲为什么要账号;按钮恒为「继续逛逛 | 登录」 ──
  const COPY = {
    favorite: { icon: 'star', title: '登录后即可收藏', msg: '收藏会保存到你的账号。' },
    favlist: { icon: 'star', title: '登录后可查看收藏', msg: '收藏会保存到你的账号。' },
    subscribe: { icon: 'plus', title: '登录后即可订阅「{name}」', msg: '游客看到的是一组默认来源;登录后按你自己的订阅编排早报与文章。' },
    subscribeAll: { icon: 'plus', title: '登录后即可订阅这些来源', msg: '游客看到的是一组默认来源;登录后按你自己的订阅编排早报与文章。' },
    unsub: { icon: 'rss', title: '登录后即可调整订阅', msg: '游客看到的是一组默认来源;登录后按你自己的订阅编排早报与文章。' },
    interest: { icon: 'tags', title: '登录后即可设置兴趣', msg: '早报会按你的兴趣多挑几篇。' },
    addsrc: { icon: 'plus', title: '登录后即可添加来源', msg: '可以添加自己的 RSS 源,或导入 OPML。' },
    publink: { icon: 'link', title: '登录后即可生成公开链接', msg: '站内链接可以直接复制;公开链接要用账号签发。' },
    feedback: { icon: 'msg', title: '登录后即可反馈', msg: '回复会出现在你的「反馈与建议」里。' },
    ai: { icon: 'sparkles', title: '游客今日的 AI 试用已用完', msg: '登录后继续使用翻译、问答与速读。' },
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const go = (patch) => {
    const next = new URLSearchParams(location.search);
    Object.entries(patch).forEach(([k, v]) => (v == null ? next.delete(k) : next.set(k, v)));
    location.search = next.toString();
  };
  const goLogin = () => go({ scene: 'login', from: 'guest', back: S.scene === 'login' ? S.back : S.scene, action: null, drawer: null });

  // ── 登录提示:结构与类名照抄 ConfirmDialog.jsx(<Modal closeOnOverlay centered size="md">) ──
  function closePrompt() { $$('[data-guest-prompt]').forEach((n) => n.remove()); }
  function openPrompt(action, vars = {}) {
    closePrompt();
    const c = COPY[action] || COPY.favorite;
    const title = c.title.replace('{name}', vars.name || '这个来源');
    const node = el(`
      <div class="modal-overlay items-center" data-guest-prompt>
        <div role="dialog" aria-modal="true" aria-label="${title}" tabindex="-1" class="modal-panel max-w-md">
          <div class="flex items-start gap-4 p-6">
            <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--dorami-wash)] text-indigo-500">${svg(P[c.icon], 'h-5 w-5')}</div>
            <div class="min-w-0 flex-1">
              <h3 class="text-base font-semibold text-[var(--dorami-ink)]">${title}</h3>
              <p class="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-500">${c.msg}</p>
            </div>
            <button type="button" class="p-1 text-slate-300 hover:text-slate-500" aria-label="关闭" data-close>${svg(P.x, 'h-5 w-5')}</button>
          </div>
          <div class="flex justify-end gap-3 border-t border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-6 py-3">
            <button type="button" class="action-button action-button-quiet min-h-[32px] px-3 text-xs" data-close>继续逛逛</button>
            <button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" data-login>登录</button>
          </div>
        </div>
      </div>`);
    node.addEventListener('click', (e) => {
      if (e.target === node || e.target.closest('[data-close]')) { e.stopPropagation(); closePrompt(); }
      else if (e.target.closest('[data-login]')) { e.stopPropagation(); goLogin(); }
    });
    document.body.appendChild(node);
  }

  // ── 游客外壳:视图轨头像位 → 「登录」;移动「我的」账户卡 → 游客卡;分享菜单按游客改写 ──
  function guestShell() {
    const avatar = $('nav.reader-vrail .reader-vrail-avatar, .reader-vrail .reader-vrail-avatar');
    if (avatar) {
      avatar.replaceWith(el(`<button type="button" class="reader-vrail-btn guest-rail-login" aria-label="登录" data-login>${svg(P.logIn, 'h-[18px] w-[18px]')}<span class="reader-vrail-label">登录</span><span class="reader-vrail-tip">登录后可收藏、订阅与设置兴趣</span></button>`));
    }
    const card = $('.m-me-card');
    if (card) {
      card.classList.add('guest-me-card');
      card.innerHTML = `<div class="m-avatar guest-avatar" aria-hidden="true">${svg(P.user)}</div><div class="min-w-0"><div class="m-me-name">游客</div><div class="m-me-sub">登录后可收藏、订阅、设置兴趣</div></div><button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs guest-login-btn" data-login>登录</button>`;
    }
    const logout = $$('.m-row.is-danger').find((b) => /退出登录/.test(b.textContent));
    if (logout) logout.closest('.m-group')?.remove();
    guestShareMenu();
  }

  function guestShareMenu() {
    const menu = $('.share-menu');
    if (!menu) return;
    const inSite = $('.share-group-head .share-group-note', menu);
    if (inSite) inSite.textContent = '免登录可看';
    const pub = $('.share-public', menu);
    if (pub) {
      pub.innerHTML = `<div class="share-group-head"><span class="micro-label">公开链接</span><span class="share-group-note">仅这一篇</span></div><div class="share-expiry"><button type="button" class="share-create" data-act="publink">生成公开链接</button></div>`;
    }
  }

  // ── 各场景 ──
  function showEntryToast() {
    const node = el(`<div class="toast-pop fixed left-1/2 z-[200] flex max-w-[90vw] items-center gap-3 rounded-[var(--r-card)] py-3.5 pl-5 pr-3 text-white shadow-2xl bg-slate-800 dark:bg-slate-900" role="status">${svg(P.info, 'h-5 w-5 shrink-0')}<span class="text-sm font-semibold guest-toast-text">正以游客身份浏览</span><button type="button" class="guest-toast-act" data-login>登录</button><button type="button" aria-label="关闭提示" class="shrink-0 rounded-[var(--r-sm)] p-1 text-white/70 transition-colors hover:bg-white/15 hover:text-white" data-toast-close>${svg(P.x, 'h-4 w-4')}</button></div>`);
    node.addEventListener('click', (e) => { if (e.target.closest('[data-toast-close]')) node.remove(); });
    document.body.appendChild(node);
  }

  function setupLogin() {
    const form = $('.auth-form');
    if (!form) return;
    $('.auth-hint')?.remove();
    if (S.from === 'guest') {
      form.prepend(el(`<button type="button" class="guest-back" data-back>${svg(P.arrowL)}返回继续逛逛</button>`));
    } else {
      const submit = $('.auth-submit', form);
      const enter = el(`<button type="button" class="guest-enter" data-guest-enter>先逛逛${svg(P.arrowR)}</button>`);
      submit.after(enter);
      enter.after(el('<p class="guest-enter-note">不登录也能阅读、试用 AI</p>'));
    }
  }

  function replaceText(root, pairs) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((n) => { let t = n.nodeValue; pairs.forEach(([a, b]) => { t = t.split(a).join(b); }); n.nodeValue = t; });
  }

  function setupBrief() {
    // 样页把 9 月 16 日那一版当作「今天」展示(本机库最近没有过线稿)
    const day = $('.bp-eyebrow span');
    if (day) day.textContent = '10月22日 周四 · 今天';
    $$('.bp-lbtn').forEach((b) => b.remove()); // 游客没有「重新编排」;样页底稿里的「回到今天」一并去掉
    const note = el(`<p class="bp-notice" role="status"><span class="guest-chip">游客版</span>按默认订阅编排 · <button type="button" class="bp-notice-act" data-login>登录后按你的订阅与兴趣编排</button></p>`);
    const partial = $('.bp-notice');
    if (partial) partial.replaceWith(note); else $('.bp-top')?.after(note);
    const sheet = $('.bp-sheet');
    if (sheet) replaceText(sheet, [['来自你订阅的', '来自默认订阅的'], ['今日订阅中', '今日默认订阅中'], ['你订阅的', '默认订阅的']]);
  }

  function setupSharePage() {
    const foot = $('.shared-foot');
    if (!foot) return;
    foot.after(el(`<div class="guest-share-cta"><span><b>还想看更多?</b> 早报、文章、播客都能直接逛,不用登录</span><button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" data-goto="brief">在哆啦美里继续逛逛${svg(P.arrowR, 'h-3.5 w-3.5')}</button></div>`));
  }

  function setupAi() {
    if (!MOBILE) {
      const thread = $('.reader-ai-thread');
      if (thread) {
        thread.innerHTML = `<div class="reader-ai-turn"><div class="reader-ai-q">这篇文章有什么值得商榷的地方?</div><div class="reader-ai-a-error">游客今日的 AI 试用已用完 · <button type="button" class="guest-inline-login" data-login>登录后继续使用</button></div></div>`;
      }
    }
    openPrompt('ai');
  }

  const GATES = {
    '订阅源 OPML': ['订阅源 OPML', '导入或导出订阅源', '导入或导出你的订阅源,方便搬到别的阅读器。'],
    '反馈与建议': ['反馈与建议', '问题、建议与回复', '提交反馈,回复也会出现在这里。'],
    '聚合接口': ['聚合接口', '个人令牌与订阅地址', '用个人令牌把订阅接到 RSS 阅读器或脚本里。'],
    'MCP 接入': ['MCP 接入', '让 AI 助手检索你的订阅', '在 Claude 等 AI 助手里按你的订阅检索与问答。'],
    'Agent 技能包': ['Agent 技能包', '连到你账号的技能', '下载一份连到你账号的 Claude 技能。'],
  };
  function setupSettings() {
    if (MOBILE) return; // 移动端「我的」页的游客卡由 guestShell 处理
    const head = $('.sett-head-hint');
    if (head) head.textContent = '当前以游客身份浏览';
    const scroll = $('.sett-scroll');
    const accountHtml = `<div>
      <div class="sett-row"><span class="sett-avatar guest-avatar" aria-hidden="true">${svg(P.user)}</span><span class="sett-id"><span class="sett-acct-name">游客</span><div class="sett-sub"><span class="sett-role-chip">未登录</span></div></span><span class="sett-ctl"><button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" data-login>${svg(P.logIn, 'h-3.5 w-3.5')} 登录</button></span></div>
      <div class="sett-row is-block"><span class="sett-id"><span class="sett-lbl">游客可以</span><div class="sett-sub">阅读全部内容,使用 AI 翻译、问答与速读,浏览发现页与榜单</div></span></div>
      <div class="sett-row is-block"><span class="sett-id"><span class="sett-lbl">登录后还能</span><div class="sett-sub">收藏、订阅、设置兴趣,早报按你的订阅与兴趣编排;接入聚合接口与 MCP</div></span></div>
    </div>`;
    if (scroll) scroll.innerHTML = accountHtml;
    $$('.sett-nav-btn').forEach((btn) => {
      const label = btn.textContent.trim();
      if (GATES[label]) btn.insertAdjacentHTML('beforeend', svg(P.lock, 'guest-lock'));
      btn.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        $$('.sett-nav-btn').forEach((b) => b.classList.toggle('is-on', b === btn));
        const titleEl = $('.sett-head-title');
        if (label === '账户') {
          titleEl.textContent = '账户'; head.textContent = '当前以游客身份浏览'; scroll.innerHTML = accountHtml;
        } else if (GATES[label]) {
          const [title, hint, sub] = GATES[label];
          titleEl.textContent = title; head.textContent = hint;
          scroll.innerHTML = `<div class="guest-gate">${svg(P.lock)}<p class="guest-gate-title">登录后可用</p><p class="guest-gate-sub">${sub}</p><button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" data-login>登录</button></div>`;
        } else {
          titleEl.textContent = label; head.textContent = '游客照常可用';
          scroll.innerHTML = `<p class="tiny-meta" style="margin:24px 4px">${label === '外观' ? '主题、配色与动效游客照常可调,只存在这台设备上。' : '版本与构建信息,游客照常可看。'}(样页不重绘此分区)</p>`;
        }
      }, true);
    });
  }

  // 运维游客区:数字为示意(本机没有游客数据),口径见方案 §3.8
  function guestChart() {
    const W = 1200, H = 200, L = 34, B = 26, T = 8;
    const days = 30;
    const seed = [6, 4, 5, 3, 7, 9, 8, 4, 3, 5, 6, 12, 15, 9, 6, 5, 4, 8, 7, 6, 3, 2, 4, 6, 5, 7, 10, 8, 6, 5];
    const conv = { 11: 1, 12: 2, 17: 1, 26: 2, 27: 1, 29: 2 };
    const max = 16;
    const bw = (W - L) / days;
    const y = (v) => T + (H - B - T) * (1 - v / max);
    let s = '';
    [0, 4, 8, 12, 16].forEach((v) => { s += `<line class="gc-grid" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="gc-axis" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`; });
    seed.forEach((v, i) => {
      const x = L + i * bw + bw * 0.18;
      s += `<rect class="gc-bar" x="${x.toFixed(1)}" y="${y(v).toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${(y(0) - y(v)).toFixed(1)}" rx="2"><title>${v} 位访客</title></rect>`;
      if (conv[i]) s += `<circle class="gc-conv" cx="${(x + bw * 0.32).toFixed(1)}" cy="${(y(v) - 9).toFixed(1)}" r="4.5"><title>${conv[i]} 位转化</title></circle>`;
      if (i % 5 === 4 || i === days - 1) {
        const d = new Date(2026, 8, 23 + i);
        s += `<text class="gc-axis" x="${(x + bw * 0.32).toFixed(1)}" y="${H - 6}" text-anchor="middle">${d.getMonth() + 1}-${d.getDate()}</text>`;
      }
    });
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="近 30 天每日游客访客与转化">${s}</svg>`;
  }

  function setupAdmin() {
    const growth = $('section[aria-label="账户增长"]');
    if (!growth) return;
    const zone = el(`<div>
      <div class="zone-head"><span class="zone-title">游客</span><span class="zone-hint">近 30 天 · 不计入上方账户口径</span><span class="guest-zone-acts"><button type="button" class="rowact-btn" title="游客设置" aria-label="游客设置" data-drawer>${svg(P.gear)}</button></span></div>
      <section class="surface-card ai-switchboard is-wrap rounded-[var(--r-card)] mb-4" aria-label="游客开关">
        <span class="ai-light" data-light></span>
        <div class="ai-switch-lbl" title="总闸:关闭后在线游客会话立即失效,未登录打开站内链接回到登录页">游客访问</div>
        <button type="button" role="switch" aria-checked="true" aria-label="游客访问总闸" class="ledger-switch is-on" data-switch="guest"></button>
        <span class="ai-divider"></span>
        <div class="ai-switch-lbl" title="只关游客的 AI;正式读者不受影响">游客 AI</div>
        <button type="button" role="switch" aria-checked="true" aria-label="游客 AI" class="ledger-switch is-on" data-switch="ai"></button>
        <span class="ai-divider"></span>
        <span class="tiny-meta" data-meta>未登录打开站内链接直接以游客浏览 · 每位游客的 AI 日额度与读者相同</span>
      </section>
      <section class="surface-card kpi-strip" aria-label="游客概览">
        <div class="kpi"><span class="kpi-num">128</span><span class="kpi-lbl">访客</span><span class="kpi-sub">新访客 96</span></div>
        <div class="kpi"><span class="kpi-num">342</span><span class="kpi-lbl">访问</span><span class="kpi-sub">日均 11.4</span></div>
        <div class="kpi"><span class="kpi-num">615</span><span class="kpi-lbl">阅读</span><span class="kpi-sub">人均 4.8</span></div>
        <div class="kpi"><span class="kpi-num">207</span><span class="kpi-lbl">AI 调用</span><span class="kpi-sub">tokens 1.2M</span></div>
        <div class="kpi"><span class="kpi-num">9</span><span class="kpi-lbl">转化</span><span class="kpi-sub">转化率 7.0%</span></div>
      </section>
      <section class="surface-card card-pad rounded-[var(--r-card)]" style="margin-top:12px" aria-label="游客走势">
        <div class="guest-chart">${guestChart()}</div>
        <div class="guest-legend"><span><i style="background:var(--dorami-blue)"></i>每日访客</span><span><i style="background:var(--state-ok);border-radius:50%"></i>当日登录(转化)</span></div>
        <div class="guest-entry">
          <div class="guest-entry-head">入口<span class="zone-hint">按访客当天首个入口</span></div>
          <div class="guest-entry-bar"><span class="ge-deep" style="width:58%"></span><span class="ge-login" style="width:19%"></span><span class="ge-share" style="width:9%"></span><span class="ge-return" style="width:14%"></span></div>
          <div class="guest-legend"><span><i class="ge-deep"></i>站内链接 58%</span><span><i class="ge-login"></i>登录页「先逛逛」19%</span><span><i class="ge-share"></i>公开分享页 9%</span><span><i class="ge-return"></i>回访 14%</span></div>
        </div>
      </section>
    </div>`);
    growth.after(zone);
    zone.addEventListener('click', (e) => {
      const sw = e.target.closest('[data-switch]');
      if (!sw) return;
      const on = !sw.classList.contains('is-on');
      sw.classList.toggle('is-on', on); sw.setAttribute('aria-checked', String(on));
      const guestOn = $('[data-switch="guest"]', zone).classList.contains('is-on');
      const aiOn = $('[data-switch="ai"]', zone).classList.contains('is-on');
      $('[data-light]', zone).classList.toggle('is-off', !guestOn);
      $('[data-meta]', zone).textContent = !guestOn
        ? '已关闭:未登录打开站内链接回到登录页,在线游客会话立即失效'
        : (aiOn ? '未登录打开站内链接直接以游客浏览 · 每位游客的 AI 日额度与读者相同' : '游客照常浏览;AI 入口对游客隐藏');
    });
    // 游客设置抽屉(ledger-drawer 语法,同运维用户抽屉)
    const sources = ['哆啦美·AI资讯日报', '量子位', 'IT之家 AI', '新智元', 'The Decoder', 'OpenAI 新闻', 'Anthropic 新闻', 'Google DeepMind 博客', 'X · OpenAI'];
    const row = (lbl, sub, val) => `<label class="guest-limit-row"><span>${lbl}${sub ? `<small>${sub}</small>` : ''}</span><input class="guest-limit-input" inputmode="numeric" value="${val}" aria-label="${lbl}每日上限"></label>`;
    const drawer = el(`<div>
      <div class="ledger-scrim" data-drawer-close aria-hidden="true"></div>
      <aside class="ledger-drawer" role="dialog" aria-modal="true" aria-label="游客设置">
        <div class="ledger-drawer-head"><span class="ledger-drawer-title">游客设置<small class="drawer-title-sub">每位游客各自计数,按服务端日期零点重置</small></span><button type="button" class="icon-button shrink-0" aria-label="关闭" data-drawer-close>${svg(P.x, 'h-5 w-5')}</button></div>
        <div class="ledger-drawer-body">
          <div>
            <div class="drawer-sec-title">每位游客的 AI 日额度 · 缺省与读者相同,0 = 不限</div>
            ${row('翻译', '文章 / 社交 / 逐字稿 / 长文后台翻译,按调用计', 50)}
            ${row('问答', '按提问计', 100)}
            ${row('速读', '按生成计', 50)}
            ${row('点播', '播客导读 + 文章旁白,按新开一次生成计', 5)}
          </div>
          <div>
            <div class="drawer-sec-title">游客的订阅 = 新账号默认订阅(9 个来源)</div>
            <div class="guest-defsrc">${sources.map((s) => `<span>${s}</span>`).join('')}</div>
            <p class="tiny-meta" style="margin-top:10px">在「内容」子页的「新账号默认订阅」里改,游客与此后新建的账号同时生效;改动当天游客早报会自动重编。</p>
          </div>
        </div>
        <div class="ledger-drawer-foot"><button type="button" class="action-button action-button-quiet min-h-[32px] px-3 text-xs" data-drawer-close>取消</button><button type="button" class="action-button action-button-primary min-h-[32px] px-3 text-xs" data-drawer-close>保存</button></div>
      </aside>
    </div>`);
    document.body.appendChild(drawer);
    const setDrawer = (open) => { $$('.ledger-scrim, .ledger-drawer', drawer).forEach((n) => n.classList.toggle('is-open', open)); };
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-drawer]')) setDrawer(true);
      else if (e.target.closest('[data-drawer-close]')) setDrawer(false);
    });
    if (S.drawer) setDrawer(true);
    document.body.appendChild(el('<div class="gs-sample-note">游客区数字为示意</div>'));
  }

  // ── 点击归类:需要账号的动作弹登录提示;其余保持样页静止(不跳转) ──
  function classify(t) {
    const text = (t.textContent || '').trim();
    const label = t.getAttribute('aria-label') || t.getAttribute('title') || '';
    if (t.matches('[data-act]')) return { action: t.dataset.act };
    if (t.matches('.reader-fav-toggle') || label === '只看收藏') return { action: 'favlist' };
    if (label === '收藏' || label === '取消收藏' || t.matches('.reader-entry-fav')) return { action: 'favorite' };
    if (t.matches('.m-row') && /^收藏/.test(text)) return { action: 'favlist' };
    if (t.matches('.reader-axis-btn') && /兴趣/.test(text)) return { action: 'interest' };
    if (t.matches('.reader-seg-btn') && text === '兴趣' && t.closest('.reader-disc-viewseg, .reader-disc-head')) return { action: 'interest' };
    if (t.matches('.m-row') && /我的兴趣/.test(text)) return { action: 'interest' };
    if (t.matches('.reader-disc-bulk-sub')) return { action: 'subscribeAll' };
    if (t.matches('.reader-disc-add')) return { action: 'addsrc' };
    if (t.matches('.reader-disc-sub')) {
      const name = t.closest('.reader-disc-card')?.querySelector('.reader-disc-name')?.textContent.trim();
      return t.matches('.is-subbed') ? { action: 'unsub' } : { action: 'subscribe', vars: { name } };
    }
    if (t.matches('.reader-pin') || label === '取消订阅') return { action: 'unsub' };
    if (label === '反馈与建议' || (t.matches('.m-row') && /反馈与建议/.test(text))) return { action: 'feedback' };
    if (S.scene === 'ai' && (t.matches('.reader-ai-send, .reader-ai-suggest-btn') || label === '译为中文')) return { action: 'ai' };
    return null;
  }

  function wire() {
    document.addEventListener('click', (e) => {
      const t = e.target.closest('button, [role="button"], a');
      if (!t || t.closest('[data-guest-prompt]')) return;
      if (t.closest('.ledger-drawer, [data-drawer], .ledger-scrim')) return;
      if (t.matches('[data-login]')) { e.preventDefault(); goLogin(); return; }
      if (t.matches('[data-back]')) { e.preventDefault(); go({ scene: S.back, from: null, back: null }); return; }
      if (t.matches('[data-guest-enter]')) { e.preventDefault(); go({ scene: 'brief', from: null, back: null }); return; }
      if (t.matches('[data-goto]')) { e.preventDefault(); go({ scene: t.dataset.goto, view: null }); return; }
      if (t.matches('.m-row') && /发现更多来源/.test(t.textContent)) { e.preventDefault(); go({ scene: 'discover' }); return; }
      const hit = classify(t);
      if (hit) { e.preventDefault(); e.stopPropagation(); openPrompt(hit.action, hit.vars); return; }
      if (t.tagName === 'A') e.preventDefault();
    }, true);
  }

  async function boot() {
    const name = SNAP[S.scene][MOBILE ? 1 : 0];
    if (!name) {
      document.body.innerHTML = '<p class="tiny-meta" style="padding:24px">运维管理面没有移动端形态,只看桌面画面。</p>';
      return;
    }
    const html = await fetch(`assets/snap/${name}.html`).then((r) => r.text());
    document.body.innerHTML = html;
    if (S.scene !== 'login' && S.scene !== 'share' && S.scene !== 'admin') guestShell();
    if (S.scene === 'share' && S.view === 'menu') guestShell();
    ({
      deeplink: () => { if (S.toast) showEntryToast(); },
      prompt: () => openPrompt(S.action, { name: 'Claude Code Changelog' }),
      login: setupLogin,
      brief: setupBrief,
      discover: () => {},
      share: () => { if (S.view === 'page') setupSharePage(); },
      ai: setupAi,
      settings: setupSettings,
      admin: setupAdmin,
    })[S.scene]();
    wire();
  }

  boot();
})();
