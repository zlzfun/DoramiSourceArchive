import { useCallback, useEffect, useMemo, useState } from 'react';
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
import {
  fetchFeedToken,
  fetchReaderSources,
  rotateFeedToken,
  subscribeSource,
  unsubscribeSource,
} from '../api';

const TOKEN_PLACEHOLDER = '$DORAMI_TOKEN';

function apiRoot() {
  const base = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`;
  return base.replace(/\/$/, '');
}

function feedEndpoint(suffix = '') {
  return `${apiRoot()}/public/feed/articles${suffix}`;
}

async function copyText(text) {
  if (!text) throw new Error('没有可复制的内容');
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) throw new Error('浏览器拒绝复制');
  } finally {
    document.body.removeChild(textarea);
  }
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

function SourceTile({ source, busy, onToggleSubscribe, onViewArticles }) {
  const subscribed = Boolean(source.subscribed);
  const hasArticles = (source.count || 0) > 0;
  return (
    <div
      className={`flex flex-col gap-3 rounded-[14px] border p-4 transition-all ${
        subscribed ? 'border-emerald-300 bg-emerald-50/40' : 'border-slate-200 bg-white hover:border-indigo-200'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] border border-slate-200 bg-white text-xl">
          {source.icon || '📡'}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-black text-slate-900" title={source.name}>{source.name}</p>
          <p className="truncate font-mono text-[11px] text-slate-400" title={source.source_id}>{source.source_id}</p>
        </div>
      </div>

      {source.description ? (
        <p className="line-clamp-2 min-h-[2.5rem] text-xs leading-5 text-slate-500" title={source.description}>{source.description}</p>
      ) : (
        <p className="min-h-[2.5rem] text-xs leading-5 text-slate-300">暂无简介</p>
      )}

      <div className="flex items-center gap-2 text-[11px] font-bold text-slate-500">
        <span className="data-chip">{source.content_type || '未知类型'}</span>
        {hasArticles ? <span>{source.count} 篇</span> : <span className="text-slate-400">尚无归档 · 订阅接收后续</span>}
      </div>

      <div className="mt-1 flex items-center gap-2">
        <button
          type="button"
          onClick={() => onToggleSubscribe(source)}
          disabled={busy}
          className={`flex flex-1 items-center justify-center gap-1.5 rounded-[10px] border px-3 py-2 text-xs font-bold transition-colors disabled:opacity-60 ${
            subscribed
              ? 'border-emerald-200 bg-emerald-100 text-emerald-700 hover:bg-emerald-200'
              : 'border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100'
          }`}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : subscribed ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          {subscribed ? '已订阅' : '订阅'}
        </button>
        <button
          type="button"
          onClick={() => onViewArticles?.(source.source_id)}
          disabled={!hasArticles}
          className="flex items-center justify-center gap-1.5 rounded-[10px] border border-slate-100 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-600 hover:border-indigo-100 hover:bg-indigo-50 hover:text-indigo-700 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-slate-50 disabled:hover:text-slate-600"
          title={hasArticles ? `在知识台账中查看「${source.name}」的文章` : '该源暂无归档文章'}
        >
          <FileText className="h-3.5 w-3.5" /> 查看文章
        </button>
      </div>
    </div>
  );
}

export default function SubscriptionTab({ showToast, onViewArticles }) {
  const [view, setView] = useState('catalog'); // catalog | manage
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

  const loadSources = useCallback(async () => {
    setSourcesLoading(true);
    try {
      const data = await fetchReaderSources();
      setSources(data.sources || []);
    } catch (error) {
      showToast(error.message || '获取内容源目录失败', 'error');
    } finally {
      setSourcesLoading(false);
    }
  }, [showToast]);

  const loadFeedToken = useCallback(async () => {
    setFeedLoading(true);
    try {
      setFeedToken(await fetchFeedToken());
    } catch (error) {
      showToast(error.message || '获取聚合接口令牌失败', 'error');
    } finally {
      setFeedLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadSources();
    loadFeedToken();
  }, [loadSources, loadFeedToken]);

  const subscribedSources = useMemo(
    () => sources.filter(source => source.subscribed),
    [sources],
  );
  const subscribedArticleTotal = useMemo(
    () => subscribedSources.reduce((total, source) => total + (source.count || 0), 0),
    [subscribedSources],
  );

  const sourcesByCategory = useMemo(() => {
    const query = sourceQuery.trim().toLowerCase();
    const map = new Map();
    for (const source of sources) {
      const haystack = `${source.name} ${source.source_id} ${source.content_type} ${source.description || ''}`.toLowerCase();
      if (query && !haystack.includes(query)) continue;
      if (!map.has(source.category)) map.set(source.category, []);
      map.get(source.category).push(source);
    }
    return [...map.entries()];
  }, [sources, sourceQuery]);

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

  const handleCopy = async (text, key) => {
    try {
      await copyText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(''), 1800);
      showToast('已复制', 'success');
    } catch (error) {
      showToast(error.message || '复制失败', 'error');
    }
  };

  const handleRotateFeedToken = async () => {
    if (feedToken?.exists && !window.confirm('重新生成会使旧的聚合令牌立即失效，确定继续？')) return;
    setRotatingToken(true);
    try {
      const result = await rotateFeedToken();
      setPlainToken(result.token);
      setDocsOpen(true);
      setFeedToken(prev => ({ ...(prev || {}), exists: true, token_preview: result.token_preview }));
      showToast('聚合接口令牌已生成', 'success');
    } catch (error) {
      showToast(error.message || '生成聚合接口令牌失败', 'error');
    } finally {
      setRotatingToken(false);
    }
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
          <p className="stat-number mt-2">{sources.length}</p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">我已订阅的源</p>
          <p className="stat-number mt-2 text-emerald-600">{subscribedSources.length}</p>
        </div>
        <div className="surface-card rounded-[14px] p-5">
          <p className="tiny-meta">订阅覆盖的文章</p>
          <p className="stat-number mt-2 text-indigo-600">{subscribedArticleTotal}</p>
        </div>
      </div>

      {view === 'catalog' ? (
        <div className="surface-card rounded-[14px]">
          <div className="flex flex-col gap-3 border-b border-slate-100 px-6 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-3">
              <div className="h-5 w-1 rounded-full bg-indigo-500" />
              <h3 className="section-title">内容源目录</h3>
              <span className="tiny-meta">点一下即订阅，再点一下取消</span>
            </div>
            <label className="search-box h-10 max-w-sm flex-1">
              <Search className="mr-2 h-4 w-4 text-slate-400" />
              <input type="text" placeholder="搜索来源名称 / 简介 / 类型" value={sourceQuery} onChange={e => setSourceQuery(e.target.value)} />
            </label>
          </div>

          {sourcesLoading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-12 text-sm font-bold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-500" /> 正在加载内容源
            </div>
          ) : sourcesByCategory.length === 0 ? (
            <div className="p-6"><div className="empty-state py-12">没有匹配的内容源</div></div>
          ) : (
            <div className="space-y-6 px-6 py-5">
              {sourcesByCategory.map(([category, items]) => {
                const allSubscribed = items.every(item => item.subscribed);
                return (
                  <div key={category}>
                    <div className="mb-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-black text-slate-800">{category}</span>
                        <span className="text-xs font-mono text-slate-400">{items.length}</span>
                      </div>
                      <button type="button" onClick={() => handleToggleCategory(items)} className="text-xs font-bold text-indigo-600 hover:text-indigo-800">
                        {allSubscribed ? '取消订阅本组' : '订阅本组'}
                      </button>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {items.map(source => (
                        <SourceTile
                          key={source.source_id}
                          source={source}
                          busy={pendingSourceIds.has(source.source_id)}
                          onToggleSubscribe={handleToggleSubscribe}
                          onViewArticles={onViewArticles}
                        />
                      ))}
                    </div>
                  </div>
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
