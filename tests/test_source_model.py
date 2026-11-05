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
