"""taxonomy-bootstrap-v1 manifest, filtering and idempotency tests."""

import datetime as dt
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    SourceConfigRecord,
)
from services.taxonomy import (  # noqa: E402
    activate_taxonomy_version,
    auto_activation_enabled,
    create_taxonomy_version,
    set_auto_activation_enabled,
)
from services.taxonomy_bootstrap import (  # noqa: E402
    BOOTSTRAP_ID,
    BootstrapProposal,
    build_bootstrap_manifest,
    ingest_bootstrap_proposals,
    publish_taxonomy_v1,
    proposal_is_structural,
    validate_manifest,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


AS_OF = dt.datetime(2026, 9, 1, 8, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))


@pytest.fixture
def storage():
    value = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        yield value
    finally:
        value.engine.dispose()


def add_article(
    session: Session,
    article_id: str,
    source_id: str,
    *,
    age_days: int,
    genre: str = "industry_news",
    title: str = "",
    url: str = "",
) -> ArticleRecord:
    stamp = (AS_OF - dt.timedelta(days=age_days)).isoformat()
    record = ArticleRecord(
        id=article_id,
        title=title or f"Unique story {article_id}",
        content_type="article",
        source_id=source_id,
        source_url=url or f"https://{source_id}.example/{article_id}",
        publish_date=stamp,
        fetched_date=stamp,
        has_content=True,
        content=("中文内容" if int(article_id.rsplit("-", 1)[-1]) % 2 else "English content") * 20,
    )
    session.add(record)
    session.flush()
    session.add(
        ArticleAnalysisRecord(
            article_id=record.id,
            status="succeeded",
            tagging_status="pending",
            content_genre=genre,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    return record


def test_manifest_is_frozen_stratified_bounded_and_reproducible(storage):
    with Session(storage.engine) as session:
        for source_id in ("rss_openai_news", "web_anthropic_news"):
            for idx in range(18):
                add_article(
                    session,
                    f"{source_id}-{idx}",
                    source_id,
                    age_days=idx % 10,
                    genre="research_paper" if idx % 3 == 0 else "industry_news",
                )
        # Outside the 30-day window and a synthetic source are never selected.
        add_article(session, "rss_openai_news-99", "rss_openai_news", age_days=31)
        add_article(session, "dorami_daily_brief-1", "dorami_daily_brief", age_days=0)
        session.commit()

        first = build_bootstrap_manifest(
            session,
            as_of=AS_OF,
            source_ids=("rss_openai_news", "web_anthropic_news"),
            per_source_limit=10,
        )
        second = build_bootstrap_manifest(
            session,
            as_of=AS_OF,
            source_ids=("rss_openai_news", "web_anthropic_news"),
            per_source_limit=10,
        )
        assert first == second
        assert first.bootstrap_id == BOOTSTRAP_ID
        assert first.manifest_sha256 == second.manifest_sha256
        assert len(first.article_ids) == 20
        assert sum(item.startswith("rss_openai_news-") for item in first.article_ids) == 10
        assert sum(item.startswith("web_anthropic_news-") for item in first.article_ids) == 10
        assert "rss_openai_news-99" not in first.article_ids
        validate_manifest(first)


def test_manifest_rejects_private_and_structural_sources(storage):
    with Session(storage.engine) as session:
        session.add(
            SourceConfigRecord(
                source_id="private-rss",
                name="Private",
                owner_username="reader",
                created_at=AS_OF.isoformat(),
                updated_at=AS_OF.isoformat(),
            )
        )
        session.commit()
        with pytest.raises(ValueError, match="excluded sources"):
            build_bootstrap_manifest(
                session,
                as_of=AS_OF,
                source_ids=("private-rss",),
                per_source_limit=10,
            )
        with pytest.raises(ValueError, match="excluded sources"):
            build_bootstrap_manifest(
                session,
                as_of=AS_OF,
                source_ids=("generic_rss",),
                per_source_limit=10,
            )
        with pytest.raises(ValueError, match="excluded sources"):
            build_bootstrap_manifest(
                session,
                as_of=AS_OF,
                source_ids=("user_rss_unregistered",),
                per_source_limit=10,
            )


def test_bootstrap_filters_structural_labels_forces_auto_activation_off_and_is_idempotent(storage):
    with Session(storage.engine) as session:
        for idx in range(10):
            add_article(session, f"rss_openai_news-{idx}", "rss_openai_news", age_days=idx)
        session.commit()
        manifest = build_bootstrap_manifest(
            session,
            as_of=AS_OF,
            source_ids=("rss_openai_news",),
            per_source_limit=10,
        )
        article_id = manifest.article_ids[0]
        proposals = [
            BootstrapProposal(
                article_id=article_id,
                label="Agent Runtime",
                proposed_kind="topic",
                confidence=0.96,
            ),
            BootstrapProposal(
                article_id=article_id,
                label="official",
                proposed_kind="topic",
                confidence=0.99,
            ),
            BootstrapProposal(
                article_id="outside-manifest",
                label="Outside",
                proposed_kind="topic",
                confidence=0.99,
            ),
        ]
        set_auto_activation_enabled(session, True)
        first = ingest_bootstrap_proposals(
            session,
            manifest=manifest,
            proposals=proposals,
            now=AS_OF,
        )
        second = ingest_bootstrap_proposals(
            session,
            manifest=manifest,
            proposals=proposals,
            now=AS_OF,
        )
        assert first == {"accepted": 1, "structural_filtered": 1, "outside_manifest": 1, "known_or_private": 0}
        assert second == first
        assert auto_activation_enabled(session) is False
        assert proposal_is_structural("tier0-primary")
        assert proposal_is_structural("网页")
        assert not proposal_is_structural("Agent Runtime")
        assert len(session.exec(select(CmsTagCandidateRecord)).all()) == 1
        assert len(session.exec(select(CmsTagCandidateEvidenceRecord)).all()) == 1
        candidate = session.exec(select(CmsTagCandidateRecord)).one()
        assert candidate.support_article_count_7d == 1


def test_reviewed_taxonomy_v1_publish_queues_one_closed_set_seven_day_retag(storage):
    with Session(storage.engine) as session:
        first = publish_taxonomy_v1(session, actor_id="product-owner", now=AS_OF)
        second = publish_taxonomy_v1(session, actor_id="product-owner", now=AS_OF)
        assert first["taxonomy_version"] == 1
        assert first["retag_job_id"] == second["retag_job_id"]
        assert first["retag_status"] == "queued"
        assert first["coverage_before_retag"]["analyzed_articles"] == 0

        version_two = create_taxonomy_version(session, change_summary="future taxonomy", now=AS_OF)
        activate_taxonomy_version(session, version_two.version, actor_id="product-owner", now=AS_OF)
        with pytest.raises(ValueError, match="while v2 is active"):
            publish_taxonomy_v1(session, actor_id="product-owner", now=AS_OF)
