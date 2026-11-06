// 单条收录表单的纯逻辑(issue #262 推送入口统一):链接解析、载荷组装、回执文案。
// 载荷与推送接口同一形状(契约 docs/contracts/ingest_push.md)。

// x.com / twitter.com 的帖子链接里直接带着作者与帖子 id。
const X_STATUS = /^https?:\/\/(?:www\.|mobile\.)?(?:x|twitter)\.com\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/i;

export function parseStatusLink(url) {
  const match = X_STATUS.exec(String(url || '').trim());
  return match ? { handle: match[1], post_id: match[2] } : null;
}

/** 表单 → 收录载荷(与推送接口同一形状,契约 docs/contracts/ingest_push.md)。 */
export function buildIngestItem(kind, form) {
  const trimmed = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, String(value || '').trim()]));
  const publishDate = trimmed.publish_date ? new Date(trimmed.publish_date).toISOString() : '';
  const base = { kind, title: trimmed.title, note: trimmed.note, publish_date: publishDate };
  if (kind === 'social_post') {
    const parsed = parseStatusLink(trimmed.url);
    return {
      ...base,
      url: trimmed.url,
      content: trimmed.content,
      post_id: trimmed.post_id || parsed?.post_id || '',
      author: { handle: trimmed.handle || parsed?.handle || '' },
    };
  }
  if (kind === 'podcast_episode') {
    return {
      ...base,
      url: trimmed.url,
      guid: trimmed.guid,
      audio_url: trimmed.audio_url,
      summary: trimmed.summary,
      source: { feed_url: trimmed.feed_url },
    };
  }
  return {
    ...base,
    url: trimmed.url,
    content: trimmed.content,
    summary: trimmed.summary,
    source: trimmed.source_id ? { source_id: trimmed.source_id } : {},
  };
}

/** 回执:新建 / 已有 / 新建临时源 / 拒收。 */
export function describeReceipt(result) {
  if (!result) return null;
  const source = result.source_id || '';
  switch (result.status) {
    case 'created':
      return result.source_created
        ? { tone: 'warn', stamp: '已新建', text: `新建临时源 ${source}` }
        : { tone: 'ok', stamp: '已新建', text: `收进 ${source}` };
    case 'duplicate':
      return { tone: 'idle', stamp: '已存在', text: `同一条已在 ${source}` };
    case 'duplicate_url':
      return { tone: 'idle', stamp: '已存在', text: `同一链接已在 ${source}` };
    case 'duplicate_content':
      return { tone: 'idle', stamp: '已存在', text: `${source} 里已有同一内容` };
    case 'refreshed':
      return { tone: 'ok', stamp: '已更新', text: `补全了 ${source} 里的已有条目` };
    default:
      return { tone: 'err', stamp: '未收录', text: result.reason || '收录失败' };
  }
}
