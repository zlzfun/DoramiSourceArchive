export default function TranslationProgress({ job, pollingError, onResume, onCancel }) {
  if (!job || job.status === 'succeeded') return null;
  const active = job.status === 'queued' || job.status === 'running';
  return (
    <div className="reader-translation-progress tiny-meta" role="status" aria-live="polite">
      <span className="tabular-nums">
        {job.status === 'queued' ? '等待翻译' : active ? '后台翻译中' : job.status === 'cancelled' ? '翻译已取消' : '翻译已暂停'}
        {' · '}{job.completed} / {job.total} 片段
      </span>
      {active && <span>可离开页面，返回后查看进度</span>}
      {job.error && <span>{job.error}</span>}
      {job.can_resume && <span>进度保留 7 天；超过 3 篇时清理最早暂停进度</span>}
      {pollingError && <span>进度暂未更新，正在重连</span>}
      {job.can_resume && <button type="button" className="reader-pane-act" onClick={onResume}>继续翻译</button>}
      {job.can_cancel && <button type="button" className="reader-pane-act" onClick={onCancel}>取消翻译</button>}
    </div>
  );
}
