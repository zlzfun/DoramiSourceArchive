import { X, Edit2, Trash2, ExternalLink } from 'lucide-react';
import { contentTypeLabel } from '../utils/contentType';
import { excerptOf } from '../utils/readerText';
import {
  analysisStatusMeta,
  contentGenreLabel,
  displayAnalysisTags,
  hasReadableAnalysis,
  qualityScoreText,
} from '../utils/analysis';
import AnalysisTagChip from './AnalysisTagChip';

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
  canManage = true,
  getFetcherName,
  onClose,
  onEdit,
  onDelete,
  onTemporaryTagSearch,
}) {
  const content = article ? (article.content ?? article.content_preview ?? '') : '';
  const chars = content ? content.replace(/\s+/g, '').length : 0;
  const hasAnalysis = hasReadableAnalysis(article);
  const analysisStatus = analysisStatusMeta(article, { includeTerminal: true });
  const score = qualityScoreText(article?.quality_score);
  const analysisTags = displayAnalysisTags(article);

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
                <h3 className="micro-label mb-2">智能分析</h3>
                {hasAnalysis ? (
                  <div className="reader-analysis-summary">
                    <div className="reader-analysis-top">
                      {score && <span className="reader-analysis-score"><strong>{score}</strong><small>内容价值分</small></span>}
                      {(analysisTags.length > 0 || article.content_genre) && <span className="reader-analysis-tags">
                        {analysisTags.map((tag, index) => (
                          <AnalysisTagChip key={`${tag.type || 'canonical'}-${tag.id || tag.code || tag.candidate_id || index}`} tag={tag} onTemporarySearch={onTemporaryTagSearch} />
                        ))}
                        {analysisTags.length === 0 && article.content_genre && <span className="reader-tag-chip">{contentGenreLabel(article.content_genre)}</span>}
                      </span>}
                      {analysisStatus && <span className={`stamp ${analysisStatus.cls}`} role="status">{analysisStatus.label}</span>}
                    </div>
                    {article.score_reason && <p>{article.score_reason}</p>}
                    {article.summary_zh && <p className="tiny-meta">{article.summary_zh}</p>}
                    <small>AI 评估用于辅助筛选，不代表事实保证或用户评分</small>
                  </div>
                ) : (
                  <p className="ledger-excerpt">
                    {analysisStatus
                      ? <span className={`stamp ${analysisStatus.cls}`} role="status">{analysisStatus.label}</span>
                      : '暂无可展示的智能分析结果'}
                  </p>
                )}
              </section>

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
                <button
                  type="button"
                  onClick={() => onEdit?.(article)}
                  className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
                >
                  <Edit2 className="h-3.5 w-3.5" /> 编辑
                </button>
                <span className="flex-1" />
                <button
                  type="button"
                  onClick={() => onDelete?.(article)}
                  className="action-button action-button-danger min-h-[32px] px-3 text-xs"
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
