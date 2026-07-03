import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Loader2,
  Sparkles,
  Send,
  X,
  Check,
  Maximize2,
  Minimize2,
  SquarePen,
  ChevronDown,
} from 'lucide-react';
import ReaderMarkdown from './ReaderMarkdown';
import { askReaderAi } from '../api';

// 阅读器 AI 问答浮层：常态收起于右下角 FAB，点击展开。自持全部问答态（多轮线程 / 范围 /
// 面板尺寸），仅从父组件读 aiEnabled、activeArticle（决定「基于本文」是否可用）、showToast。
export default function ReaderAiPanel({ aiEnabled, activeArticle, showToast }) {
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const [aiPanelClosing, setAiPanelClosing] = useState(false);
  const [aiPanelLarge, setAiPanelLarge] = useState(() => localStorage.getItem('dorami_reader_ai_panel_large') === '1');
  const [qaScope, setQaScope] = useState('article');             // article | subscription
  const [qaScopeMenuOpen, setQaScopeMenuOpen] = useState(false);
  const qaScopeRef = useRef(null);
  const [qaInput, setQaInput] = useState('');
  const [qaThread, setQaThread] = useState([]);                  // {q, a, sources, error, pending}
  const [qaLoading, setQaLoading] = useState(false);

  // ── 问答（基于本文 / 基于我的订阅）──
  const handleAsk = useCallback(async () => {
    const q = qaInput.trim();
    if (!q || qaLoading) return;
    const scope = qaScope;
    const articleId = activeArticle?.id || null;
    if (scope === 'article' && !articleId) { showToast('请先从中间选择一篇文章', 'error'); return; }
    // 多轮：把此前已完成的问答展开成 user/assistant 历史（不含本轮，未完成/出错的轮次跳过）
    const history = qaThread
      .filter((m) => m.a && !m.error && !m.pending)
      .flatMap((m) => [{ role: 'user', content: m.q }, { role: 'assistant', content: m.a }]);
    setQaLoading(true);
    setQaThread((prev) => [...prev, { q, a: null, sources: [], pending: true }]);
    setQaInput('');
    try {
      const data = await askReaderAi({ question: q, scope, articleId, history });
      setQaThread((prev) => prev.map((m, i) => (
        i === prev.length - 1 ? { q, a: data.answer, sources: data.sources || [] } : m
      )));
    } catch (error) {
      setQaThread((prev) => prev.map((m, i) => (
        i === prev.length - 1 ? { q, a: null, error: error.message || '提问失败，请稍后重试' } : m
      )));
    } finally {
      setQaLoading(false);
    }
  }, [qaInput, qaLoading, qaScope, qaThread, activeArticle, showToast]);

  // 范围下拉：点击面板外区域收起
  useEffect(() => {
    if (!qaScopeMenuOpen) return undefined;
    const onPointerDown = (e) => {
      if (qaScopeRef.current && !qaScopeRef.current.contains(e.target)) setQaScopeMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [qaScopeMenuOpen]);

  // 发起新对话：清空多轮历史与输入（切换范围/点「新对话」时调用）
  const resetConversation = useCallback(() => {
    setQaThread([]);
    setQaInput('');
  }, []);

  // 未选中文章时「基于本文」无对应文章 → 自动回落到「基于我的订阅」（该项始终成立）。
  useEffect(() => {
    if (!activeArticle && qaScope === 'article') setQaScope('subscription');
  }, [activeArticle, qaScope]);

  // 关闭面板：先播放退场动画，动画结束再卸载（与 CSS .is-closing 的 180ms 对齐）
  const closeAiPanel = useCallback(() => {
    setQaScopeMenuOpen(false);
    setAiPanelClosing(true);
    window.setTimeout(() => {
      setAiPanelOpen(false);
      setAiPanelClosing(false);
    }, 180);
  }, []);

  if (!aiEnabled) return null;

  return (
    <>
      {!aiPanelOpen && (
        <button
          type="button"
          className="reader-ai-fab"
          onClick={() => setAiPanelOpen(true)}
          aria-label="问问哆啦美"
        >
          <Sparkles className="h-4 w-4" />
          <span className="reader-ai-fab-label">问问哆啦美</span>
        </button>
      )}
      {aiPanelOpen && (
        <aside className={`reader-ai-panel ${aiPanelLarge ? 'is-large' : ''} ${aiPanelClosing ? 'is-closing' : ''}`} role="dialog" aria-label="问问哆啦美">
          <header className="reader-ai-head">
            <span className="reader-ai-title">
              <Sparkles className="h-4 w-4" /> 问问哆啦美
            </span>
            <div className="reader-ai-head-actions">
              <button
                type="button"
                className="reader-ai-head-btn"
                onClick={resetConversation}
                disabled={qaThread.length === 0}
                aria-label="新对话"
                title="新对话"
              >
                <SquarePen className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="reader-ai-head-btn"
                onClick={() => setAiPanelLarge((prev) => {
                  const next = !prev;
                  localStorage.setItem('dorami_reader_ai_panel_large', next ? '1' : '0');
                  return next;
                })}
                aria-label={aiPanelLarge ? '还原大小' : '放大'}
                title={aiPanelLarge ? '还原大小' : '放大'}
              >
                {aiPanelLarge ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
              </button>
              <button
                type="button"
                className="reader-ai-head-btn"
                onClick={closeAiPanel}
                aria-label="收起"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </header>

          <div className="reader-ai-thread">
            {qaThread.map((m, i) => (
                <div key={i} className="reader-ai-turn">
                  <div className="reader-ai-q">{m.q}</div>
                  {m.pending ? (
                    <div className="reader-ai-a reader-ai-a-pending">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在思考…
                    </div>
                  ) : m.error ? (
                    <div className="reader-ai-a reader-ai-a-error">{m.error}</div>
                  ) : (
                    <div className="reader-ai-a markdown-body">
                      <ReaderMarkdown>{m.a || ''}</ReaderMarkdown>
                      {m.sources && m.sources.length > 0 && (
                        <div className="reader-ai-sources">
                          {m.sources.slice(0, 5).map((s, si) => (
                            s.source_url ? (
                              <a key={si} href={s.source_url} target="_blank" rel="noreferrer" className="reader-ai-source">
                                {s.title || s.source_id}
                              </a>
                            ) : (
                              <span key={si} className="reader-ai-source">{s.title || s.source_id}</span>
                            )
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
          </div>

          <div className="reader-ai-composer">
            <textarea
              className="reader-ai-input"
              rows={2}
              value={qaInput}
              placeholder={qaScope === 'article' ? '三句话总结这篇文章' : '最近有哪些值得关注的进展？'}
              onChange={(e) => setQaInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleAsk(); }
              }}
            />
            <div className="reader-ai-toolbar">
              <div className="reader-ai-scope" ref={qaScopeRef}>
                <button
                  type="button"
                  className="reader-ai-scope-trigger"
                  onClick={() => setQaScopeMenuOpen((o) => !o)}
                  aria-haspopup="listbox"
                  aria-expanded={qaScopeMenuOpen}
                >
                  {qaScope === 'article' ? '基于本文' : '基于我的订阅'}
                  <ChevronDown className={`h-3.5 w-3.5 reader-ai-scope-caret ${qaScopeMenuOpen ? 'is-open' : ''}`} />
                </button>
                {qaScopeMenuOpen && (
                  <div className="reader-ai-scope-menu" role="listbox">
                    {[
                      { id: 'article', label: '基于本文' },
                      { id: 'subscription', label: '基于我的订阅' },
                    ].map((opt) => {
                      // 「基于本文」需先选中一篇文章，未选时置灰不可选。
                      const disabled = opt.id === 'article' && !activeArticle;
                      return (
                      <button
                        key={opt.id}
                        type="button"
                        role="option"
                        aria-selected={qaScope === opt.id}
                        disabled={disabled}
                        title={disabled ? '先从中间选择一篇文章' : undefined}
                        className={`reader-ai-scope-option ${qaScope === opt.id ? 'is-on' : ''} ${disabled ? 'is-disabled' : ''}`}
                        onClick={() => {
                          if (disabled) return;
                          if (opt.id !== qaScope) { setQaScope(opt.id); resetConversation(); }
                          setQaScopeMenuOpen(false);
                        }}
                      >
                        <span>{opt.label}</span>
                        {qaScope === opt.id && <Check className="h-3.5 w-3.5" />}
                      </button>
                      );
                    })}
                  </div>
                )}
              </div>
              <button
                type="button"
                className="reader-ai-send"
                onClick={handleAsk}
                disabled={qaLoading || !qaInput.trim()}
                aria-label="发送"
              >
                {qaLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </aside>
      )}
    </>
  );
}
