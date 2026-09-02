import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  GitMerge,
  Loader2,
  Link2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react';
import {
  activateCmsTagCandidate,
  addCmsTagAlias,
  backfillCmsTagAliases,
  createCmsTag,
  deprecateCmsTag,
  deleteCmsTagCandidate,
  deleteCmsTagAlias,
  fetchAnalysisConfig,
  fetchAnalysisMetrics,
  fetchCmsTagCandidates,
  fetchCmsTags,
  fetchInterestCatalogPolicy,
  fetchTaxonomyState,
  mergeCmsTag,
  publishTaxonomyV1,
  reclassifyCmsTagCandidate,
  rejectCmsTagCandidate,
  retagCmsTag,
  resolveCmsTagCandidate,
  updateAnalysisConfig,
  updateCmsTag,
  updateInterestCatalogPolicy,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import FullAnalysisBackfillCard from './FullAnalysisBackfillCard';

const KIND_LABELS = { topic: '主题', industry: '行业', entity: '实体' };
const ENTITY_TYPE_LABELS = {
  organization: '组织 / 公司 / 实验室',
  product: '产品 / 服务',
  model: '模型 / 模型家族',
  protocol: '协议 / 标准',
  project: '开源项目 / 框架',
};
const FLAG_META = {
  article_analysis_enabled: ['文章分析', '创建并消费文章级分析任务'],
  taxonomy_candidate_enabled: ['Candidate 证据', '记录公共内容中的未知概念'],
  taxonomy_auto_activation_enabled: ['候选自动激活', '仅对满足组合阈值的低风险候选生效'],
  personal_digest_enabled: ['个人早报', '开放读者早报 API、调度与页面'],
  public_digest_analysis_adapter_enabled: ['公共日报 adapter', '让公共日报读取持久化分析，不影响历史快照'],
};

const pct = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;
const displayName = (tag) => tag?.name_zh || tag?.name_en || tag?.code || '—';
const CANDIDATE_PAGE_SIZE = 100;

function Metric({ value, label, sub }) {
  return <div className="kpi"><span className="kpi-num">{value}</span><span className="kpi-lbl">{label}</span>{sub && <span className="kpi-sub">{sub}</span>}</div>;
}

function FeatureFlags({ config, onToggle, busy }) {
  if (!config) return <p className="tiny-meta">分析开关尚未接入当前后端版本</p>;
  return (
    <div className="grid gap-2 lg:grid-cols-5">
      {Object.entries(FLAG_META).map(([key, [label, hint]]) => {
        const enabled = !!config[key];
        return (
          <button
            key={key}
            type="button"
            role="switch"
            aria-checked={enabled}
            disabled={busy === key}
            onClick={() => onToggle(key, !enabled)}
            className={`taxonomy-flag ${enabled ? 'is-on' : ''}`}
          >
            <span className="taxonomy-flag-head"><i aria-hidden="true" />{label}</span>
            <span>{hint}</span>
          </button>
        );
      })}
    </div>
  );
}

function InterestCatalogPolicyCard({ showToast }) {
  const [data, setData] = useState(null);
  const [limits, setLimits] = useState({ topic: 30, industry: 15, entity: 20 });
  const [busy, setBusy] = useState(false);
  const loadPolicy = useCallback(() => {
    fetchInterestCatalogPolicy().then((result) => {
      setData(result);
      setLimits(result.policy?.limits || { topic: 30, industry: 15, entity: 20 });
    }).catch((error) => showToast?.(error.message || '获取兴趣目录策略失败', 'error'));
  }, [showToast]);
  useEffect(loadPolicy, [loadPolicy]);
  const save = async () => {
    setBusy(true);
    try {
      const result = await updateInterestCatalogPolicy({ ...limits, reason: '管理台调整兴趣目录 Top N' });
      setData(result);
      setLimits(result.policy?.limits || limits);
      showToast?.('已更新兴趣目录展示策略', 'success');
    } catch (error) { showToast?.(error.message || '更新兴趣目录策略失败', 'error'); }
    finally { setBusy(false); }
  };
  return (
    <section className="surface-card card-pad rounded-[var(--r-card)]">
      <div className="card-head">
        <div><h2 className="card-title">用户兴趣目录</h2><p className="tiny-meta mt-1">首版接受的主题、行业、实体均默认可选，再按近 {data?.policy?.window_days || 30} 天文章覆盖量展示各分面 Top N；管理员可单独下架，已选标签跌出 Top N 仍会保留</p></div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {Object.entries(KIND_LABELS).map(([key, label]) => {
          const stats = data?.facet_stats?.[key];
          return <label key={key}><span className="form-label">{label} Top N</span><input type="number" min="0" max="200" className="form-input" value={limits[key]} onChange={(e) => setLimits({ ...limits, [key]: Number(e.target.value) })} /><small className="tiny-meta">当前可展示 {stats?.top_n_count ?? '—'} / 具备资格 {stats?.eligible_count ?? '—'}</small></label>;
        })}
      </div>
      <div className="mt-3 flex justify-end"><button type="button" disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={save}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 保存展示策略</button></div>
    </section>
  );
}

function CreateTagForm({ tags, onCreated, showToast }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const emptyForm = { code: '', kind: 'topic', name_zh: '', name_en: '', description: '', prompt_description: '', status: 'active', user_selectable: true, entity_type: '', external_key: '', parent_id: null };
  const [form, setForm] = useState(emptyForm);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    try {
      await createCmsTag(form);
      showToast?.(`已创建标签 ${form.name_zh || form.code}`, 'success');
      setOpen(false);
      setForm(emptyForm);
      onCreated();
    } catch (error) {
      showToast?.(error.message || '创建标签失败', 'error');
    } finally { setBusy(false); }
  };
  if (!open) return <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /> 新建标签</button>;
  return (
    <form className="surface-card card-pad w-full rounded-[var(--r-card)]" onSubmit={submit}>
      <div className="card-head"><span className="card-title">新建规范标签</span><button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="取消新建"><X className="h-4 w-4" /></button></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label><span className="form-label">稳定 code</span><input required className="form-input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="topic.coding-agents" /></label>
        <label><span className="form-label">分面</span><select className="form-input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, entity_type: '', external_key: '', parent_id: null })}>{Object.entries(KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label><span className="form-label">中文名</span><input className="form-input" value={form.name_zh} onChange={(e) => setForm({ ...form, name_zh: e.target.value })} /></label>
        <label><span className="form-label">英文名</span><input className="form-input" value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} /></label>
        <label><span className="form-label">上位标签</span><select className="form-input" value={form.parent_id || ''} onChange={(e) => setForm({ ...form, parent_id: e.target.value ? Number(e.target.value) : null })}><option value="">无上位标签</option>{tags.filter((item) => item.kind === form.kind && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{displayName(item)} · {item.code}</option>)}</select></label>
        {form.kind === 'entity' && <label><span className="form-label">Entity 类型</span><select required className="form-input" value={form.entity_type} onChange={(e) => setForm({ ...form, entity_type: e.target.value })}><option value="">请选择</option>{Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
        {form.kind === 'entity' && <label><span className="form-label">稳定 external key（可选）</span><input className="form-input" value={form.external_key} onChange={(e) => setForm({ ...form, external_key: e.target.value })} placeholder="例如 wikidata:Q24283660" /></label>}
        <label className="sm:col-span-2"><span className="form-label">后台说明</span><input className="form-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="供管理员理解概念范围" /></label>
        <label className="sm:col-span-2"><span className="form-label">模型判定说明</span><input className="form-input" value={form.prompt_description} onChange={(e) => setForm({ ...form, prompt_description: e.target.value })} placeholder="说明何时应该或不应该打此标签" /></label>
        <label className="flex items-center gap-2 pt-6 tiny-meta"><input type="checkbox" checked={form.user_selectable} onChange={(e) => setForm({ ...form, user_selectable: e.target.checked })} />允许用户选择</label>
      </div>
      <div className="mt-3 flex justify-end"><button disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 创建标签</button></div>
    </form>
  );
}

function TagRow({ tag, tags, onChanged, showToast }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState('');
  const [parent, setParent] = useState(tag.parent_id ? String(tag.parent_id) : '');
  const [alias, setAlias] = useState('');
  const [aliasType, setAliasType] = useState('synonym');
  const [entityType, setEntityType] = useState(tag.entity_type || '');
  const [externalKey, setExternalKey] = useState(tag.external_key || '');
  const [nameZh, setNameZh] = useState(tag.name_zh || '');
  const [nameEn, setNameEn] = useState(tag.name_en || '');
  const [description, setDescription] = useState(tag.description || '');
  const [promptDescription, setPromptDescription] = useState(tag.prompt_description || '');
  const act = async (call, message) => {
    setBusy(true);
    try { await call(); showToast?.(message, 'success'); onChanged(); }
    catch (error) { showToast?.(error.message || '更新标签失败', 'error'); }
    finally { setBusy(false); }
  };
  return (
    <div className="taxonomy-row">
      <button type="button" className="taxonomy-row-main" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="brief-tag">{KIND_LABELS[tag.kind] || tag.kind}</span>
        <span className="min-w-0 flex-1"><strong>{displayName(tag)}</strong><small>{tag.code}{tag.aliases?.length ? ` · ${tag.aliases.length} 个 Alias` : ''}</small></span>
        <span className={`stamp ${tag.status === 'active' ? 'stamp-ok' : tag.status === 'deprecated' ? 'stamp-warn' : 'stamp-idle'}`}>{tag.status}</span>
        {tag.user_selectable && <span className="brief-tag">用户可选</span>}
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="taxonomy-row-detail">
          <p className="tiny-meta">稳定 code：{tag.code} · code 与分面创建后不可修改；重命名会把旧规范名保留为 Alias。</p>
          <label><span className="form-label">治理原因</span><input className="form-input form-input-inline" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="重命名、删除 Alias、合并或废弃前填写" aria-label="治理原因" /></label>
          <div className="grid gap-2 sm:grid-cols-2">
            <label><span className="form-label">中文名</span><input className="form-input form-input-inline" value={nameZh} onChange={(e) => setNameZh(e.target.value)} /></label>
            <label><span className="form-label">英文名</span><input className="form-input form-input-inline" value={nameEn} onChange={(e) => setNameEn(e.target.value)} /></label>
            <label><span className="form-label">后台说明</span><textarea className="form-input min-h-20" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="供管理员理解概念范围" /></label>
            <label><span className="form-label">模型判定说明</span><textarea className="form-input min-h-20" value={promptDescription} onChange={(e) => setPromptDescription(e.target.value)} placeholder="会进入文章分析提示词；说明何时应该或不应该打此标签" /></label>
          </div>
          <div className="flex justify-end">
            <button type="button" disabled={busy || !reason.trim() || (!nameZh.trim() && !nameEn.trim()) || (nameZh === (tag.name_zh || '') && nameEn === (tag.name_en || '') && description === (tag.description || '') && promptDescription === (tag.prompt_description || ''))} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => updateCmsTag(tag.id, { name_zh: nameZh, name_en: nameEn, description, prompt_description: promptDescription, reason: reason.trim() }), `已更新 ${nameZh || nameEn || tag.code} 的规范信息`)}>保存规范信息</button>
          </div>
          {tag.kind === 'entity' && (
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <select className="form-input form-input-inline" value={entityType} onChange={(e) => setEntityType(e.target.value)} aria-label="Entity 类型"><option value="">请选择 Entity 类型</option>{Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <input className="form-input form-input-inline" value={externalKey} onChange={(e) => setExternalKey(e.target.value)} placeholder="稳定 external key（可选）" aria-label="Entity external key" />
              <button type="button" disabled={busy || !entityType || (entityType === (tag.entity_type || '') && externalKey === (tag.external_key || ''))} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => updateCmsTag(tag.id, { entity_type: entityType, external_key: externalKey || null, reason: reason.trim() || '管理台确认 Entity 类型' }), `已更新 ${displayName(tag)} 的 Entity 类型`)}>保存 Entity 类型</button>
            </div>
          )}
          <div>
            <h4 className="micro-label mb-2">Alias</h4>
            <div className="flex flex-wrap gap-2">
              {(tag.aliases || []).map((item) => {
                const canonicalTranslation = item.alias_type === 'translation' && [tag.name_zh, tag.name_en].includes(item.alias);
                return (
                  <span key={item.id} className="taxonomy-alias-chip">
                    <span>{item.alias}</span><small>{item.alias_type}{item.locale ? ` · ${item.locale}` : ''}</small>
                    <button type="button" disabled={busy || canonicalTranslation || !reason.trim()} title={canonicalTranslation ? '规范中英文名由重命名维护' : !reason.trim() ? '请先填写治理原因' : '删除 Alias'} aria-label={`删除 Alias ${item.alias}`} onClick={() => act(() => deleteCmsTagAlias(tag.id, item.id, reason.trim()), `已删除 Alias ${item.alias}`)}><Trash2 className="h-3 w-3" /></button>
                  </span>
                );
              })}
              {!tag.aliases?.length && <span className="tiny-meta">还没有额外 Alias，规范中英文名仍作为解析入口。</span>}
            </div>
            <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_150px_auto]">
              <input className="form-input form-input-inline" value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="新增等价词、缩写或翻译" aria-label="新增 Alias" />
              <select className="form-input form-input-inline" value={aliasType} onChange={(e) => setAliasType(e.target.value)} aria-label="Alias 类型"><option value="synonym">同义词</option><option value="abbreviation">缩写</option><option value="translation">翻译</option><option value="former_name">旧称</option><option value="misspelling">常见误写</option></select>
              <button type="button" disabled={busy || !alias.trim()} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(async () => { await addCmsTagAlias(tag.id, { alias: alias.trim(), alias_type: aliasType, reason: reason.trim() }); setAlias(''); }, `已新增 Alias ${alias.trim()}`)}><Link2 className="h-3.5 w-3.5" /> 新增 Alias</button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" role="switch" aria-checked={!!tag.user_selectable} className={`action-button action-button-quiet min-h-[32px] px-3 text-xs ${tag.user_selectable ? 'is-on' : ''}`} disabled={busy} onClick={() => act(() => updateCmsTag(tag.id, { user_selectable: !tag.user_selectable, reason: '管理台调整用户可选状态' }), `已${tag.user_selectable ? '关闭' : '开启'}用户可选`)}>
              <Check className="h-3.5 w-3.5" /> 用户可选
            </button>
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" disabled={busy} onClick={() => act(() => retagCmsTag(tag.id, 7), '已创建最近 7 天重标任务')}><RotateCcw className="h-3.5 w-3.5" /> 重标 7 天</button>
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <select className="form-input form-input-inline" value={parent} onChange={(e) => setParent(e.target.value)} aria-label="上位标签"><option value="">无上位标签</option>{tags.filter((item) => item.id !== tag.id && item.kind === tag.kind && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{displayName(item)} · {item.code}</option>)}</select>
            <button type="button" disabled={busy || (parent || '') === (tag.parent_id ? String(tag.parent_id) : '')} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => updateCmsTag(tag.id, { parent_id: parent ? Number(parent) : null, reason: reason.trim() || '管理台调整上位标签' }), `已更新 ${displayName(tag)} 的上位标签`)}>更新层级</button>
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <select className="form-input form-input-inline" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="替代或合并目标标签"><option value="">选择同分面目标标签</option>{tags.filter((item) => item.id !== tag.id && item.kind === tag.kind && item.status === 'active').map((item) => <option key={item.id} value={item.id}>{displayName(item)} · {item.code}</option>)}</select>
            <span className="flex gap-2">
              <button type="button" disabled={busy || !target || !reason.trim()} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => mergeCmsTag(tag.id, Number(target), reason.trim()), `已合并标签 ${displayName(tag)}`)}><GitMerge className="h-3.5 w-3.5" /> 合并</button>
              <button type="button" disabled={busy || !reason.trim()} className="action-button action-button-danger min-h-[32px] px-3 text-xs" onClick={() => act(() => deprecateCmsTag(tag.id, target ? Number(target) : null, reason.trim()), `已废弃标签 ${displayName(tag)}`)}><ShieldAlert className="h-3.5 w-3.5" /> 废弃</button>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function CandidateRow({ candidate, tags, onChanged, showToast, confirm }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ code: '', kind: candidate.proposed_kind, name_zh: candidate.label || '', name_en: '', user_selectable: true, entity_type: '', external_key: '', reason: '' });
  const [resolveTarget, setResolveTarget] = useState('');
  const nearest = tags.find((tag) => tag.id === candidate.nearest_tag_id);
  const canDelete = ['candidate', 'reviewing', 'rejected'].includes(candidate.status);
  const act = async (call, message) => {
    setBusy(true);
    try { await call(); showToast?.(message, 'success'); onChanged(); }
    catch (error) { showToast?.(error.message || '处理 Candidate 失败', 'error'); }
    finally { setBusy(false); }
  };
  return (
    <div className="taxonomy-row">
      <button type="button" className="taxonomy-row-main" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="brief-tag">{KIND_LABELS[candidate.proposed_kind] || candidate.proposed_kind}</span>
        <span className="min-w-0 flex-1"><strong>{candidate.label}</strong><small>7 天 {candidate.support_article_count_7d} 篇 · {candidate.distinct_source_count_7d} 源 · {candidate.distinct_day_count_7d} 天 · 置信 {(Number(candidate.mean_confidence || 0) * 100).toFixed(0)}%</small></span>
        {candidate.risk_flags?.length > 0 && <span className="stamp stamp-warn">{candidate.risk_flags.length} 项风险</span>}
        <span className="stamp stamp-idle">{candidate.status}</span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="taxonomy-row-detail">
          <div className="rounded-[var(--r-card)] bg-[var(--dorami-soft)] p-3 tiny-meta">
            <strong>相似项：</strong>{nearest ? `${displayName(nearest)} · ${Math.round(Number(candidate.nearest_similarity || 0) * 100)}%` : '未命中现有规范标签'}
            {candidate.risk_flags?.length > 0 && <span> · 风险：{candidate.risk_flags.join('、')}</span>}
          </div>
          <div>
            <h4 className="micro-label mb-2">最近证据</h4>
            {candidate.evidence?.length ? <div className="grid gap-2">{candidate.evidence.map((row) => (
              <div key={`${row.article_id}-${row.source_id}`} className="rounded-[var(--r-control)] border border-[var(--dorami-border)] p-2 tiny-meta">
                <span className="font-mono">{row.source_owner_or_domain || row.source_id}</span> · {row.published_date || '日期未知'} · 置信 {Math.round(Number(row.confidence || 0) * 100)}%
                {row.context_excerpt && <p className="mt-1 body-text">{row.context_excerpt}</p>}
              </div>
            ))}</div> : <p className="tiny-meta">还没有可展示的证据样本</p>}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <input className="form-input form-input-inline" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="规范 code（可自动生成）" />
            <select className="form-input form-input-inline" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, entity_type: '', external_key: '' })} aria-label="纠正 Candidate 分面">{Object.entries(KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            <input className="form-input form-input-inline" value={form.name_zh} onChange={(e) => setForm({ ...form, name_zh: e.target.value })} placeholder="中文名" />
            <input className="form-input form-input-inline" value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} placeholder="英文名" />
            <input className="form-input form-input-inline lg:col-span-4" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="审核原因" />
            {form.kind === 'entity' && <select className="form-input form-input-inline lg:col-span-2" value={form.entity_type} onChange={(e) => setForm({ ...form, entity_type: e.target.value })} aria-label="Candidate Entity 类型"><option value="">请选择 Entity 类型</option>{Object.entries(ENTITY_TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>}
            {form.kind === 'entity' && <input className="form-input form-input-inline lg:col-span-2" value={form.external_key} onChange={(e) => setForm({ ...form, external_key: e.target.value })} placeholder="稳定 external key（可选）" />}
          </div>
          <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
            <select className="form-input form-input-inline" value={resolveTarget} onChange={(e) => setResolveTarget(e.target.value)} aria-label="归并到已有标签"><option value="">选择同分面 active 标签</option>{tags.filter((tag) => tag.status === 'active' && tag.kind === form.kind).map((tag) => <option key={tag.id} value={tag.id}>{KIND_LABELS[tag.kind]} · {displayName(tag)} · {tag.code}</option>)}</select>
            <button type="button" disabled={busy || form.kind === candidate.proposed_kind || !form.reason.trim()} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => reclassifyCmsTagCandidate(candidate.id, form.kind, form.reason.trim()), `已将 ${candidate.label} 纠正为${KIND_LABELS[form.kind]}`)}>纠正分面</button>
            <button type="button" disabled={busy || !resolveTarget || !form.reason.trim()} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={() => act(() => resolveCmsTagCandidate(candidate.id, Number(resolveTarget), form.reason.trim()), `已归并 Candidate ${candidate.label}`)}><GitMerge className="h-3.5 w-3.5" /> 归并到标签</button>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <label className="mr-auto flex items-center gap-2 tiny-meta"><input type="checkbox" checked={form.user_selectable} onChange={(e) => setForm({ ...form, user_selectable: e.target.checked })} />同时开放用户选择</label>
            <button type="button" disabled={busy || !form.reason.trim()} className="action-button action-button-danger min-h-[32px] px-3 text-xs" onClick={() => act(() => rejectCmsTagCandidate(candidate.id, form.reason.trim()), `已拒绝 Candidate ${candidate.label}`)}><X className="h-3.5 w-3.5" /> 拒绝</button>
            {canDelete && <button type="button" disabled={busy || !form.reason.trim()} className="action-button action-button-danger min-h-[32px] px-3 text-xs" onClick={async () => {
              if (!(await confirm(`删除 Candidate「${candidate.label}」及其 ${candidate.evidence?.length || 0} 条可见证据？删除后相同词可能被再次发现；若要长期屏蔽请使用“拒绝”。`))) return;
              await act(() => deleteCmsTagCandidate(candidate.id, form.reason.trim()), `已删除 Candidate ${candidate.label}`);
            }}><Trash2 className="h-3.5 w-3.5" /> 删除记录</button>}
            <button type="button" disabled={busy || !form.reason.trim() || (form.kind === 'entity' && !form.entity_type)} className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={() => act(() => activateCmsTagCandidate(candidate.id, { ...form, code: form.code || null, external_key: form.external_key || null }), `已激活 Candidate ${candidate.label}`)}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 激活为标签</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AdminTaxonomyPanel({ showToast, days = 7 }) {
  const confirm = useConfirm();
  const [view, setView] = useState('candidates');
  const [tags, setTags] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [candidatePage, setCandidatePage] = useState(0);
  const [config, setConfig] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [taxonomyState, setTaxonomyState] = useState(null);
  const [flagBusy, setFlagBusy] = useState('');
  const [publishBusy, setPublishBusy] = useState(false);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [candidateStatus, setCandidateStatus] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const [tagResult, candidateResult, stateResult] = await Promise.allSettled([
      fetchCmsTags(),
      fetchCmsTagCandidates({ limit: CANDIDATE_PAGE_SIZE, offset: candidatePage * CANDIDATE_PAGE_SIZE, q: query, kind, status: candidateStatus }),
      fetchTaxonomyState(),
    ]);
    if (tagResult.status === 'fulfilled') setTags(tagResult.value.items || []);
    else showToast?.(tagResult.reason?.message || '获取 CMS 标签失败', 'error');
    if (candidateResult.status === 'fulfilled') {
      setCandidates(candidateResult.value.items || []);
      setCandidateTotal(candidateResult.value.total || 0);
    }
    else showToast?.(candidateResult.reason?.message || '获取 Candidate 失败', 'error');
    if (stateResult.status === 'fulfilled') setTaxonomyState(stateResult.value);
    else setTaxonomyState(null);
    setLoading(false);
  }, [candidatePage, candidateStatus, kind, query, showToast]);

  useEffect(() => {
    const timer = window.setTimeout(load, 180);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => { setCandidatePage(0); }, [candidateStatus, kind, query]);
  useEffect(() => {
    fetchAnalysisConfig().then((data) => setConfig(data.feature_flags || {})).catch(() => setConfig(null));
    fetchAnalysisMetrics(days).then(setMetrics).catch(() => setMetrics(null));
  }, [days]);

  const toggleFlag = async (key, enabled) => {
    if (key === 'taxonomy_auto_activation_enabled' && enabled
      && !(await confirm('开启前请确认 taxonomy v1 已审核并结束 bootstrap。继续开启？'))) return;
    setFlagBusy(key);
    try {
      const data = await updateAnalysisConfig({ [key]: enabled });
      setConfig(data.feature_flags || {});
      window.dispatchEvent(new CustomEvent('dorami-analysis-config-changed', { detail: data.feature_flags || {} }));
      showToast?.(`已${enabled ? '开启' : '关闭'}${FLAG_META[key][0]}`, 'success');
    } catch (error) { showToast?.(error.message || '更新分析开关失败', 'error'); }
    finally { setFlagBusy(''); }
  };

  const publishV1 = async () => {
    if (!(await confirm('发布会激活 taxonomy v1，并创建最近 7 天闭集重标任务。继续发布？'))) return;
    setPublishBusy(true);
    try {
      const result = await publishTaxonomyV1('产品审核通过 taxonomy-bootstrap-v1');
      setTaxonomyState(result.state);
      showToast?.(`已发布 taxonomy v${result.taxonomy_version}`, 'success');
      await load();
    } catch (error) { showToast?.(error.message || '发布 taxonomy v1 失败', 'error'); }
    finally { setPublishBusy(false); }
  };

  const syncAliases = async () => {
    setPublishBusy(true);
    try {
      const result = await backfillCmsTagAliases('管理台同步规范中英文解析入口');
      setTaxonomyState(result.state);
      showToast?.(`已补齐 ${result.created} 个规范名 Alias`, 'success');
      await load();
    } catch (error) { showToast?.(error.message || '同步规范名 Alias 失败', 'error'); }
    finally { setPublishBusy(false); }
  };

  const filtered = useMemo(() => {
    const rows = view === 'tags' ? (tags || []) : (candidates || []);
    const needle = query.trim().toLocaleLowerCase();
    return rows.filter((row) => {
      const rowKind = row.kind || row.proposed_kind;
      if (kind && rowKind !== kind) return false;
      return !needle || [row.code, row.name_zh, row.name_en, row.label, row.normalized_label].some((value) => String(value || '').toLocaleLowerCase().includes(needle));
    });
  }, [candidates, kind, query, tags, view]);

  const analysis = metrics?.article_analysis;
  const taxonomy = metrics?.taxonomy;
  return (
    <div className="grid gap-4">
      <section className="surface-card card-pad rounded-[var(--r-card)]">
        <div className="card-head"><div><h2 className="card-title">发布开关</h2><p className="tiny-meta mt-1">关闭只停止消费方，不删除已有分析、标签或早报快照</p></div></div>
        <FeatureFlags config={config} onToggle={toggleFlag} busy={flagBusy} />
      </section>

      {metrics && (
        <section className="surface-card kpi-strip" aria-label="分析与 taxonomy 概览">
          <Metric value={pct(analysis?.success_rate)} label="分析成功率" sub={`近 ${metrics.window_days} 天`} />
          <Metric value={analysis?.score_p50 ?? '—'} label="评分 P50" sub={`P90 ${analysis?.score_p90 ?? '—'}`} />
          <Metric value={pct(analysis?.score_threshold_rates?.['7.0'])} label="7+ 占比" sub={`9+ ${pct(analysis?.score_threshold_rates?.['9.0'])}`} />
          <Metric value={pct(taxonomy?.tagged_article_rate)} label="标签覆盖" sub={`缺主标签 ${pct(taxonomy?.primary_missing_rate)}`} />
          <Metric value={taxonomy?.alias_count ?? 0} label="Alias" sub={`自动激活 ${taxonomy?.active_automatic_count ?? 0}`} />
        </section>
      )}

      <FullAnalysisBackfillCard showToast={showToast} />

      {taxonomyState && (
        <section className="surface-card card-pad rounded-[var(--r-card)]">
          <div className="card-head">
            <div><h2 className="card-title">Taxonomy 版本</h2><p className="tiny-meta mt-1">当前 active v{taxonomyState.active_version || 0} · 近 7 天覆盖 {pct(taxonomyState.coverage_7d?.coverage_rate)} · 未归并 Candidate {taxonomyState.unresolved_candidate_count}</p></div>
            <span className={`stamp ${taxonomyState.active_version > 0 || taxonomyState.publish_ready ? 'stamp-ok' : 'stamp-warn'}`}>{taxonomyState.active_version > 0 ? '已发布' : taxonomyState.publish_ready ? '可发布' : '待治理'}</span>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            {Object.entries(KIND_LABELS).map(([key, label]) => <div key={key} className="taxonomy-version-stat"><strong>{taxonomyState.active_tags_by_kind?.[key] || 0}</strong><span>{label} active</span></div>)}
          </div>
          {taxonomyState.publish_blockers?.length > 0 && <div className="mt-3 rounded-[var(--r-control)] bg-[var(--dorami-soft)] p-3 tiny-meta"><strong>发布前处理：</strong>{taxonomyState.publish_blockers.join('；')}</div>}
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            {taxonomyState.canonical_alias_gap_count > 0 && <button type="button" disabled={publishBusy} className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={syncAliases}>同步规范名 Alias</button>}
            <button type="button" disabled={publishBusy || !taxonomyState.publish_ready || taxonomyState.active_version > 0} className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={publishV1}>{publishBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} 发布 taxonomy v1</button>
          </div>
        </section>
      )}

      <InterestCatalogPolicyCard showToast={showToast} />

      <div className="zone-head">
        <span className="zone-title">CMS 标签与 Candidate</span>
        <span className="zone-hint">人工接受/新建默认用户可选，管理员仍可单独下架；合并、废弃和重标保留审计记录</span>
        <span className="zone-acts flex flex-wrap gap-2">
          <label className="relative"><Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" /><input className="form-input form-input-inline w-48 pl-8" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索标签 / Candidate" /></label>
          <select className="form-input form-input-inline w-28" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="筛选分面"><option value="">全部分面</option>{Object.entries(KIND_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          {view === 'candidates' && <select className="form-input form-input-inline w-28" value={candidateStatus} onChange={(e) => setCandidateStatus(e.target.value)} aria-label="筛选 Candidate 状态"><option value="">全部状态</option><option value="candidate">候选</option><option value="reviewing">审核中</option><option value="rejected">已拒绝</option><option value="merged">已归并</option><option value="activated">已激活</option></select>}
          <button type="button" className="icon-button" onClick={load} disabled={loading} aria-label="刷新 taxonomy"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="segmented-control" role="tablist" aria-label="CMS 治理视图">
          <button type="button" role="tab" aria-selected={view === 'candidates'} className={`segmented-option ${view === 'candidates' ? 'segmented-option-active' : ''}`} onClick={() => setView('candidates')}>候选 {candidateTotal}</button>
          <button type="button" role="tab" aria-selected={view === 'tags'} className={`segmented-option ${view === 'tags' ? 'segmented-option-active' : ''}`} onClick={() => setView('tags')}>规范标签 {tags?.length ?? '—'}</button>
        </div>
        <span className="flex-1" />
        {view === 'tags' && <CreateTagForm tags={tags || []} onCreated={load} showToast={showToast} />}
      </div>

      <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
        {loading && filtered.length === 0 ? <p className="p-8 text-center tiny-meta"><Loader2 className="mx-auto mb-2 h-4 w-4 animate-spin" />正在读取 taxonomy…</p>
          : filtered.length === 0 ? <p className="p-8 text-center tiny-meta">{view === 'tags' ? '还没有规范标签，可以新建第一个。' : '当前筛选下没有 Candidate。'}</p>
            : filtered.map((row) => view === 'tags'
              ? <TagRow key={row.id} tag={row} tags={tags || []} onChanged={load} showToast={showToast} />
              : <CandidateRow key={row.id} candidate={row} tags={tags || []} onChanged={load} showToast={showToast} confirm={confirm} />)}
      </section>
      {view === 'candidates' && candidateTotal > CANDIDATE_PAGE_SIZE && (
        <nav className="pager" aria-label="Candidate 分页">
          <button type="button" className="pager-btn" disabled={candidatePage === 0 || loading} onClick={() => setCandidatePage((value) => Math.max(0, value - 1))}>上一页</button>
          <span className="pager-ellipsis">第 {candidatePage + 1} / {Math.ceil(candidateTotal / CANDIDATE_PAGE_SIZE)} 页</span>
          <button type="button" className="pager-btn" disabled={(candidatePage + 1) * CANDIDATE_PAGE_SIZE >= candidateTotal || loading} onClick={() => setCandidatePage((value) => value + 1)}>下一页</button>
        </nav>
      )}
    </div>
  );
}
