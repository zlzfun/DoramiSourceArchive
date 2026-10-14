"""源级 category / content_tags 退役与准入状态(issue #178 第一波补)。

覆盖:
1. 注册表:元数据不再带 category / content_tags,准入状态取值合法,模板节点只看 is_template;
2. 条目标签:RSS 只取 feed 自带的 term,不再追加源级分类;
3. 配置源:接口按 admission_status 读写(含转正)、不再外露退役字段;播客目录重导不打回观察期;
4. 归档同步:sources 流传 admission_status,不再传 category / content_tags_json;
5. 每日采集脚本:按 is_template / admission_status 排除。
"""
import datetime
import json
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from fetchers.registry import admission_status_of, fetcher_registry  # noqa: E402
from models.db import SourceConfigRecord  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

STAMP = "2026-10-12T00:00:00+00:00"


# ==================== 注册表 ====================

def test_registry_metadata_drops_retired_fields_and_carries_admission():
    rows = fetcher_registry.get_all_metadata()
    assert rows
    for row in rows:
        assert "category" not in row and "content_tags" not in row, row["id"]
        assert row["admission_status"] in {"admitted", "incubating"}, row["id"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["rss_hf_blog"]["admission_status"] == "incubating"
    assert by_id["web_anthropic_news"]["admission_status"] == "admitted"
    templates = {row["id"] for row in rows if row["is_template"]}
    assert {"generic_rss", "generic_web", "generic_podcast_rss"} <= templates
    for source_id in templates:
        assert "通用扩展能力" in by_id[source_id]["curation_reason"]


def test_fetcher_classes_no_longer_declare_retired_attributes():
    """防回潮:抓取器类上不再有 category / content_tags(条目标签也就不会再拼进它们)。"""
    for source_id, fetcher_class in fetcher_registry._fetchers.items():
        assert not hasattr(fetcher_class, "category"), source_id
        assert not hasattr(fetcher_class, "content_tags"), source_id


def test_admission_status_normalization():
    assert admission_status_of("incubating") == "incubating"
    assert admission_status_of(" Incubating ") == "incubating"
    assert admission_status_of("admitted") == "admitted"
    # 拼错或旧值一律按正式处理,不会悄悄把节点挤出每日采集
    assert admission_status_of("official") == "admitted"
    assert admission_status_of(None) == "admitted"


# ==================== 条目标签 ====================

def test_rss_entry_tags_keep_only_feed_terms():
    from fetchers.impl.rss_fetcher import GenericRssFetcher

    fetcher = GenericRssFetcher()
    entry = {"tags": [{"term": "LLM"}, {"term": ""}, {"term": "Agents"}]}
    assert fetcher._entry_tags(entry) == ["LLM", "Agents"]
    assert fetcher._entry_tags({}) == []


# ==================== 配置源 ====================

def _make_app(monkeypatch, tmp_path, name):
    import api.app as app_module
    from config import RuntimeConfig
    from models.db import UserRecord
    from services import accounts as accounts_service

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    now = datetime.datetime.now().isoformat()
    with Session(sink.engine) as session:
        session.add(UserRecord(
            username="admin",
            password_hash=accounts_service.hash_password("admin"),
            role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return app_module, sink


def test_source_config_api_reads_and_writes_admission_status(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "admission_api.db")
    with TestClient(app_module.app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        created = client.post("/api/source-configs", json={
            "source_id": "custom_feed",
            "name": "Custom feed",
            "source_type": "rss",
            "url": "https://example.test/feed.xml",
            "admission_status": "incubating",
            # 退役字段:旧客户端仍可能带上,接口忽略而不报错
            "category": "official",
            "content_tags": ["model_release"],
        })
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["admission_status"] == "incubating"
        assert "category" not in body and "content_tags" not in body and "content_tags_json" not in body

        listed = client.get("/api/source-configs", params={"admission_status": "incubating"}).json()
        assert [item["source_id"] for item in listed] == ["custom_feed"]

        # 转正
        promoted = client.put("/api/source-configs/custom_feed", json={"admission_status": "admitted"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["admission_status"] == "admitted"
        # 未知值按正式处理
        odd = client.put("/api/source-configs/custom_feed", json={"admission_status": "official"})
        assert odd.json()["admission_status"] == "admitted"

    from services.collection_nodes import configured_source_metadata

    with Session(sink.engine) as session:
        record = session.get(SourceConfigRecord, "custom_feed")
        assert record.admission_status == "admitted"
        assert record.category == "" and record.content_tags_json == "[]"  # 退役列不再写入
        node = configured_source_metadata(record)  # 节点目录 / 源健康里的配置源节点形状
        assert node["admission_status"] == "admitted"
        assert "category" not in node and "content_tags" not in node


def test_podcast_catalog_reimport_keeps_promoted_admission(tmp_path):
    from services import podcast_catalog

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'catalog.db'}")
    source_id = "podcast_latent_space"
    with Session(sink.engine) as session:
        podcast_catalog.import_podcast_catalog(session, source_ids=[source_id])
        record = session.get(SourceConfigRecord, source_id)
        assert record.admission_status == "incubating"  # 新装入从观察期开始
        assert record.content_tags_json == "[]"
        record.admission_status = "admitted"
        session.add(record)
        session.commit()
        podcast_catalog.import_podcast_catalog(session, source_ids=[source_id], update_existing=True)
        assert session.get(SourceConfigRecord, source_id).admission_status == "admitted"


# ==================== 归档同步 ====================

def test_archive_sync_sources_stream_carries_admission_not_retired_columns(tmp_path):
    from services import archive_sync_v2

    producer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'producer.db'}")
    consumer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'consumer.db'}")
    with Session(producer.engine) as session:
        for source_id, admission, legacy_category in (
            ("podcast_demo", "incubating", "incubating"),
            ("podcast_promoted", "admitted", "incubating"),  # 已转正,退役列里还留着旧值
        ):
            session.add(SourceConfigRecord(
                source_id=source_id,
                name=source_id,
                source_type="podcast",
                url=f"https://example.test/{source_id}.xml",
                admission_status=admission,
                category=legacy_category,
                content_tags_json='["opinion"]',
                created_at=STAMP,
                updated_at=STAMP,
            ))
        session.commit()

    raw = archive_sync_v2.export_page(producer.engine, "sources", limit=100)
    payloads = {
        item["payload"]["source_id"]: item["payload"]
        for item in (json.loads(line) for line in raw.splitlines()[1:] if line.strip())
        if isinstance(item, dict) and isinstance(item.get("payload"), dict)
    }
    assert payloads["podcast_demo"]["admission_status"] == "incubating"
    assert "content_tags_json" not in payloads["podcast_demo"]
    # 线路兼容键:由准入状态派生(不读退役列),旧版接收端据此保留观察期
    assert payloads["podcast_demo"]["category"] == "incubating"
    assert payloads["podcast_promoted"]["category"] == ""

    archive_sync_v2.import_page(consumer.engine, raw, expected_stream="sources")
    with Session(consumer.engine) as session:
        assert session.get(SourceConfigRecord, "podcast_demo").admission_status == "incubating"
        assert session.get(SourceConfigRecord, "podcast_promoted").admission_status == "admitted"


def test_receiver_derives_admission_from_legacy_producer_payload(tmp_path):
    """旧版生产者只发 category(混部期间):新接收端按它推导准入状态,观察期不丢(codex R1 #1)。"""
    from services import archive_sync_v2

    consumer = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'legacy.db'}")
    rows = [
        {"payload": {
            "source_id": source_id, "name": source_id, "source_type": "podcast",
            "url": f"https://example.test/{source_id}.xml", "category": category,
            "content_tags_json": "[]", "created_at": STAMP, "updated_at": STAMP,
        }}
        for source_id, category in (("legacy_incubating", "incubating"), ("legacy_official", "official"))
    ]
    with Session(consumer.engine) as session:
        archive_sync_v2._apply_sources(session, rows, "producer-legacy")
        session.commit()
        assert session.get(SourceConfigRecord, "legacy_incubating").admission_status == "incubating"
        assert session.get(SourceConfigRecord, "legacy_official").admission_status == "admitted"

        def apply(payload):
            archive_sync_v2._apply_sources(session, [{"payload": {
                "source_id": "legacy_incubating", "name": "x", "source_type": "podcast",
                "url": "https://example.test/x.xml", "created_at": STAMP, "updated_at": STAMP, **payload,
            }}], "producer-legacy")
            session.commit()
            return session.get(SourceConfigRecord, "legacy_incubating").admission_status

        # 新字段在场时以它为准,旧兼容键不得覆盖
        assert apply({"admission_status": "admitted", "category": "incubating"}) == "admitted"
        assert apply({"admission_status": "incubating", "category": ""}) == "incubating"
        # 两个键都缺:不动已有状态
        assert apply({}) == "incubating"


# ==================== 每日采集脚本 ====================

def test_daily_collection_job_excludes_templates_and_incubating_nodes():
    from ensure_daily_collection_job import selected_fetcher_ids

    rows = {row["id"]: row for row in fetcher_registry.get_all_metadata()}
    default_ids = set(selected_fetcher_ids())
    assert default_ids
    assert all(
        not rows[fid]["is_template"] and rows[fid]["admission_status"] == "admitted"
        for fid in default_ids
    )
    assert "rss_hf_blog" not in default_ids  # 观察期
    assert "generic_rss" not in default_ids  # 模板
    assert set(selected_fetcher_ids(include_advanced=True)) == set(rows)


def test_promoting_one_inherited_incubating_source_only_admits_that_source(monkeypatch):
    """X 预设的观察期继承自父类:单个账号转正要在具体类上覆盖,删父类那一行等于整批转正(codex R1 #3)。"""
    from ensure_daily_collection_job import selected_fetcher_ids

    before = set(selected_fetcher_ids())
    x_openai = fetcher_registry.get_class("x_openai")
    assert "admission_status" not in vars(x_openai)  # 继承自 PresetXTimelineFetcher
    monkeypatch.setattr(x_openai, "admission_status", "admitted", raising=False)
    assert set(selected_fetcher_ids()) - before == {"x_openai"}
