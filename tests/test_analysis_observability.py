import datetime as dt
import json

from sqlmodel import Session

from models.db import (
    AppSettingRecord,
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    UserRecord,
)
from services.analysis_observability import collect_release_metrics
from storage.impl.db_storage import DatabaseStorage
from api.routers.analysis_ops import AnalysisFeatureFlagsPatch, update_config


def test_release_metrics_are_aggregate_and_cover_gate_indicators(tmp_path):
    storage = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'metrics.db'}")
    now = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
    stamp = now.isoformat()
    with Session(storage.engine) as session:
        session.add(
            UserRecord(
                username="private-user",
                password_hash="hash",
                role="user",
                is_active=True,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.add(AppSettingRecord(key="article_analysis_enabled", value="true"))
        article = ArticleRecord(
            id="secret-article-id",
            title="SECRET PRIVATE TITLE",
            content_type="rss_article",
            source_id="user_rss_secret",
            source_url="https://private.example/secret",
            publish_date=stamp,
            fetched_date=stamp,
            has_content=True,
            content="SECRET BODY",
        )
        session.add(article)
        tag = CmsTagRecord(
            code="topic-ai",
            kind="topic",
            name_en="AI",
            normalized_name="ai",
            status="active",
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(tag)
        session.flush()
        session.add(
            ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=8.0,
                primary_tag_id=tag.id,
                content_hash="hash",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        session.add(
            ArticleAnalysisAttemptRecord(
                article_id=article.id,
                attempt_no=1,
                operation="full_analysis",
                status="succeeded",
                started_at=stamp,
                ended_at=stamp,
                created_at=stamp,
            )
        )
        session.add(
            ArticleTagAssignmentRecord(
                article_id=article.id,
                tag_id=tag.id,
                tag_kind="topic",
                is_primary=True,
                relevance=0.9,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        edition = PersonalDigestEditionRecord(
            owner_username="private-user",
            report_date="2026-09-01",
            revision=1,
            status="ready",
            check_after=stamp,
            cutoff_at=stamp,
            generated_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(edition)
        session.flush()
        session.add(
            PersonalDigestItemRecord(
                edition_id=edition.id,
                article_id=article.id,
                position=0,
                section="模型发布",
                selection_lane="interest",
                quality_score_snapshot=8.0,
                coverage_adjustments_json='["soft_coverage:topic"]',
                snapshot_json='{"title":"SECRET PRIVATE TITLE"}',
                created_at=stamp,
            )
        )
        session.commit()

        metrics = collect_release_metrics(session, days=7, now=now)

    assert metrics["article_analysis"]["status_counts"] == {"succeeded": 1}
    assert metrics["article_analysis"]["score_p50"] == 8.0
    assert metrics["taxonomy"]["tagged_article_rate"] == 1.0
    assert metrics["taxonomy"]["primary_missing_rate"] == 0.0
    assert metrics["personal_digest"]["interest_ratio"] == 1.0
    assert metrics["personal_digest"]["coverage_adjusted_item_rate"] == 1.0
    encoded = json.dumps(metrics, ensure_ascii=False)
    assert "private-user" not in encoded
    assert "secret-article-id" not in encoded
    assert "SECRET PRIVATE TITLE" not in encoded
    assert "private.example" not in encoded


def test_feature_flag_config_defaults_off_and_updates_explicitly(tmp_path):
    storage = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'flags.db'}")
    with Session(storage.engine) as session:
        response = update_config(
            AnalysisFeatureFlagsPatch(
                article_analysis_enabled=True,
                personal_digest_enabled=True,
            ),
            session,
        )
        flags = response["feature_flags"]
        assert flags["article_analysis_enabled"] is True
        assert flags["personal_digest_enabled"] is True
        assert flags["taxonomy_candidate_enabled"] is False
        assert flags["taxonomy_auto_activation_enabled"] is False
        assert flags["public_digest_analysis_adapter_enabled"] is False
