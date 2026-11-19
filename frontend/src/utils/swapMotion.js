// 页内换内容时新内容的进场(issue #301 第七批对拍补差,样页 dir-s.js 的 filter 一支后半段;conventions §7「连续」):
//   换页签——榜单的三张榜、发现页的「源 / 合集 / 兴趣」、播客阅读窗的「节目简介 / 导读稿」;
//   滚下去之后的筛选 / 换范围——发现页的形态筛选、榜单的时间范围(停在页顶时是留下的条目滑到新位置,见 listFlip)。
// 新内容淡入并上移一小段(move 弹簧,落定 583ms;榜单与发现页 12px,播客正文 8px)。
// 有的要回服务端取(榜单):点下去之后先是「加载中」,等新内容真的挂上才演;等不到就算了。
// 键盘触发不做;减少动效只淡入。挂在桌面读者面的外壳上,页面组件不接线。
import { beginContentSwap, fadeIn, holdContentSwap, motionIntent, pointerMotion, scrollerOf, springTo } from './readerMotion.js';

const WAIT_MS = 6000;

// 一处一行:triggers 点了哪颗钮;scope 盯哪一块的子节点变化;targets 演谁;dist 上移多少;when 额外的条件;
// sync 当场就换的(本地筛选):盯的那一块里一有增删就演——往少里筛时只有节点被拿走,没有新节点挂上。
export const SWAP_ZONES = [
  { name: 'rank-tab', triggers: '.rk-page .reader-tabs .reader-tab', scope: '.rk-page', targets: '.rk-page .rk-list, .rk-page .rk-empty', dist: 12 },
  {
    name: 'rank-range', triggers: '.rk-page .rk-range .mini-seg-btn', scope: '.rk-page', targets: '.rk-page .rk-list, .rk-page .rk-empty', dist: 12,
    when: (shell) => (scrollerOf(shell.querySelector('.rk-page .rk-wrap'))?.scrollTop || 0) >= 2,
  },
  { name: 'discover-tab', triggers: '.reader-disc-viewseg .reader-seg-btn', scope: '.reader-disc-body', targets: '.reader-disc-body > *', dist: 12, sync: true },
  {
    name: 'discover-shape', triggers: '.reader-disc-seg .reader-seg-btn', scope: '.reader-disc-body', targets: '.reader-disc-body > *', dist: 12, sync: true,
    when: (shell) => (shell.querySelector(':scope > .reader-disc')?.scrollTop || 0) >= 2,   // 桌面上滚的是发现页这一整块
  },
  { name: 'podcast-tab', triggers: '.reader-pane .podcast-view-seg .reader-tr-seg-btn', scope: '.reader-pane', targets: '.reader-pane .podcast-view-head ~ *', dist: 8 },
];

/** 在 shell(桌面读者面的 .reader-shell)上挂起,返回卸载函数。 */
export function mountSwapMotion(shell) {
  if (!shell || typeof MutationObserver === 'undefined') return () => {};
  let job = null;   // { zone, mode, observer, timer }

  const finish = () => {
    if (!job) return;
    job.observer.disconnect();
    clearTimeout(job.timer);
    job.release();
    job = null;
  };

  const onMutate = (records) => {
    if (!job) return;
    const { zone, mode } = job;
    // 只认「新内容挂上了」:挂上的节点就是目标、在目标里面、或带着目标(「加载中」那一下不算,指示器自己的增删也不算)
    const arrived = zone.sync || records.some((record) => [...record.addedNodes].some((node) => node.nodeType === 1
      && (node.matches(zone.targets) || node.closest(zone.targets) || node.querySelector(zone.targets))));
    if (!arrived) return;
    const targets = [...shell.querySelectorAll(zone.targets)];
    finish();
    if (pointerMotion(mode.intent) !== mode.kind) return;   // 等待期间切了「减少动效」之类:不演了
    for (const el of targets) {
      if (mode.kind === 'fade') fadeIn(el, 120);
      else springTo(el, [{ opacity: 0, transform: `translateY(${zone.dist}px)` }, { opacity: 1, transform: 'none' }], 'move');
    }
  };

  const onClick = (event) => {
    const touched = SWAP_ZONES.map((zone) => [zone, event.target.closest?.(zone.triggers)]).filter(([, el]) => el && shell.contains(el));
    if (!touched.length) return;
    beginContentSwap(event);   // 这一下会换内容:还在等上一批新内容的任务(这一层或 listFlip 那一层的)先撤掉
    const hit = touched.find(([zone]) => !zone.when || zone.when(shell));
    if (!hit) return;
    const [zone, button] = hit;
    if (button.classList.contains('is-on') || button.disabled) return;   // 点的就是当前这一项
    const intent = motionIntent();
    const kind = pointerMotion(intent);
    const scope = shell.querySelector(zone.scope);
    if (kind === 'none' || !scope) return;
    const observer = new MutationObserver(onMutate);
    job = { zone, mode: { intent, kind }, observer, timer: setTimeout(finish, WAIT_MS), release: holdContentSwap(finish) };
    observer.observe(scope, { childList: true, subtree: true });
  };
  shell.addEventListener('click', onClick, true);

  return () => {
    shell.removeEventListener('click', onClick, true);
    finish();
  };
}
