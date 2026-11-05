// 节点同步的浮层:定时弹层 / 测试连接结果 / 补远端密码(立即同步时没有可用的已存凭据)+ 高危确认框。
// 浮层锚在触发件下方右对齐(.sync-anchor 内绝对定位,随页滚动不漂);Esc 走 useModalA11y 层栈(非模态,不困 Tab),
// 点外面关闭只认锚点外的按下。确认框走共用 Modal(closeOnOverlay)。
import { Fragment, useEffect, useRef, useState } from 'react';
import { LoaderCircle, TriangleAlert, X } from 'lucide-react';
import Modal from '../../Modal';
import SecretField from '../../SecretField';
import { useModalA11y } from '../../../hooks/useModalA11y';
import {
  FREQ_PRESETS, STREAM_ORDER, cronFromFreq, fmt, fmtDay, fmtSnapshot, fmtWhen, freqFromCron, negotiatedOrder, nextCronRun, withV,
} from '../../../utils/syncCatalog';

function usePopLayer(open, onClose) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useModalA11y(open, onClose, ref, { trapFocus: false });
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      const anchor = ref.current?.parentElement;
      if (anchor && !anchor.contains(e.target)) closeRef.current?.();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);
  return ref;
}

/* ── 定时同步:开关 / 频率 / cron / 协议 / 账号(密码只写不回显) ── */
export function SchedPop({ open, onClose, sched, target = '', onSave }) {
  const ref = usePopLayer(open, onClose);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  // 只在打开时取一次快照:编辑途中 status 刷新(任务结束等)不冲掉草稿
  const seed = useRef({ sched, target });
  useEffect(() => { seed.current = { sched, target }; });
  useEffect(() => {
    if (!open) return;
    const { sched: s0, target: t0 } = seed.current;
    const parsed = freqFromCron(s0?.cron);
    setForm({
      enabled: Boolean(s0?.enabled),
      ...parsed,
      protocol: s0?.protocol || (s0?.migration_required ? '' : 'v2'),
      baseUrl: s0?.base_url || t0 || '',
      username: s0?.username || '',
      password: '',
      editCred: !(s0?.username && s0?.password_set),
    });
    setErr('');
  }, [open]);
  if (!open || !form) return null;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const cron = cronFromFreq(form.freq, form.dailyTime, form.customCron);
  const next = form.enabled ? nextCronRun(cron) : null;
  const knownBase = Boolean(sched?.base_url || target);

  const save = async () => {
    if (!form.protocol) { setErr('先选择同步协议'); return; }
    if (form.enabled) {
      if (!form.baseUrl.trim() || !form.username.trim()) { setErr('启用定时同步需要远端地址与账号'); return; }
      if (!form.password && !sched?.password_set) { setErr('首次启用需要填写远端密码'); return; }
      if (!cron) { setErr('请填写 cron 表达式'); return; }
    }
    setSaving(true);
    setErr('');
    try {
      await onSave({
        enabled: form.enabled,
        cron: cron || '0 3 * * *',
        base_url: form.baseUrl.trim(),
        username: form.username.trim(),
        password: form.password,
        source_ids: form.protocol === 'v1' ? (sched?.source_ids || []) : [],
        protocol: form.protocol,
      });
      onClose();
    } catch (e) {
      setErr(e.message || '保存定时同步失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div ref={ref} className="ops-pop sync-pop" role="dialog" aria-label="定时同步">
      <div className="sync-pop-head">
        <span className="tools-title">定时同步</span>
        <button type="button" role="switch" aria-checked={form.enabled} aria-label="定时同步" className={`ledger-switch ${form.enabled ? 'is-on' : ''}`} onClick={() => set('enabled', !form.enabled)} />
      </div>
      <div className="sync-pop-body">
        {sched?.migration_required && (
          <div className="sync-pop-warn" role="alert"><TriangleAlert aria-hidden="true" />升级前的来源过滤配置已安全停用 · 选定协议并保存后再启用</div>
        )}
        {sched?.protocol_downgrade_blocked && (
          <div className="sync-pop-warn" role="alert"><TriangleAlert aria-hidden="true" />本机已是 v2 接收端,v1 定时不会运行 · 改选 v2 保存</div>
        )}
        <div className="sync-pop-row">
          <span className="sync-pop-k">频率</span>
          <div className="mini-seg" role="group" aria-label="频率">
            {FREQ_PRESETS.map(([id, label]) => (
              <button key={id} type="button" className={`mini-seg-btn ${form.freq === id ? 'is-on' : ''}`} aria-pressed={form.freq === id} onClick={() => set('freq', id)}>{label}</button>
            ))}
          </div>
        </div>
        {form.freq === 'daily' && (
          <div className="sync-pop-row">
            <span className="sync-pop-k">时间</span>
            <input className="form-input form-input-inline" type="time" style={{ width: 110, flex: 'none' }} value={form.dailyTime} onChange={(e) => set('dailyTime', e.target.value)} aria-label="每天的时间" />
          </div>
        )}
        {form.freq === 'custom' && (
          <div className="sync-pop-row">
            <span className="sync-pop-k">cron</span>
            <input className="form-input form-input-inline sync-mono" value={form.customCron} onChange={(e) => set('customCron', e.target.value)} placeholder="30 */4 * * *" aria-label="cron 表达式" />
          </div>
        )}
        <div className="sync-pop-row">
          <span className="sync-pop-k">协议</span>
          <div className="mini-seg" role="group" aria-label="协议">
            {[['v2', 'v2 全流'], ['v1', 'v1 兼容']].map(([id, label]) => (
              <button key={id} type="button" className={`mini-seg-btn ${form.protocol === id ? 'is-on' : ''}`} aria-pressed={form.protocol === id} onClick={() => set('protocol', id)}>{label}</button>
            ))}
          </div>
        </div>
        {!knownBase && (
          <div className="sync-pop-row">
            <span className="sync-pop-k">远端</span>
            <input className="form-input form-input-inline sync-mono" value={form.baseUrl} onChange={(e) => set('baseUrl', e.target.value)} placeholder="https://主机" aria-label="远端地址" />
          </div>
        )}
        {form.editCred ? (
          <>
            <div className="sync-pop-row">
              <span className="sync-pop-k">账号</span>
              <input className="form-input form-input-inline" autoComplete="off" value={form.username} onChange={(e) => set('username', e.target.value)} aria-label="远端账号" />
            </div>
            <div className="sync-pop-row">
              <span className="sync-pop-k">密码</span>
              <SecretField className="form-input form-input-inline" value={form.password} onChange={(v) => set('password', v)} isSet={Boolean(sched?.password_set)} aria-label="远端密码" />
            </div>
          </>
        ) : (
          <div className="sync-pop-row">
            <span className="sync-pop-k">账号</span>
            <span className="sync-peer-v">{form.username} · 密码已保存</span>
            <span className="flex-1" />
            <button type="button" className="kpi-sub-link" onClick={() => set('editCred', true)}>更换</button>
          </div>
        )}
      </div>
      {err && <p className="sync-pop-err" role="alert">{err}</p>}
      <div className="sync-pop-foot">
        <span className="sync-meta">{next ? `下次 ${fmtDay(next)}` : form.enabled ? '下次 —' : '未启用'}</span>
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>取消</button>
        <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled={saving} onClick={save}>
          {saving && <LoaderCircle className="sync-spin" />}保存
        </button>
      </div>
    </div>
  );
}

/* ── 测试连接:有已存凭据就直接测;否则先补密码。结果(含待同步,截至本次测试)就地显示 ── */
export function ProbeKv({ target, probe }) {
  if (!probe) return null;
  const caps = Array.isArray(probe.capabilities) ? probe.capabilities : [];
  const order = negotiatedOrder(caps) || [];
  const pend = probe.pending && typeof probe.pending === 'object' ? probe.pending : null;
  const pendTotal = pend ? Object.values(pend).reduce((a, b) => a + (Number(b) || 0), 0) : null;
  return (
    <dl className="ledger-kv">
      <dt>远端</dt><dd className="sync-mono" title={target}>{target || probe.base_url || '—'}</dd>
      <dt>版本</dt><dd>{withV(probe.version) || '未知'}</dd>
      <dt>协议</dt><dd>{probe.schema_version || '—'}</dd>
      {probe.authority_id && (<><dt>authority</dt><dd className="sync-mono" title={probe.authority_id}>{probe.authority_id}</dd></>)}
      {caps.length > 0 && (<><dt>能力</dt><dd title={caps.join('\n')}>{caps.length} 项 · {order.length} / {STREAM_ORDER.length} 流</dd></>)}
      {probe.taxonomy_version !== undefined && probe.taxonomy_version !== null && (
        <><dt>Taxonomy</dt><dd>{probe.taxonomy_ready === false ? '未发布' : `v${probe.taxonomy_version} 已发布`}</dd></>
      )}
      {probe.article_total !== undefined && probe.article_total !== null && (<><dt>文章</dt><dd>{fmt(probe.article_total)}</dd></>)}
      {probe.snapshot && (<><dt>修订</dt><dd className="sync-mono">{fmtSnapshot('', probe.snapshot)}</dd></>)}
      {pendTotal !== null && (
        <><dt>待同步</dt><dd title={Object.entries(pend).map(([k, v]) => `${k} ${v}`).join('\n')}>{fmt(pendTotal)} 条{probe.probed_at ? ` · 截至 ${fmtWhen(probe.probed_at)}` : ''}</dd></>
      )}
    </dl>
  );
}

function CredRows({ username, setUsername, password, setPassword, autoFocusPassword = true }) {
  return (
    <div className="sync-pop-body">
      <div className="sync-pop-row">
        <span className="sync-pop-k">账号</span>
        <input className="form-input form-input-inline" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} aria-label="远端账号" />
      </div>
      <div className="sync-pop-row">
        <span className="sync-pop-k">密码</span>
        <input className="form-input form-input-inline" type="password" autoComplete="new-password" autoFocus={autoFocusPassword} value={password} onChange={(e) => setPassword(e.target.value)} aria-label="远端密码" />
      </div>
    </div>
  );
}

const needsPassword = (e) => e?.status === 400 && /密码/.test(String(e?.message || ''));

export function ProbePop({ open, onClose, target, savedUser = '', hasSaved = false, lastProbe = null, onTest }) {
  const ref = usePopLayer(open, onClose);
  const [phase, setPhase] = useState('idle'); // idle | testing | ok | fail | cred
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const seq = useRef(0);

  const run = async (user, pass) => {
    const n = (seq.current += 1);
    setPhase('testing');
    setErr('');
    try {
      const r = await onTest(user, pass);
      if (n !== seq.current) return;
      setResult(r);
      setPhase('ok');
      setPassword('');
    } catch (e) {
      if (n !== seq.current) return;
      if (needsPassword(e) && !pass) { setPhase('cred'); return; }
      setErr(e.message || '远端连接测试失败');
      setPhase(pass ? 'cred' : 'fail');
    }
  };
  useEffect(() => {
    if (!open) { seq.current += 1; return; }
    setUsername(savedUser);
    setPassword('');
    setResult(null);
    setErr('');
    if (hasSaved) run(savedUser, '');
    else setPhase('cred');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在打开时测一次
  }, [open]);
  if (!open) return null;
  const shown = result || lastProbe;
  const stamp = phase === 'testing'
    ? <span className="stamp stamp-run">测试中…</span>
    : phase === 'ok' ? <span className="stamp stamp-ok">连接正常</span>
      : phase === 'fail' || (phase === 'cred' && err) ? <span className="stamp stamp-bad">连接失败</span>
        : lastProbe?.probed_at ? <span className="stamp stamp-idle">上次 {fmtWhen(lastProbe.probed_at)}</span> : null;
  return (
    <div ref={ref} className="ops-pop sync-pop is-wide" role="dialog" aria-label="测试连接">
      <div className="sync-pop-head"><span className="tools-title">测试连接</span>{stamp}</div>
      {phase === 'cred' && <CredRows username={username} setUsername={setUsername} password={password} setPassword={setPassword} />}
      {err && <p className="sync-pop-err" role="alert">{err}</p>}
      <ProbeKv target={target} probe={shown} />
      {(phase === 'cred' || phase === 'fail') && (
        <div className="sync-pop-foot">
          <span className="sync-meta" />
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>取消</button>
          <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={!username.trim() || (!password && !hasSaved)} onClick={() => run(username.trim(), password)}>测试连接</button>
        </div>
      )}
    </div>
  );
}

/* ── 立即同步要远端密码(没有已存凭据,或后端不接受空密码时) ── */
export function CredPop({ open, onClose, savedUser = '', message = '', onStart }) {
  const ref = usePopLayer(open, onClose);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (!open) return;
    setUsername(savedUser);
    setPassword('');
    setErr(message);
  }, [open, savedUser, message]);
  if (!open) return null;
  const go = async (e) => {
    e?.preventDefault();
    setBusy(true);
    setErr('');
    try {
      await onStart(username.trim(), password);
      onClose();
    } catch (ex) {
      setErr(ex.message || '启动同步失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <form ref={ref} className="ops-pop sync-pop" role="dialog" aria-label="立即同步" onSubmit={go}>
      <div className="sync-pop-head"><span className="tools-title">立即同步</span></div>
      <CredRows username={username} setUsername={setUsername} password={password} setPassword={setPassword} />
      {err && <p className="sync-pop-err" role="alert">{err}</p>}
      <div className="sync-pop-foot">
        <span className="sync-meta" />
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>取消</button>
        <button type="submit" className="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled={busy || !username.trim() || !password}>
          {busy && <LoaderCircle className="sync-spin" />}立即同步
        </button>
      </div>
    </form>
  );
}

/* ── 高危确认框:每个高危按钮都过这里 ── */
export function SyncConfirm({ open, onClose, title, kv = [], okText, onOk, busy = false }) {
  return (
    <Modal open={open} onClose={onClose} closeOnOverlay centered portal size="none" role="alertdialog" ariaLabel={title} panelClassName="form-sheet sync-confirm">
      <div className="form-sheet-head">
        <span className="card-title">{title}</span>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X className="h-4 w-4" /></button>
      </div>
      <div className="form-sheet-body">
        <dl className="ledger-kv">
          {kv.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
        </dl>
      </div>
      <div className="form-sheet-foot">
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose} autoFocus>取消</button>
        <button type="button" className="action-button action-button-danger min-h-[32px] px-3 text-xs" disabled={busy} onClick={onOk}>
          {busy && <LoaderCircle className="sync-spin" />}{okText}
        </button>
      </div>
    </Modal>
  );
}
