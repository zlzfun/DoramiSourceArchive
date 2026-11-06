import { useCallback, useEffect, useState } from 'react';
import { Ban, Copy, KeyRound, Loader2, X } from 'lucide-react';
import { createIngestToken, fetchIngestTokens, revokeIngestToken } from '../../api';
import { useConfirm } from '../../hooks/useConfirm';

const fmtNum = (n) => Number(n || 0).toLocaleString();
// 令牌时间存的是带时区的 UTC(…+00:00);统一按本地时间显示。
const pad2 = (n) => String(n).padStart(2, '0');
const fmtTime = (value) => {
  if (!value) return '–';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
};

/**
 * 收录令牌(issue #262 波 1,运维 → 自定源与收录)。调用方可以是 Agent、自动化流程或人。
 *
 * 签发(明文只回显这一次)/ 吊销。收录建的临时源与其它源一起在「源管理」里治理
 * (列入 / 改名 / 读者面隐藏;issue #262 第三波把这里的源列表搬了过去)。
 */
export default function IngestPanel({ showToast, refreshTick = 0 }) {
  const confirm = useConfirm();
  const [tokens, setTokens] = useState(null);
  const [actor, setActor] = useState('');
  const [issued, setIssued] = useState(null); // { actor, token }
  const [busy, setBusy] = useState('');

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
                  <th className="acct-th" style={{ width: 180 }}>调用方</th>
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
    </>
  );
}
