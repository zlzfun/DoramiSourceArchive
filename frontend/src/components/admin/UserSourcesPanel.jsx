import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Trash2 } from 'lucide-react';
import {
  fetchAdminUserSources,
  setAdminUserSourcesConfig,
  toggleAdminUserSource,
  deleteAdminUserSource,
} from '../../api';

const fmtNum = (n) => Number(n || 0).toLocaleString();

/**
 * 用户自定源治理区(v3.40,运维管理 → 内容)。
 *
 * 写入口 = 总闸 + 刷新间隔(保存即调度热生效);观测面 = KPI + 全量源列表
 * (创建者/订阅人数/健康/文章数)。行动作:停用/启用、删除(两击确认,级联清
 * 所有订阅者并删除文章)。「在读者面隐藏」沿源可见性通道(阅读器右键/节点管理),
 * 此处不重复入口。
 */
export default function UserSourcesPanel({ showToast }) {
  const [data, setData] = useState(null);      // admin_overview 载荷
  const [minutesInput, setMinutesInput] = useState('');
  const [savingConfig, setSavingConfig] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [confirmingId, setConfirmingId] = useState(null); // 删除两击确认(4s 回落)
  const confirmTimerRef = useRef(null);
  useEffect(() => () => clearTimeout(confirmTimerRef.current), []);

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
    if (confirmingId !== item.source_id) {
      clearTimeout(confirmTimerRef.current);
      setConfirmingId(item.source_id);
      confirmTimerRef.current = setTimeout(() => setConfirmingId(null), 4000);
      return;
    }
    clearTimeout(confirmTimerRef.current);
    setConfirmingId(null);
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

  const items = data?.items ?? [];
  const kpi = data?.kpi ?? {};
  const enabled = Boolean(data?.config?.enabled);

  return (
    <>
      <div className="zone-head" style={{ marginTop: 18 }}>
        <span className="zone-title">用户自定源</span>
        <span className="zone-hint">读者自助添加的私有 RSS 源：仅添加者可见，不进日报与公共目录</span>
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
          <section className="surface-card card-pad rounded-[var(--r-card)]" style={{ marginTop: 12 }}>
            <div className="usrc-list">
              {items.map((item) => (
                <div key={item.source_id} className="usrc-row">
                  <div className="usrc-main">
                    <div className="usrc-name-row">
                      <span className="usrc-name">{item.name}</span>
                      {!item.is_active && <span className="stamp stamp-idle">已停用</span>}
                      {item.is_active && item.status === 'failing' && (
                        <span className="stamp stamp-warn">连续失败 {item.consecutive_failures}</span>
                      )}
                    </div>
                    <div className="usrc-meta tabular-nums">
                      <a href={item.feed_url} target="_blank" rel="noreferrer" className="usrc-url" title={item.feed_url}>
                        {item.feed_url}
                      </a>
                      {' · '}创建者 {item.owner_username}
                      {' · '}{fmtNum(item.subscriber_count)} 人订阅 · {fmtNum(item.article_count)} 篇
                      {item.last_success_at ? ` · 最近成功 ${String(item.last_success_at).slice(0, 16).replace('T', ' ')}` : ''}
                    </div>
                  </div>
                  <div className="usrc-acts">
                    <button
                      type="button"
                      className="usrc-act-btn"
                      disabled={busyId === item.source_id}
                      onClick={() => handleToggleActive(item)}
                    >
                      {item.is_active ? '停用' : '启用'}
                    </button>
                    <button
                      type="button"
                      className={`usrc-act-btn is-danger ${confirmingId === item.source_id ? 'is-confirm' : ''}`}
                      disabled={busyId === item.source_id}
                      title={`删除将级联清除 ${fmtNum(item.subscriber_count)} 人的订阅与 ${fmtNum(item.article_count)} 篇文章`}
                      onClick={() => handleDelete(item)}
                    >
                      {busyId === item.source_id
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : confirmingId === item.source_id
                          ? '确认删除?'
                          : <Trash2 className="h-3 w-3" />}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
