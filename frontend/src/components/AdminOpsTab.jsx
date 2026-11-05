/**
 * 运维管理(issue #114 信息架构重设计):首页即目录 → 9 个满幅功能页(issue #261 加「节点同步」)。
 * - 路由:App 的子视图 `#/admin/<页>/<板块>`(空 = 首页),注册表与解析在 utils/opsNav;本组件只做外壳——
 *   页头(首页 = 「运维管理」;功能页 = 面包屑 `运维管理 › 页 ▾`,▾ 横切各页)、时间窗、刷新时机、板块落位。
 *   节点同步页的页头是两行变体(面包屑 + 对端身份 / 动作 + 定时),由 SyncPage 自绘,面包屑从这里传进去。
 * - 各页自持状态与 loader(admin/pages/*、admin/*Panel);切页即重挂 = 重取(与旧版切档语义一致)。
 * - 时间窗(days)跨页保留,只在用到它的页显示(首页 / 账户 / 读者功能 / 分析与标签)。
 * - 刷新语义(v3.43 M15):Tab 常驻不重挂,从其它 Tab 切回时 refreshTick 自增,当前页各 loader 重取、不重挂(不丢草稿)。
 * - 板块落位:视图带板块或收到 pendingFocus 时,滚到 `#ops-<页>-<板块>`、聚焦并短促 wash 高亮(§7 瞬态定位高亮白名单)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight } from 'lucide-react';
import FeedbackInboxPanel from './admin/FeedbackInboxPanel';
import AnnouncementsPanel from './admin/AnnouncementsPanel';
import AdminTaxonomyPanel from './admin/AdminTaxonomyPanel';
import PodcastZone from './admin/PodcastZone';
import UserSourcesPanel from './admin/UserSourcesPanel';
import OpsHome from './admin/OpsHome';
import AccountsPage from './admin/pages/AccountsPage';
import ReaderPage from './admin/pages/ReaderPage';
import ReadingPage from './admin/pages/ReadingPage';
import ExternalPage from './admin/pages/ExternalPage';
import SyncPage from './admin/pages/SyncPage';
import { OpsIcon } from './admin/opsIcons';
import { OPS_PAGES, formatOpsPath, normalizeOpsTarget, opsBlockId, opsPage, parseOpsPath } from '../utils/opsNav';
import { scrollBehavior } from '../motion';

const FLASH_MS = 1700;

// 面包屑:`运维管理 › 页 ▾`;▾ 弹层按重要度列各页(当前页打勾),Esc / 点外面关闭。
function OpsCrumb({ page, onGo }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <h1 className="page-title ops-crumb" ref={wrapRef}>
      <button type="button" className="ops-crumb-root" onClick={() => onGo(null)}>运维管理</button>
      <ChevronRight className="ops-crumb-sep" aria-hidden="true" />
      <button type="button" className="ops-crumb-cur" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {page.name}
        <ChevronDown aria-hidden="true" />
      </button>
      {open && (
        <div className="ops-pop ops-crumb-pop" role="menu" aria-label="运维功能页">
          {OPS_PAGES.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitem"
              className={`ops-crumb-item${p.id === page.id ? ' is-cur' : ''}`}
              onClick={() => { setOpen(false); onGo(p.id); }}
            >
              <OpsIcon name={p.icon} />
              {p.name}
              {p.id === page.id && <Check className="ops-crumb-check" aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </h1>
  );
}

export default function AdminOpsTab({
  showToast, active = true, currentUsername = '', rootAdmin = false, onRefreshRuntime = null,
  view = '', setView, pendingFocus = null, onPendingFocusApplied, onOpenCredentials, onArticlesChanged,
}) {
  const target = useMemo(() => parseOpsPath(view), [view]);
  const page = opsPage(target.page);
  // 单一时间窗(近 N 天):跨页保留;不接时间窗的页(累计口径)不显示。
  const [days, setDays] = useState(30);

  const [refreshTick, setRefreshTick] = useState(0);
  const wasActive = useRef(active);
  useEffect(() => {
    if (active && !wasActive.current) setRefreshTick((t) => t + 1);
    wasActive.current = active;
  }, [active]);

  const go = useCallback((pageId, blockId = null) => {
    setView?.(formatOpsPath({ page: pageId, block: blockId }));
  }, [setView]);

  // ── 板块落位:视图里的板块变化 / 跨页 pendingFocus(如设置柜「ASR 配额」回指)都触发一次 ──
  const [landing, setLanding] = useState(null); // { page, block, n }
  const landSeq = useRef(0);
  const landed = useRef(0); // 已落位的请求序号:同一请求不重放(codex r1 #5)
  useEffect(() => {
    // 普通导航(不带板块)作废旧落位请求,否则回到该页时会被拽到上次的板块
    if (target.block) setLanding({ page: target.page, block: target.block, n: (landSeq.current += 1) });
    else setLanding(null);
  }, [target.page, target.block]);
  useEffect(() => {
    if (!pendingFocus) return;
    const t = normalizeOpsTarget(pendingFocus);
    if (t.page) {
      const path = formatOpsPath(t);
      if (path !== view) setView?.(path);
      if (t.block) setLanding({ page: t.page, block: t.block, n: (landSeq.current += 1) });
    }
    onPendingFocusApplied?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应新到的 pendingFocus
  }, [pendingFocus]);
  useEffect(() => {
    if (!landing || landing.page !== target.page || landing.n === landed.current) return undefined;
    const id = opsBlockId(landing.page, landing.block);
    let raf = 0; let tries = 0; let timer = 0; let flashed = null;
    // 板块可能等数据到了才渲染(根专属区、面板装载):逐帧找,约 1.5s 内找不到就放弃。
    const attempt = () => {
      const el = document.getElementById(id);
      if (!el) {
        if ((tries += 1) < 90) raf = requestAnimationFrame(attempt);
        else landed.current = landing.n;
        return;
      }
      landed.current = landing.n;
      el.scrollIntoView({ block: 'start', behavior: scrollBehavior() });
      el.focus({ preventScroll: true });
      el.classList.remove('ops-flash');
      void el.offsetWidth;
      el.classList.add('ops-flash');
      flashed = el;
      timer = window.setTimeout(() => { el.classList.remove('ops-flash'); flashed = null; }, FLASH_MS);
    };
    raf = requestAnimationFrame(attempt);
    // 取消(换页 / 作废)时连同本次高亮一起清掉
    return () => { cancelAnimationFrame(raf); window.clearTimeout(timer); flashed?.classList.remove('ops-flash'); };
  }, [landing, target.page]);
  // 换页且不带板块:回到页顶。
  const lastPage = useRef(target.page);
  useEffect(() => {
    if (lastPage.current === target.page) return;
    lastPage.current = target.page;
    if (!target.block) window.scrollTo({ top: 0, behavior: 'auto' });
  }, [target.page, target.block]);

  const showWindow = !page || page.usesDays;

  return (
    <div className={`admin-page${page ? ' ops-page' : ' ops-home'}`}>
      {page?.id !== 'sync' && <div className="page-head">
        {page ? <OpsCrumb page={page} onGo={go} /> : <h1 className="page-title">运维管理</h1>}
        <div className="page-head-actions">
          {showWindow && (
            <>
              <span className="win-label">时间窗</span>
              <div className="mini-seg" role="group" aria-label="时间窗">
                {[7, 14, 30, 90].map((d) => (
                  <button key={d} type="button" onClick={() => setDays(d)} aria-pressed={days === d} className={`mini-seg-btn ${days === d ? 'is-on' : ''}`}>{d} 天</button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>}

      {!page && <OpsHome days={days} refreshTick={refreshTick} onGo={go} />}

      {page?.id === 'accounts' && (
        <AccountsPage
          showToast={showToast}
          days={days}
          refreshTick={refreshTick}
          currentUsername={currentUsername}
          rootAdmin={rootAdmin}
          onRefreshRuntime={onRefreshRuntime}
        />
      )}
      {page?.id === 'reading' && <ReadingPage showToast={showToast} refreshTick={refreshTick} />}
      {page?.id === 'reader' && <ReaderPage showToast={showToast} days={days} refreshTick={refreshTick} rootAdmin={rootAdmin} onOpenCredentials={onOpenCredentials} />}
      {page?.id === 'podcast' && <PodcastZone showToast={showToast} refreshTick={refreshTick} />}
      {page?.id === 'analysis' && <AdminTaxonomyPanel showToast={showToast} days={days} refreshTick={refreshTick} />}
      {page?.id === 'external' && <ExternalPage showToast={showToast} refreshTick={refreshTick} onOpenCredentials={onOpenCredentials} />}
      {page?.id === 'sync' && (
        <SyncPage crumb={<OpsCrumb page={page} onGo={go} />} showToast={showToast} refreshTick={refreshTick} onArticlesChanged={onArticlesChanged} />
      )}
      {page?.id === 'user-sources' && (
        <section id={opsBlockId('user-sources', 'sources')} className="ops-block" tabIndex={-1} aria-label="自定源">
          <UserSourcesPanel showToast={showToast} refreshTick={refreshTick} />
        </section>
      )}
      {page?.id === 'inbox' && (
        <div className="grid gap-4">
          {/* refreshTick prop:切回 Tab 时面板只重取数据,不重挂、不丢草稿(M15) */}
          <section id={opsBlockId('inbox', 'feedback')} className="ops-block" tabIndex={-1} aria-label="反馈收件箱">
            <FeedbackInboxPanel showToast={showToast} refreshTick={refreshTick} />
          </section>
          <section id={opsBlockId('inbox', 'notice')} className="ops-block" tabIndex={-1} aria-label="公告管理">
            <AnnouncementsPanel showToast={showToast} refreshTick={refreshTick} />
          </section>
        </div>
      )}
    </div>
  );
}
