/**
 * 运维 · 阅读与榜单页(issue #114,自 AdminOpsTab 内容档迁出,逻辑原样):内容概览 · 各源热度(含收藏 TOP)· 榜单快照。
 * 三个数据源都不接时间窗(累计口径),页头不显示时间窗。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchAdminContent } from '../../../api';
import { BarList } from '../../charts/DashboardCharts';
import RankingSnapshotPanel from '../RankingSnapshotPanel';
import { Kpi, KpiState } from '../Kpi';
import { C_READ, C_FAVORITE, C_SUBSCRIBE } from '../../charts/chartUtils';
import { fmtNum } from '../adminUtils';
import { opsBlockId } from '../../../utils/opsNav';

export default function ReadingPage({ showToast, refreshTick = 0 }) {
  // ── 内容看板（各源内容健康 + 收藏热度榜）──
  // 失败只让内容 KPI 那一条变成可重试错误态,不再把同页其它分区一起挡住(issue #76 P1 #1)。
  const [content, setContent] = useState(null);
  const [contentError, setContentError] = useState('');
  // 每组 loader 的请求代次守卫(v3.43.1 codex 交叉检视):M15 加了多个刷新时机后,
  // 同组请求可能并发在途——旧响应后到会覆盖新快照。发起时领代次,异步返回后校验
  // 自己仍是该组最新才落 state/弹 Toast(不真正 abort,只丢弃旧响应);keyed map
  // 让不同 loader 互不作废,实例级 useRef 不跨实例共享。
  const loadGen = useRef({});
  const claimGen = useCallback((key) => {
    const gen = (loadGen.current[key] || 0) + 1;
    loadGen.current[key] = gen;
    return () => loadGen.current[key] === gen;
  }, []);
  const loadContent = useCallback(() => {
    const fresh = claimGen('content');
    return fetchAdminContent()
      .then((v) => { if (fresh()) { setContent(v); setContentError(''); } })
      .catch((error) => { if (fresh()) setContentError(error.message); });
  }, [claimGen]);
  useEffect(() => { loadContent(); }, [loadContent, refreshTick]);

  // ── 内容看板图表数据 ──
  const contentSourceRows = useMemo(
    () => [...(content?.sources ?? [])]
      .filter((s) => (s.read_count || 0) + (s.favorite_count || 0) + (s.subscription_count || 0) > 0)
      .sort((a, b) => (b.favorite_count - a.favorite_count) || (b.read_count - a.read_count) || (b.subscription_count - a.subscription_count))
      .map((s) => ({ name: s.name, reads: s.read_count || 0, favorites: s.favorite_count || 0, subs: s.subscription_count || 0 })),
    [content],
  );
  const topArticleRows = useMemo(
    () => (content?.top_articles ?? []).slice(0, 10).map((a) => ({
      title: a.title || '无标题',
      src: a.source_name || a.source_id || '',
      fav: a.favorite_count,
    })),
    [content],
  );

  return (
    <div className="ops-page-body">
      <section id={opsBlockId('reading', 'kpi')} className="ops-block" tabIndex={-1} aria-label="内容概览">
          {/* 内容 KPI 独立持有错误态:统计请求失败只让这一条变脸,其余分区照常渲染(issue #76 P1 #1) */}
          <section className="surface-card kpi-strip" aria-label="内容概览">
            {content ? (
              <>
                <Kpi num={fmtNum(content.totals.sources)} label="内容源" sub="累计" />
                <Kpi num={fmtNum(content.totals.articles)} label="归档文章" sub="累计" />
                <Kpi num={fmtNum(content.totals.reads)} label="阅读总数" sub="累计" />
                <Kpi num={fmtNum(content.totals.favorites)} label="收藏总数" sub="累计" />
              </>
            ) : (
              <KpiState label="内容统计" error={contentError} onRetry={loadContent} />
            )}
          </section>
      </section>
      <section id={opsBlockId('reading', 'heat')} className="ops-block" tabIndex={-1} aria-label="各源热度">
              <div className="zone-head">
                <span className="zone-title">各源热度</span>
              </div>
              <div className="admin-grid">
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <div className="card-head">
                    <span className="card-title">各源 · 文章阅读 / 文章收藏 / 源订阅{contentSourceRows.length ? ` · ${contentSourceRows.length} 个源` : ''}</span>
                  </div>
                  <BarList
                    rows={contentSourceRows}
                    nameKey="name"
                    metrics={[
                      { key: 'reads', name: '文章阅读', color: C_READ },
                      { key: 'favorites', name: '文章收藏', color: C_FAVORITE },
                      { key: 'subs', name: '源订阅', color: C_SUBSCRIBE },
                    ]}
                    emptyHint="还没有阅读 / 收藏 / 订阅记录"
                  />
                </section>
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <div className="card-head">
                    <span className="card-title">文章 · 收藏 TOP{topArticleRows.length ? ` ${topArticleRows.length}` : ''}</span>
                  </div>
                  {topArticleRows.length === 0 ? (
                    <div className="flex items-center justify-center rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] tiny-meta" style={{ minHeight: 120 }}>
                      还没有任何收藏记录，读者在阅读器收藏文章后这里会出现热度榜。
                    </div>
                  ) : (
                    <div className="toplist">
                      {topArticleRows.map((a, i) => (
                        <div key={`${a.title}-${i}`} className="toplist-row">
                          <span className="toplist-rank">{i + 1}</span>
                          <span className="toplist-title" title={a.title}>{a.title}</span>
                          {a.src && <span className="toplist-src">{a.src}</span>}
                          <span className="toplist-n">{a.fav}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>
      </section>
      <section id={opsBlockId('reading', 'rank')} className="ops-block" tabIndex={-1} aria-label="榜单快照">
        <RankingSnapshotPanel showToast={showToast} refreshTick={refreshTick} />
      </section>
    </div>
  );
}
