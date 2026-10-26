import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { INITIAL_JOBS } from './fixtures';

// 正文分段翻译的本地模拟(样页专用,不调用任何接口):一份「服务端持久任务」的替身。
// 现网的事实源是 translation_jobs / translation_chunks;这里只模拟读者能看到的投影——
// 状态、已完成的片序号、暂停原因。后台并发:片按「完成顺序」逐个落库,不要求连续;
// 读者面怎么显示由中文视图的显示方式决定:逐段占位 = 哪片完成显示哪片(其余是原位骨架);
// 连续展示 = 只显示从第 1 片起连续完成的前缀(prefixLength),后面先完成的片只缓存、等前面补齐再一次接上。
//
// status:queued 等待 | running 翻译中 | paused 暂停(quota 额度用尽 / interrupted 中断 / null 可继续)
//        | failed 失败 | cancelled 已取消 | succeeded 全部完成
// live:本次会话里看着它完成的任务;false = 早已存在的完整缓存。

const ACTIVE = new Set(['queued', 'running']);
const STOPPED = new Set(['paused', 'failed', 'cancelled']);

export const isActiveJob = (job) => Boolean(job && ACTIVE.has(job.status));
// 读者能否立刻点「继续」:额度用尽的暂停不能——立即重试突破不了额度,要等额度恢复
export const canResumeJob = (job) => Boolean(job && STOPPED.has(job.status) && job.reason !== 'quota');

// 从第 1 片起连续完成的片数 = 中文视图可显示到哪里
export function prefixLength(job) {
  if (!job) return 0;
  let count = 0;
  while (count < job.total && job.done.includes(count)) count += 1;
  return count;
}

// 中文视图里「看得见哪些片」的签名,供阅读锚点判断是否要介入:
// 连续展示看前缀长度(后片仅缓存不变);逐段占位看已完成集合(任意一片换入都算)
export function shownKey(view, mode, job) {
  if (view !== 'zh' || !job) return '';
  if (mode === 'prefix') return `p${prefixLength(job)}`;
  return `s${[...job.done].sort((a, b) => a - b).join('.')}`;
}

// 完成顺序(都是并发下各片回来的先后,不是串行请求):
// shuffled 末片先回、其余按序(3 片 = 3→1→2,2 片 = 2→1);firstSlow 首片最慢(3 片 = 2→3→1);sequential 1→2→3
export function completionOrder(total, mode) {
  const indexes = Array.from({ length: total }, (_, index) => index);
  if (total < 2 || mode === 'sequential') return indexes;
  if (mode === 'firstSlow') return [...indexes.slice(1), 0];
  return [total - 1, ...indexes.slice(0, -1)];
}

const cloneJobs = (jobs) => Object.fromEntries(
  Object.entries(jobs).map(([id, job]) => [id, { ...job, done: [...job.done] }]),
);

export function jobsReducer(jobs, action) {
  const job = jobs[action.id];
  const put = (next) => ({ ...jobs, [action.id]: next });
  switch (action.type) {
    case 'start':
      if (job) return jobs;
      return put({ status: action.queued ? 'queued' : 'running', reason: null, done: [], total: action.total, live: true, failNext: false });
    case 'run':
      return job?.status === 'queued' ? put({ ...job, status: 'running' }) : jobs;
    case 'complete': {
      if (!isActiveJob(job)) return jobs;
      if (job.failNext) return put({ ...job, status: 'failed', reason: null, failNext: false });
      const order = completionOrder(job.total, action.order);
      const next = order.find((index) => !job.done.includes(index));
      if (next === undefined) return put({ ...job, status: 'succeeded' });
      const done = [...job.done, next];
      return put({ ...job, status: done.length >= job.total ? 'succeeded' : 'running', done });
    }
    case 'pause':
      return isActiveJob(job) ? put({ ...job, status: 'paused', reason: action.reason || 'quota', failNext: false }) : jobs;
    case 'restoreQuota':
      return job?.status === 'paused' && job.reason === 'quota' ? put({ ...job, reason: null }) : jobs;
    case 'failNext':
      return isActiveJob(job) ? put({ ...job, failNext: true }) : jobs;
    case 'resume':
      // 已完成(含尚未显示)的片原样保留,只补缺的片
      return canResumeJob(job) ? put({ ...job, status: action.queued ? 'queued' : 'running', reason: null, failNext: false }) : jobs;
    case 'cancel':
      return isActiveJob(job) ? put({ ...job, status: 'cancelled', reason: null, failNext: false }) : jobs;
    case 'reset': {
      const rest = { ...jobs };
      delete rest[action.id];
      const initial = INITIAL_JOBS[action.id];
      if (initial) rest[action.id] = { ...initial, done: [...initial.done] };
      return rest;
    }
    case 'resetAll':
      return cloneJobs(INITIAL_JOBS);
    default:
      return jobs;
  }
}

// pacing:auto 按节拍落下一片(模拟并发请求陆续返回);manual 只在演示控制点「完成下一片」时推进
export function useTranslationSim({ pacing, order, queueFirst }) {
  const [jobs, dispatch] = useReducer(jobsReducer, INITIAL_JOBS, cloneJobs);
  const orderRef = useRef(order);
  useEffect(() => { orderRef.current = order; }, [order]);
  const timers = useRef(new Map());

  useEffect(() => {
    const map = timers.current;
    Object.entries(jobs).forEach(([id, job]) => {
      const wanted = pacing === 'auto' && isActiveJob(job);
      const key = `${job.status}:${job.done.length}`;
      const existing = map.get(id);
      if (existing && (!wanted || existing.key !== key)) {
        clearTimeout(existing.timer);
        map.delete(id);
      }
      if (wanted && !map.has(id)) {
        const delay = job.status === 'queued' ? 1500 : job.done.length === 0 ? 2400 : 1800;
        const action = job.status === 'queued' ? { type: 'run', id } : { type: 'complete', id };
        const timer = setTimeout(() => {
          map.delete(id);
          dispatch({ ...action, order: orderRef.current });
        }, delay);
        map.set(id, { key, timer });
      }
    });
    map.forEach((entry, id) => {
      if (!jobs[id]) {
        clearTimeout(entry.timer);
        map.delete(id);
      }
    });
  }, [jobs, pacing]);

  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((entry) => clearTimeout(entry.timer));
      map.clear();
    };
  }, []);

  const start = useCallback((id, total) => dispatch({ type: 'start', id, total, queued: queueFirst }), [queueFirst]);
  const resume = useCallback((id) => dispatch({ type: 'resume', id, queued: queueFirst }), [queueFirst]);
  const cancel = useCallback((id) => dispatch({ type: 'cancel', id }), []);
  // 手动推进:排队中的先进入翻译中,再落下一片
  const step = useCallback((id) => {
    dispatch({ type: 'run', id });
    dispatch({ type: 'complete', id, order: orderRef.current });
  }, []);
  const pause = useCallback((id, reason) => dispatch({ type: 'pause', id, reason }), []);
  const restoreQuota = useCallback((id) => dispatch({ type: 'restoreQuota', id }), []);
  const failNext = useCallback((id) => dispatch({ type: 'failNext', id }), []);
  const reset = useCallback((id) => dispatch({ type: 'reset', id }), []);
  const resetAll = useCallback(() => dispatch({ type: 'resetAll' }), []);

  const actions = useMemo(
    () => ({ start, resume, cancel, step, pause, restoreQuota, failNext, reset, resetAll }),
    [start, resume, cancel, step, pause, restoreQuota, failNext, reset, resetAll],
  );
  return { jobs, actions };
}
