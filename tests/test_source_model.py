"""源模型重构(issue #262 R1a–R1c):显式绑定、源目录读服务、源表成为全集。"""

import os
import sys

from alembic import command
from sqlalchemy import create_engine, text
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.migrations import make_alembic_config  # noqa: E402


_NOW = "2026-11-05T00:00:00"


def _insert_source(conn, source_id, *, source_type="rss", fetcher_id="", authority=""):
    conn.execute(text(
        "INSERT INTO source_configs (source_id, name, source_type, url, category, fetcher_id, "
        "description, source_owner, source_brand, source_scope, source_channel, base_url, "
        "provenance_tier, content_tags_json, signal_strength, noise_risk, fetch_reliability, "
        "admission_status, owner_username, collection_authority_id, ai_analysis_enabled, is_active, "
        "params_json, created_at, updated_at) VALUES (:sid, :sid, :stype, '', '', :fid, '', '', '', "
        "'', '', '', '', '[]', '', '', '', 'admitted', '', :auth, 1, 1, '{}', :now, :now)"
    ), {"sid": source_id, "stype": source_type, "fid": fetcher_id, "auth": authority, "now": _NOW})


def test_r1b_migration_writes_implicit_bindings_and_skips_synced_rows(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'r1b.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "8166665976c4")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        _insert_source(conn, "rss_a", source_type="rss")
        _insert_source(conn, "atom_a", source_type=" Atom ")
        _insert_source(conn, "web_a", source_type="webpage")
        _insert_source(conn, "x_a", source_type="twitter")
        _insert_source(conn, "pod_a", source_type="podcast_rss")
        _insert_source(conn, "explicit_a", source_type="rss", fetcher_id="rss_custom_fetcher")
        _insert_source(conn, "odd_a", source_type="wechat")
        _insert_source(conn, "synced_a", source_type="rss", authority="authority-1")
    command.upgrade(cfg, "heads")
    with engine.connect() as conn:
        bindings = dict(conn.execute(text("SELECT source_id, fetcher_id FROM source_configs")).all())
    engine.dispose()
    assert bindings == {
        "rss_a": "generic_rss",
        "atom_a": "generic_rss",
        "web_a": "generic_web",
        "x_a": "generic_x_timeline",
        "pod_a": "generic_podcast_rss",
        "explicit_a": "rss_custom_fetcher",
        "odd_a": "",
        "synced_a": "",
    }


def test_new_source_rows_carry_explicit_bindings(tmp_path):
    from models.db import SourceConfigRecord
    from services.collection_nodes import default_fetcher_id_for_source_type
    from storage.impl.db_storage import DatabaseStorage

    assert default_fetcher_id_for_source_type("RSS") == "generic_rss"
    assert default_fetcher_id_for_source_type("podcast") == "generic_podcast_rss"
    assert default_fetcher_id_for_source_type("wechat") == ""

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'create.db'}")
    from api.routers.source_configs import SourceConfigCreate, create_source_config

    with Session(sink.engine) as session:
        created = create_source_config(
            SourceConfigCreate(source_id="web_created", name="W", source_type="web", url="https://w.test"),
            session=session,
        )
        assert created["fetcher_id"] == "generic_web"
        assert session.get(SourceConfigRecord, "web_created").fetcher_id == "generic_web"


def test_changing_source_type_moves_the_default_binding(tmp_path):
    from api.routers.source_configs import (
        SourceConfigCreate,
        SourceConfigUpdate,
        create_source_config,
        update_source_config,
    )
    from models.db import SourceConfigRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'retype.db'}")
    with Session(sink.engine) as session:
        create_source_config(
            SourceConfigCreate(source_id="rss_show", name="S", source_type="rss", url="https://s.test/feed"),
            session=session,
        )
        create_source_config(
            SourceConfigCreate(
                source_id="rss_custom", name="C", source_type="rss", url="https://c.test/feed",
                fetcher_id="rss_simonwillison",
            ),
            session=session,
        )
        update_source_config("rss_show", SourceConfigUpdate(source_type="podcast"), session=session, auth=None)
        update_source_config("rss_custom", SourceConfigUpdate(source_type="podcast"), session=session, auth=None)
        assert session.get(SourceConfigRecord, "rss_show").fetcher_id == "generic_podcast_rss"
        # 显式指定过的非默认绑定不被改类型顺手覆盖。
        assert session.get(SourceConfigRecord, "rss_custom").fetcher_id == "rss_simonwillison"


# ── R1c:源表成为全集 ─────────────────────────────────────────────────────────

import asyncio  # noqa: E402
import json  # noqa: E402

from sqlmodel import select  # noqa: E402


def _sink(tmp_path, name):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def _seed_article(engine, article_id, source_id, *, content_type="rss_article"):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=article_id, content_type=content_type, source_id=source_id,
            source_url=f"https://example.test/{article_id}", publish_date=_NOW, fetched_date=_NOW,
            archive_updated_at=_NOW, has_content=True, content="body", extensions_json="{}",
        ))
        session.commit()


def _reconcile(engine):
    from services import source_rows

    with Session(engine) as session:
        summary = source_rows.reconcile_source_rows(session, now=_NOW)
        session.commit()
    return summary


def test_reconcile_mirrors_every_preset_equal_to_class_attributes(tmp_path):
    from models.db import SourceConfigRecord, SourceMigrationLedgerRecord
    from services import source_catalog

    sink = _sink(tmp_path, "mirror.db")
    first = _reconcile(sink.engine)
    presets = source_catalog.registry_source_ids()
    assert first["mirror"] == {"created": len(presets)}
    assert first["system"] == "created"
    with Session(sink.engine) as session:
        for source_id in presets:
            row = session.get(SourceConfigRecord, source_id)
            view = source_catalog.registry_view(source_id)
            assert row.origin == "catalog"
            # X 预设记 x_timeline(外网回滚到旧版时旧代码只按类型判形态也判成社交),其余记 preset。
            assert row.source_type == ("x_timeline" if view.shape == "social" else "preset")
            assert row.fetcher_id == source_id and row.legacy_export is False
            # 外网回滚到 R1c 之前的版本:旧代码配置行优先、只按 source_type 判形态,也要判对。
            legacy_shape = "social" if row.source_type in {"x", "x_timeline"} else (
                "podcast" if row.source_type in {"podcast", "podcast_rss"} else "article"
            )
            assert legacy_shape == view.shape, source_id
            for field in ("name", "description", "shape", "platform", "default_kind", "source_scope",
                          "provenance_tier", "admission_status", "daily"):
                assert getattr(row, field) == getattr(view, field), (source_id, field)
        # 每日默认范围 = 注册表里非模板、非观察期(与 ensure_daily_collection_job 今天的口径相等)。
        daily = sorted(row.source_id for row in session.exec(
            select(SourceConfigRecord).where(SourceConfigRecord.daily == True)  # noqa: E712
        ).all())
        assert daily == sorted(
            sid for sid in presets if source_catalog.registry_view(sid).admission_status == "admitted"
        )
        system = session.get(SourceConfigRecord, "dorami_daily_brief")
        assert system.origin == "system" and system.fetcher_id == ""
        tombstone = session.get(SourceConfigRecord, "x_openrouter")
        assert tombstone.retired_at and tombstone.is_active is False
        ledger = session.exec(select(SourceMigrationLedgerRecord)).all()
        assert {row.source_id for row in ledger} >= set(presets)

    # 幂等:第二次对账不改写任何行(不制造同步 revision)。
    second = _reconcile(sink.engine)
    assert second["mirror"] == {"unchanged": len(presets)}
    assert second["system"] == "unchanged" and second["tombstones"] == 0


def test_reconcile_adopts_existing_admin_row_with_preset_id_and_records_pre_image(tmp_path):
    from models.db import SourceConfigRecord, SourceMigrationLedgerRecord

    sink = _sink(tmp_path, "adopt.db")
    with Session(sink.engine) as session:
        session.add(SourceConfigRecord(
            source_id="rss_simonwillison", name="本地改过的名字", source_type="rss",
            url="https://simonwillison.net/atom/everything/", fetcher_id="generic_rss",
            origin="admin", created_at=_NOW, updated_at=_NOW,
        ))
        session.commit()
    summary = _reconcile(sink.engine)
    assert summary["mirror"].get("adopted") == 1
    with Session(sink.engine) as session:
        row = session.get(SourceConfigRecord, "rss_simonwillison")
        assert row.origin == "catalog" and row.name != "本地改过的名字"
        # 原本就向旧接收端导出的行照旧导出;已有绑定不被改写。
        assert row.legacy_export is True and row.fetcher_id == "generic_rss"
        ledger = session.exec(select(SourceMigrationLedgerRecord).where(
            SourceMigrationLedgerRecord.source_id == "rss_simonwillison",
            SourceMigrationLedgerRecord.action == "adopted",
        )).one()
        assert json.loads(ledger.pre_image_json)["name"] == "本地改过的名字"


def test_backfill_covers_public_orphans_private_orphans_and_subscription_only_ids(tmp_path):
    from models.db import ReaderSubscriptionRecord, SourceConfigRecord
    from services import source_rows

    sink = _sink(tmp_path, "backfill.db")
    _seed_article(sink.engine, "manual_1", "manual", content_type="manual_entry")
    _seed_article(sink.engine, "x_someone_1", "x_someone", content_type="social_post")
    _seed_article(sink.engine, "user_rss_abc_1", "user_rss_abcdef123456")
    with Session(sink.engine) as session:
        session.add(ReaderSubscriptionRecord(
            owner_username="alice", name="gone", filters_json=json.dumps({"source_ids": "web_gone_source"}),
            token_hash="hash-gone", token_preview="dsub_x", created_at=_NOW, updated_at=_NOW,
        ))
        session.commit()
    summary = _reconcile(sink.engine)
    assert summary["backfill"] == {"legacy": 2, "quarantined": 1, "subscription_only": 1}
    with Session(sink.engine) as session:
        manual = session.get(SourceConfigRecord, "manual")
        assert (manual.origin, manual.shape, manual.admission_status) == ("legacy", "article", "admitted")
        social = session.get(SourceConfigRecord, "x_someone")
        assert (social.shape, social.platform, social.source_type) == ("social", "x", "x")
        private = session.get(SourceConfigRecord, "user_rss_abcdef123456")
        assert private.ai_analysis_enabled is False and private.is_active is False
        assert json.loads(private.params_json)["credentialed_private"] is True
        gone = session.get(SourceConfigRecord, "web_gone_source")
        assert gone.retired_at and gone.origin == "legacy"
        assert source_rows.orphan_article_source_ids(session) == []
        # 回填行与镜像行都不是采集节点(没有绑定)。
        from services.collection_nodes import resolve_source_fetcher_id

        assert resolve_source_fetcher_id(manual) == "" and resolve_source_fetcher_id(social) == ""


def test_receiver_never_writes_public_source_rows(tmp_path):
    from models.db import SourceConfigRecord
    from services import source_rows
    from services.sync_consumer_policy import activate_v2_consumer_mode

    sink = _sink(tmp_path, "receiver.db")
    with Session(sink.engine) as session:
        activate_v2_consumer_mode(session, reason="test")
    _seed_article(sink.engine, "manual_1", "manual")
    assert _reconcile(sink.engine) == {"skipped": "receiver"}
    with Session(sink.engine) as session:
        assert source_rows.ensure_source_row(session, "web_new") is None
        assert session.exec(select(SourceConfigRecord)).all() == []


def test_save_ensures_a_source_row_in_the_same_transaction(tmp_path):
    from api.articles_view import GenericContent
    from models.db import SourceConfigRecord

    sink = _sink(tmp_path, "ensure.db")

    def item(item_id, source_id, *, hint=None, content_type="rss_article"):
        content = GenericContent(id=item_id, title=item_id, source_url=f"https://e.test/{item_id}",
                                 publish_date=_NOW, content="x", has_content=True)
        content.source_id = source_id
        content.content_type = content_type
        if hint:
            content._source_hint = hint
        return content

    assert asyncio.run(sink.save(item("fetched_1", "rss_unconfigured"))) is True
    assert asyncio.run(sink.save(item("pushed_1", "web_pushed", hint={
        "origin": "agent", "admission_status": "provisional", "name": "推来的站点",
    }))) is True
    assert asyncio.run(sink.save(item("preset_1", "rss_simonwillison"))) is True
    assert asyncio.run(sink.save(item("tpl_1", "generic_rss"))) is True
    with Session(sink.engine) as session:
        fetched = session.get(SourceConfigRecord, "rss_unconfigured")
        assert (fetched.origin, fetched.admission_status) == ("legacy", "admitted")
        pushed = session.get(SourceConfigRecord, "web_pushed")
        assert (pushed.origin, pushed.admission_status, pushed.name) == ("agent", "provisional", "推来的站点")
        assert pushed.legacy_export is False
        assert session.get(SourceConfigRecord, "rss_simonwillison").origin == "catalog"
        # 模板 id 是血统缺陷,不建源行掩盖。
        assert session.get(SourceConfigRecord, "generic_rss") is None


def test_source_config_admin_surface_hides_catalog_rows_and_retires_referenced_sources(tmp_path):
    import pytest
    from fastapi import HTTPException

    from api.routers.source_configs import (
        SourceConfigCreate,
        create_source_config,
        delete_source_config,
        get_source_configs,
        update_source_config,
        SourceConfigUpdate,
    )
    from models.db import SourceConfigRecord

    sink = _sink(tmp_path, "admin.db")
    _seed_article(sink.engine, "manual_1", "manual")
    _reconcile(sink.engine)
    with Session(sink.engine) as session:
        create_source_config(
            SourceConfigCreate(source_id="rss_admin", name="Admin", source_type="rss", url="https://a.test/f"),
            session=session,
        )
        create_source_config(
            SourceConfigCreate(source_id="rss_unused", name="Unused", source_type="rss", url="https://u.test/f"),
            session=session,
        )
        listed = {row["source_id"] for row in get_source_configs(session=session, auth=None, limit=500)}
        assert listed == {"rss_admin", "rss_unused"}
        with pytest.raises(HTTPException) as exc:
            update_source_config(
                "rss_simonwillison", SourceConfigUpdate(name="x"), session=session, auth=None
            )
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException):
            delete_source_config("manual", session=session, auth=None)
    _seed_article(sink.engine, "rss_admin_1", "rss_admin")
    with Session(sink.engine) as session:
        assert delete_source_config("rss_admin", session=session, auth=None)["disposition"] == "retired"
        assert delete_source_config("rss_unused", session=session, auth=None)["disposition"] == "deleted"
        retired = session.get(SourceConfigRecord, "rss_admin")
        assert retired.retired_at and retired.is_active is False
        assert session.get(SourceConfigRecord, "rss_unused") is None


def test_catalog_service_compat_and_full_models_agree_on_mirror_rows(tmp_path):
    from services import source_catalog

    sink = _sink(tmp_path, "models.db")
    _reconcile(sink.engine)
    ids = source_catalog.registry_source_ids()
    with Session(sink.engine) as session:
        try:
            source_catalog.configure(model="compat")
            compat = source_catalog.describe(session, ids)
            source_catalog.configure(model="full")
            full = source_catalog.describe(session, ids)
        finally:
            source_catalog.configure(model="compat")
    for source_id in ids:
        assert compat[source_id] == full[source_id], source_id
        assert compat[source_id].configured and compat[source_id].registered


# ── R1c:Archive Sync 按接收端能力投影 ───────────────────────────────────────


def _admin_source(source_id, **overrides):
    from models.db import SourceConfigRecord

    data = dict(
        source_id=source_id, name=source_id, source_type="rss", url="https://a.test/feed",
        fetcher_id="generic_rss", origin="admin", created_at=_NOW, updated_at=_NOW,
    )
    data.update(overrides)
    return SourceConfigRecord(**data)


def _page(engine, stream, *, catalog, since=""):
    from services import archive_sync_v2

    raw = archive_sync_v2.export_page(
        engine, stream, since=since,
        receiver_capabilities=archive_sync_v2.SOURCE_CATALOG_CAPABILITY if catalog else "",
    )
    manifest, rows = archive_sync_v2.parse_page(raw, expected_stream=stream)
    return raw, manifest, {row["identity"]: row for row in rows}


def test_legacy_projection_matches_pre_refactor_export(tmp_path):
    from models.db import SourceStateRecord

    producer = _sink(tmp_path, "producer-legacy.db")
    _seed_article(producer.engine, "manual_1", "manual")
    _reconcile(producer.engine)
    with Session(producer.engine) as session:
        session.add(_admin_source("rss_admin"))
        session.add(_admin_source("web_provisional", origin="agent", admission_status="provisional",
                                  legacy_export=True))
        session.add(SourceStateRecord(source_id="rss_simonwillison", fetcher_id="rss_simonwillison",
                                      status="healthy", updated_at=_NOW))
        session.commit()

    _raw, manifest, rows = _page(producer.engine, "sources", catalog=False)
    assert manifest["source_projection"] == "legacy"
    # 重构前就会导出的行照旧 upsert,载荷不带新字段;新值 provisional 折回旧端安全的 incubating。
    assert rows["rss_admin"]["operation"] == "upsert"
    assert "origin" not in rows["rss_admin"]["payload"]
    assert rows["web_provisional"]["payload"]["admission_status"] == "incubating"
    # 镜像行 / 回填行 / 系统行:旧接收端本来就没有这些行,收到的是空操作 tombstone。
    for identity in ("rss_simonwillison", "manual", "dorami_daily_brief"):
        assert rows[identity]["operation"] == "tombstone", identity
    _raw, _manifest, states = _page(producer.engine, "source_states", catalog=False)
    assert states["rss_simonwillison"]["operation"] == "upsert"


def test_catalog_projection_carries_full_catalog_and_soft_retire(tmp_path):
    from models.db import SourceConfigRecord, SourceStateRecord

    producer = _sink(tmp_path, "producer-catalog.db")
    _reconcile(producer.engine)
    with Session(producer.engine) as session:
        session.add(_admin_source("rss_admin"))
        session.add(SourceStateRecord(source_id="rss_admin", fetcher_id="generic_rss",
                                      status="healthy", updated_at=_NOW))
        session.commit()
    _raw, manifest, rows = _page(producer.engine, "sources", catalog=True)
    assert manifest["source_projection"] == "catalog-v1"
    mirror = rows["rss_simonwillison"]
    assert mirror["operation"] == "upsert" and mirror["payload"]["origin"] == "catalog"
    assert "fetcher_id" not in mirror["payload"] and "params_json" not in mirror["payload"]
    snapshot = manifest["snapshot"]

    with Session(producer.engine) as session:
        row = session.get(SourceConfigRecord, "rss_admin")
        row.retired_at = "2026-11-06T00:00:00"
        row.is_active = False
        session.add(row)
        session.commit()
    _raw, _m, catalog_delta = _page(producer.engine, "sources", catalog=True, since=snapshot)
    assert catalog_delta["rss_admin"]["operation"] == "upsert"
    assert catalog_delta["rss_admin"]["payload"]["retired_at"] == "2026-11-06T00:00:00"
    _raw, _m, legacy_delta = _page(producer.engine, "sources", catalog=False, since=snapshot)
    assert legacy_delta["rss_admin"]["operation"] == "tombstone"
    # legacy 退役连带状态 tombstone,旧接收端不会被后置的状态 upsert 重建。
    _raw, _m, legacy_states = _page(producer.engine, "source_states", catalog=False)
    assert legacy_states["rss_admin"]["operation"] == "tombstone"
    _raw, _m, catalog_states = _page(producer.engine, "source_states", catalog=True)
    assert catalog_states["rss_admin"]["operation"] == "upsert"


def test_receiver_applies_catalog_fields_and_old_tombstones_are_noops(tmp_path):
    from models.db import SourceConfigRecord
    from services import archive_sync_v2

    producer = _sink(tmp_path, "producer-apply.db")
    _reconcile(producer.engine)
    legacy_consumer = _sink(tmp_path, "consumer-legacy.db")
    catalog_consumer = _sink(tmp_path, "consumer-catalog.db")
    legacy_raw, _m, _rows = _page(producer.engine, "sources", catalog=False)
    result = archive_sync_v2.import_page(legacy_consumer.engine, legacy_raw, expected_stream="sources")
    assert result["deleted"] == 0
    with Session(legacy_consumer.engine) as session:
        assert session.exec(select(SourceConfigRecord)).all() == []
    catalog_raw, _m, _rows = _page(producer.engine, "sources", catalog=True)
    archive_sync_v2.import_page(catalog_consumer.engine, catalog_raw, expected_stream="sources")
    with Session(catalog_consumer.engine) as session:
        row = session.get(SourceConfigRecord, "x_openrouter")
        assert row.origin == "catalog" and row.retired_at and row.collection_authority_id
        mirror = session.get(SourceConfigRecord, "rss_simonwillison")
        assert mirror.shape == "article" and mirror.fetcher_id == "" and mirror.daily in {True, False}


def test_projection_switch_replays_sources_and_states(tmp_path):
    from models.db import AppSettingRecord, ArchiveSyncEntityStateRecord
    from services import archive_sync_v2
    from services import remote_sync

    consumer = _sink(tmp_path, "consumer-switch.db")
    base_url = "https://authority.test"
    caps = list(archive_sync_v2.REQUIRED_CAPABILITIES)
    with Session(consumer.engine) as session:
        remote_sync.prepare_transaction_revision_consumer(
            session, base_url=base_url, username="sync", authority_id="authority-1",
            schema_version=archive_sync_v2.SCHEMA_VERSION, prepared_at=_NOW, capabilities=caps,
        )
        record = session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY)
        state = json.loads(record.value)
        target = state["targets"][base_url]
        assert target["v2_source_projection"] == "legacy"
        target["v2_streams"] = {
            stream: {"snapshot": "5", "authority_id": "authority-1"}
            for stream in ("sources", "articles", "source_states")
        }
        record.value = json.dumps(state)
        session.add(record)
        for stream in ("sources", "source_states", "articles"):
            session.add(ArchiveSyncEntityStateRecord(
                stream=stream, identity=f"{stream}-1", authority_id="authority-1",
                revision=3, operation="upsert",
            ))
        session.commit()

        remote_sync.prepare_transaction_revision_consumer(
            session, base_url=base_url, username="sync", authority_id="authority-1",
            schema_version=archive_sync_v2.SCHEMA_VERSION, prepared_at=_NOW,
            capabilities=[*caps, archive_sync_v2.SOURCE_CATALOG_CAPABILITY],
        )
        session.commit()
        target = json.loads(session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY).value)[
            "targets"][base_url]
        assert target["v2_source_projection"] == "catalog-v1"
        assert set(target["v2_streams"]) == {"articles"}
        remaining = {row.stream for row in session.exec(select(ArchiveSyncEntityStateRecord)).all()}
        assert remaining == {"articles"}
    assert remote_sync.source_catalog_ready(consumer.engine) is False
    # 生产者实际回显 legacy(例如中途回滚)完成的 sources 流不算就绪。
    remote_sync.record_v2_stream_success(
        consumer.engine, base_url=base_url, username="sync", stream="sources",
        checkpoint={"snapshot": "8", "authority_id": "authority-1", "source_projection": "legacy"},
        synced_at=_NOW,
    )
    assert remote_sync.source_catalog_ready(consumer.engine) is False
    remote_sync.record_v2_stream_success(
        consumer.engine, base_url=base_url, username="sync", stream="sources",
        checkpoint={"snapshot": "9", "authority_id": "authority-1", "source_projection": "catalog-v1"},
        synced_at=_NOW,
    )
    assert remote_sync.source_catalog_ready(consumer.engine) is True


def test_reader_catalog_is_unchanged_by_reconcile(monkeypatch, tmp_path):
    """R1c 无可见变化:镜像行 / 回填行 / 墓碑行落库前后,阅读器目录逐项相同。"""
    from dataclasses import replace as dc_replace

    from fastapi.testclient import TestClient

    import api.app as app_module
    from config import RuntimeConfig
    from tests.conftest import seed_default_accounts

    sink = _sink(tmp_path, "catalog-parity.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    monkeypatch.setattr(
        app_module, "settings", dc_replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    seed_default_accounts(sink.engine, (("admin", "admin", "admin"),))
    _seed_article(sink.engine, "simon_1", "rss_simonwillison")
    _seed_article(sink.engine, "x_ds_1", "x_deepseek_ai", content_type="social_post")
    _seed_article(sink.engine, "manual_1", "manual", content_type="manual_entry")
    _seed_article(sink.engine, "imp_1", "x_someone", content_type="social_post")
    _seed_article(sink.engine, "gone_1", "x_openrouter", content_type="social_post")
    _seed_article(sink.engine, "orphan_1", "user_rss_abcdef123456")

    def snapshot(client):
        payload = client.get("/api/reader/sources").json()
        items = payload["sources"] if isinstance(payload, dict) else payload
        # origin 是 R4 新增的来路字段:对账前存量 id 没有行(空),对账后是 legacy / catalog。
        ignored = {"configured", "registered", "subscribed", "subscriber_count", "origin"}
        return {
            item["source_id"]: {k: v for k, v in item.items() if k not in ignored}
            for item in items
        }

    with TestClient(app_module.app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        before = snapshot(client)
        _reconcile(sink.engine)
        after = snapshot(client)
    # 主人不可考的 user_rss_ 孤儿此前对所有人可见(缺陷),R1c 起只对订阅者可见。
    assert "user_rss_abcdef123456" not in after
    before.pop("user_rss_abcdef123456", None)
    assert "x_openrouter" not in before and "x_openrouter" not in after
    assert set(before) == set(after)
    # 预期改进:只在 articles 里出现的社交源有了显式平台(此前 platform 为空、图标退化)。
    assert (before["x_someone"]["platform"], after["x_someone"]["platform"]) == ("", "x")
    assert after["x_someone"]["icon"] == "𝕏"
    for key in ("platform", "icon"):
        before["x_someone"].pop(key)
        after["x_someone"].pop(key)
    for source_id, item in before.items():
        assert after[source_id] == item, source_id


def test_node_catalog_digest_and_event_meta_are_unchanged_by_reconcile(tmp_path):
    from services import source_catalog
    from services.collection_nodes import collection_node_catalog
    from services.event_assignment import load_source_meta
    from services.personal_digest import _source_role_and_shape

    sink = _sink(tmp_path, "parity.db")
    _seed_article(sink.engine, "manual_1", "manual", content_type="manual_entry")
    ids = [*source_catalog.registry_source_ids(), "manual", "dorami_daily_brief"]
    content_types = {sid: "rss_article" for sid in ids}

    def capture():
        with Session(sink.engine) as session:
            nodes = sorted(item["id"] for item in collection_node_catalog(session))
            digest = _source_role_and_shape(session, content_types)
            meta = load_source_meta(session, ids)
        return nodes, digest, meta

    before = capture()
    _reconcile(sink.engine)
    after = capture()
    assert after[0] == before[0]
    for source_id in source_catalog.registry_source_ids():
        assert after[1][source_id] == before[1][source_id], source_id
        assert after[2][source_id] == before[2][source_id], source_id
