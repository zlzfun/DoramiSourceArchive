import { useMemo, useState } from 'react';
import { CheckSquare, Save, Search, X } from 'lucide-react';
import Modal from './Modal';
import LogoMark from './LogoMark';
import { resolveCompany } from '../sourceTaxonomy';
import { normalizeIds } from '../utils/collection';

// 采集范围（节点组）编辑弹窗：受控 dialog。自持节点搜索这一 UI 态；草稿由父组件持有
// （draft/setDraft），保存逻辑（校验 + 建/更新 + 副作用）留在父组件的 onSave。
export default function NodeGroupModal({
  open,
  onClose,
  editing,
  draft,
  setDraft,
  fetchersById,
  availableFetchers,
  fetchConfigs,
  onSave,
}) {
  const [search, setSearch] = useState('');

  const modalFetchers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return availableFetchers.filter(fetcher => [
      fetcher.name, fetcher.id, fetcher.desc, fetcher.base_url, fetcher.source_owner, fetcher.source_brand,
      ...(fetcher.content_tags || []),
    ].filter(Boolean).join(' ').toLowerCase().includes(query));
  }, [availableFetchers, search]);

  const updateNodeParam = (fetcherId, field, value) => {
    setDraft(prev => ({
      ...prev,
      per_fetcher_params: {
        ...(prev.per_fetcher_params || {}),
        [fetcherId]: { ...((prev.per_fetcher_params || {})[fetcherId] || {}), [field]: value },
      },
    }));
  };

  const renderParamInput = (fetcherId, param) => {
    const params = (draft.per_fetcher_params || {})[fetcherId] || {};
    const value = params[param.field] ?? param.default ?? '';
    if (param.type === 'boolean') {
      const checked = typeof value === 'boolean' ? value : ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
      return (
        <input
          type="checkbox"
          checked={checked}
          onChange={event => updateNodeParam(fetcherId, param.field, event.target.checked)}
          className="w-4 h-4 text-blue-600 rounded border-slate-300"
        />
      );
    }
    return (
      <input
        type={param.type || 'text'}
        value={value}
        onChange={event => updateNodeParam(fetcherId, param.field, param.type === 'number' ? Number(event.target.value) : event.target.value)}
        className="form-input py-1.5 text-xs"
      />
    );
  };

  return (
    <Modal open={open} onClose={onClose} size="5xl" ariaLabel={editing ? '编辑采集范围' : '新建采集范围'}>
      <div className="px-5 py-4 border-b border-[var(--dorami-border)] bg-[var(--dorami-well)] flex items-center justify-between">
        <div>
          <h3 className="card-title">{editing ? '编辑采集范围' : '新建采集范围'}</h3>
          <p className="text-xs text-slate-500 mt-1">采集范围只维护节点集合和参数模板，可被采集任务复用。</p>
        </div>
        <button onClick={onClose} className="icon-button"><X className="w-4 h-4" /></button>
      </div>
      <div className="p-5 overflow-auto space-y-5">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="text-xs font-bold text-slate-500">
            名称
            <input value={draft.name} onChange={event => setDraft(prev => ({ ...prev, name: event.target.value }))} className="form-input mt-1" />
          </label>
          <label className="text-xs font-bold text-slate-500">
            说明
            <input value={draft.description} onChange={event => setDraft(prev => ({ ...prev, description: event.target.value }))} className="form-input mt-1" />
          </label>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
          <div className="border border-[var(--dorami-border)] rounded-[var(--r-card)] overflow-hidden">
            <div className="p-3 bg-[var(--dorami-soft)] border-b border-[var(--dorami-border)]">
              <div className="form-search-box relative">
                <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索节点" className="form-input pl-9" />
              </div>
            </div>
            <div className="max-h-[420px] overflow-auto divide-y divide-[var(--dorami-border)]">
              {modalFetchers.map(fetcher => {
                const checked = (draft.fetcher_ids || []).includes(fetcher.id);
                return (
                  <button
                    key={fetcher.id}
                    onClick={() => setDraft(prev => {
                      const ids = checked ? prev.fetcher_ids.filter(id => id !== fetcher.id) : [...(prev.fetcher_ids || []), fetcher.id];
                      return {
                        ...prev,
                        fetcher_ids: normalizeIds(ids),
                        per_fetcher_params: checked
                          ? prev.per_fetcher_params
                          : { ...(prev.per_fetcher_params || {}), [fetcher.id]: fetchConfigs[fetcher.id] || {} },
                      };
                    })}
                    className={`w-full px-3 py-3 flex items-center gap-3 text-left hover:bg-[var(--dorami-soft)] ${checked ? 'bg-blue-50/60' : ''}`}
                  >
                    <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${checked ? 'bg-blue-600 border-blue-600' : 'border-slate-300'}`}>{checked && <CheckSquare className="w-3.5 h-3.5 text-white" />}</div>
                    <LogoMark company={resolveCompany(fetcher)} size="sm" />
                    <div className="min-w-0">
                      <div className="font-bold text-slate-700 text-sm truncate">{fetcher.name}</div>
                      <div className="font-mono text-xs text-slate-500 truncate">{fetcher.id}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-3">
            {(draft.fetcher_ids || []).length === 0 ? (
              <div className="border border-dashed border-[var(--dorami-border)] rounded-[var(--r-card)] p-10 text-center text-slate-500 font-medium">未选择节点</div>
            ) : (draft.fetcher_ids || []).map(fetcherId => {
              const fetcher = fetchersById[fetcherId];
              return (
                <div key={fetcherId} className="border border-[var(--dorami-border)] rounded-[var(--r-card)] p-3 bg-white dark:bg-[var(--dorami-surface)]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <LogoMark company={resolveCompany(fetcher || {})} size="sm" />
                      <div className="min-w-0">
                        <div className="card-title truncate">{fetcher?.name || fetcherId}</div>
                        <div className="font-mono text-xs text-slate-500 mt-0.5">{fetcherId}</div>
                      </div>
                    </div>
                    <button onClick={() => setDraft(prev => ({ ...prev, fetcher_ids: prev.fetcher_ids.filter(id => id !== fetcherId) }))} className="p-1.5 text-slate-500 hover:text-red-600 hover:bg-red-50 rounded-[var(--r-control)]"><X className="w-4 h-4" /></button>
                  </div>
                  <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                    {(fetcher?.parameters || []).length === 0 ? (
                      <div className="text-xs text-slate-500 font-medium bg-[var(--dorami-soft)] border border-[var(--dorami-border)] rounded-[var(--r-control)] px-3 py-2">该节点无需扩展参数</div>
                    ) : (fetcher.parameters || []).map(param => (
                      <label key={param.field} className="text-xs font-bold text-slate-500">
                        {param.label}
                        <div className="mt-1">{renderParamInput(fetcherId, param)}</div>
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="px-5 py-4 border-t border-[var(--dorami-border)] bg-[var(--dorami-surface)] flex justify-end gap-2">
        <button onClick={onClose} className="action-button action-button-quiet">取消</button>
        <button onClick={onSave} className="action-button action-button-primary"><Save /> 保存</button>
      </div>
    </Modal>
  );
}
