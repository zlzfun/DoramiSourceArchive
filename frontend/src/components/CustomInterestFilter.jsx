import { canFilterCustomInterests } from '../utils/readerInterestQuery';

export default function CustomInterestFilter({ items = [], capabilities, value = '', onChange }) {
  if (!items.length) return null;
  return <select className="form-input text-sm min-h-11 w-auto max-w-full" aria-label="筛选自定义兴趣"
    value={value} disabled={!canFilterCustomInterests(capabilities)}
    onChange={(event) => onChange(event.target.value)}>
    <option value="">自定义兴趣</option>
    {items.map((item) => <option key={item.id} value={String(item.id)} disabled={item.status !== 'active'}>
      {item.name}{item.status !== 'active' ? '（已暂停）' : ''}
    </option>)}
  </select>;
}
