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
TAG_LABEL = "OpenAI"
FREE_LABEL = "A/B 案例 & C++"
TAG_MATCH_IDS = (f"{SOURCE_A}-00", f"{SOURCE_A}-01")
# issue #230 批 4b 手机守卫流程(opt-in,DORAMI_E2E_GUARDS=1):独立读者 + 长列表来源 + 今日早报,不动主流程夹具
# 一个视口一个读者:四档并行时互不干扰(同一账号并发首开会撞后端已读游标的唯一约束,见方案 §8.1 批 4b 观察)
GUARD_WIDTHS = (360, 375, 390, 430)
GUARD_PASSWORD = "e2e-guard-password"
SOURCE_C = "e2e-reader-gamma"
SOURCE_C_NAME = "端到端长列表来源丙"
LONG_COUNT = 210
BRIEF_COUNT = 6
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


def seed(sandbox: Path, *, password: str = PASSWORD, tag_search: bool = False, guards: bool = False) -> None:
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
            if tag_search:
                seed_tag_search(session, timestamp)
            if guards:
                seed_guards(session, now)
    finally:
        sink.engine.dispose()
    print(f"Seeded {ARTICLE_COUNT + 3} articles and one reader in {database}")


def seed_tag_search(session, timestamp: str) -> None:
    """Opt-in issue #93 fixture; leaves the existing mobile/PWA fixture unchanged."""
    from models.db import ArticleAnalysisRecord, ArticleRecord, ArticleTagAssignmentRecord, CmsTagRecord
    tag = CmsTagRecord(code="entity.e2e_openai", kind="entity", name_zh=TAG_LABEL,
                       normalized_name="e2e-openai", status="active", user_selectable=True,
                       created_at=timestamp, updated_at=timestamp)
    session.add(tag)
    session.flush()
    for article_id in [*TAG_MATCH_IDS, f"{SOURCE_A}-02"]:
        article = session.get(ArticleRecord, article_id)
        article.content = f"正文提及 {TAG_LABEL} 与 {FREE_LABEL}。\n\n" + BODY
        session.add(article)
        if article_id not in TAG_MATCH_IDS:
            continue  # Text mention alone must never satisfy either tag filter.
        session.add(ArticleAnalysisRecord(
            article_id=article_id, status="succeeded", tagging_status="succeeded",
            quality_score=8.0, summary="标签检索合成样例",
            display_tags_json=json.dumps([
                {"label": FREE_LABEL, "kind": "topic", "confidence": .99},
                {"label": TAG_LABEL, "kind": "entity", "confidence": .98},
            ]),
            created_at=timestamp, updated_at=timestamp,
        ))
        if article_id == TAG_MATCH_IDS[0]:
            session.add(ArticleTagAssignmentRecord(
                article_id=article_id, tag_id=tag.id, tag_kind="entity", is_primary=False,
                relevance=.07, assignment_source="llm", created_at=timestamp, updated_at=timestamp,
            ))
    session.commit()


def seed_guards(session, now: datetime) -> None:
    """Opt-in issue #230 guard fixture: one reader per guard viewport, each subscribed to A / B and a
    210-article source C (long-list DOM guard), with a ready personal brief for today (brief Tab, history sheet).
    The brief only shows while the guard flow switches ``personal_digest_enabled`` on; other flows never see it."""
    from zoneinfo import ZoneInfo
    from models.db import (AppSettingRecord, ArticleRecord, PersonalDigestEditionRecord, ReaderSubscriptionRecord,
                           SourceConfigRecord)
    from services.accounts import create_user

    timestamp = now.isoformat()
    session.add(SourceConfigRecord(
        source_id=SOURCE_C, name=SOURCE_C_NAME, source_type="rss", category="official",
        url=f"https://example.invalid/{SOURCE_C}.xml", ai_analysis_enabled=False,
        created_at=timestamp, updated_at=timestamp,
    ))
    for index in range(LONG_COUNT):
        published = (now - timedelta(minutes=200 + index)).isoformat()
        session.add(ArticleRecord(
            id=f"{SOURCE_C}-{index:03d}", title=f"长列表 {index:03d}：滚到两百条时页面节点仍有上限",
            source_id=SOURCE_C, content_type="rss_article",
            source_url=f"https://example.invalid/{SOURCE_C}/{index}",
            publish_date=published, fetched_date=published, has_content=True,
            content="长列表守卫样例正文。\n\n" + "这一段只用来占位。" * 8,
        ))
    session.flush()
    report_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for width in GUARD_WIDTHS:
        username = guard_username(width)
        user = create_user(session, username, GUARD_PASSWORD, "user")
        user.interest_onboarding_completed_at = timestamp
        session.add(user)
        session.add(AppSettingRecord(key=f"reader_defaults_seeded:{username}", value="e2e-preseeded"))
        for source_id, name in [*SOURCE_NAMES.items(), (SOURCE_C, SOURCE_C_NAME)]:
            session.add(ReaderSubscriptionRecord(
                owner_username=username, name=name,
                filters_json=json.dumps({"source_id": source_id}), token_hash=f"unused-{username}-{source_id}",
                created_at=timestamp, updated_at=timestamp,
            ))
        edition = PersonalDigestEditionRecord(
            owner_username=username, report_date=report_date, revision=1, status="ready",
            check_after=timestamp, cutoff_at=timestamp, generated_at=timestamp,
            expected_source_ids_json=json.dumps([SOURCE_A, SOURCE_B, SOURCE_C]),
            created_at=timestamp, updated_at=timestamp,
        )
        session.add(edition)
        session.flush()
        seed_brief_items(session, edition.id, timestamp)
    session.commit()


def seed_brief_items(session, edition_id: int, timestamp: str) -> None:
    from models.db import ArticleRecord, PersonalDigestItemRecord

    for position in range(BRIEF_COUNT):
        article = session.get(ArticleRecord, f"{SOURCE_A}-{position:02d}")
        session.add(PersonalDigestItemRecord(
            edition_id=edition_id, article_id=article.id, position=position, section="今日精选",
            selection_lane="quality", quality_score_snapshot=round(8.4 - position * 0.3, 1),
            snapshot_json=json.dumps({
                "article_id": article.id, "title": article.title, "content_type": article.content_type,
                "source_id": article.source_id, "source_name": SOURCE_NAMES[SOURCE_A],
                "source_url": article.source_url, "publish_date": article.publish_date,
                "fetched_date": article.fetched_date, "summary": "早报守卫样例摘要:用来验证早报卡片、长按与往期底页。",
                "quality_score": round(8.4 - position * 0.3, 1), "score_reason": "", "tags": [], "display_tags": [],
                "selection_reason": "", "is_latest_update": False, "subscribed": True, "score_dimension": "news",
            }, ensure_ascii=False),
            created_at=timestamp,
        ))


def guard_username(width: int) -> str:
    return f"e2e-guard-{width}"


if __name__ == "__main__":
    seed(Path(sys.argv[1]), password=os.environ.get("DORAMI_E2E_PASSWORD", PASSWORD),
         tag_search=os.environ.get("DORAMI_E2E_TAG_SEARCH") == "1",
         guards=os.environ.get("DORAMI_E2E_GUARDS") == "1")
