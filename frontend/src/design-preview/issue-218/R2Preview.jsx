import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import Toast from '../../components/Toast';
import AiGradientDefs from '../../components/AiGradientDefs';
import { useCompactLayout } from '../../hooks/useCompactLayout';
import { socialTranslationSignature } from '../../utils/socialTranslate';
import {
  ALL_READABLE,
  CONTAINER_ITEMS,
  DEFAULT_ARTICLE_ID,
  INITIAL_UNREAD,
  SOCIAL_POSTS,
} from './fixtures';
import { displayTitleOf, readZhTitlePref, writeZhTitlePref } from './titleDisplay';
import { prefixLength, useTranslationSim } from './translationSim';
import { useMotionAttr, usePreviewTheme } from './previewTheme';
import ReaderDesktop from './ReaderDesktop';
import ReaderMobile from './ReaderMobile';
import DemoPanel from './DemoPanel';

const initialTitleStates = () => Object.fromEntries(ALL_READABLE.map((item) => [item.id, item.title_state]));
// 进入中文视图时的进度快照:之后才出现的片做一次换入淡入(连续展示看前缀长度,逐段占位看已完成集合)
const entryOf = (job) => ({ from: prefixLength(job), done: job ? [...job.done] : [] });
const initialUnread = () => new Set(INITIAL_UNREAD.filter((id) => id !== DEFAULT_ARTICLE_ID));
const initialFav = () => new Set(['a-longctx']);

// issue #218 R2 交互样页根:持有全部模拟状态(选中、已读、收藏、标题偏好、翻译任务、社交译文),
// 桌面四栏与手机单列共用同一份;布局按 useCompactLayout(≤1024)切换,宽屏可在演示控制里看手机框。
export default function R2Preview() {
  const compact = useCompactLayout();
  const [phoneFrame, setPhoneFrame] = useState(false);
  const mobile = compact || phoneFrame;
  const theme = usePreviewTheme();
  const motion = useMotionAttr();

  // ── 阅读器状态 ──
  const [mode, setMode] = useState('article');
  const [meOpen, setMeOpen] = useState(false);
  const [sourceFilter, setSourceFilter] = useState(null);
  const [kindFilter, setKindFilter] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [favOnly, setFavOnly] = useState(false);
  const [activeId, setActiveId] = useState(DEFAULT_ARTICLE_ID);
  const [paneEpoch, setPaneEpoch] = useState(0);
  const [unread, setUnread] = useState(initialUnread);
  const [fav, setFav] = useState(initialFav);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [socialSearch, setSocialSearch] = useState({ open: false, input: '' });
  const [socialTr, setSocialTr] = useState({});

  // ── 标题:读者偏好(默认开,独立键持久化)+ 每篇译名是否就绪(演示控制可改) ──
  const [zhTitle, setZhTitleState] = useState(readZhTitlePref);
  const setZhTitle = useCallback((on) => {
    setZhTitleState(on);
    writeZhTitlePref(on);
  }, []);
  const [titleStates, setTitleStates] = useState(initialTitleStates);
  const displayFor = useCallback(
    (item) => displayTitleOf(item, { zhTitle, titleState: titleStates[item.id] }),
    [zhTitle, titleStates],
  );

  // ── 正文翻译(模拟服务端任务)+ 当前显示中文的那一篇(与现网 showId 同一语义:一次只记一篇) ──
  const [pacing, setPacing] = useState('auto');
  const [order, setOrder] = useState('shuffled');
  const [queueFirst, setQueueFirst] = useState(false);
  const { jobs, actions } = useTranslationSim({ pacing, order, queueFirst });
  const [zhView, setZhView] = useState(null); // { id, from, done }
  // 中文视图的显示方式(只在演示控制切换,不是产品设置):slots 逐段占位(默认)| prefix 连续展示;切换不重置任务
  const [displayMode, setDisplayMode] = useState('slots');

  // 没有任务时「译为中文」= 开始翻译并立刻进入中文视图(此时全是骨架,完成一片换一片);
  // 已有任务(进行中 / 暂停 / 失败 / 完整缓存)时只切视图,不重复开任务
  const showChinese = useCallback((item) => {
    const job = jobs[item.id];
    if (!job) actions.start(item.id, item.body.segments.length);
    setZhView({ id: item.id, ...entryOf(job) });
  }, [actions, jobs]);
  const showOriginal = useCallback(() => setZhView(null), []);

  // ── 提示 ──
  const [toast, setToast] = useState({ show: false, message: '', type: 'info' });
  const toastTimer = useRef(null);
  const showToast = useCallback((message, type = 'info') => {
    clearTimeout(toastTimer.current);
    setToast({ show: true, message, type });
    toastTimer.current = setTimeout(() => setToast((current) => ({ ...current, show: false })), 2600);
  }, []);
  const hideToast = useCallback(() => setToast((current) => ({ ...current, show: false })), []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);

  // 只在键盘操作后程序化移焦点(conventions §2):记录最近一次输入方式
  const keyboardRef = useRef(false);
  useEffect(() => {
    const onKey = () => { keyboardRef.current = true; };
    const onPointer = () => { keyboardRef.current = false; };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onPointer, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onPointer, true);
    };
  }, []);

  // ── 列表与动作 ──
  const select = useCallback((id) => {
    setActiveId(id);
    if (!id) return;
    setUnread((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    // 回到正在看中文的那篇:从此刻起才出现的片做换入淡入
    setZhView((view) => (view && view.id === id ? { id, ...entryOf(jobs[id]) } : view));
  }, [jobs]);

  // 与现网视图轨同语义:点容器 = 回到该容器的聚合(清掉源 / 类型筛选);换容器时清空选中
  const goView = useCallback((next) => {
    setMeOpen(false);
    setSourceFilter(null);
    setKindFilter('');
    setSocialSearch({ open: false, input: '' });
    if (next !== mode) setActiveId(null);
    setMode(next);
  }, [mode]);

  const toggleFav = useCallback((id) => {
    setFav((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    showToast(fav.has(id) ? '已取消收藏' : '已收藏', 'success');
  }, [fav, showToast]);

  const toggleRead = useCallback((id) => {
    setUnread((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const socialQuery = socialSearch.input.trim().toLowerCase();
  const visibleItems = useMemo(() => CONTAINER_ITEMS[mode].filter((item) => {
    if (sourceFilter && item.source_id !== sourceFilter) return false;
    if (mode === 'article' && kindFilter && item.kind !== kindFilter) return false;
    if (favOnly && !fav.has(item.id)) return false;
    if (unreadOnly && !unread.has(item.id) && item.id !== activeId) return false;
    if (mode === 'social' && socialSearch.open && socialQuery && !item.content.toLowerCase().includes(socialQuery)) return false;
    return true;
  }), [mode, sourceFilter, kindFilter, favOnly, fav, unreadOnly, unread, activeId, socialSearch.open, socialQuery]);

  const markAllRead = useCallback(() => {
    const ids = CONTAINER_ITEMS[mode]
      .filter((item) => !sourceFilter || item.source_id === sourceFilter)
      .map((item) => item.id)
      .filter((id) => unread.has(id));
    if (ids.length === 0) {
      showToast('没有未读内容，都看完啦');
      return;
    }
    setUnread((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.delete(id));
      return next;
    });
    showToast(`已将 ${ids.length} 条标为已读`, 'success');
  }, [mode, sourceFilter, unread, showToast]);

  // 社交卡译文:沿用现网逐卡手动翻译(本地模拟 0.9 秒返回),不自动展开、不加标题区
  const socialTrRef = useRef(socialTr);
  useEffect(() => { socialTrRef.current = socialTr; }, [socialTr]);
  const toggleSocialTranslation = useCallback((post) => {
    const sig = socialTranslationSignature(post);
    if (!sig) return;
    const entry = socialTrRef.current[post.id];
    if (entry?.sig === sig && entry.status === 'loading') return;
    if (entry?.sig === sig && entry.status === 'done') {
      setSocialTr((prev) => ({ ...prev, [post.id]: { ...prev[post.id], open: !prev[post.id].open } }));
      return;
    }
    setSocialTr((prev) => ({ ...prev, [post.id]: { status: 'loading', open: false, sig } }));
    setTimeout(() => {
      const source = SOCIAL_POSTS.find((item) => item.id === post.id)?.r2_translation || {};
      setSocialTr((prev) => (prev[post.id]?.status !== 'loading' ? prev : {
        ...prev,
        [post.id]: { status: 'done', open: true, sig, text: source.text || null, quoted: source.quoted || null },
      }));
    }, 900);
  }, []);

  // ── 演示控制专用 ──
  const [demoOpen, setDemoOpen] = useState(false);
  const demoToggleRef = useRef(null);
  const setTitleState = useCallback((id, state) => {
    setTitleStates((prev) => ({ ...prev, [id]: state }));
  }, []);
  const resetJob = useCallback((id) => {
    actions.reset(id);
    setZhView((view) => (view?.id === id ? null : view));
  }, [actions]);
  const reopenPage = useCallback(() => {
    // 刷新 / 重新打开:后台进度保留,正文仍按默认原文显示,不自动进入中文
    setZhView(null);
    setPaneEpoch((value) => value + 1);
    showToast('已模拟重新打开：进度保留，正文回到原文');
  }, [showToast]);
  const resetDemo = useCallback(() => {
    actions.resetAll();
    setTitleStates(initialTitleStates());
    setUnread(initialUnread());
    setFav(initialFav());
    setZhView(null);
    setSocialTr({});
    setSocialSearch({ open: false, input: '' });
    setMode('article');
    setMeOpen(false);
    setSourceFilter(null);
    setKindFilter('');
    setUnreadOnly(false);
    setFavOnly(false);
    setActiveId(DEFAULT_ARTICLE_ID);
    setPaneEpoch((value) => value + 1);
    showToast('已重置演示数据');
  }, [actions, showToast]);

  const model = {
    mobile,
    compact,
    mode,
    goView,
    meOpen,
    setMeOpen,
    sourceFilter,
    setSourceFilter,
    kindFilter,
    setKindFilter,
    unreadOnly,
    setUnreadOnly,
    favOnly,
    setFavOnly,
    visibleItems,
    activeId,
    select,
    paneEpoch,
    unread,
    fav,
    toggleFav,
    toggleRead,
    markAllRead,
    zhTitle,
    setZhTitle,
    displayFor,
    titleStates,
    jobs,
    actions,
    zhView,
    displayMode,
    showChinese,
    showOriginal,
    settingsOpen,
    setSettingsOpen,
    socialSearch,
    setSocialSearch,
    socialTr,
    toggleSocialTranslation,
    theme,
    keyboardRef,
    showToast,
  };

  const demo = {
    displayMode,
    setDisplayMode,
    pacing,
    setPacing,
    order,
    setOrder,
    queueFirst,
    setQueueFirst,
    phoneFrame,
    setPhoneFrame,
    motion,
    setTitleState,
    resetJob,
    reopenPage,
    resetDemo,
  };

  return (
    <div className="r2-root">
      <AiGradientDefs />
      {/* 样页外壳(反色常驻条):与产品界面分开,声明数据与进度均为模拟 */}
      <div className="r2-strip">
        <span className="r2-strip-text">R2 交互样页 · 数据与翻译进度均为模拟，未接入后台</span>
        <button
          ref={demoToggleRef}
          type="button"
          className="r2-strip-btn"
          aria-expanded={demoOpen}
          aria-controls="r2-demo"
          onClick={() => setDemoOpen((value) => !value)}
        >
          <SlidersHorizontal aria-hidden="true" />
          演示控制
        </button>
        {demoOpen && (
          <DemoPanel
            model={model}
            demo={demo}
            onClose={() => setDemoOpen(false)}
            toggleRef={demoToggleRef}
          />
        )}
      </div>
      <div className={`r2-stage ${mobile ? 'is-mobile' : ''} ${phoneFrame && !compact ? 'is-framed' : ''}`}>
        {mobile ? (
          <div className="r2-mobile-host">
            <div className="m-root font-sans">
              <ReaderMobile m={model} />
              <Toast show={toast.show} message={toast.message} type={toast.type} onClose={hideToast} />
            </div>
          </div>
        ) : (
          <>
            <ReaderDesktop m={model} />
            <Toast show={toast.show} message={toast.message} type={toast.type} onClose={hideToast} />
          </>
        )}
      </div>
    </div>
  );
}
