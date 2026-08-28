import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, Plus, Rss, X } from 'lucide-react';
import { previewCustomSource } from '../api';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';

/**
 * 添加自定源浮层(用户自定 RSS 源波 v3.40,form-sheet 三件套语法)。
 *
 * 两步流:贴 URL → 预览(守门:能解析出条目才可保存;样例条目+配额余量所见即所得)
 * → 确认添加(默认名取 feed 标题,可改)。撞中已收录系统源时转订阅引导——添加动作
 * 由 useReaderState.handleAddCustomSource 编排(建源+订阅+首抓/转订阅),本组件只管
 * 表单流与就地错误。
 */
export default function AddCustomSourceModal({ open, onClose, onAdd }) {
  const modal = useModalTransition(open);
  const panelRef = useRef(null);
  useModalA11y(open && modal.mounted, onClose, panelRef);

  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [preview, setPreview] = useState(null);   // 预览成功的载荷(entries/quota)
  const [existing, setExisting] = useState(null); // 撞中系统源/既有自定源的引导载荷
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);        // 预览中/添加中

  // 关闭即重置(下次打开从空表单起步)
  useEffect(() => {
    if (!open) {
      setUrl(''); setName(''); setPreview(null); setExisting(null); setError(''); setBusy(false);
    }
  }, [open]);

  const handlePreview = async () => {
    const trimmed = url.trim();
    if (!trimmed) { setError('请输入 RSS/Atom 地址'); return; }
    setBusy(true); setError(''); setPreview(null); setExisting(null);
    try {
      const data = await previewCustomSource(trimmed);
      if (data.status === 'exists') {
        setExisting(data.existing || null);
      } else {
        setPreview(data);
        setName(data.feed_title || '');
      }
    } catch (err) {
      setError(err.message || '预览失败,请检查地址后重试');
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!preview && !existing) { handlePreview(); return; }
    setBusy(true); setError('');
    try {
      await onAdd(url.trim(), name.trim());
      onClose();
    } catch (err) {
      setError(err.message || '添加失败,请稍后重试');
      setBusy(false);
    }
  };

  if (!modal.mounted) return null;
  const readyToAdd = Boolean(preview) || (existing && !existing.subscribed);
  return createPortal(
    <div className={`modal-overlay ${modal.closing ? 'is-closing' : ''}`} onClick={onClose}>
      <form
        ref={panelRef} role="dialog" aria-modal="true" aria-label="添加自定源" tabIndex={-1}
        className="modal-panel max-w-md form-sheet" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}
      >
        <div className="form-sheet-head">
          <h3 className="card-title">添加自定源</h3>
          <button type="button" onClick={onClose} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
        </div>
        <div className="form-sheet-body">
          <div className="form-sheet-field">
            <label className="form-label" htmlFor="csrc-url">RSS/Atom 地址</label>
            <div className="csrc-url-row">
              <input
                id="csrc-url" type="url" value={url}
                onChange={(e) => { setUrl(e.target.value); setPreview(null); setExisting(null); setError(''); }}
                placeholder="https://example.com/feed.xml"
                autoComplete="off" spellCheck={false} className="form-input w-full"
              />
              <button
                type="button" onClick={handlePreview}
                disabled={busy || !url.trim()}
                className="action-button action-button-secondary min-h-[32px] px-3 text-xs shrink-0"
              >
                {busy && !preview && !existing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '预览'}
              </button>
            </div>
            <p className="tiny-meta" style={{ marginTop: 6 }}>
              贴入任意 RSS/Atom feed 地址;来源内容仅你自己可见,正文以 feed 提供的为准
            </p>
          </div>

          {error && <p className="csrc-error" role="alert">{error}</p>}

          {existing && (
            <div className="csrc-exists">
              <Rss className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                该来源已收录为「{existing.name || existing.source_id}」
                {existing.subscribed ? ',你已订阅' : ',确认后将为你订阅'}
              </span>
            </div>
          )}

          {preview && (
            <>
              <div className="form-sheet-field">
                <label className="form-label" htmlFor="csrc-name">名称</label>
                <input
                  id="csrc-name" value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="来源展示名" autoComplete="off" className="form-input w-full"
                />
              </div>
              <div className="csrc-preview">
                <p className="micro-label csrc-preview-label">
                  最近条目样例 · 共 {preview.entry_count} 条
                </p>
                <ul className="csrc-preview-list">
                  {(preview.entries || []).map((entry, i) => (
                    <li key={i} className="csrc-preview-item">
                      <span className="csrc-preview-title">{entry.title || '(无标题)'}</span>
                      <span className="csrc-preview-meta tabular-nums">
                        {entry.content_chars > 0 ? `${entry.content_chars.toLocaleString()} 字符` : '仅标题'}
                      </span>
                    </li>
                  ))}
                </ul>
                {preview.quota && (
                  <p className="tiny-meta csrc-quota tabular-nums">
                    我的自定源 {preview.quota.used}/{preview.quota.max}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
        <div className="form-sheet-foot">
          <button type="button" onClick={onClose} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
          <button
            type="submit" disabled={busy || (!readyToAdd && !url.trim())}
            className="action-button action-button-primary min-h-[32px] px-3 text-xs"
          >
            {busy && (preview || existing)
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Plus className="h-3.5 w-3.5" />}
            {existing && !existing.subscribed
              ? '订阅该源'
              : readyToAdd ? '添加并订阅' : '预览'}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
