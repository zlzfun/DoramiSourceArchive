import { useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { formatDateTime, formatRelativeTime } from '../../utils/datetime';

/**
 * 推送绑定清单(issue #344)。令牌抽屉里列它的来源,源检视器里列在推它的令牌——同一套行:
 * 名称 + 等宽小字(源 id / 令牌尾号)· 最近一批 · 解绑。
 */
export function BindList({ rows, onUnbind, busyKey = '' }) {
  if (!rows.length) return null;
  return (
    <div className="bind-list">
      {rows.map((row) => (
        <div key={row.key} className={`bind-row ${row.muted ? 'is-muted' : ''}`}>
          <span className="bind-main">
            <span className="acct-name" title={row.name}>{row.name}</span>
            <span className="acct-mono" title={row.mono}>{row.mono}</span>
          </span>
          <span className="acct-mono bind-time" title={row.pushedAt ? formatDateTime(row.pushedAt) : undefined}>
            {formatRelativeTime(row.pushedAt, '–')}
          </span>
          {busyKey === row.key ? (
            <span className="rowact-btn" aria-hidden="true"><Loader2 className="animate-spin" /></span>
          ) : (
            <button
              type="button"
              className="rowact-btn is-danger"
              title={`解绑 ${row.name}`}
              aria-label={`解绑 ${row.name}`}
              disabled={Boolean(busyKey)}
              onClick={() => onUnbind(row)}
            >
              <X />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** 加一条绑定:下拉(可分组)+「绑定」。选项为空时不渲染。 */
export function BindAdd({ label, groups, onBind, busy = false, selectClassName = 'form-input form-input-inline' }) {
  const [value, setValue] = useState('');
  const hasOptions = groups.some((group) => group.options.length > 0);
  if (!hasOptions) return null;
  const submit = async () => {
    if (!value || busy) return;
    const done = await onBind(value);
    if (done !== false) setValue('');
  };
  return (
    <div className="bind-add">
      <select className={selectClassName} aria-label={label} value={value} onChange={(e) => setValue(e.target.value)}>
        <option value="">{label}</option>
        {groups.map((group) => (
          group.label ? (
            <optgroup key={group.label} label={group.label}>
              {group.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </optgroup>
          ) : group.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)
        ))}
      </select>
      <button
        type="button"
        className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
        disabled={!value || busy}
        onClick={submit}
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '绑定'}
      </button>
    </div>
  );
}
