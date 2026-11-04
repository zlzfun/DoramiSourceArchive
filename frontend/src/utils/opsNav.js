// 运维管理信息架构(issue #114):首页即目录 → 8 个满幅功能页;页内板块可深链。
// 本文件是页 / 板块的单一注册表 + 路由解析,纯函数、无 React 依赖(frontend/test/opsNav.test.mjs)。
// 归属规则见 docs/architecture/admin-ops.md「板块归属规则」:先按「这是谁的事」归页,页内按 观测 → 策略 → 台账 排序。

// 页按重要度排序(面包屑 ▾ 弹层即此顺序):账户 > 阅读与榜单 > 其它。
// blocks:[id, 名称, { root?: 仅根管理员可见 }];id 即深链末段,页内 DOM 锚点 = opsBlockId(page, block)。
export const OPS_PAGES = [
  {
    id: 'accounts', name: '账户', icon: 'users', usesDays: true,
    blocks: [['growth', '账户增长'], ['guest', '游客'], ['top', '活跃用户 Top', { root: true }], ['table', '账户管理', { root: true }], ['audit', '操作审计']],
  },
  {
    id: 'reading', name: '阅读与榜单', icon: 'chart', usesDays: false,
    blocks: [['kpi', '内容概览'], ['heat', '各源热度'], ['rank', '榜单快照']],
  },
  {
    id: 'reader', name: '读者功能', icon: 'brain', usesDays: true,
    blocks: [['usage', '每日用量'], ['switches', '开关与默认'], ['defaults', '默认订阅'], ['brief', '早报与兴趣']],
  },
  {
    id: 'podcast', name: '播客', icon: 'podcast', usesDays: false,
    blocks: [['overview', '概览'], ['asr', 'ASR 配额'], ['tasks', '单集处理'], ['audio', '中文精简音频']],
  },
  {
    id: 'analysis', name: '分析与标签', icon: 'tags', usesDays: true,
    blocks: [['metrics', '分析指标'], ['backfill', '完整分析回填'], ['catalog', '标签目录']],
  },
  {
    id: 'external', name: '存储与外部资源', icon: 'hard-drive', usesDays: false,
    blocks: [['storage', '存储与备份'], ['media', '媒体库'], ['xapi', 'X API']],
  },
  {
    id: 'user-sources', name: '自定源', icon: 'rss', usesDays: false,
    blocks: [['sources', '自定源']],
  },
  {
    id: 'inbox', name: '反馈与公告', icon: 'message', usesDays: false,
    blocks: [['feedback', '反馈收件箱'], ['notice', '公告管理']],
  },
];

export const OPS_PAGE_IDS = OPS_PAGES.map((p) => p.id);
// Map 查找:普通对象会让 `constructor` / `__proto__` 这类地址命中继承属性(codex r1 #4)。
const PAGE_BY_ID = new Map(OPS_PAGES.map((p) => [p.id, p]));

export function opsPage(id) {
  return PAGE_BY_ID.get(id) || null;
}

export function opsBlocks(pageId, { rootAdmin = true } = {}) {
  const page = opsPage(pageId);
  if (!page) return [];
  return page.blocks
    .filter(([, , opts]) => rootAdmin || !opts?.root)
    .map(([id, name]) => ({ id, name }));
}

// 页内板块的 DOM 锚点 id(落位 / 聚焦 / wash 高亮都认它)。
export function opsBlockId(pageId, blockId) {
  return `ops-${pageId}-${blockId}`;
}

// 旧五档 + 板块(pendingFocus = { sub, zone })→ 新页 / 板块。zone 是旧 DOM 锚点 `admin-<zone>` 的后缀。
const LEGACY_SUB_PAGE = { user: 'accounts', ai: 'reader', taxonomy: 'analysis', engage: 'inbox', content: 'reading' };
const LEGACY_ZONE = {
  'podcast-asr-quota': ['podcast', 'asr'], // 设置柜「ASR 配额」回指(App.openAdminPodcastQuota 的旧载荷)
};

// 路由目标:{ page: null | pageId, block: null | blockId }。page = null 即首页。
export function normalizeOpsTarget(target) {
  if (!target) return { page: null, block: null };
  let { page = null, block = null } = target;
  if (!page && (target.sub || target.zone)) {
    const legacy = target.zone && Object.hasOwn(LEGACY_ZONE, target.zone) ? LEGACY_ZONE[target.zone] : null;
    if (legacy) [page, block] = legacy;
    else page = Object.hasOwn(LEGACY_SUB_PAGE, target.sub || '') ? LEGACY_SUB_PAGE[target.sub] : null;
  }
  const hit = PAGE_BY_ID.get(page);
  if (!hit) return { page: null, block: null };
  const known = hit.blocks.some(([id]) => id === block);
  return { page, block: known ? block : null };
}

// hash 路径段(`#/admin/<page>/<block>` 去掉 `#/admin` 之后)↔ 目标。
export function parseOpsPath(rest) {
  // 非法百分号编码(如 `%`)不能抛到 App 初始导航里:解不开就当没给(回退合法路由)
  const decode = (s) => { try { return decodeURIComponent(s); } catch { return ''; } };
  const parts = String(rest || '').split('/').filter(Boolean).map(decode);
  return normalizeOpsTarget({ page: parts[0] || null, block: parts[1] || null });
}

export function formatOpsPath({ page = null, block = null } = {}) {
  const t = normalizeOpsTarget({ page, block });
  if (!t.page) return '';
  return t.block ? `${t.page}/${t.block}` : t.page;
}
