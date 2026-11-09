"""源管理列全部源(issue #262 第三波):目录带 bound / origin / listing / retired,无绑定的源不能运行 / 编入任务。"""

import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.conftest import seed_default_accounts  # noqa: E402

_NOW = "2026-11-06T00:00:00"


def _setup(monkeypatch, tmp_path, name="sources.db"):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, (("admin", "admin", "admin"),))
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda ids: None)
    return app_module, sink


def _seed_rows(engine):
    """一个收录临时源、一个存量回填源、系统源、一个退役源、一个有绑定的配置播客源。"""
    from models.db import ArticleRecord, SourceConfigRecord, SourceStateRecord
    from services import source_rows

    with Session(engine) as session:
        source_rows.ensure_source_row(
            session, "web_pushed_site", content_type="web_article", origin="ingest",
            admission_status="provisional", name="Pushed Site", homepage_url="https://pushed.test",
        )
        source_rows.ensure_source_row(
            session, "manual", content_type="rss_article", origin="legacy", admission_status="admitted", name="manual",
        )
        source_rows.ensure_source_row(
            session, "dorami_daily_brief", content_type="daily_brief", origin="system", admission_status="admitted",
            name="哆啦美日报",
        )
        session.add(SourceConfigRecord(
            source_id="rss_gone", name="Gone", source_type="rss", url="https://gone.test/feed", fetcher_id="generic_rss",
            origin="admin", admission_status="admitted", retired_at=_NOW, created_at=_NOW, updated_at=_NOW,
        ))
        session.add(SourceConfigRecord(
            source_id="podcast_cfg", name="Cfg Pod", source_type="podcast_rss", url="https://pod.test/feed",
            fetcher_id="generic_podcast_rss", admission_status="admitted", created_at=_NOW, updated_at=_NOW,
        ))
        # 检视 R1 #2 / #4:绑定了不存在的抓取器 = 无绑定;类型不在模板白名单但 fetcher_id 有效 = 有绑定;
        # X 配置源形态是社交。
        session.add(SourceConfigRecord(
            source_id="rss_broken", name="Broken", source_type="rss", url="https://broken.test/feed",
            fetcher_id="missing_fetcher", admission_status="admitted", created_at=_NOW, updated_at=_NOW,
        ))
        session.add(SourceConfigRecord(
            source_id="odd_bound", name="Odd", source_type="wechat", url="https://odd.test/feed",
            fetcher_id="generic_rss", admission_status="admitted", created_at=_NOW, updated_at=_NOW,
        ))
        session.add(SourceConfigRecord(
            source_id="x_cfg", name="Someone on X", source_type="x", url="https://x.com/someone",
            fetcher_id="generic_x_timeline", admission_status="admitted", created_at=_NOW, updated_at=_NOW,
        ))
        # 检视 R1 #5 / #6:存量源带着多年前的失败快照;退役源还有历史条目。
        session.add(SourceStateRecord(
            source_id="manual", fetcher_id="generic_rss", status="failing",
            consecutive_failures=3, failed_runs=3, total_runs=3, updated_at=_NOW,
        ))
        session.add(ArticleRecord(
            id="rss_gone_1", title="gone", content_type="rss_article", source_id="rss_gone",
            source_url="https://gone.test/1", publish_date=_NOW, fetched_date=_NOW, has_content=True,
            content="x", extensions_json="{}",
        ))
        session.add(ArticleRecord(
            id="web_pushed_site_1", title="pushed", content_type="web_article", source_id="web_pushed_site",
            source_url="https://pushed.test/1", publish_date=_NOW, fetched_date=_NOW, has_content=True,
            content="x", extensions_json="{}", ingest_channel="push", ingest_actor="bot",
        ))
        session.commit()


def test_source_management_catalog_lists_every_public_source_with_binding_flags(tmp_path):
    from services.collection_nodes import (
        collection_node_catalog, source_management_catalog, unbound_source_ids,
    )
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'catalog.db'}")
    _seed_rows(sink.engine)
    with Session(sink.engine) as session:
        nodes = {item["id"]: item for item in source_management_catalog(session)}
        with_retired = {item["id"]: item for item in source_management_catalog(session, include_retired=True)}
        bound_only = {item["id"] for item in collection_node_catalog(session)}
        unbound = unbound_source_ids(
            session, ["x_openai", "web_pushed_site", "manual", "rss_gone", "podcast_cfg",
                      "rss_broken", "odd_bound", "x_cfg", "nope_missing"],
        )

    # 注册表预设:有绑定、来路目录。
    assert nodes["x_openai"]["bound"] is True and nodes["x_openai"]["origin"] == "catalog"
    assert nodes["x_openai"]["listing"] == "listed" and nodes["x_openai"]["retired"] is False
    # 收录临时源:无绑定、不列出、形态按行推导;存量与系统源同样无绑定。
    pushed = nodes["web_pushed_site"]
    assert (pushed["bound"], pushed["origin"], pushed["listing"], pushed["shape"]) == (False, "ingest", "unlisted", "article")
    assert pushed["admission_status"] == "provisional" and pushed["execution_fetcher_id"] == ""
    assert pushed["homepage_url"] == "https://pushed.test" and pushed["parameters"] == []
    assert nodes["manual"]["bound"] is False and nodes["manual"]["origin"] == "legacy"
    assert nodes["dorami_daily_brief"]["bound"] is False and nodes["dorami_daily_brief"]["origin"] == "system"
    # 有绑定的配置源照旧是采集节点,来路按行(空即 admin)。
    assert nodes["podcast_cfg"]["bound"] is True and nodes["podcast_cfg"]["origin"] == "admin"
    # 退役源默认不列,带 include_retired 才出现且标 retired;采集节点目录(抓取 / 任务用)不含任何无绑定源。
    assert "rss_gone" not in nodes
    assert with_retired["rss_gone"]["retired"] is True and with_retired["rss_gone"]["bound"] is False
    assert bound_only >= {"x_openai", "podcast_cfg"}
    assert not bound_only & {"web_pushed_site", "manual", "dorami_daily_brief", "rss_gone"}
    # bound 与实际绑定检查同一口径(R1 #2):坏绑定 = 无;白名单外但有效 = 有。X 配置源形态是社交(R1 #4)。
    assert nodes["rss_broken"]["bound"] is False and nodes["rss_broken"]["execution_fetcher_id"] == ""
    assert nodes["odd_bound"]["bound"] is True
    assert (nodes["x_cfg"]["bound"], nodes["x_cfg"]["shape"], nodes["x_cfg"]["content_type"]) == (True, "social", "social_post")
    assert unbound == ["web_pushed_site", "manual", "rss_gone", "rss_broken"]


def test_unbound_sources_are_visible_but_cannot_be_run_or_scheduled(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    _seed_rows(sink.engine)
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        catalog = {item["id"]: item for item in client.get("/api/fetchers").json()}
        assert catalog["web_pushed_site"]["bound"] is False and "rss_gone" not in catalog
        retired = {item["id"]: item for item in client.get("/api/fetchers", params={"include_retired": "true"}).json()}
        assert retired["rss_gone"]["retired"] is True
        # 源健康汇总也列无绑定源:只有累计条目数,运行史为空。
        health = {item["fetcher_id"]: item for item in client.get("/api/source-health").json()}
        assert health["web_pushed_site"]["total_articles"] == 1
        assert health["web_pushed_site"]["health_status"] == "never_run"
        assert health["web_pushed_site"]["bound"] is False
        # 存量源的旧失败快照不算今天的告警(R1 #5);退役源也有健康行,带真实历史条目数(R1 #6)。
        assert health["manual"]["health_status"] == "never_run" and not health["manual"].get("consecutive_failures")
        assert health["rss_gone"]["retired"] is True and health["rss_gone"]["total_articles"] == 1
        assert catalog["rss_broken"]["bound"] is False
        # 采集任务不接受无绑定的源(前端隐藏之外的第二道闸)。
        bad = client.post("/api/collection-jobs", json={
            "name": "j", "fetcher_ids": ["x_openai", "web_pushed_site"], "cron_expr": "",
        })
        assert bad.status_code == 400 and "没有采集绑定" in bad.json()["detail"], bad.text
        ok = client.post("/api/collection-jobs", json={"name": "j", "fetcher_ids": ["x_openai"], "cron_expr": ""})
        assert ok.status_code == 200, ok.text
        job_id = ok.json()["id"]
        worse = client.put(f"/api/collection-jobs/{job_id}", json={"fetcher_ids": ["manual"]})
        assert worse.status_code == 400 and "manual" in worse.json()["detail"]
