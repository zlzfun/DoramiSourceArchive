import { useCallback, useEffect, useState } from 'react';
import { Ban, Check, Copy, EyeOff, Eye, KeyRound, ListPlus, Loader2, Pencil, X } from 'lucide-react';
import {
  createIngestToken,
  fetchIngestSources,
  fetchIngestTokens,
  ingestSourceAction,
  renameIngestSource,
  revokeIngestToken,
} from '../../api';
import { useConfirm } from '../../hooks/useConfirm';

const fmtNum = (n) => Number(n || 0).toLocaleString();
const fmtTime = (value) => (value ? String(value).slice(0, 16).replace('T', ' ') : '–');
const SHAPE_LABEL = { article: '文章', social: '社交', podcast: '播客' };

/**
 * Agent 收录(issue #262 波 1,运维 → 自定源与收录)。
 *
 * 收录令牌:签发(明文只回显这一次)/ 吊销;收录源:推送时遇到未登记出处自动建的临时源,
 * 行动作 列入(→ 观察期,进入发现目录与日报候选)、下架 / 恢复(隐藏并拒收后续推送)、改名。
 */
export default function IngestPanel({ showToast, refreshTick = 0 }) {
  const confirm = useConfirm();
  const [tokens, setTokens] = useState(null);
  const [sources, setSources] = useState(null);
  const [actor, setActor] = useState('');
  const [issued, setIssued] = useState(null); // { actor, token }
  const [busy, setBusy] = useState('');
  const [editing, setEditing] = useState(null); // { source_id, name }

  const load = useCallback(async () => {
    try {
      const [tokenRes, sourceRes] = await Promise.all([fetchIngestTokens(), fetchIngestSources()]);
      setTokens(tokenRes.items || []);
      setSources(sourceRes.items || []);
    } catch (error) {
      showToast(error.message || '获取收录数据失败', 'error');
      setTokens((prev) => prev ?? []);
      setSources((prev) => prev ?? []);
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

  const handleRevoke = async (token) => {
    if (!(await confirm(`确认吊销「${token.actor}」的收录令牌？使用它的 Agent 将立即无法推送。`))) return;
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

  const handleSourceAction = async (item, action, done) => {
    setBusy(`source:${item.source_id}`);
    try {
      await ingestSourceAction(item.source_id, action);
      showToast(done, 'success');
      load();
    } catch (error) {
      showToast(error.message || '操作失败', 'error');
    } finally {
      setBusy('');
    }
  };

  const handleRename = async () => {
    const name = (editing?.name || '').trim();
    if (!name) return;
    setBusy(`source:${editing.source_id}`);
    try {
      await renameIngestSource(editing.source_id, name);
      setEditing(null);
      load();
    } catch (error) {
      showToast(error.message || '改名失败', 'error');
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      <div className="zone-head" style={{ marginTop: 18 }}>
        <span className="zone-title">Agent 收录</span>
      </div>

      <section className="surface-card ai-switchboard rounded-[var(--r-card)] mb-3">
        <KeyRound className="h-4 w-4 text-slate-500" aria-hidden="true" />
        <input
          type="text"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleIssue(); }}
          placeholder="Agent 名称"
          aria-label="Agent 名称"
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

      {tokens !== null && tokens.length > 0 && (
        <section className="surface-card rounded-[var(--r-card)] overflow-hidden mb-3" aria-label="收录令牌">
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <th className="acct-th" style={{ width: 180 }}>Agent</th>
                  <th className="acct-th" style={{ width: 110 }}>令牌</th>
                  <th className="acct-th" style={{ width: 90 }}>日额度</th>
                  <th className="acct-th" style={{ width: 130 }}>签发</th>
                  <th className="acct-th" style={{ width: 130 }}>最近推送</th>
                  <th className="acct-th" style={{ width: 90 }}>状态</th>
                  <th className="acct-th" aria-label="操作" style={{ width: 56 }} />
                </tr>
              </thead>
              <tbody>
                {tokens.map((token) => (
                  <tr key={token.id} className="acct-row is-static">
                    <td><span className="acct-name" title={token.actor}>{token.actor}</span></td>
                    <td><span className="acct-mono">{token.token_preview}</span></td>
                    <td className="acct-n">{fmtNum(token.daily_limit)}</td>
                    <td><span className="acct-mono">{fmtTime(token.created_at)}</span></td>
                    <td><span className="acct-mono">{fmtTime(token.last_used_at)}</span></td>
                    <td>
                      {token.is_active
                        ? <span className="stamp stamp-ok">有效</span>
                        : <span className="stamp stamp-idle">已吊销</span>}
                    </td>
                    <td>
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
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {sources === null ? (
        <p className="surface-card card-pad rounded-[var(--r-card)] text-center tiny-meta">
          <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin text-slate-500" /> 正在加载收录源…
        </p>
      ) : sources.length === 0 ? (
        <section className="surface-card card-pad rounded-[var(--r-card)]">
          <p className="tiny-meta">还没有收录源。</p>
        </section>
      ) : (
        <section className="surface-card rounded-[var(--r-card)] overflow-hidden" aria-label="收录源">
          <div className="acct-scroll">
            <table className="acct-table is-fixed">
              <thead>
                <tr>
                  <th className="acct-th" style={{ width: 240 }}>源</th>
                  <th className="acct-th" style={{ width: 70 }}>形态</th>
                  <th className="acct-th" style={{ width: 72 }}>条目</th>
                  <th className="acct-th" style={{ width: 130 }}>最近收录</th>
                  <th className="acct-th" style={{ width: 150 }}>收录者</th>
                  <th className="acct-th" style={{ width: 96 }}>状态</th>
                  <th className="acct-th" aria-label="操作" style={{ width: 104 }} />
                </tr>
              </thead>
              <tbody>
                {sources.map((item) => {
                  const rowBusy = busy === `source:${item.source_id}`;
                  const isEditing = editing?.source_id === item.source_id;
                  return (
                    <tr key={item.source_id} className="acct-row is-static">
                      <td>
                        {isEditing ? (
                          <span className="flex items-center gap-1">
                            <input
                              type="text"
                              value={editing.name}
                              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                              onKeyDown={(e) => { if (e.key === 'Enter') handleRename(); if (e.key === 'Escape') setEditing(null); }}
                              aria-label="源名称"
                              maxLength={120}
                              className="form-input form-input-inline"
                              autoFocus
                            />
                            <button type="button" className="rowact-btn" title="保存" onClick={handleRename}><Check /></button>
                          </span>
                        ) : (
                          <span className="block truncate">
                            <span className="acct-name" title={item.name}>{item.name}</span>
                            <span className="acct-mono block truncate" title={item.homepage_url || item.source_id}>{item.source_id}</span>
                          </span>
                        )}
                      </td>
                      <td><span className="tiny-meta">{SHAPE_LABEL[item.shape] || item.shape}</span></td>
                      <td className="acct-n">{fmtNum(item.count)}</td>
                      <td><span className="acct-mono">{fmtTime(item.last_fetched)}</span></td>
                      <td><span className="tiny-meta truncate" title={(item.actors || []).join('、')}>{(item.actors || []).join('、') || '—'}</span></td>
                      <td>
                        {item.hidden
                          ? <span className="stamp stamp-idle">已下架</span>
                          : item.admission_status === 'provisional'
                            ? <span className="stamp stamp-warn">临时</span>
                            : <span className="stamp stamp-ok">已列入</span>}
                      </td>
                      <td>
                        <span className="rowacts">
                          {rowBusy ? (
                            <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
                          ) : (
                            <>
                              {item.admission_status === 'provisional' && !item.hidden && (
                                <button type="button" className="rowact-btn" title={`列入 ${item.name}(进入发现目录,观察期)`} onClick={() => handleSourceAction(item, 'admit', `已列入 ${item.name}`)}>
                                  <ListPlus />
                                </button>
                              )}
                              <button type="button" className="rowact-btn" title={`改名 ${item.name}`} onClick={() => setEditing({ source_id: item.source_id, name: item.name })}>
                                <Pencil />
                              </button>
                              {item.hidden ? (
                                <button type="button" className="rowact-btn" title={`恢复 ${item.name}`} onClick={() => handleSourceAction(item, 'unhide', `已恢复 ${item.name}`)}>
                                  <Eye />
                                </button>
                              ) : (
                                <button type="button" className="rowact-btn is-danger" title={`下架 ${item.name}(读者面隐藏,拒收后续推送)`} onClick={() => handleSourceAction(item, 'hide', `已下架 ${item.name}`)}>
                                  <EyeOff />
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
        </section>
      )}
    </>
  );
}
