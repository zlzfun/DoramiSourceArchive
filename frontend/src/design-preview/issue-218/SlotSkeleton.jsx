import { clampNumber, lineWidths } from './slotBlocks';

// 「逐段占位」:一片未完成时,按它原文的顶层块逐块画骨架,留在原位置;这片完成时整组换成中文。
// 块沿用正文里的元素(p / h2 / h3 / ul / blockquote / pre / table),外边距、行高、缩进、表格边线都由现网
// .markdown-body 规则给出,骨架高度与真实排版同一把尺子;行条用现网 .skeleton(颜色、流光、减少动效降级同中栏 / 早报)。
// 每行占一个正文行高(1lh),条高 0.8em;段落行数按原文篇幅与实测栏宽估算,代码按行长(ch),表格按单元格字数。
// 整组对读屏隐藏;停止态(失败 / 暂停 / 取消)骨架停走。
function Lines({ widths }) {
  return widths.map((width, index) => (
    <span key={index} className="r2-slot-line">
      <span className="skeleton" style={{ width: `${width}%` }} />
    </span>
  ));
}

function Cell({ chars }) {
  return (
    <span className="r2-slot-line">
      <span className="skeleton" style={{ width: `${clampNumber(chars * 0.55, 1.6, 12).toFixed(1)}em` }} />
    </span>
  );
}

function SlotBlock({ block, lineEm, quoteEm }) {
  switch (block.type) {
    case 'heading': {
      const major = block.level <= 2;
      const Tag = major ? 'h2' : 'h3';
      return <Tag><Lines widths={lineWidths(block.chars, lineEm / (major ? 21 / 17 : 18 / 17))} /></Tag>;
    }
    case 'list':
      return (
        <ul className="r2-slot-list">
          {block.items.map((chars, index) => (
            <li key={index}>
              <span className="skeleton r2-slot-dot" />
              <Lines widths={lineWidths(chars, lineEm - 1.4)} />
            </li>
          ))}
        </ul>
      );
    case 'quote':
      return <blockquote><p><Lines widths={lineWidths(block.chars, quoteEm)} /></p></blockquote>;
    case 'code':
      return (
        <pre className="r2-slot-pre">
          {block.lines.map((chars, index) => (
            <span key={index} className="r2-slot-line">
              {chars > 0 && <span className="skeleton" style={{ width: `min(100%, ${chars}ch)` }} />}
            </span>
          ))}
        </pre>
      );
    case 'datatable': {
      const [head = [], ...rows] = block.rows;
      return (
        <table className="r2-slot-table">
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
        <div className="r2-slot-math">
          <span className="skeleton" style={{ width: `min(80%, ${clampNumber(block.chars * 0.3, 6, 28).toFixed(1)}em)` }} />
        </div>
      );
    case 'image':
      return <div className="r2-slot-image"><span className="skeleton" /></div>;
    default:
      return <p><Lines widths={lineWidths(block.chars, lineEm)} /></p>;
  }
}

export default function SlotSkeleton({ blocks, metrics, stopped }) {
  const lineEm = metrics.width / metrics.font;
  const quoteEm = (metrics.width - 20) / metrics.font;
  return (
    <div className={`r2-slot skeleton-delay${stopped ? ' is-stopped' : ''}`} aria-hidden="true">
      {blocks.map((block, index) => (
        <SlotBlock key={index} block={block} lineEm={lineEm} quoteEm={quoteEm} />
      ))}
    </div>
  );
}
