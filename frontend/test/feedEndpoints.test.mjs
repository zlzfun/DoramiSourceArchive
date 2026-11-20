// 设置 → 聚合接口的接口清单(issue #315)与后端对账。
// 这张卡是读者知道「令牌能调什么」的唯一途径,过去靠人手维护:Atom 接口上线四个月没列,热点事件两条也没列,
// 参数说明里的「limit 上限 500」只对 JSON 成立。这里把清单(src/utils/feedEndpoints.js)和后端源码逐项比:
//   · /api/public/feed/ 下的路由,一条不多一条不少;
//   · 清单写的参数后端真的认,后端认而清单没写的只能是下面 UNLISTED 里点过名的;
//   · 条数上限、事件榜窗口与件数、说明里写出来的默认值,和后端一致;
//   · 每条接口在交付计量里的名字(运维 → 账户抽屉「接口调用」写的那个)对得上,组名也是同一套。
// 读的是 Python 源码而不是跑起来的应用(前端测试没有后端环境)。原则:读不懂的写法一律失败并说明要改哪里,
// 不猜、不跳过——但只对与令牌接口有关的声明这样要求,别处的重构不该让这条测试红。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
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

const FEED_SEGMENTS = ['', 'api', 'public', 'feed'];
const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'];
const UPDATE = '请更新 frontend/test/feedEndpoints.test.mjs 的读法';
// 后端认、页面有意不写的参数(只有这些可以不在清单里;后端新增参数时要么写进清单,要么在这里点名并写明为什么)。
const UNLISTED = {
  content_type: '单个内容类型的旧写法,content_types 已经覆盖',
  has_content: '默认只给有正文的条目;页面不提这个开关',
};

// ── 读 Python 源码的几个小工具 ──

// 去掉注释,并把三引号字符串(文档串)的内容清空:里面的 #、引号、形似装饰器的行都不该进解析。换行保留,行号不变。
export function pyCode(text) {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const three = text.slice(i, i + 3);
    if (three === '"""' || three === "'''") {
      const end = text.indexOf(three, i + 3);
      const stop = end === -1 ? text.length : end;
      out += three + text.slice(i + 3, stop).replace(/[^\n]/g, ' ') + (end === -1 ? '' : three);
      i = end === -1 ? text.length : end + 3;
      continue;
    }
    const ch = text[i];
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < text.length && text[j] !== ch && text[j] !== '\n') j += text[j] === '\\' ? 2 : 1;
      out += text.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (ch === '#') {
      const end = text.indexOf('\n', i);
      i = end === -1 ? text.length : end;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

// 从 text[open](一个左括号)起,取到与它配对的右括号之间的内容;引号里的括号不计。
function parenBody(text, open, where) {
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
  throw new Error(`${where}: 括号没有配上。${UPDATE}`);
}

// 在顶层(不在括号、引号里)找分隔符的位置。
function topLevelIndexes(body, sep) {
  const found = [];
  let depth = 0;
  let quote = null;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (quote) {
      if (ch === '\\') i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
    else if (ch === sep && depth === 0) found.push(i);
  }
  return found;
}

// 顶层逗号拆参数表(类型标注、默认值里的逗号不拆)。
function splitTopLevel(body) {
  const cuts = [-1, ...topLevelIndexes(body, ','), body.length];
  return cuts.slice(1).map((end, n) => body.slice(cuts[n] + 1, end).trim()).filter(Boolean);
}

// 一段实参是不是 `名字=值`:是就拆开,不是(位置参数)返回 null。`==` 不算。
function keyword(part) {
  const at = topLevelIndexes(part, '=').find((i) => part[i + 1] !== '=' && !'=!<>'.includes(part[i - 1] || ''));
  if (at === undefined) return null;
  const name = part.slice(0, at).trim();
  return /^\w+$/.test(name) ? { name, value: part.slice(at + 1).trim() } : null;
}
const keywordArg = (parts, name) => parts.map(keyword).find((kw) => kw?.name === name)?.value;
const positionalArgs = (parts) => parts.filter((part) => !keyword(part));

// 字符串字面量的值;不是字面量返回 null。
function literal(expr) {
  const m = /^[rRuUbB]?(["'])((?:\\.|(?!\1).)*)\1$/s.exec((expr ?? '').trim());
  return m ? m[2] : null;
}

const lineOf = (text, index) => text.slice(0, index).split('\n').length;

// 这个路径(可能带 {占位符})会不会落在 /api/public/feed/ 下:占位符段能匹配任何值,所以也算。
function couldBeFeed(fullPath) {
  const segs = fullPath.split('/');
  return FEED_SEGMENTS.every((want, i) => segs[i] !== undefined && (segs[i] === want || /^\{.*\}$/.test(segs[i])));
}

function pyFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== '__pycache__') pyFiles(p, out); }
    else if (name.endsWith('.py')) out.push(p);
  }
  return out;
}

// 读一个后端文件:里面声明的路由器、全部路由的路径(判断挂载用)、落在令牌接口前缀下的路由(带处理函数的参数)、
// 以及带 prefix 的 include_router。问题不抛出,记进 problems 由调用方统一报。
function parseApiFile(file) {
  const rel = path.relative(repo, file);
  const code = pyCode(readFileSync(file, 'utf8'));
  const problems = [];
  const at = (index) => `${rel}:${lineOf(code, index)}`;
  const consts = new Map([...code.matchAll(/^(\w+)\s*=\s*(["'])([^"'\n]*)\2\s*$/gm)].map((m) => [m[1], m[3]]));
  // 字符串字面量,或本文件里的字符串常量;读不出是 null
  const str = (expr) => literal(expr) ?? consts.get((expr ?? '').trim()) ?? null;

  const routers = new Map([['app', { prefix: '' }]]);
  for (const m of code.matchAll(/^(\w+)\s*=\s*APIRouter\(/gm)) {
    const parts = splitTopLevel(parenBody(code, m.index + m[0].length - 1, at(m.index)));
    const raw = keywordArg(parts, 'prefix');
    routers.set(m[1], { prefix: raw === undefined ? '' : str(raw), at: at(m.index) });
  }

  const allPaths = [];
  const routes = [];
  const decorator = new RegExp(`^@(\\w+)\\.(${HTTP_METHODS.join('|')}|api_route|websocket)\\(`, 'gm');
  for (const m of code.matchAll(decorator)) {
    const router = routers.get(m[1]);
    if (!router) continue;   // 不是路由器的装饰器
    const where = at(m.index);
    const parts = splitTopLevel(parenBody(code, m.index + m[0].length - 1, where));
    const sub = str(positionalArgs(parts)[0] ?? keywordArg(parts, 'path'));
    if (router.prefix === null) { problems.push(`${where}: 路由器 ${m[1]} 的 prefix 读不出(${router.at})。${UPDATE}`); continue; }
    if (sub === null) { problems.push(`${where}: 这条路由的路径读不出(不是字符串字面量,也不是本文件的字符串常量)。${UPDATE}`); continue; }
    const full = `${router.prefix}${sub}`;
    allPaths.push(full);
    if (!couldBeFeed(full)) continue;

    let methods = [m[2].toUpperCase()];
    if (m[2] === 'api_route') {
      const list = /^\[([\s\S]*)\]$/.exec(keywordArg(parts, 'methods') ?? '');
      const names = list ? splitTopLevel(list[1]).map(literal) : [null];
      if (names.includes(null)) { problems.push(`${where}: ${full} 用 api_route 声明,methods 读不出。${UPDATE}`); continue; }
      methods = names.map((name) => name.toUpperCase());
    }
    const def = /\n(?:async\s+)?def\s+(\w+)\(/g;
    def.lastIndex = m.index;
    const head = def.exec(code);
    if (!head) { problems.push(`${where}: ${full} 后面没找到处理函数。${UPDATE}`); continue; }
    const params = new Map();
    for (const piece of splitTopLevel(parenBody(code, head.index + head[0].length - 1, where))) {
      if (piece === '*' || piece === '/') continue;
      const name = /^\*{0,2}(\w+)\s*(?:[:=]|$)/.exec(piece)?.[1];
      if (!name) { problems.push(`${where}: ${head[1]} 的参数段认不出:「${piece.slice(0, 60)}」。${UPDATE}`); continue; }
      const eq = topLevelIndexes(piece, '=')[0];
      if (/\balias\s*=/.test(piece)) problems.push(`${where}: ${head[1]} 的参数 ${name} 用了 alias,对外的参数名不再是 ${name};对账是按参数名比的。${UPDATE}`);
      params.set(name, eq === undefined ? '' : piece.slice(eq + 1).trim());
    }
    for (const method of methods) routes.push({ method, path: full, file: rel, fn: head[1], params });
  }

  // add_api_route:处理函数在别处,这里读不了它的参数——只要路径与令牌接口前缀有关(或读不出)就失败。
  for (const m of code.matchAll(/\b(\w+)\.add_api_route\(/g)) {
    const where = at(m.index);
    const parts = splitTopLevel(parenBody(code, m.index + m[0].length - 1, where));
    const sub = str(positionalArgs(parts)[0] ?? keywordArg(parts, 'path'));
    const prefix = routers.get(m[1])?.prefix ?? null;
    if (sub === null || prefix === null) problems.push(`${where}: add_api_route 的路径或所属路由器读不出。${UPDATE}`);
    else if (couldBeFeed(`${prefix}${sub}`)) problems.push(`${where}: ${prefix}${sub} 用 add_api_route 声明,对账测试读不了它的参数。${UPDATE}`);
    else allPaths.push(`${prefix}${sub}`);
  }

  // include_router(…, prefix=…):记下被挂的是谁,读完全部文件后再判断它与令牌接口有没有关系。
  const includes = [];
  for (const m of code.matchAll(/\b\w+\.include_router\(/g)) {
    const where = at(m.index);
    const parts = splitTopLevel(parenBody(code, m.index + m[0].length - 1, where));
    const raw = keywordArg(parts, 'prefix');
    if (raw === undefined) continue;
    const alias = /^(\w+)/.exec(positionalArgs(parts)[0] ?? '')?.[1];
    // 被挂的路由器来自哪个文件:本文件里声明的,或 `from 包 import 模块 [as 别名]` / `from 模块 import router as 别名`
    let target = alias && routers.has(alias) && alias !== 'app' ? rel : null;
    if (!target && alias) {
      for (const imp of code.matchAll(/^from\s+([\w.]+)\s+import\s+([^\n]+)$/gm)) {
        const hit = imp[2].replace(/[()]/g, '').split(',').map((item) => item.trim().split(/\s+as\s+/)).find((pair) => (pair[1] || pair[0]) === alias);
        if (!hit) continue;
        const base = `src/${imp[1].replaceAll('.', '/')}`;
        target = [`${base}/${hit[0]}.py`, `${base}.py`].find((candidate) => existsSync(path.join(repo, candidate))) || null;
        break;
      }
    }
    includes.push({ where, prefix: str(raw), target });
  }
  return { rel, routers, routes, allPaths, includes, problems };
}

// 后端全部令牌接口:{ method, path, file, fn, params: Map<参数名, 默认值原文> }。读不懂的相关写法在这里统一失败。
function backendFeedRoutes() {
  const files = pyFiles(path.join(repo, 'src/api')).map(parseApiFile);
  const problems = files.flatMap((f) => f.problems);
  const byRel = new Map(files.map((f) => [f.rel, f]));
  for (const f of files) {
    for (const inc of f.includes) {
      const target = inc.target && byRel.get(inc.target);
      if (!target || inc.prefix === null) {
        problems.push(`${inc.where}: include_router 带了 prefix,但被挂的路由器或前缀读不出,没法判断它与令牌接口有没有关系。${UPDATE}`);
      } else if (target.routes.length > 0) {
        problems.push(`${inc.where}: ${target.rel} 里声明了令牌接口,又被带 prefix(${inc.prefix})挂载——真实路径不再是源码里写的那样。${UPDATE}`);
      } else if (target.allPaths.some((p) => couldBeFeed(`${inc.prefix}${p}`))) {
        problems.push(`${inc.where}: ${target.rel} 带 prefix(${inc.prefix})挂载后有路由落在 /api/public/feed/ 下,对账测试读不到这种挂法。${UPDATE}`);
      }
    }
  }
  assert.deepEqual(problems, [], '后端有对账测试读不懂的路由写法');
  return files.flatMap((f) => f.routes);
}

// 一条路由对外认的查询参数:去掉框架注入的(request / Depends)和路径里的占位符。
function queryParams(route) {
  const inPath = new Set([...route.path.matchAll(/\{(\w+)(?::\w+)?\}/g)].map((m) => m[1]));
  return [...route.params].filter(([name, dflt]) => name !== 'request' && !inPath.has(name) && !/^Depends\(/.test(dflt)).map(([name]) => name);
}

// 取 Python 模块里一个整数常量的值:认 `N = 200` 与 `N = 24 * 7` 两种写法。
function pyInt(code, name, where) {
  const m = new RegExp(`^${name}\\s*=\\s*(\\d+)(?:\\s*\\*\\s*(\\d+))?\\s*$`, 'm').exec(code);
  assert.ok(m, `${where}: 没读到常量 ${name}。${UPDATE}`);
  return Number(m[1]) * (m[2] ? Number(m[2]) : 1);
}

// 一个函数的函数体(到下一个顶格的 def / 装饰器 / 类为止)。
function functionBody(code, fn, where) {
  const start = code.search(new RegExp(`^(?:async\\s+)?def ${fn}\\(`, 'm'));
  assert.ok(start !== -1, `${where}: 没找到函数 ${fn}。${UPDATE}`);
  const rest = code.slice(start + 1);
  const next = rest.search(/\n(?:@|def |async def |class )/);
  return rest.slice(0, next === -1 ? undefined : next);
}

const routes = backendFeedRoutes();
const routeOf = (endpoint) => routes.find((r) => r.path === `/api${endpoint.path}` && r.method === endpoint.method);
const codeOf = (rel) => pyCode(read(rel));

test('读法自检:注释、文档串、path= 与常量', () => {
  const code = pyCode([
    '"""文档串里的 @router.get("/api/public/feed/ghost") 和 # 都不算"""',
    'x = "a # 不是注释"  # 这才是注释',
    "y = 'it''s'",
  ].join('\n'));
  assert.ok(!code.includes('ghost') && !code.includes('这才是注释'));
  assert.ok(code.includes('"a # 不是注释"'));
  assert.equal(code.split('\n').length, 3, '行数不变');
  assert.deepEqual(splitTopLevel('a: int = 1, b: Dict[str, int] = f(1, 2), c="x,y"'), ['a: int = 1', 'b: Dict[str, int] = f(1, 2)', 'c="x,y"']);
  assert.deepEqual(keyword('path="/x"'), { name: 'path', value: '"/x"' });
  assert.equal(keyword('"/x"'), null);
  assert.equal(keyword('a == b'), null);
  assert.equal(literal('"/api/x"'), '/api/x');
  assert.equal(literal('PREFIX + "/x"'), null);
  assert.ok(couldBeFeed('/api/public/feed/articles') && couldBeFeed('/api/public/{kind}/articles') && couldBeFeed('/api/public/feed'));
  assert.ok(!couldBeFeed('/api/public/subscriptions/{id}/articles') && !couldBeFeed('/api/admin') && !couldBeFeed('/api/public'));
});

test('清单与后端 /api/public/feed/ 下的路由一一对上', () => {
  assert.ok(routes.length > 0, `一条路由都没读到:后端路由的写法变了。${UPDATE}`);
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

test('说明里写出来的默认值与后端一致', () => {
  let checked = 0;
  for (const endpoint of FEED_ENDPOINTS) {
    const route = routeOf(endpoint);
    for (const param of endpoint.params) {
      if (typeof param.default !== 'boolean') continue;
      const backend = route.params.get(param.names[0]);
      assert.equal(backend, param.default ? 'True' : 'False', `${endpoint.path} 的 ${param.names[0]}: 页面写默认 ${param.default},后端默认值是 ${backend}`);
      assert.ok(param.desc.includes(`默认 ${param.default}`), `${endpoint.path} 的 ${param.names[0]}: 说明文字里没有「默认 ${param.default}」`);
      checked += 1;
    }
  }
  assert.equal(checked, 3, '页面写明默认值的布尔参数:两条 include_content、一条 include_members');
});

test('条数上限、事件榜窗口与件数、Atom 摘要长度和后端一致', () => {
  const subs = codeOf('src/api/routers/subscriptions.py');
  const events = codeOf('src/api/routers/events.py');
  const board = codeOf('src/services/event_board.py');

  // 文章三种格式各自的条数上限:函数体里 `safe_limit = min(max(limit, 1), <上限>)`
  for (const endpoint of FEED_ENDPOINT_GROUPS[0].endpoints) {
    const route = routeOf(endpoint);
    const cap = /safe_limit\s*=\s*min\(max\(limit,\s*1\),\s*(\w+)\)/.exec(functionBody(subs, route.fn, 'subscriptions.py'))?.[1];
    assert.ok(cap, `${route.fn}: 没读到条数上限。${UPDATE}`);
    const backendMax = /^\d+$/.test(cap) ? Number(cap) : pyInt(subs, cap, 'subscriptions.py');
    const listed = endpoint.params.find((param) => param.names.includes('limit'));
    assert.equal(listed.limitMax, backendMax, `${endpoint.path}: 页面写 limit 上限 ${listed.limitMax},后端是 ${backendMax}`);
    assert.ok(listed.desc.includes(String(backendMax)), `${endpoint.path}: 说明文字里没有上限 ${backendMax}`);
  }
  assert.equal(FEED_LIMITS.articles, FEED_ENDPOINT_GROUPS[0].endpoints[0].params.find((param) => param.names.includes('limit')).limitMax);

  // Atom 不下发正文时:每条带一段摘要,取正文的前 N 字(JSON 才是不带正文)
  const cut = /else:\s*ET\.SubElement\(entry, _atom_tag\("summary"\)[\s\S]{0,80}?body\[:(\d+)\]/.exec(functionBody(subs, '_atom_feed_xml', 'subscriptions.py'))?.[1];
  assert.ok(cut, `_atom_feed_xml: 没读到不下发正文时的摘要长度。${UPDATE}`);
  assert.equal(Number(cut), FEED_LIMITS.atomSummaryChars, 'Atom 摘要长度');
  const atom = FEED_ENDPOINTS.find((e) => e.format === 'Atom');
  assert.ok(atom.params.find((param) => param.names[0] === 'include_content').desc.includes(`前 ${cut} 字`), 'Atom 的 include_content 说明里没有摘要长度');

  // 事件榜:默认窗口、最大窗口、件数上限都取自 services/event_board.py 的常量
  const boardConst = (expr, where) => {
    const name = /^event_board_service\.(\w+)$/.exec(expr)?.[1];
    assert.ok(name, `${where}: 默认值不再取自 event_board_service 的常量(现在是 ${expr})。${UPDATE}`);
    return pyInt(board, name, 'event_board.py');
  };
  for (const endpoint of FEED_ENDPOINT_GROUPS[1].endpoints) {
    const route = routeOf(endpoint);
    assert.equal(boardConst(route.params.get('window_hours'), route.fn), FEED_LIMITS.eventWindowDefault, `${endpoint.path}: 默认窗口`);
    if (route.params.has('limit')) assert.equal(boardConst(route.params.get('limit'), route.fn), FEED_LIMITS.eventLimit, `${endpoint.path}: 默认件数`);
  }
  const upperBound = (helper) => {
    const name = /if not 1 <= \w+ <= event_board_service\.(\w+):/.exec(functionBody(events, helper, 'events.py'))?.[1];
    assert.ok(name, `events.py 的 ${helper} 写法变了。${UPDATE}`);
    return pyInt(board, name, 'event_board.py');
  };
  assert.equal(upperBound('_check_window'), FEED_LIMITS.eventWindowMax, '事件榜最大窗口');
  assert.equal(upperBound('_check_limit'), FEED_LIMITS.eventLimit, '事件榜件数上限');
  // 两条事件接口确实都过这两道范围检查
  for (const endpoint of FEED_ENDPOINT_GROUPS[1].endpoints) {
    const route = routeOf(endpoint);
    const body = functionBody(events, route.fn, 'events.py');
    assert.ok(body.includes('_check_window(window_hours)'), `${route.fn}: 不再校验窗口范围,说明里的「范围 1–${FEED_LIMITS.eventWindowMax}」要改`);
    if (route.params.has('limit')) assert.ok(body.includes('_check_limit(limit)'), `${route.fn}: 不再校验件数范围`);
  }
  // 说明里的「近 N 天」是由小时数除出来的:窗口不是整天时这句话就不对了
  assert.equal(FEED_LIMITS.eventWindowDefault % 24, 0);
});

test('每条接口在交付计量里的名字对得上,组也是同一套', () => {
  const usage = codeOf('src/services/token_usage.py');
  const channels = new Map([...usage.matchAll(/^(FEED_\w+) = "([^"]+)"\s*$/gm)].map((m) => [m[1], m[2]]));
  const groups = new Map([...usage.matchAll(/^(GROUP_\w+) = "([a-z]+)"\s*$/gm)].map((m) => [m[1], m[2]]));
  const groupOf = new Map([...usage.matchAll(/^\s+(FEED_\w+): (GROUP_\w+),\s*$/gm)].map((m) => [channels.get(m[1]), groups.get(m[2])]));
  assert.ok(channels.size > 0 && groupOf.size === channels.size, `token_usage.py 的接口登记写法变了。${UPDATE}`);

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
  const eventsPy = codeOf('src/api/routers/events.py');
  for (const endpoint of events.endpoints) {
    const route = routeOf(endpoint);
    const body = functionBody(eventsPy, route.fn, 'events.py');
    const call = /event_board_service\.read_(?:board|event)\(/.exec(body);
    assert.ok(call, `${route.fn}: 没读到取榜的调用。${UPDATE}`);
    const args = parenBody(body, call.index + call[0].length - 1, route.fn);
    assert.ok(!/identity|owner|username/.test(args), `${route.fn}: 取榜开始按令牌主人裁剪了,「全站公开内容」这枚范围签要重新想`);
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
  assert.ok(codeOf('src/api/tokens.py').includes('request.query_params.get("token")'), '后端不再从 ?token= 读令牌,Atom 的示例要改');
});

test('参数说明:管理员令牌的来源不受订阅限制;条数上限与不下发正文的说法逐接口不同', () => {
  const byKey = Object.fromEntries(FEED_ENDPOINTS.map((e) => [e.key, e]));
  const line = (endpoint, name, admin = false) => feedEndpointParams(endpoint, admin).find(([label]) => label === name)?.[1];
  assert.ok(line(byKey.articles, 'source_ids', false).includes('已订阅'));
  assert.ok(!line(byKey.articles, 'source_ids', true).includes('已订阅'));
  assert.ok(line(byKey.articles, 'skip / limit').endsWith(`上限 ${FEED_LIMITS.articles}`));
  assert.ok(line(byKey['articles.md'], 'skip / limit').endsWith(`上限 ${FEED_LIMITS.articlesExport}`));
  assert.ok(line(byKey['articles.xml'], 'skip / limit').endsWith(`上限 ${FEED_LIMITS.articlesExport}`));
  // JSON 传 false 不带正文;Atom 传 false 仍带一段摘要;Markdown 没有这个参数
  assert.ok(line(byKey.articles, 'include_content').includes('仅取元数据'));
  assert.ok(line(byKey['articles.xml'], 'include_content').includes('摘要') && !line(byKey['articles.xml'], 'include_content').includes('元数据'));
  assert.equal(line(byKey['articles.md'], 'include_content'), undefined);
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
