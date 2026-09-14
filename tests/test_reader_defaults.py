"""新账号默认订阅名单(issue #56 落地页早报波):代码缺省 + KV 覆盖 + 管理端点 + 播种口径。"""

import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402


def _setup(monkeypatch, tmp_path, name="rdef.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine)
    return app_module, sink


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_default_source_ids_fall_back_to_code_default(monkeypatch, tmp_path):
    from services import reader_defaults

    _app, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        assert reader_defaults.stored_source_ids(session) is None
        assert reader_defaults.default_source_ids(session) == reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS
        # 损坏的 KV 视同未设置(不让一条坏值把新账号播成零订阅)
        from services import daily_brief as daily_brief_service
        daily_brief_service.set_setting(session, reader_defaults.DEFAULT_SOURCE_IDS_KEY, "{not json")
        assert reader_defaults.default_source_ids(session) == reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS
        # 覆盖:去空/去重/保序;空数组是合法值(= 不播种)
        assert reader_defaults.set_default_source_ids(session, ["web_qbitai", " ", "web_qbitai", "rss_openai_news"]) == [
            "web_qbitai", "rss_openai_news",
        ]
        assert reader_defaults.default_source_ids(session) == ["web_qbitai", "rss_openai_news"]
        assert reader_defaults.set_default_source_ids(session, []) == []
        assert reader_defaults.stored_source_ids(session) == []
        # None = 删除覆盖回落
        assert reader_defaults.set_default_source_ids(session, None) == reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS
        assert reader_defaults.stored_source_ids(session) is None


def test_code_default_list_is_known_to_the_registry():
    """代码缺省名单里的每个 id 都必须是注册表现役源或公共日报——名单改错在测试期就暴露。"""
    from api.sources import _registry_source_meta
    from services import reader_defaults
    from services.daily_brief import DAILY_BRIEF_SOURCE_ID

    meta = _registry_source_meta()
    for source_id in reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS:
        assert source_id == DAILY_BRIEF_SOURCE_ID or source_id in meta, source_id
        if source_id in meta:
            assert not meta[source_id].get("is_template"), source_id


def test_admin_endpoint_roundtrip_validation_and_gating(monkeypatch, tmp_path):
    from models.db import SourceConfigRecord
    from services import reader_defaults

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="podcast_demo", name="演示播客", source_type="podcast", url="https://x/feed",
            fetcher_id="generic_podcast_rss", is_active=True, params_json="{}",
            created_at="2026-09-14T00:00:00", updated_at="2026-09-14T00:00:00",
        ))
        session.add(SourceConfigRecord(
            source_id="user_rss_abc123def456", name="私有源", source_type="rss", url="https://x/rss",
            fetcher_id="generic_rss", is_active=True, params_json="{}", owner_username="user",
            created_at="2026-09-14T00:00:00", updated_at="2026-09-14T00:00:00",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        # 读者不可读不可写
        _login(client, "user", "user")
        assert client.get("/api/admin/reader-defaults").status_code == 403
        assert client.post("/api/admin/reader-defaults", json={"source_ids": []}).status_code == 403

        _login(client, "admin", "admin")
        initial = client.get("/api/admin/reader-defaults").json()
        assert initial["overridden"] is False
        assert initial["source_ids"] == reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS
        assert initial["code_default"] == reader_defaults.DEFAULT_SUBSCRIPTION_SOURCE_IDS
        assert all(row["name"] for row in initial["sources"])
        assert all(row["unknown"] is False for row in initial["sources"])

        # 未知 id / 私有自定源 → 400 且不落 KV
        bad = client.post("/api/admin/reader-defaults", json={"source_ids": ["web_qbitai", "nope_source", "user_rss_abc123def456"]})
        assert bad.status_code == 400, bad.text
        assert bad.json()["detail"]["unknown_source_ids"] == ["nope_source", "user_rss_abc123def456"]
        assert client.get("/api/admin/reader-defaults").json()["overridden"] is False

        # 注册源 + 公共 source_config(播客目录)+ 日报 都合法
        ok = client.post("/api/admin/reader-defaults", json={"source_ids": ["dorami_daily_brief", "podcast_demo", "web_qbitai"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["overridden"] is True
        assert ok.json()["source_ids"] == ["dorami_daily_brief", "podcast_demo", "web_qbitai"]
        assert [row["name"] for row in ok.json()["sources"]][1] == "演示播客"

        # 恢复缺省
        reset = client.post("/api/admin/reader-defaults", json={"source_ids": None})
        assert reset.status_code == 200
        assert reset.json()["overridden"] is False

        # 写操作入审计,摘要带条数与前三个名字
        client.post("/api/admin/reader-defaults", json={"source_ids": ["web_qbitai", "web_ithome_ai", "web_aiera", "rss_openai_news"]})
        log = client.get("/api/admin/audit-log?days=1").json()
        summaries = [row["summary"] for row in log["items"]]
        assert any("更新新账号默认订阅名单(4 源" in s and "web_qbitai" in s for s in summaries), summaries


def test_login_seeding_uses_kv_override(monkeypatch, tmp_path):
    """KV 覆盖名单生效于播种时刻:新账号只订阅覆盖名单;空名单 = 不播种。"""
    from services import accounts as accounts_service
    from services import daily_brief as daily_brief_service
    from services import reader_defaults

    app_module, sink = _setup(monkeypatch, tmp_path)
    with Session(sink.engine) as session:
        daily_brief_service.set_setting(
            session, reader_defaults.DEFAULT_SOURCE_IDS_KEY, json.dumps(["web_qbitai", "rss_openai_news"])
        )
        accounts_service.create_user(session, "newbie", "pw", "user")
        accounts_service.create_user(session, "nobody", "pw", "user")

    with TestClient(app_module.app) as client:
        _login(client, "newbie", "pw")
    with Session(sink.engine) as session:
        assert set(app_module.resolve_subscribed_source_ids(session, "newbie")) == {"web_qbitai", "rss_openai_news"}
        daily_brief_service.set_setting(session, reader_defaults.DEFAULT_SOURCE_IDS_KEY, "[]")
    with TestClient(app_module.app) as client:
        _login(client, "nobody", "pw")
    with Session(sink.engine) as session:
        assert app_module.resolve_subscribed_source_ids(session, "nobody") == []
        # 名单改动不回填存量账号
        assert set(app_module.resolve_subscribed_source_ids(session, "newbie")) == {"web_qbitai", "rss_openai_news"}
