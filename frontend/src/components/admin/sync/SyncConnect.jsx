// 未配置态的「接入生产端」卡(样页 A-none):地址 / 账号 / 密码 / 协议 + 测试连接 + 开始首次同步;
// 测试结果一行 + 前置条件。密码只在本次请求里用,不落存储(定时凭据另在定时弹层)。
import { useEffect, useState } from 'react';
import { CircleAlert, CircleCheck, Download, LoaderCircle, PlugZap } from 'lucide-react';
import { REQUIRED_CAPABILITIES, fmt, shortId, withV } from '../../../utils/syncCatalog';

function Check({ ok, children }) {
  return <span className={ok ? '' : 'is-miss'}>{ok ? <CircleCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}{children}</span>;
}

export default function SyncConnect({ con, sched, onTest, onStart, starting = false }) {
  const [form, setForm] = useState({ baseUrl: '', username: '', password: '' });
  const [protocol, setProtocol] = useState('v2');
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState(null);
  const [err, setErr] = useState('');
  // 定时配置里的远端 / 账号预填(console.schedule;没有就用目标与最近账号)
  useEffect(() => {
    const baseUrl = sched?.base_url || con?.target || '';
    const username = sched?.username || con?.username || '';
    setForm((f) => (f.baseUrl || f.username ? f : { ...f, baseUrl, username }));
  }, [sched?.base_url, sched?.username, con?.target, con?.username]);
  const set = (k) => (e) => { setForm((f) => ({ ...f, [k]: e.target.value })); setProbe(null); setErr(''); };
  const ready = form.baseUrl.trim() && form.username.trim() && form.password;

  const test = async () => {
    setProbing(true); setErr(''); setProbe(null);
    try {
      setProbe(await onTest(form.baseUrl.trim(), form.username.trim(), form.password, protocol));
    } catch (e) {
      setErr(e.message || '远端连接测试失败');
    } finally {
      setProbing(false);
    }
  };
  const start = async () => {
    setErr('');
    try {
      await onStart(form.baseUrl.trim(), form.username.trim(), form.password, protocol);
    } catch (e) {
      setErr(e.message || '启动同步失败');
    }
  };

  const caps = Array.isArray(probe?.capabilities) ? probe.capabilities : [];
  const reqHit = REQUIRED_CAPABILITIES.filter((c) => caps.includes(c)).length;
  const stores = con?.local_stores || null;

  return (
    <section className="surface-card sync-connect" aria-label="接入生产端">
      <div className="sync-card-head"><span className="card-title">接入生产端</span></div>
      <div className="sync-connect-row">
        <label><span className="form-label">远端地址</span><input className="form-input" value={form.baseUrl} onChange={set('baseUrl')} placeholder="https://主机" aria-label="远端地址" /></label>
        <label><span className="form-label">远端管理员账号</span><input className="form-input" value={form.username} onChange={set('username')} autoComplete="off" aria-label="远端管理员账号" /></label>
        <label><span className="form-label">密码</span><input className="form-input" type="password" value={form.password} onChange={set('password')} autoComplete="new-password" aria-label="密码" /></label>
        <div>
          <span className="form-label">协议</span>
          <div className="mini-seg sync-proto" role="group" aria-label="协议">
            {[['v2', 'v2 全流'], ['v1', 'v1 兼容']].map(([id, label]) => (
              <button key={id} type="button" className={`mini-seg-btn ${protocol === id ? 'is-on' : ''}`} aria-pressed={protocol === id} onClick={() => { setProtocol(id); setProbe(null); }}>{label}</button>
            ))}
          </div>
        </div>
        <div className="sync-connect-acts">
          <button type="button" className="action-button action-button-secondary min-h-[36px] px-3 text-xs" disabled={!ready || probing || starting} onClick={test}>
            {probing ? <LoaderCircle className="sync-spin" /> : <PlugZap />}{probing ? '连接中…' : '测试连接'}
          </button>
          <button type="button" className="action-button action-button-primary min-h-[36px] px-3 text-xs" disabled={!ready || starting} onClick={start}>
            {starting ? <LoaderCircle className="sync-spin" /> : <Download />}{starting ? '启动中…' : '开始首次同步'}
          </button>
        </div>
      </div>
      {err && (
        <div className="sync-probe is-bad" role="alert">
          <span className="stamp stamp-bad">连接失败</span>
          <span className="sync-mono">{err}</span>
        </div>
      )}
      {probe && (
        <>
          <div className="sync-probe">
            <span className="stamp stamp-ok">连接正常</span>
            <span className="sync-probe-i">远端 <b>{withV(probe.version) || '未知'}</b></span>
            {probe.schema_version && <span className="sync-probe-i">协议 <b>{probe.schema_version}</b></span>}
            {probe.authority_id && <span className="sync-probe-i" title={probe.authority_id}>authority <b className="sync-mono">{shortId(probe.authority_id)}</b></span>}
            {caps.length > 0 && <span className="sync-probe-i" title={caps.join('\n')}>能力 <b>{caps.length} 项</b></span>}
            {probe.taxonomy_version ? <span className="sync-probe-i">Taxonomy <b>v{probe.taxonomy_version}</b></span> : null}
            {probe.article_total !== null && probe.article_total !== undefined && <span className="sync-probe-i">文章 <b>{fmt(probe.article_total)}</b></span>}
          </div>
          {protocol === 'v2' && (
            <div className="sync-checks">
              <Check ok={reqHit === REQUIRED_CAPABILITIES.length}>必需能力 {reqHit} / {REQUIRED_CAPABILITIES.length}</Check>
              <Check ok={probe.taxonomy_ready !== false}>远端 Taxonomy {probe.taxonomy_ready === false ? '未发布' : '已发布'}</Check>
              {stores && <Check ok={stores.media}>本机媒体库</Check>}
              {stores && <Check ok={stores.podcast}>本机播客产物库</Check>}
              <Check ok>首次整流全量</Check>
            </div>
          )}
        </>
      )}
    </section>
  );
}
