// 搜索命中高亮:把 text 中匹配 query 的片段包进 <mark class="search-hl">。
// · 大小写不敏感;query 为空 / 无匹配 / 空文本时原样返回字符串(不产生多余节点)。
// · 用捕获组 split,奇数位即命中片段(保留原文大小写);正则特殊字符先转义。
// 返回值可能是字符串或 (字符串 | <mark>)[] 数组,React 均可直接渲染。
export function highlightMatch(text, query) {
  const s = String(text ?? '');
  const q = (query || '').trim();
  if (!s || !q) return s;
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  let re;
  try {
    re = new RegExp(`(${escaped})`, 'ig');
  } catch {
    return s;
  }
  const parts = s.split(re);
  if (parts.length <= 1) return s;
  return parts.map((part, i) =>
    i % 2 === 1 ? <mark key={i} className="search-hl">{part}</mark> : part,
  );
}
