import { syncBytes } from '../../utils/syncProgress';
import { Loader2 } from 'lucide-react';

const STAGES = {
  sources: '来源目录', taxonomy: '分类目录', articles: '文章', analyses: '评分与摘要',
  media: '图片', podcast_texts: '播客文本', podcast_audio: '精简音频', source_states: '同步状态',
};

export default function RemoteSyncProgress({ job }) {
  const progress = job.progress;
  const running = job.status === 'running' || job.status === 'queued';
  const binary = ['media', 'podcast_audio'].includes(progress?.stream);
  const percent = !progress && job.total ? Math.min(100, Math.round(job.processed / job.total * 100)) : null;
  const stage = STAGES[progress?.stream] || '内容';

  return (
    <div className="space-y-3" role="status" aria-live="polite" aria-atomic="true">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="body-text flex items-center gap-1.5">
          {running && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          {progress ? (running ? `正在同步${stage}…` : `${stage}同步已停止`) : '正在同步…'}
        </span>
        <span className="tiny-meta tabular-nums">
          {percent != null ? `${job.processed} / ${job.total}（${percent}%）` : `累计已处理 ${job.processed || 0} 条`}
        </span>
      </div>
      {binary ? (
        <div className="grid grid-cols-3 gap-3 tabular-nums">
          <div><span className="tiny-meta block">本阶段已处理</span><b className="body-text">{progress.stream_processed || 0} 条</b></div>
          <div><span className="tiny-meta block">本地复用</span><b className="body-text">{progress.reused || 0} 个</b><span className="tiny-meta block">{syncBytes(progress.reused_bytes)}</span></div>
          <div><span className="tiny-meta block">下载完成</span><b className="body-text">{progress.downloaded || 0} 个</b><span className="tiny-meta block">{syncBytes(progress.downloaded_bytes)}</span></div>
        </div>
      ) : progress ? (
        <p className="tiny-meta tabular-nums">本阶段已处理 {progress.stream_processed || 0} 条</p>
      ) : null}
      {percent != null && (
        <div className="run-progress-track">
          <div className="run-progress-fill" style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}
