import { useEffect, useMemo, useState } from 'react';
import { Crown, FileText, Headphones, Loader2, Trophy } from 'lucide-react';
import {
  fetchReaderRankingHistory,
  fetchReaderRankings,
  fetchReaderRankingTag,
} from '../api';
import {
  RANKING_AXES,
  RANKING_SCOPE_NOTE,
  rankingMovement,
  rankingTrendPath,
  scoreBasisLabel,
} from '../utils/rankings';

function ContentList({
  items, onOpenArticle, sourceMap, must = false,
  emptyText = '还没有达到公开门槛的内容，明早 7 点再来看看',
}) {
  if (!items?.length) {
    return <p className="ranking-empty">{emptyText}</p>;
  }
  return (
    <ol className="ranking-content-list">
      {items.map((item, index) => (
        <li key={item.id}>
          <button type="button" className="ranking-content-row" onClick={() => onOpenArticle?.(item.id)}>
            <span className="ranking-content-rank">{index + 1}</span>
            <span className="ranking-content-main">
              <span className="ranking-content-title">{item.title}</span>
              <span className="ranking-content-meta">
                {sourceMap?.[item.source_id]?.name || item.source_id}
                {item.score_basis ? ` · ${scoreBasisLabel(item.score_basis)}` : ''}
                {must ? ` · 命中 ${item.appearance_count} 个榜内标签` : ''}
              </span>
            </span>
            <span className="ranking-content-score">{Number(item.score || 0).toFixed(1)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Trend({ points }) {
  const path = rankingTrendPath(points);
  return (
    <div className="ranking-trend">
      <div>
        <span className="section-title">30 日趋势</span>
        <span className="tiny-meta">每日 07:00 快照</span>
      </div>
      {path ? (
        <svg viewBox="0 0 160 42" role="img" aria-label="标签近 30 日出现次数趋势">
          <path d={path} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      ) : <span className="tiny-meta">趋势将在多个快照后出现</span>}
    </div>
  );
}

export default function RankingsPage({
  sourceMap = {}, onOpenArticle, onShapeChange, embedded = false, initialShape = 'article',
}) {
  const [shape, setShape] = useState(initialShape);
  const [axis, setAxis] = useState('topic');
  const [data, setData] = useState(null);
  const [selectedCode, setSelectedCode] = useState('');
  const [detail, setDetail] = useState(null);
  const [history, setHistory] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchReaderRankings(shape, 'latest', { signal: controller.signal })
      .then((result) => {
        setData(result);
        const first = result.axes?.[axis]?.[0]?.code || '';
        setSelectedCode((current) => (
          result.axes?.[axis]?.some((item) => item.code === current) ? current : first
        ));
      })
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason.message || '榜单加载失败，请稍后重试');
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [shape, axis]);

  useEffect(() => {
    if (!data?.snapshot_date || !selectedCode) {
      setDetail(null);
      setHistory([]);
      setDetailLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    // The selected heading changes synchronously. Clear the previous tag's
    // payload before starting the next request so it can never be presented
    // under the new tag name on a slow connection.
    setDetail(null);
    setHistory([]);
    setDetailLoading(true);
    Promise.all([
      fetchReaderRankingTag(data.snapshot_date, selectedCode, shape, { signal: controller.signal }),
      fetchReaderRankingHistory(selectedCode, shape, 30, { signal: controller.signal }),
    ]).then(([tagDetail, trend]) => {
      if (controller.signal.aborted) return;
      setDetail(tagDetail);
      setHistory(trend.points || []);
    }).catch((reason) => {
      if (reason?.name !== 'AbortError') setDetail(null);
    }).finally(() => {
      if (!controller.signal.aborted) setDetailLoading(false);
    });
    return () => controller.abort();
  }, [data?.snapshot_date, selectedCode, shape]);

  const selectedTag = useMemo(
    () => data?.axes?.[axis]?.find((item) => item.code === selectedCode) || null,
    [data, axis, selectedCode],
  );
  const shapeName = shape === 'podcast' ? '播客榜' : '文章榜';

  return (
    <main className={`rankings-page ${embedded ? 'is-embedded' : ''}`} aria-label="榜单">
      <header className="ranking-head">
        <div>
          <span className="reader-disc-title">榜单</span>
          <p className="ranking-head-note">{RANKING_SCOPE_NOTE}</p>
        </div>
        <div className="reader-seg" role="group" aria-label="内容类型">
          {[['article', '文章榜', FileText], ['podcast', '播客榜', Headphones]].map(([key, label, Icon]) => (
            <button
              key={key}
              type="button"
              className={`reader-seg-btn ${shape === key ? 'is-on' : ''}`}
              aria-pressed={shape === key}
              onClick={() => {
                setShape(key);
                onShapeChange?.(key);
              }}
            >
              <Icon aria-hidden="true" />{label}
            </button>
          ))}
        </div>
      </header>

      <div className="ranking-axis" role="tablist" aria-label={`${shapeName}分类`}>
        {RANKING_AXES.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={axis === key}
            className={axis === key ? 'is-on' : ''}
            onClick={() => {
              setAxis(key);
              setSelectedCode(data?.axes?.[key]?.[0]?.code || '');
            }}
          >{label}</button>
        ))}
      </div>

      {loading ? (
        <div className="ranking-state"><Loader2 className="animate-spin" />榜单加载中…</div>
      ) : error ? (
        <div className="ranking-state is-error">{error}</div>
      ) : (
        <>
          {data?.status === 'degraded' && (
            <p className="ranking-coverage-note">本期正式标签覆盖率较低，榜单可能少于 10 项</p>
          )}
          <div className="ranking-layout">
            <section className="ranking-tags surface-card" aria-label={`${shapeName}${RANKING_AXES.find(([key]) => key === axis)?.[1]}榜`}>
              <div className="ranking-section-head">
                <span className="card-title">{shapeName} · {RANKING_AXES.find(([key]) => key === axis)?.[1]}</span>
                <span className="tiny-meta">有效出现次数</span>
              </div>
              {(data?.axes?.[axis] || []).length ? (
                <ol>
                  {data.axes[axis].map((tag) => {
                    const movement = rankingMovement(tag.rank_change);
                    return (
                      <li key={tag.code}>
                        <button
                          type="button"
                          className={`ranking-tag-row ${selectedCode === tag.code ? 'is-on' : ''}`}
                          onClick={() => setSelectedCode(tag.code)}
                        >
                          <span className="ranking-tag-rank">{tag.rank}</span>
                          <span className="ranking-tag-name">{tag.name}</span>
                          <span className={`ranking-move is-${movement.direction}`}>{movement.label}</span>
                          <strong>{tag.occurrence_count}</strong>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              ) : <p className="ranking-empty">近 7 天还没有可展示的正式标签</p>}
            </section>

            <section className="ranking-detail surface-card" aria-label="标签高分内容">
              <div className="ranking-section-head">
                <div>
                  <span className="card-title">{selectedTag?.name || '高分内容'}</span>
                  {selectedTag && <span className="tiny-meta">{selectedTag.occurrence_count} 次出现 · {selectedTag.distinct_source_count} 个来源</span>}
                </div>
              </div>
              <Trend points={history} />
              {detailLoading ? (
                <div className="ranking-state"><Loader2 className="animate-spin" />高分内容加载中…</div>
              ) : (
                <ContentList items={detail?.contents || []} onOpenArticle={onOpenArticle} sourceMap={sourceMap} />
              )}
            </section>
          </div>

          <section className="ranking-global surface-card" aria-label="全局高分榜">
            <div className="ranking-section-head">
              <span className="card-title"><Crown aria-hidden="true" />全局高分榜</span>
              <span className="tiny-meta">全站全部历史公开{shape === 'podcast' ? '播客' : '文章'} Top 10</span>
            </div>
            <ContentList
              items={data?.all_time_high_score || []}
              onOpenArticle={onOpenArticle}
              sourceMap={sourceMap}
              emptyText="还没有可展示的历史评分内容"
            />
          </section>

          <section className="ranking-must surface-card" aria-label={shape === 'podcast' ? '必听播客' : '必读文章'}>
            <div className="ranking-section-head">
              <span className="card-title"><Trophy aria-hidden="true" />{shape === 'podcast' ? '必听播客' : '必读文章'}</span>
              <span className="tiny-meta">同时进入至少 2 个独立榜内标签 Top 10，且相关趋势覆盖至少 2 个来源</span>
            </div>
            <ContentList items={data?.must_read || []} onOpenArticle={onOpenArticle} sourceMap={sourceMap} must />
          </section>
        </>
      )}
    </main>
  );
}
