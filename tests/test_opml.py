"""读者侧 OPML 导出与导入(issue #179)。

导出只含本人订阅里的真实 RSS/播客 feed,非 RSS 跳过,XML 可解析且转义。
导入默认不写库;确认后命中公共源走订阅,新地址走自定源创建路径;重复导入幂等。
"""

import os
import sys
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_ACCOUNTS = (("admin", "admin", "admin"), ("alice", "alice", "user"), ("bob", "bob", "user"))

_ALICE_FEED = "https://alice.example.com/feed"
_BOB_FEED = "https://bob.example.com/feed"
_NEW_FEED = "https://new.example.com/feed"
_BAD_FEED = "https://bad.example/not-a-feed"
_HF_FEED = "https://huggingface.co/blog/feed.xml"
_OPENAI_FEED = "https://openai.com/news/rss.xml"
_PODCAST_FEED = "https://podcast.example.com/show.xml"


def _setup_app(monkeypatch, tmp_path, db_name="app_opml.db"):
    import api.app as app_module
    from config import RuntimeConfig

    sink = __import__("storage.impl.db_storage", fromlist=["DatabaseStorage"]).DatabaseStorage(
        db_url=f"sqlite:///{tmp_path / db_name}"
    )
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace_settings(app_module)
    )
    seed_default_accounts(sink.engine, _ACCOUNTS)

    calls = []

    async def _fake_preview(url, **kwargs):
        calls.append(url)
        if "bad.example" in url:
            raise ValueError("无法从该地址解析出任何条目,请确认这是一个 RSS/Atom feed")
        return {
            "canonical_url": url,
            "feed_title": "Imported Blog",
            "entry_count": 1,
            "entries": [{"title": "Post", "publish_date": "", "content_chars": 20}],
            "detected_kind": "article",
            "detection_confidence": "medium",
            "detection_reasons": [],
            "audio_entry_count": 0,
            "enclosure_entry_count": 0,
            "playable_entry_count": 0,
        }

    from services import user_sources as user_sources_service

    monkeypatch.setattr(user_sources_service, "fetch_feed_preview", _fake_preview)

    async def _fake_single_fetch(*args, **kwargs):
        return {"status": "success", "results": []}

    monkeypatch.setattr(app_module, "run_single_fetch_as_collection", _fake_single_fetch)
    return app_module, calls


def replace_settings(app_module):
    from config import RuntimeConfig
    from dataclasses import replace

    return replace(app_module.settings, runtime=RuntimeConfig(role="all"))


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res


def _add(client, url, name=None):
    payload = {"url": url}
    if name is not None:
        payload["name"] = name
    res = client.post("/api/reader/custom-sources", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def _outline_urls(xml_text):
    root = ET.fromstring(xml_text)
    return [element.get("xmlUrl") or "" for element in root.iter("outline")]


def _config_count(engine):
    from models.db import SourceConfigRecord

    with Session(engine) as session:
        return len(session.exec(select(SourceConfigRecord)).all())


def _opml(rows):
    parts = []
    for row in rows:
        title, url = row[0], row[1]
        extra = f' category="{row[2]}"' if len(row) > 2 and row[2] else ""
        parts.append(f'<outline text="{title}" type="rss" xmlUrl="{url}"{extra}/>')
    body = "\n".join(parts)
    return f'<?xml version="1.0" encoding="UTF-8"?><opml version="2.0"><body>{body}</body></opml>'


# ==================== 纯函数 ====================

def test_render_opml_is_escaped_and_marks_podcasts():
    from services.opml import render_opml

    xml = render_opml([{
        "name": 'Tom & Jerry <show> "live"',
        "xml_url": "https://example.com/feed?a=1&b=2",
        "html_url": "",
        "podcast": True,
    }])
    root = ET.fromstring(xml)
    assert root.tag == "opml"
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    outline = next(root.iter("outline"))
    assert outline.get("text") == 'Tom & Jerry <show> "live"'
    assert outline.get("xmlUrl") == "https://example.com/feed?a=1&b=2"
    assert outline.get("category") == "podcast"
    assert "Tom &amp; Jerry &lt;show&gt;" in xml
    assert "a=1&amp;b=2" in xml
    assert "<show>" not in xml.split(">", 1)[-1]


def test_parse_opml_flattens_folders_and_rejects_over_limit():
    from services.opml import parse_opml_outlines

    text = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="文件夹">
        <outline text="One" xmlUrl="https://one.example/feed"/>
      </outline>
      <outline text="Two" xmlurl="https://two.example/atom"/>
    </body></opml>"""
    outlines = parse_opml_outlines(text)
    assert [item["xml_url"] for item in outlines] == [
        "https://one.example/feed",
        "https://two.example/atom",
    ]
    assert outlines[0]["title"] == "One"

    too_many = "".join(
        f'<outline text="n{i}" xmlUrl="https://example.com/{i}.xml"/>'
        for i in range(201)
    )
    with pytest.raises(ValueError, match="200"):
        parse_opml_outlines(
            f'<?xml version="1.0"?><opml version="2.0"><body>{too_many}</body></opml>'
        )
    with pytest.raises(ValueError, match="不是有效的 OPML"):
        parse_opml_outlines("<html><body>nope</body></html>")


def test_parse_opml_rejects_malformed_oversized_and_entities():
    from services.opml import OPML_MAX_CHARS, parse_opml_outlines

    with pytest.raises(ValueError, match="不是有效的 OPML 文件"):
        parse_opml_outlines("<opml><body>")
    with pytest.raises(ValueError, match="OPML 文件过大"):
        parse_opml_outlines("x" * (OPML_MAX_CHARS + 1))

    external = """<?xml version="1.0"?>
    <!DOCTYPE opml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <opml version="2.0"><body>
      <outline text="&xxe;" xmlUrl="https://example.com/feed"/>
    </body></opml>"""
    with pytest.raises(ValueError, match="不是有效的 OPML 文件"):
        parse_opml_outlines(external)

    billion = """<?xml version="1.0"?>
    <!DOCTYPE opml [
      <!ENTITY a "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">
    ]>
    <opml version="2.0"><body>
      <outline text="&b;" xmlUrl="https://example.com/feed"/>
    </body></opml>"""
    with pytest.raises(ValueError, match="不是有效的 OPML 文件"):
        parse_opml_outlines(billion)

    public_doctype = """<?xml version="1.0"?>
    <!DOCTYPE opml PUBLIC "-//OPML//DTD OPML 2.0//EN" "http://www.opml.org/spec2.opml">
    <opml version="2.0"><body>
      <outline text="Kept" xmlUrl="https://example.com/feed"/>
    </body></opml>"""
    outlines = parse_opml_outlines(public_doctype)
    assert outlines[0]["xml_url"] == "https://example.com/feed"
    assert outlines[0]["title"] == "Kept"


# ==================== 导出 ====================

def test_export_contains_only_the_readers_own_rss(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_export.db")
    from fetchers.registry import fetcher_registry
    from models.db import SourceConfigRecord

    web = fetcher_registry.get_class("web_anthropic_news")
    trending = fetcher_registry.get_class("github_trending_daily")
    assert not getattr(web, "feed_url", "")
    assert not getattr(trending, "feed_url", "")

    with TestClient(app_module.app) as client:
        anonymous = client.get("/api/reader/sources/export.opml")
        assert anonymous.status_code == 401
        assert client.get("/api/reader/sources/opml").status_code == 401
        assert client.post("/api/reader/sources/import", json={"opml": "<opml/>"}).status_code == 401

        _login(client, "alice", "alice")
        alice_source = _add(client, _ALICE_FEED, name='Tom & Jerry <show>')
        subscribed = client.post("/api/reader/sources/github_trending_daily/subscribe")
        assert subscribed.status_code == 200

        now = "2026-10-13T00:00:00"
        with Session(app_module.db_sink.engine) as session:
            session.add(SourceConfigRecord(
                source_id="podcast_opml_show",
                name="OPML 测试播客",
                source_type="podcast",
                url=_PODCAST_FEED,
                fetcher_id="generic_podcast_rss",
                owner_username="",
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            session.commit()
        podcast_sub = client.post("/api/reader/sources/podcast_opml_show/subscribe")
        assert podcast_sub.status_code == 200

        client.cookies.clear()
        _login(client, "bob", "bob")
        _add(client, _BOB_FEED, name="Bob Only")
        bob_hf = client.post("/api/reader/sources/rss_hf_blog/subscribe")
        assert bob_hf.status_code == 200
        bob_export = client.get("/api/reader/sources/export.opml")
        assert bob_export.status_code == 200
        bob_urls = set(_outline_urls(bob_export.text))

        client.cookies.clear()
        _login(client, "alice", "alice")
        summary = client.get("/api/reader/sources/opml")
        assert summary.status_code == 200
        assert "no-store" in summary.headers["cache-control"]
        exported = client.get("/api/reader/sources/export.opml")
        assert exported.status_code == 200
        assert "xml" in exported.headers["content-type"]
        assert "dorami-subscriptions.opml" in exported.headers["content-disposition"]
        xml = exported.text
        urls = _outline_urls(xml)
        assert int(exported.headers["x-dorami-opml-exportable"]) == len(urls)
        assert int(exported.headers["x-dorami-opml-skipped"]) == summary.json()["skipped_count"]
        assert summary.json()["exportable_count"] == len(urls)
        assert int(exported.headers["x-dorami-opml-skipped"]) >= 1

        assert _ALICE_FEED in urls
        assert _BOB_FEED not in urls
        assert _BOB_FEED not in xml
        assert _HF_FEED not in urls
        assert web.listing_url not in urls
        assert trending.source_url not in urls
        assert trending.trending_url not in urls
        assert "Tom &amp; Jerry &lt;show&gt;" in xml
        assert "<show>" not in xml
        podcast = next(element for element in ET.fromstring(xml).iter("outline") if element.get("xmlUrl") == _PODCAST_FEED)
        assert podcast.get("category") == "podcast"
        assert podcast.get("type") == "rss"

        assert _BOB_FEED in bob_urls
        assert _ALICE_FEED not in bob_urls
        assert _HF_FEED in bob_urls
        assert alice_source["source_id"].startswith("user_rss_")


# ==================== 导入 ====================

def test_import_dry_run_matches_and_confirm_is_idempotent(monkeypatch, tmp_path):
    app_module, calls = _setup_app(monkeypatch, tmp_path, "opml_import.db")
    from models.db import SourceConfigRecord
    from services.user_sources import canonical_feed_url

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        before_configs = _config_count(app_module.db_sink.engine)
        before_subs = set(client.get("/api/reader/sources").json()["subscribed_source_ids"])
        opml = _opml([
            ("New Blog", _NEW_FEED),
            ("New Blog again", _NEW_FEED + "/"),
            ("Hugging Face Blog", _HF_FEED),
            ("OpenAI News", _OPENAI_FEED),
            ("Old Blog", _BAD_FEED),
            ("intranet", "http://127.0.0.1/feed"),
            ("lan", "http://10.1.2.3/secret.xml"),
            ("not http", "ftp://files.example/feed"),
        ])
        preview = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": True})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["dry_run"] is True
        assert body["written"] is False
        assert body["duplicate_count"] == 1
        assert _config_count(app_module.db_sink.engine) == before_configs
        assert set(client.get("/api/reader/sources").json()["subscribed_source_ids"]) == before_subs

        by_url = {item["canonical_url"] or item["xml_url"]: item for item in body["items"]}
        assert by_url[canonical_feed_url(_NEW_FEED)]["status"] == "new"
        assert by_url[canonical_feed_url(_NEW_FEED)]["selectable"] is True
        assert by_url[canonical_feed_url(_HF_FEED)]["status"] == "matched_existing"
        assert by_url[canonical_feed_url(_HF_FEED)]["source_id"] == "rss_hf_blog"
        assert by_url[_OPENAI_FEED]["status"] == "already_subscribed"
        assert by_url[_OPENAI_FEED]["selectable"] is False
        assert by_url[_BAD_FEED]["status"] == "invalid"
        assert by_url["http://127.0.0.1/feed"]["status"] == "rejected_ssrf"
        assert by_url["http://10.1.2.3/secret.xml"]["status"] == "rejected_ssrf"
        assert by_url["ftp://files.example/feed"]["status"] == "invalid"
        assert not any("127.0.0.1" in url or url.startswith("http://10.") for url in calls)
        assert len(body["items"]) == 7

        selected = [
            canonical_feed_url(_NEW_FEED),
            canonical_feed_url(_HF_FEED),
            "http://127.0.0.1/feed",
        ]
        confirmed = client.post("/api/reader/sources/import", json={
            "opml": opml,
            "dry_run": False,
            "urls": selected,
        })
        assert confirmed.status_code == 200, confirmed.text
        done = confirmed.json()
        assert done["dry_run"] is False
        assert done["created_count"] == 1
        assert done["subscribed_count"] == 1
        assert done["written"] is True
        after = set(client.get("/api/reader/sources").json()["subscribed_source_ids"])
        assert "rss_hf_blog" in after
        created = [item for item in done["items"] if item["canonical_url"] == canonical_feed_url(_NEW_FEED)]
        assert created[0]["status"] == "already_subscribed"
        assert created[0]["source_id"].startswith("user_rss_")

        with Session(app_module.db_sink.engine) as session:
            rows = session.exec(select(SourceConfigRecord)).all()
            new_rows = [row for row in rows if row.url == canonical_feed_url(_NEW_FEED)]
            hf_rows = [row for row in rows if "huggingface.co" in (row.url or "")]
        assert len(new_rows) == 1
        assert new_rows[0].owner_username == "alice"
        assert hf_rows == []

        again = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": True})
        assert again.status_code == 200
        again_by_url = {
            item["canonical_url"] or item["xml_url"]: item for item in again.json()["items"]
        }
        assert again_by_url[canonical_feed_url(_NEW_FEED)]["status"] == "already_subscribed"
        assert again_by_url[canonical_feed_url(_HF_FEED)]["status"] == "already_subscribed"
        configs_after_first = _config_count(app_module.db_sink.engine)
        subs_after_first = set(client.get("/api/reader/sources").json()["subscribed_source_ids"])

        repeat = client.post("/api/reader/sources/import", json={
            "opml": opml,
            "dry_run": False,
            "urls": selected,
        })
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["created_count"] == 0
        assert repeat.json()["subscribed_count"] == 0
        assert repeat.json()["written"] is False
        assert _config_count(app_module.db_sink.engine) == configs_after_first
        assert set(client.get("/api/reader/sources").json()["subscribed_source_ids"]) == subs_after_first


def test_import_rejects_over_limit_without_writing(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_limit.db")
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        before = _config_count(app_module.db_sink.engine)
        rows = [(f"Feed {i}", f"https://feeds.example.com/{i}.xml") for i in range(201)]
        res = client.post("/api/reader/sources/import", json={"opml": _opml(rows)})
        assert res.status_code == 400
        assert "200" in res.json()["detail"]
        assert _config_count(app_module.db_sink.engine) == before


def test_import_empty_selection_writes_nothing(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_empty_selection.db")
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        before_subs = set(client.get("/api/reader/sources").json()["subscribed_source_ids"])
        opml = _opml([("New Blog", _NEW_FEED)])
        res = client.post("/api/reader/sources/import", json={
            "opml": opml,
            "dry_run": False,
            "urls": [],
        })
        assert res.status_code == 200, res.text
        assert res.json()["written"] is False
        assert res.json()["created_count"] == 0
        assert set(client.get("/api/reader/sources").json()["subscribed_source_ids"]) == before_subs


def _item_map(body):
    return {item["canonical_url"] or item["xml_url"]: item for item in body["items"]}


def test_preview_marks_both_quota_caps_and_confirm_continues(monkeypatch, tmp_path):
    """总数与当日新增都要在预览里卡住;确认时单条配额失败不让整批 400/429。"""
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_quota.db")
    from services import user_sources
    from services.user_sources import canonical_feed_url

    monkeypatch.setattr(user_sources, "MAX_SOURCES_PER_USER", 20)
    monkeypatch.setattr(user_sources, "DAILY_ADD_LIMIT", 1)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        daily_opml = _opml([
            ("First", "https://quota.example.com/first.xml"),
            ("Second", "https://quota.example.com/second.xml"),
        ])
        preview = client.post("/api/reader/sources/import", json={"opml": daily_opml, "dry_run": True})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        quota = body["quota"]
        assert quota["daily_max"] == 1
        assert quota["max"] == 20
        assert quota["remaining"] == 1
        by_url = _item_map(body)
        first = canonical_feed_url("https://quota.example.com/first.xml")
        second = canonical_feed_url("https://quota.example.com/second.xml")
        assert by_url[first]["status"] == "new"
        assert by_url[first]["selectable"] is True
        assert by_url[second]["status"] == "invalid"
        assert by_url[second]["selectable"] is False
        assert by_url[second]["reason"] == "今日新增自定源已达上限,请明天再试"

        confirmed = client.post("/api/reader/sources/import", json={
            "opml": daily_opml,
            "dry_run": False,
            "urls": [first, second],
        })
        assert confirmed.status_code == 200, confirmed.text
        done = confirmed.json()
        assert done["created_count"] == 1
        assert done["subscribed_count"] == 0
        done_by_url = _item_map(done)
        assert done_by_url[first]["status"] == "already_subscribed"
        assert done_by_url[second]["status"] == "invalid"
        assert done["quota"]["remaining"] == 0

    monkeypatch.setattr(user_sources, "DAILY_ADD_LIMIT", 10)
    monkeypatch.setattr(user_sources, "MAX_SOURCES_PER_USER", 1)
    with TestClient(app_module.app) as client:
        _login(client, "bob", "bob")
        total_opml = _opml([
            ("One", "https://quota.example.com/one.xml"),
            ("Two", "https://quota.example.com/two.xml"),
        ])
        preview = client.post("/api/reader/sources/import", json={"opml": total_opml, "dry_run": True})
        assert preview.status_code == 200, preview.text
        by_url = _item_map(preview.json())
        one = canonical_feed_url("https://quota.example.com/one.xml")
        two = canonical_feed_url("https://quota.example.com/two.xml")
        assert preview.json()["quota"]["remaining"] == 1
        assert by_url[one]["status"] == "new"
        assert by_url[two]["status"] == "invalid"
        assert by_url[two]["reason"] == "自定源数量已达上限(1 个)"


def test_confirm_quota_error_is_per_row(monkeypatch, tmp_path):
    """写入阶段抛出的 400/429 只作废这一行,同文件里其余新建和已收录订阅继续。"""
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_quota_row.db")
    from services import user_sources
    from services.user_sources import canonical_feed_url

    real_prepare = user_sources.prepare_user_source

    def _prepare(session, username, url, name="", content_kind="article"):
        if url == canonical_feed_url("https://quota.example.com/blocked.xml"):
            raise user_sources.UserSourceQuotaError(
                "今日新增自定源已达上限,请明天再试", status_code=429,
            )
        if url == canonical_feed_url("https://quota.example.com/capped.xml"):
            raise user_sources.UserSourceQuotaError(
                "自定源数量已达上限(20 个)", status_code=400,
            )
        return real_prepare(session, username, url, name=name, content_kind=content_kind)

    monkeypatch.setattr(user_sources, "prepare_user_source", _prepare)
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        opml = _opml([
            ("Kept", "https://quota.example.com/kept.xml"),
            ("Daily", "https://quota.example.com/blocked.xml"),
            ("Total", "https://quota.example.com/capped.xml"),
            ("Hugging Face Blog", _HF_FEED),
        ])
        confirmed = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": False})
        assert confirmed.status_code == 200, confirmed.text
        done = confirmed.json()
        assert done["created_count"] == 1
        assert done["subscribed_count"] == 1
        by_url = _item_map(done)
        assert by_url[canonical_feed_url("https://quota.example.com/kept.xml")]["status"] == "already_subscribed"
        assert by_url[canonical_feed_url("https://quota.example.com/blocked.xml")]["reason"] == "今日新增自定源已达上限,请明天再试"
        assert by_url[canonical_feed_url("https://quota.example.com/capped.xml")]["reason"] == "自定源数量已达上限(20 个)"
        assert by_url[canonical_feed_url(_HF_FEED)]["status"] == "already_subscribed"
        assert "rss_hf_blog" in client.get("/api/reader/sources").json()["subscribed_source_ids"]


def test_hidden_and_disabled_sources_stay_neutral(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_hidden.db")
    from fetchers.registry import fetcher_registry
    from models.db import SourceConfigRecord
    from services import source_visibility

    hf = fetcher_registry.get_class("rss_hf_blog")
    hidden_name = hf.name
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        subscribed = client.post("/api/reader/sources/rss_openai_news/subscribe")
        assert subscribed.status_code == 200, subscribed.text
        created = _add(client, "https://disabled.example.com/feed.xml", name="Secret Disabled Name")
        with Session(app_module.db_sink.engine) as session:
            source_visibility.set_source_hidden(session, "rss_hf_blog", True)
            source_visibility.set_source_hidden(session, "rss_openai_news", True)
            record = session.get(SourceConfigRecord, created["source_id"])
            record.is_active = False
            session.add(record)
            session.commit()

        exported = client.get("/api/reader/sources/export.opml")
        assert exported.status_code == 200
        urls = set(_outline_urls(exported.text))
        assert _OPENAI_FEED in urls
        assert _HF_FEED not in urls
        assert "https://disabled.example.com/feed.xml" in urls
        assert "隐藏" not in exported.text
        assert "停用" not in exported.text

        opml = _opml([
            ("HF", _HF_FEED),
            ("Disabled", "https://disabled.example.com/feed.xml"),
        ])
        preview = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": True})
        assert preview.status_code == 200, preview.text
        assert hidden_name not in preview.text
        assert "Secret Disabled Name" not in preview.text
        assert "隐藏" not in preview.text
        by_url = _item_map(preview.json())
        for item in by_url.values():
            assert item["status"] == "invalid"
            assert item["reason"] == "该来源暂不可用"
            assert item["matched_name"] == ""
            assert item["source_id"] == ""
            assert item["selectable"] is False


def test_confirm_rejects_url_swapped_to_internal_address(monkeypatch, tmp_path):
    app_module, calls = _setup_app(monkeypatch, tmp_path, "opml_swap.db")
    from services.user_sources import canonical_feed_url

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        public = _opml([("Blog", _NEW_FEED)])
        preview = client.post("/api/reader/sources/import", json={"opml": public, "dry_run": True})
        assert preview.status_code == 200
        assert preview.json()["items"][0]["status"] == "new"
        before = _config_count(app_module.db_sink.engine)
        before_subs = set(client.get("/api/reader/sources").json()["subscribed_source_ids"])
        calls.clear()
        swapped = _opml([("Blog", "http://127.0.0.1/admin")])
        confirmed = client.post("/api/reader/sources/import", json={
            "opml": swapped,
            "dry_run": False,
            "urls": [canonical_feed_url(_NEW_FEED), "http://127.0.0.1/admin"],
        })
        assert confirmed.status_code == 200, confirmed.text
        done = confirmed.json()
        assert done["written"] is False
        assert done["created_count"] == 0
        assert done["items"][0]["status"] == "rejected_ssrf"
        assert done["items"][0]["reason"] == "内网地址，出于安全原因不可添加"
        assert _config_count(app_module.db_sink.engine) == before
        assert set(client.get("/api/reader/sources").json()["subscribed_source_ids"]) == before_subs
        assert not any("127.0.0.1" in url for url in calls)


def test_import_malformed_opml_is_invalid_file(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_xml.db")
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        res = client.post("/api/reader/sources/import", json={"opml": "<opml><body>"})
        assert res.status_code == 400
        assert res.json()["detail"] == "不是有效的 OPML 文件"
        external = (
            '<?xml version="1.0"?><!DOCTYPE opml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<opml version="2.0"><body><outline text="&xxe;" xmlUrl="https://example.com/x"/></body></opml>'
        )
        rejected = client.post("/api/reader/sources/import", json={"opml": external})
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "不是有效的 OPML 文件"
        assert "root:" not in rejected.text


def test_export_keeps_credentialed_feed_urls(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_creds.db")
    from models.db import SourceConfigRecord

    token_url = "https://feeds.example.com/private.xml?token=secret&x=1"
    userinfo_url = "https://alice:s3cret@feeds.example.com/private.xml"
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        _add(client, token_url, name="Token Feed")
        now = "2026-10-13T00:00:00"
        with Session(app_module.db_sink.engine) as session:
            session.add(SourceConfigRecord(
                source_id="user_rss_opmlcred",
                name="Userinfo Feed",
                source_type="rss",
                url=userinfo_url,
                fetcher_id="",
                owner_username="alice",
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            session.commit()
            app_module._create_single_source_subscription(
                session, "alice", "user_rss_opmlcred", "Userinfo Feed",
            )
            session.commit()
        exported = client.get("/api/reader/sources/export.opml")
        assert exported.status_code == 200
        urls = set(_outline_urls(exported.text))
        assert token_url in urls
        assert userinfo_url in urls
        assert "token=secret&amp;x=1" in exported.text
        assert "alice:s3cret@" in exported.text


def test_confirm_import_conflicts_with_subscription_mutation(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_lock.db")
    from services import subscription_mutations

    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        assert subscription_mutations.begin("alice", "subscribe-batch")
        opml = _opml([("New Blog", _NEW_FEED)])
        dry = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": True})
        assert dry.status_code == 200
        before = _config_count(app_module.db_sink.engine)
        blocked = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": False})
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "已有订阅操作正在处理中"
        assert _config_count(app_module.db_sink.engine) == before
        subscription_mutations.finish("alice", "subscribe-batch")
        allowed = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": False})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["created_count"] == 1


def test_podcast_without_playable_items_fails_only_that_row(monkeypatch, tmp_path):
    app_module, _calls = _setup_app(monkeypatch, tmp_path, "opml_podcast.db")
    from models.db import SourceConfigRecord
    from services.user_sources import canonical_feed_url

    silent = "https://podcast.example.com/silent.xml"
    with TestClient(app_module.app) as client:
        _login(client, "alice", "alice")
        opml = _opml([
            ("Silent Show", silent, "podcast"),
            ("New Blog", _NEW_FEED),
        ])
        preview = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": True})
        assert preview.status_code == 200, preview.text
        by_url = _item_map(preview.json())
        assert by_url[canonical_feed_url(silent)]["status"] == "invalid"
        assert by_url[canonical_feed_url(silent)]["reason"] == "该 feed 未检测到可播放的音频条目"
        assert by_url[canonical_feed_url(silent)]["selectable"] is False
        assert by_url[canonical_feed_url(_NEW_FEED)]["status"] == "new"

        confirmed = client.post("/api/reader/sources/import", json={"opml": opml, "dry_run": False})
        assert confirmed.status_code == 200, confirmed.text
        done = confirmed.json()
        assert done["created_count"] == 1
        done_by_url = _item_map(done)
        assert done_by_url[canonical_feed_url(silent)]["status"] == "invalid"
        assert done_by_url[canonical_feed_url(_NEW_FEED)]["status"] == "already_subscribed"
        with Session(app_module.db_sink.engine) as session:
            rows = session.exec(select(SourceConfigRecord)).all()
        assert not any(row.url == canonical_feed_url(silent) for row in rows)
        assert any(row.url == canonical_feed_url(_NEW_FEED) for row in rows)
