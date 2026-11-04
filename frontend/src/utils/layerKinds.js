// 层原语的种类表与焦点落点(issue #230;components/layer/useLayer 用,纯数据 + 纯函数,单测直接核对)。
//   modal   居中弹窗(components/Modal)   困 Tab · 全部 inert · 首个控件得焦点 · 锁 body 滚动
//   sheet   底页 / 动作单(Sheet)        困 Tab · 全部 inert · 键盘打开落首个控件、指针打开落面板
//   drawer  侧抽屉(Drawer)              同 sheet
//   page    整屏推入页(LayerPage)       不困 Tab(下层已 inert)· inert 保留页面层之上的播放条 · 焦点落面板
//   popover 锚定浮层(往期月历 / 分享)    不困 Tab · 不 inert · 点外面即关
export const LAYER_KINDS = Object.freeze({
  modal: Object.freeze({ trap: true, inert: 'all', focus: 'first', scrollLock: true, outside: false }),
  sheet: Object.freeze({ trap: true, inert: 'all', focus: 'auto', scrollLock: false, outside: false }),
  drawer: Object.freeze({ trap: true, inert: 'all', focus: 'auto', scrollLock: false, outside: false }),
  page: Object.freeze({ trap: false, inert: 'page', focus: 'panel', scrollLock: false, outside: false }),
  popover: Object.freeze({ trap: false, inert: false, focus: 'first', scrollLock: false, outside: true }),
});

// inert 时保留的元素:'all' 只留 Toast 等「永远可达」的;'page' 另留页面层之上的播放条(PlayerDock)
export const INERT_KEEP_SELECTOR = Object.freeze({
  all: '[data-layer-keep="always"]',
  page: '[data-layer-keep]',
});

// 打开时焦点落在哪(conventions §2「只在键盘操作后程序化移焦点」):
// 调用方给了目标就用它;'panel' 恒落面板容器;'auto' 键盘落首个控件、指针落面板容器;'first' 落首个控件。
// 返回 null = 交给 useModalA11y 的缺省(首个可聚焦元素,没有就面板)。
export function initialFocusTarget(focusMode, { keyboard, chosen = null, panel = null }) {
  if (chosen) return chosen;
  if (focusMode === 'panel') return panel;
  if (focusMode === 'auto' && !keyboard) return panel;
  return null;
}
