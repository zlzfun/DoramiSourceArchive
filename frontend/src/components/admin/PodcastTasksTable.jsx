import { Headphones, Loader2, Play, RefreshCcw, RotateCcw } from 'lucide-react';

import { TableFoot } from './Pager';
import { ThFilter, ThSearch, ThSort } from './TableTh';
import { formatStamp } from './adminUtils';
import {
  PODCAST_STAGE_FILTERS,
  PODCAST_TTS_FILTERS,
  PODCAST_VERDICT_FILTERS,
  podcastScoreCell,
  podcastTaskMeta,
} from '../../utils/podcastProcessing';

export const PODCAST_TASKS_PAGE_SIZE = 20;

// 单集处理表(issue #76 样页「单集处理」卡):每期播客从简介初评到导读 / TTS 的状态与动作,
// 主语是节目。列头即操作:节目搜索 / 阶段·判定·TTS 轮换筛选 / 评分·更新排序;整行可点开
// 单集抽屉,行内 hover 出快捷动作(重试 / 对账恢复 / 强制全文 / 强制 TTS),强制类过确认。
export default function PodcastTasksTable({
  state,          // { status: 'loading'|'ok'|'error', data, error }
  filters,        // { q, stage, verdict, tts, sort, order, page }
  onFilters,      // (patch) => void
  thresholds,     // { initial, premium }
  onOpen,         // (episodeId) => void
  onRetry,        // (item) => Promise
  onForceFull,    // (item) => Promise
  onForceTts,     // (item) => Promise
  running,        // { episodeId, action }
  onRetryLoad,
}) {
  const data = state.data;
  const items = data?.items ?? [];
  const breakdown = data?.breakdown?.stage ?? {};
  const filtersActive = Boolean(filters.q || filters.stage || filters.verdict || filters.tts);
  const handleSort = (k) => {
    if (filters.sort === k) onFilters({ order: filters.order === 'asc' ? 'desc' : 'asc', page: 1 });
    else onFilters({ sort: k, order: 'desc', page: 1 });
  };
  const headMeta = data
    ? `${Number(data.stats?.total ?? 0).toLocaleString()} 期 · 待全文 ${breakdown.awaiting_transcript ?? 0} · 处理中 ${breakdown.processing ?? 0} · 待对账 ${breakdown.reconciliation ?? 0} · 失败 ${breakdown.failed ?? 0}`
    : '';

  return (
    <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="tbl-head">
        <span className="tools-title">单集处理</span>
        {headMeta && <span className="tiny-meta">{headMeta}</span>}
      </div>
      {state.status === 'error' && !data ? (
        <p className="acct-empty tiny-meta" role="alert">
          {state.error} · <button type="button" className="kpi-sub-link" onClick={onRetryLoad}>重试</button>
        </p>
      ) : state.status === 'loading' && !data ? (
        <p className="acct-empty tiny-meta" aria-busy="true"><Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin" />正在加载单集…</p>
      ) : (
        <>
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <ThSearch label="节目" value={filters.q} onChange={(q) => onFilters({ q, page: 1 })} placeholder="搜索节目 / 来源" active={Boolean(filters.q.trim())} width="28%" inputWidth={200} />
                  <ThFilter label="阶段" value={filters.stage} onChange={(stage) => onFilters({ stage, page: 1 })} options={PODCAST_STAGE_FILTERS} width={190} />
                  <ThSort label="评分" k="score" sort={filters.sort} order={filters.order} onSort={handleSort} num width={150} />
                  <ThFilter label="判定" value={filters.verdict} onChange={(verdict) => onFilters({ verdict, page: 1 })} options={PODCAST_VERDICT_FILTERS} width={110} />
                  <ThFilter label="TTS" value={filters.tts} onChange={(tts) => onFilters({ tts, page: 1 })} options={PODCAST_TTS_FILTERS} width={150} />
                  <ThSort label="更新" k="updated" sort={filters.sort} order={filters.order} onSort={handleSort} width={120} />
                  <th className="acct-th" aria-label="操作" style={{ width: 76 }} />
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="acct-empty tiny-meta">
                      {filtersActive ? (
                        <>没有匹配当前筛选的单集。<button type="button" className="kpi-sub-link" onClick={() => onFilters({ q: '', stage: '', verdict: '', tts: '', page: 1 })}>清除筛选</button></>
                      ) : '还没有播客单集'}
                    </td>
                  </tr>
                ) : items.map((item) => {
                  const meta = podcastTaskMeta(item, thresholds);
                  const score = podcastScoreCell(item);
                  const busy = running.episodeId === item.episode_id;
                  const open = () => onOpen(item.episode_id);
                  return (
                    <tr
                      key={item.episode_id}
                      className="acct-row"
                      tabIndex={0}
                      onClick={open}
                      onKeyDown={(e) => { if (e.key === 'Enter') open(); }}
                    >
                      <td>
                        <span className="acct-name" title={item.title}>{item.title}</span>
                        <span className="acct-sub">{item.source_name}</span>
                      </td>
                      <td>
                        <span className="cell-2">
                          <span className={`stamp stamp-${meta.stage.tone}`}>{meta.stage.label}</span>
                          <small title={meta.reasonFull || undefined}>{meta.reason}</small>
                        </span>
                      </td>
                      <td className="acct-n">
                        {score.main === '—' ? <span className="acct-n is-zero">—</span> : (
                          <span className="score-pair">{score.main}{score.sub && <small>{score.sub}</small>}</span>
                        )}
                      </td>
                      <td>
                        <span className={`stamp stamp-${meta.verdict.tone}`}>{meta.verdict.label}</span>
                      </td>
                      <td>
                        {meta.tts.shown ? (
                          <span className="cell-2">
                            <span className={`stamp stamp-${meta.tts.tone}`} title={meta.tts.error || undefined}>{meta.tts.label}</span>
                            {meta.tts.forced && <small title="管理员强制生成，不改变优质判定">强制生成</small>}
                          </span>
                        ) : <span className="tiny-meta">—</span>}
                      </td>
                      <td><span className="acct-mono">{formatStamp(item.updated_at)}</span></td>
                      <td>
                        <span className="rowacts" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                          {busy ? (
                            <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
                          ) : (
                            <>
                              {meta.actions.retry && (
                                <button type="button" className="rowact-btn" title={meta.actions.retryLabel} aria-label={meta.actions.retryLabel} onClick={() => onRetry(item)}>
                                  {meta.actions.retry === 'reconcile' ? <RefreshCcw /> : <RotateCcw />}
                                </button>
                              )}
                              {meta.actions.force && (
                                <button type="button" className="rowact-btn" title="强制全文处理（音频准备 / ASR / 全文分析；需确认）" aria-label="强制全文处理" onClick={() => onForceFull(item)}>
                                  <Play />
                                </button>
                              )}
                              {meta.actions.forceTts && (
                                <button type="button" className="rowact-btn" title="强制生成 TTS（跳过优质筛选，消耗合成额度；需确认）" aria-label="强制生成 TTS" onClick={() => onForceTts(item)}>
                                  <Headphones />
                                </button>
                              )}
                            </>
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <TableFoot total={data?.total ?? 0} page={filters.page} pageSize={PODCAST_TASKS_PAGE_SIZE} onPage={(page) => onFilters({ page })} />
        </>
      )}
    </section>
  );
}
