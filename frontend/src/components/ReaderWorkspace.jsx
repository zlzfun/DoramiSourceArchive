import { useCallback, useEffect, useRef, useState } from 'react';
import { useReaderState } from '../hooks/useReaderState';
import { compactLayoutMatches } from '../hooks/useCompactLayout';
import ReaderTab from './ReaderTab';
import MobileReader from './mobile/MobileReader';
import PlayerProvider from './player/PlayerProvider';

// 数据、当前位置与早报返回路径只保留一份。宽度变化只替换视图，不重新加载阅读器。
// 全局播放器(issue #199)也挂在这一层:桌面 ⇄ 手机互切、换文章 / 容器 / 页面都不卸载,播放不中断。
// 播放条本身由两个视图各自挂在自己的外壳里(.reader-shell / .m-shell):手机外壳是 fixed 的层叠上下文,
// 条要排在正文页(40)之上、抽屉 / 动作单(50 / 60)之下,只能与它们同在一个上下文里。
export default function ReaderWorkspace({ mobile, surfaceActive = true, ...props }) {
  const { personalDigestEnabled, initialArticleId, showToast, account, onDeepLinkConsumed } = props;
  const [page, setPage] = useState(() => personalDigestEnabled && !initialArticleId ? 'brief' : null);
  const [briefReturn, setBriefReturn] = useState(null);
  const [briefRestore, setBriefRestore] = useState(null);
  const [discoverTab, setDiscoverTab] = useState('sources');
  const [interestVersion, setInterestVersion] = useState(0);
  const closePage = useCallback(() => setPage(null), []);
  const setBriefOpen = useCallback((open) => setPage(open ? 'brief' : null), []);
  const setRankingsOpen = useCallback((open) => setPage(open ? 'rankings' : null), []);
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
    aiEnabled: props.aiEnabled,
  });
  const tab = page || rs.mode;
  // 点播放条的标题 / 封面 = 打开正在播的那一期(切到所在容器,关掉早报 / 榜单 / 发现;手机即入栈正文页)
  const { openArticleById } = rs;
  const openPlayingItem = useCallback((item) => {
    if (!item?.articleId) return;
    setBriefReturn(null);
    openArticleById(item.articleId);
  }, [openArticleById]);
  const setTab = useCallback((next) => {
    setPage((current) => {
      const value = typeof next === 'function' ? next(current || rs.mode) : next;
      return value === 'brief' || value === 'rankings' || value === 'me' ? value : null;
    });
  }, [rs.mode]);
  // 工作区根:滚动位置恢复的查询范围,也是播放条写「让位 / 浮钮避让」变量的宿主
  const rootRef = useRef(null);
  const view = {
    briefOpen: page === 'brief', setBriefOpen,
    rankingsOpen: page === 'rankings', setRankingsOpen,
    briefReturn, setBriefReturn, briefRestore, setBriefRestore,
    discoverTab, setDiscoverTab, interestVersion, setInterestVersion,
    tab, setTab, playerHostRef: rootRef, openPlayingItem,
  };
  // 只记录两个可见阅读区域的滚动位置，不复制列表/正文，也不写 storage。
  // 跨版式正文高度会变化，用阅读进度恢复；列表保留偏移，普通切篇/筛选仍由原逻辑归零。
  const scrollRef = useRef({});
  const previousLayout = useRef(mobile);
  const articleKey = rs.activeArticle?.id;
  const listKey = JSON.stringify([rs.mode, rs.activeSourceId, rs.activeTagId, rs.scope, rs.searchQuery, rs.unreadOnly, rs.kindFilter]);
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
    <PlayerProvider username={account?.username || ''} active={surfaceActive}>
      <div ref={rootRef} className={`reader-ws ${mobile ? 'is-mobile' : 'is-desktop'}`} onScrollCapture={captureScroll}>
        <View {...props} rs={rs} view={view} />
      </div>
    </PlayerProvider>
  );
}
