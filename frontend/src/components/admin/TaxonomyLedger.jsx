import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Ban, GitMerge, Loader2, RotateCcw, ShieldAlert, Trash2, X, Zap, ZapOff } from 'lucide-react';

import {
  activateCmsTagCandidate,
  addCmsTagAlias,
  createCmsTag,
  deleteCmsTagAlias,
  deleteCmsTagCandidate,
  deprecateCmsTag,
  fetchCmsTag,
  fetchCmsTagCandidate,
  fetchCmsTags,
  fetchTaxonomyLedger,
  mergeCmsTag,
  reclassifyCmsTagCandidate,
  rejectCmsTagCandidate,
  resolveCmsTagCandidate,
  retagCmsTag,
  updateCmsTag,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { useModalA11y } from '../../hooks/useModalA11y';
import { useModalTransition } from '../../hooks/useModalTransition';
import {
  ALIAS_TYPE_LABELS,
  ENTITY_TYPE_LABELS,
  LEDGER_STATUS_FILTERS,
  LEDGER_TYPE_FILTERS,
  TAG_KIND_FILTERS,
  TAG_KIND_LABELS,
  aliasTypeLabel,
  candidateStatusMeta,
  entityTypeLabel,
  tagDisplayName,
  tagKindLabel,
  tagStatusMeta,
} from '../../utils/taxonomyLabels';
import { TableFoot } from './Pager';
import StaleNotice from './StaleNotice';
import { ThFilter, ThSearch, ThSort } from './TableTh';

export const LEDGER_PAGE_SIZE = 20;
const pctText = (value) => `${Math.round(Number(value || 0) * 100)}%`;

// 同分面 active 标签(上位 / 合并 / 归并目标的候选池);抽屉打开时按需取,面板级缓存。
function useActiveTags() {
  const [tags, setTags] = useState(null);
  const load = useCallback(async () => {
    try { setTags((await fetchCmsTags({ status: 'active' })).items || []); }
    catch { setTags((prev) => prev ?? []); }
  }, []);
  return [tags, load];
}

function TagOptions({ tags, kind, excludeId }) {
  return (tags || [])
    .filter((item) => item.kind === kind && item.id !== excludeId)
    .map((item) => <option key={item.id} value={item.id}>{tagDisplayName(item)} · {item.code}</option>);
}

// ── 标签抽屉:名称 / 层级 / 别名 / 合并 / 治理原因;头部 rowacts 重标·废弃;脚部 可选开关 + 保存 ──
function TagDrawer({ tagId, row, onClose, onChanged, showToast, activeTags, confirm }) {
  const panelRef = useRef(null);
  const genRef = useRef(0);
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  const [form, setForm] = useState(null);
  const [reason, setReason] = useState('');
  const [alias, setAlias] = useState('');
  const [aliasType, setAliasType] = useState('synonym');
  const [mergeTarget, setMergeTarget] = useState('');
  const [busy, setBusy] = useState('');
  useModalA11y(Boolean(tagId), onClose, panelRef);

  const load = useCallback(async (id, { quiet = false } = {}) => {
    const gen = ++genRef.current;
    if (!quiet) setState({ status: 'loading', data: null, error: '' });
    try {
      const data = await fetchCmsTag(id);
      if (gen !== genRef.current) return;
      setState({ status: 'ok', data, error: '' });
      setForm({
        name_zh: data.name_zh || '', name_en: data.name_en || '', description: data.description || '',
        prompt_description: data.prompt_description || '', parent_id: data.parent_id ? String(data.parent_id) : '',
        entity_type: data.entity_type || '', external_key: data.external_key || '',
      });
    } catch (error) {
      if (gen === genRef.current) setState({ status: 'error', data: null, error: error.message });
    }
  }, []);
  useEffect(() => {
    if (!tagId) { genRef.current += 1; return; }
    setReason(''); setAlias(''); setMergeTarget('');
    load(tagId);
  }, [tagId, load]);

  const tag = state.data;
  const act = async (key, call, message, { reload = true } = {}) => {
    setBusy(key);
    try {
      await call();
      showToast(message, 'success');
      onChanged();
      if (reload && tagId) await load(tagId, { quiet: true });
      return true;
    } catch (error) {
      showToast(error.message, 'error');
      return false;
    } finally { setBusy(''); }
  };

  const nameChanged = tag && form && (form.name_zh !== (tag.name_zh || '') || form.name_en !== (tag.name_en || ''));
  const dirty = tag && form && (
    nameChanged
    || form.description !== (tag.description || '')
    || form.prompt_description !== (tag.prompt_description || '')
    || form.parent_id !== (tag.parent_id ? String(tag.parent_id) : '')
    || form.entity_type !== (tag.entity_type || '')
    || form.external_key !== (tag.external_key || '')
  );
  const save = async () => {
    if (!tag || !form) return;
    if (!form.name_zh.trim() && !form.name_en.trim()) { showToast('中文名与英文名至少填一个', 'error'); return; }
    if (nameChanged && !reason.trim()) { showToast('重命名需填写治理原因（旧规范名会保留为别名）', 'error'); return; }
    const payload = {
      name_zh: form.name_zh, name_en: form.name_en, description: form.description, prompt_description: form.prompt_description,
      parent_id: form.parent_id ? Number(form.parent_id) : null,
      reason: reason.trim() || '管理台编辑标签',
    };
    if (tag.kind === 'entity') {
      if (!form.entity_type) { showToast('实体标签需选择实体类型', 'error'); return; }
      payload.entity_type = form.entity_type;
      payload.external_key = form.external_key || null;
    }
    const ok = await act('save', () => updateCmsTag(tag.id, payload), `已保存标签 ${form.name_zh || form.name_en}`);
    if (ok) onClose();
  };
  const toggleSelectable = () => tag && act('selectable', () => updateCmsTag(tag.id, { user_selectable: !tag.user_selectable, reason: '管理台调整用户可选状态' }), `已${tag.user_selectable ? '关闭' : '开启'}用户可选`);
  const retag = () => tag && act('retag', () => retagCmsTag(tag.id, 7), '已创建近 7 天文章的闭集重标任务', { reload: false });
  const merge = () => {
    if (!tag) return;
    if (!mergeTarget) { showToast('请先选择合并目标', 'error'); return; }
    if (!reason.trim()) { showToast('合并需填写治理原因', 'error'); return; }
    const target = (activeTags || []).find((item) => String(item.id) === mergeTarget);
    confirm({
      title: `合并「${tagDisplayName(tag)}」`,
      message: `合并到「${tagDisplayName(target)}」后，本标签的文章指派与别名并入目标标签，本标签废弃；不可自动恢复，进审计记录。`,
      confirmText: '合并',
    }).then(async (yes) => {
      if (!yes) return;
      const ok = await act('merge', () => mergeCmsTag(tag.id, Number(mergeTarget), reason.trim()), `已合并标签 ${tagDisplayName(tag)}`, { reload: false });
      if (ok) onClose();
    });
  };
  const deprecate = () => {
    if (!tag) return;
    if (!reason.trim()) { showToast('废弃需填写治理原因', 'error'); return; }
    confirm({
      title: `废弃标签「${tagDisplayName(tag)}」`,
      message: `标签将停止用于新分析；已打标文章不变，规范名保留为别名解析入口${mergeTarget ? '，并指向所选替代标签' : ''}。不可自动恢复，进审计记录。`,
      confirmText: '废弃',
    }).then(async (yes) => {
      if (!yes) return;
      const ok = await act('deprecate', () => deprecateCmsTag(tag.id, mergeTarget ? Number(mergeTarget) : null, reason.trim()), `已废弃标签 ${tagDisplayName(tag)}`, { reload: false });
      if (ok) onClose();
    });
  };
  const addAlias = () => tag && alias.trim() && act('alias', async () => { await addCmsTagAlias(tag.id, { alias: alias.trim(), alias_type: aliasType, reason: reason.trim() }); setAlias(''); }, `已新增别名 ${alias.trim()}`);
  const removeAlias = (item) => {
    if (!reason.trim()) { showToast('删除别名需填写治理原因', 'error'); return; }
    act('alias', () => deleteCmsTagAlias(tag.id, item.id, reason.trim()), `已删除别名 ${item.alias}`);
  };

  const status = tag ? tagStatusMeta(tag.status) : null;
  const headMeta = tag ? [tag.code, tagKindLabel(tag.kind), row?.hits_7d != null ? `近 7 天 ${row.hits_7d} 篇 / ${row.sources_7d} 源` : ''].filter(Boolean).join(' · ') : '';
  const setField = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <aside ref={panelRef} className={`ledger-drawer ${tagId ? 'is-open' : ''}`} role="dialog" aria-modal="true" aria-label={tag ? `${tagDisplayName(tag)} · 标签详情` : '标签详情'} aria-hidden={!tagId} tabIndex={-1}>
      <div className="ledger-drawer-head">
        <div className="ledger-drawer-title">
          {tag ? tagDisplayName(tag) : (state.status === 'error' ? '标签详情' : '正在读取…')}
          {status && <span className={`stamp stamp-${status.tone} drawer-title-stamp`}>{status.label}</span>}
          {headMeta && <small className="acct-mono drawer-title-sub">{headMeta}</small>}
        </div>
        <div className="drawer-acts">
          {tag && tag.status === 'active' && (
            <>
              <button type="button" className="rowact-btn" title="重标近 7 天文章（用完整目录重新匹配）" aria-label="重标近 7 天文章" disabled={Boolean(busy)} onClick={retag}><RotateCcw /></button>
              <button type="button" className="rowact-btn is-danger" title="废弃（需治理原因；需确认）" aria-label="废弃标签" disabled={Boolean(busy)} onClick={deprecate}><ShieldAlert /></button>
            </>
          )}
          <span className="ai-divider" />
          <button type="button" className="rowact-btn" onClick={onClose} title="关闭" aria-label="关闭"><X /></button>
        </div>
      </div>
      <div className="ledger-drawer-body">
        {state.status === 'error' && <p className="tiny-meta" role="alert">{state.error} · <button type="button" className="kpi-sub-link" onClick={() => load(tagId)}>重试</button></p>}
        {state.status === 'loading' && <p className="tiny-meta" aria-busy="true"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />正在读取标签…</p>}
        {tag && form && (
          <>
            <section>
              <div className="drawer-sec-title">名称</div>
              <div className="knob-grid">
                <label className="knob-field"><span>中文名</span><input className="form-input" value={form.name_zh} onChange={setField('name_zh')} /></label>
                <label className="knob-field"><span>英文名</span><input className="form-input" value={form.name_en} onChange={setField('name_en')} /></label>
                <label className="knob-field knob-span-2"><span>后台说明</span><input className="form-input" value={form.description} onChange={setField('description')} /></label>
                <label className="knob-field knob-span-2"><span>模型判定说明（进提示词）</span><textarea className="form-input" value={form.prompt_description} onChange={setField('prompt_description')} /></label>
              </div>
            </section>
            <section>
              <div className="drawer-sec-title">层级</div>
              <div className="knob-grid">
                <label className="knob-field">
                  <span>上位标签</span>
                  <select className="form-input" value={form.parent_id} onChange={setField('parent_id')}>
                    <option value="">无上位标签</option>
                    <TagOptions tags={activeTags} kind={tag.kind} excludeId={tag.id} />
                  </select>
                </label>
                {tag.kind === 'entity' ? (
                  <>
                    <label className="knob-field">
                      <span>实体类型</span>
                      <select className="form-input" value={form.entity_type} onChange={setField('entity_type')}>
                        <option value="">请选择</option>
                        {Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                      </select>
                    </label>
                    <label className="knob-field knob-span-2"><span>外部键</span><input className="form-input" value={form.external_key} onChange={setField('external_key')} placeholder="wikidata:Q…" /></label>
                  </>
                ) : (
                  <label className="knob-field"><span>外部键</span><input className="form-input" value="" placeholder="实体专用" disabled readOnly /></label>
                )}
              </div>
            </section>
            <section>
              <div className="drawer-sec-title">别名 · {tag.aliases?.length ?? 0}</div>
              <div className="alias-chips">
                {(tag.aliases || []).map((item) => {
                  const canonical = item.alias_type === 'translation' && [tag.name_zh, tag.name_en].includes(item.alias);
                  return (
                    <span key={item.id} className="alias-chip">
                      {item.alias}<small>{aliasTypeLabel(item.alias_type)}{item.locale ? ` · ${item.locale}` : ''}</small>
                      <button type="button" className="alias-chip-x" disabled={Boolean(busy) || canonical} title={canonical ? '规范中英文名由重命名维护' : '删除别名（需治理原因）'} aria-label={`删除别名 ${item.alias}`} onClick={() => removeAlias(item)}>×</button>
                    </span>
                  );
                })}
                {!tag.aliases?.length && <span className="tiny-meta">—</span>}
              </div>
              <div className="alias-add">
                <input className="form-input form-input-inline" value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="新增别名" aria-label="新增别名" onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addAlias(); } }} />
                <select className="form-input form-input-inline" value={aliasType} onChange={(e) => setAliasType(e.target.value)} aria-label="别名类型">
                  {Object.entries(ALIAS_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
                <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" disabled={Boolean(busy) || !alias.trim()} onClick={addAlias}>添加</button>
              </div>
            </section>
            {tag.status === 'active' && (
              <section>
                <div className="drawer-sec-title">合并 / 替代</div>
                <div className="alias-add">
                  <select className="form-input form-input-inline" value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)} aria-label="合并或替代目标标签">
                    <option value="">选择同分面目标标签</option>
                    <TagOptions tags={activeTags} kind={tag.kind} excludeId={tag.id} />
                  </select>
                  <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" disabled={Boolean(busy) || !mergeTarget} onClick={merge} title="合并到目标标签（需治理原因；需确认）"><GitMerge className="h-3.5 w-3.5" /> 合并</button>
                </div>
              </section>
            )}
            <section>
              <div className="drawer-sec-title">治理原因</div>
              <input className="form-input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="重命名 / 别名 / 合并 / 废弃前填写，进审计记录" aria-label="治理原因" />
            </section>
          </>
        )}
      </div>
      <div className="ledger-drawer-foot">
        {tag && (
          <label className="sw">
            <span className="tiny-meta">用户可选</span>
            <button type="button" role="switch" aria-checked={Boolean(tag.user_selectable)} aria-label="用户可选" className={`ledger-switch ${tag.user_selectable ? 'is-on' : ''}`} disabled={Boolean(busy) || tag.status !== 'active'} onClick={toggleSelectable} />
          </label>
        )}
        <span className="flex-1" />
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>取消</button>
        <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled={!tag || Boolean(busy) || !dirty} onClick={save}>
          {busy === 'save' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} 保存标签
        </button>
      </div>
    </aside>
  );
}

// ── 候选抽屉:相似项 / 证据 / 激活表单 / 归并 / 审核原因;头部 rowacts 拒绝·删除;脚部 纠正分面·归并·激活 ──
function CandidateDrawer({ candidateId, row, onClose, onChanged, showToast, activeTags, confirm }) {
  const panelRef = useRef(null);
  const genRef = useRef(0);
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  const [form, setForm] = useState(null);
  const [resolveTarget, setResolveTarget] = useState('');
  const [busy, setBusy] = useState('');
  useModalA11y(Boolean(candidateId), onClose, panelRef);

  const load = useCallback(async (id) => {
    const gen = ++genRef.current;
    setState({ status: 'loading', data: null, error: '' });
    try {
      const data = await fetchCmsTagCandidate(id);
      if (gen !== genRef.current) return;
      setState({ status: 'ok', data, error: '' });
      setForm({ code: '', kind: data.proposed_kind, name_zh: data.label || '', name_en: '', user_selectable: true, entity_type: '', external_key: '', reason: '' });
      setResolveTarget('');
    } catch (error) {
      if (gen === genRef.current) setState({ status: 'error', data: null, error: error.message });
    }
  }, []);
  useEffect(() => {
    if (!candidateId) { genRef.current += 1; return; }
    load(candidateId);
  }, [candidateId, load]);

  const candidate = state.data;
  const act = async (key, call, message, { close = true } = {}) => {
    setBusy(key);
    try {
      await call();
      showToast(message, 'success');
      onChanged();
      if (close) onClose(); else await load(candidateId);
      return true;
    } catch (error) {
      showToast(error.message, 'error');
      return false;
    } finally { setBusy(''); }
  };
  const needReason = () => { if (!form?.reason.trim()) { showToast('请先填写审核原因', 'error'); return false; } return true; };
  const activate = () => {
    if (!candidate || !needReason()) return;
    if (form.kind === 'entity' && !form.entity_type) { showToast('实体标签需选择实体类型', 'error'); return; }
    act('activate', () => activateCmsTagCandidate(candidate.id, { ...form, code: form.code || null, external_key: form.external_key || null, reason: form.reason.trim() }), `已激活「${candidate.label}」为规范标签`);
  };
  const resolve = () => {
    if (!candidate || !needReason()) return;
    if (!resolveTarget) { showToast('请先选择归并目标', 'error'); return; }
    act('resolve', () => resolveCmsTagCandidate(candidate.id, Number(resolveTarget), form.reason.trim()), `已归并候选「${candidate.label}」`);
  };
  const reclassify = () => candidate && needReason() && act('reclassify', () => reclassifyCmsTagCandidate(candidate.id, form.kind, form.reason.trim()), `已将「${candidate.label}」纠正为${TAG_KIND_LABELS[form.kind]}`, { close: false });
  const reject = () => {
    if (!candidate || !needReason()) return;
    confirm({ title: `拒绝候选「${candidate.label}」`, message: '拒绝后该词不再进入审核队列；如需长期屏蔽相同词，拒绝比删除更稳。', confirmText: '拒绝' })
      .then((yes) => yes && act('reject', () => rejectCmsTagCandidate(candidate.id, form.reason.trim()), `已拒绝候选「${candidate.label}」`));
  };
  const remove = () => {
    if (!candidate || !needReason()) return;
    confirm({ title: `删除候选「${candidate.label}」`, message: `删除记录及其 ${candidate.evidence?.length || 0} 条可见证据；删除后相同词可能被再次发现，若要长期屏蔽请用「拒绝」。`, confirmText: '删除' })
      .then((yes) => yes && act('delete', () => deleteCmsTagCandidate(candidate.id, form.reason.trim()), `已删除候选「${candidate.label}」`));
  };

  const status = candidate ? candidateStatusMeta(candidate.status) : null;
  const canDelete = candidate && ['candidate', 'reviewing', 'rejected'].includes(candidate.status);
  const open = candidate && ['candidate', 'reviewing'].includes(candidate.status);
  const nearest = candidate?.nearest_tag_id ? (activeTags || []).find((tag) => tag.id === candidate.nearest_tag_id) : null;
  const headMeta = candidate ? [tagKindLabel(candidate.proposed_kind), `近 7 天 ${candidate.support_article_count_7d} 篇 / ${candidate.distinct_source_count_7d} 源 / ${candidate.distinct_day_count_7d} 天`, `置信 ${pctText(candidate.mean_confidence)}`].join(' · ') : '';
  const setField = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <aside ref={panelRef} className={`ledger-drawer ${candidateId ? 'is-open' : ''}`} role="dialog" aria-modal="true" aria-label={candidate ? `${candidate.label} · 候选详情` : '候选详情'} aria-hidden={!candidateId} tabIndex={-1}>
      <div className="ledger-drawer-head">
        <div className="ledger-drawer-title">
          {candidate ? candidate.label : (state.status === 'error' ? '候选详情' : '正在读取…')}
          {status && <span className={`stamp stamp-${status.tone} drawer-title-stamp`}>{status.label}</span>}
          {headMeta && <small className="acct-mono drawer-title-sub">{headMeta}</small>}
        </div>
        <div className="drawer-acts">
          {open && <button type="button" className="rowact-btn is-danger" title="拒绝（需审核原因；需确认）" aria-label="拒绝候选" disabled={Boolean(busy)} onClick={reject}><Ban /></button>}
          {canDelete && <button type="button" className="rowact-btn is-danger" title="删除记录（需审核原因；需确认）" aria-label="删除候选记录" disabled={Boolean(busy)} onClick={remove}><Trash2 /></button>}
          <span className="ai-divider" />
          <button type="button" className="rowact-btn" onClick={onClose} title="关闭" aria-label="关闭"><X /></button>
        </div>
      </div>
      <div className="ledger-drawer-body">
        {state.status === 'error' && <p className="tiny-meta" role="alert">{state.error} · <button type="button" className="kpi-sub-link" onClick={() => load(candidateId)}>重试</button></p>}
        {state.status === 'loading' && <p className="tiny-meta" aria-busy="true"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />正在读取候选…</p>}
        {candidate && form && (
          <>
            <section>
              <div className="drawer-sec-title">相似与风险</div>
              <dl className="ledger-kv">
                <dt>相似项</dt>
                <dd>{candidate.nearest_tag_id ? `${nearest ? tagDisplayName(nearest) : (row?.nearest_tag_name || `#${candidate.nearest_tag_id}`)} · ${pctText(candidate.nearest_similarity)}` : '未命中现有规范标签'}</dd>
                <dt>风险</dt>
                <dd>{candidate.risk_flags?.length ? candidate.risk_flags.join('、') : '—'}</dd>
              </dl>
            </section>
            <section>
              <div className="drawer-sec-title">最近证据 · {candidate.evidence?.length || 0}</div>
              {candidate.evidence?.length ? (
                <div className="drawer-stack">
                  {candidate.evidence.map((item) => (
                    <div key={`${item.article_id}-${item.source_id}`} className="drawer-evidence">
                      <span className="acct-mono">{item.source_owner_or_domain || item.source_id}</span>
                      <span className="tiny-meta"> · {item.published_date || '日期未知'} · 置信 {pctText(item.confidence)}</span>
                      {item.context_excerpt && <p>{item.context_excerpt}</p>}
                    </div>
                  ))}
                </div>
              ) : <span className="tiny-meta">—</span>}
            </section>
            {candidate.remote_evidence?.length > 0 && (
              <section>
                <div className="drawer-sec-title">远端证据 · {candidate.remote_evidence.length}</div>
                <div className="drawer-stack">
                  {candidate.remote_evidence.map((item, index) => (
                    <div key={`${item.authority_id}-${item.created_at}-${index}`} className="drawer-evidence">
                      <span className="acct-mono">{item.authority_id}</span>
                      <span className="tiny-meta"> · {item.source_provenance || '来源未标注'} · 置信 {pctText(item.confidence)}</span>
                      <p>{item.label}{item.prompt_version ? ` · ${item.prompt_version}` : ''}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {open && (
              <>
                <section>
                  <div className="drawer-sec-title">激活为规范标签</div>
                  <div className="knob-grid">
                    <label className="knob-field"><span>稳定 code</span><input className="form-input" value={form.code} onChange={setField('code')} placeholder="留空自动生成" /></label>
                    <label className="knob-field">
                      <span>分面</span>
                      <select className="form-input" value={form.kind} onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value, entity_type: '', external_key: '' }))}>
                        {Object.entries(TAG_KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                      </select>
                    </label>
                    <label className="knob-field"><span>中文名</span><input className="form-input" value={form.name_zh} onChange={setField('name_zh')} /></label>
                    <label className="knob-field"><span>英文名</span><input className="form-input" value={form.name_en} onChange={setField('name_en')} /></label>
                    {form.kind === 'entity' && (
                      <>
                        <label className="knob-field">
                          <span>实体类型</span>
                          <select className="form-input" value={form.entity_type} onChange={setField('entity_type')}>
                            <option value="">请选择</option>
                            {Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                          </select>
                        </label>
                        <label className="knob-field"><span>外部键</span><input className="form-input" value={form.external_key} onChange={setField('external_key')} placeholder="wikidata:Q…" /></label>
                      </>
                    )}
                  </div>
                  {form.kind !== candidate.proposed_kind && (
                    <div className="card-foot">
                      <span className="tiny-meta">分面已改为{TAG_KIND_LABELS[form.kind]}</span>
                      <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs ml-auto" disabled={Boolean(busy)} onClick={reclassify}>只纠正分面</button>
                    </div>
                  )}
                </section>
                <section>
                  <div className="drawer-sec-title">归并到已有标签</div>
                  <div className="alias-add">
                    <select className="form-input form-input-inline" value={resolveTarget} onChange={(e) => setResolveTarget(e.target.value)} aria-label="归并目标标签">
                      <option value="">选择同分面启用标签</option>
                      <TagOptions tags={activeTags} kind={form.kind} />
                    </select>
                    <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" disabled={Boolean(busy) || !resolveTarget} onClick={resolve}><GitMerge className="h-3.5 w-3.5" /> 归并</button>
                  </div>
                </section>
                <section>
                  <div className="drawer-sec-title">审核原因</div>
                  <input className="form-input" value={form.reason} onChange={setField('reason')} placeholder="激活 / 归并 / 拒绝 / 删除前填写，进审计记录" aria-label="审核原因" />
                </section>
              </>
            )}
          </>
        )}
      </div>
      <div className="ledger-drawer-foot">
        {open && form && (
          <label className="sw">
            <span className="tiny-meta">激活后用户可选</span>
            <button type="button" role="switch" aria-checked={form.user_selectable} aria-label="激活后用户可选" className={`ledger-switch ${form.user_selectable ? 'is-on' : ''}`} onClick={() => setForm((f) => ({ ...f, user_selectable: !f.user_selectable }))} />
          </label>
        )}
        <span className="flex-1" />
        <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>{open ? '取消' : '关闭'}</button>
        {open && (
          <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" disabled={Boolean(busy)} onClick={activate}>
            {busy === 'activate' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />} 激活为标签
          </button>
        )}
      </div>
    </aside>
  );
}

const EMPTY_TAG_FORM = Object.freeze({
  code: '', kind: 'topic', name_zh: '', name_en: '', description: '', prompt_description: '',
  user_selectable: true, entity_type: '', external_key: '', parent_id: '',
});

// ── 新建标签:form-sheet 模态(自 zone-head「新建标签」打开) ──
export function CreateTagSheet({ open, onClose, onCreated, showToast, activeTags }) {
  const { mounted, closing } = useModalTransition(open);
  const panelRef = useRef(null);
  const [form, setForm] = useState(EMPTY_TAG_FORM);
  const [busy, setBusy] = useState(false);
  useModalA11y(open && mounted, onClose, panelRef);
  useEffect(() => { if (open) setForm(EMPTY_TAG_FORM); }, [open]);
  if (!mounted) return null;
  const setField = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    if (!form.code.trim()) { showToast('请填写稳定 code', 'error'); return; }
    if (!form.name_zh.trim() && !form.name_en.trim()) { showToast('中文名与英文名至少填一个', 'error'); return; }
    if (form.kind === 'entity' && !form.entity_type) { showToast('实体标签需选择实体类型', 'error'); return; }
    setBusy(true);
    try {
      await createCmsTag({
        ...form,
        status: 'active',
        parent_id: form.parent_id ? Number(form.parent_id) : null,
        entity_type: form.kind === 'entity' ? form.entity_type : '',
        external_key: form.kind === 'entity' ? form.external_key : '',
      });
      showToast(`已创建标签 ${form.name_zh || form.name_en || form.code}`, 'success');
      onCreated();
      onClose();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setBusy(false); }
  };
  return createPortal(
    <div className={`modal-overlay ${closing ? 'is-closing' : ''}`} onClick={onClose}>
      <form ref={panelRef} role="dialog" aria-modal="true" aria-label="新建规范标签" tabIndex={-1} className="modal-panel max-w-2xl form-sheet" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="form-sheet-head">
          <h3 className="card-title">新建规范标签</h3>
          <button type="button" onClick={onClose} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
        </div>
        <div className="form-sheet-body">
          <div className="knob-grid">
            <label className="knob-field"><span>稳定 code</span><input required className="form-input" value={form.code} onChange={setField('code')} placeholder="topic.coding-agents" autoFocus /></label>
            <label className="knob-field">
              <span>分面</span>
              <select className="form-input" value={form.kind} onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value, entity_type: '', external_key: '', parent_id: '' }))}>
                {Object.entries(TAG_KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </label>
            <label className="knob-field"><span>中文名</span><input className="form-input" value={form.name_zh} onChange={setField('name_zh')} /></label>
            <label className="knob-field"><span>英文名</span><input className="form-input" value={form.name_en} onChange={setField('name_en')} /></label>
            <label className="knob-field">
              <span>上位标签</span>
              <select className="form-input" value={form.parent_id} onChange={setField('parent_id')}>
                <option value="">无上位标签</option>
                <TagOptions tags={activeTags} kind={form.kind} />
              </select>
            </label>
            {form.kind === 'entity' ? (
              <label className="knob-field">
                <span>实体类型</span>
                <select required className="form-input" value={form.entity_type} onChange={setField('entity_type')}>
                  <option value="">请选择</option>
                  {Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
              </label>
            ) : <span />}
            {form.kind === 'entity' && <label className="knob-field knob-span-2"><span>外部键</span><input className="form-input" value={form.external_key} onChange={setField('external_key')} placeholder="wikidata:Q24283660" /></label>}
            <label className="knob-field knob-span-2"><span>后台说明</span><input className="form-input" value={form.description} onChange={setField('description')} /></label>
            <label className="knob-field knob-span-2"><span>模型判定说明（进提示词）</span><textarea className="form-input" value={form.prompt_description} onChange={setField('prompt_description')} /></label>
          </div>
        </div>
        <div className="form-sheet-foot">
          <label className="sw mr-auto">
            <span className="tiny-meta">用户可选</span>
            <button type="button" role="switch" aria-checked={form.user_selectable} aria-label="用户可选" className={`ledger-switch ${form.user_selectable ? 'is-on' : ''}`} onClick={() => setForm((f) => ({ ...f, user_selectable: !f.user_selectable }))} />
          </label>
          <button type="button" onClick={onClose} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
          <button type="submit" disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} 创建标签
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/**
 * 规范标签 ∪ 候选 统一总账(issue #76 拍板②):一张 acct-table + 抽屉。列头即操作:类型 / 分面 /
 * 状态轮换筛选、名称就地搜索、近 7 天排序;整行任意位置点击开抽屉(标签 / 候选各一种),行内
 * 「可选」开关阻断冒泡。服务端分页 / 搜索 / 排序(/api/admin/taxonomy/ledger)。
 */
export default function TaxonomyLedger({ showToast, refreshTick = 0, onChanged, createOpen, onCreateClose }) {
  const confirm = useConfirm();
  const genRef = useRef(0);
  const [filters, setFilters] = useState({ type: '', kind: '', status: '', q: '', sort: 'hits', order: 'desc', page: 1 });
  const q = useDebouncedValue(filters.q, 300);
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  const [drawer, setDrawer] = useState(null); // { type, id, row }
  const [selectableBusy, setSelectableBusy] = useState(null);
  const [activeTags, loadActiveTags] = useActiveTags();
  const query = useMemo(() => ({
    type: filters.type, kind: filters.kind, status: filters.status, q: q.trim(), sort: filters.sort, order: filters.order,
    offset: (filters.page - 1) * LEDGER_PAGE_SIZE, limit: LEDGER_PAGE_SIZE,
  }), [filters.type, filters.kind, filters.status, q, filters.sort, filters.order, filters.page]);

  const load = useCallback(async (params, { quiet = false } = {}) => {
    const gen = ++genRef.current;
    if (!quiet) setState((prev) => ({ status: 'loading', data: prev.data, error: '' }));
    try {
      const data = await fetchTaxonomyLedger(params);
      if (gen === genRef.current) setState({ status: 'ok', data, error: '' });
    } catch (error) {
      if (gen === genRef.current) {
        setState((prev) => ({ status: error.status === 404 ? 'unavailable' : 'error', data: prev.data, error: error.message }));
      }
    }
  }, []);
  useEffect(() => { load(query); }, [load, query]);
  useEffect(() => {
    if (refreshTick > 0) { load(query, { quiet: true }); loadActiveTags(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应刷新脉冲
  }, [refreshTick]);
  useEffect(() => { if ((drawer || createOpen) && activeTags === null) loadActiveTags(); }, [drawer, createOpen, activeTags, loadActiveTags]);

  const patch = (next) => setFilters((prev) => ({ ...prev, ...next }));
  const handleSort = (k) => {
    if (filters.sort === k) patch({ order: filters.order === 'asc' ? 'desc' : 'asc', page: 1 });
    else patch({ sort: k, order: 'desc', page: 1 });
  };
  const changed = () => { load(query, { quiet: true }); loadActiveTags(); onChanged?.(); };

  const toggleSelectable = async (row) => {
    setSelectableBusy(row.id);
    try {
      await updateCmsTag(row.id, { user_selectable: !row.user_selectable, reason: '管理台调整用户可选状态' });
      showToast(`已${row.user_selectable ? '关闭' : '开启'}「${tagDisplayName(row)}」用户可选`, 'success');
      changed();
    } catch (error) {
      showToast(error.message, 'error');
    } finally { setSelectableBusy(null); }
  };

  const data = state.data;
  const items = data?.items ?? [];
  const counts = data?.counts ?? {};
  const filtersActive = Boolean(filters.type || filters.kind || filters.status || filters.q.trim());
  const openRow = (row) => setDrawer({ type: row.type, id: row.id, row });

  return (
    <>
      <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
        {(state.status === 'error' || state.status === 'unavailable') && !data ? (
          <p className="acct-empty"><StaleNotice status={state.status} error={state.status === 'unavailable' ? '当前后端版本没有该端点' : state.error} onRetry={state.status === 'error' ? () => load(query) : undefined} label="标签总账" /></p>
        ) : state.status === 'loading' && !data ? (
          <p className="acct-empty tiny-meta" aria-busy="true"><Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin" />正在读取标签总账…</p>
        ) : (
          <>
            {(state.status === 'error' || state.status === 'unavailable') && (
              <div className="tbl-head"><StaleNotice status={state.status} error={state.error} onRetry={() => load(query)} label="标签总账" /></div>
            )}
            <div className="acct-scroll">
              <table className="acct-table is-fixed">
                <thead>
                  <tr>
                    <ThFilter label="类型" value={filters.type} onChange={(type) => patch({ type, page: 1 })} options={LEDGER_TYPE_FILTERS} width={96} />
                    <ThSearch label="名称" value={filters.q} onChange={(value) => patch({ q: value, page: 1 })} placeholder="搜索名称 / code / 别名" active={Boolean(filters.q.trim())} width="34%" inputWidth={200} />
                    <ThFilter label="分面" value={filters.kind} onChange={(kind) => patch({ kind, page: 1 })} options={TAG_KIND_FILTERS} width={86} />
                    <ThFilter label="状态" value={filters.status} onChange={(status) => patch({ status, page: 1 })} options={LEDGER_STATUS_FILTERS} width={110} />
                    <ThSort label="近 7 天" k="hits" sort={filters.sort} order={filters.order} onSort={handleSort} num width={118} />
                    <th className="acct-th" style={{ width: 130 }}>别名 · 证据</th>
                    <th className="acct-th" style={{ width: 70 }}>可选</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="acct-empty tiny-meta">
                        {filtersActive ? (
                          <>没有匹配当前筛选的标签或候选。<button type="button" className="kpi-sub-link" onClick={() => patch({ type: '', kind: '', status: '', q: '', page: 1 })}>清除筛选</button></>
                        ) : '还没有规范标签，可以新建第一个'}
                      </td>
                    </tr>
                  ) : items.map((row) => {
                    const isTag = row.type === 'tag';
                    const status = isTag ? tagStatusMeta(row.status) : candidateStatusMeta(row.status);
                    const risk = !isTag && row.risk_flags?.length ? row.risk_flags.length : 0;
                    const sub = isTag
                      ? `${row.code}${row.entity_type ? ` · ${entityTypeLabel(row.entity_type).split(' / ')[0]}` : ''}`
                      : `${row.nearest_tag_name ? `相似 「${row.nearest_tag_name}」 ${pctText(row.nearest_similarity)}` : '未命中现有规范标签'} · 置信 ${pctText(row.mean_confidence)}`;
                    return (
                      <tr
                        key={`${row.type}-${row.id}`}
                        className={`acct-row ${drawer && drawer.type === row.type && drawer.id === row.id ? 'is-sel' : ''}`}
                        tabIndex={0}
                        onClick={() => openRow(row)}
                        onKeyDown={(e) => { if (e.key === 'Enter') openRow(row); }}
                      >
                        <td><span className="tag-kind">{isTag ? '标签' : '候选'}</span></td>
                        <td>
                          <span className="acct-name" title={tagDisplayName(row)}>{tagDisplayName(row)}</span>
                          <span className={`acct-sub ${isTag ? 'acct-mono' : ''}`} title={sub}>{sub}</span>
                        </td>
                        <td><span className="tiny-meta">{tagKindLabel(row.kind)}</span></td>
                        <td>
                          <span className={`stamp stamp-${status.tone}`} title={risk ? `风险：${row.risk_flags.join('、')}` : undefined}>
                            {status.label}{risk ? ` · ${risk} 项风险` : ''}
                          </span>
                        </td>
                        <td className={`acct-n ${Number(row.hits_7d) > 0 ? 'is-main' : 'is-zero'}`}>
                          {Number(row.hits_7d || 0).toLocaleString()}{Number(row.sources_7d) > 0 && <small className="tiny-meta"> · {row.sources_7d} 源</small>}
                        </td>
                        <td><span className="tiny-meta">{isTag ? `别名 ${row.alias_count ?? 0}` : `证据 ${row.evidence_count ?? 0}`}</span></td>
                        <td onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                          {isTag ? (
                            <button
                              type="button"
                              role="switch"
                              aria-checked={Boolean(row.user_selectable)}
                              className={`rowact-btn ${row.user_selectable ? 'is-on' : ''}`}
                              title={row.status !== 'active' ? '非启用标签不可选' : row.user_selectable ? '用户可选（点击关闭）' : '用户不可选（点击开启）'}
                              aria-label={`${tagDisplayName(row)} 用户可选`}
                              disabled={row.status !== 'active' || selectableBusy === row.id}
                              onClick={() => toggleSelectable(row)}
                            >
                              {/* 形状 + 色两通道(账户表 AI 列同法,codex R2-P1-1):开 Zap / 关 ZapOff,不靠颜色单独传达 */}
                              {selectableBusy === row.id ? <Loader2 className="animate-spin" /> : (row.user_selectable ? <Zap /> : <ZapOff />)}
                            </button>
                          ) : <span className="tiny-meta">—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <TableFoot
              total={data?.total ?? 0}
              page={filters.page}
              pageSize={LEDGER_PAGE_SIZE}
              onPage={(page) => patch({ page })}
              extra={data ? `候选 ${Number(counts.candidates ?? 0).toLocaleString()} · 规范标签 ${Number(counts.tags ?? 0).toLocaleString()}` : null}
            />
          </>
        )}
      </section>

      <div className={`ledger-scrim ${drawer ? 'is-open' : ''}`} onClick={() => setDrawer(null)} aria-hidden="true" />
      <TagDrawer
        tagId={drawer?.type === 'tag' ? drawer.id : null}
        row={drawer?.type === 'tag' ? drawer.row : null}
        onClose={() => setDrawer(null)}
        onChanged={changed}
        showToast={showToast}
        activeTags={activeTags}
        confirm={confirm}
      />
      <CandidateDrawer
        candidateId={drawer?.type === 'candidate' ? drawer.id : null}
        row={drawer?.type === 'candidate' ? drawer.row : null}
        onClose={() => setDrawer(null)}
        onChanged={changed}
        showToast={showToast}
        activeTags={activeTags}
        confirm={confirm}
      />
      <CreateTagSheet open={Boolean(createOpen)} onClose={onCreateClose} onCreated={changed} showToast={showToast} activeTags={activeTags} />
    </>
  );
}

