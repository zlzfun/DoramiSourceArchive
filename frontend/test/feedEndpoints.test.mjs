// 设置 → 聚合接口的接口清单(issue #315)与后端对账。
// 这张卡是读者知道「令牌能调什么」的唯一途径,过去靠人手维护:Atom 接口上线四个月没列,热点事件两条也没列,
// 参数说明里的「limit 上限 500」只对 JSON 成立。这里把清单(src/utils/feedEndpoints.js)和后端源码逐项比:
//   · /api/public/feed/ 下的路由,一条不多一条不少;
//   · 清单写的参数后端真的认,后端认而清单没写的只能是下面 UNLISTED 里点过名的;
//   · 条数上限、事件榜窗口与件数,和后端的常量一致;
//   · 每条接口在交付计量里的名字(运维 → 账户抽屉「接口调用」写的那个)对得上,组名也是同一套。
// 读的是 Python 源码而不是跑起来的应用(前端测试没有后端环境);读不出预期形状时测试会明说,不会悄悄放过。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  FEED_ENDPOINTS,
  FEED_ENDPOINT_GROUPS,
  FEED_LIMITS,
  feedEndpointExample,
  feedEndpointParams,
  feedEndpointUrl,
  feedScopeLabel,
} from '../src/utils/feedEndpoints.js';
import { DELIVERY_GROUPS } from '../src/utils/deliveryChannels.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

const FEED_PREFIX = '/api/public/feed/';
// 后端认、页面有意不写的参数(只有这些可以不在清单里;后端新增参数时要么写进清单,要么在这里点名并写明为什么)。
const UNLISTED = {
  content_type: '单个内容类型的旧写法,content_types 已经覆盖',
  has_content: '默认只给有正文的条目;页面不提这个开关',
};

function pyFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== '__pycache__') pyFiles(p, out); }
    else if (name.endsWith('.py')) out.push(p);
  }
  return out;
}

// 从 text[open](一个左括号)起,取到与它配对的右括号之间的内容;引号里的括号不计。
function parenBody(text, open) {
  let depth = 0;
  let quote = null;
  for (let i = open; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === '\\') i += 1;
      else if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(open + 1, i);
    }
  }
  throw new Error('括号没有配上:后端源码的写法变了,请更新 feedEndpoints.test.mjs 的读法');
}

// 顶层逗号拆参数表(类型标注、默认值里的逗号不拆)。
function splitTopLevel(body) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let current = '';
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (quote) {
      current += ch;
      if (ch === '\\') { current += body[i + 1] ?? ''; i += 1; } else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    if (ch === ',' && depth === 0) { parts.push(current); current = ''; continue; }
    current += ch;
  }
  if (current.trim()) parts.push(current);
  return parts.map((part) => part.trim()).filter(Boolean);
}

// 后端全部路由里落在 /api/public/feed/ 下的:{ method, path, file, params: Map<参数名, 默认值原文> }。
// 认两种声明:@app.<method>("…") 与 @<router>.<method>("…")(路由器自带的 prefix 会拼上)。
function backendFeedRoutes() {
  const routes = [];
  for (const file of pyFiles(path.join(repo, 'src/api'))) {
    const text = readFileSync(file, 'utf8');
    const rel = path.relative(repo, file);
    // include_router(…, prefix=…) 与 add_api_route 这里读不到:出现了就要先更新这条测试。
    for (const m of text.matchAll(/\binclude_router\(/g)) {
      assert.ok(!/\bprefix\s*=/.test(parenBody(text, m.index + m[0].length - 1)), `${rel}: include_router 带了 prefix,对账测试读不到这种挂法,请更新 feedEndpoints.test.mjs`);
    }
    assert.ok(!/\badd_api_route\(|\.api_route\(/.test(text), `${rel}: 出现了 add_api_route / api_route,对账测试读不到,请更新 feedEndpoints.test.mjs`);

    const prefixes = new Map([['app', '']]);
    for (const m of text.matchAll(/^(\w+)\s*=\s*APIRouter\(/gm)) {
      const args = parenBody(text, m.index + m[0].length - 1);
      prefixes.set(m[1], /\bprefix\s*=\s*(["'])(.*?)\1/.exec(args)?.[2] || '');
    }
    for (const m of text.matchAll(/^@(\w+)\.(get|post|put|patch|delete|head|options)\(\s*(["'])(.*?)\3/gm)) {
      if (!prefixes.has(m[1])) continue;   // 不是路由器的装饰器
      const full = `${prefixes.get(m[1])}${m[4]}`;
      if (!full.startsWith(FEED_PREFIX)) continue;
      const def = /\n(?:async\s+)?def\s+(\w+)\(/g;
      def.lastIndex = m.index;
      const head = def.exec(text);
      assert.ok(head, `${rel}: ${full} 后面没找到处理函数`);
      const params = new Map();
      for (const piece of splitTopLevel(parenBody(text, head.index + head[0].length - 1))) {
        const name = /^\*{0,2}(\w+)/.exec(piece)?.[1];
        if (name) params.set(name, piece.includes('=') ? piece.slice(piece.indexOf('=') + 1).trim() : '');
      }
      routes.push({ method: m[2].toUpperCase(), path: full, file: rel, fn: head[1], params });
    }
  }
  return routes;
}

// 一条路由对外认的查询参数:去掉框架注入的(request / Depends)和路径里的占位符。
function queryParams(route) {
  const inPath = new Set([...route.path.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
  return [...route.params].filter(([name, dflt]) => name !== 'request' && !inPath.has(name) && !/^Depends\(/.test(dflt)).map(([name]) => name);
}

// 取 Python 模块里一个整数常量的值:认 `N = 200` 与 `N = 24 * 7` 两种写法。
function pyInt(text, name, where) {
  const m = new RegExp(`^${name}\\s*=\\s*(\\d+)(?:\\s*\\*\\s*(\\d+))?\\s*(?:#.*)?$`, 'm').exec(text);
  assert.ok(m, `${where}: 没读到常量 ${name}(写法变了,请更新 feedEndpoints.test.mjs)`);
  return Number(m[1]) * (m[2] ? Number(m[2]) : 1);
}

const routes = backendFeedRoutes();
const routeOf = (endpoint) => routes.find((r) => r.path === `/api${endpoint.path}` && r.method === endpoint.method);

test('清单与后端 /api/public/feed/ 下的路由一一对上', () => {
  assert.ok(routes.length > 0, '一条路由都没读到:后端路由的写法变了,请更新 feedEndpoints.test.mjs 的读法');
  const backend = routes.map((r) => `${r.method} ${r.path}`).sort();
  const listed = FEED_ENDPOINTS.map((e) => `${e.method} /api${e.path}`).sort();
  assert.deepEqual(listed, backend, '设置 → 聚合接口的清单(src/utils/feedEndpoints.js)与后端路由不一致:后端加、改、删了令牌接口,清单要跟着动');
  assert.equal(new Set(FEED_ENDPOINTS.map((e) => e.key)).size, FEED_ENDPOINTS.length, '接口 key 不能重复(展开状态与复制反馈按它区分)');
});

test('清单写的参数后端都认;后端认而清单没写的只有点过名的那几个', () => {
  for (const endpoint of FEED_ENDPOINTS) {
    const route = routeOf(endpoint);
    assert.ok(route, `${endpoint.path} 在后端没有对应路由`);
    const accepted = queryParams(route);
    const listed = endpoint.params.flatMap((param) => param.names);
    assert.equal(new Set(listed).size, listed.length, `${endpoint.path} 的参数写重了`);
    for (const name of listed) assert.ok(accepted.includes(name), `${endpoint.path} 的说明里写了 ${name},后端(${route.file} · ${route.fn})并不认`);
    const missing = accepted.filter((name) => !listed.includes(name) && !(name in UNLISTED));
    assert.deepEqual(missing, [], `${endpoint.path}: 后端认这些参数而页面没写。写进 feedEndpoints.js,或在本测试的 UNLISTED 里点名并写明为什么不写`);
  }
  // UNLISTED 里的名字得真有路由在认,否则是过期的豁免
  const everAccepted = new Set(routes.flatMap(queryParams));
  for (const name of Object.keys(UNLISTED)) assert.ok(everAccepted.has(name), `UNLISTED 里的 ${name} 后端已经不认了,请删掉这条豁免`);
});

test('条数上限、事件榜窗口与件数和后端的常量一致', () => {
  const subs = read('src/api/routers/subscriptions.py');
  const events = read('src/api/routers/events.py');
  const board = read('src/services/event_board.py');

  // 文章三种格式各自的条数上限:函数体里 `safe_limit = min(max(limit, 1), <上限>)`
  for (const endpoint of FEED_ENDPOINT_GROUPS[0].endpoints) {
    const route = routeOf(endpoint);
    const start = subs.indexOf(`def ${route.fn}(`);
    const next = subs.indexOf('\n@router.', start);
    const body = subs.slice(start, next === -1 ? undefined : next);
    const cap = /safe_limit\s*=\s*min\(max\(limit,\s*1\),\s*(\w+)\)/.exec(body)?.[1];
    assert.ok(cap, `${route.fn}: 没读到条数上限(写法变了,请更新 feedEndpoints.test.mjs)`);
    const backendMax = /^\d+$/.test(cap) ? Number(cap) : pyInt(subs, cap, 'subscriptions.py');
    const listed = endpoint.params.find((param) => param.names.includes('limit'));
    assert.equal(listed.limitMax, backendMax, `${endpoint.path}: 页面写 limit 上限 ${listed.limitMax},后端是 ${backendMax}`);
    assert.ok(listed.desc.includes(String(backendMax)), `${endpoint.path}: 说明文字里没有上限 ${backendMax}`);
  }
  assert.equal(FEED_LIMITS.articles, FEED_ENDPOINT_GROUPS[0].endpoints[0].params.find((param) => param.names.includes('limit')).limitMax);

  // 事件榜:默认窗口、最大窗口、件数上限都取自 services/event_board.py 的常量
  const boardConst = (expr, where) => {
    const name = /^event_board_service\.(\w+)$/.exec(expr)?.[1];
    assert.ok(name, `${where}: 默认值不再取自 event_board_service 的常量(现在是 ${expr}),请更新 feedEndpoints.test.mjs`);
    return pyInt(board, name, 'event_board.py');
  };
  for (const endpoint of FEED_ENDPOINT_GROUPS[1].endpoints) {
    const route = routeOf(endpoint);
    assert.equal(boardConst(route.params.get('window_hours'), route.fn), FEED_LIMITS.eventWindowDefault, `${endpoint.path}: 默认窗口`);
    if (route.params.has('limit')) assert.equal(boardConst(route.params.get('limit'), route.fn), FEED_LIMITS.eventLimit, `${endpoint.path}: 默认件数`);
  }
  const upperBound = (helper) => {
    const name = new RegExp(`def ${helper}\\([^)]*\\)[^\\n]*\\n\\s+if not 1 <= \\w+ <= event_board_service\\.(\\w+):`).exec(events)?.[1];
    assert.ok(name, `events.py 的 ${helper} 写法变了,请更新 feedEndpoints.test.mjs`);
    return pyInt(board, name, 'event_board.py');
  };
  assert.equal(upperBound('_check_window'), FEED_LIMITS.eventWindowMax, '事件榜最大窗口');
  assert.equal(upperBound('_check_limit'), FEED_LIMITS.eventLimit, '事件榜件数上限');
  // 说明里的「近 N 天」是由小时数除出来的:窗口不是整天时这句话就不对了
  assert.equal(FEED_LIMITS.eventWindowDefault % 24, 0);
});

test('每条接口在交付计量里的名字对得上,组也是同一套', () => {
  const usage = read('src/services/token_usage.py');
  const channels = new Map([...usage.matchAll(/^(FEED_\w+) = "([^"]+)"$/gm)].map((m) => [m[1], m[2]]));
  const groups = new Map([...usage.matchAll(/^(GROUP_\w+) = "([a-z]+)"$/gm)].map((m) => [m[1], m[2]]));
  const groupOf = new Map([...usage.matchAll(/^\s+(FEED_\w+): (GROUP_\w+),$/gm)].map((m) => [channels.get(m[1]), groups.get(m[2])]));
  assert.ok(channels.size > 0 && groupOf.size === channels.size, 'token_usage.py 的接口登记写法变了,请更新 feedEndpoints.test.mjs');

  // 计量里以 feed/ 开头的接口,就是清单里这些——两边一条不差
  assert.deepEqual(FEED_ENDPOINTS.map((e) => e.channel).sort(), [...channels.values()].sort());
  for (const group of FEED_ENDPOINT_GROUPS) {
    for (const endpoint of group.endpoints) {
      assert.equal(groupOf.get(endpoint.channel), group.key, `${endpoint.path} 在计量里归 ${groupOf.get(endpoint.channel)} 组,清单把它放在 ${group.key} 组`);
    }
    // 组名与运维 → 账户里图例的叫法一致
    assert.equal(group.label, DELIVERY_GROUPS.find((g) => g.key === group.key)?.name, `${group.key} 组的名字与 DELIVERY_GROUPS 不一致`);
  }
});

test('范围签:文章按订阅裁剪(管理员不受限),热点事件对谁都是全站', () => {
  const [articles, events] = FEED_ENDPOINT_GROUPS;
  assert.equal(feedScopeLabel(articles, false), '已订阅来源');
  assert.equal(feedScopeLabel(articles, true), '全站公开内容');
  assert.equal(feedScopeLabel(events, false), '全站公开内容');
  assert.equal(feedScopeLabel(events, true), '全站公开内容');
  // 事件榜确实不按令牌主人的订阅裁剪:读榜时不传是谁的令牌
  const eventsPy = read('src/api/routers/events.py');
  for (const endpoint of events.endpoints) {
    const route = routeOf(endpoint);
    const start = eventsPy.indexOf(`def ${route.fn}(`);
    const next = eventsPy.indexOf('\n@router.', start);
    const call = /event_board_service\.read_(?:board|event)\(/.exec(eventsPy.slice(start, next === -1 ? undefined : next));
    assert.ok(call, `${route.fn}: 没读到取榜的调用(写法变了,请更新 feedEndpoints.test.mjs)`);
    const at = start + call.index + call[0].length - 1;
    assert.ok(!/identity|owner|username/.test(parenBody(eventsPy, at)), `${route.fn}: 取榜开始按令牌主人裁剪了,「全站公开内容」这枚范围签要重新想`);
  }
});

test('地址与调用示例', () => {
  const root = 'https://dorami.example/api';
  const byKey = Object.fromEntries(FEED_ENDPOINTS.map((e) => [e.key, e]));
  assert.equal(feedEndpointUrl(root, byKey['articles.md']), `${root}/public/feed/articles.md`);
  assert.equal(feedEndpointUrl(root, byKey.event), `${root}/public/feed/events/{event_id}`);
  // 默认是带请求头的 curl,两行
  assert.equal(feedEndpointExample(root, byKey.articles), `curl -H "Authorization: Bearer $DORAMI_TOKEN" \\\n  "${root}/public/feed/articles?limit=20"`);
  assert.equal(feedEndpointExample(root, byKey.events), `curl -H "Authorization: Bearer $DORAMI_TOKEN" \\\n  "${root}/public/feed/events?include_members=true"`);
  // 带占位符的接口,示例里换成一个真能发出去的地址(花括号留在 curl 里会被当成通配)
  const detail = feedEndpointExample(root, byKey.event);
  assert.ok(/\/public\/feed\/events\/\d+"$/.test(detail) && !/[{}]/.test(detail));
  // Atom:RSS 工具带不了请求头,示例是一行把令牌写在地址里的订阅地址(拍板 5)
  assert.equal(feedEndpointExample(root, byKey['articles.xml']), `${root}/public/feed/articles.xml?token=dfeed_你的令牌`);
  // 示例里用到的查询参数,这条接口都认
  for (const endpoint of FEED_ENDPOINTS) {
    const accepted = queryParams(routeOf(endpoint));
    for (const pair of (endpoint.example.query || '').split('&').filter(Boolean)) {
      assert.ok(accepted.includes(pair.split('=')[0]), `${endpoint.path} 的示例带了它不认的参数 ${pair}`);
    }
  }
  // 「地址里带令牌」靠的是后端从查询串读 token 这个名字
  assert.ok(read('src/api/tokens.py').includes('request.query_params.get("token")'), '后端不再从 ?token= 读令牌,Atom 的示例要改');
});

test('参数说明:管理员令牌的来源不受订阅限制;条数上限逐接口不同', () => {
  const byKey = Object.fromEntries(FEED_ENDPOINTS.map((e) => [e.key, e]));
  const sourceLine = (endpoint, admin) => feedEndpointParams(endpoint, admin).find(([name]) => name === 'source_ids')[1];
  assert.ok(sourceLine(byKey.articles, false).includes('已订阅'));
  assert.ok(!sourceLine(byKey.articles, true).includes('已订阅'));
  const limitLine = (endpoint) => feedEndpointParams(endpoint).find(([name]) => name === 'skip / limit')[1];
  assert.ok(limitLine(byKey.articles).endsWith(`上限 ${FEED_LIMITS.articles}`));
  assert.ok(limitLine(byKey['articles.md']).endsWith(`上限 ${FEED_LIMITS.articlesExport}`));
  assert.ok(limitLine(byKey['articles.xml']).endsWith(`上限 ${FEED_LIMITS.articlesExport}`));
  // Markdown 不下发可选正文:没有 include_content
  assert.ok(!feedEndpointParams(byKey['articles.md']).some(([name]) => name === 'include_content'));
  // 显示名不重复(渲染时拿它当 key)
  for (const endpoint of FEED_ENDPOINTS) {
    const names = feedEndpointParams(endpoint).map(([name]) => name);
    assert.equal(new Set(names).size, names.length);
  }
});

test('设置卡照清单渲染,不另写接口地址', () => {
  const section = read('frontend/src/components/settings/FeedTokenSection.jsx');
  assert.ok(section.includes("from '../../utils/feedEndpoints'"), '聚合接口卡应从 utils/feedEndpoints.js 取清单');
  assert.ok(section.includes('FEED_ENDPOINT_GROUPS.map('), '聚合接口卡应逐组渲染清单');
  assert.ok(!/public\/feed/.test(section), '聚合接口卡里不要再手写接口地址:加接口只改 utils/feedEndpoints.js');
});
