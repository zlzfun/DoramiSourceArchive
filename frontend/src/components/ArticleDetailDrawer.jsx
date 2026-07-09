import { X, Zap, Edit2, Trash2, ExternalLink, RefreshCw } from 'lucide-react';
import { contentTypeLabel } from '../utils/contentType';
import { excerptOf } from '../utils/readerText';

// 索引流水线：已收录 → 待处理 → 索引中 → 已入索引；failed/stale 在对应位置改字/改色。
const PIPELINE_STEPS = ['已收录', '待处理', '索引中', '已入索引'];
const PIPELINE_POS = { pending: 1, indexing: 2, indexed: 3, failed: 2, stale: 1 };

function IndexPipeline({ status }) {
  const pos = PIPELINE_POS[status] ?? 1;
  return (
    <div className="ledger-pipeline" role="img" aria-label={`索引状态：${status}`}>
      {PIPELINE_STEPS.map((step, i) => {
        let cls = i < pos ? 'is-done' : i === pos ? 'is-now' : '';
        let label = step;
        if (status === 'failed' && i === pos) { cls = 'is-now is-err'; label = '失败'; }
        if (status === 'stale' && i === pos) label = '陈旧';
        return <span key={step} className={`ledger-pipeline-step ${cls}`}>{label}</span>;
      })}
    </div>
  );
}

const fmtTime = (value) => (value ? value.replace('T', ' ').substring(0, 16) : '—');

function prettyExtensions(raw, loading) {
  if (loading) return '正在加载元数据…';
  if (raw === undefined) return '—';
  try {
    return JSON.stringify(JSON.parse(raw || '{}'), null, 2);
  } catch {
    return String(raw);
  }
}

/**
 * 台账条目详情抽屉（右缘滑入）：承载「查看 + 快捷操作」。
 * 编辑等复杂操作仍由外层 ArticleDetailModal 承接（onEdit 打开编辑模态）。
 */
export default function ArticleDetailDrawer({
  open,
  article,
  loading = false,
  ragEnabled = false,
  canManage = true,
  getFetcherName,
  vectorizing = false,
  onClose,
  onVectorize,
  onEdit,
  onDelete,
}) {
  const status = article
    ? (article.index_status || (article.is_vectorized ? 'indexed' : 'pending'))
    : 'pending';
  const content = article ? (article.content ?? article.content_preview ?? '') : '';
  const chars = content ? content.replace(/\s+/g, '').length : 0;

  return (
    <>
      <div
        className={`ledger-scrim ${open ? 'is-open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={`ledger-drawer ${open ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="条目详情"
        aria-hidden={!open}
      >
        {article && (
          <>
            <div className="ledger-drawer-head">
              <h2 className="ledger-drawer-title">{article.title}</h2>
              <button type="button" className="icon-button shrink-0" onClick={onClose} aria-label="关闭详情">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="ledger-drawer-body">
              {ragEnabled && (
                <section>
                  <h3 className="micro-label mb-2">索引流水线</h3>
                  <IndexPipeline status={status} />
                </section>
              )}

              <dl className="ledger-kv">
                <dt>来源</dt>
                <dd>
                  {getFetcherName?.(article.source_id) || article.source_id}
                  <span className="ledger-kv-mono"> · {article.source_id}</span>
                </dd>
                <dt>类型</dt>
                <dd>{contentTypeLabel(article.content_type, article.content_type)}</dd>
                <dt>发布时间</dt>
                <dd className="ledger-kv-mono">{fmtTime(article.publish_date)}</dd>
                <dt>收录时间</dt>
                <dd className="ledger-kv-mono">{fmtTime(article.fetched_date)}</dd>
                <dt>原文链接</dt>
                <dd className="ledger-kv-mono break-all">
                  {article.source_url ? (
                    <a href={article.source_url} target="_blank" rel="noreferrer" className="ledger-kv-link">
                      <ExternalLink className="h-3.5 w-3.5 shrink-0" /> {article.source_url}
                    </a>
                  ) : '—（站内生成）'}
                </dd>
                <dt>正文字数</dt>
                <dd className="ledger-kv-mono">{loading ? '统计中…' : (chars ? `${chars.toLocaleString()} 字` : '无正文')}</dd>
              </dl>

              <section>
                <h3 className="micro-label mb-2">正文摘录</h3>
                <p className="ledger-excerpt">
                  {loading ? '正在加载全文…' : (excerptOf(content, 480) || '无正文内容')}
                </p>
              </section>

              <section>
                <h3 className="micro-label mb-2">扩展字段 extensions</h3>
                <pre className="ledger-extjson">{prettyExtensions(article.extensions_json, loading)}</pre>
              </section>
            </div>

            {canManage && (
              <div className="ledger-drawer-foot">
                {ragEnabled && (
                  <button
                    type="button"
                    onClick={() => onVectorize?.(article)}
                    disabled={vectorizing || status === 'indexing'}
                    className="action-button action-button-secondary min-h-[36px] px-3 text-xs"
                  >
                    {vectorizing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />}
                    {status === 'indexed' ? '重建向量' : '立即向量化'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onEdit?.(article)}
                  className="action-button action-button-quiet min-h-[36px] px-3 text-xs"
                >
                  <Edit2 className="h-3.5 w-3.5" /> 编辑
                </button>
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={() => onDelete?.(article)}
                  className="action-button action-button-danger min-h-[36px] px-3 text-xs"
                >
                  <Trash2 className="h-3.5 w-3.5" /> 删除
                </button>
              </div>
            )}
          </>
        )}
      </aside>
    </>
  );
}
