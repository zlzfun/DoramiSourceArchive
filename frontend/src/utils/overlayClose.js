// 遮罩关闭判定(issue #104):只有「按下与松开都落在遮罩本身」的真正点击才关闭。
//
// DOM 的 click 派发到 mousedown 目标与 mouseup 目标的最近公共祖先——面板内按下、
// 遮罩上松开(拖选文字最常见),公共祖先就是遮罩,裸 onClick={onClose} 会把弹窗关掉、
// 正在填的内容全丢。纯 onMouseDown 关闭(7acd710 首版)反过来让「遮罩按下、拖进面板
// 松手」也立即关,且得靠面板上的 stopPropagation 挡冒泡——React 的 stopPropagation 会
// 拦掉 document 级冒泡监听,弹窗里若嵌了靠「document mousedown 点外关闭」的浮层
// (右键菜单 / 分享浮层同款机制)会失灵。
//
// 这里改为三步核对:pointerdown 记「是否落在遮罩本身」→ pointerup 再核一次 → click 时
// 两者皆真且 click 目标仍是遮罩才算数;pointercancel(触屏滚动接管)清零。pointer 事件让
// 鼠标 / 触屏 / 触控笔一套逻辑;只认主键,右键 / 中键不算。全程只比对 target 与
// currentTarget,不需要面板 stopPropagation。
//
// 纯状态机,只判定不动作(shouldClose 返回布尔并归零,调用方决定关不关),不依赖 DOM 与
// React:Modal.jsx 把三个 pointer handler 铺到遮罩元素上、onClick 里问 shouldClose;
// frontend/test/modalOverlay.test.mjs 用假事件覆盖各路径。
export function createOverlayCloseGuard() {
  let downOnOverlay = false;
  let upOnOverlay = false;
  const hitsOverlay = (event) => event.target === event.currentTarget;
  const reset = () => { downOnOverlay = false; upOnOverlay = false; };
  return {
    onPointerDown(event) {
      downOnOverlay = (event.button ?? 0) === 0 && hitsOverlay(event);
      upOnOverlay = false;
    },
    onPointerUp(event) {
      upOnOverlay = downOnOverlay && hitsOverlay(event);
    },
    onPointerCancel: reset,
    shouldClose(event) {
      const hit = downOnOverlay && upOnOverlay && hitsOverlay(event);
      reset();
      return hit;
    },
  };
}

// 长按松手的「溢出点击」(审计 M-01 余项;issue #230 批 4c 查出):长按出来的动作单在手指还按着时就已弹出,
// 松手时浏览器补发的 mousedown / mouseup / click 落在手指下面的新元素上——矮屏(横屏手机、360 × 640)菜单项正好
// 或抓手盖在手指处,这一下会被当成点了菜单项(悄悄执行「标为已读」之类)或点了抓手(底页随即关掉)。遮罩那条路已由上面的
// guard 挡住,底页里其余的由它挡:打开之后先等到一次「没有对应按下的松手」= 打开它的那次长按还按着,紧随其后的那一下指针
// click 吞掉;打开后有过新的按下(真点)、或键盘激活(click.detail 为 0)都照常。只判定不动作:components/layer/Sheet
// 每次打开建一个,pointerdown / pointerup 挂在 window 捕获阶段,层根的捕获阶段问 shouldSwallow。
export function createSpillClickGuard() {
  let sawDown = false;
  let heldOpen = false;
  return {
    onPointerDown() { sawDown = true; heldOpen = false; },
    onPointerUp() { if (!sawDown) heldOpen = true; },
    shouldSwallow(event) {
      const swallow = heldOpen && !sawDown && (event?.detail ?? 0) > 0;
      heldOpen = false;
      return swallow;
    },
  };
}
