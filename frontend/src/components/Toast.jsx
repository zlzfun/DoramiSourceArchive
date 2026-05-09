import { AlertCircle } from 'lucide-react';

export default function Toast({ show, message, type = 'info' }) {
  if (!show) return null;

  return (
    <div className={`fixed top-24 right-8 px-5 py-4 rounded-xl shadow-2xl flex items-center space-x-3 z-50 text-white transition-all transform animate-in fade-in slide-in-from-top-4 ${type === 'error' ? 'bg-red-500' : 'bg-slate-800'}`}>
      <AlertCircle className="w-5 h-5" />
      <span className="text-sm font-medium">{message}</span>
    </div>
  );
}
