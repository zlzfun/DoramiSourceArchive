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
SOURCE_A = "e2e-reader-alpha"
SOURCE_B = "e2e-reader-beta"
SOURCE_NAMES = {SOURCE_A: "端到端测试来源甲", SOURCE_B: "端到端测试来源乙"}
ARTICLE_COUNT = 60
BODY = "\n\n".join(
    f"## 阅读章节 {index:02d}\n\n"
    + ("这是一篇用于验证移动阅读体验的测试文章。滚动正文、切换窗口宽度后，应该仍能接着阅读，而不是重新寻找位置。" * 5)
    for index in range(1, 25)
) + "\n\n```text\n" + "long-content-" * 60 + "\n```\n"


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


def seed(sandbox: Path, *, password: str = PASSWORD) -> None:
    database = validate_sandbox(sandbox)
    sys.path.insert(0, str(ROOT / "src"))
    from config import settings
    from models.db import AppSettingRecord, ArticleRecord, ReaderSubscriptionRecord, SourceConfigRecord
    from services.accounts import create_user
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    from storage.migrations import ensure_migrated

    if settings.storage.database_url != f"sqlite:///{database}":
        raise ValueError("resolved configuration escaped the sandbox")
    ensure_migrated(settings.storage.database_url)
    sink = DatabaseStorage(db_url=settings.storage.database_url)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    timestamp = now.isoformat()
    try:
        with Session(sink.engine) as session:
            user = create_user(session, USERNAME, password, "user")
            user.interest_onboarding_completed_at = timestamp
            session.add(user)
            for key, value in {
                f"reader_defaults_seeded:{USERNAME}": "e2e-preseeded",
                "personal_digest_enabled": "false",
                "user_sources_enabled": "false",
            }.items():
                session.add(AppSettingRecord(key=key, value=value))
            for source_id, name in SOURCE_NAMES.items():
                session.add(SourceConfigRecord(
                    source_id=source_id, name=name, source_type="rss", category="official",
                    url=f"https://example.invalid/{source_id}.xml", ai_analysis_enabled=False,
                    created_at=timestamp, updated_at=timestamp,
                ))
                session.add(ReaderSubscriptionRecord(
                    owner_username=USERNAME, name=name,
                    filters_json=json.dumps({"source_id": source_id}), token_hash=f"unused-{source_id}",
                    created_at=timestamp, updated_at=timestamp,
                ))
            for source_id, count, title in [(SOURCE_A, ARTICLE_COUNT, "连续阅读"), (SOURCE_B, 3, "另一来源")]:
                for index in range(count):
                    # Distinct recency keys make pagination deterministic; both feeds are recent.
                    published = (now - timedelta(minutes=index + (100 if source_id == SOURCE_B else 0))).isoformat()
                    session.add(ArticleRecord(
                        id=f"{source_id}-{index:02d}", title=f"{title} {index:02d}：移动阅读端到端样例",
                        source_id=source_id, content_type="rss_article",
                        source_url=f"https://example.invalid/{source_id}/{index}",
                        publish_date=published, fetched_date=published, has_content=True, content=BODY,
                    ))
            session.commit()
    finally:
        sink.engine.dispose()
    print(f"Seeded {ARTICLE_COUNT + 3} articles and one reader in {database}")


if __name__ == "__main__":
    seed(Path(sys.argv[1]), password=os.environ.get("DORAMI_E2E_PASSWORD", PASSWORD))
