import { useEffect, useState } from 'react';
import { RefreshCw, Zap } from 'lucide-react';
import { fetchVectorStats, getAutoVectorize, reindexAll, setAutoVectorize } from '../../api';
import { runAction } from '../../utils/runAction';
import { useConfirm } from '../../hooks/useConfirm';
import { SectionHeading, FieldRow } from './SectionPrimitives';

// 向量雷达（向量库管理，仅管理员）：自动向量化开关 + 全量重索引 + 向量库统计。
export default function VectorSection({ showToast }) {
  const confirm = useConfirm();
  const [autoVec, setAutoVec] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let alive = true;
    getAutoVectorize().then(d => { if (alive) setAutoVec(Boolean(d.enabled)); }).catch(() => {});
    fetchVectorStats().then(d => { if (alive) setStats(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const handleToggleAutoVec = async () => {
    const next = !autoVec;
    setAutoVec(next);
    try {
      await setAutoVectorize(next);
      showToast(next ? '已开启：抓取后自动向量化' : '已关闭自动向量化', 'success');
    } catch (error) {
      setAutoVec(!next);
      showToast(error.message || '设置失败', 'error');
    }
  };

  const handleReindex = async () => {
    if (!(await confirm('全量重索引将清空并重建整个向量库（适用于更换 Embedding 模型）。确认继续？'))) return;
    await runAction(() => reindexAll(), {
      showToast,
      success: (data) => `全量重索引完成：${data.total_reindexed}/${data.total_articles} 篇`,
      error: '重索引失败',
      setLoading: setReindexing,
      onSuccess: () => { fetchVectorStats().then(setStats).catch(() => {}); },
    });
  };

  return (
    <div>
      <SectionHeading title="向量雷达" hint="向量库是全局共享的，构建与重索引会影响所有订阅者，仅管理员可操作。" />

      <div className="surface-card rounded-[var(--r-card)] p-4">
        <label className="flex cursor-pointer items-center justify-between gap-4">
          <span>
            <span className="block text-sm font-bold text-slate-700">抓取后自动向量化</span>
            <span className="tiny-meta">开启后，每次抓取入库的新文章会自动写入向量库。</span>
          </span>
          <input type="checkbox" checked={autoVec} onChange={handleToggleAutoVec} className="h-5 w-5 shrink-0 rounded border-slate-300 text-indigo-600" />
        </label>
      </div>

      <div className="surface-card mt-4 rounded-[var(--r-card)] p-4">
        <div className="flex items-center justify-between gap-4">
          <span>
            <span className="block text-sm font-bold text-slate-700">全量重索引</span>
            <span className="tiny-meta">清空并重建整个向量库，更换 Embedding 模型后使用。</span>
          </span>
          <button onClick={handleReindex} disabled={reindexing} className="action-button action-button-secondary text-xs">
            {reindexing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />} 重索引
          </button>
        </div>
        <div className="mt-3 border-t border-[var(--dorami-border)] pt-3">
          <FieldRow label="向量块总数">{stats === null ? '…' : (stats.total_vectors ?? '—')}</FieldRow>
          <FieldRow label="Embedding 模型">
            <span className="font-mono text-xs">BAAI/bge-m3</span>
            <span className="tiny-meta ml-1">（默认，可经 LOCAL_MODEL_PATH 覆盖）</span>
          </FieldRow>
        </div>
      </div>
    </div>
  );
}
