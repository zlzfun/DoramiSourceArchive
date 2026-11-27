"""读者兴趣在阅读器列表里的消费方(issue #27 第一波:兴趣即透镜)。

兴趣(关注的规范标签;v3.56 issue #27 起屏蔽一极退役,兴趣只有关注)此前只有个人早报一个消费方。
本模块把它接到文章列表:
- ``interest_filter_condition``:「兴趣」谓词——文章命中读者关注的标签(SQL exists,与
  订阅 / 收藏谓词 AND 联合;三谓词两两正交,由 ``GET /api/articles`` 组合);
- ``favorite_filter_condition``:「收藏」谓词;
- ``annotate_interest``:逐条标注 ``interest_hits``(命中的关注标签名)——列表侧的命中胶囊据此渲染。

准入门槛(``INTEREST_MATCH_MIN_RELEVANCE``):兴趣谓词决定的是「放什么进来」而非「筛什么」——
订阅关掉时它在全站范围内生效,所以只认相关度 ≥ 0.8 的指派。
主标签只决定展示顺序,不能把弱相关内容提升为兴趣核心命中。
未打标的文章在兴趣谓词下不出现、在标注里命中为空——「未知」不是「不命中」,分析积压不能让内容消失
(它们在订阅谓词下照常出现)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import and_, or_
from sqlmodel import Session, exists, select

from models.db import (
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    ReaderFavoriteRecord,
    UserInterestTagRecord,
)

from services.interest_relevance import (
    INTEREST_MATCH_MIN_RELEVANCE, public_interest_qualifies, public_interest_predicate,
)


@dataclass(frozen=True)
class InterestMap:
    """读者兴趣的运行时形状:关注的 tag_id → 展示名。"""

    followed: Mapping[int, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.followed


def load_interest_map(session: Session, username: str) -> InterestMap:
    """只取 active 标签(被合并 / 废弃的标签不再产生命中,与早报 ``_load_interests`` 同口径)。"""

    username = (username or "").strip()
    if not username:
        return InterestMap()
    rows = session.exec(
        select(UserInterestTagRecord.tag_id, CmsTagRecord.name_zh, CmsTagRecord.name_en)
        .join(CmsTagRecord, CmsTagRecord.id == UserInterestTagRecord.tag_id)
        .where(
            UserInterestTagRecord.owner_username == username,
            CmsTagRecord.status == "active",
        )
    ).all()
    followed: dict[int, str] = {}
    for tag_id, name_zh, name_en in rows:
        followed[int(tag_id)] = (name_zh or name_en or "").strip() or str(tag_id)
    return InterestMap(followed=followed)


def _assignment_qualifies():
    return public_interest_predicate()


def interest_filter_condition(followed_tag_ids: Iterable[int]):
    """「兴趣」谓词:文章至少有一条命中关注标签且过准入门槛的指派。

    无关注标签时返回恒假条件(显式空集,与 ``subscribed_scope=only`` 零订阅的 ``__none__`` 哨兵同理)。
    """

    ids = sorted({int(value) for value in followed_tag_ids})
    if not ids:
        return ArticleRecord.id.in_(["__none__"])
    return exists(
        select(1).where(
            ArticleTagAssignmentRecord.article_id == ArticleRecord.id,
            ArticleTagAssignmentRecord.tag_id.in_(ids),
            _assignment_qualifies(),
        )
    )


def favorite_filter_condition(username: str):
    """「收藏」谓词:当前用户收藏过这篇。"""

    username = (username or "").strip()
    if not username:
        return ArticleRecord.id.in_(["__none__"])
    return exists(
        select(1).where(
            ReaderFavoriteRecord.owner_username == username,
            ReaderFavoriteRecord.article_id == ArticleRecord.id,
        )
    )


def interest_matches(tags: Sequence[Mapping[str, Any]], interests: InterestMap) -> list[str]:
    """按一篇文章的规范标签指派算命中关注名单;名单按目录序(指派序即主标签优先)。"""

    if interests.empty or not tags:
        return []
    hits: list[str] = []
    for tag in tags:
        try:
            tag_id = int(tag.get("id"))
        except (TypeError, ValueError):
            continue
        if tag_id in interests.followed and interests.followed[tag_id] not in hits:
            qualifies = public_interest_qualifies(tag.get('relevance'))
            if qualifies:
                hits.append(interests.followed[tag_id])
    return hits


def annotate_interest(
    items: Iterable[dict[str, Any]],
    tags_by_article: Mapping[str, Sequence[Mapping[str, Any]]],
    interests: InterestMap,
) -> None:
    """就地给列表项补 ``interest_hits``;无兴趣时为空列表(形状稳定)。"""

    for item in items:
        item["interest_hits"] = interest_matches(tags_by_article.get(item.get("id"), ()), interests)


__all__ = [
    "INTEREST_MATCH_MIN_RELEVANCE",
    "InterestMap",
    "annotate_interest",
    "favorite_filter_condition",
    "interest_filter_condition",
    "interest_matches",
    "load_interest_map",
]


def parse_interest_query(*, scope='off', kinds=None, tag_id=None, custom_id=None, annotation=False):
    """Strict opt-in: old scope=only is still public tags only."""
    from fastapi import HTTPException
    scope = (scope or 'off').strip().lower()
    if scope not in {'off', 'only'} and (kinds is not None or custom_id is not None):
        raise HTTPException(400, 'interest_scope 仅支持 off/only')
    values = {'tag'} if kinds is None else set(kinds.split(','))
    if not values or not values <= {'tag', 'custom'} or (kinds is not None and scope != 'only' and not annotation):
        raise HTTPException(400, '无效的 interest_kinds')
    if tag_id is not None and custom_id is not None:
        raise HTTPException(400, '不能同时指定两种兴趣')
    if ((custom_id is not None or tag_id is not None and kinds is not None) and scope != 'only'
            or tag_id is not None and 'tag' not in values
            or custom_id is not None and 'custom' not in values):
        raise HTTPException(400, '兴趣参数组合无效')
    return values


def require_custom(session, username, condition_id):
    from fastapi import HTTPException
    from services import custom_interests as custom
    row = session.get(custom.Condition, condition_id)
    if row is None or row.owner_username != username or row.status == 'deleted':
        raise HTTPException(404, '自定义兴趣不存在')
    return row


def unified_filter(session, username, interests, kinds, *, tag_id=None, custom_id=None, cache=None):
    from services import custom_interests as custom
    if custom_id is not None:
        require_custom(session, username, custom_id)
    predicates = []
    if 'tag' in kinds and custom_id is None:
        ids = [i for i in interests.followed if tag_id is None or i == tag_id]
        predicates.append(interest_filter_condition(ids))
    if 'custom' in kinds and tag_id is None:
        rows = custom.current_matches(session, username, condition_ids=[custom_id] if custom_id is not None else None,
                                      limit=None, snapshot_cache=cache)
        ids = sorted({article.id for _, _, article in rows})
        predicates.append(ArticleRecord.id.in_(ids))
    return or_(*predicates) if predicates else ArticleRecord.id.in_([])


def safe_evidence(raw, article, snapshot, *, historical=False, expected_fingerprint=None):
    """Never pass model prose, source claims or nested legacy evidence to readers."""
    raw = raw if isinstance(raw, dict) else {}
    fingerprint, body, _ = snapshot
    semantic = raw.get('method') == 'semantic'
    title_only = not semantic and bool(raw.get('title_groups')) and all(raw['title_groups'])
    method = 'semantic' if semantic else ('deterministic_title' if title_only else 'legacy_unknown')
    result = {'reason': '', 'quotes': [], 'redaction': None}
    if historical and (not expected_fingerprint or expected_fingerprint != fingerprint):
        result['redaction'] = 'evidence_unavailable'
        return method, result
    if semantic:
        result['reason'] = '正文支持此兴趣条件'
        for span in raw.get('evidence', []):
            if not isinstance(span, dict) or span.get('kind') != 'text':
                continue
            quote = span.get('quote')
            if not isinstance(quote, str) or not quote or len(quote) > 2000 or quote not in body:
                continue
            item = {'text': quote, 'source_kind': 'podcast_transcript' if article.content_type == 'podcast_episode' else 'article_body',
                    'availability': 'historical' if historical else 'current'}
            # No anchors: a substring match alone does not verify historical offsets.
            result['quotes'].append(item)
            if len(result['quotes']) == 6:
                break
        if not result['quotes']:
            result.update(reason='', redaction='evidence_unavailable')
    elif title_only:
        result['reason'] = '标题包含全部条件组'
        result['quotes'] = [{'text': article.title[:2000], 'source_kind': 'title',
                             'availability': 'historical' if historical else 'current'}]
    else:
        result.update(reason='', redaction='evidence_unavailable')
    return method, result


def _match(ref, name, revision, basis, method, evidence, *, role=None, drilldown=None, contribution=None):
    return {'ref': ref, 'name': name, 'revision': revision, 'basis': basis, 'outcome': 'core',
            'method': method, 'evidence': evidence, 'selection_role': role,
            **({'selection_contribution': contribution} if contribution else {}),
            'drilldown': drilldown or {'available': True, 'reason': None}}


def annotate_current(session, username, items, tags_by_article, interests, *, cache=None):
    from services import custom_interests as custom
    import json
    cache = cache if cache is not None else {}
    ids = [item['id'] for item in items]
    matches = custom.current_matches(session, username, article_ids=ids, limit=None, snapshot_cache=cache) if username else []
    custom_by_id = {}
    for match, condition, article in matches:
        raw = json.loads(match.evidence_json)
        method, evidence = safe_evidence(raw, article, custom.bound_evidence_snapshot(session, article, raw, cache=cache))
        custom_by_id.setdefault(article.id, []).append(_match({'kind': 'custom', 'id': str(condition.id)},
            condition.name, condition.revision, 'current', method, evidence))
    annotate_interest(items, tags_by_article, interests)
    for item in items:
        projected = []
        seen = set()
        for tag in tags_by_article.get(item['id'], []):
            if tag.get('id') not in interests.followed or not public_interest_qualifies(tag.get('relevance')):
                continue
            code = str(tag.get('code') or '')
            if not code or code in seen:
                continue
            seen.add(code)
            projected.append(_match({'kind': 'tag', 'id': code}, interests.followed[tag['id']], None,
                'current', 'public_assignment', {'reason': '文章主题与兴趣标签匹配', 'quotes': [], 'redaction': None}))
        item['interest_matches'] = projected + custom_by_id.get(item['id'], [])
        item['article_match_state'] = 'evaluated'


def pagination_token(session, username, *, query, results, cache=None, validated_matches=None):
    import hashlib
    import json
    from services import custom_interests as custom
    from services.interest_governance_llm import current_verifier_policy
    conditions = [(c.id, c.revision, c.status) for c in custom.list_conditions(session, username)]
    rows = validated_matches if validated_matches is not None else custom.current_matches(
        session, username, limit=None, snapshot_cache=cache, order_by_id=True)
    state = {'owner': username, 'query': query, 'results': results, 'conditions': conditions,
             'public_interests': dict(load_interest_map(session, username).followed),
             'valid_custom_matches': [(m.id, c.id, c.revision, a.id, m.fingerprint) for m, c, a in rows],
             'policy': current_verifier_policy(session),
             'readable_sources': sorted(custom.all_readable_source_ids(session, username))}
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_page_token(supplied, current, *, required=False):
    from fastapi import HTTPException
    if required and not supplied:
        raise HTTPException(400, '继续分页需要 pagination_token')
    if supplied is not None and supplied != current:
        raise HTTPException(409, {'code': 'interest_pagination_stale', 'message': '兴趣或可见内容已变化，请重新加载'})


def strip_private_evidence(value):
    """Response-only recursive removal; never mutate the persisted snapshot."""
    if isinstance(value, list):
        return [strip_private_evidence(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_private_evidence(v) for k, v in value.items()
                if k not in {'interest_evidence', 'custom_interest_evidence', 'interest_matches', 'matched_interest_refs'}}
    return value


def frozen_context(session, username):
    from services import custom_interests as custom
    followed = load_interest_map(session, username).followed
    return {'conditions': {str(c.id): c for c in custom.list_conditions(session, username)},
            'public_codes': {t.code for t in session.exec(select(CmsTagRecord).where(
                CmsTagRecord.status == 'active', CmsTagRecord.id.in_(list(followed)))).all()}}


def frozen_custom_summary(frozen, items, context):
    """An edition receipt, not a whole-archive matching/coverage claim.

    Count only the already ACL-filtered, frozen projections. Never expose the
    condition AST or borrow a current name to describe a historical edition.
    """
    result, seen = [], set()
    for definition in frozen if isinstance(frozen, list) else []:
        if not isinstance(definition, dict):
            continue
        cid, revision = definition.get('custom_interest_id'), definition.get('revision')
        if type(cid) is not int or cid < 1 or type(revision) is not int or revision < 1 or cid in seen:
            continue
        seen.add(cid)
        ref = {'kind': 'custom', 'id': str(cid)}
        matched, selected = set(), set()
        for item in items:
            if not item.get('article_id'):
                continue
            for match in item.get('interest_matches', []):
                if (match.get('ref') == ref and match.get('revision') == revision
                        and match.get('basis') == 'edition_snapshot' and match.get('outcome') == 'core'):
                    matched.add(item['article_id'])
                    if item.get('selection_lane') == 'interest' and match.get('selection_role') == 'selected_by_interest':
                        selected.add(item['article_id'])
        condition = context['conditions'].get(str(cid))
        reason = ('deleted' if condition is None or condition.status == 'deleted' else
                  'paused' if condition.status != 'active' else 'updated' if condition.revision != revision else None)
        name = definition.get('name')
        result.append({'ref': ref, 'revision': revision, 'basis': 'edition_snapshot',
                       'name': name if isinstance(name, str) and name.strip() else '自定义兴趣',
                       'matched_item_count': len(matched), 'selected_item_count': len(selected),
                       'drilldown': {'available': reason is None, 'reason': reason}})
    return result


def _frozen_contribution(features, item, ref):
    raw = features.get('selection_contribution')
    if (not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1
            or raw.get('selected_lane') != item.get('selection_lane')
            or raw.get('admission') not in {'interest_union', 'none'}
            or raw.get('ranking') not in {'interest_lane', 'none'}):
        return None
    refs = raw.get('contributor_refs')
    if (not isinstance(refs, list) or ref not in refs
            or any(not isinstance(r, dict) or set(r) != {'kind', 'id'}
                   or r['kind'] not in {'tag', 'custom'} or not isinstance(r['id'], str) or not r['id'] for r in refs)
            or (raw['admission'] == 'interest_union' and features.get('subscribed') is not False)
            or (raw['ranking'] == 'interest_lane' and item.get('selection_lane') != 'interest')):
        return None
    return {k: raw[k] for k in ('version', 'admission', 'ranking', 'selected_lane', 'contributor_refs')}


def frozen_matches(session, username, item, features, frozen, article, *, cache=None, context=None):
    from services import custom_interests as custom
    cache = cache if cache is not None else {}
    result = []
    context = context if context is not None else frozen_context(session, username)
    role = 'selected_by_interest' if item.get('selection_lane') == 'interest' else 'also_matches'
    tags = {str(t.get('code')): t for t in item.get('snapshot', {}).get('tags', []) if isinstance(t, dict)}
    for code in dict.fromkeys(item.get('matched_interest_codes', [])):
        tag = tags.get(code, {})
        result.append(_match({'kind': 'tag', 'id': code}, tag.get('name_zh') or tag.get('name_en') or '兴趣入选',
            None, 'edition_snapshot', 'public_assignment', {'reason': '本期冻结的兴趣标签匹配', 'quotes': [], 'redaction': None},
            role=role, contribution=_frozen_contribution(features, item, {'kind': 'tag', 'id': code}),
            drilldown={'available': code in context['public_codes'], 'reason': None if code in context['public_codes'] else 'unavailable'}))
    definitions = {str(c.get('custom_interest_id')): c for c in frozen if isinstance(c, dict) and c.get('custom_interest_id') is not None}
    conditions = context['conditions']
    seen = set()
    for ref in features.get('custom_interest_refs', []):
        if not isinstance(ref, dict) or ref.get('kind') != 'custom':
            continue
        cid = str(ref.get('id'))
        if cid in seen:
            continue
        seen.add(cid)
        definition = definitions.get(cid, {})
        raw = features.get('custom_interest_evidence', {}).get(cid, {})
        revision = definition.get('revision')
        condition = conditions.get(cid)
        reason = ('deleted' if condition is None or condition.status == 'deleted' else
                  'paused' if condition.status != 'active' else 'updated' if condition.revision != revision else None)
        method, evidence = safe_evidence(raw, article, custom.bound_evidence_snapshot(session, article, raw, cache=cache), historical=True,
                                         expected_fingerprint=raw.get('fingerprint'))
        result.append(_match({'kind': 'custom', 'id': cid}, definition.get('name') or '兴趣入选', revision,
            'edition_snapshot', method, evidence, role=role,
            contribution=_frozen_contribution(features, item, {'kind': 'custom', 'id': cid}),
            drilldown={'available': reason is None, 'reason': reason}))
    return result
