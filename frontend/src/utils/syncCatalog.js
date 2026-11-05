// 节点同步控制台(issue #261)的单一目录 + 纯推导:流顺序 / 产品名 / 分组 / 「装了什么」/ 接收端语义 / 「不同步」清单,
// 以及从 job 的 progress / result 推导各站状态、百分比、失败流与页、逐流用时、定时下次时间。
// 无 React 依赖(frontend/test/syncCatalog.test.mjs);流顺序与能力常量与后端协议同源(测试读后端源码比对)。

// ── 协议常量(与 src/services/remote_sync.py / archive_sync_v2.py 同步) ──
export const STREAM_ORDER = ['sources', 'taxonomy', 'articles', 'analyses', 'events', 'media', 'podcast_texts', 'podcast_audio', 'source_states'];
export const LEGACY_STREAM_ORDER = ['sources', 'taxonomy', 'articles', 'analyses', 'media', 'source_states'];
export const SCHEMA_VERSION = 'archive-sync-v3';
export const REQUIRED_CAPABILITIES = ['transaction-revision-tombstone-v1', 'authoritative-presence-v1'];
export const OPTIONAL_CAPABILITIES = {
  events: 'events_v1',
  podcast_texts: 'podcast-text-publications-v1',
  podcast_audio: 'podcast-audio-publications-v1',
};
export const EVIDENCE_ID = 'candidate_evidence';
// 带二进制下载的流:关键数下面写「下载 · 复用」而不是「新增 · 更新」
export const BINARY_STREAMS = new Set(['media', 'podcast_audio']);

export const GROUPS = { cat: '目录', raw: '原文', ai: 'AI 派生', media: '媒体', pod: '播客', ready: '就绪', back: '回传' };

// 每条流:产品名 · 分组 · 图标名(组件映射到 lucide)· 站点小字「装了什么」· 抽屉「装了什么」[内容, 接收端语义]
export const STREAMS = [
  {
    id: 'sources', name: '来源目录', group: 'cat', icon: 'rss', what: ['目录', '启停', '删除'],
    items: [['来源目录与启停', '外网权威;启停是可逆的软停用'], ['物理删除', '以 tombstone 传播,本机副本物理删除']],
  },
  {
    id: 'taxonomy', name: '标签目录', group: 'cat', icon: 'tags', what: ['已发布目录'],
    items: [['Taxonomy 已发布版本', '外网治理,本机只读;版本号单独计,不进共享修订快照'], ['候选审核', '只在外网']],
  },
  {
    id: 'articles', name: '文章', group: 'raw', icon: 'file-text', what: ['正文', '公共日报', '译文'],
    items: [['正文与元数据', '外网权威,authority 围栏;本机不重抓'], ['公共日报', '以文章形式到达,本机不生成'], ['译文缓存', '扩展字段;本机已核验的终稿译文保留(#218)']],
  },
  {
    id: 'analyses', name: 'AI 分析', group: 'ai', icon: 'sparkles', what: ['评分', '速读', 'AI 标签'],
    items: [['新闻价值 + 内容价值', '不重算、不覆盖'], ['速读摘要', '速读卡 / 列表摘要'], ['AI 标签', '只替换 AI 打标;人工 / 规则标签保留'], ['撤回', '原文变了的分析以 tombstone 撤回']],
  },
  {
    id: 'events', name: '事件', group: 'ai', icon: 'layers', opt: OPTIONAL_CAPABILITIES.events, what: ['成员', '合并'],
    items: [['事件与成员', '多源聚合;本机不往外网事件里加成员'], ['合并重定向', '旧事件号仍可解析'], ['热点榜快照', '不同步,本机按可见性重建']],
  },
  {
    id: 'media', name: '图片', group: 'media', icon: 'image', what: ['正文', '社交', '封面'],
    items: [['正文 / 社交 / 播客封面图', '校验大小、SHA-256 与格式后入库;本地已有同哈希文件直接复用'], ['原播客音频', '不同步'], ['删除', '不传播;本机 7 天宽限后回收']],
  },
  {
    id: 'podcast_texts', name: '播客文本', group: 'pod', icon: 'scroll-text', opt: OPTIONAL_CAPABILITIES.podcast_texts, what: ['逐字稿', '博客', '讲稿'],
    items: [['逐字稿 / 中文博客 / 讲稿', '只同步已发布的'], ['下架', 'tombstone 撤回本机副本']],
  },
  {
    id: 'podcast_audio', name: '精简音频', group: 'pod', icon: 'headphones', opt: OPTIONAL_CAPABILITIES.podcast_audio, what: ['中文精简音频'],
    items: [['中文精简音频', '元数据 + 二进制校验后入库'], ['下架', 'tombstone 撤回;文件随后回收']],
  },
  {
    id: 'source_states', name: '采集就绪', group: 'ready', icon: 'flag', what: ['就绪状态'],
    items: [['各源采集就绪状态', '永远最后一条:前面的内容都到了才发布就绪']],
  },
];
export const EVIDENCE = {
  id: EVIDENCE_ID, name: '候选证据', group: 'back', icon: 'undo-2', what: ['自定源标签'],
  items: [['自定源标签候选证据', '只含标签、维度、置信度、文章指纹、来源溯源号与提示词版本;不含正文、摘要、链接'], ['方向', '本机 → 外网,每次运行末尾整份替换']],
};
const STREAM_BY_ID = new Map([...STREAMS, EVIDENCE].map((s) => [s.id, s]));

export function streamMeta(id) {
  return STREAM_BY_ID.get(id) || null;
}
export function streamName(id) {
  return STREAM_BY_ID.get(id)?.name || String(id || '');
}

// 本机自有、不参与同步的内容(卡底「不同步」常驻)
export const LOCAL_ONLY = ['账户', '订阅', '兴趣', '个人早报', '隐藏源', '人工 / 规则标签', '论文全文任务', '本机自定源正文', '公告', '榜单快照'];
export const LOCAL_NOTE = { 榜单快照: '本机按可见性重建', 本机自定源正文: '本机采集与分析;只回传标签证据' };

// 逐流抽屉「本机」格:[标签, inventory.items 键];'@tombstone' 取 inventory.streams[流].tombstone;'—' 字面值原样显示
const INVENTORY_CELLS = {
  sources: [['来源', 'sources'], ['tombstone', '@tombstone']],
  taxonomy: [['标签', 'taxonomy_tags']],
  articles: [['文章', 'articles'], ['公共日报', 'daily_briefs'], ['翻译', 'translations'], ['tombstone', '@tombstone']],
  analyses: [['评分', 'analyses'], ['速读摘要', 'summaries'], ['内容价值', 'content_values'], ['AI 标签', 'llm_tags'], ['tombstone', '@tombstone']],
  events: [['事件', 'events'], ['tombstone', '@tombstone']],
  media: [['图片', 'media'], ['删除', '=不传播']],
  podcast_texts: [['逐字稿', 'podcast_transcripts'], ['中文博客', 'podcast_blogs'], ['讲稿', 'podcast_scripts'], ['tombstone', '@tombstone']],
  podcast_audio: [['精简音频', 'podcast_audio'], ['tombstone', '@tombstone']],
  source_states: [['就绪状态', 'source_states']],
  candidate_evidence: [['候选证据', 'candidate_evidence']],
};

// inventory 缺键(后端还没上线某项)→ 该格不显示;整条流都没有 → 返回空数组(抽屉整节隐藏)。
export function inventoryCells(stream, inventory) {
  const spec = INVENTORY_CELLS[stream] || [];
  if (!inventory) return [];
  const items = inventory.items || {};
  const ent = (inventory.streams || {})[stream] || {};
  const out = [];
  for (const [label, key] of spec) {
    if (key.startsWith('=')) { out.push([label, key.slice(1)]); continue; }
    const raw = key === '@tombstone' ? ent.tombstone : items[key];
    if (raw === undefined || raw === null || !Number.isFinite(Number(raw))) continue;
    out.push([label, Number(raw)]);
  }
  return out.some(([, v]) => typeof v === 'number') ? out : [];
}

// ── 协商 ──
// 与 remote_sync.negotiated_v2_stream_order 同算法:legacy 六流 + 按能力插入 events / podcast_texts / podcast_audio。
export function negotiatedOrder(capabilities) {
  if (!Array.isArray(capabilities)) return null;
  const caps = new Set(capabilities);
  const streams = [...LEGACY_STREAM_ORDER];
  if (caps.has(OPTIONAL_CAPABILITIES.events)) streams.splice(streams.indexOf('analyses') + 1, 0, 'events');
  let at = streams.indexOf('source_states');
  if (caps.has(OPTIONAL_CAPABILITIES.podcast_texts)) { streams.splice(at, 0, 'podcast_texts'); at += 1; }
  if (caps.has(OPTIONAL_CAPABILITIES.podcast_audio)) streams.splice(at, 0, 'podcast_audio');
  return streams;
}

const validOrder = (o) => (Array.isArray(o) && o.length && o.every((s) => STREAM_ORDER.includes(s)) ? o : null);

// ── 格式 ──
const pad = (n) => String(n).padStart(2, '0');
export const fmt = (n) => Number(n || 0).toLocaleString('en-US');
export function fmtBytes(n) {
  const v0 = Number(n) || 0;
  if (v0 <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = v0;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}
export function fmtDur(secs) {
  if (secs === null || secs === undefined || !Number.isFinite(Number(secs))) return '';
  const s = Math.max(0, Math.round(Number(secs)));
  if (s >= 3600) return `${Math.floor(s / 3600)} 时 ${Math.floor((s % 3600) / 60)} 分`;
  if (s >= 60) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `${s} 秒`;
}
// 时间戳三种形态:ISO 字符串(KV / 逐流起止)、epoch 秒(jobs 表)、Date
export function toDate(v) {
  if (v === null || v === undefined || v === '') return null;
  let d;
  if (v instanceof Date) d = v;
  else if (typeof v === 'number' || /^\d+(\.\d+)?$/.test(String(v))) {
    const n = Number(v);
    d = new Date(n < 1e12 ? n * 1000 : n);
  } else d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}
export function fmtClock(iso) {
  const d = toDate(iso);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}
export function fmtDay(iso) {
  const d = toDate(iso);
  return d ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}
export function fmtFull(iso) {
  const d = toDate(iso);
  return d ? `${fmtDay(d)}:${pad(d.getSeconds())}` : '';
}
// 同一天只给时刻,跨天给「月-日 时:分」
export function fmtWhen(iso, now = new Date()) {
  const d = toDate(iso);
  if (!d) return '';
  const same = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  return same ? fmtClock(d) : fmtDay(d);
}
export function secsBetween(a, b) {
  const x = toDate(a);
  const y = toDate(b);
  if (!x || !y) return null;
  return Math.max(0, (y.getTime() - x.getTime()) / 1000);
}
// authority 形如 `dorami-<uuid>`(archive_sync_v2.producer_authority_id),短码取 uuid 前 8 位;显式指定的 id 原样截前 8 位。
export const shortId = (id) => String(id || '').replace(/^dorami-/, '').slice(0, 8);
export function hostOf(url) {
  try { return new URL(url).host || String(url || ''); } catch { return String(url || '').replace(/^[a-z]+:\/\//i, '').replace(/\/.*$/, ''); }
}
export function schemaShort(schema) {
  const m = /v(\d+)$/.exec(String(schema || ''));
  return m ? `v${m[1]}` : String(schema || '');
}
export const withV = (version) => (version ? (/^v/i.test(String(version)) ? String(version) : `v${version}`) : '');
// 修订号:taxonomy 是独立版本号(v12),其余是全局修订(r2,418,520)
export function fmtSnapshot(stream, snapshot) {
  const s = String(snapshot ?? '').trim();
  if (!s) return '';
  if (stream === 'taxonomy') return `v${s}`;
  return /^\d+$/.test(s) ? `r${fmt(Number(s))}` : `r${s}`;
}
export function trigLabel(createdBy) {
  const v = String(createdBy || '').trim();
  return !v || v === 'system' ? '定时' : v;
}
export const isScheduled = (createdBy) => trigLabel(createdBy) === '定时';

// ── 统计归一:result.streams / progress.streams_done / KV checkpoint 三处字段名不同,收成一套 ──
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const has = (o, k) => o[k] !== undefined && o[k] !== null;
export function normStats(raw) {
  if (!raw || typeof raw !== 'object') return null;
  return {
    pages: num(raw.pages),
    count: num(raw.count),
    inserted: num(raw.inserted),
    updated: num(raw.updated),
    deleted: num(raw.deleted),
    pruned: num(raw.pruned),
    skipped: num(raw.skipped),
    deferred: num(raw.deferred),
    downloaded: has(raw, 'downloaded') ? num(raw.downloaded) : num(raw.media_downloaded) + num(raw.podcast_audio_downloaded),
    reused: has(raw, 'reused') ? num(raw.reused) : num(raw.media_reused) + num(raw.podcast_audio_reused),
    downloadedBytes: num(raw.downloaded_bytes),
    reusedBytes: num(raw.reused_bytes),
    pending: Number.isFinite(raw.pending) ? raw.pending : null,
    startedAt: raw.started_at || '',
    completedAt: raw.completed_at || '',
    snapshot: raw.snapshot !== undefined && raw.snapshot !== null ? String(raw.snapshot) : '',
    authority: raw.authority_id || '',
    backlog: raw.backlog_replay && typeof raw.backlog_replay === 'object' ? raw.backlog_replay : null,
  };
}
const mapStats = (obj) => Object.fromEntries(
  Object.entries(obj && typeof obj === 'object' ? obj : {})
    .filter(([sid]) => STREAM_ORDER.includes(sid))
    .map(([sid, raw]) => [sid, normStats(raw)])
    .filter(([, v]) => v),
);

// ── 一次运行(job)的视图 ──
// 成功:逐流 = result.streams;失败 / 运行中:逐流 = progress.streams_done,当前流 = progress.stream,页 = progress.page。
// fallbackOrder:协商顺序(progress.order / result.capabilities 都没有时用控制台的 stream_order)。
export function runOf(job, fallbackOrder = null) {
  if (!job || typeof job !== 'object') return null;
  const status = String(job.status || '');
  const active = status === 'running' || status === 'queued';
  const p = job.progress && typeof job.progress === 'object' ? job.progress : null;
  const r = job.result && typeof job.result === 'object' ? job.result : null;
  const v1 = Boolean(r && !r.streams && ('pulled' in r || 'imported' in r));
  const order = validOrder(p?.order) || negotiatedOrder(r?.capabilities) || validOrder(fallbackOrder) || STREAM_ORDER;
  const streams = status === 'succeeded' && r?.streams ? mapStats(r.streams) : mapStats(p?.streams_done);
  const cur = (active || status === 'failed') && p?.stream && STREAM_ORDER.includes(p.stream) ? p.stream : null;
  return {
    id: String(job.job_id || job.id || ''),
    status,
    active,
    v1,
    startAt: job.started_at || job.created_at || '',
    createdAt: job.created_at || '',
    endAt: job.ended_at || '',
    trigger: job.created_by,
    order,
    streams,
    cur,
    page: Number.isFinite(p?.page) && p.page > 0 ? p.page : null,
    processed: num(p?.processed),
    streamProcessed: num(p?.stream_processed),
    streamTotal: Number.isFinite(p?.stream_total) ? p.stream_total : null,
    streamStartedAt: p?.stream_started_at || '',
    downloaded: num(p?.downloaded),
    reused: num(p?.reused),
    downloadedBytes: num(p?.downloaded_bytes),
    reusedBytes: num(p?.reused_bytes),
    runPending: p?.run_pending && typeof p.run_pending === 'object' ? p.run_pending : null,
    evidence: r?.candidate_evidence && typeof r.candidate_evidence === 'object' ? r.candidate_evidence : null,
    error: job.error || '',
    v1Result: v1 ? r : null,
  };
}

// 没有任务记录、但 KV 里有 checkpoint(任务表已清理)时,用 checkpoint 拼出「上次同步」。
export function runFromCheckpoints(checkpoints, order) {
  const streams = mapStats(checkpoints);
  if (!Object.keys(streams).length) return null;
  const done = Object.values(streams).map((s) => s.completedAt).filter(Boolean).sort();
  return {
    id: '', status: 'succeeded', active: false, v1: false, fromCheckpoint: true,
    startAt: '', createdAt: '', endAt: done.at(-1) || '', trigger: '', order: validOrder(order) || STREAM_ORDER,
    streams, cur: null, page: null, processed: 0, streamProcessed: 0, streamTotal: null, streamStartedAt: '',
    downloaded: 0, reused: 0, downloadedBytes: 0, reusedBytes: 0, runPending: null, evidence: null, error: '', v1Result: null,
  };
}

// 一条流在某次运行里的状态:done | run | bad | todo | skip(未协商)
export function streamStateIn(run, sid, order = null) {
  if (sid === EVIDENCE_ID) {
    if (!run || run.status !== 'succeeded' || !run.evidence) return 'todo';
    return run.evidence.status === 'success' ? 'done' : 'bad';
  }
  const ord = (run && run.order) || order || STREAM_ORDER;
  if (!ord.includes(sid)) return 'skip';
  if (!run) return 'todo';
  if (run.streams[sid]) return 'done';
  if (run.cur === sid) {
    if (run.active) return 'run';
    return run.status === 'failed' ? 'bad' : 'todo';
  }
  // 老后端没有 streams_done:当前流之前的流必然已推进 checkpoint(顺序拉取)
  if (run.cur && ord.indexOf(run.cur) > ord.indexOf(sid)) return 'done';
  if (run.status === 'succeeded' && !run.v1) return 'done';
  return 'todo';
}

export const STATE_LABEL = { done: '完成', run: '进行中', bad: '失败', todo: '未跑', skip: '未协商' };

// 运行进度:第几条流(从 1 起)/ 协商流数
export function runStep(run) {
  if (!run) return null;
  const n = run.order.length;
  const i = run.cur ? run.order.indexOf(run.cur) : -1;
  return { k: i >= 0 ? i + 1 : Math.min(n, Object.keys(run.streams).length), n };
}

// 当前流百分比:stream_total 未知(老生产端)→ null,不显示百分比
export function streamPercent(run) {
  if (!run || run.streamTotal === null || run.streamTotal === undefined) return null;
  if (run.streamTotal <= 0) return run.streamProcessed > 0 ? 100 : null;
  return Math.max(0, Math.min(100, Math.round((run.streamProcessed / run.streamTotal) * 100)));
}

// 逐流用时:started_at → completed_at;老结果只有 completed_at 时用上一站的 completed_at(首站用运行开始)推算
export function streamDurations(run) {
  const out = {};
  if (!run) return out;
  let prevEnd = run.startAt || '';
  for (const sid of run.order) {
    const st = run.streams[sid];
    if (!st) continue;
    let secs = st.startedAt && st.completedAt ? secsBetween(st.startedAt, st.completedAt) : null;
    if (secs === null && st.completedAt && prevEnd) secs = secsBetween(prevEnd, st.completedAt);
    out[sid] = secs;
    if (st.completedAt) prevEnd = st.completedAt;
  }
  return out;
}

// 当前流已用时(运行中按 now;失败按任务结束时间)
export function curStreamSecs(run, now = new Date()) {
  if (!run?.cur || !run.streamStartedAt) return null;
  return secsBetween(run.streamStartedAt, run.active ? now : (run.endAt || now));
}

export function runSecs(run, now = new Date()) {
  if (!run) return null;
  if (run.fromCheckpoint) return null;
  return secsBetween(run.startAt, run.active ? now : run.endAt);
}

// 合计:已完成各流 + 当前流已提交的条数 / 字节
export function runTotals(run) {
  const t = { count: 0, inserted: 0, updated: 0, deleted: 0, downloaded: 0, reused: 0, downloadedBytes: 0, reusedBytes: 0 };
  if (!run) return t;
  if (run.v1Result) {
    t.count = num(run.v1Result.pulled);
    t.inserted = num(run.v1Result.imported);
    t.updated = num(run.v1Result.updated);
    return t;
  }
  for (const st of Object.values(run.streams)) {
    t.count += st.count; t.inserted += st.inserted; t.updated += st.updated; t.deleted += st.deleted;
    t.downloaded += st.downloaded; t.reused += st.reused; t.downloadedBytes += st.downloadedBytes; t.reusedBytes += st.reusedBytes;
  }
  if (run.cur && !run.streams[run.cur]) {
    t.count += run.streamProcessed;
    t.downloaded += run.downloaded; t.reused += run.reused;
    t.downloadedBytes += run.downloadedBytes; t.reusedBytes += run.reusedBytes;
  }
  return t;
}

// 运行中「剩余」:当前流(分母 − 已处理)+ 未开始各站的待同步;任一未知(老生产端 / media 首页未到)→ null。
// taxonomy 是独立版本号,不进计数。
export function remainingOf(run) {
  if (!run?.active || !run.cur) return null;
  let total = 0;
  for (const sid of run.order) {
    if (sid === 'taxonomy') continue;
    const k = streamStateIn(run, sid);
    if (k === 'done' || k === 'skip') continue;
    if (k === 'run') {
      if (run.streamTotal === null) return null;
      total += Math.max(0, run.streamTotal - run.streamProcessed);
      continue;
    }
    const pend = run.runPending?.[sid];
    if (!Number.isFinite(pend)) return null;
    total += pend;
  }
  return total;
}

// ── 流水线站点:状态标记 + 关键数 + 小字 + 用时(组件只管排版) ──
// 返回 { k, num, small, tone: '' | 'faint' | 'run' | 'bad', sub: string[], dur }
export function stationOf(run, sid, { configured = true, now = new Date(), durations = null, order = null } = {}) {
  const k = configured ? streamStateIn(run, sid, order) : 'todo';
  const out = { k, num: '—', small: '', tone: 'faint', sub: [], dur: '' };
  if (!configured) { out.sub = ['首次整流']; return out; }
  if (k === 'skip') { out.num = '未协商'; out.sub = ['对端未广播']; return out; }
  if (sid === EVIDENCE_ID) {
    if (k === 'done') {
      out.num = fmt(run.evidence.inserted); out.small = '新增'; out.tone = '';
      out.sub = [`跳过 ${fmt(run.evidence.skipped)}`];
    } else if (k === 'bad') {
      out.num = '失败'; out.tone = 'bad'; out.sub = ['回传失败'];
    } else out.sub = ['运行末尾'];
    return out;
  }
  if (k === 'done') {
    const st = run.streams[sid];
    const secs = (durations || streamDurations(run))[sid];
    out.dur = secs === null || secs === undefined ? '' : fmtDur(secs);
    if (!st) { out.sub = ['已完成']; return out; }
    if (sid === 'taxonomy') {
      if (st.count) { out.num = fmt(st.count); out.small = '条'; out.tone = ''; } else out.num = '未变';
      out.sub = st.snapshot ? [`v${st.snapshot}`] : [];
      return out;
    }
    if (!st.count) { out.num = '0'; out.sub = ['无变化']; return out; }
    out.num = fmt(st.count); out.small = '条'; out.tone = '';
    out.sub = BINARY_STREAMS.has(sid)
      ? [`下载 ${fmt(st.downloaded)}`, `复用 ${fmt(st.reused)}`]
      : [`+${fmt(st.inserted)}`, `${fmt(st.updated)} 更新`, ...(st.deleted ? [`−${fmt(st.deleted)}`] : [])];
    return out;
  }
  if (k === 'run') {
    out.num = fmt(run.streamProcessed); out.tone = 'run';
    out.small = run.streamTotal !== null ? `/ ${fmt(run.streamTotal)}` : '条';
    out.sub = BINARY_STREAMS.has(sid)
      ? [`下载 ${fmt(run.downloaded)}`, `复用 ${fmt(run.reused)}`]
      : (run.page ? [`第 ${run.page} 页`] : []);
    const secs = curStreamSecs(run, now);
    out.dur = secs === null ? '进行中' : `${fmtDur(secs)} · 进行中`;
    return out;
  }
  if (k === 'bad') {
    out.num = run.page ? `第 ${run.page} 页` : '失败'; out.tone = 'bad';
    out.sub = run.page > 1 ? [`前 ${run.page - 1} 页已提交`] : ['未提交'];
    const secs = curStreamSecs(run, now);
    out.dur = secs === null ? '' : fmtDur(secs);
    return out;
  }
  // todo
  if (run?.status === 'failed') out.sub = ['未跑'];
  else if (run?.active) {
    const pend = run.runPending?.[sid];
    out.sub = Number.isFinite(pend) ? [`待同步 ${fmt(pend)}`] : ['待跑'];
  } else out.sub = ['待跑'];
  return out;
}

// ── 定时 ──
export const FREQ_PRESETS = [['daily', '每天'], ['six', '每 6 小时'], ['hourly', '每小时'], ['custom', '自定义']];
export function cronFromFreq(freq, dailyTime, customCron) {
  if (freq === 'hourly') return '0 * * * *';
  if (freq === 'six') return '0 */6 * * *';
  if (freq === 'daily') {
    const [h, m] = String(dailyTime || '03:00').split(':');
    return `${Number(m) || 0} ${Number(h) || 0} * * *`;
  }
  return String(customCron || '').trim();
}
export function freqFromCron(cron) {
  const c = String(cron || '').trim();
  if (!c || c === '0 3 * * *') return { freq: 'daily', dailyTime: '03:00', customCron: '' };
  if (c === '0 * * * *') return { freq: 'hourly', dailyTime: '03:00', customCron: '' };
  if (c === '0 */6 * * *') return { freq: 'six', dailyTime: '03:00', customCron: '' };
  const m = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec(c);
  if (m) return { freq: 'daily', dailyTime: `${pad(m[2])}:${pad(m[1])}`, customCron: '' };
  return { freq: 'custom', dailyTime: '03:00', customCron: c };
}
export function cronLabel(cron) {
  const f = freqFromCron(cron);
  if (f.freq === 'daily') return `每天 ${f.dailyTime}`;
  return FREQ_PRESETS.find(([id]) => id === f.freq)?.[1] || '自定义';
}

// APScheduler 3.x CronTrigger.from_crontab 语义:day_of_week 0 = 周一(mon..sun),day 与 day_of_week 取交集。
const DOW_NAMES = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };
function cronField(src, min, max, names = null) {
  const out = new Set();
  const val = (s) => {
    if (names && Object.hasOwn(names, s)) return names[s];
    return /^\d+$/.test(s) ? Number(s) : null;
  };
  for (const part of String(src).toLowerCase().split(',')) {
    const m = /^(\*|[a-z0-9]+(?:-[a-z0-9]+)?)(?:\/(\d+))?$/.exec(part.trim());
    if (!m) return null;
    let lo = min;
    let hi = max;
    if (m[1] !== '*') {
      const [a, b] = m[1].split('-');
      lo = val(a);
      hi = b !== undefined ? val(b) : (m[2] ? max : lo);
      if (lo === null || hi === null) return null;
    }
    const step = m[2] ? Number(m[2]) : 1;
    if (!step || lo < min || hi > max || lo > hi) return null;
    for (let v = lo; v <= hi; v += step) out.add(v);
  }
  return out;
}
export function nextCronRun(cron, from = new Date()) {
  const f = String(cron || '').trim().split(/\s+/);
  if (f.length !== 5) return null;
  const mi = cronField(f[0], 0, 59);
  const ho = cronField(f[1], 0, 23);
  const dom = cronField(f[2], 1, 31);
  const mon = cronField(f[3], 1, 12);
  const dow = cronField(f[4], 0, 6, DOW_NAMES);
  if (!mi || !ho || !dom || !mon || !dow) return null;
  const d = new Date(from.getTime());
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() + 1);
  for (let guard = 0; guard < 200000; guard += 1) {
    if (!mon.has(d.getMonth() + 1)) { d.setMonth(d.getMonth() + 1, 1); d.setHours(0, 0, 0, 0); continue; }
    if (!dom.has(d.getDate()) || !dow.has((d.getDay() + 6) % 7)) { d.setDate(d.getDate() + 1); d.setHours(0, 0, 0, 0); continue; }
    if (!ho.has(d.getHours())) { d.setHours(d.getHours() + 1, 0, 0, 0); continue; }
    if (!mi.has(d.getMinutes())) { d.setMinutes(d.getMinutes() + 1, 0, 0); continue; }
    return d;
  }
  return null;
}

// 运行失败的定位文案:「流 · 第 N 页」;失败在任何流开始之前(登录 / 协商)只说「同步失败」
export function runErrorHead(run) {
  if (!run?.cur) return '同步失败';
  return `${streamName(run.cur)}${run.page ? ` · 第 ${run.page} 页` : ''}`;
}

// 定时胶囊:开 = 频率 + 下次;关 / 需选协议 / v1 已停用 = 状态词(条件性警示挂在点的颜色上)
export function schedChip(sched, now = new Date()) {
  if (!sched) return { on: false, label: '—', next: null };
  if (sched.migration_required) return { on: false, label: '需选协议', next: null, warn: true };
  if (sched.protocol_downgrade_blocked) return { on: false, label: 'v1 已停用', next: null, warn: true };
  if (!sched.enabled) return { on: false, label: '关', next: null };
  return { on: true, label: cronLabel(sched.cron), next: nextCronRun(sched.cron, now) };
}

// ── 控制台块:后端新字段缺省时从旧的 state.targets 兜底推导(老后端也能显示接入 / 上次同步) ──
export function consoleOf(status, schedule = null) {
  const c = status?.console && typeof status.console === 'object' ? status.console : null;
  if (c) {
    const order = validOrder(c.stream_order) || negotiatedOrder(c.capabilities) || STREAM_ORDER;
    return { ...c, configured: Boolean(c.configured ?? c.target), stream_order: order, checkpoints: c.checkpoints || {} };
  }
  const targets = status?.state?.targets || {};
  const urls = Object.keys(targets);
  const schedUrl = schedule?.base_url || '';
  const pick = (schedUrl && targets[schedUrl] ? schedUrl : null)
    || urls.sort((a, b) => String(targets[b]?.last_synced_at || '').localeCompare(String(targets[a]?.last_synced_at || '')))[0]
    || schedUrl || '';
  const t = targets[pick] || {};
  const caps = Array.isArray(t.v2_capabilities) ? t.v2_capabilities : null;
  return {
    target: pick,
    configured: Boolean(pick),
    stream_order: negotiatedOrder(caps) || STREAM_ORDER,
    authority_id: t.v2_authority_id || '',
    schema_version: t.v2_schema_version || '',
    capabilities: caps || [],
    last_synced_at: t.last_synced_at || '',
    checkpoints: t.v2_streams || {},
    username: t.username || '',
    legacy: true,
  };
}

// 「待同步 N」(截至测试连接时):只在 probe.pending 有该流、且该流 checkpoint.snapshot == probe.pending_since 时成立;
// 计数口径是「修订晚于 checkpoint 的实体」(含更新),不是两端条数差。taxonomy / media 不在 pending_by_stream 里。
export function probePending(con, sid) {
  const p = con?.probe;
  if (!p || !p.pending || typeof p.pending !== 'object') return null;
  const v = p.pending[sid];
  if (!Number.isFinite(v)) return null;
  const since = String(p.pending_since ?? '');
  const snap = String(con.checkpoints?.[sid]?.snapshot ?? '');
  return snap === since ? v : null;
}

// 首页 S 磁贴(读 /api/admin/home 的 sync,契约 §4.7)
export function syncTileView(sync, now = new Date()) {
  if (!sync || typeof sync !== 'object') return { label: '上次同步', num: '—', tone: '', mark: null, title: '' };
  if (sync.configured === false || sync.status === 'none') return { label: '远端', num: '未接入', tone: 'is-faint', mark: null, title: '' };
  if (sync.status === 'running') {
    const total = Number(sync.running?.total);
    const done = Number(sync.running?.done) || 0;
    return {
      label: '同步中',
      num: Number.isFinite(total) && total > 0 ? `${Math.min(total, done + 1)} / ${total}` : '…',
      tone: '', mark: 'run',
      title: sync.running?.stream ? `同步中 · ${streamName(sync.running.stream)}` : '同步中',
    };
  }
  if (sync.status === 'failed') {
    const f = sync.failed || {};
    return {
      label: '上次成功', num: fmtWhen(sync.last_success_at, now) || '—', tone: '', mark: 'bad',
      title: `上次同步失败${f.stream ? ` · ${streamName(f.stream)}` : ''}${f.page ? ` 第 ${f.page} 页` : ''}`,
    };
  }
  const at = sync.last_success_at || sync.last_run_at;
  return { label: '上次同步', num: fmtWhen(at, now) || '—', tone: '', mark: at ? 'ok' : null, title: at ? `上次同步成功 · ${fmtDay(at)}` : '' };
}

// 「状态」磁贴待处理:同步失败时多一条(告警只在状态磁贴出现一次),点了落到本页运行记录
export function syncPendingItem(sync) {
  if (!sync || sync.status !== 'failed') return null;
  return { tone: 'bad', key: 'sync', name: '节点同步失败', value: sync.failed?.stream ? streamName(sync.failed.stream) : '', page: 'sync', block: 'runs' };
}
