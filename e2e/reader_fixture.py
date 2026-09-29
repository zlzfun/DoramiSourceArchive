"""Synthetic content in a real, disposable SQLite database; never mock reader APIs."""
from __future__ import annotations

import configparser
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERNAME = "e2e-reader"
PASSWORD = "e2e-reader-password"
DESK_USERNAME = "e2e-desk"
ADMIN_USERNAME = "e2e-admin"
SOURCE_A = "e2e-reader-alpha"
SOURCE_B = "e2e-reader-beta"
SOURCE_C = "e2e-reader-gamma"
SOURCE_NAMES = {SOURCE_A: "端到端测试来源甲", SOURCE_B: "端到端测试来源乙"}
ARTICLE_COUNT = 60
BULLETIN_COUNT = 4
# Completed news-value analyses for the newest SOURCE_A articles. The last one sits below the
# personal-brief floor (5.0) and must never appear in an edition.
BRIEF_SCORES = (8.8, 8.4, 7.9, 7.2, 6.5, 5.6, 4.2)
BRIEF_FLOOR = 5.0
BODY = "\n\n".join(
    f"## 阅读章节 {index:02d}\n\n"
    + ("这是一篇用于验证移动阅读体验的测试文章。滚动正文、切换窗口宽度后，应该仍能接着阅读，而不是重新寻找位置。" * 5)
    for index in range(1, 25)
) + "\n\n```text\n" + "long-content-" * 60 + "\n```\n"


def article_title(source_id: str, index: int) -> str:
    title = "连续阅读" if source_id == SOURCE_A else "另一来源"
    return f"{title} {index:02d}：移动阅读端到端样例"


def bulletin_title(index: int) -> str:
    return f"端到端动态 v1.{index}.0 发布说明"


def validate_sandbox(sandbox: Path) -> Path:
    """Fail before importing application code (config import can otherwise select a real DB)."""
    sandbox = sandbox.resolve()
    config = sandbox / "backend.ini"
    database = sandbox / "reader.db"
    if not (sandbox / ".dorami-e2e").is_file():
        raise ValueError("refusing a directory not created by the E2E runner")
    if Path(os.environ.get("DORAMI_CONFIG_FILE", "")).resolve() != config:
        raise ValueError("DORAMI_CONFIG_FILE must point to this sandbox")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config)
    if parser.get("storage", "database_url", fallback="") != f"sqlite:///{database}":
        raise ValueError("database must be reader.db inside the sandbox")
    if database.exists():
        raise ValueError("refusing to seed an existing database")
    return database


def seed(sandbox: Path, *, password: str = PASSWORD, with_admin: bool = False) -> None:
    database = validate_sandbox(sandbox)
    sys.path.insert(0, str(ROOT / "src"))
    from config import settings
    from llm.article_analysis_prompt import ARTICLE_ANALYSIS_PROMPT_VERSION, ARTICLE_ANALYSIS_SCORING_VERSION
    from models.db import (AppSettingRecord, ArticleAnalysisRecord, ArticleRecord, CollectionJobRecord,
                           ReaderSubscriptionRecord, SourceConfigRecord)
    from services.accounts import create_user
    from services.article_analysis import compute_content_hash
    from services.podcast_catalog import ensure_default_podcast_sources
    from sqlmodel import Session, select
    from storage.impl.db_storage import DatabaseStorage
    from storage.migrations import ensure_migrated

    if settings.storage.database_url != f"sqlite:///{database}":
        raise ValueError("resolved configuration escaped the sandbox")
    ensure_migrated(settings.storage.database_url)
    sink = DatabaseStorage(db_url=settings.storage.database_url)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    timestamp = now.isoformat()

    def subscribe(session, username, source_id, name):
        session.add(ReaderSubscriptionRecord(
            owner_username=username, name=name,
            filters_json=json.dumps({"source_id": source_id}), token_hash=f"unused-{username}-{source_id}",
            created_at=timestamp, updated_at=timestamp,
        ))

    try:
        with Session(sink.engine) as session:
            if with_admin:
                create_user(session, ADMIN_USERNAME, password, "admin")
            for username in (USERNAME, DESK_USERNAME):
                user = create_user(session, username, password, "user")
                user.interest_onboarding_completed_at = timestamp
                session.add(user)
                session.add(AppSettingRecord(key=f"reader_defaults_seeded:{username}", value="e2e-preseeded"))
            for key, value in {"personal_digest_enabled": "false", "user_sources_enabled": "false"}.items():
                session.add(AppSettingRecord(key=key, value=value))
            for source_id, name in SOURCE_NAMES.items():
                session.add(SourceConfigRecord(
                    source_id=source_id, name=name, source_type="rss", category="official",
                    url=f"https://example.invalid/{source_id}.xml", ai_analysis_enabled=False,
                    created_at=timestamp, updated_at=timestamp,
                ))
                subscribe(session, USERNAME, source_id, name)
            # The desktop reader leaves SOURCE_B unsubscribed so Discover has a real target.
            subscribe(session, DESK_USERNAME, SOURCE_A, SOURCE_NAMES[SOURCE_A])
            subscribe(session, DESK_USERNAME, SOURCE_C, "端到端动态")
            for source_id, count in [(SOURCE_A, ARTICLE_COUNT), (SOURCE_B, 3)]:
                for index in range(count):
                    # Distinct recency keys make pagination deterministic; both feeds are recent.
                    published = (now - timedelta(minutes=index + (100 if source_id == SOURCE_B else 0))).isoformat()
                    session.add(ArticleRecord(
                        id=f"{source_id}-{index:02d}", title=article_title(source_id, index),
                        source_id=source_id, content_type="rss_article",
                        source_url=f"https://example.invalid/{source_id}/{index}",
                        publish_date=published, fetched_date=published, has_content=True, content=BODY,
                    ))
            for index in range(BULLETIN_COUNT):
                published = (now - timedelta(minutes=index + 5)).isoformat()
                session.add(ArticleRecord(
                    id=f"{SOURCE_C}-{index:02d}", title=bulletin_title(index), source_id=SOURCE_C,
                    content_type="github_release", source_url=f"https://example.invalid/{SOURCE_C}/{index}",
                    publish_date=published, fetched_date=published, has_content=True,
                    content=f"端到端动态正文 {index}：修复若干问题。",
                ))
            session.flush()
            for index, score in enumerate(BRIEF_SCORES):
                article = session.get(ArticleRecord, f"{SOURCE_A}-{index:02d}")
                session.add(ArticleAnalysisRecord(
                    article_id=article.id, status="succeeded", tagging_status="succeeded",
                    quality_score=score, score_reason="端到端合成分析。", summary=f"端到端早报摘要 {index:02d}",
                    content_genre="industry_news", content_hash=compute_content_hash(article), model_name="e2e",
                    prompt_version=ARTICLE_ANALYSIS_PROMPT_VERSION, scoring_version=ARTICLE_ANALYSIS_SCORING_VERSION,
                    analyzed_at=timestamp, created_at=timestamp, updated_at=timestamp,
                ))
            session.commit()
        # The backend installs the podcast catalog on import, including an enabled collection job.
        # Installing it here first and storing every job disabled keeps a collector-role sandbox off
        # the network: the bootstrap respects an existing job's on/off state.
        ensure_default_podcast_sources(sink.engine)
        with Session(sink.engine) as session:
            for job in session.exec(select(CollectionJobRecord)).all():
                job.is_active = False
                session.add(job)
            session.commit()
    finally:
        sink.engine.dispose()
    print(f"Seeded {ARTICLE_COUNT + 3 + BULLETIN_COUNT} items, two readers"
          f"{' and one admin' if with_admin else ''} in {database}")


if __name__ == "__main__":
    # The public PWA preview reuses this seed; only the local test runner asks for an admin.
    seed(Path(sys.argv[1]), password=os.environ.get("DORAMI_E2E_PASSWORD", PASSWORD),
         with_admin="--with-admin" in sys.argv[2:])
