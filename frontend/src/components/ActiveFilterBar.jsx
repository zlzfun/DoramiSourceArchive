import { X } from 'lucide-react';

/**
 * 当前生效筛选条：把每个生效的筛选渲染成可一键移除的胶囊，并在多于一项时提供「清除全部」。
 * 让藏在下拉/高级面板里的筛选条件显性化，省去「打开下拉 → 滚动找全部 → 点击」的清除路径。
 *
 * items: Array<{ key, label, value?, onRemove }>
 *   - label: 维度名（如「数据来源」）
 *   - value: 当前取值展示（如「Anthropic News」），可选
 *   - onRemove: 清除该项
 * onClearAll: 清除全部
 */
export default function ActiveFilterBar({ items, onClearAll, className = '' }) {
  if (!items || items.length === 0) return null;
  return (
    <div className={`active-filter-bar ${className}`}>
      <span className="active-filter-label">当前筛选</span>
      {items.map(item => (
        <button
          key={item.key}
          type="button"
          onClick={item.onRemove}
          className="active-filter-chip"
          title={`清除筛选：${item.label}${item.value ? ` · ${item.value}` : ''}`}
        >
          <span className="active-filter-chip-label">{item.label}</span>
          {item.value && <span className="active-filter-chip-value">{item.value}</span>}
          <X className="h-3 w-3" />
        </button>
      ))}
      {items.length > 1 && (
        <button type="button" onClick={onClearAll} className="active-filter-clear">
          <X className="h-3.5 w-3.5" /> 清除全部
        </button>
      )}
    </div>
  );
}
