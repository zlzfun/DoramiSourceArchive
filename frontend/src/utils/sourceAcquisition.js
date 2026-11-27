// 源的获取方式(issue #344):抓取(平台的抓取器去拿)与推送(收录令牌送来)并列,一个源可以两样都有。
// 源管理的行、检视器与运维「收录令牌」共用这里的口径。

export const ACQUISITION_LABELS = { fetch: '抓取', push: '推送', both: '抓取 · 推送', none: '无抓取' };

// 在往这个源推的收录令牌(`GET /api/fetchers` 每条带的 push_bindings)。
export function pushBindingsOf(source) {
  return Array.isArray(source?.push_bindings) ? source.push_bindings : [];
}

// 有没有抓取器:读者自定源走专用抓取通道,算有;公共源看 bound(与源管理「能否运行」同一口径)。
export function hasFetcher(source) {
  return Boolean(source?.user_source) || source?.bound !== false;
}

export function acquisitionOf(source) {
  const fetched = hasFetcher(source);
  const pushed = pushBindingsOf(source).length > 0;
  if (fetched && pushed) return 'both';
  if (fetched) return 'fetch';
  return pushed ? 'push' : 'none';
}

// 最近一批推送的时间:各令牌里最晚的那个;都没推过返回空串。
export function lastPushedAt(source) {
  return pushBindingsOf(source).reduce((latest, binding) => {
    const at = binding?.last_pushed_at || '';
    return at > latest ? at : latest;
  }, '');
}

// 可以挂推送绑定的源:公共源里不是抓取模板、没退役的(服务端另有校验,这里只是不把绑不了的列进下拉)。
export function bindableSources(catalog, boundIds = []) {
  const taken = new Set(boundIds);
  return (Array.isArray(catalog) ? catalog : []).filter((source) => (
    source && source.id && !source.is_template && !source.retired && !source.user_source && !taken.has(source.id)
  ));
}

// 令牌自行登记来源(issue #344 第二个 PR):今天登记了几个 / 每日上限;没有这项权限返回空串。
export function registerQuotaOf(token) {
  if (!token?.can_register_sources) return '';
  return `${Number(token.registered_today || 0)} / ${Number(token.register_daily_limit || 0)}`;
}

// 绑定行上的徽标:还没列入的来源(令牌自己登记的源都从待列入起步)标「临时」,与源管理同一个词。
export function bindingPillOf(binding) {
  return binding?.admission_status === 'provisional' ? '临时' : '';
}

// ── 登记来源(源管理页头的抽屉) ──
const SUFFIX = /^[a-z0-9_]{3,40}$/;
const FEED_LIKE = /(\.xml|\.rss|\.atom|\/feed\/?|\/rss\/?)(\?.*)?$/i;

// 表单 → 登记接口的载荷;填得不够返回 null(「登记」按钮据此置灰)。
// 给了账号 / 站点 / feed 的是定向源;「不限」的是合集,源 id 是 push_<后缀>。播客只有定向(必须给 feed)。
export function registerPayload(form) {
  const name = String(form.name || '').trim();
  const identity = String(form.identity || '').trim();
  const scope = form.kind === 'podcast_episode' ? 'one' : form.scope;
  if (!name) return null;
  const payload = { kind: form.kind, name, listed: Boolean(form.listed) };
  const avatar = String(form.avatar || '').trim();
  if (avatar) payload.avatar_url = avatar;
  if (form.tokenId) payload.token_id = Number(form.tokenId);
  if (scope === 'open') {
    const suffix = String(form.suffix || '').trim();
    if (!SUFFIX.test(suffix)) return null;
    return { ...payload, source_id_suffix: suffix };
  }
  if (!identity) return null;
  if (form.kind === 'social_post') return { ...payload, handle: identity.replace(/^@/, '') };
  if (form.kind === 'podcast_episode' || FEED_LIKE.test(identity)) return { ...payload, feed_url: identity };
  return { ...payload, url: identity };
}
