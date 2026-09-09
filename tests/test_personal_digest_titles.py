"""个人早报条目标题中文化(issue #33 §4,v3.51.2):三级来源 / 中文跳过 / 失败回退 / 写回缓存。"""

import asyncio
import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import LLMConfig  # noqa: E402
from models.db import (  # noqa: E402
    ArticleRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    UserRecord,
)
from services import personal_digest_titles as titles  # noqa: E402
from services.reader_ai import TRANSLATION_TITLE_FP_KEY, TRANSLATION_TITLE_KEY, _body_fingerprint  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


NOW_ISO = dt.datetime(2026, 9, 9, 8, 30, tzinfo=dt.timezone(dt.timedelta(hours=8))).isoformat()
CONFIGURED = LLMConfig(base_url="http://llm.test", api_key="k", model="main", aux_model="aux")


@pytest.fixture
def storage(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'titles.db'}")
    yield value
    value.engine.dispose()


def _seed(session: Session, articles: dict[str, str | tuple[str, dict]]) -> PersonalDigestEditionRecord:
    """articles: id → title 或 (title, extensions);返回一期 edition,每篇一条。"""
    session.add(UserRecord(username="alice", password_hash="h", role="user", created_at=NOW_ISO, updated_at=NOW_ISO))
    session.flush()
    edition = PersonalDigestEditionRecord(
        owner_username="alice", report_date="2026-09-09", revision=1, status="ready",
        check_after=NOW_ISO, cutoff_at=NOW_ISO, created_at=NOW_ISO, updated_at=NOW_ISO,
    )
    session.add(edition)
    session.flush()
    for position, (article_id, spec) in enumerate(articles.items()):
        title, ext = (spec, {}) if isinstance(spec, str) else spec
        session.add(ArticleRecord(
            id=article_id, title=title, content_type="rss_article", source_id="rss_a",
            source_url=f"https://example.com/{article_id}", publish_date=NOW_ISO, fetched_date=NOW_ISO,
            content="body", extensions_json=json.dumps(ext),
        ))
        session.add(PersonalDigestItemRecord(
            edition_id=edition.id, article_id=article_id, position=position, section="精选",
            selection_lane="quality", snapshot_json=json.dumps({"article_id": article_id, "title": title}),
            created_at=NOW_ISO,
        ))
    session.commit()
    return edition


def _snapshots(session: Session, edition_id: int) -> dict[str, dict]:
    rows = session.exec(select(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id == edition_id)).all()
    return {row.article_id: json.loads(row.snapshot_json) for row in rows}


def _fake_translate(monkeypatch, mapping: dict[str, str], *, fail: set[str] = frozenset(), calls: list | None = None):
    async def fake(title, config, usage_meta=None, http_client=None):
        if calls is not None:
            calls.append((title, config.model, usage_meta.purpose if usage_meta else None))
        if title in fail:
            raise RuntimeError("boom")
        return mapping[title]
    monkeypatch.setattr(titles, "_translate_title", fake)


def test_sources_in_cost_order_and_chinese_skipped(storage, monkeypatch):
    calls: list = []
    _fake_translate(monkeypatch, {"Fresh English Title": "新译标题"}, calls=calls)
    cached_ext = {TRANSLATION_TITLE_KEY: "缓存译名", TRANSLATION_TITLE_FP_KEY: _body_fingerprint("Cached Title")}
    stale_ext = {TRANSLATION_TITLE_KEY: "过期译名", TRANSLATION_TITLE_FP_KEY: "deadbeef"}
    with Session(storage.engine) as session:
        edition = _seed(session, {
            "a-zh": "中文标题不用译",
            "a-cached": ("Cached Title", cached_ext),
            "a-brief": ("Brief Title", stale_ext),
            "a-fresh": "Fresh English Title",
        })
        # 公共日报里带 a-brief 的编辑标题;a-fresh 在日报里但 title_cn 不是中文 → 不算
        session.add(ArticleRecord(
            id="daily_brief_2026-09-09", title="日报", content_type="daily_brief",
            source_id=titles.PUBLIC_DAILY_BRIEF_SOURCE_ID, source_url="", publish_date="2026-09-09",
            fetched_date=NOW_ISO, content="x",
            extensions_json=json.dumps({"items": [
                {"id": "a-brief", "title_cn": "日报编辑标题"},
                {"id": "a-fresh", "title_cn": "Fresh English Title"},
            ]}),
        ))
        session.commit()
        stats = titles.localize_edition_titles(session, edition, llm_config=CONFIGURED)
        snaps = _snapshots(session, edition.id)
        fresh = session.get(ArticleRecord, "a-fresh")
        brief_article = session.get(ArticleRecord, "a-brief")

    assert stats == {"chinese": 1, "cached": 1, "public_brief": 1, "translated": 1, "fallback": 0}
    assert "title_zh" not in snaps["a-zh"]
    assert snaps["a-cached"]["title_zh"] == "缓存译名"
    assert snaps["a-brief"]["title_zh"] == "日报编辑标题"
    assert snaps["a-fresh"]["title_zh"] == "新译标题"
    # 只有真正没来源的那条才调 LLM,且走 aux 模型、系统用途
    assert calls == [("Fresh English Title", "aux", "personal_digest_title")]
    # 翻译结果写回文章缓存(阅读窗/其他读者受益);公共日报编辑标题不写回
    fresh_ext = json.loads(fresh.extensions_json)
    assert fresh_ext[TRANSLATION_TITLE_KEY] == "新译标题"
    assert fresh_ext[TRANSLATION_TITLE_FP_KEY] == _body_fingerprint("Fresh English Title")
    assert json.loads(brief_article.extensions_json)[TRANSLATION_TITLE_KEY] == "过期译名"


def test_failure_and_unconfigured_fall_back_to_original(storage, monkeypatch):
    calls: list = []
    _fake_translate(monkeypatch, {"Good": "好", "Bad": "坏"}, fail={"Bad"}, calls=calls)
    with Session(storage.engine) as session:
        edition = _seed(session, {"a-good": "Good", "a-bad": "Bad"})
        stats = titles.localize_edition_titles(session, edition, llm_config=CONFIGURED)
        snaps = _snapshots(session, edition.id)
        assert stats["translated"] == 1 and stats["fallback"] == 1
        assert snaps["a-good"]["title_zh"] == "好"
        assert "title_zh" not in snaps["a-bad"]
        assert session.get(PersonalDigestEditionRecord, edition.id).status == "ready"

        # 幂等:已补的不再调;未配置 LLM 时只做缓存/日报两级,失败的那条仍回退
        calls.clear()
        stats = titles.localize_edition_titles(session, edition, llm_config=LLMConfig())
        assert calls == []
        assert stats == {"chinese": 0, "cached": 0, "public_brief": 0, "translated": 0, "fallback": 1}


def test_budget_timeout_falls_back_without_blocking(storage, monkeypatch):
    async def slow(title, config, usage_meta=None, http_client=None):
        if title == "Slow":
            await asyncio.sleep(5)
        return "快"
    monkeypatch.setattr(titles, "_translate_title", slow)
    with Session(storage.engine) as session:
        edition = _seed(session, {"a-fast": "Fast", "a-slow": "Slow"})
        started = dt.datetime.now()
        stats = titles.localize_edition_titles(session, edition, llm_config=CONFIGURED, budget_seconds=0.3)
        elapsed = (dt.datetime.now() - started).total_seconds()
        snaps = _snapshots(session, edition.id)
    assert elapsed < 3
    assert stats["translated"] == 1 and stats["fallback"] == 1
    assert snaps["a-fast"]["title_zh"] == "快"
    assert "title_zh" not in snaps["a-slow"]


def test_run_async_works_inside_running_loop(monkeypatch):
    async def inner():
        return 42

    async def outer():
        return titles._run_async(inner())

    assert asyncio.run(outer()) == 42


def test_credentialed_private_sources_never_reach_external_llm(storage, monkeypatch):
    """带凭证的自定源条目不送外部 LLM(codex 检视 P1);缓存/日报两级仍可用;孤儿私有源 fail closed。"""
    from models.db import SourceConfigRecord

    calls: list = []
    _fake_translate(monkeypatch, {"Public Title": "公开译名", "Secret Feed Title": "泄露", "Orphan Title": "孤儿"}, calls=calls)
    cached_ext = {TRANSLATION_TITLE_KEY: "私有缓存译名", TRANSLATION_TITLE_FP_KEY: _body_fingerprint("Secret Cached")}
    with Session(storage.engine) as session:
        edition = _seed(session, {
            "a-public": "Public Title",
            "a-secret": "Secret Feed Title",
            "a-secret-cached": ("Secret Cached", cached_ext),
            "a-orphan": "Orphan Title",
        })
        for article_id, source_id in (("a-secret", "user_rss_secret01"), ("a-secret-cached", "user_rss_secret01"), ("a-orphan", "user_rss_orphan01")):
            session.get(ArticleRecord, article_id).source_id = source_id
        session.add(SourceConfigRecord(
            source_id="user_rss_secret01", name="secret", owner_username="alice",
            url="https://feeds.example.com/rss?token=s3cret",
            params_json=json.dumps({"credentialed_private": True}),
            created_at=NOW_ISO, updated_at=NOW_ISO,
        ))
        session.commit()
        stats = titles.localize_edition_titles(session, edition, llm_config=CONFIGURED)
        snaps = _snapshots(session, edition.id)
        secret_ext = json.loads(session.get(ArticleRecord, "a-secret").extensions_json)

    assert calls == [("Public Title", "aux", "personal_digest_title")]
    assert snaps["a-public"]["title_zh"] == "公开译名"
    assert snaps["a-secret-cached"]["title_zh"] == "私有缓存译名"
    assert "title_zh" not in snaps["a-secret"] and "title_zh" not in snaps["a-orphan"]
    assert TRANSLATION_TITLE_KEY not in secret_ext
    assert stats == {"chinese": 0, "cached": 1, "public_brief": 0, "translated": 1, "fallback": 2}
