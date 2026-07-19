import { useMemo, useState } from 'react';
import { ChevronRight, Loader2, Search } from 'lucide-react';
import LogoMark from './LogoMark';
import { EDITORIAL_GROUPS, editorialGroupOf, resolveCompany } from '../sourceTaxonomy';

// last_fetched(ISO)→ 人话:今日 / 昨日 / MM-DD;空值不显示
function lastLabel(lastFetched) {
  const day = String(lastFetched || '').slice(0, 10);
  if (!day) return '';
  const today = new Date();
  const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  if (day === fmt(today)) return '今日有更新';
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (day === fmt(yesterday)) return '昨日更新';
  return `最近 ${day.slice(5)}`;
}

/**
 * 发现页(参照 Folo,按设计样页):取代源栏的「发现更多来源」内联子列表,
 * 占据 条目列+阅读窗 的整片区域。目录=全站可订阅来源(含已订阅,卡上呈订阅态),
 * 按编辑分层分组、双列卡片;形态分段 + 目录搜索过滤。
 * 「预览」= Folo 语义:直接跳转到该源的条目列表(onPreview → goSource,退出发现页),
 * 未订阅的源同样可看——列表接口本就不按订阅收窄。目录数据全部来自
 * GET /api/reader/sources(count/last_fetched 现成),零后端改动。
 */
export default function DiscoverPage({
  sources,
  subscribedIds,
  loading = false,
  pinningId = null,
  onSubscribe,
  onUnsubscribe,
  onPreview,
}) {
  const [shape, setShape] = useState('all');   // all | article | bulletin
  const [query, setQuery] = useState('');

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const buckets = { official: [], media: [], personal: [], bulletin: [] };
    for (const s of sources) {
      const sShape = s.shape || 'article';
      if (shape !== 'all' && sShape !== shape) continue;
      if (q && !`${s.name || ''} ${s.description || ''} ${s.source_id}`.toLowerCase().includes(q)) continue;
      buckets[editorialGroupOf(s)].push(s);
    }
    for (const key of Object.keys(buckets)) {
      buckets[key].sort((a, b) => (b.count || 0) - (a.count || 0));
    }
    return EDITORIAL_GROUPS
      .map((g) => ({ ...g, list: buckets[g.key] }))
      .filter((g) => g.list.length > 0);
  }, [sources, shape, query]);

  return (
    <main className="reader-disc" aria-label="发现">
      <div className="reader-disc-head">
        <div className="reader-disc-head-inner">
          <div className="reader-disc-title-row">
            <span className="reader-disc-title">发现</span>
            <span className="reader-disc-hint">浏览全站收录的来源,一键订阅到你的阅读器</span>
          </div>
          <div className="reader-disc-tools">
            <label className="reader-disc-search">
              <Search className="h-[13px] w-[13px]" aria-hidden="true" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="筛选来源名称或简介…"
                aria-label="筛选来源"
              />
            </label>
            <span className="reader-seg reader-disc-seg" role="group" aria-label="形态筛选">
              {[['all', '全部'], ['article', '文章'], ['bulletin', '动态']].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`reader-seg-btn ${shape === key ? 'is-on' : ''}`}
                  onClick={() => setShape(key)}
                >
                  {label}
                </button>
              ))}
            </span>
          </div>
        </div>
      </div>

      <div className="reader-disc-scroll">
        <div className="reader-disc-body">
          {loading ? (
            <div className="reader-disc-empty">目录加载中…</div>
          ) : groups.length === 0 ? (
            <div className="reader-disc-empty">没有匹配的来源</div>
          ) : (
            groups.map(({ key, label, list }) => (
              <section key={key}>
                <div className="reader-disc-grp">
                  <span className="reader-src-label reader-disc-grp-label">{label}</span>
                  <span className="reader-disc-grp-n">{list.length}</span>
                </div>
                <div className="reader-disc-grid">
                  {list.map((source) => {
                    const subbed = subscribedIds.has(source.source_id);
                    const pinning = pinningId === source.source_id;
                    return (
                      <div key={source.source_id} className="reader-disc-card">
                        <div className="reader-disc-card-main">
                          <LogoMark company={resolveCompany(source)} size="s34" emoji={source.icon} />
                          <div className="reader-disc-card-mid">
                            <div className="reader-disc-name-row">
                              <span className="reader-disc-name">{source.name || source.source_id}</span>
                              {(source.shape || 'article') === 'bulletin' && (
                                <span className="reader-shape-chip">动态</span>
                              )}
                            </div>
                            {source.description && (
                              <p className="reader-disc-desc">{source.description}</p>
                            )}
                            <p className="reader-disc-meta">
                              收录 {(source.count || 0).toLocaleString()} 篇
                              {source.last_fetched ? ` · ${lastLabel(source.last_fetched)}` : ''}
                            </p>
                          </div>
                          <div className="reader-disc-card-side">
                            <button
                              type="button"
                              className={`reader-disc-sub ${subbed ? 'is-subbed' : ''}`}
                              disabled={pinning}
                              onClick={() => (subbed ? onUnsubscribe(source) : onSubscribe(source))}
                            >
                              {pinning
                                ? <Loader2 className="h-3 w-3 animate-spin" />
                                : subbed
                                  ? <><span className="t-on">✓ 已订阅</span><span className="t-off">取消订阅</span></>
                                  : '订阅'}
                            </button>
                            <button
                              type="button"
                              className="reader-disc-prev"
                              title={`查看 ${source.name || source.source_id} 的收录列表`}
                              onClick={() => onPreview?.(source)}
                            >
                              <ChevronRight className="h-[11px] w-[11px]" aria-hidden="true" />
                              预览
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </main>
  );
}
