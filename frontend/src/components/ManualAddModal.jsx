import { useEffect } from 'react';
import { Plus, X } from 'lucide-react';

export default function ManualAddModal({ isOpen, uniqueContentTypes, uniqueSourceIds, onClose, onSubmit }) {
  useEffect(() => {
    if (!isOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    onSubmit(Object.fromEntries(formData.entries()));
  };

  return (
    <div className="modal-overlay animate-in fade-in">
      <div className="modal-panel max-w-2xl">
        <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
          <h3 className="font-bold text-lg text-slate-800 flex items-center"><Plus className="w-5 h-5 mr-2 text-blue-600" /> 手工录入知识数据</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="p-6 overflow-y-auto flex-1 space-y-4">
            <label className="form-field"><span>文章标题 *</span><input required name="title" className="form-input" /></label>
            <div className="grid grid-cols-2 gap-4">
              <label className="form-field">
                <span>结构类型 (Content Type) *</span>
                <input required name="content_type" placeholder="例如: tech_news" className="form-input" list="ct-list" />
                <datalist id="ct-list">{uniqueContentTypes.map(t => <option key={t} value={t} />)}</datalist>
              </label>
              <label className="form-field">
                <span>来源通道 (Source ID) *</span>
                <input required name="source_id" placeholder="例如: manual_entry" defaultValue="manual_entry" className="form-input" list="src-list" />
                <datalist id="src-list">{uniqueSourceIds.map(t => <option key={t} value={t} />)}</datalist>
              </label>
            </div>
            <label className="form-field"><span>文章链接 (URL)</span><input name="source_url" type="url" className="form-input" /></label>
            <label className="form-field"><span>发布时间 (ISO格式，留空为当前)</span><input name="publish_date" type="datetime-local" className="form-input" /></label>
            <label className="form-field"><span>核心正文/摘要</span><textarea required name="content" rows="4" className="form-input" /></label>
            <label className="form-field"><span>任意扩展元数据 (严格的 JSON 格式)</span><textarea name="extensions_json" defaultValue="{}" rows="4" className="form-input font-mono" /></label>
          </div>
          <div className="p-5 bg-slate-50 border-t border-slate-200 flex justify-end">
            <button type="submit" className="action-button action-button-primary">确认写入数据库</button>
          </div>
        </form>
      </div>
    </div>
  );
}
