// 个人聚合令牌(dfeed_)能调的全部 HTTP 接口——设置 → 聚合接口照这张清单渲染(issue #315)。
// 这是页面上的单点:后端在 /api/public/feed/ 下每加、改、删一个路由,这里跟着动。
// frontend/test/feedEndpoints.test.mjs 拿它和后端的路由、路由认的参数、条数与窗口的上下限、
// 交付计量里的接口名(services/token_usage.py)逐项对账,对不上测试就红。
// 纯数据 + 纯函数,无 React 依赖。单订阅令牌(dsub_)的接口不在这里:那种令牌没有界面(issue #315 拍板 6)。

// 后端的数(对账测试逐个比对):文章 JSON 的条数上限、Markdown / Atom 的条数上限、
// Atom 不下发正文时摘要取正文的前多少字、事件榜窗口(小时)与件数。
export const FEED_LIMITS = {
  articles: 500,
  articlesExport: 200,
  atomSummaryChars: 280,
  eventWindowDefault: 168,
  eventWindowMax: 336,
  eventLimit: 10,
};

const page = (max) => ({ names: ['skip', 'limit'], label: 'skip / limit', desc: `分页偏移与条数，limit 上限 ${max}`, limitMax: max });

// 参数说明:names 是后端真正认的参数名(对账用),label 省略时就显示 names[0];adminDesc 是管理员令牌的说法;
// default 是说明里写出来的布尔默认值(对账用)。
const P = {
  dates: { names: ['publish_date_start', 'publish_date_end'], label: 'publish_date_start / _end', desc: '发布时间窗口（YYYY-MM-DD），生成日报最常用' },
  types: { names: ['content_types'], desc: '逗号分隔的内容类型，如 rss_article,web_article' },
  sources: { names: ['source_ids'], desc: '逗号分隔的来源；仅取与你已订阅来源的交集', adminDesc: '逗号分隔的来源' },
  search: { names: ['search'], desc: '标题关键词过滤' },
  content: { names: ['include_content'], default: true, desc: '是否下发正文，默认 true；传 false 仅取元数据' },
  // Atom 没有「只取元数据」:不下发正文时,每条仍带一段取自正文开头的摘要
  contentAtom: { names: ['include_content'], default: true, desc: `是否下发正文，默认 true；传 false 只给正文前 ${FEED_LIMITS.atomSummaryChars} 字的摘要` },
  window: {
    names: ['window_hours'],
    desc: `榜单窗口（小时），默认 ${FEED_LIMITS.eventWindowDefault} 即近 ${FEED_LIMITS.eventWindowDefault / 24} 天，48 即近 2 天；范围 1–${FEED_LIMITS.eventWindowMax}`,
  },
  limit: { names: ['limit'], desc: `返回几件事，1–${FEED_LIMITS.eventLimit}，默认 ${FEED_LIMITS.eventLimit}`, limitMax: FEED_LIMITS.eventLimit },
  members: { names: ['include_members'], default: false, desc: '传 true 时每件事带上它的全部报道，默认 false' },
};

// 组 = 取到的范围不同的两类内容:文章按订阅裁剪(管理员不受限),热点事件对谁都是全站。
// key / label 与交付计量的接口组(utils/deliveryChannels.js 的 DELIVERY_GROUPS)同名;
// channel 是这条接口在计量里的名字——运维 → 账户抽屉「接口调用」写的就是它。
// example:query = 示例地址带的查询串;path = 示例里换掉的路径(带占位符的接口);
//   tokenInUrl = 示例把令牌写在地址里(RSS 工具带不了请求头,issue #315 拍板 5)。
export const FEED_ENDPOINT_GROUPS = [
  {
    key: 'articles',
    label: '文章',
    scope: 'subscribed',
    endpoints: [
      {
        key: 'articles', method: 'GET', path: '/public/feed/articles', channel: 'feed/articles', format: 'JSON',
        example: { query: 'limit=20' },
        params: [P.dates, P.types, P.sources, P.search, P.content, page(FEED_LIMITS.articles)],
      },
      {
        key: 'articles.md', method: 'GET', path: '/public/feed/articles.md', channel: 'feed/articles.md', format: 'Markdown',
        example: { query: 'limit=20' },
        params: [P.dates, P.types, P.sources, P.search, page(FEED_LIMITS.articlesExport)],
      },
      {
        key: 'articles.xml', method: 'GET', path: '/public/feed/articles.xml', channel: 'feed/articles.xml', format: 'Atom',
        example: { tokenInUrl: true },
        params: [P.dates, P.types, P.sources, P.search, P.contentAtom, page(FEED_LIMITS.articlesExport)],
      },
    ],
  },
  {
    key: 'events',
    label: '热点事件',
    scope: 'site',
    endpoints: [
      {
        key: 'events', method: 'GET', path: '/public/feed/events', channel: 'feed/events', format: 'JSON',
        example: { query: 'include_members=true' },
        params: [P.window, P.limit, P.members],
      },
      {
        key: 'event', method: 'GET', path: '/public/feed/events/{event_id}', channel: 'feed/events/{id}', format: 'JSON',
        example: { path: '/public/feed/events/128' },
        params: [P.window],
      },
    ],
  },
];

export const FEED_ENDPOINTS = FEED_ENDPOINT_GROUPS.flatMap((group) => group.endpoints);

// 这一组取到的范围(数据,不是说明):全站的那组对谁都一样;按订阅裁剪的那组,管理员的令牌不受订阅限制。
export function feedScopeLabel(group, adminScope = false) {
  return group.scope === 'site' || adminScope ? '全站公开内容' : '已订阅来源';
}

export function feedEndpointUrl(apiRoot, endpoint) {
  return `${apiRoot}${endpoint.path}`;
}

// 调用示例:默认是带请求头的 curl;tokenInUrl 的那条是一行可以直接贴进 RSS 工具的地址。
export function feedEndpointExample(apiRoot, endpoint) {
  const example = endpoint.example || {};
  const url = `${apiRoot}${example.path || endpoint.path}`;
  if (example.tokenInUrl) return `${url}?token=dfeed_你的令牌`;
  const query = example.query ? `?${example.query}` : '';
  return `curl -H "Authorization: Bearer $DORAMI_TOKEN" \\\n  "${url}${query}"`;
}

// 这条接口认的参数:[显示名, 说明]。
export function feedEndpointParams(endpoint, adminScope = false) {
  return endpoint.params.map((param) => [
    param.label || param.names[0],
    adminScope && param.adminDesc ? param.adminDesc : param.desc,
  ]);
}
