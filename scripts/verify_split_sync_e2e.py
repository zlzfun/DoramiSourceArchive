#!/usr/bin/env python3
"""Run Archive Sync v2 against two real, isolated Dorami HTTP instances.

This is an operator smoke test rather than a unit test.  It creates temporary
producer/consumer databases and media roots, starts two uvicorn processes with
``role=all``, triggers the consumer's public remote-sync API, and verifies the
six v2 streams plus the reverse custom-RSS Candidate channel. It also starts a
real ``main.py`` authority twice to verify deployment-time Taxonomy reconciliation
is idempotent and does not auto-publish.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
from sqlmodel import Session, select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from models.db import (  # noqa: E402
    AppSettingRecord,
    ArchiveSyncEntityStateRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagCandidateRecord,
    CmsTagRecord,
    JobRecord,
    MediaAssetRecord,
    RemoteCandidateEvidenceRecord,
    SourceConfigRecord,
    SourceStateRecord,
    TaxonomyVersionRecord,
)
from services import archive_sync_v2  # noqa: E402
from services.article_analysis import compute_content_hash, queue_article_analysis  # noqa: E402
from services.media_store import MediaStore, url_hash_of  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import ensure_migrated  # noqa: E402


STAMP = (dt.datetime.now() - dt.timedelta(minutes=5)).isoformat(timespec="microseconds")
ANALYSIS_STAMP = (
    dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
).isoformat(timespec="microseconds")
EXPECTED_STREAMS = (
    "sources",
    "taxonomy",
    "articles",
    "analyses",
    "media",
    "source_states",
)
PUBLIC_SOURCE = "rss_split_e2e"
PUBLIC_ARTICLE = "split-e2e-public"
HISTORICAL_ARTICLE = "split-e2e-historical"
CUSTOM_SOURCE = "user_rss_split_e2e"
PRIVATE_SOURCE = "user_rss_split_secret"
BODY_IMAGE_URL = "https://images.example.test/body.png"
SOCIAL_IMAGE_URL = "https://images.example.test/social.png"
PODCAST_COVER_URL = "https://images.example.test/podcast-cover.png"
AUDIO_URL = "https://audio.example.test/not-mirrored.mp3"
STALE_MEDIA_URL = "https://images.example.test/retained-for-local-gc.png"
MEDIA_BODIES = {
    BODY_IMAGE_URL: b"\x89PNG\r\n\x1a\nbody-image",
    SOCIAL_IMAGE_URL: b"\x89PNG\r\n\x1a\nsocial-image",
    PODCAST_COVER_URL: b"\x89PNG\r\n\x1a\npodcast-cover",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(
    path: Path,
    db_path: Path,
    media_root: Path,
    port: int,
    *,
    taxonomy_mode: str = "manual",
) -> None:
    path.write_text(
        "\n".join(
            (
                "[server]",
                "host = 127.0.0.1",
                f"port = {port}",
                "reload = false",
                "",
                "[runtime]",
                "role = all",
                "",
                "[taxonomy]",
                f"deployment = {taxonomy_mode}",
                "",
                "[network]",
                "disable_ca_bundle = false",
                "hf_endpoint =",
                "",
                "[proxy]",
                "http_proxy =",
                "https_proxy =",
                "no_proxy = 127.0.0.1,localhost",
                "",
                "[auth]",
                f"cookie_name = dorami_split_e2e_{port}",
                f"secret = split-e2e-only-secret-{port}",
                "cookie_secure = false",
                "",
                "[storage]",
                f"database_url = sqlite:///{db_path}",
                "",
                "[cors]",
                "allow_origins = *",
                "allow_credentials = true",
                "allow_methods = *",
                "allow_headers = *",
                "",
                "[media]",
                "enabled = true",
                f"media_dir = {media_root}",
                "max_file_mb = 20",
                "timeout_seconds = 5",
                "prefetch_concurrency = 1",
                "",
                "[llm]",
                "base_url =",
                "api_key =",
                "model =",
                "",
            )
        ),
        encoding="utf-8",
    )


def _source(source_id: str, *, owner: str = "", params: str = "{}") -> SourceConfigRecord:
    return SourceConfigRecord(
        source_id=source_id,
        name=source_id,
        source_type="rss",
        url=f"https://feeds.example.test/{source_id}.xml",
        category="user" if owner else "official",
        fetcher_id="generic_rss" if not owner else "",
        owner_username=owner,
        ai_analysis_enabled="credentialed_private" not in params,
        is_active=True,
        params_json=params,
        created_at=STAMP,
        updated_at=STAMP,
    )


def _article(article_id: str, source_id: str, content: str, *, extensions: dict | None = None) -> ArticleRecord:
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type="rss_article",
        source_id=source_id,
        source_url=f"https://content.example.test/{article_id}",
        publish_date=STAMP,
        fetched_date=STAMP,
        archive_updated_at=STAMP,
        has_content=True,
        content=content,
        extensions_json=json.dumps(extensions or {}, ensure_ascii=False),
    )


def _prepare_database(path: Path) -> DatabaseStorage:
    url = f"sqlite:///{path}"
    ensure_migrated(url)
    return DatabaseStorage(db_url=url)


def _seed_producer(db_path: Path, media_root: Path) -> None:
    storage = _prepare_database(db_path)
    media_rows: list[MediaAssetRecord] = []
    for url, body in {
        **MEDIA_BODIES,
        AUDIO_URL: b"ID3-not-an-image",
    }.items():
        is_audio = url == AUDIO_URL
        row = MediaAssetRecord(
            url_hash=url_hash_of(url),
            url=url,
            status="cached",
            content_hash=hashlib.sha256(body).hexdigest(),
            mime="audio/mpeg" if is_audio else "image/png",
            ext=".mp3" if is_audio else ".png",
            size_bytes=len(body),
            created_at=STAMP,
            fetched_at=STAMP,
            updated_at=STAMP,
        )
        path = MediaStore(storage.engine, media_root).file_path_for(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        media_rows.append(row)
    with Session(storage.engine) as session:
        session.add(_source(PUBLIC_SOURCE))
        article = _article(
            PUBLIC_ARTICLE,
            PUBLIC_SOURCE,
            f"Body A\n\n![body]({BODY_IMAGE_URL})",
        )
        session.add(article)
        session.add(_article(HISTORICAL_ARTICLE, PUBLIC_SOURCE, "Historical body without analysis"))
        session.add(_article(
            "split-e2e-social",
            PUBLIC_SOURCE,
            "Social post body",
            extensions={"media_urls": [SOCIAL_IMAGE_URL]},
        ))
        podcast = _article(
            "split-e2e-podcast",
            PUBLIC_SOURCE,
            "Podcast description",
            extensions={"image_url": PODCAST_COVER_URL, "audio_url": AUDIO_URL},
        )
        podcast.content_type = "podcast_episode"
        session.add(podcast)
        tag = CmsTagRecord(
            code="topic.split-e2e",
            kind="topic",
            name_zh="双实例测试",
            normalized_name="双实例测试",
            status="active",
            user_selectable=True,
            taxonomy_version=1,
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        )
        session.add(tag)
        session.add(TaxonomyVersionRecord(
            version=1,
            status="active",
            change_summary="split e2e",
            activated_by="smoke-test",
            activated_at=ANALYSIS_STAMP,
            created_at=ANALYSIS_STAMP,
        ))
        session.add(AppSettingRecord(key="taxonomy:sync_revision", value="1"))
        session.add_all(media_rows)
        session.add(SourceStateRecord(
            source_id=PUBLIC_SOURCE,
            fetcher_id="generic_rss",
            content_type="rss_article",
            status="healthy",
            last_completed_at=STAMP,
            last_success_at=STAMP,
            total_runs=1,
            success_runs=1,
            latest_fetched_count=2,
            latest_saved_count=2,
            updated_at=STAMP,
        ))
        session.flush()
        analysis = ArticleAnalysisRecord(
            article_id=article.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=8.6,
            summary="The complete producer result.",
            content_hash=compute_content_hash(article),
            model_name="split-e2e-model",
            prompt_version="article-analysis-v3",
            scoring_version="content-value-v1",
            taxonomy_version=1,
            analyzed_at=ANALYSIS_STAMP,
            tagged_at=ANALYSIS_STAMP,
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        )
        session.add(analysis)
        session.flush()
        session.add(ArticleTagAssignmentRecord(
            article_id=article.id,
            tag_id=int(tag.id or 0),
            tag_kind="topic",
            is_primary=True,
            relevance=0.95,
            assignment_source="llm",
            prompt_version="article-analysis-v3",
            taxonomy_version=1,
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        ))
        session.commit()
    storage.engine.dispose()


def _seed_consumer(db_path: Path, media_root: Path, producer_url: str) -> None:
    storage = _prepare_database(db_path)
    with Session(storage.engine) as session:
        platform = _source(PUBLIC_SOURCE)
        platform.collection_authority_id = "split-e2e-producer"
        session.add(platform)
        stale = _article(PUBLIC_ARTICLE, PUBLIC_SOURCE, "Stale internal body")
        stale.analysis_authority_id = "split-e2e-producer"
        session.add(stale)
        stale_historical = _article(
            HISTORICAL_ARTICLE,
            PUBLIC_SOURCE,
            "Historical body with stale internal analysis",
        )
        session.add(stale_historical)
        session.add(_source(CUSTOM_SOURCE, owner="alice"))
        session.add(_source(
            PRIVATE_SOURCE,
            owner="alice",
            params='{"credentialed_private":true}',
        ))
        custom = _article("split-e2e-custom", CUSTOM_SOURCE, "Internal custom RSS body")
        private = _article("split-e2e-private", PRIVATE_SOURCE, "Credentialed RSS body")
        session.add(custom)
        session.add(private)
        session.flush()
        session.add(ArticleAnalysisRecord(
            article_id=stale.id,
            status="running",
            tagging_status="pending",
            quality_score=None,
            content_hash=compute_content_hash(stale),
            lease_owner="internal-worker",
            lease_expires_at="2099-01-01T00:00:00+00:00",
            authority_id="split-e2e-producer",
            authority_revision="2026-09-03T00:00:00+00:00",
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        ))
        session.add(ArticleAnalysisRecord(
            article_id=stale_historical.id,
            status="succeeded",
            tagging_status="succeeded",
            quality_score=4.0,
            content_hash=compute_content_hash(stale_historical),
            authority_id="",
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        ))
        session.add(SourceStateRecord(
            source_id=PUBLIC_SOURCE,
            fetcher_id="generic_rss",
            status="healthy",
            authority_id="split-e2e-producer",
            authority_revision="2026-09-03T00:00:00+00:00",
            updated_at=ANALYSIS_STAMP,
        ))
        stale_media_body = b"\x89PNG\r\n\x1a\nretained"
        stale_media = MediaAssetRecord(
            url_hash=url_hash_of(STALE_MEDIA_URL),
            url=STALE_MEDIA_URL,
            status="cached",
            content_hash=hashlib.sha256(stale_media_body).hexdigest(),
            mime="image/png",
            ext=".png",
            size_bytes=len(stale_media_body),
            sync_authority_id="split-e2e-producer",
            sync_authority_revision="2026-09-03T00:00:00+00:00",
            created_at=STAMP,
            fetched_at=STAMP,
            updated_at=STAMP,
        )
        session.add(stale_media)
        session.add(ArchiveSyncEntityStateRecord(
            stream="articles",
            identity=PUBLIC_ARTICLE,
            authority_id="split-e2e-producer",
            revision=7,
            operation="upsert",
            updated_at=STAMP,
        ))
        session.add(AppSettingRecord(
            key="remote_sync:state",
            value=json.dumps({
                "targets": {
                    producer_url: {
                        "username": "admin",
                        "v2_streams": {
                            "articles": {
                                "authority_id": "split-e2e-producer",
                                "snapshot": "2026-09-03T00:00:00+00:00",
                            }
                        },
                    }
                }
            }),
        ))
        candidate = CmsTagCandidateRecord(
            label="Agent Memory",
            normalized_label="agent memory",
            proposed_kind="topic",
            first_seen_at=ANALYSIS_STAMP,
            last_seen_at=ANALYSIS_STAMP,
            created_at=ANALYSIS_STAMP,
            updated_at=ANALYSIS_STAMP,
        )
        session.add(candidate)
        session.flush()
        for article, source in ((custom, CUSTOM_SOURCE), (private, PRIVATE_SOURCE)):
            session.add(CmsTagCandidateEvidenceRecord(
                candidate_id=int(candidate.id or 0),
                article_id=article.id,
                source_id=source,
                confidence=0.8,
                raw_label="Agent Memory",
                prompt_version="article-analysis-v3",
                created_at=ANALYSIS_STAMP,
            ))
        session.commit()
        stale_path = MediaStore(storage.engine, media_root).file_path_for(stale_media)
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_bytes(stale_media_body)
    storage.engine.dispose()


def _server_command(port: int) -> list[str]:
    del port
    return [sys.executable, str(SRC_DIR / "main.py")]


def _wait_ready(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited ({process.returncode}):\n{log_path.read_text(errors='replace')}")
        try:
            if httpx.get(f"{base_url}/api/auth/session", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"server did not become ready:\n{log_path.read_text(errors='replace')}")


def _login(base_url: str) -> httpx.Client:
    client = httpx.Client(base_url=base_url, timeout=10)
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    response.raise_for_status()
    return client


def _run_taxonomy_deployment_smoke(root: Path) -> dict:
    """Start the real authority entrypoint twice and verify idempotent catalog install."""

    db_path = root / "taxonomy-authority.db"
    media_root = root / "taxonomy-authority-media"
    config_path = root / "taxonomy-authority.ini"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    _write_config(
        config_path,
        db_path,
        media_root,
        port,
        taxonomy_mode="authority",
    )
    counts: list[int] = []
    for attempt in (1, 2):
        log_path = root / f"taxonomy-authority-{attempt}.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                _server_command(port),
                cwd=PROJECT_ROOT,
                env={
                    **os.environ,
                    "DORAMI_CONFIG_FILE": str(config_path),
                    "DORAMI_ARCHIVE_AUTHORITY_ID": "split-e2e-taxonomy-authority",
                    "PYTHONPATH": str(SRC_DIR),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_ready(base_url, process, log_path)
                storage = DatabaseStorage(db_url=f"sqlite:///{db_path}")
                try:
                    with Session(storage.engine) as session:
                        counts.append(len(session.exec(select(CmsTagRecord)).all()))
                        receipt = session.get(
                            AppSettingRecord, "taxonomy_v1_review_receipt"
                        )
                        assert receipt is not None and receipt.value
                        assert session.exec(select(TaxonomyVersionRecord)).all() == []
                finally:
                    storage.engine.dispose()
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    assert counts == [96, 96]
    return {"mode": "authority", "catalog_count_by_start": counts, "published": False}


def _run_sync(consumer: httpx.Client, producer_url: str, *, consumer_db: Path) -> dict:
    response = consumer.post("/api/admin/remote-sync/start", json={
        "base_url": producer_url,
        "username": "admin",
        "password": "admin",
        "page_size": 1,
        "protocol": "v2",
    })
    response.raise_for_status()
    job_id = response.json()["job_id"]
    storage = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        with Session(storage.engine) as session:
            marker = session.get(AppSettingRecord, "remote_sync:v2_consumer_mode")
            job_row = session.get(JobRecord, job_id)
            assert marker is not None
            assert job_row is not None and "password" not in job_row.payload_json.lower()
    finally:
        storage.engine.dispose()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job_response = consumer.get(f"/api/jobs/{job_id}")
        job_response.raise_for_status()
        job = job_response.json()
        if job["status"] == "succeeded":
            return job["result"]
        if job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"remote sync job {job_id} failed: {job.get('error')}")
        time.sleep(0.2)
    raise RuntimeError(f"remote sync job {job_id} did not finish")


def _assert_first_sync(producer_db: Path, consumer_db: Path, consumer_media: Path, result: dict) -> None:
    assert tuple(result["streams"]) == EXPECTED_STREAMS
    assert result["authority_id"] == "split-e2e-producer"
    assert result["candidate_evidence"]["status"] == "success"
    producer = DatabaseStorage(db_url=f"sqlite:///{producer_db}")
    consumer = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        with Session(consumer.engine) as session:
            source = session.get(SourceConfigRecord, PUBLIC_SOURCE)
            article = session.get(ArticleRecord, PUBLIC_ARTICLE)
            analysis = session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE)
            historical = session.get(ArticleRecord, HISTORICAL_ARTICLE)
            state = session.get(SourceStateRecord, PUBLIC_SOURCE)
            custom = session.get(ArticleRecord, "split-e2e-custom")
            private_source = session.get(SourceConfigRecord, PRIVATE_SOURCE)
            media = session.exec(select(MediaAssetRecord)).all()
            assert source and source.collection_authority_id == result["authority_id"]
            assert article and "Body A" in (article.content or "")
            assert article.analysis_authority_id == result["authority_id"]
            assert analysis and analysis.status == "succeeded" and analysis.quality_score == 8.6
            assert analysis.lease_owner is None and analysis.authority_id == result["authority_id"]
            assert historical and session.get(ArticleAnalysisRecord, historical.id) is None
            assert state and state.status == "healthy" and state.authority_id == result["authority_id"]
            assert custom and custom.analysis_authority_id == ""
            assert private_source and private_source.ai_analysis_enabled is False
            assert len(media) == len(MEDIA_BODIES) + 1
            by_url = {row.url: row for row in media}
            assert set(MEDIA_BODIES) < set(by_url)
            for url, expected_body in MEDIA_BODIES.items():
                row = by_url[url]
                body = MediaStore(consumer.engine, consumer_media).file_path_for(row).read_bytes()
                assert row.status == "cached" and row.sync_authority_id == result["authority_id"]
                assert row.size_bytes == len(expected_body)
                assert row.content_hash == hashlib.sha256(expected_body).hexdigest()
                assert body == expected_body
            assert session.get(MediaAssetRecord, url_hash_of(AUDIO_URL)) is None
            retained = by_url[STALE_MEDIA_URL]
            assert MediaStore(consumer.engine, consumer_media).file_path_for(retained).exists()
            sync_target = json.loads(
                session.get(AppSettingRecord, "remote_sync:state").value
            )["targets"]
            assert sync_target
            target = next(iter(sync_target.values()))
            assert target["v2_schema_version"] == archive_sync_v2.SCHEMA_VERSION
            assert target["v2_authority_id"] == result["authority_id"]
        with Session(producer.engine) as session:
            remote = session.exec(select(RemoteCandidateEvidenceRecord)).all()
            assert len(remote) == 1
            assert remote[0].source_provenance == CUSTOM_SOURCE
            assert session.get(ArticleRecord, "split-e2e-custom") is None
            assert PRIVATE_SOURCE not in {row.source_provenance for row in remote}
    finally:
        producer.engine.dispose()
        consumer.engine.dispose()


def _mutate_producer_to_pending(db_path: Path) -> None:
    storage = DatabaseStorage(db_url=f"sqlite:///{db_path}")
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, PUBLIC_ARTICLE)
        assert article
        article.content = article.content.replace("Body A", "Body B")
        session.add(article)
        session.flush()
        outcome = queue_article_analysis(
            session,
            article.id,
            force=True,
            now=dt.datetime.now(dt.timezone.utc),
        )
        assert outcome == "invalidated"
        session.commit()
    storage.engine.dispose()


def _complete_producer_analysis(db_path: Path) -> None:
    storage = DatabaseStorage(db_url=f"sqlite:///{db_path}")
    with Session(storage.engine) as session:
        article = session.get(ArticleRecord, PUBLIC_ARTICLE)
        analysis = session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE)
        tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == "topic.split-e2e")).one()
        assert article and analysis
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        analysis.status = "succeeded"
        analysis.tagging_status = "succeeded"
        analysis.quality_score = 9.4
        analysis.summary = "Incremental producer result."
        analysis.content_hash = compute_content_hash(article)
        analysis.primary_tag_id = tag.id
        analysis.analyzed_at = now
        analysis.tagged_at = now
        analysis.updated_at = now
        session.add(analysis)
        session.add(ArticleTagAssignmentRecord(
            article_id=article.id,
            tag_id=int(tag.id or 0),
            tag_kind="topic",
            is_primary=True,
            relevance=0.97,
            assignment_source="llm",
            prompt_version="article-analysis-v3",
            taxonomy_version=1,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    storage.engine.dispose()


def _assert_pending_increment(consumer_db: Path, result: dict) -> None:
    assert result["streams"]["articles"]["count"] == 1
    assert result["streams"]["analyses"]["count"] == 1
    consumer = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        with Session(consumer.engine) as session:
            article = session.get(ArticleRecord, PUBLIC_ARTICLE)
            analysis = session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE)
            assert article and "Body B" in (article.content or "")
            assert analysis and analysis.status == "pending"
            assert analysis.quality_score is None and analysis.primary_tag_id is None
            assert analysis.content_hash == compute_content_hash(article)
    finally:
        consumer.engine.dispose()


def _assert_completed_increment(consumer_db: Path, result: dict) -> None:
    assert result["streams"]["articles"]["count"] == 0
    assert result["streams"]["analyses"]["count"] == 1
    consumer = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        with Session(consumer.engine) as session:
            article = session.get(ArticleRecord, PUBLIC_ARTICLE)
            analysis = session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE)
            assignments = session.exec(select(ArticleTagAssignmentRecord).where(
                ArticleTagAssignmentRecord.article_id == PUBLIC_ARTICLE,
                ArticleTagAssignmentRecord.assignment_source == "llm",
            )).all()
            assert article and analysis and analysis.status == "succeeded"
            assert analysis.quality_score == 9.4
            assert analysis.content_hash == compute_content_hash(article)
            assert len(assignments) == 1 and assignments[0].is_primary is True
    finally:
        consumer.engine.dispose()


def _delete_analysis_after_source_delete(producer_db: Path) -> None:
    storage = DatabaseStorage(db_url=f"sqlite:///{producer_db}")
    try:
        with Session(storage.engine) as session:
            analysis = session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE)
            state = session.get(SourceStateRecord, PUBLIC_SOURCE)
            assert analysis and state is None
            session.delete(analysis)
            session.commit()
    finally:
        storage.engine.dispose()


def _assert_semantic_deletions(
    consumer_db: Path,
    consumer_media: Path,
    *,
    article_deleted: bool,
) -> None:
    consumer = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        with Session(consumer.engine) as session:
            source = session.get(SourceConfigRecord, PUBLIC_SOURCE)
            assert source is None
            assert session.get(ArticleAnalysisRecord, PUBLIC_ARTICLE) is None
            assert session.get(SourceStateRecord, PUBLIC_SOURCE) is None
            article = session.get(ArticleRecord, PUBLIC_ARTICLE)
            assert (article is None) is article_deleted
            # Producer deletions never remove receiver media. Reference GC owns
            # both the database row and bytes after the article disappears.
            media = session.get(MediaAssetRecord, url_hash_of(BODY_IMAGE_URL))
            assert media is not None
            assert MediaStore(consumer.engine, consumer_media).file_path_for(media).exists()
    finally:
        consumer.engine.dispose()


def _run_and_assert_media_gc(consumer_db: Path, consumer_media: Path) -> dict:
    """Age the tombstoned article's image, then exercise receiver-local GC."""

    consumer = DatabaseStorage(db_url=f"sqlite:///{consumer_db}")
    try:
        store = MediaStore(consumer.engine, consumer_media)
        with Session(consumer.engine) as session:
            media = session.get(MediaAssetRecord, url_hash_of(BODY_IMAGE_URL))
            assert media is not None
            path = store.file_path_for(media)
            assert path.is_file()
            media.updated_at = "2026-08-01T00:00:00+00:00"
            session.add(media)
            session.commit()
        result = store.gc_remote_unreferenced(
            now=dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc),
        )
        assert result["records_deleted"] >= 1
        with Session(consumer.engine) as session:
            assert session.get(MediaAssetRecord, url_hash_of(BODY_IMAGE_URL)) is None
        assert not path.exists()
        return result
    finally:
        consumer.engine.dispose()


def _delete_public_article(producer_db: Path) -> None:
    storage = DatabaseStorage(db_url=f"sqlite:///{producer_db}")
    try:
        with Session(storage.engine) as session:
            article = session.get(ArticleRecord, PUBLIC_ARTICLE)
            assert article is not None
            session.delete(article)
            session.commit()
    finally:
        storage.engine.dispose()


def _assert_http_contract(consumer: httpx.Client) -> None:
    historical = consumer.get(f"/api/articles/{HISTORICAL_ARTICLE}")
    historical.raise_for_status()
    item = historical.json()
    assert item["analysis_status"] is None
    assert item["analysis_has_result"] is False
    assert item["quality_score"] is None
    assert item["tags"] == [] and item["display_tags"] == []

    analysis = consumer.get(f"/api/articles/{HISTORICAL_ARTICLE}/analysis")
    analysis.raise_for_status()
    assert analysis.json() == {
        "article_id": HISTORICAL_ARTICLE,
        "status": "not_started",
        "tags": [],
        "display_tags": [],
    }

    media = consumer.get(
        "/api/media/proxy",
        params={"url": BODY_IMAGE_URL},
    )
    media.raise_for_status()
    assert media.content == MEDIA_BODIES[BODY_IMAGE_URL]
    assert media.headers["content-type"].startswith("image/png")
    assert media.headers["x-content-type-options"] == "nosniff"

    assert consumer.put(
        f"/api/articles/{PUBLIC_ARTICLE}", json={"title": "must be blocked"}
    ).status_code == 409
    assert consumer.put(
        f"/api/source-configs/{PUBLIC_SOURCE}", json={"name": "must be blocked"}
    ).status_code == 409
    local_update = consumer.put(
        "/api/articles/split-e2e-custom", json={"title": "local update allowed"}
    )
    local_update.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    if not __debug__:
        raise RuntimeError("do not run the split-sync verifier with python -O")
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="Keep the temporary environment")
    args = parser.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="dorami-split-sync-e2e-"))
    producer_db = root / "producer.db"
    consumer_db = root / "consumer.db"
    producer_media = root / "producer-media"
    consumer_media = root / "consumer-media"
    producer_port = _free_port()
    consumer_port = _free_port()
    while consumer_port == producer_port:
        consumer_port = _free_port()
    producer_url = f"http://127.0.0.1:{producer_port}"
    consumer_url = f"http://127.0.0.1:{consumer_port}"
    producer_config = root / "producer.ini"
    consumer_config = root / "consumer.ini"
    processes: list[subprocess.Popen] = []
    passed = False
    try:
        taxonomy_deployment = _run_taxonomy_deployment_smoke(root)
        _write_config(producer_config, producer_db, producer_media, producer_port)
        _write_config(consumer_config, consumer_db, consumer_media, consumer_port)
        _seed_producer(producer_db, producer_media)
        _seed_consumer(consumer_db, consumer_media, producer_url)
        with ExitStack() as stack:
            for name, config, port, url in (
                ("producer", producer_config, producer_port, producer_url),
                ("consumer", consumer_config, consumer_port, consumer_url),
            ):
                log_path = root / f"{name}.log"
                log = stack.enter_context(log_path.open("w", encoding="utf-8"))
                env = {
                    **os.environ,
                    "DORAMI_CONFIG_FILE": str(config),
                    "DORAMI_ARCHIVE_AUTHORITY_ID": f"split-e2e-{name}",
                    "PYTHONPATH": str(SRC_DIR),
                }
                process = subprocess.Popen(
                    _server_command(port),
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                processes.append(process)
                _wait_ready(url, process, log_path)
            consumer = _login(consumer_url)
            producer_client = _login(producer_url)
            try:
                probe = consumer.post("/api/admin/remote-sync/test", json={
                    "base_url": producer_url,
                    "username": "admin",
                    "password": "admin",
                    "protocol": "v2",
                })
                probe.raise_for_status()
                assert probe.json()["authority_id"] == "split-e2e-producer"
                first = _run_sync(consumer, producer_url, consumer_db=consumer_db)
                _assert_first_sync(producer_db, consumer_db, consumer_media, first)
                _assert_http_contract(consumer)
                _mutate_producer_to_pending(producer_db)
                pending = _run_sync(consumer, producer_url, consumer_db=consumer_db)
                _assert_pending_increment(consumer_db, pending)
                pending_view = consumer.get(f"/api/articles/{PUBLIC_ARTICLE}").json()
                assert pending_view["analysis_status"] == "pending"
                assert pending_view["analysis_has_result"] is False
                assert pending_view["quality_score"] is None
                assert pending_view["display_tags"] == []
                _complete_producer_analysis(producer_db)
                completed = _run_sync(consumer, producer_url, consumer_db=consumer_db)
                _assert_completed_increment(consumer_db, completed)
                no_op = _run_sync(consumer, producer_url, consumer_db=consumer_db)
                assert all(value["count"] == 0 for value in no_op["streams"].values())
                source_delete = producer_client.delete(
                    f"/api/source-configs/{PUBLIC_SOURCE}"
                )
                source_delete.raise_for_status()
                _delete_analysis_after_source_delete(producer_db)
                semantic_delete = _run_sync(
                    consumer, producer_url, consumer_db=consumer_db
                )
                assert semantic_delete["streams"]["sources"]["count"] == 1
                assert semantic_delete["streams"]["analyses"]["deleted"] == 1
                assert semantic_delete["streams"]["source_states"]["count"] == 1
                # Applying the Source tombstone already removes its remote
                # SourceState; the later readiness tombstone is an idempotent no-op.
                assert semantic_delete["streams"]["source_states"]["deleted"] == 0
                _assert_semantic_deletions(
                    consumer_db, consumer_media, article_deleted=False
                )
                _delete_public_article(producer_db)
                article_delete = _run_sync(
                    consumer, producer_url, consumer_db=consumer_db
                )
                assert article_delete["streams"]["articles"]["deleted"] == 1
                _assert_semantic_deletions(
                    consumer_db, consumer_media, article_deleted=True
                )
                media_gc = _run_and_assert_media_gc(consumer_db, consumer_media)
                checkpoints = consumer.get("/api/admin/remote-sync/status").json()["state"]
                stream_checkpoints = checkpoints["targets"][producer_url]["v2_streams"]
                assert tuple(stream_checkpoints) == EXPECTED_STREAMS
                assert all(
                    checkpoint["authority_id"] == "split-e2e-producer"
                    and checkpoint["snapshot"]
                    and checkpoint["completed_at"]
                    for checkpoint in stream_checkpoints.values()
                )
                producer = DatabaseStorage(db_url=f"sqlite:///{producer_db}")
                try:
                    with Session(producer.engine) as session:
                        assert len(session.exec(select(RemoteCandidateEvidenceRecord)).all()) == 1
                finally:
                    producer.engine.dispose()
            finally:
                producer_client.close()
                consumer.close()
        print(json.dumps({
            "status": "passed",
            "producer": producer_url,
            "consumer": consumer_url,
            "streams": list(EXPECTED_STREAMS),
            "first_counts": {key: value["count"] for key, value in first["streams"].items()},
            "pending_counts": {key: value["count"] for key, value in pending["streams"].items()},
            "completed_counts": {key: value["count"] for key, value in completed["streams"].items()},
            "no_op_counts": {key: value["count"] for key, value in no_op["streams"].items()},
            "semantic_delete_counts": {
                key: value["count"] for key, value in semantic_delete["streams"].items()
            },
            "article_delete_counts": {
                key: value["count"] for key, value in article_delete["streams"].items()
            },
            "media_gc": media_gc,
            "candidate_evidence": first["candidate_evidence"],
            "taxonomy_deployment": taxonomy_deployment,
            "temp_root": str(root) if args.keep else "removed",
        }, ensure_ascii=False, indent=2))
        passed = True
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if not args.keep and passed:
            shutil.rmtree(root)
            if root.exists():
                raise RuntimeError(f"failed to remove successful test environment: {root}")
        elif not passed:
            print(f"split-sync environment retained for diagnosis: {root}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
