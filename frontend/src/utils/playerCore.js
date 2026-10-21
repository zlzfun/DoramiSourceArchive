// 全局播放器的纯逻辑(issue #199):条目、状态机、会话恢复、续播位置、轻量动效判定。
// 组件(components/player/*)只做 DOM 与音频;这里不碰 window,便于 node --test。
import { podcastOf } from './podcast.js';
import { latinLang } from './readerText.js';

export const PLAYER_TRACKS = Object.freeze(['original', 'digest', 'article_narration']);
export const PLAYER_RATES = Object.freeze([1, 1.25, 1.5, 2]);
export const PLAYER_FORMS = Object.freeze(['none', 'bar', 'mini']);

const isHttp = (value) => typeof value === 'string' && /^https?:\/\//i.test(value.trim());
const positive = (value) => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

export function playerItemKey(articleId, track) {
  return `${articleId}:${track}`;
}

/** 播客单集的一条音轨:original = 发布方原节目;digest = AI 精品导读。没有可播地址返回 null。 */
export function podcastTrackItem(article, track = 'original', showFallback = '') {
  const podcast = podcastOf(article);
  if (!article?.id || !podcast) return null;
  const digest = track === 'digest';
  const src = digest ? podcast.condensed_audio_url : podcast.audio_url;
  if (!isHttp(src)) return null;
  const title = article.title || '（无标题）';
  return {
    key: playerItemKey(article.id, digest ? 'digest' : 'original'),
    articleId: article.id,
    track: digest ? 'digest' : 'original',
    shape: 'podcast',
    src: src.trim(),
    title,
    lang: latinLang(title) || '',
    show: podcast.show_title || showFallback || '',
    cover: isHttp(podcast.image_url) ? podcast.image_url.trim() : '',
    ai: digest,
    duration: positive(digest ? podcast.condensed_duration_seconds : podcast.duration_seconds),
  };
}

/** 文章的 AI 导读旁白(listen_guide)。没有封面:条上画 ✦ 方块。 */
export function articleNarrationItem(article, sourceName = '') {
  const guide = article?.listen_guide && typeof article.listen_guide === 'object' ? article.listen_guide : null;
  if (!article?.id || !guide?.audio_ready || !isHttp(guide.audio_url)) return null;
  const title = article.title || '（无标题）';
  return {
    key: playerItemKey(article.id, 'article_narration'),
    articleId: article.id,
    track: 'article_narration',
    shape: 'article',
    src: guide.audio_url.trim(),
    title,
    lang: latinLang(title) || '',
    show: sourceName || '',
    cover: '',
    ai: true,
    duration: positive(guide.duration_seconds),
  };
}

/** 条副行的音轨名(AI 音轨写明来历,不只靠图标)。 */
export function playerTrackLabel(item) {
  if (!item) return '';
  if (item.track === 'digest') return '精品导读';
  if (item.track === 'article_narration') return '文章导读';
  return '原节目';
}

/** 可访问名里的音轨说明:「原节目」/「精品导读，AI 生成」。 */
export function playerTrackSpeech(item) {
  if (!item) return '';
  return item.ai ? `${playerTrackLabel(item)}，AI 生成` : playerTrackLabel(item);
}

export function normalizeRate(value) {
  const n = Number(value);
  return PLAYER_RATES.includes(n) ? n : 1;
}

export function nextRate(value) {
  const i = PLAYER_RATES.indexOf(normalizeRate(value));
  return PLAYER_RATES[(i + 1) % PLAYER_RATES.length];
}

export function formatRate(value) {
  return `${normalizeRate(value)}×`;
}

// ── 状态机 ──
// status:idle(无条目)| loading(读者要求播放、尚未出声)| playing | paused | error(音频加载失败)。
// form:none(只有标题区入口)| bar(底部浮条)| mini(右下角小胶囊)。
export const initialPlayerState = Object.freeze({
  item: null,
  form: 'none',
  status: 'idle',
  buffering: false,
  rate: 1,
});

export function playerReducer(state, action) {
  switch (action?.type) {
    case 'load': {
      if (!action.item) return state;
      return {
        ...state,
        item: action.item,
        form: state.form === 'none' ? 'bar' : state.form,
        status: 'loading',
        buffering: false,
      };
    }
    case 'restore': {
      if (!action.item) return state;
      return {
        ...state,
        item: action.item,
        form: action.form === 'mini' ? 'mini' : 'bar',
        status: 'paused',
        buffering: false,
        rate: normalizeRate(action.rate),
      };
    }
    case 'playing':
      return state.item ? { ...state, status: 'playing', buffering: false } : state;
    case 'paused':
      // 失败态不被随后的 pause 事件冲掉:读者看到「无法播放」直到重试或换台
      if (!state.item || state.status === 'error') return state;
      return { ...state, status: 'paused', buffering: false };
    case 'intent-play':
      return state.item && state.status !== 'playing' ? { ...state, status: 'loading' } : state;
    case 'waiting':
      return state.item && !state.buffering ? { ...state, buffering: true } : state;
    case 'canplay':
      return state.buffering ? { ...state, buffering: false } : state;
    case 'error':
      return state.item ? { ...state, status: 'error', buffering: false } : state;
    case 'ended':
      return state.item ? { ...state, status: 'paused', buffering: false } : state;
    case 'rate':
      return { ...state, rate: normalizeRate(action.rate) };
    case 'minimize':
      return state.form === 'bar' ? { ...state, form: 'mini' } : state;
    case 'expand':
      return state.form === 'mini' ? { ...state, form: 'bar' } : state;
    case 'close':
      return { ...initialPlayerState, rate: state.rate };
    default:
      return state;
  }
}

/** 读者意图是「在放」:loading 时播放钮已经是暂停形,入口已是律动态。 */
export function isPlayingIntent(state) {
  return state?.status === 'playing' || state?.status === 'loading';
}

/** 条目行 / 入口用的当前播放态:'playing' | 'paused' | ''(失败或不是这一期)。 */
export function nowPlayingFor(state, articleId) {
  if (!state?.item || state.form === 'none' || !articleId || state.item.articleId !== articleId) return '';
  if (state.status === 'error') return '';
  return isPlayingIntent(state) ? 'playing' : 'paused';
}

// ── 续播位置 ──
/** 恢复位置:离结尾 3 秒以内视为已听完,从头开始(否则续播即刻结束)。 */
export function startPositionFor(saved, duration) {
  const pos = Number(saved);
  const safe = Number.isFinite(pos) && pos > 0 ? pos : 0;
  const d = Number(duration);
  if (!Number.isFinite(d) || d <= 0) return safe;
  if (safe >= d - 3) return 0;
  return Math.min(safe, d);
}

/**
 * 首次播放 / 元数据到达时从哪里起播。explicit = 读者在元数据到达之前明确拖到的位置(0 也算):
 * 只按时长夹取,不套「末 3 秒视为听完」;没有显式位置才按自动续播规则(startPositionFor)。
 */
export function resolveStartPosition({ saved = 0, explicit = null, duration = 0 } = {}) {
  const pos = explicit === null || explicit === undefined ? NaN : Number(explicit);
  if (Number.isFinite(pos)) {
    const t = Math.max(0, pos);
    const d = Number(duration);
    return Number.isFinite(d) && d > 0 ? Math.min(t, d) : t;
  }
  return startPositionFor(saved, duration);
}

export function formatClock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${String(m).padStart(2, '0')}:${ss}`;
}

/** 读屏的进度说法:「3 分 05 秒 / 共 11 分 44 秒」。 */
export function speakClock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h) return `${h} 小时 ${m} 分 ${ss} 秒`;
  if (m) return `${m} 分 ${ss} 秒`;
  return `${ss} 秒`;
}

// ── 会话恢复(sessionStorage:同一标签页刷新恢复为暂停态;新会话不恢复)──
export const PLAYER_SESSION_KEY = 'dorami-player-session-v1';

function validItem(item) {
  return Boolean(
    item && typeof item === 'object'
    && typeof item.articleId === 'string' && item.articleId
    && PLAYER_TRACKS.includes(item.track)
    && isHttp(item.src),
  );
}

export function sanitizeItem(item) {
  if (!validItem(item)) return null;
  const track = item.track;
  return {
    key: playerItemKey(item.articleId, track),
    articleId: item.articleId,
    track,
    shape: track === 'article_narration' ? 'article' : 'podcast',
    src: item.src.trim(),
    title: String(item.title || '（无标题）'),
    lang: item.lang === 'en' ? 'en' : '',
    show: String(item.show || ''),
    cover: isHttp(item.cover) ? item.cover.trim() : '',
    ai: track !== 'original',
    duration: positive(item.duration),
  };
}

export function readPlayerSession(storage, username) {
  if (!storage || !username) return null;
  try {
    const raw = storage.getItem(PLAYER_SESSION_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    // 账号不同(同一标签页换号登录)一律不恢复,并清掉
    if (!data || data.user !== username) {
      storage.removeItem(PLAYER_SESSION_KEY);
      return null;
    }
    const item = sanitizeItem(data.item);
    if (!item) return null;
    return { item, form: data.form === 'mini' ? 'mini' : 'bar', rate: normalizeRate(data.rate) };
  } catch {
    return null;
  }
}

export function writePlayerSession(storage, username, state) {
  if (!storage || !username) return false;
  try {
    if (!state?.item || state.form === 'none') {
      storage.removeItem(PLAYER_SESSION_KEY);
      return true;
    }
    storage.setItem(PLAYER_SESSION_KEY, JSON.stringify({
      user: username,
      item: sanitizeItem(state.item),
      form: state.form,
      rate: normalizeRate(state.rate),
    }));
    return true;
  } catch {
    return false;
  }
}

export function clearPlayerSession(storage) {
  try {
    storage?.removeItem(PLAYER_SESSION_KEY);
  } catch {
    /* 存储不可用:本会话本就不会恢复 */
  }
}

// ── 轻量动效自动档 ──
// 软件合成(无 GPU)下全套形变会掉帧:WebGL renderer 是软件光栅器,或首次形变的 p95 帧间隔超过 24ms,
// 本次会话改走「淡入 + 12px 位移」。设置里的「减少动效」优先级更高(那一档连淡入都缩到 120ms)。
export const LITE_FRAME_BUDGET_MS = 24;
const SOFTWARE_RENDERERS = /swiftshader|basic render|llvmpipe|softpipe|software rasterizer|microsoft basic/i;

export function rendererIsSoftware(renderer) {
  return SOFTWARE_RENDERERS.test(String(renderer || ''));
}

export function framePercentile(deltas, p = 0.95) {
  const list = (deltas || []).filter((d) => Number.isFinite(d) && d > 0).sort((a, b) => a - b);
  if (!list.length) return 0;
  const idx = Math.min(list.length - 1, Math.max(0, Math.ceil(p * list.length) - 1));
  return list[idx];
}

/** 首次形变录到的帧间隔是否说明这台机器吃不消(样本太少不下结论)。 */
export function framesSayLite(deltas) {
  const usable = (deltas || []).filter((d) => Number.isFinite(d) && d > 0);
  if (usable.length < 8) return false;
  return framePercentile(usable, 0.95) > LITE_FRAME_BUDGET_MS;
}

/** 'full' | 'lite':显式会话覆盖(测试与调试)> 已判定的 lite > 渲染器判定。 */
export function decideFxTier({ override = '', lite = false, renderer = '' } = {}) {
  if (override === 'full' || override === 'lite') return override;
  if (lite) return 'lite';
  return rendererIsSoftware(renderer) ? 'lite' : 'full';
}

// ── 问哆啦美浮钮避让 ──
// 条出现:让到条顶之上 12px;迷你:让到迷你左边 10px、中线对齐;否则归位。
// 入参都是视口坐标的矩形(left/top/right/bottom/width/height),fab 是没有位移时的矩形。
export function fabOffset(form, fab, bar, mini) {
  if (!fab || !fab.width) return { x: 0, y: 0 };
  if (form === 'bar' && bar?.height && bar.left < fab.right && bar.right > fab.left) {
    return { x: 0, y: Math.round(bar.top - 12 - fab.bottom) };
  }
  if (form === 'mini' && mini?.width) {
    return {
      x: Math.round(mini.left - 10 - fab.right),
      y: Math.round(mini.top + mini.height / 2 - (fab.top + fab.height / 2)),
    };
  }
  return { x: 0, y: 0 };
}
