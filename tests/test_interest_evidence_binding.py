"""Regression for title enrichment invalidating real issue-298 interest evidence."""
import json

import pytest
from sqlmodel import Session

from tests.test_custom_interests import store, save, add_article, NOW
from tests.test_interest_projection import seed_match
from services import custom_interests as custom, reader_interests as reader
from services import personal_digest as digest, digest_selection as selection
from models.db import ARTICLE_ARCHIVE_FIELDS
from models.analysis_contracts import DigestArticleCandidateDTO, UserInterestDTO, InterestRef


def test_binding_schema_covers_archive():
    assert custom.EVIDENCE_ARTICLE_FIELDS == ARTICLE_ARCHIVE_FIELDS | {'id', 'analysis_authority_id'}


@pytest.mark.parametrize('patch', [
    {'translation_zh_title': '中文标题'}, {'translation_zh_title_fp': 'cached-fp'},
    {'translation_zh_title': '中文标题', 'translation_zh_title_fp': 'cached-fp'},
])
def test_only_title_cache_and_watermark_do_not_invalidate_new_binding(store, patch):
    with Session(store.engine) as s:
        a = add_article(s)
        a.extensions_json = json.dumps({'real_source_version': 1})
        old = custom.evidence_snapshot(s, a)
        new = custom.evidence_snapshot(s, a, binding_version=2)
        a.extensions_json = json.dumps({'real_source_version': 1, **patch})
        a.archive_updated_at = 'pure-title-cache-write'
        assert custom.evidence_snapshot(s, a)[0] != old[0]
        assert custom.evidence_snapshot(s, a, binding_version=2) == new
        assert custom.evidence_snapshot(s, a, assets=custom.evidence_assets(s, [a]), binding_version=2) == new
        a.extensions_json = json.dumps({'real_source_version': 2, **patch})
        assert custom.evidence_snapshot(s, a, binding_version=2)[0] != new[0]


@pytest.mark.parametrize('field', sorted(custom.EVIDENCE_ARTICLE_FIELDS - {'id', 'extensions_json'}))
def test_all_real_archive_fields_and_authority_still_invalidate(store, field):
    with Session(store.engine) as s:
        a = add_article(s)
        before = custom.evidence_snapshot(s, a, binding_version=2)[0]
        # Detached object, avoiding unrelated persistence validation.
        changed = a.model_copy(update={field: not a.has_content if field == 'has_content' else 'changed'})
        assert custom.evidence_snapshot(s, changed, binding_version=2)[0] != before


@pytest.mark.parametrize('value', [None, '', 'null', '[]', '{broken', '{"unknown":1}', '{"x":NaN}'])
def test_nonobject_unknown_and_malformed_extensions_remain_bound(store, value):
    with Session(store.engine) as s:
        a = add_article(s)
        s.refresh(a)
        base = custom.evidence_snapshot(s, a.model_copy(update={'extensions_json': '{}'}), binding_version=2)[0]
        assert custom.evidence_snapshot(s, a.model_copy(update={'extensions_json': value}), binding_version=2)[0] != base


@pytest.mark.parametrize('bad', [None, True, False, '2', 2.0, 0, 3])
def test_explicit_bad_version_denied_and_legacy_not_upgraded(store, bad):
    with Session(store.engine) as s:
        a = add_article(s)
        c = save(s)
        m = seed_match(s, c, a)
        assert custom.current_matches(s, 'alice')
        m.evidence_json = json.dumps({'evidence_binding_version': bad})
        s.add(m); s.commit()
        assert custom.current_matches(s, 'alice') == []


def test_mixed_versions_current_and_history_with_pure_get(store):
    with Session(store.engine) as s:
        a = add_article(s); old = save(s, 'old'); new = save(s, 'new')
        legacy = seed_match(s, old, a)
        raw = {**json.loads(legacy.evidence_json), 'evidence_binding_version': 2}
        fp = custom.evidence_snapshot(s, a, binding_version=2)[0]
        custom._put_match(s, new, a, fp, 'core', raw, NOW.isoformat()); s.commit()
        s.refresh(legacy)
        before = legacy.model_dump()
        a.extensions_json = json.dumps({'translation_zh_title': '缓存标题'})
        a.archive_updated_at = 'cache-write'; s.add(a); s.commit()
        cache = {}
        assert [c.id for _, c, _ in custom.current_matches(s, 'alice', snapshot_cache=cache)] == [new.id]
        assert set(cache) == {(a.id, 1), (a.id, 2)}
        for version, fingerprint, expected in [(1, legacy.fingerprint, 'evidence_unavailable'), (2, fp, None)]:
            evidence = {**raw, 'evidence_binding_version': version}
            _, projected = reader.safe_evidence(evidence, a, custom.bound_evidence_snapshot(s, a, evidence),
                                               historical=True, expected_fingerprint=fingerprint)
            assert projected['redaction'] == expected
        assert legacy.model_dump() == before


def candidate(*, subscribed=False, aid='actual'):
    return DigestArticleCandidateDTO(article_id=aid, source_id='podcast_source', title='Actual workflow',
        source_url='https://example.test/a', publish_date=NOW.isoformat(), fetched_date=NOW.isoformat(),
        quality_score=7.0, score_reason='source', content_genre='industry_news',
        content_shape='podcast', content_kind='podcast', content_value=8.0,
        subscribed=subscribed, tag_codes=('topic.ai',),
        custom_interest_refs=(InterestRef(kind='custom', id='7'), InterestRef(kind='custom', id='15')))


@pytest.mark.parametrize('subscribed', [False, True])
def test_actual_value_selector_freezes_all_admission_refs_once(subscribed, monkeypatch):
    inputs = [candidate(subscribed=subscribed)]
    interests = [UserInterestDTO(tag_code='topic.ai'), UserInterestDTO(custom_interest_id=7), UserInterestDTO(custom_interest_id=15)]
    calls = []; original = selection.value_lane_eligible
    def observed(*args, **kwargs):
        result = original(*args, **kwargs); calls.append(result); return result
    monkeypatch.setattr(selection, 'value_lane_eligible', observed)
    selected = selection.select_value_lane(inputs, interests, lane='listen', policy=selection.ValueLanePolicy(min_value=7, max_items=2))
    assert len(calls) == 1 and len(selected) == 1
    facts = selected[0].selection_contribution
    assert facts['admission'] == ('none' if subscribed else 'interest_union')
    assert facts['ranking'] == 'none'
    assert len(facts['contributor_refs']) == 3
    features = {'subscribed': subscribed, 'selection_contribution': facts}
    assert reader._frozen_contribution(features, {'selection_lane': 'listen'}, {'kind': 'custom', 'id': '7'}) == facts
    assert reader._frozen_contribution({}, {'selection_lane': 'listen'}, {'kind': 'custom', 'id': '7'}) is None
    if not subscribed:
        assert reader._frozen_contribution({'selection_contribution': facts}, {'selection_lane': 'listen'},
                                           {'kind': 'custom', 'id': '7'}) is None
