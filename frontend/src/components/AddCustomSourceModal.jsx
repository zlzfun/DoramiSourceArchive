import { useEffect, useState } from 'react';
import { Loader2, Rss, X } from 'lucide-react';
import { previewCustomSource } from '../api';
import Modal from './Modal';

/**
 * 添加自定源浮层(用户自定 RSS 源波 v3.40)。
 *
 * 阅读器语境的 quiet 浮层语法(自持 .csrc-* 类族,非工作区 form-sheet):留白分层
 * 而非横线分层、一体化输入条(内嵌图标+就地预览钮)、hairline 条目样例、胶囊 CTA。
 * 两步流:贴 URL → 预览(守门:能解析出条目才可保存;样例+配额所见即所得)→ 确认。
 * 撞中已收录系统源时转订阅引导——添加动作由 useReaderState.handleAddCustomSource
 * 编排,本组件只管表单流与就地错误。
 * 外壳(遮罩关闭判定 / Esc / 焦点陷阱 / 退场动画)走共用 Modal(issue #104):
 * 面板内拖选文字松手落到遮罩不再误关。
 */
export default function AddCustomSourceModal({ open, onClose, onAdd, expectedKind = null }) {
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [kind, setKind] = useState(expectedKind || 'article');
  const [preview, setPreview] = useState(null);   // 预览成功的载荷(entries/quota)
  const [existing, setExisting] = useState(null); // 撞中系统源/既有自定源的引导载荷
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);        // 预览中/添加中

  // 关闭即重置(下次打开从空表单起步)
  useEffect(() => {
    if (!open) {
      setUrl(''); setName(''); setPreview(null); setExisting(null); setError(''); setBusy(false);
    } else {
      setKind(expectedKind || 'article');
    }
  }, [open, expectedKind]);

  const handlePreview = async () => {
    const trimmed = url.trim();
    if (!trimmed) { setError('请输入 RSS/Atom 地址'); return; }
    setBusy(true); setError(''); setPreview(null); setExisting(null);
    try {
      const data = await previewCustomSource(trimmed);
      if (data.status === 'exists') {
        setExisting(data.existing || null);
        setKind(data.existing?.content_kind || expectedKind || 'article');
      } else {
        setPreview(data);
        setName(data.feed_title || '');
        setKind(data.detected_kind || expectedKind || 'article');
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
      await onAdd(url.trim(), name.trim(), existing?.content_kind || kind);
      onClose();
    } catch (err) {
      setError(err.message || '添加失败,请稍后重试');
      setBusy(false);
    }
  };

  const previewing = busy && !preview && !existing;
  const readyToAdd = Boolean(preview) || (existing && !existing.subscribed);
  const title = expectedKind === 'podcast' ? '添加播客' : expectedKind === 'article' ? '添加文章源' : '添加自定源';
  return (
    <Modal
      open={open} onClose={onClose} closeOnOverlay portal size="none"
      as="form" panelClassName="csrc-sheet" ariaLabel={title}
      panelProps={{ onSubmit: handleSubmit }}
    >
      <button type="button" onClick={onClose} className="icon-button csrc-close" aria-label="关闭">
        <X className="w-4 h-4" />
      </button>

      <div className="csrc-head">
        <h3 className="csrc-title">{title}</h3>
        <p className="csrc-sub">贴入 RSS/Atom 地址，系统会自动识别文章或播客</p>
      </div>

      <div className={`csrc-urlbox ${error ? 'is-bad' : ''}`}>
        <Rss className="csrc-urlbox-ico" aria-hidden="true" />
        <input
          id="csrc-url" type="url" value={url}
          onChange={(e) => {
            setUrl(e.target.value); setPreview(null); setExisting(null); setError('');
            setKind(expectedKind || 'article');
          }}
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
          该{existing.content_kind === 'podcast' ? '播客' : '文章源'}已收录为「{existing.name || existing.source_id}」
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
          <div className="csrc-kind-row">
            <span className="csrc-kind-note">
              自动识别为{preview.detected_kind === 'podcast' ? '播客' : '文章'}，识别不准可手动调整
            </span>
            <span className="mini-seg" role="group" aria-label="内容类型">
              {[['article', '文章'], ['podcast', '播客']].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={`mini-seg-btn ${kind === value ? 'is-on' : ''}`}
                  aria-pressed={kind === value}
                  onClick={() => setKind(value)}
                >
                  {label}
                </button>
              ))}
            </span>
          </div>
          <ul className="csrc-entries">
            {(preview.entries || []).map((entry, i) => (
              <li key={i} className="csrc-entry">
                <span className="csrc-entry-title">{entry.title || '（无标题）'}</span>
                <span className="csrc-entry-meta tabular-nums">
                  {entry.has_audio
                    ? '可播放'
                    : entry.content_chars > 0 ? `${entry.content_chars.toLocaleString()} 字符` : '仅标题'}
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
    </Modal>
  );
}
