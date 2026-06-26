import { FileText, Link as LinkIcon, Calendar, Database, Box, ExternalLink, Edit2, Save, X, AlertCircle } from 'lucide-react';
import Modal from './Modal';

export default function ArticleDetailModal({ isOpen, data, isEditing, isLoading = false, getFetcherName, canEdit = true, onClose, onToggleEdit, onSave }) {
  if (!data) return null;

  const hasFullDetail = Object.prototype.hasOwnProperty.call(data, 'content') && data.extensions_json !== undefined;
  const canToggleEdit = canEdit && hasFullDetail && !isLoading;

  const handleSave = () => {
    onSave(data.id, {
      title: document.getElementById('edit-title').value,
      source_url: document.getElementById('edit-url').value,
      content: document.getElementById('edit-content').value,
      extensions_json: document.getElementById('edit-extensions').value,
    });
  };

  return (
    <Modal open={isOpen} onClose={onClose} size="4xl">
        <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex justify-between items-center bg-[var(--dorami-well)]">
          <div className="flex items-center space-x-3">
            <h3 className="card-title">数据全景档案</h3>
            <span className="data-chip">{data.content_type}</span>
          </div>
          <div className="flex items-center space-x-2">
            {canEdit && (
              <button onClick={onToggleEdit} disabled={!canToggleEdit} className={`action-button min-h-[34px] px-3 text-xs ${isEditing ? 'action-button-danger' : 'action-button-quiet'}`}>
                {isEditing ? <X /> : <Edit2 />}
                {isLoading ? '加载中' : isEditing ? '取消编辑' : '进入编辑模式'}
              </button>
            )}
            <button onClick={onClose} className="icon-button"><X className="w-5 h-5" /></button>
          </div>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-5 bg-[var(--dorami-surface)]">
          <div>
            <label className="form-label flex items-center"><FileText className="w-3.5 h-3.5 mr-1" /> 文章标题</label>
            {isEditing ? (
              <input type="text" defaultValue={data.title} id="edit-title" className="form-input" />
            ) : <div className="text-xl font-bold text-[var(--dorami-ink)] leading-snug">{data.title}</div>}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="form-label flex items-center"><LinkIcon className="w-3.5 h-3.5 mr-1" /> 原始来源链接 (URL)</label>
              {isEditing ? (
                <input type="text" defaultValue={data.source_url} id="edit-url" className="form-input" />
              ) : (
                data.source_url ?
                  <a href={data.source_url} target="_blank" rel="noreferrer" className="text-sm font-medium text-[var(--dorami-blue)] hover:text-[var(--dorami-accent-ink)] flex items-center break-all"><ExternalLink className="w-3.5 h-3.5 mr-1 shrink-0" /> {data.source_url}</a>
                  : <span className="text-sm text-slate-500">无链接</span>
              )}
            </div>
            <div>
              <label className="form-label flex items-center"><Calendar className="w-3.5 h-3.5 mr-1" /> 来源节点与收录时间</label>
              <div className="text-sm font-medium text-slate-700 flex items-center space-x-2">
                <span className="bg-[var(--dorami-soft)] px-2 py-0.5 rounded text-slate-500">{getFetcherName(data.source_id)}</span>
                <span className="text-slate-500">|</span>
                <span className="font-mono">{data.fetched_date?.replace('T', ' ').substring(0, 19)}</span>
              </div>
            </div>
          </div>

          <div>
            <label className="form-label flex items-center"><Database className="w-3.5 h-3.5 mr-1" /> 正文核心/摘要 (用于向量检索)</label>
            {isEditing ? (
              <textarea defaultValue={data.content} id="edit-content" rows="8" className="form-input leading-relaxed" />
            ) : <div className="text-sm bg-[var(--dorami-soft)] p-4 rounded-[var(--r-card)] border border-[var(--dorami-border)] whitespace-pre-wrap leading-relaxed text-slate-700 shadow-inner max-h-64 overflow-y-auto">{isLoading ? '正在加载全文…' : (data.content || '无正文内容')}</div>}
          </div>

          <div>
            <label className="form-label flex items-center"><Box className="w-3.5 h-3.5 mr-1" /> 扩展元数据 (Extensions JSON)</label>
            {isEditing ? (
              <textarea defaultValue={data.extensions_json} id="edit-extensions" rows="6" className="form-input font-mono text-xs" />
            ) : <pre className="text-xs bg-slate-800 dark:bg-slate-900 text-emerald-400 p-4 rounded-[var(--r-card)] overflow-x-auto shadow-inner">{isLoading ? '正在加载元数据…' : JSON.stringify(JSON.parse(data.extensions_json || '{}'), null, 2)}</pre>}
          </div>
        </div>

        {isEditing && (
          <div className="p-4 bg-[var(--dorami-soft)] border-t border-[var(--dorami-border)] flex justify-end space-x-3">
            <span className="text-xs text-amber-600 flex items-center mr-auto px-2"><AlertCircle className="w-3.5 h-3.5 mr-1" /> 修改内容后系统将自动抹除旧的向量索引，需重新构建。</span>
            <button onClick={onToggleEdit} className="action-button action-button-quiet">取消</button>
            <button onClick={handleSave} className="action-button action-button-primary">
              <Save /> 确认保存修改
            </button>
          </div>
        )}
    </Modal>
  );
}
