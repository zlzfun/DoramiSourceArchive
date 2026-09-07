import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { fetchPodcastEpisodeTexts } from '../api';
import {
  isPodcastTextRequestCurrent,
  mergePodcastTextPage,
  podcastTextPageAction,
  podcastTextView,
} from '../utils/podcastTextReader';
import ReaderMarkdown from './ReaderMarkdown';

export default function PodcastTextPanel({ episodeId }) {
  const [state, setState] = useState({ loading: true, response: null, error: '' });
  const [reload, setReload] = useState(0);
  const [pages, setPages] = useState({});
  const [more, setMore] = useState({});
  const requestGroup = useRef({ episodeId: null, controllers: new Set() });

  useEffect(() => {
    const group = { episodeId, controllers: new Set() };
    requestGroup.current = group;
    const controller = new AbortController();
    group.controllers.add(controller);
    setState({ loading: true, response: null, error: '' });
    setPages({});
    setMore({});
    fetchPodcastEpisodeTexts(episodeId, {}, { signal: controller.signal })
      .then((response) => {
        if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) return;
        setState({ loading: false, response, error: '' });
        setPages(Object.fromEntries((response.items || []).map((item) => [item.kind, item])));
      })
      .catch((error) => {
        if (
          isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)
          && error?.name !== 'AbortError'
        ) {
          setState({ loading: false, response: null, error: '播客文字载入失败，请重试' });
        }
      })
      .finally(() => group.controllers.delete(controller));
    return () => {
      for (const request of group.controllers) request.abort();
      group.controllers.clear();
      if (requestGroup.current === group) {
        requestGroup.current = { episodeId: null, controllers: new Set() };
      }
    };
  }, [episodeId, reload]);

  const view = podcastTextView({ items: Object.values(pages) });
  const refreshFirstPage = (kind, group = requestGroup.current) => {
    if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) {
      return Promise.resolve();
    }
    const controller = new AbortController();
    group.controllers.add(controller);
    setMore((current) => ({ ...current, [kind]: { loading: true, error: '' } }));
    return fetchPodcastEpisodeTexts(episodeId, { kind }, { signal: controller.signal })
      .then((response) => {
        if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) return;
        const fresh = response.items?.[0];
        setPages((current) => {
          if (fresh) return { ...current, [kind]: fresh };
          const next = { ...current };
          delete next[kind];
          return next;
        });
        setMore((current) => ({
          ...current,
          [kind]: { loading: false, error: '', notice: '文字已刷新到最新版本' },
        }));
      })
      .catch((error) => {
        if (
          isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)
          && error?.name !== 'AbortError'
        ) {
          setMore((current) => ({
            ...current,
            [kind]: { loading: false, error: '刷新失败，请重试' },
          }));
        }
      })
      .finally(() => group.controllers.delete(controller));
  };
  const loadMore = (item) => {
    if (!item?.next_cursor || more[item.kind]?.loading) return;
    const group = requestGroup.current;
    if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) return;
    const requestedCursor = item.next_cursor;
    const controller = new AbortController();
    group.controllers.add(controller);
    setMore((current) => ({ ...current, [item.kind]: { loading: true, error: '' } }));
    fetchPodcastEpisodeTexts(episodeId, {
      kind: item.kind,
      cursor: requestedCursor,
    }, { signal: controller.signal })
      .then((response) => {
        if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) return;
        const next = response.items?.[0];
        const action = podcastTextPageAction(item, next);
        if (action === 'refresh') return refreshFirstPage(item.kind, group);
        setPages((current) => {
          const existing = current[item.kind];
          // Ignore a late or duplicate response after the visible cursor moved.
          if (!existing || existing.next_cursor !== requestedCursor) return current;
          return { ...current, [item.kind]: mergePodcastTextPage(existing, next) };
        });
        setMore((current) => ({ ...current, [item.kind]: { loading: false, error: '' } }));
        return undefined;
      })
      .catch((error) => {
        if (!isPodcastTextRequestCurrent(group, requestGroup.current, episodeId)) return;
        const action = podcastTextPageAction(item, null, error);
        if (action === 'refresh') {
          return refreshFirstPage(item.kind, group);
        }
        if (action === 'error' && error?.name !== 'AbortError') {
          setMore((current) => ({
            ...current,
            [item.kind]: { loading: false, error: '载入失败，请重试' },
          }));
        }
        return undefined;
      })
      .finally(() => group.controllers.delete(controller));
  };
  if (state.loading) {
    return (
      <div className="podcast-text-loading" role="status">
        <Loader2 className="h-4 w-4 animate-spin" /> 正在读取节目文字…
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="podcast-text-initial-error" role="alert">
        <span>{state.error}</span>
        <button type="button" onClick={() => setReload((value) => value + 1)}>重新载入</button>
      </div>
    );
  }
  // 精品导读由上方 Tab 控制显示；没有中文博客时不再渲染额外占位壳。
  if (!view.digest) return null;

  return (
    <section className="podcast-text-panel" aria-label="精品导读中文博客">
      <div className="podcast-text-digest">
        <div className="podcast-text-heading">
          <h2>精品导读</h2>
          <span>中文博客 · AI 整理</span>
        </div>
        <div className="podcast-guide-body">
          <ReaderMarkdown>{view.digest.text}</ReaderMarkdown>
          {view.digest.next_cursor && (
            <button
              type="button"
              className="podcast-text-more"
              disabled={more[view.digest.kind]?.loading}
              onClick={() => loadMore(view.digest)}
            >
              {more[view.digest.kind]?.loading ? '正在载入…' : '继续阅读精品导读'}
            </button>
          )}
          {more[view.digest.kind]?.error && <p className="podcast-text-more-error" role="alert">{more[view.digest.kind].error}</p>}
          {more[view.digest.kind]?.notice && <p className="podcast-text-refresh-notice" role="status">{more[view.digest.kind].notice}</p>}
        </div>
      </div>
    </section>
  );
}
