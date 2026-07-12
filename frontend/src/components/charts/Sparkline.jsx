/**
 * 迷你趋势柱(sparkline,A 每日聚合端点波):无轴无网格、单色、逐柱 <title> 数值。
 * dataviz 口径:sparkline 免图例(容器标题即命名);零值日画 2px 井底短柱占位
 * (保持日历对齐,可与有值日区分);全零直接不渲染(无信息不占位)。
 * values 与 labels 按日对齐(升序,含今天)。
 */
import { C_PRIMARY } from './chartUtils';

export default function Sparkline({ values = [], labels = [], height = 16, color = C_PRIMARY, title = '近 7 天趋势' }) {
  if (!values.length || values.every((v) => !v)) return null;
  const barW = 5;
  const gap = 2;
  const width = values.length * (barW + gap) - gap;
  const max = Math.max(...values);
  return (
    <svg width={width} height={height} role="img" aria-label={title} className="shrink-0">
      {values.map((v, i) => {
        const h = v > 0 ? Math.max(2, Math.round((v / max) * height)) : 2;
        return (
          <rect
            key={labels[i] ?? i}
            x={i * (barW + gap)}
            y={height - h}
            width={barW}
            height={h}
            rx={1}
            fill={v > 0 ? color : 'var(--dorami-border-strong)'}
            fillOpacity={v > 0 ? 0.85 : 0.6}
          >
            <title>{`${labels[i] ?? ''} · ${v}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}
