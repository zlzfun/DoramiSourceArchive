import { useCallback, useEffect, useMemo, useState } from 'react';
import { Ban, Loader2, Power, Trash2 } from 'lucide-react';
import {
  fetchAdminUserSources,
  setAdminUserSourcesConfig,
  toggleAdminUserSource,
  deleteAdminUserSource,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import Pager from './Pager';
import { ThFilter, ThSearch, ThSort } from './TableTh';

const fmtNum = (n) => Number(n || 0).toLocaleString();

// 自定源列表分页(v3.42 M08):行数随读者数×人均源数增长,不再全量平铺。
const SRC_PAGE_SIZE = 20;

// 治理状态三态:停用(is_active=false) / 失败中 / 正常。
function sourceState(item) {
  if (!item.is_active) return 'disabled';
  if (item.status === 'failing' || (item.consecutive_failures || 0) > 0) return 'failing';
  return 'ok';
}

/**
 * 用户自定源治理区(v3.40,运维管理 → 内容)。
 *
 * 写入口 = 总闸 + 刷新间隔(保存即调度热生效);观测面 = KPI + 全量源列表
 * (创建者/订阅人数/健康/文章数)。行动作:停用/启用、删除(两击确认,级联清
 * 所有订阅者并删除文章)。「在读者面隐藏」沿源可见性通道(阅读器右键/节点管理),
 * 此处不重复入口。
 */
export default function UserSourcesPanel({ showToast }) {
  const confirm = useConfirm();
  const [data, setData] = useState(null);      // admin_overview 载荷
  const [minutesInput, setMinutesInput] = useState('');
  const [savingConfig, setSavingConfig] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchAdminUserSources();
      setData(res);
      setMinutesInput(String(res.config?.refresh_minutes ?? 60));
    } catch (error) {
      showToast(error.message || '获取用户自定源失败', 'error');
      setData((prev) => prev ?? { items: [], kpi: {}, config: {} });
    }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const handleToggleEnabled = async () => {
    const next = !data?.config?.enabled;
    setSavingConfig(true);
    try {
      const res = await setAdminUserSourcesConfig({ enabled: next });
      setData((prev) => ({ ...prev, config: res }));
      showToast(next ? '已开启自定源功能' : '已关闭自定源功能（既有源与内容保留，调度暂停）', 'success');
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    } finally {
      setSavingConfig(false);
    }
  };

  const handleSaveMinutes = async () => {
    const minutes = parseInt(minutesInput, 10);
    if (!Number.isFinite(minutes) || minutes <= 0) {
      showToast('刷新间隔需为正整数分钟', 'error');
      return;
    }
    setSavingConfig(true);
    try {
      const res = await setAdminUserSourcesConfig({ refresh_minutes: minutes });
      setData((prev) => ({ ...prev, config: res }));
      setMinutesInput(String(res.refresh_minutes));
      showToast(`已把自定源刷新间隔设为 ${res.refresh_minutes} 分钟`, 'success');
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    } finally {
      setSavingConfig(false);
    }
  };

  const handleToggleActive = async (item) => {
    setBusyId(item.source_id);
    try {
      await toggleAdminUserSource(item.source_id, !item.is_active);
      showToast(item.is_active ? `已停用 ${item.name}` : `已启用 ${item.name}`, 'success');
      load();
    } catch (error) {
      showToast(error.message || '操作失败', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (item) => {
    if (!(await confirm(
      `确认删除自定源「${item.name}」？将级联清除 ${fmtNum(item.subscriber_count)} 人的订阅与 ${fmtNum(item.article_count)} 篇文章，且不可恢复。`,
    ))) return;
    setBusyId(item.source_id);
    try {
      const res = await deleteAdminUserSource(item.source_id);
      showToast(`已删除自定源 ${item.name}（清除 ${fmtNum(res.articles_deleted)} 篇文章）`, 'success');
      load();
    } catch (error) {
      showToast(error.message || '删除失败', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const items = useMemo(() => data?.items ?? [], [data]);
  const kpi = data?.kpi ?? {};
  const enabled = Boolean(data?.config?.enabled);

  // 检索 × 状态过滤 × 排序 × 分页(v3.42 M08,本地——admin_overview 已一次拿全量
  // 轻载荷);检索/筛选/排序全部集成在列头(TableTh 三件套,与账户/审计表同语言)。
  const [srcQuery, setSrcQuery] = useState('');       // 源列:名称/地址
  const [srcOwnerQuery, setSrcOwnerQuery] = useState(''); // 创建者列
  const [srcStatus, setSrcStatus] = useState(''); // '' | 'ok' | 'failing' | 'disabled'
  const [srcSort, setSrcSort] = useState('');     // '' = 后端序(创建序)
  const [srcOrder, setSrcOrder] = useState('desc');
  const [srcPage, setSrcPage] = useState(1);
  useEffect(() => { setSrcPage(1); }, [srcQuery, srcOwnerQuery, srcStatus, srcSort, srcOrder]);
  const handleSrcSort = (k) => {
    if (srcSort === k) setSrcOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    else { setSrcSort(k); setSrcOrder('desc'); }
  };
  const filteredItems = useMemo(() => {
    const needle = srcQuery.trim().toLowerCase();
    const ownerNeedle = srcOwnerQuery.trim().toLowerCase();
    const filtered = items.filter((item) => {
      if (srcStatus && sourceState(item) !== srcStatus) return false;
      if (ownerNeedle && !String(item.owner_username || '').toLowerCase().includes(ownerNeedle)) return false;
      if (!needle) return true;
      return [item.name, item.feed_url]
        .filter(Boolean).join(' ').toLowerCase().includes(needle);
    });
    if (!srcSort) return filtered;
    const keyOf = (it) => (
      srcSort === 'subscribers' ? (it.subscriber_count || 0)
        : srcSort === 'articles' ? (it.article_count || 0)
          : String(it.last_success_at || '')
    );
    return [...filtered].sort((a, b) => {
      const ka = keyOf(a); const kb = keyOf(b);
      const cmp = ka < kb ? -1 : ka > kb ? 1 : 0;
      return srcOrder === 'asc' ? cmp : -cmp;
    });
  }, [items, srcQuery, srcOwnerQuery, srcStatus, srcSort, srcOrder]);
  const srcTotalPages = Math.max(1, Math.ceil(filteredItems.length / SRC_PAGE_SIZE));
  const srcSafePage = Math.min(srcPage, srcTotalPages);
  const pagedItems = filteredItems.slice((srcSafePage - 1) * SRC_PAGE_SIZE, srcSafePage * SRC_PAGE_SIZE);
  const srcFiltersActive = Boolean(srcQuery.trim() || srcOwnerQuery.trim() || srcStatus);

  return (
    <>
      <div className="zone-head" style={{ marginTop: 18 }}>
        <span className="zone-title">用户自定源</span>
      </div>

      {/* 总闸 + 刷新间隔（与公开分享总闸同形制） */}
      <section className="surface-card ai-switchboard rounded-[var(--r-card)] mb-3">
        <span className={`ai-light ${enabled ? '' : 'is-off'}`} />
        <div className="ai-switch-lbl" title="总闸:关闭后读者不能再添加,定时抓取暂停;既有源与文章数据不动,重开即回归">
          自定源功能
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label="用户自定源总闸"
          disabled={data === null || savingConfig}
          onClick={handleToggleEnabled}
          className={`ledger-switch ${enabled ? 'is-on' : ''}`}
        />
        <span className="ai-divider" />
        <label className="tiny-meta usrc-minutes" htmlFor="usrc-minutes">
          刷新间隔
          <input
            id="usrc-minutes"
            type="number"
            min="15"
            value={minutesInput}
            onChange={(e) => setMinutesInput(e.target.value)}
            className="form-input form-input-inline usrc-minutes-input"
            disabled={data === null}
          />
          分钟
        </label>
        <button
          type="button"
          onClick={handleSaveMinutes}
          disabled={data === null || savingConfig}
          className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
        >
          {savingConfig ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '保存间隔'}
        </button>
      </section>

      {data === null ? (
        <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
          <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载自定源…
        </p>
      ) : items.length === 0 ? (
        <section className="surface-card card-pad rounded-[var(--r-card)]">
          <p className="tiny-meta">还没有读者添加自定源；读者可在阅读器「发现」页贴入 RSS 地址自助添加。</p>
        </section>
      ) : (
        <>
          <section className="surface-card kpi-strip" aria-label="用户自定源概览">
            <div className="kpi"><span className="kpi-num">{fmtNum(kpi.source_count)}</span><span className="kpi-lbl">自定源</span></div>
            <div className="kpi"><span className="kpi-num">{fmtNum(kpi.covered_users)}</span><span className="kpi-lbl">覆盖读者</span></div>
            <div className="kpi"><span className="kpi-num">{fmtNum(kpi.article_count)}</span><span className="kpi-lbl">收录文章</span></div>
            <div className="kpi"><span className={`kpi-num${kpi.failing_count > 0 ? ' is-warn' : ''}`}>{fmtNum(kpi.failing_count)}</span><span className="kpi-lbl">失败中</span></div>
          </section>
          <section className="surface-card rounded-[var(--r-card)] overflow-hidden" style={{ marginTop: 12 }}>
            {filteredItems.length === 0 ? (
              <p className="p-6 text-center tiny-meta">
                没有匹配当前检索条件的自定源。
                <button
                  type="button"
                  className="kpi-sub-link"
                  onClick={() => { setSrcQuery(''); setSrcOwnerQuery(''); setSrcStatus(''); }}
                >
                  清除筛选
                </button>
              </p>
            ) : (
              <div className="acct-scroll">
                <table className="acct-table is-fixed usrc-table">
                  <thead>
                    <tr>
                      {/* 列宽:源定宽(名称短)、地址吃弹性(URL 长易截断)。 */}
                      <ThSearch label="源" value={srcQuery} onChange={setSrcQuery} placeholder="搜索源 / 地址" active={Boolean(srcQuery.trim())} width={200} inputWidth={176} />
                      <th className="acct-th">地址</th>
                      <ThSearch label="创建者" value={srcOwnerQuery} onChange={setSrcOwnerQuery} placeholder="搜索创建者" active={Boolean(srcOwnerQuery.trim())} width={120} inputWidth={104} />
                      <ThFilter label="状态" value={srcStatus} onChange={setSrcStatus} options={[['', '全部'], ['ok', '正常'], ['failing', '失败中'], ['disabled', '已停用']]} width={96} />
                      <ThSort label="订阅" k="subscribers" sort={srcSort} order={srcOrder} onSort={handleSrcSort} num width={72} />
                      <ThSort label="文章" k="articles" sort={srcSort} order={srcOrder} onSort={handleSrcSort} num width={72} />
                      <ThSort label="最近成功" k="last_success" sort={srcSort} order={srcOrder} onSort={handleSrcSort} width={130} />
                      <th className="acct-th" aria-label="操作" style={{ width: 80 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {pagedItems.map((item) => {
                      const state = sourceState(item);
                      return (
                        <tr key={item.source_id} className="acct-row is-static">
                          <td><span className="acct-name" title={item.name}>{item.name}</span></td>
                          <td>
                            <a
                              href={item.feed_url}
                              target="_blank"
                              rel="noreferrer"
                              className="acct-mono block truncate hover:underline"
                              title={item.feed_url}
                            >
                              {item.feed_url}
                            </a>
                          </td>
                          <td><span className="tiny-meta">{item.owner_username || '—'}</span></td>
                          <td>
                            {state === 'disabled'
                              ? <span className="stamp stamp-idle">已停用</span>
                              : state === 'failing'
                                ? <span className="stamp stamp-warn">失败 ×{item.consecutive_failures || 1}</span>
                                : <span className="stamp stamp-ok">正常</span>}
                          </td>
                          <td className="acct-n">{fmtNum(item.subscriber_count)}</td>
                          <td className="acct-n">{fmtNum(item.article_count)}</td>
                          <td>
                            <span className="acct-mono">
                              {item.last_success_at ? String(item.last_success_at).slice(0, 16).replace('T', ' ') : '–'}
                            </span>
                          </td>
                          <td>
                            <span className="rowacts">
                              {busyId === item.source_id ? (
                                <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
                              ) : (
                                <>
                                  <button
                                    type="button"
                                    className="rowact-btn"
                                    title={item.is_active ? `停用 ${item.name}` : `启用 ${item.name}`}
                                    onClick={() => handleToggleActive(item)}
                                  >
                                    {item.is_active ? <Ban /> : <Power />}
                                  </button>
                                  <button
                                    type="button"
                                    className="rowact-btn is-danger"
                                    title={`删除 ${item.name}（级联清除订阅与文章）`}
                                    onClick={() => handleDelete(item)}
                                  >
                                    <Trash2 />
                                  </button>
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
            )}
            {srcTotalPages > 1 && (
              <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
                <span className="tiny-meta">
                  {srcFiltersActive ? `匹配 ${filteredItems.length} 个 · ` : ''}共 {items.length} 个
                </span>
                <Pager page={srcSafePage} totalPages={srcTotalPages} onPage={setSrcPage} />
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
