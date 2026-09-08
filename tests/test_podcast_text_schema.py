"""Podcast text artifact/publication invariants and Archive Sync revisions."""

from __future__ import annotations

import hashlib
import os
import sys

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleRecord,
    ArchiveSyncClockRecord,
    ArchiveSyncEntityStateRecord,
    PodcastArtifactRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
    SQLModel,
    SourceConfigRecord,
)
from storage.fts import fts_include_object  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import make_alembic_config  # noqa: E402


STAMP = "2026-09-05T12:00:00+00:00"


def _sink(tmp_path, name="podcast-text.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _episode(episode_id="episode-1"):
    return ArticleRecord(
        id=episode_id,
        title="一期播客",
        content_type="podcast_episode",
        source_id="podcast_public",
        source_url="https://example.test/episode-1",
        publish_date=STAMP,
        fetched_date=STAMP,
        archive_updated_at=STAMP,
        has_content=True,
        content="show notes",
        extensions_json="{}",
    )


def _source(source_id="podcast_public", *, owner_username="", authority_id=""):
    return SourceConfigRecord(
        source_id=source_id,
        name="公开播客",
        source_type="podcast",
        url="https://example.test/feed.xml",
        category="podcast",
        fetcher_id="generic_podcast_rss",
        owner_username=owner_username,
        collection_authority_id=authority_id,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _artifact(
    *,
    artifact_id="text-1",
    kind="transcript_zh",
    version=1,
    text="你好，世界。",
    authority_id="",
    episode_id="episode-1",
):
    return PodcastTextArtifactRecord(
        id=artifact_id,
        episode_id=episode_id,
        kind=kind,
        version=version,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        inline_text=text,
        language="zh-CN",
        authority_id=authority_id,
        source_artifact_id="publisher-source-1",
        source_content_hash="a" * 64,
        provenance_json='{"pipeline":"podcast-v1"}',
        created_at=STAMP,
    )


def _publication(
    *,
    artifact_id="text-1",
    kind="transcript_zh",
    status="published",
    authority_id="",
    episode_id="episode-1",
):
    return PodcastTextPublicationRecord(
        identity=f"{episode_id}:{kind}",
        episode_id=episode_id,
        kind=kind,
        artifact_id=artifact_id,
        status=status,
        authority_id=authority_id,
        published_at=STAMP,
        unpublished_at=STAMP if status == "unpublished" else None,
        updated_at=STAMP,
    )


def test_local_publication_replace_withdraw_and_delete_share_transaction_revision(tmp_path):
    sink = _sink(tmp_path)
    identity = "episode-1:transcript_zh"
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact())
        session.add(_publication())
        session.flush()
        inserted = session.get(ArchiveSyncEntityStateRecord, ("podcast_texts", identity))
        assert inserted.operation == "upsert"
        inserted_revision = inserted.revision
        assert inserted_revision == session.get(ArchiveSyncClockRecord, 1).revision
        session.commit()

    with Session(sink.engine) as session:
        session.add(_artifact(artifact_id="text-rollback", version=2, text="回滚版本"))
        publication = session.get(PodcastTextPublicationRecord, identity)
        publication.artifact_id = "text-rollback"
        publication.updated_at = "2026-09-05T12:01:00+00:00"
        session.add(publication)
        session.flush()
        assert session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision > inserted_revision
        session.rollback()

    with Session(sink.engine) as session:
        assert session.get(PodcastTextArtifactRecord, "text-rollback") is None
        assert session.get(PodcastTextPublicationRecord, identity).artifact_id == "text-1"
        assert session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision == inserted_revision

        session.add(_artifact(artifact_id="text-2", version=2, text="更新后的文字"))
        publication = session.get(PodcastTextPublicationRecord, identity)
        publication.artifact_id = "text-2"
        publication.updated_at = "2026-09-05T12:02:00+00:00"
        session.add(publication)
        session.commit()
        replaced_revision = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision
        assert replaced_revision > inserted_revision

        publication = session.get(PodcastTextPublicationRecord, identity)
        publication.status = "unpublished"
        publication.unpublished_at = "2026-09-05T12:03:00+00:00"
        publication.updated_at = "2026-09-05T12:03:00+00:00"
        session.add(publication)
        session.commit()
        withdrawn = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert withdrawn.operation == "tombstone"
        assert withdrawn.revision > replaced_revision
        assert withdrawn.revision == session.get(ArchiveSyncClockRecord, 1).revision

        publication = session.get(PodcastTextPublicationRecord, identity)
        publication.status = "published"
        publication.published_at = "2026-09-05T12:04:00+00:00"
        publication.unpublished_at = None
        publication.updated_at = "2026-09-05T12:04:00+00:00"
        session.add(publication)
        session.commit()
        republished_revision = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision

        session.delete(session.get(PodcastTextPublicationRecord, identity))
        session.commit()
        deleted = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert deleted.operation == "tombstone"
        assert deleted.revision > republished_revision


@pytest.mark.parametrize("kind", ["transcript_zh", "normalized_transcript"])
def test_remote_publication_never_creates_local_producer_state(tmp_path, kind):
    sink = _sink(tmp_path, "remote.db")
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact(kind=kind, authority_id="external-authority"))
        session.add(_publication(kind=kind, authority_id="external-authority"))
        session.commit()
        assert session.get(
            ArchiveSyncEntityStateRecord,
            ("podcast_texts", f"episode-1:{kind}"),
        ) is None

        publication = session.get(
            PodcastTextPublicationRecord, f"episode-1:{kind}"
        )
        publication.updated_at = "2026-09-05T13:00:00+00:00"
        session.add(publication)
        session.commit()
        assert session.get(
            ArchiveSyncEntityStateRecord,
            ("podcast_texts", f"episode-1:{kind}"),
        ) is None


def test_local_normalized_transcript_requires_attempt_binding(tmp_path):
    sink = _sink(tmp_path, "local-normalized-binding.db")
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact(kind="normalized_transcript"))
        with pytest.raises(
            IntegrityError, match="ck_podcast_text_artifacts_normalized_bound"
        ):
            session.commit()


def test_text_namespace_rejects_audio_and_preserves_immutable_slot(tmp_path):
    sink = _sink(tmp_path, "constraints.db")
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact(kind="digest_audio_zh"))
        with pytest.raises(IntegrityError, match="ck_podcast_text_artifacts_kind"):
            session.commit()
        session.rollback()

        session.add(_artifact())
        session.add(_publication())
        session.commit()

        artifact = session.get(PodcastTextArtifactRecord, "text-1")
        artifact.inline_text = "试图篡改"
        session.add(artifact)
        with pytest.raises(IntegrityError, match="podcast text artifacts are immutable"):
            session.commit()
        session.rollback()

        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        publication.identity = "moved:transcript_zh"
        publication.episode_id = "moved"
        session.add(publication)
        with pytest.raises(IntegrityError):
            session.commit()


def test_publication_authority_must_match_artifact_and_is_immutable(tmp_path):
    sink = _sink(tmp_path, "authority-constraint.db")
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact(authority_id="producer-a"))
        session.add(_publication(authority_id="producer-b"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(_artifact(authority_id="producer-a"))
        session.add(_publication(authority_id="producer-a"))
        session.commit()
        publication = session.get(
            PodcastTextPublicationRecord, "episode-1:transcript_zh"
        )
        publication.authority_id = "producer-b"
        session.add(publication)
        with pytest.raises(
            IntegrityError, match="podcast text publication identity is immutable"
        ):
            session.commit()


def test_source_scope_and_authority_changes_revise_podcast_text_state(tmp_path):
    sink = _sink(tmp_path, "source-scope.db")
    identity = "episode-1:transcript_zh"
    with Session(sink.engine) as session:
        session.add(_source())
        session.add(_episode())
        session.commit()
        session.add(_artifact())
        session.add(_publication())
        session.commit()
        initial = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision

        source = session.get(SourceConfigRecord, "podcast_public")
        source.owner_username = "private-reader"
        session.add(source)
        session.commit()
        private = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert private.operation == "tombstone"
        assert private.revision > initial
        private_revision = private.revision

        source = session.get(SourceConfigRecord, "podcast_public")
        source.owner_username = ""
        session.add(source)
        session.commit()
        public = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert public.operation == "upsert"
        assert public.revision > private_revision
        public_revision = public.revision

        source = session.get(SourceConfigRecord, "podcast_public")
        source.collection_authority_id = "remote-producer"
        session.add(source)
        session.commit()
        handed_off = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert handed_off.operation == "tombstone"
        assert handed_off.revision > public_revision


def test_late_private_source_insert_tombstones_prior_podcast_text_upsert(tmp_path):
    sink = _sink(tmp_path, "late-private-source.db")
    identity = "episode-1:transcript_zh"
    with Session(sink.engine) as session:
        # Legacy Article rows may precede their SourceConfig row. Missing source
        # metadata is historically treated as public by Archive Sync.
        session.add(_episode())
        session.commit()
        session.add(_artifact())
        session.add(_publication())
        session.commit()
        upsert_revision = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision

        session.add(_source(owner_username="private-reader"))
        session.commit()
        state = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert state.operation == "tombstone"
        assert state.revision > upsert_revision


def test_article_authority_handoff_and_return_revise_podcast_text_state(tmp_path):
    sink = _sink(tmp_path, "article-scope.db")
    identity = "episode-1:transcript_zh"
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact())
        session.add(_publication())
        session.commit()
        initial = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        ).revision

        episode = session.get(ArticleRecord, "episode-1")
        episode.analysis_authority_id = "remote-producer"
        session.add(episode)
        session.commit()
        handed_off = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert handed_off.operation == "tombstone"
        assert handed_off.revision > initial
        handoff_revision = handed_off.revision

        episode = session.get(ArticleRecord, "episode-1")
        episode.analysis_authority_id = ""
        session.add(episode)
        session.commit()
        returned = session.get(
            ArchiveSyncEntityStateRecord, ("podcast_texts", identity)
        )
        assert returned.operation == "upsert"
        assert returned.revision > handoff_revision


def test_episode_delete_cascades_text_rows_and_leaves_sync_tombstone(tmp_path):
    sink = _sink(tmp_path, "cascade.db")
    identity = "episode-1:transcript_zh"
    with Session(sink.engine) as session:
        session.add(_episode())
        session.commit()
        session.add(_artifact())
        session.add(_publication())
        session.commit()
        session.delete(session.get(ArticleRecord, "episode-1"))
        session.commit()
        assert session.get(PodcastTextArtifactRecord, "text-1") is None
        assert session.get(PodcastTextPublicationRecord, identity) is None
        state = session.get(ArchiveSyncEntityStateRecord, ("podcast_texts", identity))
        assert state.operation == "tombstone"


def test_podcast_text_migration_is_single_head_and_matches_create_all(tmp_path):
    cfg = make_alembic_config(f"sqlite:///{tmp_path / 'migration.db'}")
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["9c5e2a7d1b40"]
    command.upgrade(cfg, "head")

    engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "render_as_batch": True,
                    "include_object": fts_include_object,
                },
            )
            assert compare_metadata(context, SQLModel.metadata) == []
    finally:
        engine.dispose()


def test_podcast_text_migration_empty_downgrade_upgrade_cycle(tmp_path):
    cfg = make_alembic_config(f"sqlite:///{tmp_path / 'migration-cycle.db'}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "6c1f8a2d4e90")
    command.upgrade(cfg, "head")


def test_podcast_text_migration_preserves_local_publication(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'migration-local-data.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "6c1f8a2d4e90")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            session.add(_episode())
            session.commit()
            session.exec(text(
                "INSERT INTO podcast_text_artifacts "
                "(id,episode_id,kind,version,content_hash,inline_text,language,"
                "authority_id,source_artifact_id,source_content_hash,rights_version,"
                "provenance_json,created_at) VALUES "
                "('text-1','episode-1','transcript_zh',1,:content_hash,'你好，世界。',"
                "'zh-CN','','publisher-source-1',:source_hash,'rights-v1',"
                "'{\"pipeline\":\"podcast-v1\"}',:stamp)"
            ).bindparams(
                content_hash=hashlib.sha256("你好，世界。".encode()).hexdigest(),
                source_hash="a" * 64,
                stamp=STAMP,
            ))
            session.exec(text(
                "INSERT INTO podcast_text_publications "
                "(identity,episode_id,kind,artifact_id,status,authority_id,"
                "published_at,unpublished_at,updated_at) VALUES "
                "('episode-1:transcript_zh','episode-1','transcript_zh','text-1',"
                "'published','',:stamp,NULL,:stamp)"
            ).bindparams(stamp=STAMP))
            session.commit()
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            publication = session.get(
                PodcastTextPublicationRecord, "episode-1:transcript_zh"
            )
            assert publication is not None
            assert publication.artifact_id == "text-1"
            assert publication.authority_id == ""
    finally:
        engine.dispose()


def test_narration_dependency_migration_withdraws_unprovable_legacy_audio(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'migration-legacy-audio.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "1d7c9a4e2b60")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            session.add(_episode())
            session.commit()
            session.exec(text(
                "INSERT INTO podcast_artifacts "
                "(id, episode_id, kind, content_hash, mime, ext, size_bytes, "
                "duration_seconds, status, provenance, authority_id, created_at, "
                "updated_at, published_at, withdrawn_at) VALUES "
                "('legacy-audio', 'episode-1', 'digest_audio_zh', :content_hash, "
                "'audio/mpeg', '.mp3', 10, 1, 'published', 'legacy', '', :stamp, "
                ":stamp, :stamp, NULL)"
            ).bindparams(content_hash="f" * 64, stamp=STAMP))
            session.commit()
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            artifact = session.get(PodcastArtifactRecord, "legacy-audio")
            assert artifact is not None
            assert artifact.status == "withdrawn"
            assert artifact.narration_artifact_id is None
            assert artifact.narration_content_hash is None
            artifact.status = "ready"
            session.add(artifact)
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_audio_dependency_downgrade_allows_local_source_audio(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'downgrade-local-source-audio.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            session.add(_episode())
            session.commit()
            session.add(PodcastArtifactRecord(
                id="local-source-audio",
                episode_id="episode-1",
                kind="source_audio",
                content_hash="f" * 64,
                mime="audio/mpeg",
                ext=".mp3",
                size_bytes=10,
                status="ready",
                provenance="feed",
                authority_id="dev-local",
                expires_at="2099-01-01T00:00:00.000000+00:00",
                created_at=STAMP,
                updated_at=STAMP,
            ))
            session.commit()
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="仍[含有] source_audio"):
        command.downgrade(cfg, "1d7c9a4e2b60")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "6b8d2f4a9c70"
            )
    finally:
        engine.dispose()


def test_audio_dependency_downgrade_refuses_lossy_digest_binding(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'downgrade-digest-binding.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        script = _artifact(
            artifact_id="narration-for-audio",
            kind="narration_script_zh",
            text="需要保留的口播稿。",
        )
        with Session(engine) as session:
            session.add(_episode())
            session.commit()
            session.add(script)
            session.add(_publication(
                artifact_id=script.id,
                kind="narration_script_zh",
            ))
            session.commit()
            session.add(PodcastArtifactRecord(
                id="bound-digest-audio",
                episode_id="episode-1",
                kind="digest_audio_zh",
                content_hash="f" * 64,
                mime="audio/mpeg",
                ext=".mp3",
                size_bytes=10,
                status="published",
                provenance="legacy_tts_unbound",
                authority_id="dev-local",
                narration_artifact_id=script.id,
                narration_content_hash=script.content_hash,
                created_at=STAMP,
                updated_at=STAMP,
                published_at=STAMP,
            ))
            session.commit()
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Podcast 音频依赖"):
        command.downgrade(cfg, "1d7c9a4e2b60")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "6b8d2f4a9c70"
            )
    finally:
        engine.dispose()

def test_podcast_text_migration_refuses_remote_authority_downgrade(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'migration-fence.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            session.add(_episode())
            session.commit()
            session.add(_artifact(authority_id="external-producer"))
            session.add(_publication(authority_id="external-producer"))
            session.commit()
        with pytest.raises(RuntimeError, match="远端 authority"):
            command.downgrade(cfg, "6c1f8a2d4e90")
    finally:
        engine.dispose()
