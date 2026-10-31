import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useReaderState } from '../hooks/useReaderState';
import { compactLayoutMatches } from '../hooks/useCompactLayout';
import ReaderTab from './ReaderTab';
import MobileReader from './mobile/MobileReader';
import PlayerProvider from './player/PlayerProvider';
import { prefetchReaderMarkdownWhenIdle } from '../utils/readerMarkdownLoader';

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
  // 早报正在看的那一期与读到的位置(审计 M-36,issue #230 批 4a):跨 1024 换版式时早报页随视图卸载重挂,
  // 旧的那份卸载前把 {date, revision, progress} 交到这里,新的那份挂载时取走接着看(iPad 旋转不再跳回今天、回到顶)
  const briefPlaceRef = useRef(null);
  const briefPlace = useMemo(() => ({
    keep: (place) => { briefPlaceRef.current = place; },
    take: () => {
      const place = briefPlaceRef.current;
      briefPlaceRef.current = null;
      return place;
    },
  }), []);
  const closePage = useCallback(() => setPage(null), []);
  const setBriefOpen = useCallback((open) => setPage(open ? 'brief' : null), []);
  const setRankingsOpen = useCallback((open) => setPage(open ? 'rankings' : null), []);
  useEffect(() => {
    if (!personalDigestEnabled) setPage((current) => current === 'brief' ? null : current);
  }, [personalDigestEnabled]);
  // 正文 Markdown 渲染器不进首屏(issue #230 M-14),首屏落定后空闲预取,第一次打开一篇时多半已就绪
  useEffect(() => prefetchReaderMarkdownWhenIdle(), []);

  const rs = useReaderState({
    showToast,
    account,
    initialArticleId,
    onDeepLinkConsumed,
    onBeforeOpenArticle: closePage,
    interestAxisEnabled: personalDigestEnabled,
    aiEnabled: props.aiEnabled,
    // 手机壳的内容 Tab 只放订阅聚合;来源 / 收藏 / 兴趣在订阅 Tab 的推入页(issue #230 批 3a)
    aggregateOnly: mobile,
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
      // 手机壳的四个 Tab(issue #230):早报 / 订阅 / 榜单是页,内容 = 当前容器(rs.mode);其它值一律回内容
      return value === 'brief' || value === 'rankings' || value === 'subs' ? value : null;
    });
  }, [rs.mode]);
  // 工作区根:滚动位置恢复的查询范围,也是播放条写「让位 / 浮钮避让」变量的宿主
  const rootRef = useRef(null);
  const view = {
    briefOpen: page === 'brief', setBriefOpen,
    rankingsOpen: page === 'rankings', setRankingsOpen,
    briefReturn, setBriefReturn, briefRestore, setBriefRestore, briefPlace,
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
    // 手机订阅 Tab 的推入页是另一个列表实例(issue #230 批 3a):它的滚动不是主列表的位置
    if (el.closest?.('[data-own-list]')) return;
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

  // 换版式时的主列表交接(codex 检视第 1 轮 P1-2 / P2-3),在绘制前做,不闪一帧错的版式:
  //  进桌面——手机推入页里读的那篇形态可能 ≠ 主列表容器,桌面阅读窗按容器画版式(播客 / 文章,社交容器不开阅读窗),
  //    主列表切到它的容器并保留右栏;
  //  进手机——内容 Tab 只放订阅聚合、没有来源筛选提示:没开正文时桌面留下的来源 / 兴趣下钻当场撤掉
  //    (开着正文的由 MobileReader.closeArticle 离开正文时撤,同一条规则)。只改会话状态,不写持久存储。
  const handoffLayoutRef = useRef(mobile);
  useLayoutEffect(() => {
    if (handoffLayoutRef.current === mobile) return;
    handoffLayoutRef.current = mobile;
    if (!mobile) {
      if (rs.activeArticle) rs.alignModeToArticle(rs.activeArticle, { keepArticle: true });
    } else if (!rs.activeArticle && (rs.activeSourceId || rs.activeTagId)) {
      rs.dropNarrowing();
    }
  }, [mobile, rs]);

  const View = mobile ? MobileReader : ReaderTab;
  return (
    <PlayerProvider username={account?.username || ''} active={surfaceActive}>
      <div ref={rootRef} className={`reader-ws ${mobile ? 'is-mobile' : 'is-desktop'}`} onScrollCapture={captureScroll}>
        <View {...props} rs={rs} view={view} />
      </div>
    </PlayerProvider>
  );
}
