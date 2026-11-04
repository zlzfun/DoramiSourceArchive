/**
 * 运维 · 存储与外部资源页(issue #114,自 AdminOpsTab 内容档迁出,逻辑原样):存储与备份 · 媒体库(KPI + 热点图)· X API。
 * 都是「花钱 / 占盘」的外部资源(方案 §2.1);数据源都不接时间窗,页头不显示时间窗。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchMediaStats, getXApiConfig, getXApiQuota } from '../../../api';
import MediaHeatmap from '../MediaHeatmap';
import StorageStatusPanel from '../StorageStatusPanel';
import { Kpi } from '../Kpi';
import { fmtNum } from '../adminUtils';
import { opsBlockId } from '../../../utils/opsNav';

// 媒体库占用空间可读化（去重后落盘字节）。
function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export default function ExternalPage({ showToast, refreshTick = 0, onOpenCredentials }) {
  const [xStatus, setXStatus] = useState(null);
  const [xQuota, setXQuota] = useState(null);
  // ── 媒体库（图床）：缓存统计 ──
  // 全量回填按钮已撤（2026-07-20 拍板：生产只做「随抓预取」,突发回填易触发反爬且
  // 死链超时极慢;后端 /api/admin/media/backfill 端点保留作脚本化应急通道）。
  const [media, setMedia] = useState(null);
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
  const loadMedia = useCallback(() => {
    const fresh = claimGen('media');
    return fetchMediaStats().then((v) => { if (fresh()) setMedia(v); }).catch(() => {});
  }, [claimGen]);

  const loadX = useCallback(() => {
    const fresh = claimGen('x');
    return Promise.all([
      getXApiConfig().then((v) => { if (fresh()) setXStatus(v); }).catch(() => {}),
      getXApiQuota().then((v) => { if (fresh()) setXQuota(v); }).catch(() => {}),
    ]);
  }, [claimGen]);
  useEffect(() => { loadMedia(); loadX(); }, [loadMedia, loadX, refreshTick]);

  return (
    <div className="ops-page-body">
      <section id={opsBlockId('external', 'storage')} className="ops-block" tabIndex={-1} aria-label="存储与备份">
        <StorageStatusPanel refreshTick={refreshTick} />
      </section>
      <section id={opsBlockId('external', 'media')} className="ops-block" tabIndex={-1} aria-label="媒体库">
              {/* ── 媒体库（图床）：正文外链图片本地缓存 ── */}
              <div className="zone-head">
                <span className="zone-title">媒体库</span>
              </div>
              {media?.enabled === false ? (
                <section className="surface-card card-pad rounded-[var(--r-card)]">
                  <p className="tiny-meta">媒体库未启用（[media] enabled = false），正文图片走外链直连。</p>
                </section>
              ) : (
                <>
                  <section className="surface-card kpi-strip" aria-label="媒体库概览">
                    {!media ? (
                      <Kpi num="—" label="图片缓存" sub="加载中…" />
                    ) : (
                      <>
                        <Kpi num={fmtNum(media.cached_count)} label="已缓存图片" sub="按 URL 计" />
                        <Kpi num={fmtNum(media.distinct_files)} label="去重文件" sub="按内容计" />
                        <Kpi num={fmtBytes(media.disk_bytes)} label="占用空间" sub="去重后落盘" />
                        <Kpi num={fmtNum(media.failed_count)} label="下载失败" sub="多为签名过期 / 防盗链" tone={media.failed_count > 0 ? 'is-warn' : undefined} />
                      </>
                    )}
                  </section>
                  {/* 统计读回且媒体库开着才挂热点图(未启用时端点 503,不白打一枪) */}
                  {media && <MediaHeatmap showToast={showToast} />}
                </>
              )}
      </section>
      <section id={opsBlockId('external', 'xapi')} className="ops-block" tabIndex={-1} aria-label="X API">
              {/* ── X API(社交源采集):按量付费开销观测面;凭据编辑已收敛到 设置 → 凭据 ── */}
              <div className="zone-head">
                <span className="zone-title">X API</span>
                {xQuota?.blocked && <span className="stamp stamp-bad">已达预算上限 · 停止抓取</span>}
                <div className="zone-acts">
                  <button
                    type="button"
                    className="model-chip"
                    title="前往设置编辑 X API 凭据"
                    onClick={() => onOpenCredentials?.()}
                  >
                    <i className={xStatus?.configured ? '' : 'is-off'} />凭据{' '}
                    <b>{xStatus?.bearer_token_set ? (xStatus.bearer_token_preview || '已配置') : '未配置'}</b>
                  </button>
                </div>
              </div>

              {xQuota && (
                <section className="surface-card kpi-strip" style={{ marginTop: 12 }} aria-label="X API 用量">
                  <Kpi
                    num={`$${Number(xQuota.estimated_cost_usd || 0).toFixed(2)}`}
                    label="本月开销"
                    sub={xQuota.month}
                    tone={xQuota.blocked ? 'is-bad' : undefined}
                  />
                  <Kpi num={`$${Number(xQuota.remaining_usd || 0).toFixed(2)}`} label="剩余额度" sub={`预算 $${xQuota.monthly_budget_usd}`} />
                  <Kpi num={fmtNum(xQuota.post_reads)} label="推文" sub="$0.005 / 条" />
                  <Kpi num={fmtNum(xQuota.media_reads)} label="图片 / 视频" sub="$0.005 / 个" />
                  <Kpi num={fmtNum(xQuota.note_reads)} label="长文" sub="$0.005 / 条" />
                  <Kpi num={fmtNum(xQuota.user_reads)} label="账号解析" sub="$0.010 / 次" />
                </section>
              )}
      </section>
    </div>
  );
}
