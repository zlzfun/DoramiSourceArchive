"""读者文章点播：评估函数 + HTTP 端点契约（issue #124）。"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import AiUsageRecord, ArticleRecord  # noqa: E402
from services.article_listen_guides import (  # noqa: E402
    ArticleListenStore,
    evaluate_reader_ondemand,
    prepare_ondemand,
    projection_from_extensions,
)
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


STAMP = "2026-09-07T00:00:00+00:00"
HASH = "a" * 64


def _make_sink(tmp_path, name: str) -> DatabaseStorage:
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed_users(engine):
    from models.db import UserRecord
    from services import accounts as accounts_service

    with Session(engine) as session:
        for username, password, role in (
            ("admin", "admin", "admin"),
            ("user", "user", "user"),
        ):
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(password),
                    role=role,
                    is_active=True,
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
        session.commit()


def _configure_llm(engine):
    from services import daily_brief as db

    with Session(engine) as session:
        db.set_setting(session, db.KEY_LLM_BASE_URL, "https://llm.test/v1")
        db.set_setting(session, db.KEY_LLM_API_KEY, "sk-test")
        db.set_setting(session, db.KEY_LLM_MODEL, "test-model")


def _enable_ai_beta(engine, username="user"):
    from services import accounts as accounts_service

    with Session(engine) as session:
        accounts_service.set_ai_beta_enabled(session, username, True)


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def _base_setup(monkeypatch, tmp_path, name: str):
    import api.app as app_module

    sink = _make_sink(tmp_path, name)
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    return app_module, sink


def _seed_article(
    engine,
    *,
    article_id: str = "article-ondemand",
    source_id: str = "rss_safe",
    content: str = "这是一篇足够长的正文，用来点播精简旁白。",
    extensions: dict | None = None,
    content_type: str = "rss_article",
    credentialed: bool = False,
):
    from models.db import SourceConfigRecord

    with Session(engine) as session:
        if session.get(SourceConfigRecord, source_id) is None:
            session.add(
                SourceConfigRecord(
                    source_id=source_id,
                    name="Safe RSS",
                    source_type="rss",
                    url=(
                        "https://feeds.example.test/rss?subscriber=Abc123Def456Ghi789Jkl012"
                        if credentialed
                        else "https://example.test/safe.xml"
                    ),
                    params_json=(
                        '{"credentialed_private": true}' if credentialed else "{}"
                    ),
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
        session.add(
            ArticleRecord(
                id=article_id,
                title="Ondemand article",
                content_type=content_type,
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date=STAMP,
                fetched_date=STAMP,
                has_content=bool(content),
                content=content,
                extensions_json=json.dumps(extensions or {}, ensure_ascii=False),
            )
        )
        session.commit()


def test_evaluate_ready_and_in_progress(tmp_path):
    sink = _make_sink(tmp_path, "eval.db")
    _seed_article(
        sink.engine,
        extensions={
            "listen_guide": {
                "status": "ready",
                "content_hash": HASH,
                "mime": "audio/mpeg",
            }
        },
    )
    assert evaluate_reader_ondemand(
        sink.engine, article_id="article-ondemand", actor="user"
    ) == {"outcome": "ready", "status": "ready"}

    _seed_article(
        sink.engine,
        article_id="article-queued",
        extensions={"listen_guide": {"status": "narrating"}},
    )
    assert evaluate_reader_ondemand(
        sink.engine, article_id="article-queued", actor="user"
    ) == {"outcome": "in_progress", "status": "narrating"}


def test_evaluate_rejects_empty_body_and_podcast(tmp_path):
    from services.article_listen_guides import ArticleListenError

    sink = _make_sink(tmp_path, "eval-reject.db")
    _seed_article(sink.engine, article_id="empty", content="")
    try:
        evaluate_reader_ondemand(sink.engine, article_id="empty", actor="user")
        assert False, "expected ArticleListenError"
    except ArticleListenError as exc:
        assert exc.code == "article_ondemand_no_body"

    _seed_article(
        sink.engine,
        article_id="pod",
        content_type="podcast_episode",
        content="notes",
    )
    try:
        evaluate_reader_ondemand(sink.engine, article_id="pod", actor="user")
        assert False, "expected ArticleListenError"
    except ArticleListenError as exc:
        assert exc.code == "article_ondemand_podcast_use_podcast_api"


def test_prepare_marks_queued_once(tmp_path):
    sink = _make_sink(tmp_path, "prep.db")
    _seed_article(sink.engine)
    first = prepare_ondemand(
        sink.engine, article_id="article-ondemand", actor="user"
    )
    assert first == {
        "outcome": "queued",
        "status": "queued",
        "should_schedule": True,
    }
    second = prepare_ondemand(
        sink.engine, article_id="article-ondemand", actor="user"
    )
    assert second == {
        "outcome": "in_progress",
        "status": "queued",
        "should_schedule": False,
    }


def test_projection_and_store_roundtrip(tmp_path):
    store = ArticleListenStore(tmp_path / "cas")
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)  # 0.1s
    wav = buf.getvalue()
    audio = store.import_bytes(wav, declared_mime="audio/wav")
    assert audio.path.is_file()
    assert store.resolve(audio.content_hash, audio.mime) == audio.path

    proj = projection_from_extensions(
        "article-ondemand",
        {
            "listen_guide": {
                "status": "ready",
                "content_hash": audio.content_hash,
                "mime": audio.mime,
                "duration_seconds": int(round(audio.duration_seconds)),
            }
        },
    )
    assert proj["audio_ready"] is True
    assert proj["audio_url"] == "/api/reader/articles/article-ondemand/listen-audio"
    assert proj["duration_seconds"] >= 0


def test_api_ready_reuses_without_charge(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-ready.db")
    _seed_article(
        sink.engine,
        extensions={
            "listen_guide": {
                "status": "ready",
                "content_hash": HASH,
                "mime": "audio/mpeg",
            }
        },
    )
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_article_listen_guide",
        lambda *a, **k: calls.append(True),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/articles/article-ondemand/ondemand")
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "ready"
    assert body["charged"] is False
    assert body["started"] is False
    assert calls == []

    with Session(sink.engine) as session:
        used = session.exec(
            select(AiUsageRecord).where(AiUsageRecord.purpose == "article_ondemand")
        ).all()
    assert used == []


def test_api_queues_and_charges_once(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-queue.db")
    _seed_article(sink.engine)
    calls = []

    def schedule(article_id, **kwargs):
        calls.append((article_id, kwargs))
        return {
            "outcome": "queued",
            "status": "queued",
            "should_schedule": True,
            "started": True,
        }

    monkeypatch.setattr(app_module, "schedule_article_listen_guide", schedule)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/articles/article-ondemand/ondemand")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "success",
        "article_id": "article-ondemand",
        "outcome": "queued",
        "guide_status": "queued",
        "charged": True,
        "started": True,
    }
    assert calls == [("article-ondemand", {"actor": "user"})]

    with Session(sink.engine) as session:
        rows = session.exec(
            select(AiUsageRecord).where(
                AiUsageRecord.username == "user",
                AiUsageRecord.purpose == "article_ondemand",
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].calls == 1


def test_api_shared_quota_with_podcast_ondemand(monkeypatch, tmp_path):
    from api.routers import reader as reader_router

    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-quota.db")
    _seed_article(sink.engine)
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_article_listen_guide",
        lambda *a, **k: calls.append(True) or {"outcome": "queued", "started": True},
    )

    limit = reader_router._AI_ONDEMAND_DAILY_LIMIT
    today = dt.date.today().isoformat()
    with Session(sink.engine) as session:
        session.add(
            AiUsageRecord(
                day=today,
                username="user",
                purpose="podcast_ondemand",
                model="ondemand",
                calls=limit,
                total_tokens=0,
                updated_at=today,
            )
        )
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/articles/article-ondemand/ondemand")
    assert resp.status_code == 429
    assert calls == []


def test_api_no_body_friendly_message(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-empty.db")
    _seed_article(sink.engine, content="")

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/articles/article-ondemand/ondemand")
    assert resp.status_code == 400
    assert resp.json()["code"] == "article_ondemand_no_body"


def test_api_rejects_podcast_and_credentialed(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-deny.db")
    _seed_article(
        sink.engine,
        article_id="pod-1",
        content_type="podcast_episode",
        content="notes",
    )
    _seed_article(
        sink.engine,
        article_id="cred-1",
        source_id="rss_credentialed",
        credentialed=True,
    )
    calls = []
    monkeypatch.setattr(
        app_module,
        "schedule_article_listen_guide",
        lambda *a, **k: calls.append(True),
    )

    with TestClient(app_module.app) as client:
        _login(client)
        pod = client.post("/api/reader/ai/articles/pod-1/ondemand")
        cred = client.post("/api/reader/ai/articles/cred-1/ondemand")
    assert pod.status_code == 404
    assert cred.status_code == 403
    assert "访问凭证" in cred.json()["detail"]
    assert calls == []


def test_list_item_exposes_listen_guide(monkeypatch, tmp_path):
    from api.articles_view import serialize_article_list_item

    app_module, sink = _base_setup(monkeypatch, tmp_path, "api-proj.db")
    _seed_article(
        sink.engine,
        extensions={
            "listen_guide": {
                "status": "ready",
                "content_hash": HASH,
                "mime": "audio/mpeg",
                "duration_seconds": 180,
            }
        },
    )
    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, "article-ondemand")
        item = serialize_article_list_item(record)
    assert item["listen_guide"]["audio_ready"] is True
    assert item["listen_guide"]["audio_url"].endswith("/listen-audio")
    assert "content_hash" not in item["listen_guide"]
