// Private navigation is an explicit, owner-scoped capability; public tag search stays independent.
export const canFilterCustomInterests = (capabilities) => capabilities?.interest_match_projection === 1
  && capabilities?.custom_interest_filter === true && capabilities?.match_contract === 1;

export function resolveInterestNavigation(ref, { tags = [], custom = [], capabilities } = {}) {
  if (ref?.kind === 'custom' && canFilterCustomInterests(capabilities)) {
    const item = custom.find((entry) => String(entry.id) === ref.id && entry.status === 'active');
    return item ? { kind: 'custom', id: String(item.id), name: item.name } : null;
  }
  if (ref?.kind === 'tag' && capabilities?.interest_match_projection === 1) {
    const item = tags.find((entry) => entry.code === ref.id);
    return item ? { kind: 'tag', id: item.id, name: item.name, facet: item.kind } : null;
  }
  return null;
}

export const strictInterestQuery = (query) => query?.scope?.axis === 'interest'
  && !query.activeTagId && canFilterCustomInterests(query.interestCapabilities);
export const blockedInterestQuery = (query) => (Boolean(query?.activeCustomInterestId)
  && (!canFilterCustomInterests(query.interestCapabilities) || query.customInterestUnavailable === true))
  || (query?.scope?.axis === 'interest' && !query.activeTagId && query.interestUnion === true
    && !canFilterCustomInterests(query.interestCapabilities));
export const invalidInterestWindow = (error) => error?.interestWindowInvalid === true
  || [401, 403, 404].includes(error?.status) || staleInterestPagination(error);

export const staleInterestPagination = (error) => error?.status === 409 && error?.code === 'interest_pagination_stale';

// Both attempts share one AbortSignal. A stale append is replaced, never concatenated.
export async function loadInterestPage(fetchPage, { skip = 0, append = false, token = '', strict = false } = {}) {
  if (strict && append && !token) throw Object.assign(new Error('兴趣列表已变化，请重新加载'), { interestWindowInvalid: true });
  let replace = !append;
  let data;
  try { data = await fetchPage(skip, strict && append ? token : ''); }
  catch (error) {
    if (!strict || !staleInterestPagination(error)) throw error;
    try { data = await fetchPage(0, ''); }
    catch (retryError) { retryError.interestWindowInvalid = true; throw retryError; }
    replace = true;
  }
  if (strict && (typeof data?.pagination_token !== 'string' || !data.pagination_token)) {
    throw Object.assign(new Error('兴趣列表暂不可用，请重新加载'), { interestWindowInvalid: true });
  }
  return { data, replace, token: strict ? data.pagination_token : '' };
}
