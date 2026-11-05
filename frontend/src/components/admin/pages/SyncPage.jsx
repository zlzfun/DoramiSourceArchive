/**
 * 运维 · 节点同步页(issue #261,样页 docs/design/issue-261-sync-console/ 方向 A 流水线):
 * 页头(面包屑 + 对端身份;测试连接 · ⋯ 高危 · 立即同步 + 定时胶囊)· 流水线卡 · 运行记录 · 连接与定时;
 * 逐流 / 运行 / 高危三种抽屉;定时 / 测试连接浮层。未配置 = 页顶「接入远端」卡,其余板块空态变淡。
 * 节点不分角色:每台都一致显示「本机从其它 server 拉取」的同步(拍板 #3),没拉过显示接入卡;不做「被拉取」视图
 * (2026-11-05 用户:一台同时拉与被拉罕见)、数据来源角标、更换远端。
 * 出网不扩大(方案 §3):打开页面只读本机(status / inventory),不自动测试连接;运行中轮询 GET /api/jobs/{id}。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchBackgroundJob, fetchRemoteSyncInventory, fetchRemoteSyncSchedule, fetchRemoteSyncStatus,
  resetRemoteSyncStream, saveRemoteSyncSchedule, startRemoteSync, testRemoteSync,
} from '../../../api';
import { opsBlockId } from '../../../utils/opsNav';
import {
  consoleOf, fmt, fmtSnapshot, runErrorHead, runFromCheckpoints, runOf, runTotals, streamName, toDate,
} from '../../../utils/syncCatalog';
import StaleNotice from '../StaleNotice';
import SyncHead from '../sync/SyncHead';
import SyncPipeline from '../sync/SyncPipeline';
import SyncRuns from '../sync/SyncRuns';
import SyncLink from '../sync/SyncLink';
import SyncConnect from '../sync/SyncConnect';
import { CredPop, ProbePop, SchedPop, SyncConfirm } from '../sync/SyncPops';
import { DangerDrawer, RunDrawer, StreamDrawer } from '../sync/SyncDrawers';

const POLL_MS = 1500; // 沿用设置柜远程同步的轮询节奏
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const sameUrl = (a, b) => String(a || '').replace(/\/+$/, '') === String(b || '').replace(/\/+$/, '');
const needsPassword = (e) => e?.status === 400 && /密码/.test(String(e?.message || ''));

export default function SyncPage({ crumb, showToast, refreshTick = 0, onArticlesChanged }) {
  const [status, setStatus] = useState(null);
  const [statusErr, setStatusErr] = useState('');
  const [inventory, setInventory] = useState(null);
  const [schedFallback, setSchedFallback] = useState(null); // 老后端没有 console.schedule 时的 GET /schedule
  const [liveJob, setLiveJob] = useState(null);
  const [pollId, setPollId] = useState(null);
  const [now, setNow] = useState(() => new Date());
  const [pop, setPop] = useState(''); // probe | sched | cred
  const [credMsg, setCredMsg] = useState('');
  const [starting, setStarting] = useState(false);
  const [drawerKind, setDrawerKind] = useState(''); // stream | run | danger
  const [streamId, setStreamId] = useState('sources');
  const [runId, setRunId] = useState('');
  const [pick, setPick] = useState('analyses');
  const [confirm, setConfirm] = useState(null); // { kind: 'reset' | 'replay', sid }
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);

  // 每组 loader 的代次守卫:迟到响应不覆盖新快照(运维 loader 纪律)
  const gens = useRef({});
  const claim = useCallback((key) => {
    const g = (gens.current[key] || 0) + 1;
    gens.current[key] = g;
    return () => gens.current[key] === g;
  }, []);

  const loadStatus = useCallback(() => {
    const fresh = claim('status');
    return fetchRemoteSyncStatus()
      .then((v) => {
        if (!fresh()) return v;
        setStatus(v);
        setStatusErr('');
        const active = (v?.jobs || []).find((j) => j.status === 'running' || j.status === 'queued');
        if (active) setPollId((cur) => cur || active.job_id);
        if (!v?.console?.schedule) {
          fetchRemoteSyncSchedule().then((s) => { if (fresh()) setSchedFallback(s); }).catch(() => {});
        }
        return v;
      })
      .catch((e) => { if (fresh()) setStatusErr(e.message || '获取同步状态失败'); });
  }, [claim]);
  const loadInventory = useCallback(() => {
    const fresh = claim('inventory');
    return fetchRemoteSyncInventory().then((v) => { if (fresh()) setInventory(v); }).catch(() => {});
  }, [claim]);
  useEffect(() => { loadStatus(); loadInventory(); }, [loadStatus, loadInventory, refreshTick]);

  // ── 运行中轮询:结束后刷新 status 与 inventory ──
  const onDoneRef = useRef(null);
  const onDone = (job) => {
    loadStatus();
    loadInventory();
    if (job.status === 'succeeded') {
      const run = runOf(job);
      const t = runTotals(run);
      showToast?.(`已完成同步:${fmt(t.count)} 条 · 新增 ${fmt(t.inserted)}`, 'success');
      onArticlesChanged?.();
    } else if (job.status === 'failed') {
      const run = runOf(job);
      showToast?.(`同步失败:${runErrorHead(run)}${job.error ? ` · ${job.error}` : ''}`, 'error');
    }
  };
  useEffect(() => { onDoneRef.current = onDone; });
  useEffect(() => {
    if (!pollId) return undefined;
    let alive = true;
    let timer = 0;
    const tick = async () => {
      let job;
      try {
        job = await fetchBackgroundJob(pollId);
      } catch {
        if (alive) timer = window.setTimeout(tick, POLL_MS * 2); // 轮询失败不刷屏,放慢再试
        return;
      }
      if (!alive) return;
      setLiveJob(job);
      if (TERMINAL.has(job.status)) {
        setPollId(null);
        onDoneRef.current?.(job);
        return;
      }
      timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [pollId]);

  const sched = status?.console?.schedule || schedFallback;
  const con = useMemo(() => (status ? consoleOf(status, sched) : null), [status, sched]);
  const configured = Boolean(con?.configured);
  const order = con?.stream_order || null;

  // 任务列表:轮询到的最新快照替换同 id 的那一条
  const jobs = useMemo(() => {
    const list = (status?.jobs || []).slice();
    if (liveJob) {
      const i = list.findIndex((j) => j.job_id === liveJob.job_id);
      if (i >= 0) list[i] = { ...list[i], ...liveJob, created_by: list[i].created_by ?? liveJob.created_by };
      else list.unshift(liveJob);
    }
    const at = (j) => toDate(j.created_at)?.getTime() ?? 0;
    return list.sort((a, b) => at(b) - at(a)).slice(0, 10);
  }, [status, liveJob]);
  const runs = useMemo(() => jobs.map((j) => runOf(j, order)).filter(Boolean), [jobs, order]);
  const cardRun = runs[0] || (configured ? runFromCheckpoints(con.checkpoints, order) : null);
  // 整次成功:最近 10 次里的成功任务,否则后端记的 v2_last_success_at(逐流推进时间不算,检视 R1 #4)
  const lastOkAt = runs.find((r) => r.status === 'succeeded')?.endAt || con?.last_success_at || '';
  const active = Boolean(cardRun?.active);

  // 运行中每秒走一次「已用」
  useEffect(() => {
    if (!active) return undefined;
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, [active]);

  // ── 动作 ──
  const savedUser = sched?.username || con?.username || '';
  const hasSaved = Boolean(sched?.password_set && savedUser && (!sched?.base_url || sameUrl(sched.base_url, con?.target)));
  // 已接入 v2 后手动「立即同步 / 测试连接」固定 v2;旧 v1 定时协议只在定时弹层里管理(检视 R1 #2)。
  const protocol = configured ? 'v2' : (sched?.protocol || 'v2');

  const launch = useCallback(async (baseUrl, username, password, proto = 'v2') => {
    const { job_id: jobId } = await startRemoteSync(baseUrl, username, password, { protocol: proto });
    setLiveJob({ job_id: jobId, status: 'queued', created_at: Date.now() / 1000, progress: null, result: null });
    setPollId(jobId);
    showToast?.('已开始同步', 'success');
    loadStatus();
  }, [showToast, loadStatus]);

  const syncNow = useCallback(async () => {
    if (!con?.target || active || starting) return;
    setPop('');
    if (!hasSaved) { setCredMsg(''); setPop('cred'); return; }
    setStarting(true);
    try {
      // 先试已存的定时凭据(空密码 = 用已存的);后端不接受时补密码
      await launch(con.target, savedUser, '', protocol);
    } catch (e) {
      if (needsPassword(e)) { setCredMsg(''); setPop('cred'); } else showToast?.(e.message || '启动同步失败', 'error');
    } finally {
      setStarting(false);
    }
  }, [con, active, starting, hasSaved, launch, savedUser, protocol, showToast]);

  const startWithCred = useCallback(async (username, password) => {
    setStarting(true);
    try { await launch(con.target, username, password, protocol); } finally { setStarting(false); }
  }, [con, launch, protocol]);

  const testProbe = useCallback(async (username, password) => {
    const r = await testRemoteSync(con.target, username, password, protocol);
    loadStatus();
    return r;
  }, [con, protocol, loadStatus]);

  const connectTest = useCallback((baseUrl, username, password, proto) => testRemoteSync(baseUrl, username, password, proto), []);
  const connectStart = useCallback(async (baseUrl, username, password, proto) => {
    setStarting(true);
    try { await launch(baseUrl, username, password, proto); } finally { setStarting(false); }
  }, [launch]);

  const saveSched = useCallback(async (payload) => {
    await saveRemoteSyncSchedule(payload);
    showToast?.(payload.enabled ? '已开启定时同步' : '已保存定时同步(未启用)', 'success');
    loadStatus();
  }, [showToast, loadStatus]);

  const openStream = (sid) => { setPop(''); setStreamId(sid); setDrawerKind('stream'); };
  const openRun = (id) => { setPop(''); setRunId(id); setDrawerKind('run'); };
  const openDanger = (sid = null) => { setPop(''); if (sid) setPick(sid); setDrawerKind('danger'); };
  const closeDrawer = useCallback(() => setDrawerKind(''), []);

  const ask = (kind, sid) => { setConfirm({ kind, sid }); setConfirmOpen(true); };
  const doConfirm = async () => {
    if (!confirm) return;
    setConfirmBusy(true);
    try {
      await resetRemoteSyncStream(confirm.sid);
      showToast?.(confirm.kind === 'replay' ? '已重放分析流,下次同步整流重拉' : `已重置「${streamName(confirm.sid)}」checkpoint`, 'success');
      setConfirmOpen(false);
      loadStatus();
      loadInventory();
    } catch (e) {
      showToast?.(e.message || '重置同步流失败', 'error');
    } finally {
      setConfirmBusy(false);
    }
  };
  const confirmCp = confirm ? con?.checkpoints?.[confirm.sid] : null;
  const confirmView = confirm?.kind === 'replay'
    ? {
      title: '重放分析流',
      kv: [['清空', 'analyses checkpoint 与本 authority 的分析实体状态'], ['下次同步', '整流重拉 AI 分析;只清掉确认缺席的']],
      okText: '重放分析流',
    }
    : confirm ? {
      title: `重置「${streamName(confirm.sid)}」checkpoint`,
      kv: [['流', <span key="s" className="sync-mono">{confirm.sid}</span>], ['当前', <span key="c" className="sync-mono">{confirmCp?.snapshot ? fmtSnapshot(confirm.sid, confirmCp.snapshot) : '—'}</span>], ['下次同步', '本流整流重拉,其余流照常增量']],
      okText: '重置所选流',
    } : { title: '', kv: [], okText: '' };

  const selectedRun = runs.find((r) => r.id === runId) || null;
  const togglePop = (name) => setPop((p) => (p === name ? '' : name));

  return (
    <>
      <SyncHead
        crumb={crumb}
        con={con}
        sched={sched}
        run={cardRun}
        now={now}
        pop={pop}
        starting={starting}
        onProbe={() => togglePop('probe')}
        onDanger={() => openDanger()}
        onSync={syncNow}
        onSched={() => togglePop('sched')}
        probePop={configured && (
          <ProbePop
            open={pop === 'probe'}
            onClose={() => setPop('')}
            target={con?.target}
            savedUser={savedUser}
            hasSaved={hasSaved}
            lastProbe={con?.probe || null}
            onTest={testProbe}
          />
        )}
        schedPop={<SchedPop open={pop === 'sched'} onClose={() => setPop('')} sched={sched} target={con?.target || ''} fenceActive={Boolean(con?.consumer_fence?.active)} onSave={saveSched} />}
        credPop={<CredPop open={pop === 'cred'} onClose={() => setPop('')} savedUser={savedUser} message={credMsg} onStart={startWithCred} />}
      />
      {statusErr && (
        <div className="mb-3"><StaleNotice status="error" error={statusErr} onRetry={loadStatus} label="同步状态" /></div>
      )}
      {status && (
        <div className="ops-page-body sync-page">
          {!configured && (
            <section className="ops-block" aria-label="接入远端">
              <SyncConnect con={con} sched={sched} onTest={connectTest} onStart={connectStart} starting={starting} />
            </section>
          )}
          <section id={opsBlockId('sync', 'streams')} className="ops-block" tabIndex={-1} aria-label="逐流状态">
            {!configured && <div className="zone-head"><span className="zone-title">将同步</span></div>}
            <SyncPipeline
              con={con}
              run={configured ? cardRun : null}
              configured={configured}
              now={now}
              selected={drawerKind === 'stream' ? streamId : ''}
              onOpen={openStream}
              onRetry={syncNow}
              retryBusy={starting || active}
              lastOkAt={lastOkAt}
            />
          </section>
          {(configured || runs.length > 0) && (
            <section id={opsBlockId('sync', 'runs')} className="ops-block" tabIndex={-1} aria-label="运行记录">
              <SyncRuns runs={runs} order={order || []} now={now} onOpen={openRun} />
            </section>
          )}
          <section id={opsBlockId('sync', 'link')} className="ops-block" tabIndex={-1} aria-label="连接与定时">
            <SyncLink con={con} sched={sched} onSched={() => { window.scrollTo({ top: 0, behavior: 'auto' }); setPop('sched'); }} />
          </section>
        </div>
      )}

      <StreamDrawer
        open={drawerKind === 'stream'}
        onClose={closeDrawer}
        sid={streamId}
        con={con}
        inventory={inventory}
        run={configured ? cardRun : null}
        runs={runs}
        now={now}
        onReset={(sid) => openDanger(sid)}
      />
      <RunDrawer open={drawerKind === 'run'} onClose={closeDrawer} run={selectedRun} order={order || []} now={now} onRetry={() => { closeDrawer(); syncNow(); }} retryBusy={starting || active} />
      <DangerDrawer
        open={drawerKind === 'danger'}
        onClose={closeDrawer}
        con={con}
        pick={pick}
        setPick={setPick}
        locked={active}
        onAsk={ask}
        showToast={showToast}
        onArticlesChanged={onArticlesChanged}
      />
      <SyncConfirm
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={confirmView.title}
        kv={confirmView.kv}
        okText={confirmView.okText}
        onOk={doConfirm}
        busy={confirmBusy}
      />
    </>
  );
}
