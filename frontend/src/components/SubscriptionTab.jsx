import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  Copy,
  Edit3,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
} from 'lucide-react';
import { API_BASE_URL } from '../config';
import {
  createSubscription,
  deleteSubscription,
  fetchSubscriptions,
  rotateSubscriptionToken,
  updateSubscription,
} from '../api';

const EMPTY_DRAFT = {
  name: '',
  description: '',
  source_ids: '',
  content_types: '',
  search: '',
  run_scope: '',
  has_content: true,
  include_content: true,
  default_limit: 100,
  max_limit: 500,
  is_active: true,
  passthrough_filters: {},
};

const DELIVERY_LIMIT_MAX = 500;
const UI_FILTER_KEYS = new Set([
  'content_type',
  'content_types',
  'source_id',
  'source_ids',
  'search',
  'run_scope',
  'has_content',
]);

const RUN_SCOPE_LABELS = {
  ad_hoc: '临时采集',
  saved_job: '固定任务',
  legacy_task: '旧任务',
};

function normalizeCsv(value) {
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .join(',');
}

function numberInRange(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function draftFromSubscription(subscription) {
  const filters = subscription.filters || {};
  const policy = subscription.delivery_policy || {};
  const passthroughFilters = Object.fromEntries(
    Object.entries(filters).filter(([key]) => !UI_FILTER_KEYS.has(key)),
  );
  return {
    name: subscription.name || '',
    description: subscription.description || '',
    source_ids: filters.source_ids || filters.source_id || '',
    content_types: filters.content_types || filters.content_type || '',
    search: filters.search || '',
    run_scope: filters.run_scope || '',
    has_content: filters.has_content !== false,
    include_content: policy.include_content !== false,
    default_limit: policy.default_limit || 100,
    max_limit: policy.max_limit || 500,
    is_active: subscription.is_active !== false,
    passthrough_filters: passthroughFilters,
  };
}

function buildPayload(draft) {
  const filters = {
    ...(draft.passthrough_filters || {}),
    has_content: Boolean(draft.has_content),
  };
  const sourceIds = normalizeCsv(draft.source_ids);
  const contentTypes = normalizeCsv(draft.content_types);
  if (sourceIds) filters.source_ids = sourceIds;
  if (contentTypes) filters.content_types = contentTypes;
  if (draft.search.trim()) filters.search = draft.search.trim();
  if (draft.run_scope) filters.run_scope = draft.run_scope;

  const defaultLimit = numberInRange(draft.default_limit, 100, 1, DELIVERY_LIMIT_MAX);
  const maxLimit = numberInRange(draft.max_limit, 500, defaultLimit, DELIVERY_LIMIT_MAX);

  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    filters,
    delivery_policy: {
      include_content: Boolean(draft.include_content),
      default_limit: defaultLimit,
      max_limit: maxLimit,
    },
    is_active: Boolean(draft.is_active),
  };
}

async function copyText(text) {
  if (!text) throw new Error('没有可复制的内容');
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) throw new Error('浏览器拒绝复制');
  } finally {
    document.body.removeChild(textarea);
  }
}

function publicEndpoint(subscriptionId) {
  const apiRoot = API_BASE_URL.startsWith('http')
    ? API_BASE_URL
    : `${window.location.origin}${API_BASE_URL}`;
  return `${apiRoot.replace(/\/$/, '')}/public/subscriptions/${subscriptionId}/dify/articles`;
}

function FilterSummary({ filters }) {
  const chips = [];
  const sourceLabel = filters.source_ids || filters.source_id;
  const typeLabel = filters.content_types || filters.content_type;
  if (sourceLabel) chips.push(`来源 ${sourceLabel}`);
  if (typeLabel) chips.push(`类型 ${typeLabel}`);
  if (filters.run_scope) chips.push(RUN_SCOPE_LABELS[filters.run_scope] || filters.run_scope);
  if (filters.search) chips.push(`检索 ${filters.search}`);
  chips.push(filters.has_content === false ? '允许无正文' : '仅正文');

  return (
    <div className="flex flex-wrap gap-2">
      {chips.map(chip => (
        <span key={chip} className="data-chip max-w-[220px] truncate" title={chip}>{chip}</span>
      ))}
    </div>
  );
}

function TokenNotice({ tokenInfo, onCopy, copied }) {
  if (!tokenInfo) return null;
  return (
    <div className="surface-card rounded-[14px] border-emerald-200 bg-emerald-50/80 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-emerald-100 text-emerald-700">
            <KeyRound className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-black text-emerald-900">订阅令牌仅显示一次</p>
            <p className="tiny-meta mt-1 text-emerald-700">请复制到下游系统，后续只能通过轮换生成新令牌。</p>
            <code className="mt-2 block break-all rounded-[10px] bg-white/80 px-3 py-2 text-xs font-bold text-emerald-950">
              {tokenInfo.token}
            </code>
          </div>
        </div>
        <button onClick={() => onCopy(tokenInfo.token, 'token-notice')} className="action-button action-button-secondary shrink-0">
          {copied === 'token-notice' ? <Check /> : <Copy />}
          {copied === 'token-notice' ? '已复制' : '复制令牌'}
        </button>
      </div>
    </div>
  );
}

export default function SubscriptionTab({ showToast }) {
  const [subscriptions, setSubscriptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [tokenInfo, setTokenInfo] = useState(null);
  const [copiedKey, setCopiedKey] = useState('');

  const loadSubscriptions = useCallback(async () => {
    setLoading(true);
    try {
      setSubscriptions(await fetchSubscriptions());
    } catch (error) {
      showToast(error.message || '获取订阅源失败', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadSubscriptions();
  }, [loadSubscriptions]);

  const activeCount = useMemo(
    () => subscriptions.filter(item => item.is_active).length,
    [subscriptions],
  );

  const openCreate = () => {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setEditorOpen(true);
  };

  const openEdit = (subscription) => {
    setEditing(subscription);
    setDraft(draftFromSubscription(subscription));
    setEditorOpen(true);
  };

  const handleCopy = async (text, key) => {
    try {
      await copyText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(''), 1800);
      showToast('已复制', 'success');
    } catch (error) {
      showToast(error.message || '复制失败', 'error');
    }
  };

  const handleSave = async (event) => {
    event.preventDefault();
    const payload = buildPayload(draft);
    if (!payload.name) {
      showToast('订阅源名称不能为空', 'error');
      return;
    }

    setSaving(true);
    try {
      const result = editing
        ? await updateSubscription(editing.id, payload)
        : await createSubscription(payload);
      setEditorOpen(false);
      setEditing(null);
      setTokenInfo(result.token ? { id: result.id, token: result.token } : null);
      await loadSubscriptions();
      showToast(editing ? '订阅源已更新' : '订阅源已创建', 'success');
    } catch (error) {
      showToast(error.message || '保存订阅源失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleRotate = async (subscription) => {
    if (!window.confirm(`轮换「${subscription.name}」的访问令牌？旧令牌会立即失效。`)) return;
    try {
      const result = await rotateSubscriptionToken(subscription.id);
      setTokenInfo({ id: result.id, token: result.token });
      await loadSubscriptions();
      showToast('订阅令牌已轮换', 'success');
    } catch (error) {
      showToast(error.message || '轮换订阅令牌失败', 'error');
    }
  };

  const handleDelete = async (subscription) => {
    if (!window.confirm(`删除订阅源「${subscription.name}」？下游接口会停止访问。`)) return;
    try {
      await deleteSubscription(subscription.id);
      if (tokenInfo?.id === subscription.id) setTokenInfo(null);
      await loadSubscriptions();
      showToast('订阅源已删除', 'success');
    } catch (error) {
      showToast(error.message || '删除订阅源失败', 'error');
    }
  };

  const updateDraft = (key, value) => {
    setDraft(prev => ({ ...prev, [key]: value }));
  };

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">订阅分发</h2>
          <p className="page-subtitle mt-3 max-w-3xl">管理读者层的个性化内容源，向 Dify 等下游编排应用提供带令牌的拉取接口。</p>
        </div>
        <div className="page-actions">
          <button onClick={loadSubscriptions} disabled={loading} className="action-button action-button-secondary">
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </button>
          <button onClick={openCreate} className="action-button action-button-primary">
            <Plus />
            新建订阅源
          </button>
        </div>
      </div>

      <TokenNotice tokenInfo={tokenInfo} onCopy={handleCopy} copied={copiedKey} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">订阅源总数</p>
          <p className="stat-number mt-2">{subscriptions.length}</p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">启用中</p>
          <p className="stat-number mt-2 text-emerald-600">{activeCount}</p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">交付协议</p>
          <p className="mt-2 text-sm font-black text-slate-900">Dify Articles Pull</p>
        </div>
      </div>

      <div className="surface-card overflow-hidden rounded-[14px]">
        <div className="flex items-center gap-3 border-b border-slate-100 px-6 py-4">
          <div className="h-5 w-1 rounded-full bg-indigo-500" />
          <h3 className="section-title">订阅源列表</h3>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 px-6 py-12 text-sm font-bold text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
            正在加载订阅源
          </div>
        ) : subscriptions.length === 0 ? (
          <div className="p-6">
            <div className="empty-state py-12">还没有订阅源</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table w-full min-w-[980px] text-left text-sm">
              <thead>
                <tr className="text-xs font-black uppercase tracking-wide text-slate-500">
                  <th className="px-5 py-3">名称</th>
                  <th className="px-5 py-3">过滤范围</th>
                  <th className="px-5 py-3">交付策略</th>
                  <th className="px-5 py-3">下游接口</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {subscriptions.map(subscription => {
                  const endpoint = publicEndpoint(subscription.id);
                  const policy = subscription.delivery_policy || {};
                  return (
                    <tr key={subscription.id} className="align-top">
                      <td className="px-5 py-4">
                        <div className="flex items-start gap-3">
                          <span className={`mt-1 h-2.5 w-2.5 rounded-full ${subscription.is_active ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                          <div className="min-w-0">
                            <p className="font-black text-slate-900">{subscription.name}</p>
                            {subscription.description && (
                              <p className="mt-1 max-w-[260px] text-xs font-medium leading-5 text-slate-500">{subscription.description}</p>
                            )}
                            <p className="tiny-meta mt-2">Token {subscription.token_preview || '未生成'}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <FilterSummary filters={subscription.filters || {}} />
                      </td>
                      <td className="px-5 py-4">
                        <div className="space-y-2 text-xs font-bold text-slate-600">
                          <p>{policy.include_content === false ? '仅元数据' : '含正文'}</p>
                          <p className="tiny-meta">默认 {policy.default_limit || 100}，上限 {policy.max_limit || 500}</p>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex max-w-[320px] items-center gap-2 rounded-[10px] border border-slate-100 bg-slate-50 px-3 py-2">
                          <code className="min-w-0 flex-1 truncate text-xs font-bold text-slate-600" title={endpoint}>{endpoint}</code>
                          <button
                            type="button"
                            onClick={() => handleCopy(endpoint, `endpoint-${subscription.id}`)}
                            className="shrink-0 text-slate-400 hover:text-indigo-600"
                            title="复制接口地址"
                            aria-label="复制接口地址"
                          >
                            {copiedKey === `endpoint-${subscription.id}` ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
                          </button>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex justify-end gap-2">
                          <button onClick={() => openEdit(subscription)} className="icon-button" title="编辑订阅源" aria-label="编辑订阅源">
                            <Edit3 className="h-4 w-4" />
                          </button>
                          <button onClick={() => handleRotate(subscription)} className="icon-button" title="轮换令牌" aria-label="轮换令牌">
                            <RotateCw className="h-4 w-4" />
                          </button>
                          <button onClick={() => handleDelete(subscription)} className="icon-button text-rose-500" title="删除订阅源" aria-label="删除订阅源">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editorOpen && (
        <div className="modal-overlay">
          <form onSubmit={handleSave} className="modal-panel max-w-3xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
              <div>
                <h3 className="text-base font-black text-slate-950">{editing ? '编辑订阅源' : '新建订阅源'}</h3>
                <p className="tiny-meta mt-1">订阅源只筛选读者层已有归档，不触发外网采集。</p>
              </div>
              <button type="button" onClick={() => setEditorOpen(false)} className="action-button action-button-quiet">取消</button>
            </div>

            <div className="space-y-5 overflow-y-auto px-6 py-5">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="form-field">
                  <span>名称</span>
                  <input className="form-input" value={draft.name} onChange={event => updateDraft('name', event.target.value)} placeholder="OpenAI 产品更新" />
                </label>
                <label className="form-field">
                  <span>运行范围</span>
                  <select className="form-input" value={draft.run_scope} onChange={event => updateDraft('run_scope', event.target.value)}>
                    <option value="">全部运行来源</option>
                    <option value="ad_hoc">临时采集</option>
                    <option value="saved_job">固定任务</option>
                    <option value="legacy_task">旧任务</option>
                  </select>
                </label>
              </div>

              <label className="form-field">
                <span>描述</span>
                <textarea className="form-input min-h-[86px] resize-y" value={draft.description} onChange={event => updateDraft('description', event.target.value)} placeholder="给下游工作流看的用途说明" />
              </label>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="form-field">
                  <span>来源 ID（逗号分隔）</span>
                  <input className="form-input" value={draft.source_ids} onChange={event => updateDraft('source_ids', event.target.value)} placeholder="rss_openai_news,rss_anthropic" />
                </label>
                <label className="form-field">
                  <span>内容类型（逗号分隔）</span>
                  <input className="form-input" value={draft.content_types} onChange={event => updateDraft('content_types', event.target.value)} placeholder="rss_article,webpage" />
                </label>
              </div>

              <label className="form-field">
                <span>关键词过滤</span>
                <input className="form-input" value={draft.search} onChange={event => updateDraft('search', event.target.value)} placeholder="产品发布 OR research" />
              </label>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="form-field">
                  <span>默认拉取数量</span>
                  <input className="form-input" type="number" min="1" max={DELIVERY_LIMIT_MAX} value={draft.default_limit} onChange={event => updateDraft('default_limit', event.target.value)} />
                </label>
                <label className="form-field">
                  <span>最大拉取数量</span>
                  <input className="form-input" type="number" min="1" max={DELIVERY_LIMIT_MAX} value={draft.max_limit} onChange={event => updateDraft('max_limit', event.target.value)} />
                </label>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="flex items-center gap-3 rounded-[12px] border border-slate-100 bg-slate-50 px-4 py-3 text-sm font-bold text-slate-700">
                  <input type="checkbox" checked={draft.has_content} onChange={event => updateDraft('has_content', event.target.checked)} />
                  仅返回有正文
                </label>
                <label className="flex items-center gap-3 rounded-[12px] border border-slate-100 bg-slate-50 px-4 py-3 text-sm font-bold text-slate-700">
                  <input type="checkbox" checked={draft.include_content} onChange={event => updateDraft('include_content', event.target.checked)} />
                  下发正文
                </label>
                <label className="flex items-center gap-3 rounded-[12px] border border-slate-100 bg-slate-50 px-4 py-3 text-sm font-bold text-slate-700">
                  <input type="checkbox" checked={draft.is_active} onChange={event => updateDraft('is_active', event.target.checked)} />
                  启用订阅
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
              <button type="button" onClick={() => setEditorOpen(false)} className="action-button action-button-secondary">取消</button>
              <button type="submit" disabled={saving} className="action-button action-button-primary">
                {saving ? <Loader2 className="animate-spin" /> : <Check />}
                保存
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
