// 反馈收件箱(v3.18 互通波,运维管理 → 消息):静止态 = 干净的扫读列表
// (用户/分类/时间/状态章/正文/已有回复);点「处理」就地展开处理区(状态点击即存 +
// 回复输入),一次只展开一条——行内不常驻控件,合静默仪器纪律。
import { useCallback, useEffect, useState } from 'react';
import { Loader2, PenLine } from 'lucide-react';
import { fetchAdminFeedback, updateFeedbackStatus } from '../../api';
import { formatStamp } from './adminUtils';
import Pager from './Pager';

// 规模化波:服务端分页,前端只持有当前页(反馈行是高卡片,页容量取小)。
const FEEDBACK_PAGE_SIZE = 10;

const FILTERS = [
  ['all', '全部'],
  ['open', '待处理'],
  ['in_progress', '处理中'],
  ['resolved', '已完成'],
  ['dismissed', '已关闭'],
];

const STATUS_OPTIONS = FILTERS.slice(1);
const STATUS_LABELS = Object.fromEntries(STATUS_OPTIONS);
const STATUS_STAMPS = { open: 'stamp-run', in_progress: 'stamp-warn', resolved: 'stamp-ok', dismissed: 'stamp-idle' };
const CATEGORY_LABELS = { source_request: '想要新内容', bug: '问题反馈', suggestion: '功能建议', other: '其他' };

export default function FeedbackInboxPanel({ showToast }) {
  const [filter, setFilter] = useState('all');
  const [items, setItems] = useState(null); // null = 加载中
  const [counts, setCounts] = useState(null);
  const [total, setTotal] = useState(0); // 当前过滤下总条数(服务端给)
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState(null); // 展开处理区的那一条
  const [draftNote, setDraftNote] = useState('');
  const [busyId, setBusyId] = useState(null);

  const load = useCallback((status, targetPage) => {
    fetchAdminFeedback(status === 'all' ? null : status, {
      skip: (targetPage - 1) * FEEDBACK_PAGE_SIZE,
      limit: FEEDBACK_PAGE_SIZE,
    })
      .then((data) => {
        setItems(Array.isArray(data?.items) ? data.items : []);
        setCounts(data?.counts || null);
        setTotal(Number(data?.total) || 0);
      })
      .catch((error) => { setItems([]); showToast(error.message, 'error'); });
  }, [showToast]);

  // 切过滤归位第一页;翻页/首载取对应页。
  useEffect(() => { setPage(1); }, [filter]);
  useEffect(() => { setItems(null); load(filter, page); }, [filter, page, load]);

  const totalPages = Math.max(1, Math.ceil(total / FEEDBACK_PAGE_SIZE));
  // 数据收缩(处理完最后一页的条目等)后当前页越界时回落到末页。
  useEffect(() => {
    if (items !== null && page > totalPages) setPage(totalPages);
  }, [items, page, totalPages]);

  const toggleProcess = (item) => {
    if (openId === item.id) {
      setOpenId(null);
      return;
    }
    setOpenId(item.id);
    setDraftNote(item.admin_note || '');
  };

  const handleStatus = async (item, status) => {
    if (status === item.status || busyId) return;
    setBusyId(item.id);
    try {
      await updateFeedbackStatus(item.id, status);
      showToast(`已将反馈标为「${STATUS_LABELS[status]}」`, 'success');
      load(filter, page);
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleReply = async (item) => {
    setBusyId(item.id);
    try {
      await updateFeedbackStatus(item.id, item.status, draftNote.trim());
      showToast(`已回复 ${item.owner_username}`, 'success');
      load(filter, page);
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="surface-card card-pad">
      <div className="card-head">
        <span className="card-title">反馈收件箱</span>
        <div className="ml-auto flex flex-wrap items-center gap-2.5">
          {counts ? (
            <span className="tiny-meta whitespace-nowrap">待处理 {counts.open ?? 0} · 共 {counts.total ?? 0}</span>
          ) : null}
          <div className="mini-seg" role="group" aria-label="按状态筛选反馈">
            {FILTERS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`mini-seg-btn ${filter === value ? 'is-on' : ''}`}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-2">
        {items === null ? (
          <p className="flex items-center justify-center gap-2 py-8 tiny-meta">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </p>
        ) : items.length === 0 ? (
          <p className="py-8 text-center tiny-meta">
            {filter === 'all' ? '还没有收到反馈,读者提交后会出现在这里' : `没有「${STATUS_LABELS[filter]}」状态的反馈`}
          </p>
        ) : (
          items.map((item) => {
            const expanded = openId === item.id;
            const busy = busyId === item.id;
            return (
              <article key={item.id} className="fb-row">
                <div className="fb-row-head">
                  <span className="acct-user">
                    <span className="acct-avatar">{(item.owner_username || '?').charAt(0).toUpperCase()}</span>
                    <span className="acct-name">{item.owner_username}</span>
                  </span>
                  <span className="micro-label text-slate-500">{CATEGORY_LABELS[item.category] || '其他'}</span>
                  <time className="tiny-meta tabular-nums" dateTime={item.created_at}>{formatStamp(item.created_at)}</time>
                  <span className={`stamp ${STATUS_STAMPS[item.status] || 'stamp-idle'}`}>
                    {STATUS_LABELS[item.status] || item.status}
                  </span>
                  <span className="ml-auto" />
                  <button
                    type="button"
                    className="fb-textbtn"
                    aria-expanded={expanded}
                    onClick={() => toggleProcess(item)}
                  >
                    <PenLine /> {expanded ? '收起' : '处理'}
                  </button>
                </div>

                <p className="fb-row-body">{item.content}</p>

                {!expanded && item.admin_note ? (
                  <div className="fb-reply">
                    <span className="micro-label fb-reply-label">已回复</span>
                    {item.admin_note}
                  </div>
                ) : null}

                {expanded ? (
                  <div className="fb-process">
                    <div className="mini-seg" role="group" aria-label="流转反馈状态">
                      {STATUS_OPTIONS.map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          className={`mini-seg-btn ${item.status === value ? 'is-on' : ''}`}
                          aria-pressed={item.status === value}
                          disabled={busy}
                          onClick={() => handleStatus(item, value)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <input
                      className="form-input form-input-inline min-w-52 flex-1"
                      value={draftNote}
                      maxLength={2000}
                      onChange={(e) => setDraftNote(e.target.value)}
                      placeholder="写给读者的回复"
                      aria-label={`回复 ${item.owner_username}`}
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
                      onClick={() => handleReply(item)}
                      disabled={busy || draftNote.trim() === (item.admin_note || '')}
                    >
                      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      保存回复
                    </button>
                  </div>
                ) : null}
              </article>
            );
          })
        )}
      </div>

      {items !== null && totalPages > 1 && (
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] pt-2.5">
          <span className="tiny-meta">
            共 {total} 条 · 第 {(page - 1) * FEEDBACK_PAGE_SIZE + 1}–{Math.min(page * FEEDBACK_PAGE_SIZE, total)} 条
          </span>
          <Pager page={page} totalPages={totalPages} onPage={setPage} />
        </div>
      )}
    </section>
  );
}
