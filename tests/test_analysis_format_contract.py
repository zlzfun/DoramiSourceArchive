"""Format-only prompt upgrade must not turn existing assets into paid backfill."""
import datetime as dt
import pytest
from sqlmodel import Session
from llm import article_analysis_prompt as prompts
from models.db import ArticleAnalysisRecord
from services import article_analysis as analysis, analysis_backfill, analysis_observability
from tests.test_content_value import storage, _article, _payload


@pytest.mark.parametrize('raw,warning', [
    ({'feature': 'research'}, 'content_features_not_list'),
    ('research', 'content_features_not_list'), (None, 'content_features_not_list'),
    ([{}], 'content_feature_not_string'), ([True], 'content_feature_not_string'),
    ([1], 'content_feature_not_string'), (['  '], 'content_feature_empty'),
    (['x' * 81], 'content_feature_too_long'),
])
def test_bad_features_stay_partial_without_stringifying(raw, warning):
    result = analysis.validate_analysis_payload(_payload(content_features=raw), active_tags=[])
    assert result.result.content_features == ()
    assert warning in result.warnings


def test_valid_features_are_kept_in_order_with_fixed_bound():
    raw = ['valid', {}, ' second ', 'valid'] + [str(i) for i in range(10)]
    result = analysis.validate_analysis_payload(_payload(content_features=raw), active_tags=[])
    assert result.result.content_features == ('valid', 'second', *map(str, range(8)))
    assert set(result.warnings) == {'content_feature_not_string', 'content_features_too_many'}


@pytest.mark.parametrize('patch', [
    {'name': {}}, {'name': ''}, {'name': 'x'*121}, {'type': []}, {'type': ''},
    {'type': 'x'*61}, {'relevance': True}, {'relevance': '0.8'},
    {'relevance': float('nan')}, {'relevance': float('inf')}, {'relevance': -1}, {'relevance': 2},
])
def test_invalid_entity_is_discarded_with_warning(patch):
    good = {'name': 'Example', 'type': 'system', 'relevance': .8}
    result = analysis.validate_analysis_payload(_payload(entities=[good, {**good, **patch}]), active_tags=[])
    assert list(result.result.entities) == [good]
    assert 'invalid_entity' in result.warnings


@pytest.mark.parametrize('version,scoring,current', [
    ('article-analysis-v7', 'news-value-v2', True),
    ('article-analysis-v8', 'news-value-v2', True),
    ('article-analysis-v6', 'news-value-v2', False),
    ('unknown', 'news-value-v2', False),
    ('article-analysis-v7', 'old-score', False),
])
def test_currentness_consistent_across_queue_scan_estimate_and_metrics(storage, version, scoring, current):
    now = dt.datetime.now(dt.timezone.utc)
    stamp = (now - dt.timedelta(hours=1)).isoformat()
    article = _article('format-test', publish_date=stamp, fetched_date=stamp)
    with Session(storage.engine) as session:
        session.add(article); session.flush()
        row = ArticleAnalysisRecord(article_id=article.id, status='succeeded', tagging_status='partial',
            prompt_version=version, scoring_version=scoring, taxonomy_version=1,
            content_hash=analysis.compute_content_hash(article), quality_score=6,
            summary='Existing summary', analyzed_at=stamp, created_at=stamp, updated_at=stamp)
        session.add(row); session.commit(); session.refresh(row)
        before = row.model_dump()
        assert prompts.analysis_contract_is_reusable(article.content_type, version, scoring) is current
        assert analysis.in_version_refresh_window(article, row, now=now) is (not current)
        estimate = analysis_backfill.estimate_full_analysis_backfill(session, days=7, selection='missing_or_outdated', now=now)
        assert estimate['article_count'] == (0 if current else 1)
        scoped = analysis_backfill.estimate_full_analysis_backfill(session, days=None, selection='missing_or_outdated',
            article_ids=[article.id], publish_after=(now-dt.timedelta(days=1)).isoformat(), publish_before=now.isoformat(), now=now)
        assert scoped['article_count'] == estimate['article_count']
        metrics = analysis_observability.collect_release_metrics(session, now=now)
        assert metrics['article_analysis']['version_stale'] == (0 if current else 1)
        stats = analysis.scan_analysis_backfill(session, now=now)
        if current:
            assert session.get(ArticleAnalysisRecord, article.id).model_dump() == before
            assert analysis.queue_article_analysis(session, article.id, now=now) == 'unchanged'
            assert session.get(ArticleAnalysisRecord, article.id).model_dump() == before
            assert analysis.queue_article_analysis(session, article.id, force=True, now=now) == 'invalidated'
        else:
            assert session.get(ArticleAnalysisRecord, article.id).status == 'pending'


def test_article_only_prompt_change_preserves_podcast_compatibility():
    assert prompts.ARTICLE_ANALYSIS_PROMPT_VERSION == 'article-analysis-v8'
    assert prompts.reusable_analysis_prompt_versions('podcast_episode') == (prompts.PODCAST_ANALYSIS_PROMPT_VERSION,)
    assert not prompts.analysis_contract_is_reusable('podcast_episode', 'article-analysis-v7', 'news-value-v2')
    assert 'content_features 必须是 JSON 字符串数组' in prompts.ARTICLE_ANALYSIS_SYSTEM_PROMPT
    assert 'entities 必须是 JSON 对象数组' in prompts.ARTICLE_ANALYSIS_SYSTEM_PROMPT
