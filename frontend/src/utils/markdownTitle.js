/**
 * 正文首个标题与文章标题重复时的去重（阅读窗渲染侧）
 *
 * 阅读窗自己已用 <h1 class="reader-pane-title"> 画了文章标题，若正文首行又是同名标题
 * （典型：哆啦美日报的 `# 🤖 哆啦美 AI 资讯日报 · 2026-07-29`，以及不少 changelog/
 * markdown 源把标题烤进正文的习惯），读者会连看两遍。
 *
 * 为什么在渲染侧收口而不是改生成/抓取：
 * · 存量归档正文里已经烤进了这个标题，改 prompt 修不了历史；
 * · 正文作为独立 markdown 导出时（feed .md / 技能包 / 归档同步）首行标题是**正确**的，
 *   删掉反而破坏导出契约与档案忠实性。故只在「标题已由容器画出」的场景剥离。
 */

// 归一化比较键：去掉标题记号、emoji/符号、空白与常见分隔标点，只留可比较的文字与数字。
// 目的是让「# 🤖 哆啦美 AI 资讯日报 · 2026-07-29」与「哆啦美 AI 资讯日报 · 2026-07-29」相等。
function normalizeHeadingKey(text) {
  return String(text || '')
    .replace(/^\s*#{1,6}\s+/, '')       // 前导 ATX 标题记号
    .replace(/[*_`~]/g, '')             // 强调/代码记号
    .replace(/\p{Extended_Pictographic}/gu, '') // emoji
    .replace(/[\s·•\-—–|:：,，.。、/\\]/g, '') // 空白与分隔标点
    .toLowerCase();
}

/**
 * 若正文的第一个非空行是与 title 同名的 ATX 标题，则剥离该行（连同其后的空行）。
 * 不匹配时原样返回——绝不猜测、绝不动第二行以后的内容。
 */
export function stripDuplicateLeadingHeading(body, title) {
  if (!body || !title) return body;
  const key = normalizeHeadingKey(title);
  if (!key) return body;

  const lines = String(body).split('\n');
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i += 1;   // 跳过前导空行
  if (i >= lines.length) return body;

  const first = lines[i].trim();
  if (!/^#{1,6}\s+/.test(first)) return body;            // 首个内容行不是标题 → 不处理
  if (normalizeHeadingKey(first) !== key) return body;   // 不同名 → 不处理

  let j = i + 1;
  while (j < lines.length && !lines[j].trim()) j += 1;   // 一并吃掉标题后的空行
  return lines.slice(j).join('\n');
}

export default stripDuplicateLeadingHeading;
