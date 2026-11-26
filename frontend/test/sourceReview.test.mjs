// 源审查三项的显示口径:检视器只显示中文档位。目录里的稳定性取值带抓法后缀,曾原样露出、把右栏撑出横向滚动。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import { NOISE_LABELS, RELIABILITY_LABELS, SIGNAL_LABELS, reviewOf } from '../src/utils/sourceReview.js';

const labelsOf = (source) => Object.fromEntries(reviewOf(source).map((item) => [item.name, item.label]));
const only = (source) => reviewOf(source)[0];

test('三项各自显示中文档位,没有值的项不列', () => {
  assert.deepEqual(
    labelsOf({ signal_strength: 'high_signal', noise_risk: 'medium_noise', fetch_reliability: 'stable_public' }),
    { 信号: '高信号', 噪声: '中噪声', 稳定性: '稳定公开' },
  );
  assert.deepEqual(reviewOf({ signal_strength: '', noise_risk: null }), []);
  assert.deepEqual(reviewOf(null), []);
  assert.deepEqual(reviewOf({ noise_risk: 'low_noise' }).map((item) => item.key), ['noise_risk']);
});

test('稳定性带抓法后缀时按档位显示,原始取值留给悬停提示', () => {
  for (const value of ['stable_public_rss', 'stable_public_website', 'stable_public_website_obfuscated_css']) {
    assert.deepEqual(only({ fetch_reliability: value }), { key: 'fetch_reliability', name: '稳定性', label: '稳定公开', raw: value });
  }
  // 正好是档位本身:没有多出来的信息,不给悬停提示
  assert.equal(only({ fetch_reliability: 'stable_public' }).raw, '');
  assert.equal(only({ fetch_reliability: 'official_paid_api' }).label, '官方付费 API');
  // 只在下划线处认档位:stable_publicity 不是 stable_public 的后缀写法
  assert.equal(only({ fetch_reliability: 'stable_publicity' }).label, 'stable_publicity');
});

test('认不出的取值原样显示,并带悬停提示(那一格会截断)', () => {
  assert.deepEqual(only({ fetch_reliability: 'something_new' }), { key: 'fetch_reliability', name: '稳定性', label: 'something_new', raw: 'something_new' });
});

test('旧 Podcast 目录的短枚举照旧接住', () => {
  assert.deepEqual(
    labelsOf({ signal_strength: 'high', noise_risk: 'low', fetch_reliability: 'blocked' }),
    { 信号: '高信号', 噪声: '低噪声', 稳定性: '易阻断' },
  );
  assert.equal(only({ fetch_reliability: 'high' }).raw, '');
});

// 仓内写下的取值(源目录 + 抓取器类属性 + 播客目录)都要落到中文档位上:新增一种写法而没补标签,这里先红。
const repo = (path) => new URL(`../../${path}`, import.meta.url);
const read = (path) => readFileSync(repo(path), 'utf8');
const filesIn = (dir, suffix) => readdirSync(repo(dir)).filter((name) => name.endsWith(suffix)).map((name) => `${dir}/${name}`);
const quoted = (text, pattern) => [...text.matchAll(pattern)].map((m) => m[1]);

test('仓内出现的审查取值都有中文档位', () => {
  const catalog = filesIn('src/sources/catalog', '.toml').map(read).join('\n');
  const fetchers = filesIn('src/fetchers/impl', '.py').map(read).join('\n');
  const podcast = read('src/services/podcast_catalog.py');
  const found = {
    signal_strength: [...quoted(catalog, /^signal = "([^"]+)"/gm), ...quoted(fetchers, /^\s*signal_strength = "([^"]+)"/gm)],
    noise_risk: [...quoted(catalog, /^noise = "([^"]+)"/gm), ...quoted(fetchers, /^\s*noise_risk = "([^"]+)"/gm)],
    fetch_reliability: [
      ...quoted(catalog, /^reliability = "([^"]+)"/gm),
      ...quoted(fetchers, /^\s*fetch_reliability = "([^"]+)"/gm),
      ...quoted(podcast, /"(stable_public\w*|blocked_or_fragile)"/g),
    ],
  };
  const known = new Set([SIGNAL_LABELS, NOISE_LABELS, RELIABILITY_LABELS].flatMap((labels) => Object.values(labels)));
  for (const [key, values] of Object.entries(found)) {
    assert.ok(values.length > 0, `${key}: 没有读到取值,正则与文件格式对不上`);
    for (const value of new Set(values)) {
      const { label } = only({ [key]: value });
      assert.ok(known.has(label), `${key} = ${value} 没有中文档位(显示成了 ${label})`);
    }
  }
  // 读到的确实包含带后缀的写法,不是只扫到了三个基本档位
  assert.ok(found.fetch_reliability.includes('stable_public_website_obfuscated_css'));
  assert.ok(found.fetch_reliability.includes('official_paid_api'));
});
