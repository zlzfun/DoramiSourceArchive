import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTranslationJob, resumeTranslationJob, startTranslationJob, translationEventsUrl } from '../api';
import { EMPTY_TRANSLATION, applyTranslationSnapshot, isActiveStatus } from '../utils/translationBlocks';

const POLL_MS = 2000;
const POLL_ERROR_MS = 10000;
// 权限撤销 / 文章不可见 / 原文或配置已变:旧预览与缓存一律不再可信,清空后不再展示
const CLEARS = new Set([403, 404, 409]);

// 正文渐进翻译(issue #218)。服务端的持久任务是唯一事实来源:这里只订阅当前这一篇(AI 可用时)——
// SSE 推送 snapshot(数据同 GET),终态即关流;不支持 / 断开时补一次 GET 取真实状态码,再按约 2 秒轮询回落,
// 同一时刻只有一个请求。切文章、卸载只停订阅,永不取消任务;前端没有取消入口。
// 点「译为中文」立即进入中文视图(未完成块画骨架),已有任务时这个钮只切视图;后台更新从不强切视图。
export function useArticleTranslation(articleId, enabled, showToast, onResultTitle) {
  const [state, setState] = useState(EMPTY_TRANSLATION);
  const stateRef = useRef(EMPTY_TRANSLATION);
  const [showId, setShowId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [watch, setWatch] = useState(0);
  const transportRef = useRef(null);
  const activeIdRef = useRef(articleId);
  const actionSeq = useRef(0);
  const titleRef = useRef(onResultTitle);

  useEffect(() => { activeIdRef.current = articleId; }, [articleId]);
  useEffect(() => { titleRef.current = onResultTitle; }, [onResultTitle]);

  const commit = useCallback((id, data) => {
    const prev = stateRef.current;
    const next = applyTranslationSnapshot(prev, id, data);
    stateRef.current = next;
    setState(next);
    const title = next.result?.title;
    if (typeof title === 'string' && title && title !== (prev.id === id ? prev.result?.title : null)) {
      titleRef.current?.(id, title);
    }
    return next;
  }, []);

  const clear = useCallback((id) => {
    const next = { ...EMPTY_TRANSLATION, id };
    stateRef.current = next;
    setState(next);
  }, []);

  useEffect(() => {
    if (!enabled || !articleId) return undefined;
    const id = articleId;
    let closed = false;
    let timer = null;
    let source = null;
    let inflight = false;

    const closeSource = () => {
      if (source) source.close();
      source = null;
    };
    const stop = () => {
      closed = true;
      clearTimeout(timer);
      closeSource();
    };
    transportRef.current = { stop };

    const cursor = () => {
      const current = stateRef.current;
      return current.id === id && !current.resync ? (current.preview?.cursor || null) : null;
    };
    const failed = (error) => {
      if (closed) return;
      if (CLEARS.has(error?.status)) {
        clear(id);
        return;
      }
      if (error?.status === 401) return; // 会话过期:交给全局的登录态处理
      timer = setTimeout(poll, POLL_ERROR_MS);
    };
    async function fetchOnce() {
      if (closed || inflight) return null;
      inflight = true;
      try {
        const data = await fetchTranslationJob(id, cursor());
        inflight = false;
        return closed ? null : commit(id, data);
      } catch (error) {
        inflight = false;
        failed(error);
        return null;
      }
    }
    async function poll() {
      const next = await fetchOnce();
      if (!next || closed) return;
      if (next.resync) timer = setTimeout(poll, 0);
      else if (isActiveStatus(next.status)) timer = setTimeout(poll, POLL_MS);
    }
    function stream() {
      if (closed) return;
      if (typeof EventSource === 'undefined') {
        timer = setTimeout(poll, POLL_MS);
        return;
      }
      const events = new EventSource(translationEventsUrl(id, cursor()), { withCredentials: true });
      source = events;
      events.addEventListener('snapshot', (event) => {
        if (closed || source !== events) return;
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        const next = commit(id, data);
        if (next.resync) {
          closeSource();
          poll();
        } else if (!isActiveStatus(next.status)) {
          closeSource(); // 终态快照之后服务端断流;先关,免得浏览器自动重连
        }
      });
      // 断线,或流内鉴权失败(服务端发的同名 error 事件):关流,补一次 GET 取真实状态码,之后按轮询回落
      events.addEventListener('error', () => {
        if (closed || source !== events) return;
        closeSource();
        poll();
      });
    }
    (async () => {
      const current = stateRef.current;
      if (current.id === id && isActiveStatus(current.status) && !current.resync) {
        stream();
        return;
      }
      const next = await fetchOnce();
      if (!next || closed) return;
      if (next.resync) poll();
      else if (isActiveStatus(next.status)) stream();
    })();
    return () => {
      stop();
      if (transportRef.current?.stop === stop) transportRef.current = null;
    };
  }, [articleId, enabled, watch, commit, clear]);

  // 额度用尽的停止态(can_resume=false + retry_after 秒):终态不轮询,只在到期时补读一次状态,跨午夜停在同一篇
  // 也能等到「继续翻译」;后台标签页的定时器可能被推迟,回到前台时若已过期同样补读(只读一次)。
  const quotaJob = state.id === articleId && !isActiveStatus(state.status) && state.job?.can_resume === false
    ? state.job : null;
  const retryAfter = Number(quotaJob?.retry_after);
  const quotaDelay = quotaJob && Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 0;
  useEffect(() => {
    if (!enabled || !articleId || !quotaDelay) return undefined;
    const waitMs = quotaDelay * 1000 + 1000;
    const due = Date.now() + waitMs;
    let fired = false;
    const refresh = () => {
      if (fired) return;
      fired = true;
      setWatch((value) => value + 1); // 重启订阅 = 不带游标之外的同一次 GET;仍在额度停止态则按新的 retry_after 再排
    };
    const timer = setTimeout(refresh, waitMs);
    const onVisible = () => {
      if (document.visibilityState !== 'hidden' && Date.now() >= due) refresh();
    };
    const hasDocument = typeof document !== 'undefined';
    if (hasDocument) document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearTimeout(timer);
      if (hasDocument) document.removeEventListener('visibilitychange', onVisible);
    };
  }, [articleId, enabled, quotaDelay]);

  const act = useCallback(async (operation, { leaveViewOnError = false } = {}) => {
    const id = articleId;
    if (!id || busyId === id) return;
    transportRef.current?.stop(); // 动作期间停订阅,旧快照不与动作响应交错;结束后按最新状态重启
    const seq = ++actionSeq.current;
    setBusyId(id);
    try {
      const data = await operation();
      if (seq !== actionSeq.current || activeIdRef.current !== id) return;
      commit(id, data);
      if (data?.evicted_progress) showToast('已清理最早暂停任务的进度，为本次翻译留出空间', 'info');
    } catch (error) {
      if (seq !== actionSeq.current || activeIdRef.current !== id) return;
      if (CLEARS.has(error?.status)) clear(id);
      if (leaveViewOnError) setShowId((current) => (current === id ? null : current));
      // 游客登录提示 / 额度提示已由 api 层统一弹出,这里不再叠一条 toast
      if (!error?.loginPrompted) showToast(error?.message || '翻译操作失败，请稍后重试', 'error');
    } finally {
      setBusyId((current) => (current === id ? null : current));
      if (seq === actionSeq.current) setWatch((value) => value + 1);
    }
  }, [articleId, busyId, clear, commit, showToast]);

  const handleTranslate = useCallback(() => {
    if (!articleId) return;
    if (showId === articleId) {
      setShowId(null);
      return;
    }
    setShowId(articleId);
    const current = stateRef.current;
    const known = current.id === articleId && Boolean(current.result || current.preview || current.job);
    if (!known) act(() => startTranslationJob(articleId), { leaveViewOnError: true });
  }, [act, articleId, showId]);

  const resumeTranslation = useCallback(() => {
    const current = stateRef.current;
    const job = current.id === articleId ? current.job : null;
    if (job?.can_resume && job.job_id) act(() => resumeTranslationJob(job.job_id));
  }, [act, articleId]);

  const restartTranslation = useCallback(() => {
    if (articleId) act(() => startTranslationJob(articleId));
  }, [act, articleId]);

  const current = state.id === articleId ? state : EMPTY_TRANSLATION;
  const loaded = Boolean(articleId) && state.id === articleId;
  const requesting = Boolean(articleId) && busyId === articleId;
  const hasTranslation = Boolean(current.result || current.preview || current.job);
  // 中文视图:读者点过「中文」,且有可显示 / 在途的内容;回到这篇、首个快照未到时先画骨架(不闪原文)
  const showTranslation = Boolean(enabled && articleId && showId === articleId)
    && (!loaded || hasTranslation || requesting);

  return {
    showTranslation,
    translating: requesting || isActiveStatus(current.status),
    hasTranslation,
    translationReady: showTranslation && typeof current.result?.translation === 'string',
    translatedBody: current.result?.translation || null,
    translatedTitle: current.result?.title || null,
    translation: {
      status: current.status,
      job: current.job,
      result: current.result,
      preview: current.preview,
      loaded,
      requesting,
    },
    handleTranslate,
    resumeTranslation,
    restartTranslation,
  };
}
