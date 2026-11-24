import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { decideReviewProposal, fetchCmsTags, fetchReviewProposal, fetchReviewProposals, groupReviewProposals } from '../../api';
import Modal from '../Modal';
import { formatStamp } from './adminUtils';

const STATUS = { pending: '待审核', deferred: '已暂缓', approved: '已批准待发布', merged: '已归并', rejected: '已拒绝', split: '已重新归组', resolved: '已处理' };
const KIND = { topic: '主题', industry: '行业', entity: '实体' };
const DELIVERY = { pending: '待发送', sending: '发送中', accepted: '飞书已接收', unknown: '接收状态未知', failed: '发送失败', cancelled: '已取消' };

export default function TaxonomyReviewPanel({ showToast, proposalId = null, refreshTick = 0 }) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('pending');
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState([]);
  const [editing, setEditing] = useState(null);
  const [action, setAction] = useState('approve');
  const [name, setName] = useState('');
  const [kind, setKind] = useState('topic');
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState('');
  const [splitIds, setSplitIds] = useState([]);
  const [tagQuery, setTagQuery] = useState('');
  const [tags, setTags] = useState([]);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const load = useCallback(async (nextCursor = 0) => {
    const gen = ++generation.current;
    setLoading(true); setError('');
    try {
      const data = proposalId ? { items: [await fetchReviewProposal(proposalId)] } : await fetchReviewProposals({ status, cursor: nextCursor, limit: 30 });
      if (gen !== generation.current) return;
      setItems((old) => nextCursor ? [...old, ...data.items] : data.items);
      setCursor(data.next_cursor || null);
      if (!nextCursor) setSelected([]);
    } catch (e) { if (gen === generation.current) setError(e.message); }
    finally { if (gen === generation.current) setLoading(false); }
  }, [proposalId, status]);
  useEffect(() => { load(); }, [load, refreshTick]);
  useEffect(() => {
    if (!editing || action !== 'merge') return undefined;
    let active = true;
    const timer = setTimeout(() => {
      fetchCmsTags({ status: 'active' }).then((data) => {
        if (!active) return;
        const query = tagQuery.normalize('NFKC').trim().toLocaleLowerCase();
        setTags((data.items || []).filter((tag) => String(tag.id) === target || !query
          || [tag.name_zh, tag.name_en, tag.code, ...(tag.aliases || []).map((alias) => alias.alias)]
            .some((value) => String(value || '').normalize('NFKC').toLocaleLowerCase().includes(query))));
      }).catch((e) => { if (active) setError(e.message); });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [editing, action, tagQuery, target]);

  const edit = (item, nextAction) => {
    setEditing(item); setAction(nextAction); setName(item.canonical_name); setKind(item.kind);
    setReason(''); setTarget(item.target_tag_id ? String(item.target_tag_id) : ''); setSplitIds([]); setTagQuery(''); setError('');
  };
  const close = () => { if (!busy) setEditing(null); };
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (action === 'group') {
        const chosen = items.filter((item) => selected.includes(item.id));
        await groupReviewProposals({ proposals: chosen.map((item) => ({ id: item.id, revision: item.revision })), expected_taxonomy_revision: chosen[0].current_taxonomy_revision, name, reason });
      } else {
        await decideReviewProposal(editing.id, { expected_revision: editing.revision, expected_taxonomy_revision: editing.current_taxonomy_revision,
          action, name, kind, reason, target_tag_id: target ? Number(target) : null, split_candidate_ids: splitIds, defer_days: 7 });
      }
      setEditing(null); showToast?.('已处理审核提案'); await load();
    } catch (e) {
      setError(e.message);
      if (e.status === 409) { setEditing(null); await load(); setError(e.message); }
    } finally { setBusy(false); }
  };
  const toggleSelection = (id) => setSelected((old) => old.includes(id) ? old.filter((value) => value !== id) : [...old, id]);

  return (
    <section className="grid gap-3" aria-label="新概念审核">
      <div className="card-head flex-wrap">
        <h2 className="card-title">新概念审核</h2>
        {!proposalId && <select className="form-input text-base min-h-11 w-auto" aria-label="审核状态" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="all">全部</option>{Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>}
        <span className="ml-auto flex gap-2">
          {selected.length > 1 && <button type="button" className="action-button min-h-11" onClick={() => edit(items.find((item) => selected.includes(item.id)), 'group')}>归为一组（{selected.length}）</button>}
          <button type="button" className="action-button min-h-11 action-button-quiet" onClick={() => load()} disabled={loading} aria-label="刷新审核清单">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </button>
        </span>
      </div>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {!loading && !items.length && !error && <p className="tiny-meta">清单为空，可切换状态查看已处理提案</p>}
      {items.map((item) => {
        const canDecide = ['pending', 'deferred'].includes(item.status) && !item.read_only;
        return <article className="surface-card card-pad rounded-[var(--r-card)] grid gap-3" key={item.id}>
          <div className="flex items-center flex-wrap gap-2">
            {canDecide && !proposalId && <input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggleSelection(item.id)} aria-label={`选择 ${item.canonical_name}`} />}
            <a className="card-title" href={`/review/taxonomy/${item.id}`}>{item.canonical_name}</a>
            <span className="stamp stamp-idle">{KIND[item.kind] || item.kind}</span>
            <span className="stamp stamp-idle">{STATUS[item.status] || item.status}</span>
            <span className="tiny-meta ml-auto">{item.evidence?.article_count || 0} 篇 · {item.evidence?.source_count || 0} 个来源</span>
          </div>
          {item.members?.length > 1 && <div className="flex flex-wrap gap-2">{item.members.map((member) => <span className="stamp stamp-idle" key={member.id}>{member.label}</span>)}</div>}
          {item.definition && <p className="text-sm">{item.definition}</p>}
          <div className="grid gap-1 text-sm"><strong>{item.recommendation}</strong><p>{item.reason}</p></div>
          {item.risks?.length > 0 && <ul className="list-disc pl-5 text-sm text-slate-500">{item.risks.map((risk, index) => <li key={index}>{risk}</li>)}</ul>}
          <ul className="grid gap-2 text-sm">{item.evidence?.articles?.map((article) => <li key={article.id}>{article.url ? <a className="kpi-sub-link" href={article.url} target="_blank" rel="noopener noreferrer">{article.title}</a> : <span>{article.title}</span>}</li>)}</ul>
          <div className="tiny-meta flex flex-wrap gap-3">
            <span>{formatStamp(item.updated_at)}</span>
            {item.notification_statuses?.map((value) => <span key={value}>{DELIVERY[value] || value}</span>)}
            {item.publication_status === 'draft' && <span>目录草稿 #{item.draft_tag_id}</span>}
            {item.retag_status !== 'not_queued' && <span>补标：{item.retag_status}</span>}
          </div>
          {item.decision_reason && <p className="text-sm">{item.decided_by}：{item.decision_reason}</p>}
          {canDecide && <div className="flex flex-wrap gap-2">
            <button type="button" className="action-button min-h-11" onClick={() => edit(item, 'approve')}>批准为目录草稿</button>
            <button type="button" className="action-button min-h-11 action-button-quiet" onClick={() => edit(item, 'merge')}>归并已有标签</button>
            <button type="button" className="action-button min-h-11 action-button-quiet" onClick={() => edit(item, 'defer')}>暂缓 7 天</button>
            <button type="button" className="action-button min-h-11 action-button-quiet" onClick={() => edit(item, 'reject')}>拒绝提案</button>
            {item.members?.length > 1 && <button type="button" className="action-button min-h-11 action-button-quiet" onClick={() => edit(item, 'split')}>拆分概念组</button>}
          </div>}
        </article>;
      })}
      {cursor && <button className="action-button min-h-11 action-button-quiet justify-self-center" type="button" disabled={loading} onClick={() => load(cursor)}>加载更多</button>}
      <Modal open={Boolean(editing)} onClose={close} closeOnOverlay ariaLabel="处理审核提案" size="lg" as="form" panelProps={{ onSubmit: submit }}>
        <div className="card-pad grid gap-4">
          <h3 className="card-title">{{ approve: '批准为目录草稿', merge: '归并已有标签', defer: '暂缓 7 天', reject: '拒绝提案', group: '归为一组', split: '拆分概念组' }[action]}</h3>
          {['approve', 'group'].includes(action) && <label className="grid gap-1 text-sm">规范名称<input className="form-input text-base min-h-11" value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} /></label>}
          {action === 'approve' && <><label className="grid gap-1 text-sm">类型<select className="form-input text-base min-h-11" value={kind} onChange={(event) => setKind(event.target.value)}>{Object.entries(KIND).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><p className="text-sm">批准后保存为草稿，需通过目录发布流程后生效</p></>}
          {action === 'merge' && <><input className="form-input text-base min-h-11" aria-label="搜索目标标签" placeholder="搜索正式名称或别名" value={tagQuery} onChange={(event) => setTagQuery(event.target.value)} /><select className="form-input text-base min-h-11" aria-label="归并目标" value={target} onChange={(event) => setTarget(event.target.value)} required><option value="">选择正式标签</option>{tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.name_zh || tag.name_en} · {KIND[tag.kind]}</option>)}</select></>}
          {action === 'split' && <div className="grid gap-2">{editing?.members?.map((member) => <label className="flex min-h-11 items-center gap-2 text-sm" key={member.id}><input type="checkbox" checked={splitIds.includes(member.id)} onChange={() => setSplitIds((old) => old.includes(member.id) ? old.filter((id) => id !== member.id) : [...old, member.id])} />{member.label}</label>)}</div>}
          <label className="grid gap-1 text-sm">处理理由<textarea className="form-input text-base min-h-11" value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={1000} rows={3} /></label>
          <div className="flex justify-end gap-2"><button type="button" className="action-button min-h-11 action-button-quiet" onClick={close} disabled={busy}>取消</button><button type="submit" className="action-button min-h-11" disabled={busy || !reason.trim()}>{busy ? '保存中…' : '提交处理'}</button></div>
        </div>
      </Modal>
    </section>
  );
}
