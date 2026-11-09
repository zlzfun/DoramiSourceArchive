import { useEffect, useRef, useState } from 'react';
import { Headphones, Loader2, RefreshCw, Sparkles } from 'lucide-react';
import { fetchArticle, requestPodcastOndemand } from '../api';
import { briefGuideAction, briefGuideActive, isBriefPodcast } from '../utils/briefPodcastGuide';
import { requestLogin } from '../utils/loginPrompt';
import { isPlayingIntent, playerItemKey, podcastTrackItem } from '../utils/playerCore';
import { usePlayer } from './player/playerContext';

function GuideButton({ articleId, sourceName, guest, showToast }) {
  const { state, api } = usePlayer();
  const [article, setArticle] = useState(null);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const requestInFlight = useRef(false);
  const version = useRef(0);
  const refresh = useRef(null);
  const alive = useRef(false);

  useEffect(() => {
    alive.current = true;
    let stopped = false;
    let controller = null;
    let pending = null;
    let nextRead = 0;
    let retryAutomatically = true;
    const load = () => {
      if (pending) return pending;
      const readVersion = version.current;
      controller = new AbortController();
      pending = fetchArticle(articleId, { signal: controller.signal }).then((fresh) => {
        if (stopped || readVersion !== version.current) return;
        setArticle(fresh);
        setError('');
        setAccepted(false);
        retryAutomatically = true;
        nextRead = Date.now() + (briefGuideActive(fresh) ? 4000 : 30000);
      }).catch((err) => {
        if (stopped || readVersion !== version.current) return;
        setError(err?.message || '导读状态加载失败，请重试');
        // Frozen editions can outlive an article's visibility. Retry 404 only on demand.
        retryAutomatically = err?.status !== 404;
        nextRead = Date.now() + 8000;
      }).finally(() => { pending = null; });
      return pending;
    };
    // Wait for an older read to settle before loading the state committed by POST.
    const reload = async () => {
      if (pending) await pending;
      if (!stopped) await load();
    };
    refresh.current = reload;
    void load();
    const poll = () => {
      if (retryAutomatically && !document.hidden && !requestInFlight.current && Date.now() >= nextRead) void load();
    };
    const focus = () => { nextRead = 0; poll(); };
    const timer = window.setInterval(poll, 4000);
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', focus);
    return () => {
      stopped = true;
      alive.current = false;
      controller?.abort();
      refresh.current = null;
      window.clearInterval(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', focus);
    };
  }, [articleId]);

  const action = briefGuideAction(article, { submitting, accepted, error });
  const current = state.item?.key === playerItemKey(articleId, 'digest')
    && state.form !== 'none' && state.status !== 'error';
  const playing = current && isPlayingIntent(state);
  const busy = action.kind === 'busy';
  const Icon = busy ? Loader2 : action.kind === 'play' ? Headphones : action.kind === 'refresh' ? RefreshCw : Sparkles;

  const activate = async (event) => {
    event.stopPropagation();
    if (busy || requestInFlight.current || action.kind === 'unavailable') return;
    if (action.kind === 'refresh') { await refresh.current?.(); return; }
    if (action.kind === 'play') {
      const track = podcastTrackItem(article, 'digest', sourceName);
      if (track) api.play(track, { originEl: event.currentTarget });
      return;
    }
    if (guest) { requestLogin('podcastGuide'); return; }
    requestInFlight.current = true;
    version.current += 1; // Do not let an earlier GET overwrite the accepted state.
    setSubmitting(true);
    try {
      const result = await requestPodcastOndemand(articleId);
      if (!alive.current) return;
      setAccepted(result?.outcome !== 'ready');
      showToast?.(result?.outcome === 'ready' ? '导读音频已就绪' : '导读音频正在生成中…', 'success');
      await refresh.current?.();
    } catch (err) {
      if (alive.current && !err?.loginPrompted) showToast?.(err?.message || '导读生成未完成，请稍后重试', 'error');
    } finally {
      requestInFlight.current = false;
      if (alive.current) setSubmitting(false);
    }
  };

  return (
    <button
      type="button"
      className={`bp-guide${current ? ' is-current' : ''}`}
      data-guide-article={articleId}
      data-listen-key={action.kind === 'play' ? playerItemKey(articleId, 'digest') : undefined}
      aria-label={action.kind === 'play' ? `${action.label}，AI 生成` : action.label}
      aria-pressed={action.kind === 'play' ? playing : undefined}
      aria-disabled={busy || action.kind === 'unavailable' ? true : undefined}
      title={error || (article?.podcast?.premium_guide?.status === 'failed' ? '上次导读生成未完成，可重新请求' : undefined)}
      onClick={activate}
      onTouchStart={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.stopPropagation()}
    >
      <Icon className={busy ? 'animate-spin' : undefined} aria-hidden="true" />
      <span aria-live="polite">{action.label}</span>
    </button>
  );
}

export default function BriefPodcastGuide({ item, source, guest = false, showToast }) {
  if (!isBriefPodcast(item, source)) return null;
  return <GuideButton key={item.article_id} articleId={item.article_id}
    sourceName={item.snapshot?.source_name || source?.name || ''} guest={guest} showToast={showToast} />;
}
