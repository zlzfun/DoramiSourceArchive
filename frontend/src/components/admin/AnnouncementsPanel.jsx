// 公告管理(v3.18 互通波,运维管理 → 消息):撰写 → 满宽真实预览 → 发布;
// 已发列表 hairline 行(渲染后摘要,不显 markdown 源码)+ 启停/删除 + 触达计数。
// 预览复用读者横幅的 AnnouncementCard,所见即读者所得。
import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Info, Loader2, Megaphone, Power, Send, Trash2 } from 'lucide-react';
import {
  fetchAdminAnnouncements,
  createAnnouncement,
  toggleAnnouncement,
  deleteAnnouncement,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { AnnouncementCard } from '../AnnouncementBanner';
import { stripAnnouncementMarkup } from '../../utils/announcementText';
import { formatStamp } from './adminUtils';

const CONTENT_MAX = 2000;
const TITLE_MAX = 200;

const LEVELS = [
  ['info', '通知'],
  ['accent', '强调'],
  ['warning', '警示'],
];

const LEVEL_ICONS = { info: Info, accent: Megaphone, warning: AlertTriangle };

export default function AnnouncementsPanel({ showToast }) {
  const confirm = useConfirm();
  const [items, setItems] = useState(null); // null = 加载中
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [level, setLevel] = useState('info');
  const [publishing, setPublishing] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    fetchAdminAnnouncements()
      .then((res) => setItems(Array.isArray(res?.items) ? res.items : []))
      .catch((error) => { setItems([]); showToast(error.message, 'error'); });
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const handlePublish = async (event) => {
    event.preventDefault();
    if (!content.trim() || publishing) return;
    setPublishing(true);
    try {
      await createAnnouncement({ title: title.trim(), content: content.trim(), level });
      showToast('已发布公告', 'success');
      setTitle('');
      setContent('');
      setLevel('info');
      load();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setPublishing(false);
    }
  };

  const handleToggle = async (item) => {
    setBusyId(item.id);
    try {
      await toggleAnnouncement(item.id);
      showToast(item.is_active ? '已下线公告' : '已重新上线公告', 'success');
      load();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item) => {
    const name = item.title || stripAnnouncementMarkup(item.content).slice(0, 20);
    if (!(await confirm(`删除公告「${name}」?读者将不再看到它,且不可恢复。`))) return;
    setBusyId(item.id);
    try {
      await deleteAnnouncement(item.id);
      showToast('已删除公告', 'success');
      load();
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="surface-card card-pad">
      <div className="card-head"><span className="card-title">公告管理</span></div>

      <form className="ann-composer" onSubmit={handlePublish}>
        <input
          className="form-input"
          value={title}
          maxLength={TITLE_MAX}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="标题,可空"
          aria-label="公告标题"
        />
        <textarea
          className="form-input resize-y"
          rows={3}
          value={content}
          maxLength={CONTENT_MAX}
          onChange={(e) => setContent(e.target.value)}
          placeholder="公告正文,支持 **加粗** 与 [文字](链接) 写法"
          aria-label="公告正文"
        />
        <div className="ann-composer-tools">
          <div className="mini-seg" role="group" aria-label="公告档位">
            {LEVELS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`mini-seg-btn ${level === value ? 'is-on' : ''}`}
                aria-pressed={level === value}
                onClick={() => setLevel(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="ann-composer-spacer" />
          <span className="tiny-meta ann-charcount">{content.length} / {CONTENT_MAX}</span>
          <button
            type="submit"
            className="action-button action-button-primary min-h-[32px] px-3 text-xs"
            disabled={publishing || !content.trim()}
          >
            {publishing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            {publishing ? '发布中…' : '发布公告'}
          </button>
        </div>

        {content.trim() ? (
          <div>
            <div className="micro-label mb-1.5 text-slate-500">读者将看到</div>
            <div className="ann-preview">
              <AnnouncementCard item={{ id: 'preview', title: title.trim(), content: content.trim(), level }} />
            </div>
          </div>
        ) : null}
      </form>

      <div className="ann-mgr-list">
        {items === null ? (
          <p className="flex items-center justify-center gap-2 py-8 tiny-meta">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </p>
        ) : items.length === 0 ? (
          <p className="py-8 text-center tiny-meta">还没有公告,用上方表单发布第一条</p>
        ) : (
          items.map((item) => {
            const summary = stripAnnouncementMarkup(item.content);
            const levelKey = LEVEL_ICONS[item.level] ? item.level : 'info';
            const LevelIcon = LEVEL_ICONS[levelKey];
            return (
              <div key={item.id} className={`ann-mgr-row ${item.is_active ? '' : 'is-off'}`}>
                <span className={`ann-level-${levelKey}`}>
                  <LevelIcon className="ann-icon" aria-hidden="true" />
                </span>
                <div className="ann-mgr-main">
                  <div className="ann-mgr-text" title={summary}>
                    {item.title ? <span className="ann-title">{item.title}</span> : null}
                    {summary}
                  </div>
                  <div className="ann-mgr-meta">
                    <span className="tiny-meta">{formatStamp(item.created_at)}</span>
                    <span className="tiny-meta">·</span>
                    <span className="tiny-meta">{item.dismiss_count || 0} 人已关闭</span>
                  </div>
                </div>
                {item.is_active
                  ? <span className="stamp stamp-ok">展示中</span>
                  : <span className="stamp stamp-idle">已下线</span>}
                <div className="ann-mgr-acts">
                  <button
                    type="button"
                    className="rowact-btn"
                    onClick={() => handleToggle(item)}
                    disabled={busyId === item.id}
                    title={item.is_active ? '下线公告' : '重新上线'}
                    aria-label={item.is_active ? '下线公告' : '重新上线公告'}
                  >
                    <Power />
                  </button>
                  <button
                    type="button"
                    className="rowact-btn is-danger"
                    onClick={() => handleDelete(item)}
                    disabled={busyId === item.id}
                    title="删除公告"
                    aria-label="删除公告"
                  >
                    <Trash2 />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
