// 最近一次输入方式(issue #230 层原语;口径同 PlayerProvider 的 inputRef,conventions §2「只在键盘操作后程序化移焦点」):
// 键盘打开的层把焦点落到第一个控件;指针 / 触屏打开的层只把焦点落在面板容器上(读屏照样进得了层,
// 但不会在 Safari 上给按钮画出多余的焦点环)。第一次调用时才挂监听,捕获阶段,不拦事件。
//
// 同一个值也写到 <html data-input="key | pointer">(issue #301):样式表里的状态过渡拿不到上面这个函数,
// 「键盘触发的不动」在 CSS 里靠 :root:not([data-input="key"]) 判(桌面读者面的按下缩放、提示弹入)。只在变化时写一次。
let last = 'pointer';
let installed = false;

function set(next) {
  if (last === next) return;
  last = next;
  if (typeof document !== 'undefined') document.documentElement.setAttribute('data-input', next);
}

function install() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', (event) => {
    if (!event.metaKey && !event.ctrlKey && !event.altKey) set('key');
  }, true);
  window.addEventListener('pointerdown', () => { set('pointer'); }, true);
}

export function lastInputWasKeyboard() {
  install();
  return last === 'key';
}

// 层原语在模块加载时先挂上,免得「第一次就用键盘打开」时还没开始记
install();
