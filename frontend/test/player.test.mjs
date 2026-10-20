// 全局播客播放条(issue #199)的纯逻辑:状态机、会话恢复、续播、弹簧采样、封面遮罩对比度、轻量动效判定、收听入口。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  articleNarrationItem,
  clearPlayerSession,
  decideFxTier,
  fabOffset,
  formatClock,
  framePercentile,
  framesSayLite,
  initialPlayerState,
  isPlayingIntent,
  nextRate,
  nowPlayingFor,
  playerReducer,
  playerTrackSpeech,
  podcastTrackItem,
  readPlayerSession,
  rendererIsSoftware,
  resolveStartPosition,
  sanitizeItem,
  startPositionFor,
  writePlayerSession,
  PLAYER_SESSION_KEY,
} from '../src/utils/playerCore.js';
import { SPRING_FALLBACK, springEasing, springSamples } from '../src/utils/playerSpring.js';
import { boxBlurRGBA, compositeOver, contrastRatio, veilAlphaFor } from '../src/utils/coverTone.js';
import {
  articleListenActive,
  articleListenEntries,
  listenEntrySpeech,
  podcastListenEntries,
} from '../src/utils/listenEntries.js';
import { podcastPlaybackKey } from '../src/utils/podcastPlayback.js';

const episode = (podcast = {}, extra = {}) => ({
  id: 'ep-1',
  title: 'Meta shakes up the frontiers',
  podcast: {
    show_title: 'Interconnects',
    audio_url: 'https://cdn.example.com/ep.mp3',
    duration_seconds: 704,
    image_url: 'https://img.example.com/c.jpg',
    condensed_audio_url: '',
    condensed_duration_seconds: null,
    premium_guide: { status: '', blog_ready: false, mode: '' },
    ...podcast,
  },
  ...extra,
});

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    has: (key) => values.has(key),
  };
}

test('音轨条目:原节目 / 精品导读 / 文章导读;没有可播地址不出条目', () => {
  const original = podcastTrackItem(episode(), 'original');
  assert.equal(original.key, 'ep-1:original');
  assert.equal(original.ai, false);
  assert.equal(original.show, 'Interconnects');
  assert.equal(original.lang, 'en');
  assert.equal(original.duration, 704);
  assert.equal(podcastTrackItem(episode(), 'digest'), null);
  const digest = podcastTrackItem(episode({ condensed_audio_url: 'https://cdn.example.com/d.mp3', condensed_duration_seconds: 236 }), 'digest');
  assert.equal(digest.track, 'digest');
  assert.equal(digest.ai, true);
  assert.equal(digest.duration, 236);
  assert.equal(playerTrackSpeech(digest), '精品导读，AI 生成');
  assert.equal(podcastTrackItem(episode({ audio_url: 'javascript:alert(1)' }), 'original'), null);

  const article = { id: 'a-1', title: '阶跃发布 StepAudio 3', listen_guide: { audio_ready: true, audio_url: 'https://cdn.example.com/n.mp3', duration_seconds: 188 } };
  const narration = articleNarrationItem(article, 'IT之家');
  assert.equal(narration.track, 'article_narration');
  assert.equal(narration.cover, '');
  assert.equal(narration.show, 'IT之家');
  assert.equal(playerTrackSpeech(narration), '文章导读，AI 生成');
  assert.equal(articleNarrationItem({ ...article, listen_guide: { audio_ready: false } }), null);
});

test('状态机:首次播放展开成条;迷你态换台保持迷你;失败不被随后的 pause 冲掉;关闭保留倍速', () => {
  const item = podcastTrackItem(episode(), 'original');
  let s = playerReducer(initialPlayerState, { type: 'load', item });
  assert.equal(s.form, 'bar');
  assert.equal(s.status, 'loading');
  assert.equal(isPlayingIntent(s), true);
  s = playerReducer(s, { type: 'playing' });
  assert.equal(s.status, 'playing');
  s = playerReducer(s, { type: 'minimize' });
  assert.equal(s.form, 'mini');
  s = playerReducer(s, { type: 'load', item: podcastTrackItem(episode({}, { id: 'ep-2' }), 'original') });
  assert.equal(s.form, 'mini');
  assert.equal(s.item.articleId, 'ep-2');
  s = playerReducer(s, { type: 'expand' });
  assert.equal(s.form, 'bar');
  s = playerReducer(s, { type: 'rate', rate: 1.5 });
  s = playerReducer(s, { type: 'error' });
  assert.equal(s.status, 'error');
  s = playerReducer(s, { type: 'paused' });
  assert.equal(s.status, 'error');
  s = playerReducer(s, { type: 'intent-play' });
  assert.equal(s.status, 'loading');
  s = playerReducer(s, { type: 'close' });
  assert.equal(s.form, 'none');
  assert.equal(s.item, null);
  assert.equal(s.rate, 1.5);
  assert.equal(playerReducer(s, { type: 'minimize' }), s);
  assert.equal(playerReducer(s, { type: 'playing' }), s);
});

test('条目行播放态:只给正在播的那一期;失败与关闭后不再标', () => {
  const item = podcastTrackItem(episode(), 'original');
  const playing = { ...initialPlayerState, item, form: 'bar', status: 'playing' };
  assert.equal(nowPlayingFor(playing, 'ep-1'), 'playing');
  assert.equal(nowPlayingFor({ ...playing, status: 'paused' }, 'ep-1'), 'paused');
  assert.equal(nowPlayingFor({ ...playing, form: 'mini', status: 'loading' }, 'ep-1'), 'playing');
  assert.equal(nowPlayingFor(playing, 'ep-2'), '');
  assert.equal(nowPlayingFor({ ...playing, status: 'error' }, 'ep-1'), '');
  assert.equal(nowPlayingFor({ ...playing, form: 'none' }, 'ep-1'), '');
});

test('会话恢复:同一账号恢复为暂停态;换号与坏数据不恢复;关闭即清', () => {
  const storage = memoryStorage();
  const item = podcastTrackItem(episode(), 'original');
  assert.equal(writePlayerSession(storage, 'alice', { item, form: 'mini', rate: 1.25 }), true);
  const back = readPlayerSession(storage, 'alice');
  assert.deepEqual(back, { item: sanitizeItem(item), form: 'mini', rate: 1.25 });
  assert.equal(readPlayerSession(storage, 'bob'), null);
  assert.equal(storage.has(PLAYER_SESSION_KEY), false, '换号读取即清掉旧会话');

  writePlayerSession(storage, 'alice', { item, form: 'bar', rate: 3 });
  assert.equal(readPlayerSession(storage, 'alice').rate, 1, '非法倍速回 1×');
  writePlayerSession(storage, 'alice', { item, form: 'none' });
  assert.equal(readPlayerSession(storage, 'alice'), null);

  storage.setItem(PLAYER_SESSION_KEY, '{not json');
  assert.equal(readPlayerSession(storage, 'alice'), null);
  storage.setItem(PLAYER_SESSION_KEY, JSON.stringify({ user: 'alice', item: { ...item, src: 'blob:x' }, form: 'bar' }));
  assert.equal(readPlayerSession(storage, 'alice'), null);
  clearPlayerSession(storage);
  assert.equal(storage.has(PLAYER_SESSION_KEY), false);
  assert.equal(readPlayerSession(null, 'alice'), null);
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
  assert.equal(readPlayerSession(broken, 'alice'), null);
  assert.equal(writePlayerSession(broken, 'alice', { item, form: 'bar' }), false);
  clearPlayerSession(broken);
});

test('续播位置:离结尾 3 秒内从头开始;越界夹住;时钟格式', () => {
  assert.equal(startPositionFor(120, 704), 120);
  assert.equal(startPositionFor(702, 704), 0);
  assert.equal(startPositionFor(900, 0), 900);
  assert.equal(startPositionFor('bad', 704), 0);
  assert.equal(startPositionFor(-5, 704), 0);
  assert.equal(formatClock(0), '00:00');
  assert.equal(formatClock(704), '11:44');
  assert.equal(formatClock(5230), '1:27:10');
  assert.equal(nextRate(1), 1.25);
  assert.equal(nextRate(2), 1);
  assert.equal(nextRate(9), 1.25);
});

test('起播位置:元数据到达前的显式 seek 只按时长夹取(含 0),自动续播仍套末 3 秒规则', () => {
  // 显式拖到末 3 秒内:照读者的意思,不当成「已听完」
  assert.equal(resolveStartPosition({ saved: 60, explicit: 119, duration: 120 }), 119);
  assert.equal(resolveStartPosition({ saved: 119, explicit: 119, duration: 119.9 }), 119);
  // 0 是有效的显式位置,不能回落到续播的 60
  assert.equal(resolveStartPosition({ saved: 60, explicit: 0, duration: 120 }), 0);
  // 只夹时长:超出真实时长夹到结尾,负数夹到 0;时长未知时原样
  assert.equal(resolveStartPosition({ saved: 0, explicit: 130, duration: 120 }), 120);
  assert.equal(resolveStartPosition({ saved: 0, explicit: -4, duration: 120 }), 0);
  assert.equal(resolveStartPosition({ saved: 0, explicit: 119, duration: Number.NaN }), 119);
  // 没有显式位置(null / undefined / 非数):走自动续播,末 3 秒内归零
  assert.equal(resolveStartPosition({ saved: 118.5, explicit: null, duration: 120 }), 0);
  assert.equal(resolveStartPosition({ saved: 60, duration: 120 }), 60);
  assert.equal(resolveStartPosition({ saved: 60, explicit: 'x', duration: 120 }), 60);
  assert.equal(resolveStartPosition(), 0);
});

test('播放位置的键:新增文章导读音轨,原节目 / 精品导读沿用旧键名', () => {
  assert.match(podcastPlaybackKey('ep-1', 'original'), /:ep-1:original$/);
  assert.match(podcastPlaybackKey('ep-1', 'digest'), /:ep-1:digest$/);
  assert.match(podcastPlaybackKey('a-1', 'article_narration'), /:a-1:narration$/);
});

test('弹簧采样:起点 0、末点钉 1;ζ0.84 几乎不过冲;临界阻尼不过冲;不支持 linear() 退回贝塞尔', () => {
  const pts = springSamples({ zeta: 0.84, response: 0.46, dur: 560 });
  assert.equal(pts[0], 0);
  assert.equal(pts.at(-1), 1);
  assert.ok(Math.max(...pts) < 1.02, `过冲 ${Math.max(...pts)}`);
  const critical = springSamples({ zeta: 1, response: 0.36, dur: 420 });
  assert.ok(Math.max(...critical) <= 1);
  const kicked = springSamples({ zeta: 0.92, response: 0.3, dur: 360, kick: 0.6 });
  assert.ok(kicked[1] > springSamples({ zeta: 0.92, response: 0.3, dur: 360 })[1], '带初速度的第二段一开始就在走');
  assert.match(springEasing({ zeta: 0.84, response: 0.46, dur: 560 }, true), /^linear\(0, [\d., ]+, 1\)$/);
  assert.equal(springEasing({ zeta: 0.84, response: 0.46, dur: 560 }, false), SPRING_FALLBACK);
});

test('封面遮罩:最差像素也过 4.5:1;明亮封面自动加浓;纯色位图模糊后不变', () => {
  assert.equal(Math.round(contrastRatio([0, 0, 0], [255, 255, 255])), 21);
  assert.deepEqual(compositeOver([255, 255, 255], 0.5, [0, 0, 0]), [127.5, 127.5, 127.5]);
  const raised = [255, 255, 255];
  const muted = [100, 110, 125];
  const accentInk = [70, 70, 170];
  // 深色封面:基础浓度不够时要加浓,结果仍在上限内
  const dark = new Uint8ClampedArray(4 * 16).fill(0).map((v, i) => (i % 4 === 3 ? 255 : 10));
  const a = veilAlphaFor(dark, raised, [muted, accentInk], { lo: 0.7 });
  assert.ok(a >= 0.7 && a <= 0.96);
  for (let i = 0; i < dark.length; i += 4) {
    const bg = compositeOver(raised, a, [dark[i], dark[i + 1], dark[i + 2]]);
    assert.ok(contrastRatio(muted, bg) >= 4.6 - 1e-9);
  }
  // 浅色封面在基础浓度下就过线
  const light = new Uint8ClampedArray(4 * 16).fill(240);
  assert.equal(veilAlphaFor(light, raised, [muted], { lo: 0.7 }), 0.7);
  assert.equal(veilAlphaFor(null, raised, [muted], { lo: 0.64 }), 0.64);
  const flat = new Uint8ClampedArray(8 * 8 * 4).fill(128);
  boxBlurRGBA(flat, 8, 2, 3);
  assert.ok([...flat].every((v, i) => (i % 4 === 3 ? v === 255 : v === 128)));
  const spike = new Uint8ClampedArray(8 * 8 * 4).fill(0);
  spike[(3 * 8 + 3) * 4] = 255;
  boxBlurRGBA(spike, 8, 1, 1);
  assert.ok(spike[(3 * 8 + 3) * 4] < 255 && spike[(3 * 8 + 4) * 4] > 0, '模糊把尖峰摊开');
});

test('轻量动效档:软件光栅器 / 首次形变 p95 超 24ms;显式会话覆盖优先', () => {
  assert.equal(rendererIsSoftware('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)'), true);
  assert.equal(rendererIsSoftware('Microsoft Basic Render Driver'), true);
  assert.equal(rendererIsSoftware('llvmpipe (LLVM 15.0.7, 256 bits)'), true);
  assert.equal(rendererIsSoftware('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)'), false);
  assert.equal(framePercentile([16, 16, 17, 40], 0.95), 40);
  assert.equal(framePercentile([]), 0);
  assert.equal(framesSayLite([16, 17, 16, 18]), false, '样本太少不下结论');
  assert.equal(framesSayLite(Array(30).fill(16.7)), false);
  assert.equal(framesSayLite([...Array(20).fill(16.7), ...Array(10).fill(33)]), true);
  assert.equal(decideFxTier({ renderer: 'SwiftShader' }), 'lite');
  assert.equal(decideFxTier({ override: 'full', renderer: 'SwiftShader' }), 'full');
  assert.equal(decideFxTier({ lite: true, renderer: 'Apple M2' }), 'lite');
  assert.equal(decideFxTier({ renderer: 'Apple M2' }), 'full');
});

test('问哆啦美浮钮避让:条出现让到条顶之上 12px;迷你时让到左边、中线对齐;否则归位', () => {
  const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
  const fab = rect(1378, 838, 40, 40);
  assert.deepEqual(fabOffset('bar', fab, rect(76, 816, 1352, 72), null), { x: 0, y: -74 });
  assert.deepEqual(fabOffset('mini', fab, null, rect(1330, 830, 88, 48)), { x: -98, y: -4 });
  assert.deepEqual(fabOffset('none', fab, null, null), { x: 0, y: 0 });
  assert.deepEqual(fabOffset('bar', fab, rect(76, 816, 500, 72), null), { x: 0, y: 0 }, '横向不重叠不让');
});

test('收听入口 s1–s6 + 导读稿可读而音频生成中', () => {
  const opts = { aiEnabled: true, ondemandEnabled: true };
  const ids = (list) => list.map((e) => `${e.kind}:${e.label}`);
  // s1 原节目 · 可点播
  assert.deepEqual(ids(podcastListenEntries(episode(), opts)), ['play:收听原节目', 'request:点播精品导读']);
  assert.equal(podcastListenEntries(episode(), opts)[0].duration, '12 分钟');
  assert.equal(podcastListenEntries(episode(), opts)[0].primary, true);
  // s2 导读生成中
  assert.deepEqual(ids(podcastListenEntries(episode({ premium_guide: { status: 'queued' } }), opts)), ['play:收听原节目', 'busy:导读生成中…']);
  // s3 / s4 导读就绪(两条音轨都可播;正文与音轨解耦,入口不区分在听哪条)
  const ready = episode({ condensed_audio_url: 'https://cdn.example.com/d.mp3', condensed_duration_seconds: 236, premium_guide: { status: 'ready', blog_ready: true } });
  assert.deepEqual(ids(podcastListenEntries(ready, opts)), ['play:收听原节目', 'play:精品导读']);
  assert.equal(podcastListenEntries(ready, { aiEnabled: false })[1].label, '精品导读', '就绪的导读音频不受点播开关影响');
  // s5 导读稿可读 · 音频未生成
  const blog = episode({ premium_guide: { status: '', blog_ready: true } });
  assert.deepEqual(ids(podcastListenEntries(blog, opts)), ['play:收听原节目', 'request:生成导读音频']);
  // 新增:导读稿可读 + 音频生成中
  assert.deepEqual(ids(podcastListenEntries(episode({ premium_guide: { status: 'synthesizing', blog_ready: true } }), opts)), ['play:收听原节目', 'busy:导读音频生成中…']);
  // 失败对读者不展示:回到可点播
  assert.deepEqual(ids(podcastListenEntries(episode({ premium_guide: { status: 'failed', blog_ready: true } }), opts)), ['play:收听原节目', 'request:生成导读音频']);
  // s6 点播未开放 / AI 关:只剩原节目;已在生成的仍看得见
  assert.deepEqual(ids(podcastListenEntries(episode(), { aiEnabled: true, ondemandEnabled: false })), ['play:收听原节目']);
  assert.deepEqual(ids(podcastListenEntries(episode(), { aiEnabled: false, ondemandEnabled: true })), ['play:收听原节目']);
  assert.deepEqual(ids(podcastListenEntries(episode({ premium_guide: { status: 'summarizing' } }), { aiEnabled: true, ondemandEnabled: false })), ['play:收听原节目', 'busy:导读生成中…']);
  // 只出中文导读文字的模式没有音频可点
  assert.deepEqual(ids(podcastListenEntries(episode({ premium_guide: { mode: 'brief_zh', blog_ready: true } }), opts)), ['play:收听原节目']);
  // 提交在途
  assert.deepEqual(ids(podcastListenEntries(episode(), { ...opts, submitting: true })), ['play:收听原节目', 'busy:提交中…']);
  assert.equal(listenEntrySpeech(podcastListenEntries(ready, opts)[0]), '收听原节目，12 分钟');
  assert.equal(listenEntrySpeech(podcastListenEntries(ready, opts)[1]), '收听精品导读，4 分钟，AI 生成');
});

test('文章「✦ 听导读」:就绪可播 / 可点播 / 生成中;失败回到可点播', () => {
  const opts = { aiEnabled: true, ondemandEnabled: true };
  const base = { id: 'a-1', title: '文章' };
  assert.deepEqual(articleListenEntries(base, opts).map((e) => e.kind), ['request']);
  assert.deepEqual(articleListenEntries(base, { aiEnabled: true, ondemandEnabled: false }), []);
  const active = { ...base, listen_guide: { status: 'narrating' } };
  assert.deepEqual(articleListenEntries(active, { aiEnabled: false }).map((e) => e.label), ['导读生成中…']);
  assert.equal(articleListenActive(active), true);
  const failed = { ...base, listen_guide: { status: 'failed', error: '内部原因' } };
  assert.deepEqual(articleListenEntries(failed, opts).map((e) => e.label), ['听导读']);
  const ready = { ...base, listen_guide: { audio_ready: true, audio_url: 'https://cdn.example.com/n.mp3', duration_seconds: 188 } };
  const [entry] = articleListenEntries(ready, opts);
  assert.equal(entry.track, 'article_narration');
  assert.equal(entry.duration, '3 分钟');
  assert.equal(listenEntrySpeech(entry), '听导读，3 分钟，AI 生成');
  assert.equal(articleListenActive(ready), false);
  assert.deepEqual(articleListenEntries(episode(), opts), [], '播客不出文章导读入口');
});
