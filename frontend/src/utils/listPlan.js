import { dayKeyOf } from './readerTime';

/* 列表渲染计划:按日期组标出每组首条(日期头落在它身上)。grouping=false 时整列视为一组。
   桌面与移动壳共用。(v3.55 issue #27 取消屏蔽后,折叠行随之退役,计划只剩分组。) */
export function buildListPlan(articles, grouping) {
  const plan = [];
  let currentKey = null;
  articles.forEach((article, index) => {
    const key = grouping ? dayKeyOf(article) : '__all__';
    const first = key !== currentKey;
    currentKey = key;
    plan.push({ type: 'article', article, dayKey: key, showLabel: grouping && first, index });
  });
  return plan;
}
