import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTranslationJob, startTranslationJob, resumeTranslationJob, cancelTranslationJob } from '../api';

const EMPTY = { status: 'idle', job: null };
const running = (status) => status === 'queued' || status === 'running';

// The server owns progress. Closing this hook only stops polling, never the job.
export function useArticleTranslation(articleId, enabled, showToast) {
  const [snapshot, setSnapshot] = useState({ id: null, data: EMPTY });
  const [showId, setShowId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [pollError, setPollError] = useState(null);
  const generation = useRef(0);
  const sequence = useRef(0);
  const actionPending = useRef(false);
  const current = snapshot.id === articleId ? snapshot.data : EMPTY;
  const busy = !!articleId && (busyId === articleId || running(current.status));

  useEffect(() => {
    const epoch = ++generation.current;
    let timer;
    const poll = async () => {
      if (actionPending.current) { timer = setTimeout(poll, 2000); return; }
      const seq = ++sequence.current;
      try {
        const data = await fetchTranslationJob(articleId);
        if (generation.current !== epoch) return;
        if (seq !== sequence.current) { timer = setTimeout(poll, 2000); return; }
        setSnapshot({ id: articleId, data });
        setPollError(null);
        if (running(data.status)) timer = setTimeout(poll, 2000);
      } catch {
        if (generation.current !== epoch) return;
        if (seq !== sequence.current) { timer = setTimeout(poll, 2000); return; }
        setPollError(articleId);
        timer = setTimeout(poll, 10000);
      }
    };
    if (enabled && articleId) poll();
    return () => { generation.current = epoch + 1; clearTimeout(timer); };
  }, [articleId, enabled, current.status]);

  const act = useCallback(async (operation) => {
    if (!articleId || busyId === articleId) return;
    const epoch = generation.current;
    const seq = ++sequence.current;
    actionPending.current = true;
    setBusyId(articleId);
    try {
      const data = await operation();
      if (epoch !== generation.current || seq !== sequence.current) return;
      setSnapshot({ id: articleId, data });
      setPollError(null);
      setShowId(articleId);
    } catch (error) {
      if (epoch === generation.current) showToast(error.message || '翻译操作失败，请稍后重试', 'error');
    } finally {
      actionPending.current = false;
      setBusyId((id) => id === articleId ? null : id);
    }
  }, [articleId, busyId, showToast]);

  const handleTranslate = useCallback(() => {
    if (showId === articleId && current.result) { setShowId(null); return; }
    if (current.result) { setShowId(articleId); return; }
    if (current.job?.can_resume) act(() => resumeTranslationJob(current.job.job_id));
    else act(() => startTranslationJob(articleId));
  }, [act, articleId, current, showId]);
  const cancelTranslation = useCallback(() => {
    if (current.job?.can_cancel) act(() => cancelTranslationJob(current.job.job_id));
  }, [act, current.job]);

  return {
    showTranslation: showId === articleId && !!current.result,
    translating: busy,
    translatedBody: current.result?.translation || null,
    translatedTitle: current.result?.title || null,
    translationJob: current.job,
    translationPollError: pollError === articleId,
    handleTranslate,
    cancelTranslation,
  };
}
