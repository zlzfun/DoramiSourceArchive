// 订阅 Tab 的来源分组(issue #230 第 2 批;样页拍板 25「合表」):三种形态的订阅合在一张表里——
// 文章按信息角色分组(官方 / 媒体 / 个人 / 榜单 / 自定源,与源栏同口径),播客、社交各一组。
// 角色表与判定函数由调用方注入(sourceTaxonomy 的 SOURCE_ROLES / sourceRoleOf),本模块保持纯函数,单测直接核对。
const SHAPE_GROUPS = [
  ['podcast', '播客'],
  ['social', '社交'],
];

export function subscriptionGroups(sources, { shapeOf, roleOf, roles }) {
  const byRole = {};
  const byShape = { podcast: [], social: [] };
  for (const source of sources || []) {
    const shape = shapeOf(source.source_id);
    if (byShape[shape]) {
      byShape[shape].push(source);
      continue;
    }
    const role = roleOf(source);
    (byRole[role] ||= []).push(source);
  }
  return [
    ...roles.map((role) => ({ key: role.key, label: role.label, list: byRole[role.key] || [] })),
    ...SHAPE_GROUPS.map(([key, label]) => ({ key, label, list: byShape[key] })),
  ].filter((group) => group.list.length > 0);
}
