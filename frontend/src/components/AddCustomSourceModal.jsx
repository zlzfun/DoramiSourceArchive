import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, Rss, X } from 'lucide-react';
import { previewCustomSource } from '../api';
import { useModalTransition } from '../hooks/useModalTransition';
import { useModalA11y } from '../hooks/useModalA11y';

/**
 * 添加自定源浮层(用户自定 RSS 源波 v3.40)。
 *
 * 阅读器语境的 quiet 浮层语法(自持 .csrc-* 类族,非工作区 form-sheet):留白分层
 * 而非横线分层、一体化输入条(内嵌图标+就地预览钮)、hairline 条目样例、胶囊 CTA。
 * 两步流:贴 URL → 预览(守门:能解析出条目才可保存;样例+配额所见即所得)→ 确认。
 * 撞中已收录系统源时转订阅引导——添加动作由 useReaderState.handleAddCustomSource
 * 编排,本组件只管表单流与就地错误。
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
  const previewing = busy && !preview && !existing;
  const readyToAdd = Boolean(preview) || (existing && !existing.subscribed);
  return createPortal(
    <div className={`modal-overlay ${modal.closing ? 'is-closing' : ''}`} onClick={onClose}>
      <form
        ref={panelRef} role="dialog" aria-modal="true" aria-label="添加自定源" tabIndex={-1}
        className="modal-panel csrc-sheet" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}
      >
        <button type="button" onClick={onClose} className="icon-button csrc-close" aria-label="关闭">
          <X className="w-4 h-4" />
        </button>

        <div className="csrc-head">
          <h3 className="csrc-title">添加自定源</h3>
          <p className="csrc-sub">贴入 RSS/Atom 地址，来源与内容只有你自己可见</p>
        </div>

        <div className={`csrc-urlbox ${error ? 'is-bad' : ''}`}>
          <Rss className="csrc-urlbox-ico" aria-hidden="true" />
          <input
            id="csrc-url" type="url" value={url}
            onChange={(e) => { setUrl(e.target.value); setPreview(null); setExisting(null); setError(''); }}
            onKeyDown={(e) => {
              // CTA 未预览时 disabled,Enter 的默认 submit 不会发生——就地触发预览保键盘流
              if (e.key === 'Enter' && !preview && !existing) { e.preventDefault(); handlePreview(); }
            }}
            placeholder="https://example.com/feed.xml"
            autoComplete="off" spellCheck={false} aria-label="RSS/Atom 地址"
          />
          <button
            type="button" className="csrc-urlbox-btn" onClick={handlePreview}
            disabled={busy || !url.trim()}
          >
            {previewing ? <Loader2 className="h-3 w-3 animate-spin" /> : '预览'}
          </button>
        </div>
        {error && <p className="csrc-error" role="alert">{error}</p>}

        {existing && (
          <div className="csrc-exists">
            该来源已收录为「{existing.name || existing.source_id}」
            {existing.subscribed ? '，你已订阅' : '，确认后将为你订阅'}
          </div>
        )}

        {preview && (
          <>
            <div className="csrc-feedmeta">
              <input
                id="csrc-name" className="csrc-name-input" value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="来源展示名" autoComplete="off" aria-label="来源展示名"
              />
              <span className="csrc-feedcount tabular-nums">{preview.entry_count} 条</span>
            </div>
            <ul className="csrc-entries">
              {(preview.entries || []).map((entry, i) => (
                <li key={i} className="csrc-entry">
                  <span className="csrc-entry-title">{entry.title || '（无标题）'}</span>
                  <span className="csrc-entry-meta tabular-nums">
                    {entry.content_chars > 0 ? `${entry.content_chars.toLocaleString()} 字符` : '仅标题'}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        <div className="csrc-foot">
          {preview?.quota ? (
            <span className="csrc-quota tabular-nums">我的自定源 {preview.quota.used}/{preview.quota.max}</span>
          ) : <span />}
          <div className="csrc-foot-acts">
            <button type="button" onClick={onClose} className="csrc-btn-quiet">取消</button>
            {/* CTA 恒为确认语义:未预览时禁用(预览是输入条内的就地动作,两钮不重复);
                Enter 提交在未预览态仍触发预览(handleSubmit 分流),键盘流不断 */}
            <button
              type="submit" className="csrc-btn-cta"
              disabled={busy || !readyToAdd}
            >
              {busy && (preview || existing)
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : null}
              {existing ? (existing.subscribed ? '已订阅' : '订阅该源') : '添加并订阅'}
            </button>
          </div>
        </div>
      </form>
    </div>,
    document.body,
  );
}
