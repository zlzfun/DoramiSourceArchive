// 内容类型(issue #178)的读者面呈现规则。词表与判定都在后端(services/content_classification.py),
// 条目带 kind / kind_label / event_type / event_label,分面带 {kind, label, count};
// 这里只决定「怎么摆」,不再维护一份中文名表。

// 「资讯」是默认值:列表不挂类型签、筛选条不单列。
const DEFAULT_KIND = 'news';

// 版本更新用紧凑行(单行标题、无摘要),取代原「动态」容器的短条目扫读形。
const COMPACT_KINDS = new Set(['release']);

/** 条目列表上的类型签文字;资讯或尚未判定时返回空串(不挂签)。 */
export function kindChipLabel(article) {
  const kind = article?.kind;
  if (!kind || kind === DEFAULT_KIND) return '';
  return article?.kind_label || '';
}

/** 是否用紧凑行渲染。 */
export function isCompactKind(article) {
  return COMPACT_KINDS.has(article?.kind);
}

/**
 * 阅读窗眉头「源 · ___」的第二段:非资讯写内容类型(论文 / 版本更新…),
 * 资讯写事件类型(模型发布 / 行业动态…),两者都没有时留空。
 */
export function kindKickerLabel(article) {
  return kindChipLabel(article) || article?.event_label || '';
}

/**
 * 筛选条选项:分面里计数大于 0 的类型,保持后端词表顺序。当前选中的类型即使计数归零
 * (例如叠加了「未读」后)也保留,读者才看得见、点得掉它。
 */
export function kindFilterOptions(facets, selected = '') {
  if (!Array.isArray(facets)) return [];
  return facets
    .filter((facet) => facet && facet.kind && facet.kind !== DEFAULT_KIND
      && (Number(facet.count) > 0 || facet.kind === selected))
    .map((facet) => ({ kind: facet.kind, label: facet.label || facet.kind, count: Number(facet.count) }));
}

// ── 类型筛选的作用域绑定 ──
// 选中值记成 {key, kind}:key 是选中时的作用域(容器 / 源 / 标签 / 轴 / 收藏)。

export const EMPTY_KIND_CHOICE = Object.freeze({ key: '', kind: '' });

/** 当前生效的类型筛选:只在选中时的作用域里有效,作用域一变即为空(同一帧就不会带旧类型发请求)。 */
export function kindFilterFor(choice, scopeKey) {
  return choice?.kind && choice.key === scopeKey ? choice.kind : '';
}

/**
 * 作用域变化后应保存的选中值:离开选中时的作用域即永久清除,返回原作用域也不复活。
 * 无需变化时原样返回同一对象,调用方据此判断是否 setState。
 */
export function reconcileKindChoice(choice, scopeKey) {
  return choice?.kind && choice.key !== scopeKey ? EMPTY_KIND_CHOICE : choice;
}

/**
 * 分析轮询:已加载条目在响应里的类型变化,记为分面增量 [{from, to}](未判定记为空串)。
 * 筛选下的成员变化不走增量,由 polledMembershipChanged 判定后整窗重拉(codex R2)。
 */
export function polledKindChanges(articles, fresh) {
  const changes = [];
  if (!Array.isArray(articles) || !fresh) return changes;
  for (const article of articles) {
    const next = fresh.get(article?.id);
    if (!next) continue;
    const from = article.kind || '';
    const to = next.kind || '';
    if (from !== to) changes.push({ from, to });
  }
  return changes;
}

/**
 * 分析轮询:按类型 / 标签筛选时,在途条目不在本轮响应(同一窗口 [0, N))里,说明窗口成员变了——
 * 可能已改判出筛选范围,也可能被补位或新到的条目挤出窗口。两种情况本地都分辨不了,也不能在本地
 * 扣总数(翻页可能已拿到服务端的新总数),调用方应整窗重拉一份带总数与分面的快照。
 * 不带筛选时分析结果不影响成员,返回 false。
 */
export function polledMembershipChanged(articles, inFlightIds, polledIds, { kindFilter = '', tagSearch = false } = {}) {
  if (!kindFilter && !tagSearch) return false;
  if (!Array.isArray(articles) || !inFlightIds || !polledIds) return false;
  return articles.some((article) => inFlightIds.has(article?.id) && !polledIds.has(article?.id));
}

/**
 * 按类型变化修正分面计数:旧类型减一、新类型加一(计数从 0 变 1 时筛选选项随之出现)。
 * 只覆盖已加载条目;其余范围的变化等下次列表加载。无变化时原样返回。
 */
export function applyKindFacetDelta(facets, changes) {
  if (!Array.isArray(facets) || !Array.isArray(changes) || changes.length === 0) return facets;
  const delta = new Map();
  for (const { from, to } of changes) {
    if (from === to) continue;
    if (from) delta.set(from, (delta.get(from) || 0) - 1);
    if (to) delta.set(to, (delta.get(to) || 0) + 1);
  }
  if (![...delta.values()].some(Boolean)) return facets;
  return facets.map((facet) => (
    delta.get(facet?.kind)
      ? { ...facet, count: Math.max(0, Number(facet.count || 0) + delta.get(facet.kind)) }
      : facet
  ));
}

/**
 * 只把增量应用到轮询开始时那一代分面上:同作用域手动刷新已拿到服务端新计数(代次 +1)后,
 * 晚到的旧轮询不得再加减一次。state:{key, gen, items};basis:轮询开始时的 {key, gen}。
 */
export function applyPolledFacetDelta(state, basis, changes) {
  if (!state || !basis || state.key !== basis.key || state.gen !== basis.gen) return state;
  const items = applyKindFacetDelta(state.items, changes);
  return items === state.items ? state : { ...state, items };
}
