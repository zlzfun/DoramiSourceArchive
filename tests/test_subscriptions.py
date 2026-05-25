import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def _set_runtime_role(monkeypatch, app_module, role: str):
    from config import RuntimeConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role=role)),
    )


def _seed_article(engine, article_id: str, source_id: str, title: str):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(
            ArticleRecord(
                id=article_id,
                title=title,
                content_type="rss_article",
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date="2026-05-20T00:00:00",
                fetched_date="2026-05-21T00:00:00",
                has_content=True,
                content=f"{title} body",
                extensions_json="{}",
                is_vectorized=False,
            )
        )
        session.commit()


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def test_subscription_tokenized_dify_delivery_filters_articles(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "subscriptions.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article(sink.engine, "openai_1", "rss_openai", "OpenAI Update")
    _seed_article(sink.engine, "hf_1", "rss_huggingface", "Hugging Face Update")

    with TestClient(app_module.app) as client:
        _login(client)
        create_response = client.post(
            "/api/subscriptions",
            json={
                "name": "OpenAI feed",
                "filters": {"source_id": "rss_openai"},
                "delivery_policy": {"include_content": True, "default_limit": 50, "max_limit": 100},
            },
        )
        assert create_response.status_code == 200
        subscription = create_response.json()
        assert subscription["token"].startswith("dsub_")
        assert subscription["token_preview"] == f"...{subscription['token'][-6:]}"

        public_response = client.get(
            f"/api/public/subscriptions/{subscription['id']}/dify/articles",
            headers={"Authorization": f"Bearer {subscription['token']}"},
        )
        assert public_response.status_code == 200
        data = public_response.json()
        assert data["count"] == 1
        assert data["items"][0]["id"] == "openai_1"
        assert data["items"][0]["metadata"]["source_id"] == "rss_openai"
        assert "content" in data["items"][0]


def test_subscription_public_delivery_requires_valid_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "tokens.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        subscription = client.post("/api/subscriptions", json={"name": "Token test"}).json()

        assert client.get(f"/api/public/subscriptions/{subscription['id']}/dify/articles").status_code == 401
        assert client.get(
            f"/api/public/subscriptions/{subscription['id']}/dify/articles?token=wrong"
        ).status_code == 401

        ok = client.get(
            f"/api/public/subscriptions/{subscription['id']}/dify/articles?token={subscription['token']}"
        )
        assert ok.status_code == 200

        missing = client.get("/api/public/subscriptions/999/dify/articles?token=wrong")
        assert missing.status_code == 401


def test_subscription_token_rotation_invalidates_old_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "rotation.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        created = client.post("/api/subscriptions", json={"name": "Rotating"}).json()
        rotated = client.post(f"/api/subscriptions/{created['id']}/rotate-token").json()

        old_response = client.get(
            f"/api/public/subscriptions/{created['id']}/dify/articles?token={created['token']}"
        )
        new_response = client.get(
            f"/api/public/subscriptions/{created['id']}/dify/articles?token={rotated['token']}"
        )

        assert old_response.status_code == 401
        assert new_response.status_code == 200


def test_subscription_delivery_disabled_in_collector_role(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "collector.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_runtime_role(monkeypatch, app_module, "collector")

    with TestClient(app_module.app) as client:
        response = client.get("/api/public/subscriptions/1/dify/articles?token=anything")
        assert response.status_code == 403
