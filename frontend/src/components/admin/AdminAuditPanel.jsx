import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchAdminAuditLog } from '../../api';
import { formatStamp } from './adminUtils';
import Pager from './Pager';

// 操作审计(v3.19 多管理员波):中间件对命中管理面前缀的非 GET 请求逐条落行,
// 多管理员之间互相可查。summary 为空的行退化显示「METHOD /path」等宽原文;
// 4xx 行同样保留——「谁试图删最后一个管理员」也是审计要回答的问题。
// 规模化波:服务端分页(skip/limit),前端只持有当前页。
const AUDIT_PAGE_SIZE = 15;

function statusStamp(code) {
  if (code >= 500) return 'stamp-bad';
  if (code >= 400) return 'stamp-warn';
  return 'stamp-ok';
}

export default function AdminAuditPanel({ days, showToast }) {
  const [data, setData] = useState(null); // {items, total} | null = 加载中
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  const load = useCallback(async (targetPage) => {
    setLoading(true);
    try {
      setData(await fetchAdminAuditLog(days, {
        skip: (targetPage - 1) * AUDIT_PAGE_SIZE,
        limit: AUDIT_PAGE_SIZE,
      }));
    } catch (error) {
      showToast(error.message || '获取操作审计失败', 'error');
      setData((prev) => prev ?? { items: [], total: 0 });
    } finally {
      setLoading(false);
    }
  }, [days, showToast]);

  // 时间窗变化归位第一页;翻页/首载取对应页。
  useEffect(() => { setPage(1); }, [days]);
  useEffect(() => { load(page); }, [load, page]);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));

  // 数据收缩(如窗口改小)后当前页越界时回落到末页。
  useEffect(() => {
    if (data && page > totalPages) setPage(totalPages);
  }, [data, page, totalPages]);

  return (
    <>
      <div className="zone-head">
        <span className="zone-title">操作审计</span>
        <span className="zone-hint">近 {days} 天的管理写操作;被拒绝的尝试(4xx)同样记录</span>
        <span className="zone-acts">
          <button
            type="button"
            className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            onClick={() => load(page)}
            disabled={loading}
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} 刷新
          </button>
        </span>
      </div>

      <section className="surface-card rounded-[var(--r-card)] overflow-hidden">
        {data === null ? (
          <p className="p-6 tiny-meta">加载中…</p>
        ) : total === 0 ? (
          <p className="p-6 text-center tiny-meta">近 {days} 天没有管理写操作记录。</p>
        ) : (
          <>
            <div className="acct-scroll">
              <table className="acct-table">
                <thead>
                  <tr>
                    <th className="acct-th" style={{ width: 150 }}>时间</th>
                    <th className="acct-th" style={{ width: 140 }}>操作者</th>
                    <th className="acct-th">操作</th>
                    <th className="acct-th" style={{ width: 80 }}>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={it.id} className="acct-row">
                      <td><span className="acct-mono">{formatStamp(it.at)}</span></td>
                      <td>
                        <span className="acct-user">
                          <span className="acct-avatar">{(it.username || '?').charAt(0).toUpperCase()}</span>
                          <span className="acct-name">{it.username}</span>
                        </span>
                      </td>
                      <td>
                        {it.summary ? (
                          <span title={`${it.method} ${it.path}`}>{it.summary}</span>
                        ) : (
                          <span className="acct-mono">{it.method} {it.path}</span>
                        )}
                      </td>
                      <td>
                        <span className={`stamp ${statusStamp(it.status_code)}`}>{it.status_code}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {totalPages > 1 && (
              <div className="flex flex-wrap items-center gap-2 border-t border-[var(--dorami-border)] px-4 py-2.5">
                <span className="tiny-meta">
                  共 {total} 条 · 第 {(page - 1) * AUDIT_PAGE_SIZE + 1}–{Math.min(page * AUDIT_PAGE_SIZE, total)} 条
                </span>
                <Pager page={page} totalPages={totalPages} onPage={setPage} />
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}
