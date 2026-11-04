/**
 * 运维 · 账户页(issue #114,自 AdminOpsTab 用户档迁出,逻辑原样):
 * 账户增长(含窗口 KPI 行)· 游客 · 活跃用户 Top(根)· 账户管理(根)· 操作审计;单用户活动抽屉 + 新建 / 重置密码弹窗。
 * 非根管理员只见聚合数字(账户分布 + 增长曲线 + 游客 + 审计),名单 / 活跃榜 / 逐用户抽屉不渲染(端点侧 403 兜底)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  UserPlus,
  KeyRound,
  Trash2,
  Power,
  Ban,
  Zap,
  ZapOff,
  X,
  Loader2,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react';
import {
  fetchAdminAccounts,
  fetchAdminAccountGrowth,
  fetchAccountActivity,
  getAiBetaGlobal,
  createAccount,
  updateAccount,
  resetAccountPassword,
  deleteAccount,
  batchUpdateAccounts,
} from '../../../api';
import { useConfirm } from '../../../hooks/useConfirm';
import { ThFilter, ThSearch, ThSort } from '../TableTh';
import { useDebouncedValue } from '../../../hooks/useDebouncedValue';
import Modal from '../../Modal';
import { useBodyScrollLock } from '../../../hooks/useBodyScrollLock';
import { useModalA11y } from '../../../hooks/useModalA11y';
import { MultiSeriesBars, RankBars, BarList } from '../../charts/DashboardCharts';
import AdminAuditPanel from '../AdminAuditPanel';
import AccountGrowth from '../AccountGrowth';
import GuestZone from '../GuestZone';
import Pager from '../Pager';
import { Kpi } from '../Kpi';
import { pivotDaily, C_READ, C_FAVORITE } from '../../charts/chartUtils';
import { PURPOSE_LABELS, formatStamp, fmtNum, truncLabel } from '../adminUtils';
import { avatarInitial, avatarHue } from '../../../utils/avatarColor';
import { opsBlockId } from '../../../utils/opsNav';

// 账户列表分页大小：超过即翻页，避免成百上千账户一次性平铺。
const ACCOUNTS_PAGE_SIZE = 15;

export default function AccountsPage({ showToast, days, refreshTick = 0, currentUsername = '', rootAdmin = false, onRefreshRuntime = null }) {
  const confirm = useConfirm();
  // 账户列表(规模化波):服务端分页 + 搜索,前端只持有当前页;summary 聚合全量供 KPI/排行。
  const [acctData, setAcctData] = useState(null); // {items,total,summary} | null = 加载中
  // 账户增长(v3.55 issue #31):聚合口径,全体管理员可见;账户名单/逐用户明细只有根管理员(rootAdmin)。
  const [growth, setGrowth] = useState(null);
  // 用户 AI 总闸(只读):批量条 / 抽屉的 AI 动作在总闸关闭时禁用;开关本身在「读者功能」页。
  const [globalAi, setGlobalAi] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('user'); // 新建账户角色(v3.19 多管理员:可直建管理员,默认读者防误触)
  const [accountQuery, setAccountQuery] = useState(''); // 输入框即时值(「用户」列头就地搜索)
  const [acctQ, setAcctQ] = useState(''); // 防抖后生效的搜索词(300ms)
  const [accountPage, setAccountPage] = useState(1);
  // 账户管理 V2(v3.41,审计 M05/M06):服务端组合过滤 × 排序 + 勾选批量。
  const [acctRole, setAcctRole] = useState('');       // '' | 'admin' | 'user'
  const [acctStatus, setAcctStatus] = useState('');   // '' | 'active' | 'disabled'
  const [acctAi, setAcctAi] = useState('');           // '' | 'on' | 'off'
  const [acctSort, setAcctSort] = useState('username');
  const [acctOrder, setAcctOrder] = useState('asc');
  const [selectedAccounts, setSelectedAccounts] = useState(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  // 活跃用户 Top 维度：阅读 | 登录。
  const [topMetric, setTopMetric] = useState('reads');

  // ── 单用户活动详情抽屉 ──
  const [detailUser, setDetailUser] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [loginListOpen, setLoginListOpen] = useState(false); // 抽屉「最近登录」展开列表
  // ── 新建账户弹窗 ──
  const [createModalOpen, setCreateModalOpen] = useState(false);

  // ── 重置密码弹窗（取代 window.prompt：不回显明文、与全站 Modal 体系一致）──
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetBusy, setResetBusy] = useState(false);

  // 两个弹窗走共用 Modal 外壳(Esc / 焦点陷阱 / 遮罩关闭判定,issue #104);
  // 详情抽屉的可访问性仍自挂 panelRef,其 useModalA11y 在 closeDetail 定义之后挂(见下方,Esc 关闭须作废在途请求)。
  const detailPanelRef = useRef(null);

  // 每组 loader 的请求代次守卫(v3.43.1 codex 交叉检视):M15 加了多个刷新时机后,
  // 同组请求可能并发在途——旧响应后到会覆盖新快照。发起时领代次,异步返回后校验
  // 自己仍是该组最新才落 state/弹 Toast(不真正 abort,只丢弃旧响应);keyed map
  // 让不同 loader 互不作废,实例级 useRef 不跨实例共享。
  const loadGen = useRef({});
  const claimGen = useCallback((key) => {
    const gen = (loadGen.current[key] || 0) + 1;
    loadGen.current[key] = gen;
    return () => loadGen.current[key] === gen;
  }, []);
  const loadGrowth = useCallback(() => {
    const fresh = claimGen('growth');
    return fetchAdminAccountGrowth().then((v) => { if (fresh()) setGrowth(v); }).catch(() => {});
  }, [claimGen]);
  // 搜索防抖:停键 300ms 后生效并归位第一页(服务端过滤)。
  const debouncedAccountQuery = useDebouncedValue(accountQuery, 300);
  useEffect(() => {
    setAcctQ(debouncedAccountQuery.trim());
    setAccountPage(1);
  }, [debouncedAccountQuery]);

  const reloadAccounts = useCallback(async () => {
    const fresh = claimGen('accounts');
    // 非根管理员没有账户名单的接触权(端点 403),不发请求、直接落空态。
    if (!rootAdmin) { setAcctData({ items: [], total: 0, summary: null }); return; }
    try {
      const data = await fetchAdminAccounts(days, {
        skip: (accountPage - 1) * ACCOUNTS_PAGE_SIZE,
        limit: ACCOUNTS_PAGE_SIZE,
        q: acctQ,
        role: acctRole,
        status: acctStatus,
        ai: acctAi,
        sort: acctSort,
        order: acctOrder,
      });
      if (fresh()) setAcctData(data);
    } catch (error) {
      if (!fresh()) return;
      showToast(error.message || '加载账户失败', 'error');
      setAcctData((prev) => prev ?? { items: [], total: 0, summary: null });
    }
  }, [claimGen, rootAdmin, days, accountPage, acctQ, acctRole, acctStatus, acctAi, acctSort, acctOrder, showToast]);

  // 账户写操作成功后的收尾(v3.55 issue #31 检视返修):先重取能力位——回落根把 admin 升回管理员
  // 会让根身份立即转移,此时旧闭包里的 reloadAccounts 仍以为自己是根、再拉名单必 403;
  // 只有最新能力位仍为根才重拉,否则交给 rootAdmin 变化的 effect 收拾界面。
  const afterAccountMutation = useCallback(async () => {
    const latest = onRefreshRuntime ? await onRefreshRuntime() : null;
    if (latest && latest.root_admin === false) return false;
    await reloadAccounts();
    return true;
  }, [onRefreshRuntime, reloadAccounts]);

  // 过滤/排序/搜索/时间窗变化:归位第一页并清空勾选(勾选集是当前结果集语境的)。
  useEffect(() => {
    setAccountPage(1);
    setSelectedAccounts(new Set());
  }, [acctRole, acctStatus, acctAi, acctSort, acctOrder, acctQ, days]);

  useEffect(() => { loadGrowth(); }, [loadGrowth, refreshTick]);
  // 账户列表随时间窗口/页码/搜索词变化重载（窗口指标按 days 聚合）;切回运维 Tab(refreshTick)同样重取。
  useEffect(() => { reloadAccounts(); }, [reloadAccounts, refreshTick]);
  useEffect(() => {
    const fresh = claimGen('globalAi');
    getAiBetaGlobal().then((g) => { if (fresh()) setGlobalAi(g.enabled); }).catch(() => {});
  }, [claimGen, refreshTick]);

  // 详情抽屉打开时锁定页面滚动(新建 / 重置密码弹窗的锁由 Modal 外壳承担);全站共用引用计数锁,
  // 抽屉上再叠确认框 / 表单弹窗、Esc 一次关两层也不会把 hidden 留在 body 上。
  useBodyScrollLock(Boolean(detailUser));
  const handleCreate = async (event) => {
    event.preventDefault();
    if (!newUsername.trim() || !newPassword) {
      showToast('请填写用户名与密码', 'error');
      return;
    }
    if (newPassword.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    setBusy(true);
    try {
      await createAccount({ username: newUsername.trim(), password: newPassword, role: newRole });
      showToast(`已创建${newRole === 'admin' ? '管理员' : '读者'}账户 ${newUsername.trim()}`, 'success');
      setNewUsername('');
      setNewPassword('');
      setNewRole('user');
      setCreateModalOpen(false);
      await afterAccountMutation();
    } catch (error) {
      showToast(error.message || '创建账户失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  // 单账户操作若发生在详情抽屉打开的这个人身上,顺手刷新抽屉快照(M19 就地管理闭环)。
  // 与 openDetail 共用 'detail' 代次(v3.43.2 codex 检视·高):快速点开 A→B 时,
  // A 的迟到响应曾会挂到 B 的标题下,抽屉动作按 detailData.account 实际操作 A——
  // 落状态前校验代次,关抽屉/换人即作废在途请求。
  // detailUserRef(终审返修):本函数在单账户操作 handler 的 await 之后调用,读
  // state 是发起时那次 render 的旧闭包——操作 A 在途时关 A 开 B,旧闭包仍判
  // 「抽屉开着 A」,会 bump 代次作废 B 的在途请求并把 A 数据挂进 B 的抽屉;
  // 改经 ref 读最新抽屉归属(openDetail/closeDetail 同步维护)。
  const detailUserRef = useRef(null);
  const refreshDetailIfOpen = async (username) => {
    if (detailUserRef.current !== username) return;
    const fresh = claimGen('detail');
    try {
      const data = await fetchAccountActivity(username, days);
      if (fresh() && detailUserRef.current === username) setDetailData(data);
    } catch { /* 详情刷新失败不打断主操作 */ }
  };

  // 关抽屉统一走此函数:bump 'detail' 代次,作废在途详情请求。
  const closeDetail = () => {
    claimGen('detail');
    detailUserRef.current = null;
    setDetailUser(null);
  };
  useModalA11y(Boolean(detailUser), closeDetail, detailPanelRef);

  // 角色变更(v3.19 多管理员):提升/降级都需确认;降级自己额外预警——生效后当前
  // 会话在下一次请求即被吊销(read_auth_token 回查),表现为被登出,不预警会像 bug。
  const handleToggleRole = async (acc) => {
    const toAdmin = acc.role !== 'admin';
    const selfNote = !toAdmin && acc.username === currentUsername
      ? '这是你当前登录的账号,取消后将立即被登出管理台。'
      : '';
    const message = toAdmin
      ? `确认将「${acc.username}」设为管理员?管理员拥有采集、归档、账户与系统配置的全部权限。`
      : `确认取消「${acc.username}」的管理员身份?该账户将变为读者,仅保留阅读与订阅能力。${selfNote}`;
    if (!(await confirm(message))) return;
    try {
      await updateAccount(acc.username, { role: toAdmin ? 'admin' : 'user' });
      showToast(toAdmin ? `已将 ${acc.username} 设为管理员` : `已取消 ${acc.username} 的管理员身份`, 'success');
      if (await afterAccountMutation()) await refreshDetailIfOpen(acc.username);
    } catch (error) {
      // 末位管理员保护等后端裁决文案直接透传
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleActive = async (acc) => {
    try {
      await updateAccount(acc.username, { is_active: !acc.is_active });
      showToast(acc.is_active ? `已停用 ${acc.username}` : `已启用 ${acc.username}`, 'success');
      if (await afterAccountMutation()) await refreshDetailIfOpen(acc.username);
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleAiBeta = async (acc) => {
    try {
      await updateAccount(acc.username, { ai_beta_enabled: !acc.ai_beta_enabled });
      showToast(acc.ai_beta_enabled ? `已为 ${acc.username} 关闭 AI` : `已为 ${acc.username} 开启 AI`, 'success');
      if (await afterAccountMutation()) await refreshDetailIfOpen(acc.username);
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleResetPassword = (acc) => {
    setResetPassword('');
    setResetTarget(acc);
  };

  const handleResetSubmit = async (event) => {
    event.preventDefault();
    if (resetPassword.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    setResetBusy(true);
    try {
      await resetAccountPassword(resetTarget.username, resetPassword);
      showToast(`已重置 ${resetTarget.username} 的密码`, 'success');
      setResetTarget(null);
    } catch (error) {
      showToast(error.message || '重置密码失败', 'error');
    } finally {
      setResetBusy(false);
    }
  };

  const handleDelete = async (acc) => {
    if (!(await confirm(`确认删除账户「${acc.username}」？其订阅、收藏、分享链接与个人接口令牌会一并清除，且不可恢复。`))) return;
    try {
      await deleteAccount(acc.username);
      showToast(`已删除 ${acc.username}`, 'success');
      if (detailUser === acc.username) closeDetail();
      setSelectedAccounts((prev) => {
        if (!prev.has(acc.username)) return prev;
        const next = new Set(prev); next.delete(acc.username); return next;
      });
      await afterAccountMutation();
    } catch (error) {
      showToast(error.message || '删除账户失败', 'error');
    }
  };

  // ── 排序表头(v3.41 M06):点击同列翻转方向,换列取该列的惯用初始方向 ──
  const handleSort = (key) => {
    if (acctSort === key) {
      setAcctOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setAcctSort(key);
      setAcctOrder(key === 'username' ? 'asc' : 'desc');
    }
  };

  // ── 勾选与批量(v3.41 M05):页级全选,批量走原子端点 ──
  const toggleAccountSelection = (username) => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(username)) next.delete(username); else next.add(username);
      return next;
    });
  };

  const handleBatch = async (updates, { confirmMsg, done } = {}) => {
    const names = Array.from(selectedAccounts);
    if (!names.length) return;
    if (confirmMsg && !(await confirm(confirmMsg))) return;
    setBatchBusy(true);
    try {
      const res = await batchUpdateAccounts(names, updates);
      showToast(
        `${done(res.updated)}${res.unchanged ? `（${res.unchanged} 个本已如此）` : ''}`,
        'success',
      );
      setSelectedAccounts(new Set());
      await afterAccountMutation();
    } catch (error) {
      // 原子语义:任一账户不存在/末位管理员保护 → 整批未生效,后端文案直接透传。
      showToast(error.message || '批量更新失败', 'error');
    } finally {
      setBatchBusy(false);
    }
  };
  // 账户分页派生(服务端分页:items 即当前页,total 为过滤后总数)。
  const pagedAccounts = acctData?.items ?? [];
  const acctTotal = acctData?.total ?? 0;
  const acctSummary = acctData?.summary || null;
  const accountTotalPages = Math.max(1, Math.ceil(acctTotal / ACCOUNTS_PAGE_SIZE));
  const acctFiltersActive = Boolean(acctQ || acctRole || acctStatus || acctAi);
  // 页级全选(150 人规模在 15/页 下最多十页;勾选集跨页累积,过滤变化即清)。
  const pageUsernames = pagedAccounts.map((a) => a.username);
  const allPageSelected = pageUsernames.length > 0 && pageUsernames.every((n) => selectedAccounts.has(n));
  const toggleAllPage = () => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (allPageSelected) pageUsernames.forEach((n) => next.delete(n));
      else pageUsernames.forEach((n) => next.add(n));
      return next;
    });
  };
  // 列头即操作:排序/筛选/搜索三种列头统一走共享组件(components/admin/TableTh,
  // 账户/审计/自定源三表同一套操作语言与样式)。
  // 数据收缩(删号/改窗)后当前页越界时回落到末页。
  useEffect(() => {
    if (acctData && accountPage > accountTotalPages) setAccountPage(accountTotalPages);
  }, [acctData, accountPage, accountTotalPages]);

  // ── 用户子页总览 KPI（窗口指标，服务端 summary 聚合全量,不受分页/搜索影响）──
  const userKpis = useMemo(() => ({
    accounts: acctSummary?.accounts ?? 0,
    admins: acctSummary?.admins ?? 0,
    disabled: acctSummary?.disabled ?? 0,
    loggedIn: acctSummary?.logged_in_window ?? 0,
    logins: acctSummary?.logins ?? 0,
    reads: acctSummary?.reads ?? 0,
    aiCalls: acctSummary?.ai_calls ?? 0,
    aiTokens: acctSummary?.ai_tokens ?? 0,
  }), [acctSummary]);
  // 根身份被转走(true→false):收起根专属层、清逐用户态,并重取已剥掉按用户维度的 AI 用量。
  const wasRoot = useRef(rootAdmin);
  useEffect(() => {
    if (wasRoot.current && !rootAdmin) {
      closeDetail();
      setCreateModalOpen(false);
      setResetTarget(null); setResetPassword('');
      setSelectedAccounts(new Set());
      setAcctData({ items: [], total: 0, summary: null });
    }
    wasRoot.current = rootAdmin;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- closeDetail 是普通函数,只在 rootAdmin 翻转时调用
  }, [rootAdmin]);
  const perDay = (n) => (days > 0 ? (n / days).toFixed(1) : '0');
  // 活跃用户 Top：按所选维度（阅读 / 登录）排行,服务端全量聚合。
  const activeUserRows = useMemo(() => {
    const rows = topMetric === 'reads' ? acctSummary?.top_reads : acctSummary?.top_logins;
    return (rows ?? []).map((r) => ({ name: r.username, value: r.value }));
  }, [acctSummary, topMetric]);

  // ── 单用户详情：打开抽屉并拉取窗口活动（'detail' 代次守卫,见 refreshDetailIfOpen 注释）──
  const openDetail = useCallback(async (username) => {
    const fresh = claimGen('detail');
    detailUserRef.current = username;
    setDetailUser(username);
    setDetailData(null);
    setLoginListOpen(false);
    try {
      const data = await fetchAccountActivity(username, days);
      if (fresh()) setDetailData(data);
    } catch (error) {
      if (fresh()) showToast(error.message || '获取用户详情失败', 'error');
    }
  }, [claimGen, days, showToast]);

  // 详情抽屉图表数据：每日 AI 用量（按用途堆叠，calls / tokens 两套）+ 各源阅读/收藏。
  const detailDayPurpose = useMemo(
    () => (detailData?.usage?.by_day_purpose ?? []).map((r) => ({ ...r, purpose: PURPOSE_LABELS[r.purpose] || r.purpose })),
    [detailData],
  );
  const detailWindow = detailData?.usage?.window_days ?? days;
  const detailDatasets = useMemo(() => ({
    calls: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'calls'),
    tokens: pivotDaily(detailDayPurpose, detailWindow, 'purpose', 'total_tokens'),
  }), [detailDayPurpose, detailWindow]);
  const detailEngagementRows = useMemo(
    () => (detailData?.source_engagement ?? []).map((s) => ({ name: s.name || s.source_id, reads: s.reads, favorites: s.favorites })),
    [detailData],
  );

  return (
    <div className="ops-page-body">
        <div>
          <section id={opsBlockId('accounts', 'growth')} className="ops-block" tabIndex={-1} aria-label="账户增长">
          {/* 非根管理员:只见聚合数字(账户分布 + 增长曲线),账户名单/活跃榜/逐用户抽屉不渲染(端点侧 403 兜底)。 */}
          {!rootAdmin && (
            <section className="surface-card kpi-strip" aria-label="账户概览">
              <Kpi num={fmtNum(growth?.totals?.accounts ?? 0)} label="账户" sub="现存" />
              <Kpi num={fmtNum(growth?.totals?.admins ?? 0)} label="管理员" sub="含根管理员" />
              <Kpi num={fmtNum(growth?.totals?.readers ?? 0)} label="读者" sub="现存" />
              <Kpi num={fmtNum(growth?.totals?.disabled ?? 0)} label="停用" sub="不计入活跃" tone={growth?.totals?.disabled > 0 ? 'is-warn' : undefined} />
            </section>
          )}
          {rootAdmin && (
          <section className="surface-card kpi-strip" aria-label="窗口活跃概览">
            <Kpi
              num={fmtNum(userKpis.accounts)}
              label="账户"
              sub={(
                <>
                  <button
                    type="button"
                    className={`kpi-sub-link ${acctRole === 'admin' ? 'is-on' : ''}`}
                    title={acctRole === 'admin' ? '取消管理员筛选' : '筛选管理员账户'}
                    onClick={() => setAcctRole((r) => (r === 'admin' ? '' : 'admin'))}
                  >
                    管理员 {userKpis.admins}
                  </button>
                  {userKpis.disabled > 0 && (
                    <>
                      {' · '}
                      <button
                        type="button"
                        className={`kpi-sub-link ${acctStatus === 'disabled' ? 'is-on' : ''}`}
                        title={acctStatus === 'disabled' ? '取消停用筛选' : '筛选停用账户'}
                        onClick={() => setAcctStatus((s) => (s === 'disabled' ? '' : 'disabled'))}
                      >
                        停用 {userKpis.disabled}
                      </button>
                    </>
                  )}
                </>
              )}
            />
            <Kpi num={fmtNum(userKpis.loggedIn)} label={`近 ${days} 天活跃`} sub="登录过 ≥1 次" />
            <Kpi num={fmtNum(userKpis.logins)} label="登录次数" sub={`日均 ${perDay(userKpis.logins)}`} />
            <Kpi num={fmtNum(userKpis.reads)} label="阅读次数" sub={`日均 ${perDay(userKpis.reads)}`} />
            <Kpi num={fmtNum(userKpis.aiCalls)} label="AI 调用" sub={`tokens ${fmtNum(userKpis.aiTokens)}`} />
          </section>
          )}

          <AccountGrowth growth={growth} days={days} />
          </section>

          <section id={opsBlockId('accounts', 'guest')} className="ops-block" tabIndex={-1} aria-label="游客">
          <GuestZone days={days} showToast={showToast} refreshTick={refreshTick} />
          </section>

          {rootAdmin && (userKpis.reads + userKpis.logins) > 0 && (
            <section id={opsBlockId('accounts', 'top')} className="ops-block" tabIndex={-1} aria-label="活跃用户 Top">
              <div className="zone-head">
                <span className="zone-title">活跃用户 Top</span>
                <span className="zone-hint">近 {days} 天</span>
                <span className="zone-acts mini-seg" role="group" aria-label="排序维度">
                  {[['reads', '按阅读'], ['logins', '按登录']].map(([k, lbl]) => (
                    <button key={k} type="button" onClick={() => setTopMetric(k)} className={`mini-seg-btn ${topMetric === k ? 'is-on' : ''}`}>{lbl}</button>
                  ))}
                </span>
              </div>
              <section className="surface-card card-pad rounded-[var(--r-card)]">
                <RankBars
                  rows={activeUserRows}
                  labelKey="name"
                  valueKey="value"
                  name={topMetric === 'reads' ? '阅读' : '登录'}
                  height={Math.max(112, activeUserRows.length * 26)}
                  tickFormatter={truncLabel}
                  emptyHint={topMetric === 'reads' ? '窗口内还没有阅读记录' : '窗口内还没有登录记录'}
                />
              </section>
            </section>
          )}

          {rootAdmin && (
          <section id={opsBlockId('accounts', 'table')} className="ops-block" tabIndex={-1} aria-label="账户管理">
          <div className="zone-head">
            <span className="zone-title">账户管理</span>
            <span className="zone-acts">
              <button onClick={() => setCreateModalOpen(true)} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                <UserPlus className="h-4 w-4" /> 新建账户
              </button>
            </span>
          </div>

          <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
            {acctData === null ? (
              <p className="p-6 tiny-meta">加载中…</p>
            ) : (
              <>
                {acctTotal === 0 ? (
                  <p className="p-6 text-center tiny-meta">
                    {acctFiltersActive ? (
                      <>
                        没有匹配当前筛选的账户。
                        <button
                          type="button"
                          className="kpi-sub-link"
                          onClick={() => { setAcctRole(''); setAcctStatus(''); setAcctAi(''); setAccountQuery(''); }}
                        >
                          清除筛选
                        </button>
                      </>
                    ) : '还没有账户，用右上角「新建账户」创建第一个。'}
                  </p>
                ) : (
                  <>
                <div className="acct-scroll">
                  <table className="acct-table is-fixed">
                    <thead>
                      <tr>
                        <th className="acct-th" style={{ width: 40 }}>
                          <input
                            type="checkbox"
                            aria-label="全选本页账户"
                            checked={allPageSelected}
                            onChange={toggleAllPage}
                            className="h-4 w-4 cursor-pointer rounded align-middle"
                          />
                        </th>
                        <ThSearch label="用户" value={accountQuery} onChange={setAccountQuery} placeholder="搜索用户名" active={Boolean(acctQ)} />
                        <ThFilter label="角色" value={acctRole} onChange={setAcctRole} options={[['', '全部'], ['admin', '管理员'], ['user', '读者']]} width={96} />
                        <ThFilter label="状态" value={acctStatus} onChange={setAcctStatus} options={[['', '全部'], ['active', '启用'], ['disabled', '停用']]} width={80} />
                        <ThFilter label="AI" value={acctAi} onChange={setAcctAi} options={[['', '全部'], ['on', 'AI 已开'], ['off', 'AI 未开']]} width={88} />
                        <ThSort label="最近登录" k="last_login" sort={acctSort} order={acctOrder} onSort={handleSort} width={124} />
                        <ThSort label="登录" k="logins" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
                        <ThSort label="阅读" k="reads" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
                        <ThSort label="AI 调用" k="ai_calls" sort={acctSort} order={acctOrder} onSort={handleSort} num width={96} />
                        <ThSort label="订阅" k="subscriptions" sort={acctSort} order={acctOrder} onSort={handleSort} num width={76} />
                      </tr>
                    </thead>
                    <tbody>
                      {pagedAccounts.map((account) => (
                        <tr
                          key={account.username}
                          className={`acct-row ${detailUser === account.username ? 'is-sel' : ''}`}
                          role="button"
                          tabIndex={0}
                          aria-label={`${account.username} 活动详情`}
                          onClick={() => openDetail(account.username)}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(account.username); } }}
                        >
                          <td onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              aria-label={`选择账户 ${account.username}`}
                              checked={selectedAccounts.has(account.username)}
                              onChange={() => toggleAccountSelection(account.username)}
                              className="h-4 w-4 cursor-pointer rounded align-middle"
                            />
                          </td>
                          <td>
                            <span className="acct-user">
                              <span className="acct-avatar avatar-letter" style={{ '--avatar-h': avatarHue(account.username) }}>{avatarInitial(account.username)}</span>
                              <span className="acct-name">{account.username}</span>
                            </span>
                          </td>
                          <td>
                            {account.role === 'admin'
                              ? <span className="sett-role-chip" style={{ color: 'var(--dorami-accent-ink)' }}><ShieldCheck className="h-3 w-3" /> 管理员</span>
                              : <span className="sett-role-chip">读者</span>}
                          </td>
                          <td>{account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>}</td>
                          <td>
                            {account.ai_beta_enabled
                              ? <span className="acct-ai-flag is-on" title="AI 已开启"><Zap /></span>
                              : <span className="acct-ai-flag" title="AI 未开启"><ZapOff /></span>}
                          </td>
                          <td><span className="acct-mono">{formatStamp(account.last_login_at)}</span></td>
                          <td className={`acct-n ${(account.logins || 0) ? '' : 'is-zero'}`}>{account.logins || '–'}</td>
                          <td className={`acct-n is-main ${(account.reads || 0) ? '' : 'is-zero'}`}>{account.reads || '–'}</td>
                          <td className={`acct-n ${(account.ai_calls || 0) ? '' : 'is-zero'}`}>{account.ai_calls || '–'}</td>
                          <td className="acct-n">{account.subscription_count ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* 批量操作条(v3.41 M05,返修改小图标组):勾选即现,动作走原子批量端点;
                    语义全靠图标 + title,与详情抽屉的动作图标同一套词汇。 */}
                {selectedAccounts.size > 0 && (
                  <div className="ledger-batchbar">
                    <span className="ledger-batch-n">{selectedAccounts.size} 个已选</span>
                    <span className="batch-acts">
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy || globalAi === false}
                        title={globalAi === false ? 'AI 功能总闸已关闭（读者功能页）' : '为已选账户开启 AI'}
                        onClick={() => handleBatch({ ai_beta_enabled: true }, { done: (n) => `已为 ${n} 个账户开启 AI` })}
                      >
                        <Zap />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy || globalAi === false}
                        title={globalAi === false ? 'AI 功能总闸已关闭（读者功能页）' : '为已选账户关闭 AI'}
                        onClick={() => handleBatch({ ai_beta_enabled: false }, { done: (n) => `已为 ${n} 个账户关闭 AI` })}
                      >
                        <ZapOff />
                      </button>
                      <span className="ai-divider" />
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="启用已选账户"
                        onClick={() => handleBatch({ is_active: true }, { done: (n) => `已启用 ${n} 个账户` })}
                      >
                        <Power />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="停用已选账户"
                        onClick={() => handleBatch(
                          { is_active: false },
                          {
                            confirmMsg: `确认停用选中的 ${selectedAccounts.size} 个账户？其登录会话将立即失效。`,
                            done: (n) => `已停用 ${n} 个账户`,
                          },
                        )}
                      >
                        <Ban />
                      </button>
                      <span className="ai-divider" />
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="将已选账户设为管理员"
                        onClick={() => handleBatch(
                          { role: 'admin' },
                          {
                            confirmMsg: `确认将选中的 ${selectedAccounts.size} 个账户设为管理员？管理员拥有采集、归档、账户与系统配置的全部权限。`,
                            done: (n) => `已将 ${n} 个账户设为管理员`,
                          },
                        )}
                      >
                        <ShieldCheck />
                      </button>
                      <button
                        type="button"
                        className="rowact-btn"
                        disabled={batchBusy}
                        title="将已选账户设为读者"
                        onClick={() => handleBatch(
                          { role: 'user' },
                          {
                            confirmMsg: `确认将选中的 ${selectedAccounts.size} 个账户设为读者？${selectedAccounts.has(currentUsername) ? '选中包含你自己，生效后你将立即被登出管理台。' : ''}`,
                            done: (n) => `已将 ${n} 个账户设为读者`,
                          },
                        )}
                      >
                        <ShieldOff />
                      </button>
                    </span>
                    <span className="flex-1" />
                    <button
                      type="button"
                      className="rowact-btn"
                      title="取消选择"
                      aria-label="取消选择"
                      onClick={() => setSelectedAccounts(new Set())}
                    >
                      <X />
                    </button>
                  </div>
                )}
                {accountTotalPages > 1 && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
                    <span className="tiny-meta">
                      共 {acctTotal} 个 · 第 {(accountPage - 1) * ACCOUNTS_PAGE_SIZE + 1}–{Math.min(accountPage * ACCOUNTS_PAGE_SIZE, acctTotal)} 个
                    </span>
                    <Pager page={accountPage} totalPages={accountTotalPages} onPage={setAccountPage} />
                  </div>
                )}
                  </>
                )}
              </>
            )}
          </section>

          </section>
          )}

          {/* ── 操作审计(v3.19 多管理员):管理面写操作逐条落行,多管理员互相可查;不属读者隐私,全体管理员可见 ── */}
          <section id={opsBlockId('accounts', 'audit')} className="ops-block" tabIndex={-1} aria-label="操作审计">
          <AdminAuditPanel days={days} showToast={showToast} />
          </section>
        </div>
      {/* ── 单用户活动详情抽屉（右缘滑入，ledger-drawer 语法）;仅根管理员 ── */}
      {rootAdmin && (<>
      <div className={`ledger-scrim ${detailUser ? 'is-open' : ''}`} onClick={closeDetail} aria-hidden="true" />
      <aside
        ref={detailPanelRef}
        className={`ledger-drawer ${detailUser ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={detailUser ? `${detailUser} · 活动详情` : '用户活动详情'}
        aria-hidden={!detailUser}
      >
        <div className="ledger-drawer-head acct-drawer-head">
          <span className="acct-avatar avatar-letter" style={{ '--avatar-h': avatarHue(detailUser) }}>{detailUser ? avatarInitial(detailUser) : ''}</span>
          <span className="ledger-drawer-title">{detailUser}</span>
          {detailData && (detailData.account.is_active ? <span className="stamp stamp-ok">启用</span> : <span className="stamp stamp-idle">停用</span>)}
          {/* 就地管理动作(v3.41 M19,小图标形态):与批量条同一套图标词汇,语义挂 title */}
          {detailData && (
            <span className="drawer-acts">
              {globalAi !== false && (
                <button
                  type="button"
                  className={`rowact-btn ${detailData.account.ai_beta_enabled ? 'is-on' : ''}`}
                  title={detailData.account.ai_beta_enabled ? `关闭 ${detailUser} 的 AI` : `开启 ${detailUser} 的 AI`}
                  onClick={() => handleToggleAiBeta(detailData.account)}
                >
                  {detailData.account.ai_beta_enabled ? <Zap /> : <ZapOff />}
                </button>
              )}
              <button
                type="button"
                className={`rowact-btn ${detailData.account.role === 'admin' ? 'is-on' : ''}`}
                title={detailData.account.role === 'admin' ? `取消 ${detailUser} 的管理员身份` : `将 ${detailUser} 设为管理员`}
                onClick={() => handleToggleRole(detailData.account)}
              >
                {detailData.account.role === 'admin' ? <ShieldOff /> : <ShieldCheck />}
              </button>
              <button
                type="button"
                className="rowact-btn"
                title="重置密码"
                onClick={() => handleResetPassword(detailData.account)}
              >
                <KeyRound />
              </button>
              <button
                type="button"
                className="rowact-btn"
                title={detailData.account.is_active ? '停用账户' : '启用账户'}
                onClick={() => handleToggleActive(detailData.account)}
              >
                {detailData.account.is_active ? <Ban /> : <Power />}
              </button>
              <button
                type="button"
                className="rowact-btn is-danger"
                title="删除账户"
                onClick={() => handleDelete(detailData.account)}
              >
                <Trash2 />
              </button>
              <span className="ai-divider" />
            </span>
          )}
          <button type="button" className="icon-button shrink-0" onClick={closeDetail} aria-label="关闭详情"><X className="h-5 w-5" /></button>
        </div>
        <div className="ledger-drawer-body">
          {!detailData ? (
            <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center tiny-meta">
              <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载活动详情…
            </p>
          ) : (
            <>
              <div className="tiles">
                <div className="tile"><div className="tile-num">{fmtNum(detailData.reads.total)}</div><div className="tile-lbl">近 {detailWindow} 天阅读</div></div>
                <div className="tile"><div className="tile-num">{fmtNum(detailData.usage.totals.calls)}</div><div className="tile-lbl">近 {detailWindow} 天 AI 调用</div></div>
                <div className="tile"><div className="tile-num">{fmtNum(detailData.account.subscription_count)}</div><div className="tile-lbl">订阅来源</div></div>
              </div>

              {detailData.logins.recent.length > 0 ? (
                <details className="login-card" open={loginListOpen} onToggle={(e) => setLoginListOpen(e.currentTarget.open)}>
                  <summary>
                    <span className="tile-num" style={{ fontSize: '18px', lineHeight: '22px' }}>{fmtNum(detailData.logins.count)}</span>
                    <span className="tile-lbl" style={{ alignSelf: 'center' }}>近 {detailWindow} 天登录 · 最近 {formatStamp(detailData.account.last_login_at)}</span>
                    <span className="login-toggle">{loginListOpen ? '收起' : `展开近 ${detailData.logins.recent.length} 次`}</span>
                  </summary>
                  <ul className="login-list">
                    {detailData.logins.recent.map((at, i) => <li key={`${at}-${i}`}>{formatStamp(at)}</li>)}
                  </ul>
                </details>
              ) : (
                <div className="login-card">
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span className="tile-num" style={{ fontSize: '18px', lineHeight: '22px' }}>{fmtNum(detailData.logins.count)}</span>
                    <span className="tile-lbl" style={{ alignSelf: 'center' }}>近 {detailWindow} 天登录</span>
                  </div>
                </div>
              )}

              {(detailData.usage.totals.calls === 0 && detailData.reads.total === 0 && detailData.favorites_total === 0) ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                  近 {detailWindow} 天该用户没有阅读 / 收藏 / AI 调用记录。
                </p>
              ) : (
                <>
                  <div>
                    <div className="drawer-sec-title">各源 · 文章阅读 / 文章收藏{detailEngagementRows.length ? ` · ${detailEngagementRows.length} 个源` : ''}</div>
                    <BarList
                      rows={detailEngagementRows}
                      nameKey="name"
                      metrics={[{ key: 'reads', name: '文章阅读', color: C_READ }, { key: 'favorites', name: '文章收藏', color: C_FAVORITE }]}
                      emptyHint="窗口内无阅读、且无收藏记录"
                    />
                  </div>
                  <MultiSeriesBars
                    title="每日 AI 用量"
                    datasets={detailDatasets}
                    dims={[['calls', '调用'], ['tokens', 'tokens']]}
                    namespace="user-detail"
                  />
                </>
              )}
            </>
          )}
        </div>
      </aside>
      </>)}

      {/* ── 新建账户弹窗(portal 到 body,避开变换祖先造成的 fixed 错位) ── */}
      <Modal
        open={createModalOpen} onClose={() => setCreateModalOpen(false)} closeOnOverlay portal size="md"
        as="form" panelClassName="form-sheet" ariaLabel="新建账户" panelProps={{ onSubmit: handleCreate }}
      >
        <div className="form-sheet-head">
          <h3 className="card-title">新建账户</h3>
          <button type="button" onClick={() => setCreateModalOpen(false)} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
        </div>
        <div className="form-sheet-body">
          <div className="form-sheet-field">
            <label className="form-label" htmlFor="acct-new-name">用户名</label>
            <input id="acct-new-name" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="用户名" autoComplete="off" className="form-input w-full" />
          </div>
          <div className="form-sheet-field">
            <label className="form-label" htmlFor="acct-new-pw">初始密码</label>
            <input id="acct-new-pw" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="至少 6 位" autoComplete="new-password" className="form-input w-full" />
          </div>
          <div className="form-sheet-field">
            <span className="form-label">角色</span>
            <div className="mini-seg" role="group" aria-label="账户角色">
              {[['user', '读者'], ['admin', '管理员']].map(([role, label]) => (
                <button
                  key={role}
                  type="button"
                  onClick={() => setNewRole(role)}
                  className={`mini-seg-btn ${newRole === role ? 'is-on' : ''}`}
                >
                  {label}
                </button>
              ))}
            </div>
            {newRole === 'admin' && (
              <p className="tiny-meta" style={{ marginTop: 6 }}>管理员拥有采集、归档、账户与系统配置的全部权限。</p>
            )}
          </div>
        </div>
        <div className="form-sheet-foot">
          <button type="button" onClick={() => setCreateModalOpen(false)} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
          <button type="submit" disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UserPlus className="h-3.5 w-3.5" />} 创建{newRole === 'admin' ? '管理员' : '读者'}账户
          </button>
        </div>
      </Modal>

      {/* ── 重置密码弹窗(portal 到 body) ── */}
      <Modal
        open={Boolean(resetTarget)} onClose={() => setResetTarget(null)} closeOnOverlay portal size="md"
        as="form" panelClassName="form-sheet" ariaLabel="重置密码" panelProps={{ onSubmit: handleResetSubmit }}
      >
        <div className="form-sheet-head">
          <h3 className="card-title">重置密码</h3>
          <button type="button" onClick={() => setResetTarget(null)} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
        </div>
        <div className="form-sheet-body">
          <p className="tiny-meta">为账户「{resetTarget?.username}」设置新密码，设置后该账户需用新密码登录。</p>
          <div className="form-sheet-field">
            <label className="form-label" htmlFor="acct-reset-pw">新密码</label>
            <input
              id="acct-reset-pw"
              type="password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
              placeholder="至少 6 位"
              autoComplete="new-password"
              autoFocus
              className="form-input w-full"
            />
          </div>
        </div>
        <div className="form-sheet-foot">
          <button type="button" onClick={() => setResetTarget(null)} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
          <button type="submit" disabled={resetBusy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {resetBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />} 保存新密码
          </button>
        </div>
      </Modal>
    </div>
  );
}
