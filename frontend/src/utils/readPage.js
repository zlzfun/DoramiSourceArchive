// 手机正文页的纯逻辑(issue #230 批 3c;样页 docs/design/issue-230-mobile/ 正文 / ⋯ 菜单屏,拍板 3 / 7 / 26)。
// 不碰 DOM、不碰 React,便于 node --test 直接测。

// 「⋯」底页的菜单(样页 read-menu,第 4 轮):只留菜单、不带预览卡;按使用频率排——
// 译为中文 / 显示原文(只对有英文原文的文章)· 查看原文 | 复制站内链接 · 分享给… | 标为未读 · 标记该源全部已读。
// 收藏在右上角的星、分享浮层在右上角的分享钮,这里不重复。
// 返回描述(key / label / disabled 与分隔线),图标与动作由正文页按 key 接上(动作与长按动作单同源:useReaderState.buildArticleMenuItems)。
//   translatable   这篇有英文原文且 AI 可用(正文页的 aiEnabled && !activeIsChinese)
//   translationOn  正在看中文视图;hasTranslation 已有译文(缓存或已完成)
//   translating    后台翻译在途;bodyReady 正文已到(没到时翻译项置灰,不隐藏——菜单结构稳定)
//   podcast        播客单集(翻译的是节目简介,原文是节目页面)
//   canShare       环境支持 Web Share(navigator.share),不支持就不画「分享给…」(呈现的诚实)
//   unread         这篇当前是否未读(打开即标读,通常为 false →「标为未读」)
//   hasSource      有 source_url;没有时「查看原文」置灰
export function readMenuSpec({
  translatable = false,
  translationOn = false,
  hasTranslation = false,
  translating = false,
  bodyReady = true,
  podcast = false,
  canShare = false,
  unread = false,
  hasSource = true,
} = {}) {
  const items = [];
  if (translatable) {
    const base = translationOn ? '显示原文'
      : hasTranslation ? '显示中文' : podcast ? '将节目简介译为中文' : '译为中文';
    items.push({ key: 'translate', label: translating ? `${base}（翻译中）` : base, disabled: !bodyReady });
  }
  items.push({ key: 'open', label: podcast ? '打开节目页面' : '查看原文', disabled: !hasSource });
  items.push({ type: 'sep' });
  items.push({ key: 'copy', label: '复制站内链接' });
  if (canShare) items.push({ key: 'sysshare', label: '分享给…' });
  items.push({ type: 'sep' });
  items.push({ key: 'read', label: unread ? '标为已读' : '标为未读' });
  items.push({ key: 'markall', label: '标记该源全部已读' });
  return items;
}

// 正文右上的镀层钮随滚动退场(样页动效清单「镀层退场」):往下读(越过 80px 且这一下往下 > 6px)藏起,
// 往上一划(> 6px)或回到顶部 80px 内就回来;减少动效时常显;不是读者自己滚的(位置恢复、译文对位)不改状态。
export const READ_HEAD_HIDE_AT = 80;
export const READ_HEAD_STEP = 6;
export function readHeadHidden(prev, { y, dy, reduced = false, intent = true }) {
  if (reduced) return false;
  if (y < READ_HEAD_HIDE_AT) return false;
  if (!intent) return prev;
  if (dy > READ_HEAD_STEP) return true;
  if (dy < -READ_HEAD_STEP) return false;
  return prev;
}
