import { canFilterCustomInterests } from '../utils/readerInterestQuery';
import { useEffect, useRef, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { deleteCustomInterest, fetchInterests, previewCustomInterest, saveCustomInterest } from '../api';
import Modal from './Modal';
import { requestLogin } from '../utils/loginPrompt';
import { customInterestBody, customInterestForm } from '../utils/customInterestForm';
import './customInterests.css';

export default function CustomInterestPanel({ guest = false, onSaved, showToast, interestCapabilities = null, onReadCustom = null }) {
  const [items, setItems] = useState([]);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(customInterestForm);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const previewRequest = useRef(0);
  useEffect(() => {
    if (guest) return undefined;
    const abort = new AbortController();
    fetchInterests({ signal: abort.signal }).then((result) => setItems(result.custom_items || []))
      .catch((err) => { if (err.name !== 'AbortError') setError(err.message); });
    return () => { abort.abort(); previewRequest.current += 1; };
  }, [guest]);
  const requireAccount = () => {
    if (!guest) return true;
    requestLogin('interest');
    return false;
  };
  const edit = (item = null) => {
    if (!requireAccount()) return;
    previewRequest.current += 1;
    setEditing(item);
    setForm(customInterestForm(item));
    setError(''); setPreview(null); setPreviewBusy(false); setOpen(true);
  };
  const change = (patch) => {
    previewRequest.current += 1;
    setForm((prev) => ({ ...prev, ...patch })); setPreview(null); setPreviewBusy(false); setError('');
  };
  const close = () => {
    if (busy) return;
    previewRequest.current += 1;
    setOpen(false); setPreview(null); setPreviewBusy(false);
  };
  const body = () => customInterestBody(form, editing);
  const submit = async (event) => {
    event.preventDefault();
    if (!requireAccount()) return;
    previewRequest.current += 1;
    setPreview(null); setPreviewBusy(false);
    setBusy(true); setError('');
    try {
      const result = await saveCustomInterest(body(), editing?.id);
      setItems((prev) => [...prev.filter((item) => item.id !== result.item.id), result.item]);
      setOpen(false); onSaved?.({ onboardingCompleted: false }); showToast?.('已保存自定义兴趣', 'success');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const inspect = async () => {
    if (!requireAccount()) return;
    const request = ++previewRequest.current;
    setPreviewBusy(true); setPreview(null); setError('');
    try {
      const result = await previewCustomInterest(body());
      if (request === previewRequest.current) setPreview(result);
    } catch (err) {
      if (request === previewRequest.current) setError(err.message);
    } finally {
      if (request === previewRequest.current) setPreviewBusy(false);
    }
  };
  const semanticPreview = preview?.status === 'semantic_verification_required';
  const previewItems = (semanticPreview ? preview.lexical_candidates : preview?.items) || [];
  const remove = async () => {
    if (!requireAccount()) return;
    previewRequest.current += 1;
    setPreview(null); setPreviewBusy(false);
    setBusy(true); setError('');
    try {
      await deleteCustomInterest(editing.id, editing.revision);
      setItems((prev) => prev.filter((item) => item.id !== editing.id));
      setOpen(false); onSaved?.({ onboardingCompleted: false }); showToast?.('已移除自定义兴趣', 'success');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return (
    <section className="custom-interests" aria-label="自定义兴趣">
      <div className="brief-sec-head">
        <span className="brief-sec-title">自定义兴趣</span>
        <span className="brief-sec-count">{guest ? 0 : items.length}</span>
        <span className="brief-sec-rule" />
        <button type="button" className="interest-btn" onClick={() => edit()}><Plus aria-hidden="true" />添加</button>
      </div>
      <div className="custom-interest-list">
        {!guest && items.map((item) => (
          <div key={item.id} className="custom-interest-entry">
            <button type="button" className="interest-chip" onClick={() => edit(item)}>
              <span className="interest-chip-name">{item.name}</span>{item.status === 'paused' && <span>已暂停</span>}
            </button>
            <button type="button" className="interest-btn"
              disabled={item.status !== 'active' || !canFilterCustomInterests(interestCapabilities) || !onReadCustom}
              aria-label={`查看「${item.name}」的文章`}
              onClick={() => onReadCustom?.({ kind: 'custom', id: String(item.id) })}>查看文章</button>
          </div>
        ))}
      </div>
      {!open && error && <p role="alert">{error}</p>}
      <Modal open={open && !guest} onClose={close} closeOnOverlay portal size="lg"
        as="form" panelClassName="custom-interest-editor" ariaLabel={editing ? '编辑自定义兴趣' : '添加自定义兴趣'}
        panelProps={{ onSubmit: submit }}>
        <div className="brief-sec-head">
          <h2 className="brief-sec-title">{editing ? '编辑自定义兴趣' : '添加自定义兴趣'}</h2>
          <span className="brief-sec-rule" />
          <button type="button" className="icon-button" aria-label="关闭自定义兴趣" disabled={busy} onClick={close}><X /></button>
        </div>
        <label>显示名称<input className="form-input" required maxLength={80} disabled={busy} value={form.name}
          onChange={(e) => change({ name: e.target.value })} placeholder="企业内网 Agent" /></label>
        <label>匹配方式<select className="form-input" disabled={busy} value={form.mode} onChange={(e) => change({ mode: e.target.value })}>
          <option value="topic">话题含义</option><option value="rules">条件组合</option>
        </select></label>
        <label>{form.mode === 'topic' ? '感兴趣的话题范围' : '匹配说明（按含义）'}<textarea className="form-input" rows={3} maxLength={1000} disabled={busy}
          required={form.mode === 'topic' || form.negativeExamples.length > 0} value={form.requirement}
          onChange={(e) => change({ requirement: e.target.value })} placeholder="例如：关注企业内部部署的智能体，以及权限与系统集成实践" /></label>
        {form.mode !== 'topic' && <fieldset disabled={busy}>
          <legend>同时满足全部条件组</legend>
          {form.groups.map((group, index) => (
            <div className="custom-interest-group" key={index}>
              <label>条件组 {index + 1} · 任一词组
                <input className="form-input" required value={group} placeholder="内网，私有化，on-premise"
                  onChange={(e) => change({ groups: form.groups.map((g, i) => i === index ? e.target.value : g) })} />
              </label>
              {form.groups.length > 1 && <button type="button" className="icon-button" aria-label={`移除条件组 ${index + 1}`}
                onClick={() => change({ groups: form.groups.filter((_, i) => i !== index) })}><X /></button>}
            </div>
          ))}
          {form.groups.length < 6 && <button type="button" className="interest-btn" onClick={() => change({ groups: [...form.groups, ''] })}>添加条件组</button>}
        </fieldset>}
        <label>排除词组（逐字）<input className="form-input" disabled={busy} value={form.exclude} onChange={(e) => change({ exclude: e.target.value })} placeholder="多个词组用逗号分隔" /></label>
        <fieldset disabled={busy}>
          <legend>排除示例（按含义）</legend>
          {form.negativeExamples.map((example, index) => (
            <div className="custom-interest-group" key={index}>
              <label>示例 {index + 1}<textarea className="form-input" rows={2} required maxLength={500} value={example}
                onChange={(e) => change({ negativeExamples: form.negativeExamples.map((value, i) => i === index ? e.target.value : value) })}
                placeholder="例如：只介绍公共聊天产品，没有讨论企业内部部署" /></label>
              <button type="button" className="icon-button" aria-label={`移除排除示例 ${index + 1}`}
                onClick={() => change({ negativeExamples: form.negativeExamples.filter((_, i) => i !== index) })}><X /></button>
            </div>
          ))}
          {form.negativeExamples.length < 6 && <button type="button" className="interest-btn"
            onClick={() => change({ negativeExamples: [...form.negativeExamples, ''] })}>添加排除示例</button>}
        </fieldset>
        <label>状态<select className="form-input" disabled={busy} value={form.status} onChange={(e) => change({ status: e.target.value })}>
          <option value="active">启用</option><option value="paused">暂停</option>
        </select></label>
        {error && <p role="alert">{error}</p>}
        {preview && <div className="custom-interest-preview" aria-live="polite">
          <div className="brief-sec-head"><span className="brief-sec-title">{semanticPreview ? '待语义核验' : '近期匹配'}</span><span className="brief-sec-count">检查 {preview.scanned} 篇</span></div>
          {!semanticPreview && previewItems.length === 0 ? <p>暂无匹配内容</p> : previewItems.map((item) => (
            <div key={item.article_id}><span>{item.title}</span><strong>{semanticPreview ? (form.mode === 'topic' ? '待核验' : '词组候选') : item.outcome === 'core' ? '核心议题' : '仅提及'}</strong></div>
          ))}
        </div>}
        <div className="custom-interest-actions">
          {editing && <button type="button" className="interest-btn" disabled={busy} onClick={remove}>移除</button>}
          <button type="button" className="interest-btn" disabled={busy || previewBusy || !form.name.trim()} onClick={inspect}>{previewBusy ? '预览中…' : '预览'}</button>
          <button type="submit" className="interest-btn is-primary" disabled={busy}>{busy ? '处理中…' : '保存'}</button>
        </div>
      </Modal>
    </section>
  );
}
