import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchAdminAuditLog } from '../../api';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { formatStamp } from './adminUtils';
import { avatarInitial, avatarHue } from '../../utils/avatarColor';
import Pager from './Pager';
import { ThFilter, ThSearch } from './TableTh';

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
  // 检索(v3.42 M11):操作者/关键词(防抖 300ms)+ 结果档,全部服务端生效。
  const [operatorInput, setOperatorInput] = useState('');
  const [queryInput, setQueryInput] = useState('');
  const [statusScope, setStatusScope] = useState(''); // '' | 'ok' | 'denied'
  const operator = useDebouncedValue(operatorInput, 300);
  const query = useDebouncedValue(queryInput, 300);

  // 请求代次守卫(v3.43.2 codex 检视):非第一页改检索条件时,「新条件旧页」与
  // 「归位第一页」两个请求并发在途,旧页后到会覆盖第一页结果——只允许最新代次
  // 落 state/弹错/收 loading。
  const loadGenRef = useRef(0);
  const load = useCallback(async (targetPage) => {
    const gen = ++loadGenRef.current;
    setLoading(true);
    try {
      const res = await fetchAdminAuditLog(days, {
        skip: (targetPage - 1) * AUDIT_PAGE_SIZE,
        limit: AUDIT_PAGE_SIZE,
        operator,
        q: query,
        status: statusScope,
      });
      if (gen === loadGenRef.current) setData(res);
    } catch (error) {
      if (gen === loadGenRef.current) {
        showToast(error.message || '获取操作审计失败', 'error');
        setData((prev) => prev ?? { items: [], total: 0 });
      }
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [days, operator, query, statusScope, showToast]);

  // 时间窗/检索条件变化归位第一页;翻页/首载取对应页。
  useEffect(() => { setPage(1); }, [days, operator, query, statusScope]);
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
          <p className="p-6 text-center tiny-meta">
            {(operator || query || statusScope) ? (
              <>
                没有匹配当前检索条件的审计记录。
                <button
                  type="button"
                  className="kpi-sub-link"
                  onClick={() => { setOperatorInput(''); setQueryInput(''); setStatusScope(''); }}
                >
                  清除检索
                </button>
              </>
            ) : `近 ${days} 天没有管理写操作记录。`}
          </p>
        ) : (
          <>
            <div className="acct-scroll">
              <table className="acct-table is-fixed">
                <thead>
                  <tr>
                    <th className="acct-th" style={{ width: 150 }}>时间</th>
                    <ThSearch label="操作者" value={operatorInput} onChange={setOperatorInput} placeholder="搜索操作者" active={Boolean(operator)} width={160} />
                    <ThSearch label="操作" value={queryInput} onChange={setQueryInput} placeholder="搜索操作 / 目标 / 路径" active={Boolean(query)} inputWidth={196} />
                    <ThFilter label="结果" value={statusScope} onChange={setStatusScope} options={[['', '全部'], ['ok', '成功'], ['denied', '被拒']]} width={90} />
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={it.id} className="acct-row is-static">
                      <td><span className="acct-mono">{formatStamp(it.at)}</span></td>
                      <td>
                        <span className="acct-user">
                          <span className="acct-avatar avatar-letter" style={{ '--avatar-h': avatarHue(it.username) }}>{avatarInitial(it.username)}</span>
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
