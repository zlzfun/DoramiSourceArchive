"""读者 AI translate/ask 逐用户每日配额（限流）回归测试。

配额以当日 AiUsageRecord.calls（底层 LLM 调用次数）聚合判定：达上限即 429，
且请求前置拦截（不再触发 LLM 调用）；admin 不豁免；按用途各自独立计数。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    from models.db import UserRecord
    from services import accounts as accounts_service

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in accounts:
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed_article(engine, article_id, source_id, title, content="正文内容"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=title,
            content_type="rss_article",
            source_id=source_id,
            source_url=f"https://example.test/{article_id}",
            publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00",
            has_content=True,
            content=content,
            extensions_json="{}",
            is_vectorized=False,
        ))
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


def _seed_ai_usage(engine, username, purpose, calls, day=None):
    """预置当日某用途的 calls 计数，用于把配额顶到上限。"""
    from models.db import AiUsageRecord

    day = day or datetime.date.today().isoformat()
    with Session(engine) as session:
        session.add(AiUsageRecord(
            day=day,
            username=username,
            purpose=purpose,
            model="test-model",
            calls=calls,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            updated_at=datetime.datetime.now().isoformat(),
        ))
        session.commit()


def _patch_llm(monkeypatch):
    import services.reader_ai as rai

    calls = []

    async def fake_chat_completion(*, messages, config, **kwargs):
        calls.append([m.content for m in messages])
        return "AI-MOCK-OUTPUT"

    monkeypatch.setattr(rai, "chat_completion", fake_chat_completion)
    return calls


def _base_setup(monkeypatch, tmp_path, name):
    import api.app as app_module

    sink = _make_sink(tmp_path, name)
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    return app_module, sink


# ──────────────────────────────────────────────────────────────

def test_translate_429_when_daily_quota_exhausted(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "limit_translate.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_article(sink.engine, "a1", "rss_x", "Title", "Hello world body")
    llm_calls = _patch_llm(monkeypatch)
    # 顶满 translate 当日配额（默认 50）
    _seed_ai_usage(sink.engine, "user", "translate", 50)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/translate", json={"article_id": "a1"})
        assert resp.status_code == 429
        # 前置拦截：未触发任何 LLM 调用
        assert llm_calls == []


def test_ask_429_when_daily_quota_exhausted(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "limit_ask.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_article(sink.engine, "a1", "rss_x", "标题", "正文")
    llm_calls = _patch_llm(monkeypatch)
    _seed_ai_usage(sink.engine, "user", "ask", 100)

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post(
            "/api/reader/ai/ask",
            json={"question": "讲了什么？", "scope": "article", "article_id": "a1"},
        )
        assert resp.status_code == 429
        assert llm_calls == []


def test_translate_below_quota_ok(monkeypatch, tmp_path):
    app_module, sink = _base_setup(monkeypatch, tmp_path, "under_translate.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_article(sink.engine, "a1", "rss_x", "Title", "Hello world body")
    _patch_llm(monkeypatch)
    _seed_ai_usage(sink.engine, "user", "translate", 49)  # 差一次到上限

    with TestClient(app_module.app) as client:
        _login(client)
        resp = client.post("/api/reader/ai/translate", json={"article_id": "a1"})
        assert resp.status_code == 200


def test_quota_is_per_purpose(monkeypatch, tmp_path):
    """translate 顶满不影响 ask（按用途各自计数）。"""
    app_module, sink = _base_setup(monkeypatch, tmp_path, "per_purpose.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine)
    _seed_article(sink.engine, "a1", "rss_x", "标题", "正文")
    _patch_llm(monkeypatch)
    _seed_ai_usage(sink.engine, "user", "translate", 50)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post(
            "/api/reader/ai/translate", json={"article_id": "a1"}
        ).status_code == 429
        assert client.post(
            "/api/reader/ai/ask",
            json={"question": "问？", "scope": "article", "article_id": "a1"},
        ).status_code == 200


def test_admin_not_exempt_from_quota(monkeypatch, tmp_path):
    """admin 也走这两个端点，不豁免限流。"""
    app_module, sink = _base_setup(monkeypatch, tmp_path, "admin_limit.db")
    _configure_llm(sink.engine)
    _enable_ai_beta(sink.engine, "admin")
    _seed_article(sink.engine, "a1", "rss_x", "Title", "Hello world body")
    _patch_llm(monkeypatch)
    _seed_ai_usage(sink.engine, "admin", "translate", 50)

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        resp = client.post("/api/reader/ai/translate", json={"article_id": "a1"})
        assert resp.status_code == 429
