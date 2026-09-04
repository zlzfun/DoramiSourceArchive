"""WP-0 schema and shared-contract guards for analysis and personal briefs."""

import os
import sys

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import (  # noqa: E402
    ANALYSIS_LEASE_SECONDS,
    PERSONAL_DIGEST_INTEREST_MAX_RATIO,
    AnalysisStatus,
    ArticleAnalysisResultDTO,
    ContentGenre,
    TagKind,
)
from models.db import (  # noqa: E402
    ArticleAnalysisAttemptRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagAliasRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    DuplicateGroupMemberRecord,
    DuplicateGroupRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    TagRetagJobItemRecord,
    TagRetagJobRecord,
    TaxonomyVersionRecord,
    UserInterestTagRecord,
    UserRecord,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW = "2026-09-01T08:30:00+08:00"


def _article(article_id: str = "a1") -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=f"Article {article_id}",
        content_type="article",
        source_id="source-1",
        source_url=f"https://example.com/{article_id}",
        publish_date=NOW,
        fetched_date=NOW,
        has_content=True,
        content="body",
    )


def _tag(code: str, *, kind: str = "topic") -> CmsTagRecord:
    return CmsTagRecord(
        code=code,
        kind=kind,
        name_en=code,
        normalized_name=code,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("db_url_factory", "expected_journal"),
    [
        (lambda _tmp: "sqlite:///:memory:", "memory"),
        (lambda tmp: f"sqlite:///{tmp / 'pragma.db'}", "wal"),
    ],
)
def test_database_storage_enables_sqlite_pragmas(tmp_path, db_url_factory, expected_journal):
    storage = DatabaseStorage(db_url=db_url_factory(tmp_path))
    try:
        with storage.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == expected_journal
        ai_column = next(
            column
            for column in inspect(storage.engine).get_columns("source_configs")
            if column["name"] == "ai_analysis_enabled"
        )
        assert ai_column["nullable"] is False
        assert ai_column["default"] in {"1", "(1)"}
    finally:
        storage.engine.dispose()


def test_contracts_keep_genre_separate_and_validate_score():
    assert ANALYSIS_LEASE_SECONDS == 300
    assert PERSONAL_DIGEST_INTEREST_MAX_RATIO == 0.5
    assert {item.value for item in ContentGenre}.isdisjoint({item.value for item in TagKind})
    assert AnalysisStatus.SUCCEEDED.value == "succeeded"

    result = ArticleAnalysisResultDTO(
        quality_score=8.6,
        score_reason="Original and actionable.",
        summary="A detailed summary.",
        content_genre=ContentGenre.TUTORIAL,
    )
    assert result.quality_score == 8.6
    assert result.content_genre == "tutorial"

    with pytest.raises(ValidationError):
        ArticleAnalysisResultDTO(
            quality_score=10.1,
            score_reason="Out of range.",
            summary="Summary.",
            content_genre=ContentGenre.OPINION,
        )


def test_required_idempotency_constraints_and_scan_indexes_exist():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        schema = inspect(storage.engine)
        assert set(schema.get_pk_constraint("cms_tag_candidate_evidence")["constrained_columns"]) == {
            "candidate_id",
            "article_id",
        }
        assert set(schema.get_pk_constraint("user_interest_tags")["constrained_columns"]) == {
            "owner_username",
            "tag_id",
        }

        edition_uniques = {
            tuple(item["column_names"])
            for item in schema.get_unique_constraints("personal_digest_editions")
        }
        assert ("owner_username", "report_date", "revision") in edition_uniques
        item_uniques = {
            tuple(item["column_names"])
            for item in schema.get_unique_constraints("personal_digest_items")
        }
        assert ("edition_id", "position") in item_uniques

        analysis_indexes = {item["name"] for item in schema.get_indexes("article_analyses")}
        assert "ix_article_analyses_scan" in analysis_indexes
        assignment_indexes = {
            item["name"]: item for item in schema.get_indexes("article_tag_assignments")
        }
        assert assignment_indexes["uq_article_tag_assignments_primary_facet"]["unique"] == 1
        candidate_indexes = {item["name"] for item in schema.get_indexes("cms_tag_candidates")}
        assert "ix_cms_tag_candidates_status_last_seen" in candidate_indexes
        backfill_item_indexes = {
            item["name"] for item in schema.get_indexes("tag_retag_job_items")
        }
        assert "ix_tag_retag_job_items_job_status_id" in backfill_item_indexes
        backfill_item_uniques = {
            tuple(item["column_names"])
            for item in schema.get_unique_constraints("tag_retag_job_items")
        }
        assert ("job_id", "article_id_snapshot") in backfill_item_uniques
    finally:
        storage.engine.dispose()


def test_full_analysis_item_survives_article_delete_as_audit_snapshot():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            article = _article("backfill-delete")
            session.add(article)
            session.add(TaxonomyVersionRecord(version=1, status="active", created_at=NOW))
            session.flush()
            job = TagRetagJobRecord(
                taxonomy_version=1,
                operation="full_analysis",
                status="queued",
                scope_json="{}",
                affected_count=1,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(job)
            session.flush()
            session.add(
                TagRetagJobItemRecord(
                    job_id=job.id,
                    article_id=article.id,
                    article_id_snapshot=article.id,
                    status="pending",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()

            session.delete(article)
            session.commit()
            item = session.exec(select(TagRetagJobItemRecord)).one()
            assert item.article_id is None
            assert item.article_id_snapshot == "backfill-delete"
            assert session.get(TagRetagJobRecord, job.id) is not None
        with storage.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        storage.engine.dispose()


def test_article_delete_cascades_analysis_but_preserves_digest_snapshot():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            user = UserRecord(
                username="reader",
                password_hash="hash",
                role="user",
                created_at=NOW,
                updated_at=NOW,
            )
            article = _article()
            tag = _tag("agents")
            session.add_all([user, article, tag])
            session.flush()

            analysis = ArticleAnalysisRecord(
                article_id=article.id,
                status="succeeded",
                tagging_status="succeeded",
                quality_score=8.6,
                content_genre="tutorial",
                primary_tag_id=tag.id,
                created_at=NOW,
                updated_at=NOW,
            )
            attempt = ArticleAnalysisAttemptRecord(
                article_id=article.id,
                attempt_no=1,
                status="succeeded",
                started_at=NOW,
                created_at=NOW,
            )
            assignment = ArticleTagAssignmentRecord(
                article_id=article.id,
                tag_id=tag.id,
                tag_kind="topic",
                is_primary=True,
                relevance=0.95,
                created_at=NOW,
                updated_at=NOW,
            )
            candidate = CmsTagCandidateRecord(
                label="Agentic AI",
                normalized_label="agentic ai",
                proposed_kind="topic",
                first_seen_at=NOW,
                last_seen_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
            group = DuplicateGroupRecord(
                fingerprint="same-story",
                representative_article_id=article.id,
                created_at=NOW,
                updated_at=NOW,
            )
            edition = PersonalDigestEditionRecord(
                owner_username=user.username,
                report_date="2026-09-01",
                revision=1,
                status="ready",
                check_after=NOW,
                cutoff_at=NOW,
                generated_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add_all([analysis, attempt, assignment, candidate, group, edition])
            session.flush()
            session.add_all(
                [
                    CmsTagCandidateEvidenceRecord(
                        candidate_id=candidate.id,
                        article_id=article.id,
                        source_id=article.source_id,
                        confidence=0.91,
                        raw_label="Agentic AI",
                        created_at=NOW,
                    ),
                    DuplicateGroupMemberRecord(
                        group_id=group.id,
                        article_id=article.id,
                        is_representative=True,
                        created_at=NOW,
                    ),
                    PersonalDigestItemRecord(
                        edition_id=edition.id,
                        article_id=article.id,
                        position=0,
                        selection_lane="quality",
                        quality_score_snapshot=8.6,
                        snapshot_json='{"title":"Article a1"}',
                        created_at=NOW,
                    ),
                    UserInterestTagRecord(
                        owner_username=user.username,
                        tag_id=tag.id,
                        stance="follow",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                ]
            )
            session.commit()

            session.delete(session.get(ArticleRecord, article.id))
            session.commit()

            assert session.get(ArticleAnalysisRecord, article.id) is None
            assert session.exec(
                select(ArticleAnalysisAttemptRecord).where(
                    ArticleAnalysisAttemptRecord.article_id == article.id
                )
            ).first() is None
            assert session.exec(
                select(ArticleTagAssignmentRecord).where(
                    ArticleTagAssignmentRecord.article_id == article.id
                )
            ).first() is None
            assert session.exec(select(CmsTagCandidateEvidenceRecord)).first() is None
            assert session.exec(select(DuplicateGroupMemberRecord)).first() is None

            group_after = session.get(DuplicateGroupRecord, group.id)
            assert group_after is not None and group_after.representative_article_id is None
            item_after = session.exec(select(PersonalDigestItemRecord)).one()
            assert item_after.article_id is None
            assert item_after.snapshot_json == '{"title":"Article a1"}'

            session.delete(session.get(UserRecord, user.username))
            session.commit()
            assert session.exec(select(UserInterestTagRecord)).first() is None
            assert session.exec(select(PersonalDigestEditionRecord)).first() is None
            assert session.exec(select(PersonalDigestItemRecord)).first() is None

        with storage.engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        storage.engine.dispose()


def test_assignment_uniqueness_and_one_primary_per_facet():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            article = _article()
            topic_a = _tag("agents")
            topic_b = _tag("rag")
            entity = _tag("openai", kind="entity")
            session.add_all([article, topic_a, topic_b, entity])
            session.commit()

            session.add(
                ArticleTagAssignmentRecord(
                    article_id=article.id,
                    tag_id=topic_a.id,
                    tag_kind="topic",
                    is_primary=True,
                    relevance=0.9,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()

            session.add(
                ArticleTagAssignmentRecord(
                    article_id=article.id,
                    tag_id=topic_b.id,
                    tag_kind="topic",
                    is_primary=True,
                    relevance=0.8,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            session.add(
                ArticleTagAssignmentRecord(
                    article_id=article.id,
                    tag_id=entity.id,
                    tag_kind="entity",
                    is_primary=True,
                    relevance=0.8,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()  # a primary in another facet is allowed

            session.add(
                ArticleTagAssignmentRecord(
                    article_id=article.id,
                    tag_id=entity.id,
                    tag_kind="entity",
                    is_primary=False,
                    relevance=0.5,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        storage.engine.dispose()


def test_alias_is_unique_within_facet_but_not_across_facets():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with Session(storage.engine) as session:
            topic = _tag("apple-topic")
            entity = _tag("apple-entity", kind="entity")
            session.add_all([topic, entity])
            session.commit()
            session.add_all(
                [
                    CmsTagAliasRecord(
                        tag_id=topic.id,
                        kind="topic",
                        alias="Apple",
                        normalized_alias="apple",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                    CmsTagAliasRecord(
                        tag_id=entity.id,
                        kind="entity",
                        alias="Apple",
                        normalized_alias="apple",
                        created_at=NOW,
                        updated_at=NOW,
                    ),
                ]
            )
            session.commit()

            session.add(
                CmsTagAliasRecord(
                    tag_id=topic.id,
                    kind="topic",
                    alias="Ａｐｐｌｅ",
                    normalized_alias="apple",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        storage.engine.dispose()


def test_database_check_constraints_reject_invalid_quality_score():
    storage = DatabaseStorage(db_url="sqlite:///:memory:")
    try:
        with storage.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO articles "
                    "(id,title,content_type,source_id,source_url,publish_date,fetched_date,"
                    "has_content,run_scope,read_count) "
                    "VALUES ('bad','Bad','article','s','u',:now,:now,1,'ad_hoc',0)"
                ),
                {"now": NOW},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO article_analyses "
                        "(article_id,status,tagging_status,quality_score,dimension_scores_json,"
                        "score_reason,summary,content_features_json,entities_json,"
                        "content_hash,model_name,prompt_version,scoring_version,taxonomy_version,"
                        "attempt_count,created_at,updated_at) "
                        "VALUES ('bad','succeeded','succeeded',11,'{}','','','[]','[]','',"
                        "'','','',0,0,:now,:now)"
                    ),
                    {"now": NOW},
                )
    finally:
        storage.engine.dispose()
