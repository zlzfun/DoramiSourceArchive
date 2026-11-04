// 后端生成的音轨以 /api/... 发布；原节目通常是 HTTP(S) 绝对地址。
// 列表、收听入口与会话恢复共用判定，避免列表说「可听」却没有播放入口。
export function isPlayableAudioUrl(value) {
  if (typeof value !== 'string') return false;
  const url = value.trim();
  if (url.startsWith('/')) {
    return !url.startsWith('//') && !url.includes('\\') && !/\s/.test(url);
  }
  if (!/^https?:\/\//i.test(url)) return false;
  try {
    return ['http:', 'https:'].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}
