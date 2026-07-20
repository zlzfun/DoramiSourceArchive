"""社交 raw_data 零网络回填：字段保真、幂等、跳过计数与 admin job。"""

import datetime
import json
import os
import sys
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import (  # noqa: E402
    ArticleRecord,
    INDEX_STATUS_INDEXED,
    INDEX_STATUS_STALE,
    UserRecord,
)
from services import accounts as accounts_service  # noqa: E402
from services.social_backfill import backfill_social_posts  # noqa: E402
from services.x_api_config import read_user_cache  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _raw_payload(kind: str, *, post_id: str, author_id: str, handle: str):
    referenced_id = f"{post_id}0"
    referenced_author_id = f"{author_id}0"
    reference_type = "quoted" if kind == "quoted" else "retweeted"
    return {
        "data": {
            "id": post_id,
            "text": (
                "current post" if kind == "quoted" else f"RT @{handle}_original: short"
            ),
            "author_id": author_id,
            "conversation_id": post_id,
            "created_at": "2026-07-20T08:00:00Z",
            "lang": "en",
            "referenced_tweets": [{"type": reference_type, "id": referenced_id}],
            "public_metrics": {"like_count": 2},
        },
        "includes": {
            "users": [
                {
                    "id": author_id,
                    "username": handle,
                    "name": handle.title(),
                    "profile_image_url": f"https://img.test/{handle}_normal.jpg",
                },
                {
                    "id": referenced_author_id,
                    "username": f"{handle}_original",
                    "name": f"{handle.title()} Original",
                    "profile_image_url": (
                        f"https://img.test/{handle}_original_normal.png"
                    ),
                },
            ],
            "tweets": [
                {
                    "id": referenced_id,
                    "author_id": referenced_author_id,
                    "text": "short original",
                    "note_tweet": {"text": f"full {kind} original"},
                    "attachments": {"media_keys": [f"m-{post_id}"]},
                }
            ],
            "media": [
                {
                    "media_key": f"m-{post_id}",
                    "type": "photo",
                    "url": f"https://img.test/media-{post_id}.jpg",
                }
            ],
        },
    }


def _article(
    article_id: str,
    source_id: str,
    extensions_json: str,
    *,
    content: str = "archived body",
    is_vectorized: bool = False,
    index_status: str = INDEX_STATUS_STALE,
):
    return ArticleRecord(
        id=article_id,
        title=article_id,
        content_type="social_post",
        source_id=source_id,
        source_url=f"https://x.com/i/web/status/{article_id}",
        publish_date="2026-07-20T08:00:00Z",
        fetched_date="2026-07-20T08:01:00Z",
        content=content,
        extensions_json=extensions_json,
        is_vectorized=is_vectorized,
        index_status=index_status,
    )


def _seed_backfill_cases(engine):
    quoted_raw = _raw_payload(
        "quoted", post_id="101", author_id="1", handle="quoted_source"
    )
    reposted_raw = _raw_payload(
        "reposted", post_id="202", author_id="2", handle="reposted_source"
    )
    with Session(engine) as session:
        session.add(
            _article(
                "x_quote_101",
                "x_quote",
                json.dumps({"author_handle": "quoted_source", "raw_data": quoted_raw}),
                content="quote body must stay byte-identical",
                is_vectorized=True,
                index_status=INDEX_STATUS_INDEXED,
            )
        )
        session.add(
            _article(
                "x_repost_202",
                "x_repost",
                json.dumps(
                    {"author_handle": "reposted_source", "raw_data": reposted_raw}
                ),
                content="repost body must stay byte-identical",
            )
        )
        session.add(_article("missing", "x_missing", "{}"))
        session.add(
            _article(
                "invalid_raw",
                "x_invalid",
                json.dumps({"raw_data": {"data": [], "includes": {}}}),
            )
        )
        session.add(_article("invalid_json", "x_invalid_json", "{broken"))
        session.commit()
    return quoted_raw, reposted_raw


class _ForbiddenAsyncClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("social backfill must not construct an HTTP client")


def test_social_backfill_is_local_idempotent_and_preserves_archive_state(
    monkeypatch, tmp_path
):
    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'backfill.db'}")
    quoted_raw, reposted_raw = _seed_backfill_cases(sink.engine)
    monkeypatch.setattr(httpx, "AsyncClient", _ForbiddenAsyncClient)

    first = backfill_social_posts(sink.engine)
    assert first == {
        "articles_scanned": 5,
        "articles_processed": 2,
        "extensions_updated": 2,
        "extensions_unchanged": 0,
        "skipped_total": 3,
        "skipped_missing_raw": 1,
        "skipped_invalid_extensions": 1,
        "skipped_invalid_raw": 1,
        "records_with_avatar": 2,
        "quoted_records": 1,
        "reposted_records": 1,
        "sources_with_avatar": 2,
        "user_caches_updated": 2,
    }

    with Session(sink.engine) as session:
        quoted = session.get(ArticleRecord, "x_quote_101")
        reposted = session.get(ArticleRecord, "x_repost_202")
        quoted_ext = json.loads(quoted.extensions_json)
        reposted_ext = json.loads(reposted.extensions_json)
        assert quoted.content == "quote body must stay byte-identical"
        assert quoted.is_vectorized is True
        assert quoted.index_status == INDEX_STATUS_INDEXED
        assert reposted.content == "repost body must stay byte-identical"
        assert reposted.is_vectorized is False
        assert reposted.index_status == INDEX_STATUS_STALE
        assert quoted_ext["raw_data"] == quoted_raw
        assert reposted_ext["raw_data"] == reposted_raw
        assert quoted_ext["author_avatar_url_large"].endswith(
            "/quoted_source_400x400.jpg"
        )
        assert quoted_ext["quoted"]["text"] == "full quoted original"
        assert quoted_ext["quoted"]["author_avatar_url_large"].endswith(
            "/quoted_source_original_400x400.png"
        )
        assert "reposted" not in quoted_ext
        assert reposted_ext["reposted"]["text"] == "full reposted original"
        assert "quoted" not in reposted_ext
        quote_cache = read_user_cache(session, "x_quote", handle="quoted_source")
        repost_cache = read_user_cache(
            session, "x_repost", handle="reposted_source"
        )
        assert quote_cache["author_avatar_url_large"].endswith(
            "/quoted_source_400x400.jpg"
        )
        assert repost_cache["author_avatar_url_large"].endswith(
            "/reposted_source_400x400.jpg"
        )
        first_extensions = {
            quoted.id: quoted.extensions_json,
            reposted.id: reposted.extensions_json,
        }
        first_caches = {"quote": dict(quote_cache), "repost": dict(repost_cache)}

    second = backfill_social_posts(sink.engine)
    assert second["extensions_updated"] == 0
    assert second["extensions_unchanged"] == 2
    assert second["skipped_total"] == 3
    assert second["user_caches_updated"] == 0
    with Session(sink.engine) as session:
        assert session.get(ArticleRecord, "x_quote_101").extensions_json == (
            first_extensions["x_quote_101"]
        )
        assert session.get(ArticleRecord, "x_repost_202").extensions_json == (
            first_extensions["x_repost_202"]
        )
        assert read_user_cache(session, "x_quote") == first_caches["quote"]
        assert read_user_cache(session, "x_repost") == first_caches["repost"]


def _setup_app(monkeypatch, tmp_path):
    import api.app as app_module
    from config import RuntimeConfig

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'endpoint.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role="all")),
    )
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for username, password, role in (
            ("admin", "admin", "admin"),
            ("user", "user", "user"),
        ):
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(password),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        raw = _raw_payload(
            "quoted", post_id="303", author_id="3", handle="endpoint_source"
        )
        session.add(
            _article(
                "x_endpoint_303",
                "x_endpoint",
                json.dumps({"author_handle": "endpoint_source", "raw_data": raw}),
            )
        )
        session.add(_article("endpoint_missing", "x_missing", "{}"))
        session.commit()
    return app_module


def _login(client, username, password):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def test_admin_social_backfill_job_is_gated_and_never_constructs_http_client(
    monkeypatch, tmp_path
):
    app_module = _setup_app(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user", "user")
        assert client.post("/api/admin/social/backfill").status_code == 403

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        # TestClient 使用同步 httpx.Client；这里禁掉全部 AsyncClient 构造，若回填
        # 意外进入 X/media/任意异步网络路径，后台 job 会直接失败。
        monkeypatch.setattr(httpx, "AsyncClient", _ForbiddenAsyncClient)
        response = client.post("/api/admin/social/backfill")
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        final = None
        for _ in range(200):
            final = client.get(f"/api/jobs/{job_id}").json()
            if final["status"] in ("succeeded", "failed"):
                break
        assert final["status"] == "succeeded", final
        assert final["type"] == "social_backfill"
        assert final["total"] == 2 and final["processed"] == 2
        assert final["result"]["articles_processed"] == 1
        assert final["result"]["extensions_updated"] == 1
        assert final["result"]["skipped_total"] == 1
        assert final["result"]["quoted_records"] == 1
        assert final["result"]["user_caches_updated"] == 1

        sources = {
            item["source_id"]: item
            for item in client.get("/api/reader/sources").json()["sources"]
        }
        assert sources["x_endpoint"]["avatar_url"].endswith(
            "/endpoint_source_400x400.jpg"
        )

