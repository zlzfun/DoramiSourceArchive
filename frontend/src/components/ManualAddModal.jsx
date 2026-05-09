import { Plus, X } from 'lucide-react';

export default function ManualAddModal({ isOpen, uniqueContentTypes, uniqueSourceIds, onClose, onSubmit }) {
  if (!isOpen) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    onSubmit(Object.fromEntries(formData.entries()));
  };

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center animate-in fade-in p-4">
      <div className="bg-white rounded-3xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
          <h3 className="font-bold text-lg text-slate-800 flex items-center"><Plus className="w-5 h-5 mr-2 text-blue-600" /> 手工录入知识数据</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col overflow-hidden">
          <div className="p-6 overflow-y-auto flex-1 space-y-4">
            <div><label className="text-xs font-bold text-slate-500 mb-1 block">文章标题 *</label><input required name="title" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-bold text-slate-500 mb-1 block">结构类型 (Content Type) *</label>
                <input required name="content_type" placeholder="例如: tech_news" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" list="ct-list" />
                <datalist id="ct-list">{uniqueContentTypes.map(t => <option key={t} value={t} />)}</datalist>
              </div>
              <div>
                <label className="text-xs font-bold text-slate-500 mb-1 block">来源通道 (Source ID) *</label>
                <input required name="source_id" placeholder="例如: manual_entry" defaultValue="manual_entry" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" list="src-list" />
                <datalist id="src-list">{uniqueSourceIds.map(t => <option key={t} value={t} />)}</datalist>
              </div>
            </div>
            <div><label className="text-xs font-bold text-slate-500 mb-1 block">文章链接 (URL)</label><input name="source_url" type="url" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" /></div>
            <div><label className="text-xs font-bold text-slate-500 mb-1 block">发布时间 (ISO格式，留空为当前)</label><input name="publish_date" type="datetime-local" className="w-full p-2.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" /></div>
            <div><label className="text-xs font-bold text-slate-500 mb-1 block">核心正文/摘要</label><textarea required name="content" rows="4" className="w-full p-3 border border-slate-200 rounded-lg text-sm outline-none focus:border-blue-500" /></div>
            <div><label className="text-xs font-bold text-slate-500 mb-1 block">任意扩展元数据 (严格的 JSON 格式)</label><textarea name="extensions_json" defaultValue="{}" rows="4" className="w-full p-3 border border-slate-200 rounded-lg text-sm font-mono bg-slate-50 outline-none focus:border-blue-500" /></div>
          </div>
          <div className="p-5 bg-slate-50 border-t border-slate-200 flex justify-end">
            <button type="submit" className="px-6 py-2.5 bg-blue-600 text-white font-bold rounded-xl hover:bg-blue-700 shadow-md transition-all">确认写入数据库</button>
          </div>
        </form>
      </div>
    </div>
  );
}
