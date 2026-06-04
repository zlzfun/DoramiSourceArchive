import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AnimatedNumber from './AnimatedNumber';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  KeyRound,
  Layers,
  Loader2,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  Terminal,
  Trash2,
} from 'lucide-react';
import { API_BASE_URL } from '../config';
import LogoMark from './LogoMark';
import {
  fetchFeedToken,
  fetchReaderSources,
  rotateFeedToken,
  subscribeSource,
  unsubscribeSource,
} from '../api';
import {
  SECTIONS,
  groupBySection,
  labelFrom,
  resolveCompany,
  tierMeta,
  SOURCE_CHANNEL_LABELS,
  SOURCE_SCOPE_LABELS,
} from '../sourceTaxonomy';
import { copyText } from '../utils/clipboard';
import { runAction } from '../utils/runAction';
import { useConfirm } from '../hooks/useConfirm';

const TOKEN_PLACEHOLDER = '$DORAMI_TOKEN';

function apiRoot() {
  const base = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`;
  return base.replace(/\/$/, '');
}

function feedEndpoint(suffix = '') {
  return `${apiRoot()}/public/feed/articles${suffix}`;
}

const FEED_PARAMS = [
  ['publish_date_start / publish_date_end', '发布时间窗口（YYYY-MM-DD），生成日报最常用'],
  ['content_types', '逗号分隔的内容类型，如 rss_article,web_article'],
  ['source_ids', '逗号分隔的来源；仅取与你已订阅来源的交集'],
  ['search', '标题关键词过滤'],
  ['include_content', '是否下发正文，默认 true；传 false 仅取元数据'],
  ['has_content', '仅返回有正文的记录，默认 true'],
  ['skip / limit', '分页偏移与条数，limit 上限 500'],
];

const TIER_TONE_CLASS = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  sky: 'bg-sky-50 text-sky-700 border-sky-100',
  violet: 'bg-violet-50 text-violet-700 border-violet-100',
  slate: 'bg-slate-50 text-slate-500 border-slate-200',
};

function tierPillClass(tier) {
  return TIER_TONE_CLASS[tierMeta(tier).tone] || TIER_TONE_CLASS.slate;
}

function sectionIdOf(source) {
  const key = resolveCompany(source).key;
  const section = SECTIONS.find(s => s.companies.includes(key));
  return section ? section.id : 'other';
}

function inferSourceOwner(source) {
  const text = `${source.source_id || ''} ${source.name || ''}`.toLowerCase();
  if (text.includes('anthropic') || text.includes('claude')) return 'anthropic';
  if (text.includes('gemini') || text.includes('gemma') || text.includes('google')) return 'google';
  if (text.includes('openai') || text.includes('codex')) return 'openai';
  if (text.includes('xai') || text.includes('grok')) return 'xai';
  if (text.includes('deepseek')) return 'deepseek';
  if (text.includes('qwen') || text.includes('alibaba')) return 'alibaba';
  if (text.includes('bytedance') || text.includes('seed')) return 'bytedance_seed';
  if (text.includes('zhipu') || text.includes('z.ai') || text.includes('glm')) return 'zai';
  if (text.includes('cursor')) return 'cursor';
  if (text.includes('openclaw')) return 'openclaw';
  if (text.includes('opencode')) return 'opencode';
  if (text.includes('nous') || text.includes('hermes')) return 'nousresearch';
  if (text.includes('hugging') || text.includes('hf_')) return 'huggingface';
  if (text.includes('qbit') || text.includes('量子位')) return 'qbitai';
  if (text.includes('hacker') || text.includes('ycombinator')) return 'ycombinator';
  return '';
}

function enrichSource(source) {
  return {
    ...source,
    id: source.source_id,
    desc: source.description,
    source_owner: source.source_owner || inferSourceOwner(source),
  };
}

function TokenNotice({ token, onCopy, copied }) {
  if (!token) return null;
  return (
    <div className="surface-card rounded-[14px] border-emerald-200 bg-emerald-50/80 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-emerald-100 text-emerald-700">
            <KeyRound className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-black text-emerald-900">访问令牌仅显示一次</p>
            <p className="tiny-meta mt-1 text-emerald-700">复制到你的下游系统，后续只能再次生成新令牌。</p>
            <code className="mt-2 block break-all rounded-[10px] bg-white/80 px-3 py-2 text-xs font-bold text-emerald-950">
              {token}
            </code>
          </div>
        </div>
        <button onClick={() => onCopy(token, 'token-notice')} className="action-button action-button-secondary shrink-0">
          {copied === 'token-notice' ? <Check /> : <Copy />}
          {copied === 'token-notice' ? '已复制' : '复制令牌'}
        </button>
      </div>
    </div>
  );
}

function FeedDocsPanel({ plainToken, onCopy, copiedKey }) {
  const token = plainToken || TOKEN_PLACEHOLDER;
  const examples = [
    ['拉取最新（默认 100 条）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint()}"`],
    ['按发布时间筛选（日报）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?publish_date_start=2026-05-20&publish_date_end=2026-05-26')}"`],
    ['指定类型 + 仅元数据', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?content_types=rss_article&include_content=false')}"`],
    ['Markdown 批量导出', `curl -H "Authorization: Bearer ${token}" \\\n  "${apiRoot()}/public/feed/articles.md"`],
  ];
  return (
    <div className="mt-4 space-y-4 border-t border-slate-100 pt-4">
      {!plainToken && (
        <p className="tiny-meta">下例中的 <code className="font-mono">{TOKEN_PLACEHOLDER}</code> 请替换为你的令牌（生成时仅显示一次）。</p>
      )}
      <div>
        <p className="form-label mb-2">请求参数</p>
        <div className="overflow-hidden rounded-[10px] border border-slate-100">
          <table className="w-full text-left text-xs">
            <tbody className="divide-y divide-slate-100">
              {FEED_PARAMS.map(([name, desc]) => (
                <tr key={name} className="align-top">
                  <td className="w-[220px] bg-slate-50 px-3 py-2 font-mono font-bold text-slate-600">{name}</td>
                  <td className="px-3 py-2 text-slate-500">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="space-y-3">
        <p className="form-label">调用示例（curl）</p>
        {examples.map(([label, cmd], idx) => (
          <div key={label}>
            <div className="mb-1 flex items-center justify-between">
              <span className="tiny-meta">{label}</span>
              <button
                type="button"
                onClick={() => onCopy(cmd, `curl-${idx}`)}
                className="flex items-center gap-1 text-xs font-bold text-indigo-600 hover:text-indigo-800"
              >
                {copiedKey === `curl-${idx}` ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                复制
              </button>
            </div>
            <pre className="overflow-x-auto rounded-[10px] bg-slate-900 px-3 py-2.5 text-[11px] leading-5 text-slate-100"><code>{cmd}</code></pre>
          </div>
        ))}
      </div>
    </div>
  );
}

function SubscriptionSourceRow({ source, busy, onToggleSubscribe, onViewArticles, innerRef, highlighted }) {
  const subscribed = Boolean(source.subscribed);
  const hasArticles = (source.count || 0) > 0;
  const tier = tierMeta(source.provenance_tier);
  const scopeLabel = labelFrom(SOURCE_SCOPE_LABELS, source.source_scope);
  const channelLabel = labelFrom(SOURCE_CHANNEL_LABELS, source.source_channel);
  return (
    <div ref={innerRef} className={`source-row subscription-source-row ${subscribed ? 'subscription-source-subscribed' : ''} ${highlighted ? 'source-row-focus' : ''}`}>
      <div className="source-row-head">
        <button
          type="button"
          onClick={() => onToggleSubscribe(source)}
          disabled={busy}
          className={`source-check subscription-check ${subscribed ? 'subscription-check-on' : ''}`}
          aria-label={subscribed ? `取消订阅 ${source.name}` : `订阅 ${source.name}`}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : subscribed ? <Check className="h-3.5 w-3.5 text-white" /> : <Plus className="h-3.5 w-3.5" />}
        </button>

        <div className="source-row-id">
          <div className="flex items-center gap-2 min-w-0">
            <span className="source-name truncate" title={source.name}>{source.name}</span>
            {source.provenance_tier && <span className={`tier-pill tier-pill-wide ${tierPillClass(source.provenance_tier)}`}>{tier.short} {tier.label}</span>}
            {subscribed && <span className="subscription-status-pill">已订阅</span>}
          </div>
          <div className="source-sub">
            <span className="font-mono truncate" title={source.source_id}>{source.source_id}</span>
            {source.source_scope && <span className="source-sub-dot">{scopeLabel}</span>}
            {source.source_channel && <span className="source-sub-dot">{channelLabel}</span>}
            {!source.source_channel && source.category && <span className="source-sub-dot">{source.category}</span>}
          </div>
        </div>

        <div className="source-stats subscription-source-stats">
          <span className="source-stat">
            <FileText className="h-3.5 w-3.5 text-emerald-500" />
            <span className="font-bold text-emerald-700">{source.count || 0}</span>
          </span>
        </div>

        <div className="source-actions">
          <button
            type="button"
            onClick={() => onToggleSubscribe(source)}
            disabled={busy}
            className={`subscription-action ${subscribed ? 'subscription-action-on' : ''}`}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : subscribed ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            <span>{subscribed ? '取消订阅' : '订阅'}</span>
          </button>
          <button
            type="button"
            onClick={() => onViewArticles?.(source.source_id)}
            disabled={!hasArticles}
            className="source-icon-btn"
            title={hasArticles ? `在知识台账中查看「${source.name}」的文章` : '该源暂无归档文章'}
          >
            <FileText className="h-4 w-4" />
          </button>
        </div>
      </div>

      {source.description ? (
        <p className="source-desc">{source.description}</p>
      ) : (
        <p className="source-desc text-slate-300">暂无简介</p>
      )}
    </div>
  );
}

export default function SubscriptionTab({ showToast, view, setView, onViewArticles, pendingFocus, onPendingFocusApplied }) {
  const confirm = useConfirm();
  const [sources, setSources] = useState([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourceQuery, setSourceQuery] = useState('');
  const [pendingSourceIds, setPendingSourceIds] = useState(() => new Set());
  const [feedToken, setFeedToken] = useState(null);
  const [feedLoading, setFeedLoading] = useState(true);
  const [rotatingToken, setRotatingToken] = useState(false);
  const [plainToken, setPlainToken] = useState('');
  const [docsOpen, setDocsOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');
  const [collapsedSourceSections, setCollapsedSourceSections] = useState(() => new Set());
  const [expandedSourceCompanies, setExpandedSourceCompanies] = useState(() => new Set());
  const [highlightedSourceId, setHighlightedSourceId] = useState(null);
  const sourceRowRefs = useRef({});

  const loadSources = useCallback(() => runAction(() => fetchReaderSources(), {
    showToast,
    error: '获取内容源目录失败',
    setLoading: setSourcesLoading,
    onSuccess: (data) => setSources(data.sources || []),
  }), [showToast]);

  const loadFeedToken = useCallback(() => runAction(() => fetchFeedToken(), {
    showToast,
    error: '获取聚合接口令牌失败',
    setLoading: setFeedLoading,
    onSuccess: (token) => setFeedToken(token),
  }), [showToast]);

  useEffect(() => {
    loadSources();
    loadFeedToken();
  }, [loadSources, loadFeedToken]);

  // 接收来自知识台账「数据来源」列的定位请求（阅读端）：切到源目录、清空搜索、展开对应板块/主体并高亮该源行。
  useEffect(() => {
    if (!pendingFocus?.source_id) return;
    if (sourcesLoading) return; // 等源目录加载完
    const sid = pendingFocus.source_id;
    const source = sources.find(s => s.source_id === sid);
    onPendingFocusApplied?.();
    if (!source) {
      showToast?.('该来源不在你的内容源目录中', 'info');
      return;
    }
    const company = resolveCompany(enrichSource(source));
    const sectionId = sectionIdOf(enrichSource(source));
    setView('catalog');
    setSourceQuery('');
    setCollapsedSourceSections(prev => {
      if (!prev.has(sectionId)) return prev;
      const next = new Set(prev);
      next.delete(sectionId);
      return next;
    });
    setExpandedSourceCompanies(prev => (prev.has(company.key) ? prev : new Set(prev).add(company.key)));
    setHighlightedSourceId(sid);
  }, [pendingFocus, sourcesLoading, sources, onPendingFocusApplied, showToast]);

  // 高亮目标源行：下一帧滚动到视野中央，短暂高亮后自动消退。
  useEffect(() => {
    if (!highlightedSourceId) return undefined;
    const raf = requestAnimationFrame(() => {
      sourceRowRefs.current[highlightedSourceId]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    const timer = setTimeout(() => setHighlightedSourceId(null), 2400);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
    };
  }, [highlightedSourceId]);

  const subscribedSources = useMemo(
    () => sources.filter(source => source.subscribed),
    [sources],
  );
  const subscribedArticleTotal = useMemo(
    () => subscribedSources.reduce((total, source) => total + (source.count || 0), 0),
    [subscribedSources],
  );

  const filteredSources = useMemo(() => {
    const query = sourceQuery.trim().toLowerCase();
    return sources
      .map(enrichSource)
      .filter(source => {
        const company = resolveCompany(source);
        const haystack = [
          source.name,
          source.source_id,
          source.content_type,
          source.category,
          source.description,
          source.source_owner,
          source.source_brand,
          company.name,
          company.en,
          ...(source.content_tags || []),
        ].filter(Boolean).join(' ').toLowerCase();
        return !query || haystack.includes(query);
      });
  }, [sources, sourceQuery]);

  const groupedSourceSections = useMemo(() => groupBySection(filteredSources), [filteredSources]);
  const autoExpandSourceSections = sourceQuery.trim().length > 0;

  const isSourceSectionOpen = useCallback((sectionId) => {
    if (autoExpandSourceSections) return true;
    return !collapsedSourceSections.has(sectionId);
  }, [autoExpandSourceSections, collapsedSourceSections]);

  const toggleSourceSection = useCallback((sectionId, currentlyOpen) => {
    setCollapsedSourceSections(prev => {
      const next = new Set(prev);
      if (currentlyOpen) next.add(sectionId);
      else next.delete(sectionId);
      return next;
    });
  }, []);

  const handleSourceSectionKeyDown = useCallback((event, sectionId, currentlyOpen) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleSourceSection(sectionId, currentlyOpen);
  }, [toggleSourceSection]);

  const isSourceCompanyOpen = useCallback((companyKey, fetchers) => {
    if (autoExpandSourceSections) return true;
    if (fetchers.some(source => source.subscribed || pendingSourceIds.has(source.source_id))) return true;
    return expandedSourceCompanies.has(companyKey);
  }, [autoExpandSourceSections, expandedSourceCompanies, pendingSourceIds]);

  const toggleSourceCompany = useCallback((companyKey, currentlyOpen) => {
    setExpandedSourceCompanies(prev => {
      const next = new Set(prev);
      if (currentlyOpen) next.delete(companyKey);
      else next.add(companyKey);
      return next;
    });
  }, []);

  const applySubscribedIds = useCallback((ids) => {
    const idSet = new Set(ids || []);
    setSources(prev => prev.map(source => ({ ...source, subscribed: idSet.has(source.source_id) })));
  }, []);

  const handleToggleSubscribe = useCallback(async (source) => {
    const { source_id, subscribed, name } = source;
    setPendingSourceIds(prev => new Set(prev).add(source_id));
    setSources(prev => prev.map(s => (s.source_id === source_id ? { ...s, subscribed: !subscribed } : s)));
    try {
      const result = subscribed ? await unsubscribeSource(source_id) : await subscribeSource(source_id);
      applySubscribedIds(result.subscribed_source_ids);
      showToast(subscribed ? `已取消订阅「${name}」` : `已订阅「${name}」`, 'success');
    } catch (error) {
      setSources(prev => prev.map(s => (s.source_id === source_id ? { ...s, subscribed } : s)));
      showToast(error.message || '操作失败', 'error');
    } finally {
      setPendingSourceIds(prev => {
        const next = new Set(prev);
        next.delete(source_id);
        return next;
      });
    }
  }, [applySubscribedIds, showToast]);

  const handleToggleCategory = useCallback(async (items) => {
    const allSubscribed = items.every(item => item.subscribed);
    const targets = items.filter(item => (allSubscribed ? item.subscribed : !item.subscribed));
    if (targets.length === 0) return;
    const ids = targets.map(item => item.source_id);
    setPendingSourceIds(prev => new Set([...prev, ...ids]));
    setSources(prev => prev.map(s => (ids.includes(s.source_id) ? { ...s, subscribed: !allSubscribed } : s)));
    try {
      let latestIds = null;
      for (const sid of ids) {
        const result = allSubscribed ? await unsubscribeSource(sid) : await subscribeSource(sid);
        latestIds = result.subscribed_source_ids ?? latestIds;
      }
      if (latestIds) applySubscribedIds(latestIds);
      showToast(allSubscribed ? `已取消订阅 ${ids.length} 个源` : `已订阅 ${ids.length} 个源`, 'success');
    } catch (error) {
      await loadSources();
      showToast(error.message || '批量操作失败', 'error');
    } finally {
      setPendingSourceIds(prev => {
        const next = new Set(prev);
        ids.forEach(id => next.delete(id));
        return next;
      });
    }
  }, [applySubscribedIds, loadSources, showToast]);

  const handleCopy = (text, key) => runAction(() => copyText(text), {
    showToast,
    success: '已复制',
    error: '复制失败',
    onSuccess: () => { setCopiedKey(key); setTimeout(() => setCopiedKey(''), 1800); },
  });

  const handleRotateFeedToken = async () => {
    if (feedToken?.exists && !(await confirm('重新生成会使旧的聚合令牌立即失效，确定继续？'))) return;
    await runAction(() => rotateFeedToken(), {
      showToast,
      success: '聚合接口令牌已生成',
      error: '生成聚合接口令牌失败',
      setLoading: setRotatingToken,
      onSuccess: (result) => {
        setPlainToken(result.token);
        setDocsOpen(true);
        setFeedToken(prev => ({ ...(prev || {}), exists: true, token_preview: result.token_preview }));
      },
    });
  };

  const refreshAll = useCallback(async () => {
    await Promise.all([loadSources(), loadFeedToken()]);
  }, [loadSources, loadFeedToken]);

  return (
    <div className="space-y-6 animate-in fade-in">
      <div className="page-header flex-col xl:flex-row">
        <div className="page-heading">
          <h2 className="page-title">订阅分发</h2>
          <p className="page-subtitle mt-3 max-w-3xl">在源目录中一键订阅你关注的内容源，再用一个聚合接口把它们交付给下游编排应用（按发布时间等条件自由拉取）。</p>
        </div>
        <div className="page-actions">
          <div className="segmented-control">
            <button onClick={() => setView('catalog')} className={`segmented-option ${view === 'catalog' ? 'segmented-option-active' : ''}`}><Layers /> 源目录</button>
            <button onClick={() => setView('manage')} className={`segmented-option ${view === 'manage' ? 'segmented-option-active' : ''}`}><KeyRound /> 我的订阅</button>
          </div>
          <button onClick={refreshAll} disabled={sourcesLoading || feedLoading} className="action-button action-button-secondary">
            {sourcesLoading || feedLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新
          </button>
        </div>
      </div>

      <TokenNotice token={plainToken} onCopy={handleCopy} copied={copiedKey} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">可订阅内容源</p>
          <p className="stat-number mt-2"><AnimatedNumber value={sources.length} /></p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">我已订阅的源</p>
          <p className="stat-number mt-2 text-emerald-600"><AnimatedNumber value={subscribedSources.length} /></p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">订阅覆盖的文章</p>
          <p className="stat-number mt-2 text-indigo-600"><AnimatedNumber value={subscribedArticleTotal} /></p>
        </div>
      </div>

      {view === 'catalog' ? (
        <div className="space-y-6">
          <div className="surface-card rounded-[16px] overflow-hidden">
            <div className="catalog-topbar">
              <div className="section-title">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                  <Layers className="h-5 w-5" />
                </div>
                <span>内容源目录</span>
                <span className="text-xs font-mono text-slate-400">{filteredSources.length}/{sources.length}</span>
              </div>
            </div>
            <div className="catalog-filter-row catalog-filter-row-with-search">
              <span className="catalog-dimension-label">搜索</span>
              <label className="search-box catalog-search catalog-search-grow">
                <Search className="mr-2 h-4 w-4 text-slate-400" />
                <input type="text" placeholder="搜索名称、主体、ID、标签" value={sourceQuery} onChange={e => setSourceQuery(e.target.value)} />
              </label>
            </div>
          </div>

          {sourcesLoading ? (
            <div className="surface-card rounded-[16px] flex items-center justify-center gap-2 px-6 py-12 text-sm font-bold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-500" /> 正在加载内容源
            </div>
          ) : groupedSourceSections.length === 0 ? (
            <div className="surface-card rounded-[16px] p-6"><div className="empty-state py-12">没有匹配的内容源</div></div>
          ) : (
            <div className="space-y-8">
              {groupedSourceSections.map(section => {
                const sectionOpen = isSourceSectionOpen(section.id);
                return (
                  <section key={section.id} className="space-y-4">
                    <div
                      role="button"
                      tabIndex={0}
                      className="section-band section-band-toggle"
                      style={{ '--section-accent': section.accent }}
                      onClick={() => toggleSourceSection(section.id, sectionOpen)}
                      onKeyDown={event => handleSourceSectionKeyDown(event, section.id, sectionOpen)}
                      aria-expanded={sectionOpen}
                    >
                      <div className="section-band-line" />
                      <div className="section-band-text">
                        <h3 className="section-band-title">{section.label}</h3>
                        <span className="section-band-en">{section.en}</span>
                      </div>
                      <span className="section-band-blurb">{section.blurb}</span>
                      <span className="section-band-count">{section.companies.reduce((sum, c) => sum + c.fetchers.length, 0)} 源 · {section.companies.length} 主体</span>
                      <span className="section-band-chevron">
                        {sectionOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </span>
                    </div>

                    {sectionOpen && (
                      <div className="dept-grid animate-in fade-in slide-in-from-top-1">
                        {section.companies.map(({ company, fetchers }) => {
                          const companyOpen = isSourceCompanyOpen(company.key, fetchers);
                          const allSubscribed = fetchers.every(item => item.subscribed);
                          const subscribedCount = fetchers.filter(item => item.subscribed).length;
                          const articleCount = fetchers.reduce((sum, item) => sum + (item.count || 0), 0);
                          return (
                            <div key={company.key} className={`dept-card ${companyOpen ? 'dept-card-open' : ''} subscription-dept-card`} style={{ '--dept-accent': company.accent }}>
                              <div className="dept-spine" />
                              <div className="dept-head">
                                <button
                                  type="button"
                                  onClick={() => handleToggleCategory(fetchers)}
                                  className={`dept-check ${allSubscribed ? 'dept-check-on subscription-check-on' : subscribedCount > 0 ? 'dept-check-some' : ''}`}
                                  title={allSubscribed ? '取消订阅本主体全部来源' : '订阅本主体全部来源'}
                                >
                                  {allSubscribed ? <Check className="h-3.5 w-3.5 text-white" /> : subscribedCount > 0 ? <span className="dept-check-dash" /> : null}
                                </button>
                                <LogoMark company={company} size="md" />
                                <button type="button" className="dept-headline" onClick={() => toggleSourceCompany(company.key, companyOpen)}>
                                  <div className="flex items-center gap-2 min-w-0">
                                    <span className="dept-name truncate">{company.name}</span>
                                    {company.en && company.en !== company.name && <span className="dept-alias truncate">{company.en}</span>}
                                  </div>
                                  <div className="dept-meta">
                                    <span>{fetchers.length} 源</span>
                                    <span className="dept-meta-dot">{subscribedCount} 已订阅</span>
                                    <span className="dept-meta-dot">{articleCount} 篇</span>
                                  </div>
                                </button>
                                <button type="button" onClick={() => handleToggleCategory(fetchers)} className="subscription-action shrink-0">
                                  {allSubscribed ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
                                  <span>{allSubscribed ? '取消本主体' : '订阅本主体'}</span>
                                </button>
                                <button type="button" className="dept-chevron" onClick={() => toggleSourceCompany(company.key, companyOpen)} aria-label={companyOpen ? '收起' : '展开'}>
                                  {companyOpen ? <ChevronDown className="h-4.5 w-4.5" /> : <ChevronRight className="h-4.5 w-4.5" />}
                                </button>
                              </div>
                              {companyOpen && (
                                <div className="dept-body row-stagger animate-in fade-in slide-in-from-top-1">
                                  {fetchers.map(source => (
                                    <SubscriptionSourceRow
                                      key={source.source_id}
                                      source={source}
                                      busy={pendingSourceIds.has(source.source_id)}
                                      onToggleSubscribe={handleToggleSubscribe}
                                      onViewArticles={onViewArticles}
                                      innerRef={el => { sourceRowRefs.current[source.source_id] = el; }}
                                      highlighted={highlightedSourceId === source.source_id}
                                    />
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          <div className="surface-card rounded-[14px] p-6">
            <div className="flex flex-col gap-1 border-b border-slate-100 pb-4">
              <div className="flex items-center gap-3">
                <div className="h-5 w-1 rounded-full bg-indigo-500" />
                <h3 className="section-title">聚合拉取接口</h3>
              </div>
              <p className="tiny-meta ml-4">一个接口覆盖你订阅的全部来源，下游可按发布时间、类型、关键词等自由筛选拉取。</p>
            </div>

            <div className="mt-4 space-y-4">
              <div>
                <p className="tiny-meta mb-1">接口地址</p>
                <div className="flex items-center gap-2 rounded-[10px] border border-slate-100 bg-slate-50 px-3 py-2">
                  <code className="min-w-0 flex-1 truncate text-xs font-bold text-slate-600" title={feedEndpoint()}>{feedEndpoint()}</code>
                  <button
                    type="button"
                    onClick={() => handleCopy(feedEndpoint(), 'feed-endpoint')}
                    className="shrink-0 text-slate-400 hover:text-indigo-600"
                    title="复制接口地址"
                    aria-label="复制接口地址"
                  >
                    {copiedKey === 'feed-endpoint' ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="tiny-meta">
                  {feedLoading
                    ? '正在读取令牌状态…'
                    : feedToken?.exists
                      ? `访问令牌 ${feedToken.token_preview}`
                      : '尚未生成访问令牌'}
                </p>
                <button onClick={handleRotateFeedToken} disabled={rotatingToken} className="action-button action-button-secondary text-xs">
                  {rotatingToken ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
                  {feedToken?.exists ? '重新生成令牌' : '生成访问令牌'}
                </button>
              </div>

              <button
                type="button"
                onClick={() => setDocsOpen(open => !open)}
                className="flex items-center gap-2 text-sm font-bold text-indigo-600 hover:text-indigo-800"
              >
                {docsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                <Terminal className="h-4 w-4" /> 接口文档与调用示例
              </button>
              {docsOpen && <FeedDocsPanel plainToken={plainToken} onCopy={handleCopy} copiedKey={copiedKey} />}
            </div>
          </div>

          <div className="surface-card rounded-[14px]">
            <div className="flex items-center gap-3 border-b border-slate-100 px-6 py-4">
              <div className="h-5 w-1 rounded-full bg-indigo-500" />
              <h3 className="section-title">已订阅来源</h3>
              <span className="text-xs font-mono text-slate-400">{subscribedSources.length}</span>
            </div>

            {sourcesLoading ? (
              <div className="flex items-center justify-center gap-2 px-6 py-12 text-sm font-bold text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin text-indigo-500" /> 正在加载
              </div>
            ) : subscribedSources.length === 0 ? (
              <div className="p-6"><div className="empty-state py-12">还没有订阅来源 —— 到「源目录」点一下来源即可订阅</div></div>
            ) : (
              <div className="grid grid-cols-1 gap-3 p-6 sm:grid-cols-2 lg:grid-cols-3">
                {subscribedSources.map(source => (
                  <div key={source.source_id} className="flex items-center gap-3 rounded-[12px] border border-slate-200 bg-white p-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-slate-200 bg-white text-lg">
                      {source.icon || '📡'}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-black text-slate-900" title={source.name}>{source.name}</p>
                      <p className="tiny-meta">{(source.count || 0) > 0 ? `${source.count} 篇` : '尚无归档'}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => onViewArticles?.(source.source_id)}
                      disabled={(source.count || 0) === 0}
                      className="icon-button shrink-0 disabled:opacity-30"
                      title="查看文章"
                      aria-label="查看文章"
                    >
                      <FileText className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleToggleSubscribe(source)}
                      disabled={pendingSourceIds.has(source.source_id)}
                      className="icon-button shrink-0 text-rose-500 disabled:opacity-40"
                      title="取消订阅"
                      aria-label="取消订阅"
                    >
                      {pendingSourceIds.has(source.source_id) ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
