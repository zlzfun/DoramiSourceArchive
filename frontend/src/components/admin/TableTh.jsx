// 「列头即操作」三件套(v3.42 目检返修沉淀):管理面表格统一的表头操作语言——
// 数值列点击排序、枚举列点击轮换筛选、文本列点击展开就地搜索。
// 消费方:账户管理 / 操作审计 / 用户自定源。样式同源(.acct-th/.acct-sortbtn/
// .acct-th-search),配合 .acct-table.is-fixed 的定高表头(36px,middle 对齐)与
// 定宽布局(列宽由表头定死,筛选/搜索换行集零跳动)。
import { useState } from 'react';
import { Search } from 'lucide-react';

// 排序列头:点击回调 onSort(k),同列翻向/换列取惯用初始方向由调用方裁决。
export function ThSort({ label, k, sort, order, onSort, num = false, width = 0 }) {
  const active = sort === k;
  return (
    <th
      className={`acct-th ${num ? 'is-num' : ''} ${active ? 'is-sorted' : ''}`}
      style={width ? { width } : undefined}
      aria-sort={active ? (order === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      <button type="button" className="acct-sortbtn" onClick={() => onSort(k)} title={`按${label}排序`}>
        {label}
        <span className="acct-sort-arrow" aria-hidden="true">{active ? (order === 'asc' ? '▲' : '▼') : ''}</span>
      </button>
    </th>
  );
}

// 筛选列头:点击轮换档位,列头文字即当前档(未筛选=列名);
// data-widest 隐形占位让按钮点击区恒覆盖最长档文字。
export function ThFilter({ label, value, onChange, options, width = 0 }) {
  const idx = Math.max(0, options.findIndex(([v]) => v === value));
  const next = options[(idx + 1) % options.length][0];
  const cycle = [label, ...options.slice(1).map(([, lbl]) => lbl)].join(' → ');
  const widest = [label, ...options.slice(1).map(([, lbl]) => lbl)]
    .reduce((a, b) => (b.length > a.length ? b : a));
  return (
    <th className={`acct-th ${value ? 'is-filtered' : ''}`} style={width ? { width } : undefined}>
      <button
        type="button"
        className="acct-sortbtn"
        data-widest={widest}
        onClick={() => onChange(next)}
        title={`点击轮换筛选：${cycle}`}
      >
        {value ? options[idx][1] : label}
      </button>
    </th>
  );
}

// 搜索列头:静止态=列名+搜索小图标,点击展开输入框顶替列名(就地展开范式);
// Esc 清空收起、空值失焦收起,有搜索词保持展开并随筛选列同语高亮(active)。
// inputWidth:输入框宽度(默认 132,长 placeholder 的列调大避免提示语截断)。
export function ThSearch({ label, value, onChange, placeholder, active = false, width = 0, inputWidth = 0 }) {
  const [open, setOpen] = useState(false);
  return (
    <th className={`acct-th ${active ? 'is-filtered' : ''}`} style={width ? { width } : undefined}>
      {(open || value !== '') ? (
        <span className="relative inline-flex items-center">
          <Search className="pointer-events-none absolute left-1.5 h-3 w-3 text-slate-500" />
          <input
            autoFocus
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onBlur={() => { if (!value.trim()) setOpen(false); }}
            onKeyDown={(e) => { if (e.key === 'Escape') { onChange(''); setOpen(false); } }}
            placeholder={placeholder}
            className="acct-th-search"
            style={inputWidth ? { width: inputWidth } : undefined}
            aria-label={placeholder}
          />
        </span>
      ) : (
        <button type="button" className="acct-sortbtn" onClick={() => setOpen(true)} title={placeholder}>
          {label}
          <Search className="h-2.5 w-2.5" aria-hidden="true" />
        </button>
      )}
    </th>
  );
}
