import { useCallback, useEffect, useRef, useState } from 'react';
import { ExternalLink, Loader2, X } from 'lucide-react';

import { fetchPodcastPremiumTaskDetail, podcastArtifactAdminAudioUrl } from '../../api';
import { useModalA11y } from '../../hooks/useModalA11y';
import { articleDeepLink } from '../../utils/shareLink';
import { formatPodcastArtifactBytes, podcastArtifactStatusMeta } from '../../utils/podcastArtifactAdmin';
import {
  PODCAST_TIMELINE_TONES,
  podcastScoreText,
  podcastTaskMeta,
} from '../../utils/podcastProcessing';
import { formatStamp } from './adminUtils';

const TEXT_LABELS = {
  publisher_transcript: '发布方逐字稿',
  normalized_transcript: 'ASR 逐字稿',
  transcript_zh: '中文译文',
  digest_blog_zh: '导读博客',
  narration_script_zh: '口播稿',
};

function durationText(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return '';
  return `${Math.round(n / 60)} 分钟`;
}

function TextLine({ text }) {
  if (!text) return <span className="tiny-meta">—</span>;
  const status = text.status === 'published' ? '已发布' : text.status === 'unpublished' ? '已下架' : text.status;
  return (
    <>
      <span className={`stamp ${text.status === 'published' ? 'stamp-ok' : 'stamp-idle'}`}>{status}</span>
      {text.version != null && <> · v{text.version}</>}
      {text.chars != null && <> · {Number(text.chars).toLocaleString()} 字</>}
      {text.published_at && <> · <span className="acct-mono">{formatStamp(text.published_at)}</span></>}
    </>
  );
}

// 单集抽屉(issue #76 目检返修):三张表里播客行整行可点即开;评分与判定 / 处理时间线 /
// 产物 / 逐字稿 / 强制动作都在这里。数据来自 /admin/podcast-premium-tasks/{id}(一次取齐),
// 面板动作成功后经 refreshTick 重取。
export default function PodcastEpisodeDrawer({
  episodeId,
  onClose,
  refreshTick = 0,
  running,
  onRetry,
  onForceFull,
  onForceTts,
}) {
  const panelRef = useRef(null);
  const genRef = useRef(0);
  const [state, setState] = useState({ status: 'idle', data: null, error: '' });
  useModalA11y(Boolean(episodeId), onClose, panelRef);

  const load = useCallback(async (id, { quiet = false } = {}) => {
    const gen = ++genRef.current;
    if (!quiet) setState((prev) => ({ status: 'loading', data: prev.data?.item?.episode_id === id ? prev.data : null, error: '' }));
    try {
      const data = await fetchPodcastPremiumTaskDetail(id);
      if (gen === genRef.current) setState({ status: 'ok', data, error: '' });
    } catch (error) {
      if (gen === genRef.current) setState((prev) => ({ status: 'error', data: prev.data, error: error.message }));
    }
  }, []);

  useEffect(() => {
    if (!episodeId) { genRef.current += 1; return; }
    load(episodeId);
  }, [episodeId, load]);
  useEffect(() => {
    if (episodeId && refreshTick > 0) load(episodeId, { quiet: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应面板动作后的刷新脉冲
  }, [refreshTick]);

  const data = state.data;
  const item = data?.item;
  const thresholds = { initial: data?.initial_processing_threshold, premium: data?.threshold };
  const meta = item ? podcastTaskMeta(item, thresholds) : null;
  const episode = data?.episode;
  const busy = Boolean(item && running?.episodeId === item.episode_id);
  const texts = data?.texts ?? {};
  const transcript = texts.publisher_transcript || texts.normalized_transcript;
  const audios = data?.artifacts ?? [];
  const headMeta = episode
    ? [episode.source_name, episode.publish_date ? String(episode.publish_date).slice(0, 10) : '', durationText(episode.duration_seconds), episode.id].filter(Boolean).join(' · ')
    : '';

  return (
    <>
      <div className={`ledger-scrim ${episodeId ? 'is-open' : ''}`} onClick={onClose} aria-hidden="true" />
      <aside
        ref={panelRef}
        className={`ledger-drawer ${episodeId ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={item ? `${item.title} · 单集详情` : '单集详情'}
        aria-hidden={!episodeId}
        tabIndex={-1}
      >
        <div className="ledger-drawer-head">
          <div className="ledger-drawer-title">
            {item ? item.title : (state.status === 'error' ? '单集详情' : '正在读取…')}
            {meta && <span className={`stamp stamp-${meta.verdict.tone} drawer-title-stamp`}>{meta.verdict.label}</span>}
            {headMeta && <small className="acct-mono drawer-title-sub">{headMeta}</small>}
          </div>
          <div className="drawer-acts">
            {item && (
              <a className="rowact-btn" href={articleDeepLink(item.episode_id)} target="_blank" rel="noreferrer" title="在阅读器打开" aria-label="在阅读器打开">
                <ExternalLink />
              </a>
            )}
            <span className="ai-divider" />
            <button type="button" className="rowact-btn" onClick={onClose} title="关闭" aria-label="关闭">
              <X />
            </button>
          </div>
        </div>
        <div className="ledger-drawer-body">
          {state.status === 'error' && !data && (
            <p className="tiny-meta" role="alert">{state.error} · <button type="button" className="kpi-sub-link" onClick={() => load(episodeId)}>重试</button></p>
          )}
          {state.status === 'loading' && !data && (
            <p className="tiny-meta" aria-busy="true"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />正在读取单集…</p>
          )}
          {item && meta && (
            <>
              <section>
                <div className="drawer-sec-title">评分与判定</div>
                <dl className="ledger-kv">
                  <dt>简介初评</dt>
                  <dd>
                    {item.initial_score == null ? '尚未初评' : (
                      <>
                        <span className="acct-mono">{podcastScoreText(item.initial_score)}</span> · 基于节目简介 · 付费 ASR 线 {podcastScoreText(thresholds.initial)}
                        <span className={`stamp ${item.initial_eligible ? 'stamp-ok' : 'stamp-idle'} drawer-kv-stamp`}>{item.initial_eligible ? '已过付费 ASR 线' : '未过付费 ASR 线'}</span>
                      </>
                    )}
                  </dd>
                  <dt>全文终评</dt>
                  <dd>
                    {item.final_score == null ? '尚未终评' : (
                      <>
                        <span className="acct-mono">{podcastScoreText(item.final_score)}</span> · 基于{meta.scores.basis || '逐字稿'} · 优质门槛 {podcastScoreText(thresholds.premium)}
                        <span className={`stamp stamp-${meta.verdict.tone} drawer-kv-stamp`}>{meta.verdict.label}</span>
                      </>
                    )}
                  </dd>
                  <dt>当前分</dt>
                  <dd>
                    {item.current_score == null ? '—' : (
                      <><span className="acct-mono">{podcastScoreText(item.current_score)}</span> · {item.final_score != null ? '全文分已替换简介初评，读者面按此显示' : '读者面按简介初评显示'}</>
                    )}
                  </dd>
                </dl>
              </section>
              <section>
                <div className="drawer-sec-title">处理时间线</div>
                <div className="timeline">
                  {(data.timeline || []).map((row) => (
                    <div key={row.step} className="tl-row">
                      <span className={`stamp stamp-${PODCAST_TIMELINE_TONES[row.state] || 'idle'}`}>{row.label}</span>
                      <span className="tl-what">{row.note || (row.state === 'pending' ? '等待中' : row.state === 'skipped' ? '跳过' : '')}</span>
                      <span className="acct-mono">{row.at ? formatStamp(row.at) : ''}</span>
                    </div>
                  ))}
                </div>
              </section>
              <section>
                <div className="drawer-sec-title">产物</div>
                <dl className="ledger-kv">
                  <dt>导读博客</dt><dd><TextLine text={texts.digest_blog_zh} /></dd>
                  <dt>口播稿</dt><dd><TextLine text={texts.narration_script_zh} /></dd>
                  <dt>精简音频</dt>
                  <dd>
                    {audios.length === 0 ? (
                      <span className={`stamp stamp-${meta.tts.tone}`}>{meta.tts.label}</span>
                    ) : audios.map((audio) => {
                      const status = podcastArtifactStatusMeta(audio.status);
                      return (
                        <span key={audio.id} className="drawer-line">
                          <span className={`stamp stamp-${status.tone}`}>{status.label}</span>
                          {' · '}{formatPodcastArtifactBytes(audio.size_bytes)}
                          {audio.duration_seconds ? ` · ${durationText(audio.duration_seconds)}` : ''}
                          {' · '}<span className="acct-mono">{formatStamp(audio.created_at)}</span>
                          {' · '}<a className="kpi-sub-link" href={podcastArtifactAdminAudioUrl(audio.id)} target="_blank" rel="noreferrer">试听</a>
                        </span>
                      );
                    })}
                    {meta.tts.error && <span className="tiny-meta drawer-line" title={meta.tts.error}>{meta.tts.error}</span>}
                  </dd>
                </dl>
              </section>
              <section>
                <div className="drawer-sec-title">逐字稿</div>
                <dl className="ledger-kv">
                  <dt>来源</dt>
                  <dd>
                    {transcript ? (
                      <>{texts.publisher_transcript ? TEXT_LABELS.publisher_transcript : TEXT_LABELS.normalized_transcript} · <TextLine text={transcript} /></>
                    ) : <span className="tiny-meta">尚无逐字稿</span>}
                  </dd>
                  {texts.transcript_zh && (<><dt>中文译文</dt><dd><TextLine text={texts.transcript_zh} /></dd></>)}
                </dl>
              </section>
            </>
          )}
        </div>
        <div className="ledger-drawer-foot">
          <button
            type="button"
            className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            disabled={!meta || busy || !(meta.actions.retry || meta.actions.force)}
            title={meta ? (meta.actions.retry ? meta.actions.retryLabel : meta.actions.force ? '强制全文处理（需确认）' : meta.stage.code === 'full_analyzed' ? '全文分析已完成' : '全文处理进行中') : undefined}
            onClick={() => { if (!item) return; if (meta.actions.retry) onRetry(item); else onForceFull(item); }}
          >
            {busy && running?.action === 'full' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {meta?.actions.retry ? meta.actions.retryLabel : '强制全文'}
          </button>
          <button
            type="button"
            className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            disabled={!meta || busy || !meta.actions.forceTts}
            title={meta ? (meta.actions.forceTts ? '跳过优质筛选生成中文精简音频（需确认）' : meta.tts.active ? 'TTS 正在进行中' : item.audio_ready ? '音频已生成' : '需先完成全文分析') : undefined}
            onClick={() => { if (item) onForceTts(item); }}
          >
            {busy && running?.action === 'tts' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            强制 TTS
          </button>
          <span className="flex-1" />
          <button type="button" className="action-button action-button-quiet min-h-[32px] px-3 text-xs" onClick={onClose}>关闭</button>
        </div>
      </aside>
    </>
  );
}
