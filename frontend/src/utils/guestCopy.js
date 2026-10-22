/**
 * 游客模式(issue #213)的措辞与常量。
 */

/** 设置柜里需要账号的分区 → 登录入口里的一句说明;键即设置柜分区 id(桌面柜与移动设置栈共用)。 */
export const GUEST_LOCKED_SECTIONS = {
  opml: '导入或导出你的订阅源，方便搬到别的阅读器。',
  feedback: '提交反馈，回复也会出现在这里。',
  feed: '用个人令牌把订阅接到 RSS 阅读器或脚本里。',
  mcp: '在 Agent 工具里按你的订阅检索与问答。',
  skill: '下载一份按本站地址配好的资讯技能。',
};

/**
 * 游客早报的措辞:游客版是按默认订阅编排的共享一版,后端选篇理由写的是「你订阅的」,
 * 给游客看时改成「默认订阅的」。只改显示,快照本身不动(同一版对所有游客共用,登录后看的是自己的早报)。
 */

const REASON_REWRITES = [
  ['来自你订阅的', '来自默认订阅的'],
  ['来自你未订阅的', '来自默认订阅外的'],
  ['今日订阅中', '今日默认订阅中'],
  ['不在你的订阅内', '不在默认订阅内'],
  ['你订阅的', '默认订阅的'],
];

export function guestSelectionReason(text) {
  if (!text) return text;
  return REASON_REWRITES.reduce((acc, [from, to]) => acc.split(from).join(to), text);
}

/** 整版改写:条目与快照里的 selection_reason 一并换成游客措辞;返回新对象,原 edition 不变。 */
export function guestEdition(edition) {
  if (!edition?.items) return edition;
  return {
    ...edition,
    items: edition.items.map((item) => ({
      ...item,
      selection_reason: guestSelectionReason(item.selection_reason),
      snapshot: item.snapshot
        ? { ...item.snapshot, selection_reason: guestSelectionReason(item.snapshot.selection_reason) }
        : item.snapshot,
    })),
  };
}
