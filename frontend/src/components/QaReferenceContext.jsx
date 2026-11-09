import { useEffect, useRef, useState } from 'react';
import { fetchQaReferenceContext } from '../api';

// Each opening rechecks current permissions/publication. Closing discards text.
export default function QaReferenceContext({ source }) {
  const locations = source.reference_locations || [];
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState({});
  const focusRef = useRef(null);
  const contextRef = useRef(null);
  const token = locations[index]?.token;
  useEffect(() => {
    if (!open || !token) return undefined;
    const controller = new AbortController();
    setState({ loading: true });
    fetchQaReferenceContext(source.id, token, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setState({ data }); })
      .catch((error) => { if (!controller.signal.aborted) setState({ error: error.message }); });
    return () => controller.abort();
  }, [open, token, source.id, retry]);
  useEffect(() => {
    if (state.data && focusRef.current && contextRef.current) {
      contextRef.current.scrollTop = Math.max(0, focusRef.current.offsetTop - contextRef.current.offsetTop - 48);
    }
  }, [state.data]);
  if (!locations.length) return null;
  return (
    <div className="reader-ai-context">
      <button type="button" className="reader-ai-context-toggle tiny-meta" aria-expanded={open} onClick={() => {
        setOpen((current) => !current);
        setState({});
        // Native details coalesces toggle events; an explicit generation also
        // revalidates when rapid close/open ends at the same boolean state.
        setRetry((current) => current + 1);
      }}>{open ? '收起前后文' : '查看前后文'}</button>
      {open && <>
        <p className="tiny-meta">{locations[index]?.origin === 'selected'
          ? '定位回答选用的片段，可结合前后文核对。'
          : '从参考片段开头定位，可继续查看完整原文。'}</p>
        {locations.length > 1 && <div className="reader-ai-context-nav tiny-meta">
          <button type="button" disabled={index === 0 || state.loading} onClick={() => { setState({ loading: true }); setIndex(index - 1); }}>上一处</button>
          <span>{index + 1} / {locations.length}</span>
          <button type="button" disabled={index === locations.length - 1 || state.loading} onClick={() => { setState({ loading: true }); setIndex(index + 1); }}>下一处</button>
        </div>}
        {state.loading && <p className="tiny-meta" role="status">正在定位原文…</p>}
        {state.error && <div role="alert"><p className="tiny-meta">{state.error}</p>
          <button type="button" className="reader-ai-evidence-toggle tiny-meta" onClick={() => setRetry(retry + 1)}>重新加载</button></div>}
        {state.data && <div className="reader-ai-context-copy" ref={contextRef} tabIndex={0} role="region" aria-label="相关片段及前后文">
          {state.data.has_before && <p className="tiny-meta">前面还有内容</p>}
          <div>{state.data.before}</div>
          <div className="reader-ai-context-focus" ref={focusRef}><span className="tiny-meta">相关片段</span><div>{state.data.focus}</div></div>
          <div>{state.data.after}</div>
          {state.data.has_after && <p className="tiny-meta">后面还有内容，可打开完整原文继续阅读</p>}
        </div>}
      </>}
    </div>
  );
}
