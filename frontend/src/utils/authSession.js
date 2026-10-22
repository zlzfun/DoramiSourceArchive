/**
 * 认证请求串行与 401 处置(issue #213 检视 F4)。
 *
 * - 会话代次在 api.js(`authGeneration()` / `bumpAuthGeneration()`):每次 Cookie 换身份——登录、游客进入或续期、
 *   退出 / 回落匿名——由调用点显式 +1;`apiFetch` 发请求时记下当时的代次,401 事件 detail 带 `{ gen }`。
 * - 改认证 Cookie 的请求(登录、`/auth/guest`、退出、续期)一律经 `authQueue.run` 串行:登录进行中不会起续期,
 *   续期进行中也不会并发登录,两路 Set-Cookie 不会反序互盖。代次只在队列任务里推进,任务内部的 await 之间
 *   不会有别的身份切换插进来。
 * - 401 的处置见 `decideAuthExpired`:旧身份发出的请求晚到 → 忽略;游客且距上次续期超过冷却 → 续期;
 *   其余(正式账号,或续期后冷却期内新身份又失效)→ 回登录页。同批并发的 401 各排一个任务,第一个续期成功后
 *   代次已变,其余任务自然判为「旧身份」而忽略。
 */

export const GUEST_RESUME_COOLDOWN_MS = 60000;

/** 串行队列:任务按提交顺序一个接一个跑,前一个失败不影响后一个;run 返回该任务自己的结果。 */
export function createSerialQueue() {
  let tail = Promise.resolve();
  return {
    run(task) {
      const result = tail.then(() => task());
      tail = result.then(() => undefined, () => undefined);
      return result;
    },
  };
}

export const authQueue = createSerialQueue();

/**
 * 一次 401 该怎么处置:'ignore' | 'resume' | 'reset'。
 * eventGen 缺省(非 apiFetch 派发)按当前代次处理。
 */
export function decideAuthExpired({ eventGen, currentGen, isGuest, now, lastResumeAt }) {
  if (Number.isInteger(eventGen) && eventGen !== currentGen) return 'ignore';
  if (isGuest && now - (lastResumeAt || 0) > GUEST_RESUME_COOLDOWN_MS) return 'resume';
  return 'reset';
}
