import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Loader2,
  Sparkles,
  Send,
  X,
  Maximize2,
  Minimize2,
  SquarePen,
} from 'lucide-react';
import ReaderMarkdown from './ReaderMarkdown';
import { askReaderAi, fetchAskProgress } from '../api';

// 阅读器 AI 问答浮层(v3.32 方案 A 重设计,样页 docs/design/dorami-ask-quiet.html):
// 常态收起于右下角 FAB,点击展开。读者问句保留右缘 wash 胶囊,哆啦美的回答**去框化**
// (无框全宽正文块 + 行首星标身份行);回答里的 [n] 行内引用 chip 与末尾出处列表
// 可点站内跳转(onOpenArticle,与 v3.25 深链落地同路);范围控件为 composer 内 seg;
// 空态给建议问题;等待态按检索管线阶段轮询呈现(流式输出挂 backlog)。
// 自持全部问答态,仅从父组件读 aiEnabled、activeArticle、showToast、onOpenArticle。

// 行内引用标记 → 链接形(#dorami-cite-n),由 ReaderMarkdown 的 citations 扩展点渲染成
// chip。行内标记是「尽力而为」:模型漏标只是少行内锚;越界序号剥除(幻觉编号不产生
// 死链);末尾出处列表由前端据 sources 确定性渲染,保底必有。
function withCitationLinks(text, count) {
  if (!count) return text || '';
  return (text || '').replace(/\[(\d{1,2})\](?!\()/g, (marker, digits) => {
    const n = Number(digits);
    return n >= 1 && n <= count ? `[${n}](#dorami-cite-${n})` : '';
  });
}

// 客户端进度标识(后端按它登记阶段进度;字符集须过 _valid_ask_id:字母数字 - _)
function genAskId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `ask-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// 空态建议问题池(按范围;每次露出 3 条,定时轮动、悬停暂停、点击填入输入框)
const SUGGEST_POOL = {
  article: [
    '三句话总结这篇文章',
    '这篇文章的关键结论是什么?',
    '用大白话解释这篇文章在说什么',
    '文中提到了哪些关键数据?',
    '这篇文章有什么值得商榷的地方?',
    '这对行业意味着什么?',
  ],
  subscription: [
    '最近有哪些值得关注的进展?',
    '过去一周有什么大事?',
    '最近有什么新模型发布?',
    '哪些公司最近有大动作?',
    '开源社区最近有什么新东西?',
    '帮我盘点一下最近的重要发布',
  ],
};

const SUGGEST_VISIBLE = 3;
// 轮换节奏(目检三轮定稿):间隔 4.5s、淡出/浮现拉长到 ~1.2s 且位移加大——
// 换得更勤,而"换"本身是舒缓明显的慢动作。淡出相须覆盖 1200ms 过渡 + 逐行 120ms 错落。
const SUGGEST_ROTATE_MS = 4500;
const SUGGEST_FADE_MS = 1500;

// 建议问题轮动(v3.32.1 目检返修):居中无框、引号包裹,整组「浮现-消失」两相轮换;
// 悬停暂停(让用户来得及点),随机起点避免每次都同一批。
function SuggestRotator({ scope, onPick }) {
  const pool = SUGGEST_POOL[scope] || SUGGEST_POOL.subscription;
  const [offset, setOffset] = useState(() => Math.floor(Math.random() * pool.length));
  const [leaving, setLeaving] = useState(false);
  const hoverRef = useRef(false);

  useEffect(() => {
    // 切范围换池:重掷起点,直接以浮现相进场
    setOffset(Math.floor(Math.random() * pool.length));
    setLeaving(false);
  }, [pool]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (hoverRef.current) return;
      setLeaving(true);
      window.setTimeout(() => {
        setOffset((prev) => (prev + SUGGEST_VISIBLE) % pool.length);
        setLeaving(false);
      }, SUGGEST_FADE_MS);
    }, SUGGEST_ROTATE_MS);
    return () => clearInterval(timer);
  }, [pool]);

  const visible = Array.from({ length: SUGGEST_VISIBLE }, (_, i) => pool[(offset + i) % pool.length]);
  return (
    <div
      className={`reader-ai-suggest ${leaving ? 'is-leaving' : ''}`}
      onMouseEnter={() => { hoverRef.current = true; }}
      onMouseLeave={() => { hoverRef.current = false; }}
    >
      {visible.map((text, i) => (
        <button
          key={text}
          type="button"
          className="reader-ai-suggest-btn"
          style={{ '--sg-delay': `${i * 120}ms`, animationDelay: `${i * 120}ms` }}
          onClick={() => onPick(text)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

// 检索型 scope 的阶段清单(与后端 reader_search 的 progress 上报对齐)。
// 清单是**动态的**(v3.32 二轮返修):只画实际上报过的阶段——闲聊/对话元问题
// 后端不检索(plan 后直达 answer),等待态就只有「理解问题 → 组织回答」两行,
// 不再按部就班走完四步。done 文案吸收下一阶段 detail 里的计数(阶段跃迁时
// 才知道上一步的产出);中间阶段若因轮询间隔被跳过,直接不画(诚实呈现)。
const SEARCH_STAGE_ORDER = ['plan', 'search', 'select', 'answer'];
const SEARCH_STAGE_TEXT = {
  plan: {
    doing: '正在理解问题…',
    done: (by) => {
      if (by.search?.temporal) return '已理解(浏览最新内容)';
      if (by.search) return `已规划检索${by.search.keywords ? `(${by.search.keywords} 组关键词)` : ''}`;
      return '已理解(无需检索资料)';
    },
  },
  search: {
    doing: '正在检索文章…',
    done: (by) => `已完成检索${by.select?.candidates ? `(${by.select.candidates} 篇候选)` : ''}`,
  },
  select: {
    doing: '正在挑选相关文章…',
    done: (by) => (by.answer?.articles ? `已选出 ${by.answer.articles} 篇` : '已挑选文章'),
  },
  answer: { doing: '正在组织回答…', done: () => '回答完成' },
};

// 等待态:检索型 scope 画动态阶段清单,显式名单 scope(article/articles)只有作答一步
function PendingBlock({ scope, progress }) {
  if (scope !== 'subscription' && scope !== 'all') {
    return (
      <div className="reader-ai-wait">
        <div className="reader-ai-wait-row is-active"><span className="reader-ai-wait-dot" />正在阅读文章并组织回答…</div>
      </div>
    );
  }
  const seen = progress?.seen?.length ? progress.seen : ['plan'];
  const ordered = SEARCH_STAGE_ORDER.filter((s) => seen.includes(s));
  const by = progress?.byStage || {};
  const current = ordered[ordered.length - 1];
  return (
    <div className="reader-ai-wait">
      {ordered.map((stage) => {
        const active = stage === current;
        const text = SEARCH_STAGE_TEXT[stage];
        return (
          <div key={stage} className={`reader-ai-wait-row ${active ? 'is-active' : 'is-done'}`}>
            <span className="reader-ai-wait-dot" />{active ? text.doing : text.done(by)}
          </div>
        );
      })}
    </div>
  );
}

// 单轮回答:去框正文 + 行内引用 + 末尾出处列表(sources 确定性渲染)。
// 出处列表只列**回答里真正引用过**的文章(行内 [n] 标记为准,保留原编号与 chip 对应):
// 检索是宽召回,注入上下文的不等于支撑了回答,弱相关文章照单全列会稀释出处的可信度;
// 回答通篇无标记时退回列全量(此时「参考」而非「引用」,文案随之切换)。
function AnswerBlock({ turn, onOpenRef }) {
  const sources = useMemo(() => turn.sources || [], [turn.sources]);
  const markdown = useMemo(() => withCitationLinks(turn.a, sources.length), [turn.a, sources.length]);
  const citations = useMemo(() => {
    if (!sources.length) return null;
    return {
      titleFor: (n) => sources[n - 1]?.title || '',
      onCite: (n) => onOpenRef(sources[n - 1]),
    };
  }, [sources, onOpenRef]);
  const listed = useMemo(() => {
    const rows = sources.map((s, i) => ({ s, num: i + 1 }));
    if (!rows.length) return { rows, cited: false };
    const citedNums = new Set();
    const re = /\[(\d{1,2})\](?!\()/g;
    let match;
    while ((match = re.exec(turn.a || '')) !== null) {
      const n = Number(match[1]);
      if (n >= 1 && n <= sources.length) citedNums.add(n);
    }
    if (!citedNums.size) return { rows, cited: false };
    return { rows: rows.filter(({ num }) => citedNums.has(num)), cited: true };
  }, [turn.a, sources]);
  return (
    <div className="reader-ai-a">
      <div className="reader-ai-a-id"><Sparkles className="h-3 w-3" /> 哆啦美</div>
      <div className="reader-ai-a-body markdown-body">
        <ReaderMarkdown citations={citations}>{markdown}</ReaderMarkdown>
      </div>
      {listed.rows.length > 0 && (
        <div className="reader-ai-refs">
          <div className="reader-ai-refs-label">{listed.cited ? '本次回答引用' : '本次回答参考'}</div>
          {listed.rows.map(({ s, num }) => (
            <button
              key={num}
              type="button"
              className="reader-ai-ref-row"
              onClick={() => onOpenRef(s)}
              title={s.id ? '在阅读器中打开' : '打开原文'}
            >
              <span className="reader-ai-ref-num">{num}</span>
              <span className="reader-ai-ref-title">{s.title || s.source_id}</span>
              <span className="reader-ai-ref-meta">
                {[s.source_name, (s.publish_date || '').slice(5)].filter(Boolean).join(' · ')}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ReaderAiPanel({ aiEnabled, activeArticle, showToast, onOpenArticle }) {
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const [aiPanelClosing, setAiPanelClosing] = useState(false);
  const [aiPanelLarge, setAiPanelLarge] = useState(() => localStorage.getItem('dorami_reader_ai_panel_large') === '1');
  const [qaScope, setQaScope] = useState('article');             // article | subscription(前端两档;后端另有 articles/all)
  const [qaInput, setQaInput] = useState('');
  const [qaThread, setQaThread] = useState([]);                  // {q, a, sources, error, pending, scope}
  const [qaLoading, setQaLoading] = useState(false);
  const [qaProgress, setQaProgress] = useState(null);            // {stage, byStage} 阶段化等待态
  const inputRef = useRef(null);
  const threadRef = useRef(null);
  const pollRef = useRef(null);
  // 输入法组字态:composition 事件对 + isComposing + keyCode 229 三重判据——
  // Safari 在 compositionend 之后才派发确认回车(此刻 isComposing 已假),
  // 部分输入法的确认回车只带 keyCode 229;单一判据在跨浏览器下都有漏网。
  const composingRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  useEffect(() => stopPolling, [stopPolling]); // 卸载时清轮询

  // 引用跳转:有 id 走站内(深链落地同路,切作用域+选中一次完成),否则退外链
  const handleOpenRef = useCallback((source) => {
    if (!source) return;
    if (source.id && onOpenArticle) {
      onOpenArticle(source.id);
      return;
    }
    if (source.source_url) window.open(source.source_url, '_blank', 'noopener');
  }, [onOpenArticle]);

  // ── 问答(基于本文 / 基于我的订阅) ──
  const handleAsk = useCallback(async () => {
    const q = qaInput.trim();
    if (!q || qaLoading) return;
    const scope = qaScope;
    const articleId = activeArticle?.id || null;
    if (scope === 'article' && !articleId) { showToast('请先从中间选择一篇文章', 'error'); return; }
    // 多轮:把此前已完成的问答展开成 user/assistant 历史(不含本轮,未完成/出错的轮次跳过)
    const history = qaThread
      .filter((m) => m.a && !m.error && !m.pending)
      .flatMap((m) => [{ role: 'user', content: m.q }, { role: 'assistant', content: m.a }]);
    const askId = genAskId();
    setQaLoading(true);
    setQaProgress(null);
    setQaThread((prev) => [...prev, { q, a: null, sources: [], pending: true, scope }]);
    setQaInput('');
    // 阶段化等待态:检索型 scope 轮询后端进度(显式名单只有作答一步,不轮询)
    if (scope === 'subscription') {
      pollRef.current = setInterval(async () => {
        try {
          const p = await fetchAskProgress(askId);
          if (p?.stage) {
            // 阶段历史采信服务端全量累积(stages)——瞬时阶段(search 只存活几毫秒)
            // 轮询采样必漏,靠采样拼历史会把「没轮询到」误判成「没发生」
            const stages = p.stages?.length ? p.stages : [{ stage: p.stage, detail: p.detail }];
            setQaProgress({
              stage: p.stage,
              byStage: Object.fromEntries(stages.map((s) => [s.stage, s.detail || {}])),
              seen: stages.map((s) => s.stage),
            });
          }
        } catch { /* 进度是观测面,失败静默 */ }
      }, 600);
    }
    try {
      const data = await askReaderAi({ question: q, scope, articleId, history, askId });
      setQaThread((prev) => prev.map((m, i) => (
        i === prev.length - 1 ? { q, a: data.answer, sources: data.sources || [], scope } : m
      )));
    } catch (error) {
      setQaThread((prev) => prev.map((m, i) => (
        i === prev.length - 1 ? { q, a: null, error: error.message || '提问失败，请稍后重试', scope } : m
      )));
    } finally {
      stopPolling();
      setQaProgress(null);
      setQaLoading(false);
    }
  }, [qaInput, qaLoading, qaScope, qaThread, activeArticle, showToast, stopPolling]);

  // 发起新对话:清空多轮历史与输入(切换范围/点「新对话」时调用)
  const resetConversation = useCallback(() => {
    setQaThread([]);
    setQaInput('');
  }, []);

  const switchScope = useCallback((next) => {
    if (next === qaScope) return;
    if (next === 'article' && !activeArticle) return;
    setQaScope(next);
    resetConversation();
  }, [qaScope, activeArticle, resetConversation]);

  // 未选中文章时「基于本文」无对应文章 → 自动回落到「基于我的订阅」(该项始终成立)
  useEffect(() => {
    if (!activeArticle && qaScope === 'article') setQaScope('subscription');
  }, [activeArticle, qaScope]);

  // 新增轮次/回答落地/阶段推进时,线程滚到底(让最新内容始终可见)
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [qaThread, qaProgress]);

  // 关闭面板:先播放退场动画,动画结束再卸载(与 CSS .is-closing 的 200ms 对齐);
  // 函数式 set 防重入——点外关闭与关闭钮可能连发,重复 setTimeout 会闪帧
  const closeAiPanel = useCallback(() => {
    setAiPanelClosing((closing) => {
      if (closing) return closing;
      window.setTimeout(() => {
        setAiPanelOpen(false);
        setAiPanelClosing(false);
      }, 200);
      return true;
    });
  }, []);

  // 点击面板外任意处自动收起(目检拍板);正文图灯箱是 body 级 portal,点它不算「外」
  const panelRef = useRef(null);
  useEffect(() => {
    if (!aiPanelOpen) return undefined;
    const onPointerDown = (e) => {
      if (e.target.closest?.('.reader-lightbox')) return;
      if (panelRef.current && !panelRef.current.contains(e.target)) closeAiPanel();
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [aiPanelOpen, closeAiPanel]);

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
        <aside ref={panelRef} className={`reader-ai-panel ${aiPanelLarge ? 'is-large' : ''} ${aiPanelClosing ? 'is-closing' : ''}`} role="dialog" aria-label="问问哆啦美">
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

          <div className="reader-ai-thread" ref={threadRef}>
            {qaThread.length === 0 && (
              <div className="reader-ai-empty">
                <Sparkles className="h-5 w-5" />
                <div className="reader-ai-empty-title">问问哆啦美</div>
                <div className="reader-ai-empty-sub">
                  {qaScope === 'article' ? '基于当前打开的这篇文章作答' : '在你订阅的内容里检索相关文章后作答'}
                </div>
                <SuggestRotator
                  scope={qaScope}
                  onPick={(text) => {
                    setQaInput(text);
                    inputRef.current?.focus();
                  }}
                />
              </div>
            )}
            {qaThread.map((m, i) => (
              <div key={i} className="reader-ai-turn">
                <div className="reader-ai-q">{m.q}</div>
                {m.pending ? (
                  <PendingBlock scope={m.scope} progress={qaProgress} />
                ) : m.error ? (
                  <div className="reader-ai-a-error">{m.error}</div>
                ) : (
                  <AnswerBlock turn={m} onOpenRef={handleOpenRef} />
                )}
              </div>
            ))}
          </div>

          <div className="reader-ai-composer">
            <textarea
              className="reader-ai-input"
              ref={inputRef}
              rows={2}
              value={qaInput}
              placeholder={qaScope === 'article' ? '问问这篇文章…' : '问问你订阅的内容…'}
              onChange={(e) => setQaInput(e.target.value)}
              onCompositionStart={() => { composingRef.current = true; }}
              onCompositionEnd={() => {
                // 延一拍再解除:Safari 的确认回车在 compositionend 之后同步到达
                window.setTimeout(() => { composingRef.current = false; }, 0);
              }}
              onKeyDown={(e) => {
                // 输入法组字中的回车是「确认候选词」,不是发送(三重判据见 composingRef 注)
                const composing = composingRef.current
                  || e.nativeEvent.isComposing
                  || e.keyCode === 229;
                if (e.key === 'Enter' && !e.shiftKey && !composing) {
                  e.preventDefault();
                  handleAsk();
                }
              }}
            />
            <div className="reader-ai-toolbar">
              <div className="reader-ai-seg" role="group" aria-label="提问范围">
                <button
                  type="button"
                  className={`reader-ai-seg-btn ${qaScope === 'article' ? 'is-on' : ''}`}
                  disabled={!activeArticle}
                  title={activeArticle ? '只基于当前打开的这篇文章作答' : '先从中间选择一篇文章'}
                  onClick={() => switchScope('article')}
                >
                  本文
                </button>
                <button
                  type="button"
                  className={`reader-ai-seg-btn ${qaScope === 'subscription' ? 'is-on' : ''}`}
                  title="在你订阅的内容里检索相关文章后作答"
                  onClick={() => switchScope('subscription')}
                >
                  我的订阅
                </button>
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
