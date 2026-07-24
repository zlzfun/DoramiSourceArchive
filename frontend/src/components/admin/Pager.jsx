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
