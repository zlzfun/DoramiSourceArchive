// 反馈与建议(v3.18 互通波,设置柜 · 通用):读者把想法与问题告诉管理员,
// 并在同一处看到处理进展与回复。读者面文案不出现内部架构词;时间戳用相对时间
// (hover 给完整时间,§1);回复气泡与管理面同一视觉语言(.fb-reply)。
import { useCallback, useEffect, useState } from 'react';
import { Loader2, Send, Undo2 } from 'lucide-react';
import { fetchMyFeedback, submitFeedback, withdrawFeedback } from '../../api';
import { formatDateTime, formatRelativeTime } from '../../utils/datetime';

const CATEGORIES = [
  ['source_request', '想要新内容'],
  ['bug', '问题反馈'],
  ['suggestion', '功能建议'],
  ['other', '其他'],
];
const CATEGORY_LABELS = Object.fromEntries(CATEGORIES);

const STATUS_META = {
  open: { label: '待处理', stamp: 'stamp-run' },
  in_progress: { label: '处理中', stamp: 'stamp-warn' },
  resolved: { label: '已完成', stamp: 'stamp-ok' },
  dismissed: { label: '已关闭', stamp: 'stamp-idle' },
};

const CONTENT_MAX = 2000;

export default function FeedbackSection({ showToast }) {
  const [category, setCategory] = useState('source_request');
  const [content, setContent] = useState('');
  const [items, setItems] = useState(null); // null = 加载中
  const [submitting, setSubmitting] = useState(false);
  const [withdrawingId, setWithdrawingId] = useState(null);

  const load = useCallback(() => {
    fetchMyFeedback()
      .then((data) => setItems(Array.isArray(data?.items) ? data.items : []))
      .catch((error) => { setItems([]); showToast(error.message, 'error'); });
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!content.trim() || submitting) return;
    setSubmitting(true);
    try {
      await submitFeedback(category, content.trim());
      showToast('已发送反馈', 'success');
      setContent('');
      load();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleWithdraw = async (id) => {
    setWithdrawingId(id);
    try {
      await withdrawFeedback(id);
      setItems((prev) => (prev || []).filter((item) => item.id !== id));
      showToast('已撤回反馈', 'success');
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setWithdrawingId(null);
    }
  };

  return (
    <div>
      <form className="sett-row is-block" onSubmit={handleSubmit}>
        <span className="sett-lbl">告诉我们你的想法</span>
        <p className="sett-sub">想看到新的内容、遇到了问题,或有功能上的建议,都可以写在这里。</p>

        <div className="mini-seg mt-3" role="group" aria-label="反馈类型">
          {CATEGORIES.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`mini-seg-btn ${category === value ? 'is-on' : ''}`}
              aria-pressed={category === value}
              onClick={() => setCategory(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <textarea
          className="form-input mt-3 resize-y"
          rows={4}
          value={content}
          maxLength={CONTENT_MAX}
          onChange={(e) => setContent(e.target.value)}
          placeholder="比如:希望多一些关于……的内容"
          aria-label="反馈内容"
        />
        <div className="mt-2.5 flex items-center justify-between gap-3">
          <span className="tiny-meta tabular-nums">{content.length} / {CONTENT_MAX}</span>
          <button
            type="submit"
            className="action-button action-button-primary min-h-[32px] px-3 text-xs"
            disabled={submitting || !content.trim()}
          >
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            {submitting ? '发送中…' : '发送反馈'}
          </button>
        </div>
      </form>

      <div className="sett-row is-block">
        <span className="sett-lbl">我的反馈</span>

        {items === null ? (
          <p className="flex items-center justify-center gap-2 py-8 tiny-meta">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </p>
        ) : items.length === 0 ? (
          <p className="py-8 text-center tiny-meta">还没有反馈,有想法时随时写给我们</p>
        ) : (
          <div className="mt-1">
            {items.map((item) => {
              const status = STATUS_META[item.status] || STATUS_META.open;
              return (
                <article key={item.id} className="fb-row">
                  <div className="fb-row-head">
                    <span className="micro-label text-slate-500">{CATEGORY_LABELS[item.category] || '其他'}</span>
                    <time className="tiny-meta" dateTime={item.created_at} title={formatDateTime(item.created_at)}>
                      {formatRelativeTime(item.created_at, '—')}
                    </time>
                    <span className={`stamp ${status.stamp}`}>{status.label}</span>
                    {item.status === 'open' ? (
                      <>
                        <span className="ml-auto" />
                        <button
                          type="button"
                          className="fb-textbtn is-danger"
                          onClick={() => handleWithdraw(item.id)}
                          disabled={withdrawingId === item.id}
                        >
                          {withdrawingId === item.id
                            ? <Loader2 className="animate-spin" />
                            : <Undo2 />}
                          撤回
                        </button>
                      </>
                    ) : null}
                  </div>
                  <p className="fb-row-body">{item.content}</p>
                  {item.admin_note ? (
                    <div className="fb-reply">
                      <span className="micro-label fb-reply-label">回复</span>
                      {item.admin_note}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
