// 说明位守卫(issue #291 · conventions §1 首条「界面不靠说明文字解释自己」)。
// 说明句不是凭空长出来的:模板里留了一个放小字的位置(行名下的灰字、页头副标题、区头右边的提示、卡片脚注),
// 写的人就会去填。2026-11-16 复查时全站有 72 处这样的句子,设置面板占了近一半——每一行都是「行名 + 一行灰字」。
// 这里把「形似说明位」的类名(以 -hint / -sub / -desc / -note / -lede / -intro / -tip / -caption / -eyebrow /
// -kicker / -blurb / -slogan / -tagline / -explain / -help 结尾)在 JSX 里的使用次数冻结成在册表 SLOTS:
//   · 多了(或出现不在册的新类名)→ 失败。先按 §1 首条问一遍「删掉这句,读者会不会做错或看不懂」;
//     确实是数据、条件状态或豁免的登录提示,再把在册数调高并在这一行写明装的是什么。
//   · 少了 → 也失败,请把在册数一起调低(棘轮,同 hoverGuard)。
// 在册的类名并不都是说明句——很多装的是数据(时间窗、id、计数)或状态。表的作用是让「新增一处小字」这件事
// 必须改这个文件、进检视,而不是静悄悄地多一句。
// 只数 className 属性里的字面量(含三元 / 模板串里的);拼好再传进来的类名数不到,这是已知的口子。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../src');

export const SLOT_NAME = /-(hint|sub|desc|note|lede|intro|tip|caption|eyebrow|kicker|blurb|slogan|tagline|explain|help)$/;

// 已退役的说明位(issue #291 第一批):JSX 与样式表里都不许再出现。
//   sett-head-hint 设置分区页头副标题 · sett-nav-foot 设置导航栏底的品牌行 · sett-file-hint OPML 选择文件下的格式说明
//   sett-pal-tone 中性色卡的第二个名字 · m-sett-hint 手机设置行尾的说明 · pwa-intro 安装指引的引言 · card-desc 接入集成各卡标题下的介绍
export const RETIRED = ['sett-head-hint', 'sett-nav-foot', 'sett-file-hint', 'sett-pal-tone', 'm-sett-hint', 'pwa-intro', 'card-desc'];

// 在册表:类名 → 允许的使用次数。行尾注释写的是这个位置现在装什么;没写的是存量,尚未逐个清点。
export const SLOTS = {
  'acct-sub': 3,
  'auth-card-eyebrow': 1, // 登录卡眉题,待第二批清
  'auth-card-sub': 1, // 登录卡副标题,待第二批清
  'auth-eyebrow': 1,
  'auth-hint': 1,
  'auth-lede': 1, // 登录页口号(品牌页,留)
  'bp-eyebrow': 1,
  'bp-kicker': 3,
  'bp-skel-kicker': 1,
  'bp-top-note': 1,
  'brief-mast-kicker': 4,
  'brief-mast-sub': 2, // 兴趣页报头句,待第二批收进首登引导
  'cred-sub': 1, // 凭据卡:只剩 DeepSeek 模型改名的条件提示
  'csrc-kind-note': 1,
  'csrc-sub': 1, // 添加自定源弹窗副标题,待第二批清
  'drawer-title-sub': 4, // 抽屉标题旁:三处是 id(数据),游客设置那一句待第二批清
  'flow-stat-sub': 6,
  'guest-defsrc-note': 1, // 待第二批清
  'guest-gate-sub': 1, // 游客看到的「登录后可用」下一句(登录提示,豁免)
  'has-tip': 1, // 状态修饰类(这一格有悬停提示),不是说明位
  'heat-empty-hint': 1,
  'heat-note': 1,
  'heat-tip': 1,
  'inspector-desc': 3,
  'inspector-hint': 2, // 待第二批清
  'inspector-param-hint': 1,
  'inspector-persist-note': 1, // 待第二批清
  'inspector-visibility-hint': 4, // 待第二批清
  'interest-card-desc': 2,
  'interest-sec-hint': 2, // 兴趣页分面副标签,待第二批清
  'is-note': 2,
  'jr-foot-note': 1, // 待第二批清
  'jr-kicker': 1,
  'kpi-sub': 1,
  'listen-note': 1,
  'm-flow-sub': 1,
  'm-hint': 1,
  'm-int-lede': 1, // 手机壳兴趣页报头句,待第二批清
  'm-note': 2,
  'm-profile-sub': 2, // 手机设置资料卡:已订阅数 / 角色;游客是一句登录提示(豁免)
  'm-subs-hint': 1,
  'opml-foot-note': 1, // OPML 预览脚:尚未写入(状态)
  'opml-note': 2, // OPML 预览里的配额与重复提示(条件状态)
  'ops-tip': 1,
  'paper-content-note': 1,
  'pipeline-note': 4,
  'pl-note': 3,
  'pl-sub': 1,
  'player-ident-sub': 1,
  'player-sub': 2,
  'podcast-view-note': 1,
  'pwa-note': 2, // 安装指引里的条件提示
  'ranking-head-note': 1, // 榜单页头:桌面不出,手机壳待第二批清
  'reader-ai-layer-note': 2,
  'reader-ai-score-tip': 1,
  'reader-disc-bulk-sub': 1,
  'reader-disc-coll-desc': 1,
  'reader-disc-coll-note': 2,
  'reader-disc-coll-sub': 1,
  'reader-disc-desc': 2,
  'reader-disc-hint': 1, // 发现页页名旁:桌面不出,手机壳待第二批清
  'reader-disc-sub': 2,
  'reader-kicker': 2,
  'reader-list-sub': 1,
  'reader-pane-sub': 2,
  'reader-side-hint': 1,
  'reader-vrail-tip': 9,
  'rk-kicker': 1,
  'run-obj-sub': 1,
  'scope-note': 3, // 折叠的参考内容(接口参数、启停与取数范围、日报范围)
  'sett-sub': 3, // 设置行名下的小字:账户角色胶囊、游客卡的一句登录提示(豁免)、OPML 导出的数字。不放说明句
  'share-group-note': 2,
  'shared-empty-hint': 2,
  'shared-kicker': 1,
  'social-tr-note': 2,
  'tt-foot': 1, // 采集任务时刻表脚注(类名不以说明位后缀结尾,单独在册),待第二批清
  'tool-desc': 1, // MCP 工具表的说明(参考资料)
  'zone-hint': 6, // 管理面区头小字:时间窗是数据,口径解释待第二批清
};

// className 属性里出现的类名(字面量 / 模板串 / 表达式里的字符串)。同一个属性里同名只计一次——
// `on ? 'sett-sub active' : 'sett-sub'` 是一处使用,不是两处;`className = "x"` 这种等号两边带空格的写法也认(检视首轮 P2)。
export function classTokens(code) {
  const out = [];
  const re = /className\s*=\s*/g;
  let m;
  while ((m = re.exec(code))) {
    const seen = new Set();
    // 模板串里 ${cond ? 'a-hint' : ''} 这样的插值:把插值里的字符串字面量也拆出来
    const push = (text) => {
      const inner = [];
      const plain = text.replace(/\$\{([^}]*)\}/g, (_, expr) => {
        for (const q of expr.matchAll(/(['"])((?:(?!\1).)*)\1/g)) inner.push(q[2]);
        return ' ';
      });
      for (const tok of [plain, ...inner].join(' ').split(/\s+/)) if (tok) seen.add(tok);
    };
    let i = m.index + m[0].length;
    const open = code[i];
    if (open === '"' || open === "'") {
      const end = code.indexOf(open, i + 1);
      if (end !== -1) push(code.slice(i + 1, end));
    } else if (open === '{') {
      let depth = 0;
      for (; i < code.length; i += 1) {
        const ch = code[i];
        if (ch === '"' || ch === "'" || ch === '`') {
          let j = i + 1;
          while (j < code.length && code[j] !== ch) j += code[j] === '\\' ? 2 : 1;
          push(code.slice(i + 1, j));
          i = j;
        } else if (ch === '{') depth += 1;
        else if (ch === '}') { depth -= 1; if (depth === 0) break; }
      }
    }
    out.push(...seen);
  }
  return out;
}

// 样式表里有没有这个类的规则:只看类名末尾的边界——`card-desc` 要命中 `p.card-desc`、`.surface-card.card-desc`,
// 不命中 `.interest-card-desc`、`.card-desc-x`(检视首轮 P2:原先点号前还有一个后顾,把复合选择器漏掉了)
export function cssHasClass(css, name) {
  return new RegExp(`\\.${name}(?![\\w-])`).test(css);
}

function jsxFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== 'design-preview') jsxFiles(p, out); }
    else if (name.endsWith('.jsx')) out.push(p);
  }
  return out;
}

export function slotCounts(root = src) {
  const counts = new Map();
  for (const file of jsxFiles(root)) {
    for (const tok of classTokens(readFileSync(file, 'utf8'))) {
      if (SLOT_NAME.test(tok) || tok in SLOTS) counts.set(tok, (counts.get(tok) || 0) + 1);
    }
  }
  return counts;
}

test('classTokens 认得字面量、模板串与三元里的类名', () => {
  const code = [
    '<p className="sett-sub">a</p>',
    '<i className={`row ${on ? "is-on" : ""} zone-hint`} />',
    "<b className={open ? 'reader-disc-hint' : ''} />",
    '{/* className="sett-sub" 写在注释里也算——守卫宁可多报 */}',
    '<u className = "new-hint" />',
    "<s className={on ? 'sett-sub active' : 'sett-sub'} />",
  ].join('\n');
  const tokens = classTokens(code);
  // 三处使用:字面量、注释里那一处、三元那一处(两个分支同名只计一次)
  assert.equal(tokens.filter((t) => t === 'sett-sub').length, 3);
  assert.ok(tokens.includes('zone-hint') && tokens.includes('reader-disc-hint') && tokens.includes('is-on'));
  assert.ok(tokens.includes('new-hint'), '等号两边带空格的写法也要数到');
});

test('cssHasClass 认得复合选择器,不误报更长的类名', () => {
  assert.ok(cssHasClass('.card-desc { color: red }', 'card-desc'));
  assert.ok(cssHasClass('p.card-desc { color: red }', 'card-desc'));
  assert.ok(cssHasClass('.surface-card.card-desc:hover { color: red }', 'card-desc'));
  assert.ok(!cssHasClass('.interest-card-desc { color: red }', 'card-desc'));
  assert.ok(!cssHasClass('.card-desc-x { color: red }', 'card-desc'));
});

test('说明位类名的使用次数只许减不许增,新类名要在册', () => {
  const counts = slotCounts();
  const unknown = [];
  const more = [];
  const fewer = [];
  for (const [name, n] of counts) {
    if (!(name in SLOTS)) unknown.push(`${name} ×${n}`);
    else if (n > SLOTS[name]) more.push(`${name}: 在册 ${SLOTS[name]},现在 ${n}`);
    else if (n < SLOTS[name]) fewer.push(`${name}: 在册 ${SLOTS[name]},现在 ${n}`);
  }
  for (const name of Object.keys(SLOTS)) {
    if (!counts.has(name)) fewer.push(`${name}: 在册 ${SLOTS[name]},现在 0(整行删掉)`);
  }
  const ask = '先按 conventions §1 首条判断这句该不该写;确属数据 / 条件状态 / 豁免的登录提示,再改 test/explanatorySlots.test.mjs 的 SLOTS 并注明装的是什么';
  assert.deepEqual(unknown, [], `出现了不在册的说明位类名。${ask}`);
  assert.deepEqual(more, [], `说明位的使用次数增加了。${ask}`);
  assert.deepEqual(fewer, [], '说明位减少了(好事):请把 SLOTS 里的在册数一起调低');
});

test('已退役的说明位不再出现在 JSX 与样式表里', () => {
  const jsx = jsxFiles(src).flatMap((file) => classTokens(readFileSync(file, 'utf8')));
  const css = ['index.css', 'mobile.css'].map((name) => readFileSync(path.join(src, name), 'utf8')).join('\n');
  for (const name of RETIRED) {
    assert.ok(!jsx.includes(name), `${name} 已退役,JSX 里不应再用`);
    assert.ok(!cssHasClass(css, name), `${name} 已退役,样式表里不应再有规则`);
  }
});

test('设置:分区页头与手机设置行不带副标题表', () => {
  const desktop = readFileSync(path.join(src, 'components/SettingsModal.jsx'), 'utf8');
  const mobile = readFileSync(path.join(src, 'components/mobile/MobileSettings.jsx'), 'utf8');
  assert.ok(!/\bHINTS\b/.test(desktop), '桌面设置柜不再有分区副标题表');
  assert.ok(!/\bHINTS\b/.test(mobile), '手机设置不再有行尾说明表');
});
