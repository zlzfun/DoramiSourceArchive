import { useEffect, useRef, useState } from 'react';
import { Inbox, Loader2, X } from 'lucide-react';
import Modal from './Modal';
import { buildIngestItem, describeReceipt, parseStatusLink } from '../utils/ingestForm';

const KINDS = [
  ['article', '文章'],
  ['social_post', '社交帖'],
  ['podcast_episode', '播客单集'],
];

const EMPTY = {
  url: '', title: '', content: '', summary: '', source_id: '', publish_date: '', note: '',
  handle: '', post_id: '', feed_url: '', audio_url: '', guid: '',
};

const STAMP_CLASS = { ok: 'stamp-ok', warn: 'stamp-warn', idle: 'stamp-idle', err: 'stamp-bad' };

function Field({ id, label, children }) {
  return (
    <div className="form-sheet-field">
      <label className="form-label" htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}

/**
 * 单条收录(issue #262 推送入口统一):知识台账的手工录入并入收录——与外部推送同一端点、同一套
 * 身份解析与查重,由服务端决定条目 id、类型与来路。
 */
export default function IngestItemModal({ isOpen, sourceIds = [], onClose, onSubmit }) {
  const [kind, setKind] = useState('article');
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState(null);
  // 请求代次:关闭 / 换类型 / 再收一条都让在途请求作废,迟到的回执与 finally 不碰当前表单(检视 R1 #6)。
  const generation = useRef(0);
  const invalidate = () => { generation.current += 1; setBusy(false); };

  useEffect(() => {
    if (!isOpen) {
      generation.current += 1;
      setKind('article'); setForm(EMPTY); setBusy(false); setReceipt(null);
    }
  }, [isOpen]);

  const set = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));
  const parsed = kind === 'social_post' ? parseStatusLink(form.url) : null;

  const handleSubmit = async (event) => {
    event.preventDefault();
    const mine = ++generation.current;
    setBusy(true);
    try {
      const result = await onSubmit(buildIngestItem(kind, form));
      if (generation.current === mine) setReceipt(result || null);
    } finally {
      if (generation.current === mine) setBusy(false);
    }
  };

  const restart = () => { invalidate(); setForm(EMPTY); setReceipt(null); };
  const info = describeReceipt(receipt);
  const stored = receipt && receipt.status !== 'rejected';

  return (
    <Modal
      open={isOpen} onClose={onClose} closeOnOverlay portal size="lg"
      as="form" panelClassName="form-sheet" ariaLabel="单条收录" panelProps={{ onSubmit: handleSubmit }}
    >
      <div className="form-sheet-head">
        <h3 className="card-title">单条收录</h3>
        <button type="button" onClick={onClose} className="icon-button" aria-label="关闭"><X className="w-4 h-4" /></button>
      </div>
      <div className="form-sheet-body">
        <div className="mini-seg self-start" role="group" aria-label="条目类型">
          {KINDS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => { invalidate(); setKind(value); setReceipt(null); }}
              className={`mini-seg-btn ${kind === value ? 'is-on' : ''}`}
              aria-pressed={kind === value}
            >
              {label}
            </button>
          ))}
        </div>

        {kind === 'article' && (
          <>
            <Field id="ingest-url" label="链接">
              <input id="ingest-url" required type="url" value={form.url} onChange={set('url')} className="form-input w-full" placeholder="https://" />
            </Field>
            <Field id="ingest-title" label="标题">
              <input id="ingest-title" value={form.title} onChange={set('title')} className="form-input w-full" />
            </Field>
            <Field id="ingest-content" label="正文">
              <textarea id="ingest-content" rows={5} value={form.content} onChange={set('content')} className="form-input w-full" />
            </Field>
            <Field id="ingest-source" label="来源">
              <input
                id="ingest-source" value={form.source_id} onChange={set('source_id')} list="ingest-source-list"
                className="form-input w-full" placeholder="按链接自动认"
              />
              <datalist id="ingest-source-list">{sourceIds.map((id) => <option key={id} value={id} />)}</datalist>
            </Field>
          </>
        )}

        {kind === 'social_post' && (
          <>
            <Field id="ingest-url" label="帖子链接">
              <input id="ingest-url" type="url" value={form.url} onChange={set('url')} className="form-input w-full" placeholder="https://x.com/…/status/…" />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field id="ingest-handle" label="作者 handle">
                <input
                  id="ingest-handle" required={!parsed} value={form.handle} onChange={set('handle')}
                  className="form-input w-full" placeholder={parsed?.handle || ''}
                />
              </Field>
              <Field id="ingest-post-id" label="帖子 ID">
                <input
                  id="ingest-post-id" required={!parsed} value={form.post_id} onChange={set('post_id')}
                  className="form-input w-full" placeholder={parsed?.post_id || ''}
                />
              </Field>
            </div>
            <Field id="ingest-content" label="正文">
              <textarea id="ingest-content" required rows={4} value={form.content} onChange={set('content')} className="form-input w-full" />
            </Field>
          </>
        )}

        {kind === 'podcast_episode' && (
          <>
            <Field id="ingest-feed" label="节目 feed">
              <input id="ingest-feed" required type="url" value={form.feed_url} onChange={set('feed_url')} className="form-input w-full" placeholder="https://" />
            </Field>
            <Field id="ingest-title" label="单集标题">
              <input id="ingest-title" required value={form.title} onChange={set('title')} className="form-input w-full" />
            </Field>
            <Field id="ingest-audio" label="音频地址">
              <input id="ingest-audio" required type="url" value={form.audio_url} onChange={set('audio_url')} className="form-input w-full" />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field id="ingest-guid" label="guid">
                <input id="ingest-guid" value={form.guid} onChange={set('guid')} className="form-input w-full" />
              </Field>
              <Field id="ingest-url" label="单集链接">
                <input id="ingest-url" type="url" value={form.url} onChange={set('url')} className="form-input w-full" />
              </Field>
            </div>
            <Field id="ingest-summary" label="简介">
              <textarea id="ingest-summary" rows={3} value={form.summary} onChange={set('summary')} className="form-input w-full" />
            </Field>
          </>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field id="ingest-date" label="发布时间">
            <input id="ingest-date" type="datetime-local" value={form.publish_date} onChange={set('publish_date')} className="form-input w-full" />
          </Field>
          <Field id="ingest-note" label="收录理由">
            <input id="ingest-note" value={form.note} onChange={set('note')} className="form-input w-full" />
          </Field>
        </div>

        {info && (
          <p className="flex items-center gap-2" role="status" data-testid="ingest-receipt">
            <span className={`stamp ${STAMP_CLASS[info.tone]}`}>{info.stamp}</span>
            <span className="body-text truncate" title={info.text}>{info.text}</span>
          </p>
        )}
      </div>
      <div className="form-sheet-foot">
        {stored ? (
          <>
            <button type="button" onClick={onClose} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">关闭</button>
            <button type="button" onClick={restart} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
              <Inbox className="h-3.5 w-3.5" /> 再收一条
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onClose} className="action-button action-button-quiet min-h-[32px] px-3 text-xs">取消</button>
            <button type="submit" disabled={busy} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
              {busy
                ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> 收录中…</>
                : <><Inbox className="h-3.5 w-3.5" /> 收录{KINDS.find(([v]) => v === kind)[1]}</>}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
