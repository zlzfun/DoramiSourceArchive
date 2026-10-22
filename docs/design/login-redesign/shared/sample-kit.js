/* 登录页重设计样页 · 共用控制条(样页工具,不属于任何设计方向)
 *
 * 用法:在方向页 <head> 里同步引入(先于页面脚本),好让 data-motion 在首帧前就位:
 *   <script src="../shared/sample-kit.js"></script>
 *
 * 方向页契约(页面脚本里注册):
 *   window.LoginSample = {
 *     replay(),            // 从头重播开场
 *     skipIntro(),         // 立即跳到开场完成态(截图 / 减弱动效用)
 *     setState(name),      // 'idle' | 'loading' | 'error' | 'success'
 *     extraControls: [{ label, run }],   // 可选:方向专属的样页按钮
 *   };
 *   动效档位:读 document.documentElement.dataset.motion('full' | 'reduce'),
 *   并监听 window 'sample:motion' 事件({ detail: { reduced } })随时切换。
 *   全局「任意键 / 点击跳过」类监听要忽略落在 [data-sample-kit] 内的事件。
 *
 * URL 参数:motion=reduce|full · intro=skip · state=loading|error|success · kit=0(隐藏控制条)
 * 被 iframe 嵌入(索引页)时控制条自动隐藏。
 */
(function () {
  const params = new URLSearchParams(location.search);
  const root = document.documentElement;
  const embedded = window.top !== window || params.get('kit') === '0';

  let stored = null;
  try { stored = localStorage.getItem('login-sample-motion'); } catch (e) { /* 隐私模式 */ }
  root.dataset.motion = params.get('motion') || (embedded ? null : stored) || 'full';

  function api() { return window.LoginSample || null; }

  function setMotion(mode) {
    root.dataset.motion = mode;
    try { localStorage.setItem('login-sample-motion', mode); } catch (e) { /* 忽略 */ }
    window.dispatchEvent(new CustomEvent('sample:motion', { detail: { reduced: mode === 'reduce' } }));
    render();
  }

  function applyParams() {
    const s = api();
    if (!s) return;
    if (params.get('intro') === 'skip' || params.get('state')) s.skipIntro && s.skipIntro();
    const state = params.get('state');
    if (state && s.setState) setTimeout(() => s.setState(state), 60);
  }

  let panel = null;
  function button(label, onClick, pressed) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'sk-btn';
    b.textContent = label;
    if (pressed != null) b.setAttribute('aria-pressed', String(pressed));
    b.addEventListener('click', onClick);
    return b;
  }

  function render() {
    if (!panel) return;
    panel.textContent = '';
    const s = api();
    const reduced = root.dataset.motion === 'reduce';
    const tag = document.createElement('span');
    tag.className = 'sk-tag';
    tag.textContent = '样页';
    panel.append(tag);
    panel.append(button('↻ 重播', () => s && s.replay && s.replay()));
    panel.append(button(reduced ? '动效:减弱' : '动效:完整', () => setMotion(reduced ? 'full' : 'reduce'), reduced));
    panel.append(button('登录中', () => s && s.setState && s.setState('loading')));
    panel.append(button('出错', () => s && s.setState && s.setState('error')));
    panel.append(button('成功', () => s && s.setState && s.setState('success')));
    if (s && Array.isArray(s.extraControls)) {
      for (const c of s.extraControls) panel.append(button(c.label, () => { c.run(); render(); }));
    }
    const back = document.createElement('a');
    back.className = 'sk-btn';
    back.href = '../index.html';
    back.textContent = '全部方向';
    panel.append(back);
  }

  function mountPanel() {
    const css = document.createElement('style');
    css.textContent = `
      [data-sample-kit]{position:fixed;left:12px;bottom:12px;z-index:2147483000;display:flex;flex-wrap:wrap;gap:4px;align-items:center;
        max-width:calc(100vw - 24px);padding:5px;border-radius:12px;background:rgba(18,18,24,.78);backdrop-filter:blur(10px);
        -webkit-backdrop-filter:blur(10px);box-shadow:0 6px 24px rgba(0,0,0,.28),inset 0 0 0 1px rgba(255,255,255,.08);
        font:500 12px/1 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#e8e8f0;
        opacity:.42;transition:opacity .2s}
      [data-sample-kit]:hover,[data-sample-kit]:focus-within{opacity:1}
      [data-sample-kit] .sk-tag{padding:0 6px;color:#9a9ab0;letter-spacing:.08em}
      [data-sample-kit] .sk-btn{all:unset;cursor:pointer;padding:6px 9px;border-radius:8px;color:#e8e8f0;white-space:nowrap}
      [data-sample-kit] .sk-btn:hover{background:rgba(255,255,255,.1)}
      [data-sample-kit] .sk-btn:focus-visible{outline:2px solid #8f8cf0;outline-offset:1px}
      [data-sample-kit] .sk-btn[aria-pressed=true]{background:rgba(143,140,240,.28)}
    `;
    document.head.append(css);
    panel = document.createElement('div');
    panel.setAttribute('data-sample-kit', '');
    panel.setAttribute('aria-label', '样页控制条');
    for (const type of ['pointerdown', 'mousedown', 'click', 'keydown']) {
      panel.addEventListener(type, (e) => e.stopPropagation());
    }
    document.body.append(panel);
    render();
  }

  window.addEventListener('load', () => {
    // 页面脚本可能晚于 load 才注册 LoginSample:最多等 3 秒
    let tries = 0;
    const wait = () => {
      if (api() || tries++ > 60) {
        if (!embedded) mountPanel();
        applyParams();
        return;
      }
      setTimeout(wait, 50);
    };
    wait();
  });

  window.SampleKit = { setMotion, render };
})();
