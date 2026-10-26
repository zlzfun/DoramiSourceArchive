import { memo } from 'react';
import { clampNumber, lineWidths } from '../utils/translationBlocks';

// 正文渐进翻译(issue #218 第三稿「逐段占位」):一个块还没有译文时,按它原文的顶层结构在原位置画骨架,
// 译文就绪即原位替换。块沿用正文里的元素(p / h2 / h3 / ul / blockquote / pre / table),外边距、行高、缩进、
// 表格边线都由 .markdown-body 规则给出,骨架高度与真实排版同一把尺子;行条用 .skeleton(颜色、流光、
// 减少动效降级同中栏 / 早报)。每行占一个正文行高(1lh),条高 0.8em;段落行数按原文篇幅与实测栏宽估算,
// 代码按行长(ch),表格按单元格字数。整组对读屏隐藏;停止态(失败 / 额度用尽 / 取消)骨架停走。
function Lines({ widths }) {
  return widths.map((width, index) => (
    <span key={index} className="reader-tr-slot-line">
      <span className="skeleton" style={{ width: `${width}%` }} />
    </span>
  ));
}

function Cell({ chars }) {
  return (
    <span className="reader-tr-slot-line">
      <span className="skeleton" style={{ width: `${clampNumber(chars * 0.55, 1.6, 12).toFixed(1)}em` }} />
    </span>
  );
}

function SlotShape({ shape, lineEm, quoteEm }) {
  switch (shape.type) {
    case 'heading': {
      const major = shape.level <= 2;
      const Tag = major ? 'h2' : 'h3';
      return <Tag><Lines widths={lineWidths(shape.chars, lineEm / (major ? 21 / 17 : 18 / 17))} /></Tag>;
    }
    case 'list':
      return (
        <ul className="reader-tr-slot-list">
          {shape.items.map((chars, index) => (
            <li key={index}>
              <span className="skeleton reader-tr-slot-dot" />
              <Lines widths={lineWidths(chars, lineEm - 1.4)} />
            </li>
          ))}
        </ul>
      );
    case 'quote':
      return <blockquote><p><Lines widths={lineWidths(shape.chars, quoteEm)} /></p></blockquote>;
    case 'code':
      return (
        <pre className="reader-tr-slot-pre">
          {shape.lines.map((chars, index) => (
            <span key={index} className="reader-tr-slot-line">
              {chars > 0 && <span className="skeleton" style={{ width: `min(100%, ${chars}ch)` }} />}
            </span>
          ))}
        </pre>
      );
    case 'datatable': {
      const [head = [], ...rows] = shape.rows;
      return (
        <table>
          <thead>
            <tr>{head.map((chars, index) => <th key={index}><Cell chars={chars} /></th>)}</tr>
          </thead>
          <tbody>
            {rows.map((cells, row) => (
              <tr key={row}>{cells.map((chars, index) => <td key={index}><Cell chars={chars} /></td>)}</tr>
            ))}
          </tbody>
        </table>
      );
    }
    case 'math':
      return (
        <div className="reader-tr-slot-math">
          <span className="skeleton" style={{ width: `min(80%, ${clampNumber(shape.chars * 0.3, 6, 28).toFixed(1)}em)` }} />
        </div>
      );
    case 'image':
      return <div className="reader-tr-slot-image"><span className="skeleton" /></div>;
    default:
      return <p><Lines widths={lineWidths(shape.chars, lineEm)} /></p>;
  }
}

// memo:形状数组随块集合变化才换,进行中每 2 秒的无变化快照不重画骨架
export default memo(function TranslationSkeleton({ shapes, metrics, stopped }) {
  const lineEm = metrics.width / metrics.font;
  const quoteEm = (metrics.width - 20) / metrics.font;
  return (
    <div className={`reader-tr-slot skeleton-delay${stopped ? ' is-stopped' : ''}`} aria-hidden="true">
      {shapes.map((shape, index) => (
        <SlotShape key={index} shape={shape} lineEm={lineEm} quoteEm={quoteEm} />
      ))}
    </div>
  );
});
