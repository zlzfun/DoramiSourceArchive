export const RANKING_AXES = [
  ['topic', '话题'],
  ['industry', '产业'],
  ['entity', '实体'],
];

export function rankingMovement(value) {
  if (value === null || value === undefined || value === '') {
    return { label: '新', direction: 'new' };
  }
  const number = Number(value);
  if (!Number.isFinite(number)) return { label: '新', direction: 'new' };
  if (number === 0) return { label: '持平', direction: 'flat' };
  return number > 0
    ? { label: `↑ ${number}`, direction: 'up' }
    : { label: `↓ ${Math.abs(number)}`, direction: 'down' };
}

export function rankingTrendPath(points, width = 160, height = 42) {
  const values = (points || []).map((point) => Number(point.occurrence_count || 0));
  if (!values.length) return '';
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = Math.max(1, max - min);
  return values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

export function scoreBasisLabel(value) {
  if (value === 'full_transcript') return '全文终评';
  if (value === 'show_notes') return '简介初评';
  return '全文评分';
}
