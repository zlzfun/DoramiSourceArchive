// 最近一次输入方式(issue #230 层原语;口径同 PlayerProvider 的 inputRef,conventions §2「只在键盘操作后程序化移焦点」):
// 键盘打开的层把焦点落到第一个控件;指针 / 触屏打开的层只把焦点落在面板容器上(读屏照样进得了层,
// 但不会在 Safari 上给按钮画出多余的焦点环)。第一次调用时才挂监听,捕获阶段,不拦事件。
let last = 'pointer';
let installed = false;

function install() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', (event) => {
    if (!event.metaKey && !event.ctrlKey && !event.altKey) last = 'key';
  }, true);
  window.addEventListener('pointerdown', () => { last = 'pointer'; }, true);
}

export function lastInputWasKeyboard() {
  install();
  return last === 'key';
}

// 层原语在模块加载时先挂上,免得「第一次就用键盘打开」时还没开始记
install();
