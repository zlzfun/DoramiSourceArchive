import { useCallback, useEffect, useRef, useState } from 'react';
import { useReaderState } from '../hooks/useReaderState';
import { compactLayoutMatches } from '../hooks/useCompactLayout';
import ReaderTab from './ReaderTab';
import MobileReader from './mobile/MobileReader';

// 数据、当前位置与早报返回路径只保留一份。宽度变化只替换视图，不重新加载阅读器。
export default function ReaderWorkspace({ mobile, ...props }) {
  const { personalDigestEnabled, initialArticleId, showToast, account, onDeepLinkConsumed } = props;
  const [page, setPage] = useState(() => personalDigestEnabled && !initialArticleId ? 'brief' : null);
  const [briefReturn, setBriefReturn] = useState(null);
  const [briefRestore, setBriefRestore] = useState(null);
  const [discoverTab, setDiscoverTab] = useState('sources');
  const [interestVersion, setInterestVersion] = useState(0);
  const closePage = useCallback(() => setPage(null), []);
  const setBriefOpen = useCallback((open) => setPage(open ? 'brief' : null), []);
  useEffect(() => {
    if (!personalDigestEnabled) setPage((current) => current === 'brief' ? null : current);
  }, [personalDigestEnabled]);

  const rs = useReaderState({
    showToast,
    account,
    initialArticleId,
    onDeepLinkConsumed,
    onBeforeOpenArticle: closePage,
    interestAxisEnabled: personalDigestEnabled,
  });
  const tab = page || rs.mode;
  const setTab = useCallback((next) => {
    setPage((current) => {
      const value = typeof next === 'function' ? next(current || rs.mode) : next;
      return value === 'brief' || value === 'me' ? value : null;
    });
  }, [rs.mode]);
  const view = {
    briefOpen: page === 'brief', setBriefOpen,
    briefReturn, setBriefReturn, briefRestore, setBriefRestore,
    discoverTab, setDiscoverTab, interestVersion, setInterestVersion,
    tab, setTab,
  };

  // 只记录两个可见阅读区域的滚动位置，不复制列表/正文，也不写 storage。
  // 跨版式正文高度会变化，用阅读进度恢复；列表保留偏移，普通切篇/筛选仍由原逻辑归零。
  const rootRef = useRef(null);
  const scrollRef = useRef({});
  const previousLayout = useRef(mobile);
  const articleKey = rs.activeArticle?.id;
  const listKey = JSON.stringify([rs.mode, rs.activeSourceId, rs.activeTagId, rs.scope, rs.searchQuery, rs.unreadOnly]);
  const captureScroll = (event) => {
    const el = event.target;
    const kind = el.matches('.reader-pane, .m-read-scroll') ? 'article'
      : el.matches('.reader-list-scroll, .m-list, .reader-social-scroll') ? 'list' : null;
    if (!kind) return;
    // 浏览器可能先按新宽度重排旧视图、派发 scroll，再通知媒体查询切换。
    // 这不是用户阅读进度；不能覆盖仍待恢复的旧版式快照。
    if (mobile !== compactLayoutMatches()) return;
    scrollRef.current[kind] = {
      key: kind === 'article' ? articleKey : listKey,
      top: el.scrollTop,
      progress: el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight),
    };
  };
  useEffect(() => {
    if (previousLayout.current === mobile) return;
    previousLayout.current = mobile;
    for (const [kind, selector, key] of [
      ['article', '.reader-pane, .m-read-scroll', articleKey],
      ['list', '.reader-list-scroll, .m-list, .reader-social-scroll', listKey],
    ]) {
      const saved = scrollRef.current[kind];
      const el = rootRef.current?.querySelector(selector);
      if (el && saved?.key === key) {
        el.scrollTop = kind === 'article' ? saved.progress * (el.scrollHeight - el.clientHeight) : saved.top;
      }
    }
  }, [mobile, articleKey, listKey]);

  const View = mobile ? MobileReader : ReaderTab;
  return (
    <div ref={rootRef} onScrollCapture={captureScroll}>
      <View {...props} rs={rs} view={view} />
    </div>
  );
}
