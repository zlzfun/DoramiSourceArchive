import { dayKeyOf } from './readerTime';

/* 列表渲染计划:按日期组把命中屏蔽的条目折进组尾一行;展开的组把它们按原序放回(降调)。
   grouping=false 时整列视为一组。桌面与移动壳共用。 */
export function buildListPlan(articles, grouping, expandedDays) {
  const plan = [];
  let group = null;
  const flushGroup = () => {
    if (!group) return;
    if (group.muted.length > 0) {
      plan.push({
        type: 'fold',
        dayKey: group.key,
        tags: Array.from(new Set(group.muted.flatMap((a) => a.interest_muted || []))),
        expanded: group.expanded,
        showLabel: grouping && group.first, // 整组都被折叠:日期头落在折叠行上
      });
    }
    group = null;
  };
  articles.forEach((article, index) => {
    const key = grouping ? dayKeyOf(article) : '__all__';
    if (!group || group.key !== key) {
      flushGroup();
      group = { key, muted: [], expanded: expandedDays.has(key), first: true };
    }
    const isMuted = (article.interest_muted || []).length > 0;
    if (isMuted) {
      group.muted.push(article);
      if (!group.expanded) return;
    }
    plan.push({
      type: 'article',
      article,
      dayKey: key,
      showLabel: grouping && group.first,
      muted: isMuted,
      index,
    });
    group.first = false;
  });
  flushGroup();
  return plan;
}

