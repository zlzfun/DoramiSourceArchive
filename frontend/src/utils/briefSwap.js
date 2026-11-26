// 早报页换版里不依赖 React 与 DOM 的几件(issue #343),抽出来是为了能单独测:
//   请求先后(迟到的旧响应不采用)、换版排程(旧版面淡出 → 放上新的 → 那次提交里收尾)、
//   带着版本号看今天时跟着换到当前版、眼前是不是今天一个被换下的版次。
// 页面怎么用它们见 components/PersonalBriefPage.jsx;动效本身在 utils/spatialMotion 的 leaveBrief / swapInBrief。

const SETTLED_TODAY = new Set(['ready', 'degraded', 'empty_subscriptions']);

/**
 * 今日早报的请求先后。轮询、切回页面、首次载入的请求可以重叠,回来的顺序不保证:
 * 更晚发出的请求已经被采用之后,更早发出的那一份(成功或失败)一律作废——它看到的是更旧的服务端状态。
 */
export function createRequestOrder() {
  let issued = 0;
  let adopted = 0;
  return {
    begin() { issued += 1; return issued; },
    /** 这一份还作数吗(没有更晚发出的请求先被采用)。不改状态,失败的响应用它判。 */
    current(seq) { return seq >= adopted; },
    /** 采用这一份;已经过期就返回 false。 */
    adopt(seq) {
      if (seq < adopted) return false;
      adopted = seq;
      return true;
    },
  };
}

/**
 * 换版排程。系统换上来的内容不硬切:
 *   run(kind, apply, leave)   旧版面开始淡出(leave() 给出动画与时长),到点 apply() 把新的放上去,然后 onLand()
 *                      (页面在 onLand 里触发一次提交,并在那次提交里调 settle());
 *                      淡出期间再来的同一类(kind)以最后一份为准,不同类各留一份、到点一起放(这时不再调 leave)。
 *   drop(kind)         撤掉一类还没放上去的(读者自己切了日期,「回页首换当前版」那一件就不该再执行)。
 *   settle()           新内容放上去的那次提交里调用:返回 { animations, applied }——animations 是要撤掉的淡出动画
 *                      (留在场的节点带着 forwards 的终帧),applied 是这次真的放了几件(0 件就不演「换上来」);
 *                      没有待收尾的返回 null。
 *   dispose()          卸载:清计时器,返回还没撤的动画。
 * leave() 返回 { animations, duration };duration 为 0(减少动效、窗口在后台、不支持动画)时当场放上去。
 */
export function createSoftSwap({ onLand, setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id) }) {
  let job = null;

  const run = (kind, apply, leave) => {
    if (job && !job.landed) {
      job.applies[kind] = apply;
      return;
    }
    const out = leave?.() || { animations: [], duration: 0 };
    // 上一件刚放上去、那次提交还没到(job.landed 而 settle 还没被调用):它的淡出动画一并交给这一件撤
    const next = { applies: { [kind]: apply }, animations: [...(job?.animations || []), ...out.animations], landed: false, applied: 0, timer: null };
    const land = () => {
      next.timer = null;
      next.landed = true;
      const todo = Object.values(next.applies);
      next.applied = todo.length;
      todo.forEach((fn) => fn());
      onLand();
    };
    job = next;
    if (out.duration) next.timer = setTimer(land, out.duration);
    else land();
  };

  return {
    run,
    /** 旧版面正在淡出、新的还没放上去。 */
    leaving: () => Boolean(job && !job.landed),
    drop(kind) {
      if (job && !job.landed) delete job.applies[kind];
    },
    settle() {
      if (!job?.landed) return null;
      const { animations, applied } = job;
      job = null;
      return { animations, applied };
    },
    dispose() {
      if (job?.timer != null) clearTimer(job.timer);
      const animations = job?.animations || [];
      job = null;
      return animations;
    },
  };
}

/** 读者订阅范围的指纹:订阅集合的内容变了它才变(集合对象换了、内容没变不算)。 */
export function scopeKeyOf(subscribedIds) {
  return [...(subscribedIds || [])].sort().join('|');
}

/**
 * 带着版本号看今天(从正文返回时留的位置),而今天这一版换了:选择跟着换到当前版,不留在被换下的那一版上。
 * 只在「选中的正是换之前的今天这一版」时跟;看往期、看今天别的版次都不动。返回原对象表示不变。
 */
export function followToday(sel, prevToday, nextToday) {
  const prev = prevToday?.edition;
  if (!prev || sel?.revision == null) return sel;
  if (sel.date != null && sel.date !== prev.report_date) return sel;
  if (sel.revision !== prev.revision) return sel;
  if (nextToday?.edition?.id === prev.id) return sel;
  return { date: null, revision: null };
}

/**
 * 眼前是今天一个被换下的版次:今天已经有编好的当前版(或已经是空订阅的引导),而选中的不是它。
 * 当天只有一版(被换下的不进往期),所以不必看那一版自己的状态——它的详情可能是换下之前取的。
 * 今天还在编 / 失败时不算:那时选中的可能正是最近一份编好的。
 */
export function isReplacedView({ isToday, usesToday, todayStatus, revision }) {
  return Boolean(isToday && !usesToday && revision != null && SETTLED_TODAY.has(todayStatus));
}
