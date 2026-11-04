"""Reader guide filters use published assets and retain scope/paging/visibility."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from e2e.podcast_fixture import SOURCE, seed_podcasts
from models.db import AppSettingRecord, ArticleRecord
from storage.impl.db_storage import DatabaseStorage
from tests.conftest import seed_default_accounts


@pytest.fixture
def guide_api(monkeypatch, tmp_path):
    import api.app as app_module
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'guides.db'}")
    seed_default_accounts(sink.engine)
    with Session(sink.engine) as session:
        expected = seed_podcasts(session, datetime.now(timezone.utc).replace(tzinfo=None), username="user")
    monkeypatch.setattr(app_module, "db_sink", sink)
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "user", "password": "user"}).status_code == 200
        yield client, sink, expected
    sink.engine.dispose()


@pytest.mark.parametrize("availability", ["any", "readable", "listenable"])
def test_filter_matches_projection_before_paging_and_search(guide_api, availability):
    client, _, expected = guide_api
    ids = {aid for aid, item in expected.items()
           if (item["readable"] or item["listenable"]) if availability == "any" or item[availability]}
    base = {"podcast_guide": availability, "include_total": True, "shape": "podcast", "subscribed_scope": "only"}
    found = []
    for skip in range(0, len(ids), 7):
        response = client.get("/api/articles", params={**base, "skip": skip, "limit": 7})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == len(ids)
        for row in data["items"]:
            podcast = row["podcast"]
            assert "analysis_basis" not in row and "analysis_basis" not in podcast
            assert podcast["premium_guide"]["blog_ready"] == expected[row["id"]]["readable"]
            assert bool(podcast["condensed_audio_url"]) == expected[row["id"]]["listenable"]
        found.extend(row["id"] for row in data["items"])
    assert len(found) == len(set(found)) and set(found) == ids
    searched = client.get("/api/articles", params={**base, "search": "both", "source_id": SOURCE}).json()
    assert searched["total"] == 2
    assert {row["id"] for row in searched["items"]} == {f"{SOURCE}-both-0", f"{SOURCE}-both-1"}
    assert client.get("/api/articles", params={**base, "source_id": "no-such-source"}).json()["total"] == 0


def test_unavailable_assets_and_unscored_rows_are_not_falsely_labeled(guide_api):
    client, _, expected = guide_api
    rows = client.get("/api/articles", params={"source_id": SOURCE, "limit": 100}).json()
    assert len(rows) == len(expected)
    for row in rows:
        assert row["is_premium_podcast"] == expected[row["id"]]["premium"]
    notes = next(row for row in rows if row["id"].endswith("-notes"))
    assert not notes["podcast"]["full_analysis_ready"]
    available = client.get("/api/articles", params={"podcast_guide": "any", "limit": 100}).json()
    assert not ({row["id"] for row in available} & {f"{SOURCE}-{suffix}" for suffix in ("withdrawn", "notes", "unscored", "queued-0", "retry-0")})
    assert client.get("/api/articles", params={"podcast_guide": "invalid"}).status_code == 422


def test_filter_preserves_hidden_source_and_nonpodcast_boundaries(guide_api):
    client, sink, _ = guide_api
    with Session(sink.engine) as session:
        session.add(AppSettingRecord(key="reader_hidden_source_ids", value=f'["{SOURCE}"]'))
        session.commit()
    assert client.get("/api/articles?podcast_guide=any&include_total=true").json()["total"] == 0
    with Session(sink.engine) as session:
        session.get(AppSettingRecord, "reader_hidden_source_ids").value = "[]"
        session.get(ArticleRecord, f"{SOURCE}-both-0").content_type = "rss_article"
        session.commit()
    rows = client.get("/api/articles?podcast_guide=any&limit=100").json()
    assert f"{SOURCE}-both-0" not in {row["id"] for row in rows}
    assert client.get("/api/articles?podcast_guide=any&shape=article&include_total=true").json()["total"] == 0


def test_filter_keeps_unread_and_favorite_scopes_and_ignores_claimed_readiness(guide_api):
    import json
    client, sink, expected = guide_api
    base = {"podcast_guide": "any", "include_total": True, "source_id": SOURCE}
    assert client.get("/api/articles", params={**base, "favorite_scope": "only"}).json()["total"] == 0
    before = client.get("/api/articles", params={**base, "unread_only": True}).json()["total"]
    assert client.post(f"/api/reader/articles/{SOURCE}-both-0/read").status_code == 200
    after = client.get("/api/articles", params={**base, "unread_only": True}).json()["total"]
    assert after == before - 1
    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, f"{SOURCE}-none-0")
        row.extensions_json = json.dumps({"premium_guide": {"status": "ready", "blog_ready": True, "audio_ready": True},
                                          "condensed_audio_url": "https://example.invalid/not-published.mp3"})
        session.add(row)
        session.commit()
    rows = client.get("/api/articles", params={**base, "limit": 100}).json()
    assert rows["total"] == sum(item["readable"] or item["listenable"] for item in expected.values())
    assert f"{SOURCE}-none-0" not in {row["id"] for row in rows["items"]}
