import { useCallback, useEffect, useMemo, useState } from 'react';
import { Ban, Copy, KeyRound, Loader2, X } from 'lucide-react';
import {
  bindIngestSource, createIngestToken, fetchFetchers, fetchIngestTokens, revokeIngestToken, unbindIngestSource,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';
import { bindableSources } from '../../utils/sourceAcquisition';
import { BindAdd, BindList } from './PushBindings';
import { SyncDrawerShell } from './sync/syncParts';

const fmtNum = (n) => Number(n || 0).toLocaleString();
// 令牌时间存的是带时区的 UTC(…+00:00);统一按本地时间显示。
const pad2 = (n) => String(n).padStart(2, '0');
const fmtTime = (value) => {
  if (!value) return '–';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
};

const SHAPE_GROUPS = [['article', '文章'], ['podcast', '播客'], ['social', '社交']];

// 来源下拉按内容形态分组;认不出形态的归文章。
function sourceGroups(sources) {
  const known = new Set(SHAPE_GROUPS.map(([key]) => key));
  return SHAPE_GROUPS.map(([key, label]) => ({
    label,
    options: sources
      .filter((source) => (known.has(source.shape) ? source.shape : 'article') === key)
      .sort((a, b) => String(a.name).localeCompare(String(b.name), 'zh'))
      .map((source) => ({ value: source.id, label: source.name || source.id })),
  }));
}

/**
 * 收录令牌(issue #262 波 1,运维 → 自定源与收录)。调用方可以是 Agent、自动化流程或人。
 *
 * 签发(明文只回显这一次)/ 吊销;令牌只能往它绑定的来源推(issue #344)——表里列出每个令牌的来源,
 * 点一行在抽屉里加、解。来源本身(登记 / 列入 / 改名 / 读者面隐藏)在「源管理」里治理。
 */
export default function IngestPanel({ showToast, refreshTick = 0 }) {
  const confirm = useConfirm();
  const [tokens, setTokens] = useState(null);
  const [actor, setActor] = useState('');
  const [issued, setIssued] = useState(null); // { actor, token }
  const [busy, setBusy] = useState('');
  // 抽屉:开合与「是哪个令牌」分开记——关上时令牌 id 留着,滑出去的过程里内容不空。
  const [drawerId, setDrawerId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [catalog, setCatalog] = useState(null); // 源目录(每次打开抽屉时重取)
  const [bindBusy, setBindBusy] = useState(''); // 'add' | 绑定行的 key

  const load = useCallback(async () => {
    try {
      const tokenRes = await fetchIngestTokens();
      setTokens(tokenRes.items || []);
    } catch (error) {
      showToast(error.message || '获取收录令牌失败', 'error');
      setTokens((prev) => prev ?? []);
    }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (refreshTick > 0) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只响应「切回 Tab / 切子页」刷新脉冲(M15)
  }, [refreshTick]);

  const handleIssue = async () => {
    const name = actor.trim();
    if (!name) return;
    setBusy('issue');
    try {
      const res = await createIngestToken({ actor: name });
      setIssued({ actor: res.actor, token: res.token });
      setActor('');
      load();
    } catch (error) {
      showToast(error.message || '签发失败', 'error');
    } finally {
      setBusy('');
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(issued.token);
      showToast('已复制令牌', 'success');
    } catch {
      showToast('复制失败,请手动选中复制', 'error');
    }
  };

  // 抽屉:这个令牌的来源。绑定 / 解绑的接口都返回更新后的令牌,就地换进列表。
  const shown = tokens?.find((token) => token.id === drawerId) || null;
  const openDrawer = (token) => {
    setDrawerId(token.id);
    setDrawerOpen(true);
    // 每次打开都重取:在源管理里新登记的来源要出现在下拉里(取不到就沿用上一份)。
    fetchFetchers().then((list) => { if (Array.isArray(list)) setCatalog(list); }).catch(() => {});
  };
  const replaceToken = (next) => setTokens((prev) => (prev || []).map((token) => (token.id === next.id ? next : token)));
  const handleBind = async (sourceId) => {
    setBindBusy('add');
    try {
      replaceToken(await bindIngestSource(shown.id, sourceId));
      return true;
    } catch (error) {
      showToast(error.message || '绑定失败', 'error');
      return false;
    } finally {
      setBindBusy('');
    }
  };
  const handleUnbind = async (row) => {
    setBindBusy(row.key);
    try {
      replaceToken(await unbindIngestSource(shown.id, row.id));
    } catch (error) {
      showToast(error.message || '解绑失败', 'error');
    } finally {
      setBindBusy('');
    }
  };
  const bindings = useMemo(() => shown?.bindings || [], [shown]);
  const addGroups = useMemo(
    () => sourceGroups(bindableSources(catalog, bindings.map((binding) => binding.source_id))),
    [catalog, bindings],
  );

  const handleRevoke = async (token) => {
    if (!(await confirm(`确认吊销「${token.actor}」的收录令牌？使用它的调用方将立即无法推送。`))) return;
    setBusy(`token:${token.id}`);
    try {
      await revokeIngestToken(token.id);
      showToast(`已吊销 ${token.actor}`, 'success');
      load();
    } catch (error) {
      showToast(error.message || '吊销失败', 'error');
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      <div className="zone-head" style={{ marginTop: 18 }}>
        <span className="zone-title">收录令牌</span>
      </div>

      <section className="surface-card ai-switchboard rounded-[var(--r-card)] mb-3">
        <KeyRound className="h-4 w-4 text-slate-500" aria-hidden="true" />
        <input
          type="text"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleIssue(); }}
          placeholder="调用方"
          aria-label="调用方"
          maxLength={60}
          className="form-input form-input-inline"
        />
        <button
          type="button"
          onClick={handleIssue}
          disabled={!actor.trim() || busy === 'issue'}
          className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
        >
          {busy === 'issue' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '签发令牌'}
        </button>
        {issued && (
          <>
            <span className="ai-divider" />
            <span className="acct-mono truncate" title={issued.token} data-testid="ingest-issued-token">{issued.token}</span>
            <button type="button" className="rowact-btn" title="复制令牌(只显示这一次)" onClick={handleCopy}><Copy /></button>
            <button type="button" className="rowact-btn" title="收起" onClick={() => setIssued(null)}><X /></button>
          </>
        )}
      </section>

      {tokens === null ? (
        <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
          <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载收录令牌…
        </p>
      ) : tokens.length === 0 ? (
        <section className="surface-card card-pad rounded-[var(--r-card)]">
          <p className="tiny-meta">还没有签发过收录令牌。</p>
        </section>
      ) : (
        <section className="surface-card rounded-[var(--r-card)] overflow-hidden" aria-label="收录令牌列表">
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <th className="acct-th" style={{ width: 160 }}>调用方</th>
                  <th className="acct-th" style={{ width: 110 }}>令牌</th>
                  <th className="acct-th">来源</th>
                  <th className="acct-th" style={{ width: 80 }}>日额度</th>
                  <th className="acct-th" style={{ width: 140 }}>签发</th>
                  <th className="acct-th" style={{ width: 140 }}>最近推送</th>
                  <th className="acct-th" style={{ width: 90 }}>状态</th>
                  <th className="acct-th" aria-label="操作" style={{ width: 56 }} />
                </tr>
              </thead>
              <tbody>
                {tokens.map((token) => {
                  const names = (token.bindings || []).map((binding) => binding.name);
                  return (
                  <tr
                    key={token.id}
                    className={`acct-row ${drawerOpen && drawerId === token.id ? 'is-sel' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`${token.actor} 的来源`}
                    onClick={() => openDrawer(token)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDrawer(token); } }}
                  >
                    <td><span className="acct-name" title={token.actor}>{token.actor}</span></td>
                    <td><span className="acct-mono">{token.token_preview}</span></td>
                    <td>
                      {names.length
                        ? <span className="tok-sources" title={names.join(' · ')}>{names.join(' · ')}</span>
                        : <span className="tok-sources is-none">–</span>}
                    </td>
                    <td className="acct-n">{fmtNum(token.daily_limit)}</td>
                    <td><span className="acct-mono">{fmtTime(token.created_at)}</span></td>
                    <td><span className="acct-mono">{fmtTime(token.last_used_at)}</span></td>
                    <td>
                      {token.is_active
                        ? <span className="stamp stamp-ok">有效</span>
                        : <span className="stamp stamp-idle">已吊销</span>}
                    </td>
                    <td onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                      <span className="rowacts">
                        {busy === `token:${token.id}` ? (
                          <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
                        ) : token.is_active ? (
                          <button type="button" className="rowact-btn is-danger" title={`吊销 ${token.actor}`} onClick={() => handleRevoke(token)}>
                            <Ban />
                          </button>
                        ) : null}
                      </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <SyncDrawerShell
        open={drawerOpen && Boolean(shown)}
        onClose={() => setDrawerOpen(false)}
        icon={<KeyRound />}
        title={shown?.actor || ''}
        sub={shown?.token_preview || ''}
        stamp={shown ? (shown.is_active
          ? <span className="stamp stamp-ok">有效</span>
          : <span className="stamp stamp-idle">已吊销</span>) : null}
        label={`${shown?.actor || '令牌'} · 来源`}
      >
        {shown && (
          <section>
            <div className="drawer-sec-title">来源</div>
            <BindList
              rows={bindings.map((binding) => ({
                key: binding.source_id,
                id: binding.source_id,
                name: binding.name,
                mono: binding.source_id,
                pushedAt: binding.last_pushed_at,
              }))}
              onUnbind={handleUnbind}
              busyKey={bindBusy}
            />
            {shown.is_active && (
              <BindAdd label="来源" groups={addGroups} onBind={handleBind} busy={bindBusy === 'add'} />
            )}
          </section>
        )}
      </SyncDrawerShell>
    </>
  );
}
