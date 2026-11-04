// 浏览器地址栏 / PWA 状态栏的颜色跟应用主题(审计 M-32;issue #230 批 4a)。
// index.html 的两条 <meta name="theme-color"> 只按系统深浅写死,应用内切暗色、换中性色(瓷 / 墨 / 宣)时地址栏与
// 主屏 App 的状态栏不跟。手机壳挂着时改为运行时取壳的底色(.m-shell 的 background = --rd-list-bg),
// <html> 的 data-theme / data-palette 一变就重取;壳卸下(换回桌面四栏)时停表并把两条 meta 还原成原值,桌面不变。
// 颜色经 canvas 规整成 #rrggbb(token 里有 color-mix / oklch,meta 不一定认);两条 meta 写同一个值,哪条命中都对。

const META = 'meta[name="theme-color"]';

// 任意 CSS 颜色 → #rrggbb;解析不了或全透明返回 ''(不改 meta)
export function hexOfColor(color, doc = typeof document === 'undefined' ? null : document) {
  if (!color || !doc) return '';
  try {
    const canvas = doc.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return '';
    ctx.fillStyle = 'transparent';
    ctx.fillStyle = color;   // 认不出的颜色保持上一个值(透明)
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    if (a === 0) return '';
    return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
  } catch {
    return '';
  }
}

let frame = 0;

// 按当前界面重写 theme-color(下一帧取,等样式落定);没有手机壳时什么都不做
export function syncThemeColor() {
  if (typeof document === 'undefined' || typeof window === 'undefined') return;
  window.cancelAnimationFrame(frame);
  frame = window.requestAnimationFrame(() => {
    const shell = document.querySelector('.m-shell');
    if (!shell) return;
    // 取底色变量(--rd-list-bg,已代入成具体颜色)而不是 background-color:换主题时底色有过渡,过渡中读到的是旧色
    const style = getComputedStyle(shell);
    const color = hexOfColor(style.getPropertyValue('--rd-list-bg').trim()) || hexOfColor(style.backgroundColor);
    if (!color) return;
    document.querySelectorAll(META).forEach((meta) => {
      if (meta.getAttribute('content') !== color) meta.setAttribute('content', color);
    });
  });
}

// 手机壳挂载时装上(返回卸载函数):盯 <html> 的主题与中性色属性;卸载时还原 meta 原值
export function installThemeColorSync() {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return () => {};
  const metas = [...document.querySelectorAll(META)];
  const original = metas.map((meta) => meta.getAttribute('content'));
  const observer = new MutationObserver(syncThemeColor);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-palette'] });
  syncThemeColor();
  return () => {
    observer.disconnect();
    if (typeof window !== 'undefined') window.cancelAnimationFrame(frame);
    metas.forEach((meta, i) => {
      if (original[i] != null) meta.setAttribute('content', original[i]);
    });
  };
}
