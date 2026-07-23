// 公告正文的受限 markdown 子集渲染(v3.18 互通波):仅识别 **加粗** 与
// [文字](http(s)://链接),其余一律纯文本——零依赖、零 HTML 注入面。
// 单独成文件以满足 react-refresh「组件文件只导出组件」约束(消费方:
// AnnouncementBanner 横幅渲染、AnnouncementsPanel 预览与列表摘要)。

const INLINE_TOKEN = /\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;

function renderInline(text, keyBase) {
  const nodes = [];
  let cursor = 0;
  let match;
  INLINE_TOKEN.lastIndex = 0;
  while ((match = INLINE_TOKEN.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    if (match[1] !== undefined) {
      nodes.push(<strong key={`${keyBase}-${match.index}`}>{match[1]}</strong>);
    } else {
      nodes.push(
        <a key={`${keyBase}-${match.index}`} href={match[3]} target="_blank" rel="noopener noreferrer">
          {match[2]}
        </a>,
      );
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

// 正文 → React 节点(按行拆分,空行忽略,行间 <br>)。
export function renderAnnouncementContent(content) {
  const lines = String(content || '').trim().split('\n').filter((line) => line.trim() !== '');
  return lines.map((line, i) => (
    <span key={i}>
      {i > 0 && <br />}
      {renderInline(line, i)}
    </span>
  ));
}

// 管理面列表摘要用:剥掉标记还原纯文本(摘要行不该出现 ** 与 [ ]( ) 源码)。
export function stripAnnouncementMarkup(content) {
  return String(content || '')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '$1')
    .replace(/\n+/g, ' ')
    .trim();
}
