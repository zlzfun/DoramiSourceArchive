import { useEffect, useMemo, useRef, useState } from 'react';
import { Ban, Check, Loader2, Search, X } from 'lucide-react';
import { fetchInterestCatalog, fetchInterests, saveInterests } from '../api';
import { useModalA11y } from '../hooks/useModalA11y';

const KIND_META = {
  topic: { label: '主题', hint: '技术方向与长期议题' },
  industry: { label: '行业', hint: '应用领域与产业方向' },
  entity: { label: '实体', hint: '组织、产品、模型、协议与开源项目' },
};

const keyOf = (tag) => String(tag.id);

export default function InterestManager({ open, onboarding = false, onClose, onSaved, showToast }) {
  const panelRef = useRef(null);
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState({});
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('topic');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  useModalA11y(open, onboarding ? undefined : onClose, panelRef);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    setCatalog(null);
    setError('');
    Promise.all([
      fetchInterestCatalog({ signal: controller.signal }),
      fetchInterests({ signal: controller.signal }),
    ]).then(([catalogData, current]) => {
      setCatalog(catalogData);
      const next = {};
      (current.items || []).forEach(({ tag, stance }) => {
        next[keyOf(tag)] = stance === 'mute' ? 'mute' : 'follow';
      });
      setDraft(next);
    }).catch((err) => {
      if (err.name !== 'AbortError') setError(err.message || '加载兴趣设置失败，请重试');
    });
    return () => controller.abort();
  }, [open, reloadKey]);

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (catalog?.facets?.[kind] || []).filter((tag) => {
      if (!needle) return true;
      return [tag.name_zh, tag.name_en, tag.code, tag.description]
        .some((value) => String(value || '').toLocaleLowerCase().includes(needle));
    });
  }, [catalog, kind, query]);

  const followedCount = Object.values(draft).filter((value) => value === 'follow').length;
  const mutedCount = Object.values(draft).filter((value) => value === 'mute').length;

  const handleSave = async ({ skip = false } = {}) => {
    setSaving(true);
    setError('');
    try {
      const tags = catalog?.items || [];
      const items = (skip ? [] : tags.flatMap((tag) => {
        const value = draft[keyOf(tag)] || 'off';
        if (value === 'off') return [];
        return [{
          tag_id: tag.id,
          stance: value === 'mute' ? 'mute' : 'follow',
        }];
      }));
      await saveInterests(items, { completeOnboarding: onboarding });
      showToast?.(
        onboarding
          ? (skip ? '已跳过兴趣选择，可稍后在我的兴趣中设置' : '已保存兴趣并开始准备个人早报')
          : '已保存我的兴趣并重新编排今日早报',
        'success',
      );
      onSaved?.({ onboardingCompleted: onboarding });
      onClose?.();
    } catch (err) {
      setError(err.message || '保存兴趣设置失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;
  return (
    <div className="modal-overlay" onMouseDown={(event) => !onboarding && event.target === event.currentTarget && onClose?.()}>
      <section
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="interest-title"
        className="modal-panel max-w-4xl form-sheet"
      >
        <div className="form-sheet-head">
          <div>
            <div className="micro-label">{onboarding ? '欢迎来到哆啦美' : '个人早报偏好'}</div>
            <h2 id="interest-title" className="card-title mt-1">{onboarding ? '选择你感兴趣的内容' : '我的兴趣'}</h2>
            <p className="tiny-meta mt-1">
              关注会提高相关内容的入选优先级；未关注仍可能因质量高而入选；屏蔽会从你的个人早报中排除。只作用于已订阅来源。
            </p>
          </div>
          {!onboarding && (
            <button type="button" className="icon-button" onClick={onClose} aria-label="关闭兴趣管理">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="form-sheet-body min-h-0 overflow-y-auto">
          {!catalog && !error ? (
            <div className="flex min-h-64 items-center justify-center gap-2 tiny-meta" role="status">
              <Loader2 className="h-4 w-4 animate-spin" /> 正在读取可选标签…
            </div>
          ) : error ? (
            <div className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center">
              <p className="body-text">{error}</p>
              <button type="button" className="action-button action-button-secondary mt-3 min-h-[32px] px-3 text-xs" onClick={() => setReloadKey((value) => value + 1)}>重试加载</button>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <div className="segmented-control" role="tablist" aria-label="兴趣分面">
                  {Object.entries(KIND_META).map(([id, meta]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={kind === id}
                      onClick={() => setKind(id)}
                      className={`segmented-option ${kind === id ? 'segmented-option-active' : ''}`}
                    >
                      {meta.label}
                    </button>
                  ))}
                </div>
                <label className="relative ml-auto min-w-56 flex-1 sm:max-w-72">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                  <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索标签"
                    aria-label="搜索兴趣标签"
                    className="form-input form-input-inline w-full pl-8"
                  />
                </label>
              </div>
              <p className="tiny-meta mt-3">{KIND_META[kind].hint}。目录按近 {catalog?.policy?.window_days || 30} 天热度展示；兴趣内容最多占早报的 50%，其余由质量通道补齐。</p>

              <div className="mt-4 grid gap-2">
                {visible.length === 0 ? (
                  <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-6 text-center tiny-meta">没有匹配的可选标签</p>
                ) : visible.map((tag) => {
                  const value = draft[keyOf(tag)] || 'off';
                  const setValue = (next) => setDraft((prev) => ({
                    ...prev,
                    [keyOf(tag)]: value === next ? 'off' : next,
                  }));
                  return (
                    <div key={tag.id} className="flex flex-col gap-3 rounded-[var(--r-card)] bg-[var(--dorami-soft)] p-3 sm:flex-row sm:items-center">
                      <div className="min-w-0 flex-1">
                        <div className="body-text">{tag.name_zh || tag.name_en || tag.code}</div>
                        <div className="tiny-meta mt-0.5 line-clamp-2">{tag.description || tag.name_en || tag.code}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2" role="group" aria-label={`${tag.name_zh || tag.code}的兴趣设置`}>
                        <span className="tiny-meta min-w-12 text-right">
                          {value === 'follow' ? '已关注' : value === 'mute' ? '已屏蔽' : '未关注'}
                        </span>
                        <button
                          type="button"
                          aria-pressed={value === 'follow'}
                          onClick={() => setValue('follow')}
                          className={`action-button min-h-[32px] px-3 text-xs ${value === 'follow' ? 'action-button-primary' : 'action-button-secondary'}`}
                        >
                          {value === 'follow' && <Check className="h-3.5 w-3.5" />} 关注
                        </button>
                        <button
                          type="button"
                          aria-pressed={value === 'mute'}
                          onClick={() => setValue('mute')}
                          className={`action-button min-h-[32px] px-3 text-xs ${value === 'mute' ? 'action-button-primary' : 'action-button-quiet'}`}
                        >
                          <Ban className="h-3.5 w-3.5" /> 屏蔽
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        <div className="form-sheet-foot">
          <span className="tiny-meta mr-auto">已关注 {followedCount} 个 · 已屏蔽 {mutedCount} 个</span>
          {onboarding ? (
            <button type="button" disabled={saving} className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={() => handleSave({ skip: true })}>暂不关注，直接开始</button>
          ) : (
            <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>取消</button>
          )}
          <button type="button" disabled={!catalog || saving} className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={() => handleSave()}>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {saving ? '保存中…' : onboarding ? '保存兴趣，开始使用' : '保存并重编今日早报'}
          </button>
        </div>
      </section>
    </div>
  );
}
