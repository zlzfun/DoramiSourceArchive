// 窗口化页码条(规模化波):首尾页 + 当前页邻域 + 省略号,取代逐页平铺按钮
// (平铺在大规模数据下会渲染上百个页钮)。复用 .pager/.pager-btn 既有范式类。
function windowedPages(page, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const anchors = [...new Set([1, total, page - 1, page, page + 1])]
    .filter((p) => p >= 1 && p <= total)
    .sort((a, b) => a - b);
  const out = [];
  let prev = 0;
  for (const p of anchors) {
    if (p - prev > 1) out.push(`gap-${prev}`);
    out.push(p);
    prev = p;
  }
  return out;
}

// 表脚范式(issue #76 归并稿 P2 #15):「共 N 条 · 第 a–b 条」+ 页码,所有管理面表格
// 同一句式;单页时只留计数(有 extra 时一并显示),不画页码。
export function TableFoot({ total, page, pageSize, onPage, extra = null }) {
  const safeTotal = Number(total || 0);
  const totalPages = Math.max(1, Math.ceil(safeTotal / Math.max(1, pageSize)));
  const safePage = Math.min(Math.max(1, page), totalPages);
  if (safeTotal === 0 && !extra) return null;
  const start = safeTotal === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const end = Math.min(safePage * pageSize, safeTotal);
  return (
    <div className="table-foot">
      <span className="tiny-meta">
        {extra}{extra ? ' · ' : ''}共 {safeTotal.toLocaleString()} 条{safeTotal > pageSize ? ` · 第 ${start}–${end} 条` : ''}
      </span>
      <Pager page={safePage} totalPages={totalPages} onPage={onPage} />
    </div>
  );
}

export default function Pager({ page, totalPages, onPage }) {
  if (totalPages <= 1) return null;
  const safePage = Math.min(Math.max(1, page), totalPages);
  return (
    <div className="pager">
      <button
        type="button"
        className="pager-btn"
        disabled={safePage <= 1}
        onClick={() => onPage(safePage - 1)}
        aria-label="上一页"
      >
        ‹
      </button>
      {windowedPages(safePage, totalPages).map((p) => (
        typeof p === 'number' ? (
          <button
            key={p}
            type="button"
            className={`pager-btn ${p === safePage ? 'is-on' : ''}`}
            aria-current={p === safePage ? 'page' : undefined}
            onClick={() => onPage(p)}
          >
            {p}
          </button>
        ) : (
          <span key={p} className="px-1 tiny-meta select-none" aria-hidden="true">…</span>
        )
      ))}
      <button
        type="button"
        className="pager-btn"
        disabled={safePage >= totalPages}
        onClick={() => onPage(safePage + 1)}
        aria-label="下一页"
      >
        ›
      </button>
    </div>
  );
}
