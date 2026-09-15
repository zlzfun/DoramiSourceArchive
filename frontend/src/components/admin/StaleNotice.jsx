// 旧快照仍在时的非阻断失败提示(归并稿 P1 #7 / codex R2-P1-2):loader 保留上次数据继续渲染,
// 同时在对应区头 / 卡头 / 表头就地显示「刷新失败 · 原因 · 重试」,不清空已有数据;
// 404(未接入)同形只换章。无快照时调用方仍用整块错误 / 空态,这里不负责。
export default function StaleNotice({ status, error = '', onRetry, label = '' }) {
  if (status !== 'error' && status !== 'unavailable') return null;
  const unavailable = status === 'unavailable';
  return (
    <span className="stale-notice" role="alert">
      <span className={`stamp ${unavailable ? 'stamp-warn' : 'stamp-bad'}`}>
        {unavailable ? `${label}未接入` : `${label}刷新失败`}
      </span>
      <span className="tiny-meta" title={error || undefined}>
        {error}
        {onRetry && <> · <button type="button" className="kpi-sub-link" onClick={onRetry}>重试</button></>}
      </span>
    </span>
  );
}
