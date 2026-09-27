import { X } from 'lucide-react';
import { dismissUpdate, usePwa } from '../pwa';

export default function PwaStatus() {
  const { online, updateAvailable } = usePwa();
  if (online && !updateAvailable) return null;
  return (
    <aside className="pwa-status" role="status" aria-label={online ? '版本更新' : '网络状态'}>
      <span>{online ? '新版本已就绪' : '网络已断开，联网后可继续阅读'}</span>
      {online && <>
        <button type="button" onClick={() => window.location.reload()}>刷新</button>
        <button type="button" onClick={dismissUpdate} aria-label="稍后更新"><X size={16} /></button>
      </>}
    </aside>
  );
}
