// 推文卡片的小口径(issue #344)。

// 转推卡片顶上那一行写谁在转推:帖子顶层的作者是转推者——显示名优先,只有账号名的写 @账号名;
// 两样都没有的旧条目才退回源名。合集源里一个源有很多账号,源名不是转推者。
export function reposterLabel(ext, sourceName = '', sourceId = '') {
  const name = String(ext?.author_name || '').trim();
  if (name) return name;
  const handle = String(ext?.author_handle || '').trim().replace(/^@/, '');
  if (handle) return `@${handle}`;
  return sourceName || sourceId;
}
