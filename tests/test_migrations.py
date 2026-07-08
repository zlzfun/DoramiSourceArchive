"""Alembic 迁移的护栏测试（阶段 2「数据层固化」）。

核心不变量：运行期用 `SQLModel.metadata.create_all()` 建表，Alembic 用版本化
迁移演进已有库——两条通道必须**永远等价**，否则新老部署会拿到不同 schema。

- ``test_upgrade_head_has_no_drift_from_metadata``：全新库 ``upgrade head`` 后，
  拿 metadata 与实库对比必须**零差异**——即「迁移链 == 模型定义 == create_all」。
  任何改了 model 却漏写迁移（或反之）的提交都会在此失败。
- ``test_ensure_migrated_adopts_legacy_db``：模拟老库（create_all 建好表但无
  ``alembic_version``），``ensure_migrated`` 应打基线戳并升到 head，且不重跑建表。
- ``test_memory_db_skips_migration``：内存库跳过迁移（无版本表）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

from models.db import SQLModel  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from storage.migrations import (  # noqa: E402
    BASELINE_REVISION,
    ensure_migrated,
    make_alembic_config,
)


def _head_revision() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(make_alembic_config()).get_current_head()


def test_upgrade_head_has_no_drift_from_metadata(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    command.upgrade(make_alembic_config(db_url), "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "render_as_batch": True}
            )
            diffs = compare_metadata(ctx, SQLModel.metadata)
    finally:
        engine.dispose()

    assert diffs == [], f"迁移链与模型 metadata 出现漂移（改了 model 却漏写迁移？）：{diffs}"


def test_ensure_migrated_adopts_legacy_db(tmp_path):
    # 模拟老库：仅用 create_all 建表，无 alembic_version。
    db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    DatabaseStorage(db_url=db_url)
    engine = create_engine(db_url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    ensure_migrated(db_url)

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        assert current == _head_revision()
        # 采纳基线不应破坏已有表。
        assert "articles" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_ensure_migrated_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'idem.db'}"
    DatabaseStorage(db_url=db_url)
    ensure_migrated(db_url)
    ensure_migrated(db_url)  # 二次调用不应报错
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == _head_revision()
    finally:
        engine.dispose()


def test_memory_db_skips_migration():
    # 内存库无迁移意义，ensure_migrated 直接返回、不建 alembic_version。
    ensure_migrated("sqlite:///:memory:")  # 不应抛错


def test_reconcile_migration_restores_dropped_declared_indexes(tmp_path):
    """模拟旧库缺索引：drop 掉声明索引并 stamp 基线，upgrade head 应把它们补回。"""
    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'legacy_idx.db'}"
    DatabaseStorage(db_url=db_url)  # create_all：此时索引齐全

    dropped = ["ix_articles_job_id", "ix_users_ai_beta_enabled", "ix_source_configs_source_owner"]
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for name in dropped:
                conn.execute(text(f"DROP INDEX {name}"))
        with engine.connect() as conn:
            before = {ix["name"] for ix in inspect(conn).get_indexes("articles")}
        assert "ix_articles_job_id" not in before
    finally:
        engine.dispose()

    # 老库采纳基线（跳过建表），再升级到含对账迁移的 head。
    cfg = make_alembic_config(db_url)
    alembic_command.stamp(cfg, BASELINE_REVISION)
    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        all_idx = set()
        for tbl in ("articles", "users", "source_configs"):
            all_idx |= {ix["name"] for ix in insp.get_indexes(tbl)}
        for name in dropped:
            assert name in all_idx, f"对账迁移未补回声明索引 {name}"
    finally:
        engine.dispose()


def test_index_status_migration_backfills_from_is_vectorized(tmp_path):
    """升级到含 index_status 的迁移前：旧库无该列；升级后 is_vectorized=1 应回填为 indexed。"""
    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'idxmig.db'}"
    cfg = make_alembic_config(db_url)
    # 升到 index_status 之前的版本（jobs 表已在、index_status 列未加）。
    alembic_command.upgrade(cfg, "c8df1ef41529")

    engine = create_engine(db_url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("articles")}
        assert "index_status" not in cols
        # 绕过 ORM（模型已含 index_status）用 raw SQL 插入两行。
        with engine.begin() as conn:
            for rid, vec in (("v1", 1), ("v0", 0)):
                conn.execute(text(
                    "INSERT INTO articles (id,title,content_type,source_id,source_url,"
                    "publish_date,fetched_date,has_content,is_vectorized,run_scope) "
                    "VALUES (:id,'t','web','s','u','2026-06-01','2026-06-01',1,:v,'ad_hoc')"
                ), {"id": rid, "v": vec})
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT id, index_status FROM articles")).all())
        assert rows["v1"] == "indexed"   # 已向量化 → 回填 indexed
        assert rows["v0"] == "pending"   # 其余 → server_default pending
    finally:
        engine.dispose()


def test_retire_migration_inlines_groups_and_converts_legacy_tasks(tmp_path):
    """实体简化阶段 2 迁移：升级前造「引用采集范围的任务 + 带 cron 的独立范围 + 旧定时任务」，
    升级后断言内联合并语义、独立范围转任务、旧任务转单节点任务、退役表/列消失。"""
    import json as _json

    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'retire.db'}"
    cfg = make_alembic_config(db_url)
    # 升到退役迁移之前的版本（node_groups/fetch_tasks/group_id 仍在）。
    alembic_command.upgrade(cfg, "8bba6f81b240")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            # 采集范围 1：被任务引用（无自身 cron）。
            conn.execute(text(
                "INSERT INTO node_groups (id,name,description,fetcher_ids_json,params_json,"
                "per_fetcher_params_json,cron_expr,per_fetcher_cron_json,is_active,created_at,updated_at) "
                "VALUES (1,'范围甲','','[\"a\",\"b\"]','{\"limit\": 5}',"
                "'{\"a\": {\"limit\": 9}}','','{}',1,'2026-01-01','2026-01-01')"
            ))
            # 采集范围 2：未被引用、自带 cron —— 应转独立任务保调度。
            conn.execute(text(
                "INSERT INTO node_groups (id,name,description,fetcher_ids_json,params_json,"
                "per_fetcher_params_json,cron_expr,per_fetcher_cron_json,is_active,created_at,updated_at) "
                "VALUES (2,'范围乙','独立调度','[\"c\"]','{}','{}','0 9 * * *','{}',1,'2026-01-01','2026-01-01')"
            ))
            # 任务 1：引用范围 1，自身节点为空、带覆盖参数。
            conn.execute(text(
                "INSERT INTO collection_jobs (id,name,description,group_id,fetcher_ids_json,params_json,"
                "per_fetcher_params_json,cron_expr,per_fetcher_cron_json,is_active,downstream_policy_json,"
                "created_at,updated_at) "
                "VALUES (1,'任务甲','',1,'[]','{\"past_days\": 2}',"
                "'{\"b\": {\"limit\": 3}}','','{}',1,'{}','2026-01-01','2026-01-01')"
            ))
            # 旧定时任务：启用中 —— 迁移后必须保持启用（旧调度路径已移除）。
            conn.execute(text(
                "INSERT INTO fetch_tasks (id,fetcher_id,cron_expr,params_json,is_active,created_at) "
                "VALUES (7,'hf_daily','0 8 * * *','{\"limit\": 4}',1,'2026-01-01')"
            ))
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert "node_groups" not in tables and "fetch_tasks" not in tables
        assert "group_id" not in {c["name"] for c in insp.get_columns("collection_jobs")}

        with engine.connect() as conn:
            jobs = [dict(r._mapping) for r in conn.execute(text("SELECT * FROM collection_jobs"))]
        by_name = {j["name"]: j for j in jobs}

        # ① 引用内联：节点来自范围、参数按 group.params < job.params <
        #    group.per_fetcher < job.per_fetcher 合并。
        inlined = by_name["任务甲"]
        assert _json.loads(inlined["fetcher_ids_json"]) == ["a", "b"]
        assert _json.loads(inlined["params_json"]) == {"limit": 5, "past_days": 2}
        assert _json.loads(inlined["per_fetcher_params_json"]) == {"a": {"limit": 9}, "b": {"limit": 3}}
        assert bool(inlined["is_active"]) is True

        # ② 独立范围 → 独立任务，cron 保留。
        standalone = by_name["范围乙"]
        assert _json.loads(standalone["fetcher_ids_json"]) == ["c"]
        assert standalone["cron_expr"] == "0 9 * * *"
        assert bool(standalone["is_active"]) is True

        # ③ 旧任务 → 单节点任务，legacy_task_id 溯源、启用状态沿用。
        legacy = by_name["hf_daily 定时采集"]
        assert _json.loads(legacy["fetcher_ids_json"]) == ["hf_daily"]
        assert legacy["cron_expr"] == "0 8 * * *"
        assert legacy["legacy_task_id"] == 7
        assert bool(legacy["is_active"]) is True
    finally:
        engine.dispose()


def test_baseline_revision_is_migration_root():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(make_alembic_config())
    bases = list(script.get_bases())
    assert bases == [BASELINE_REVISION], f"基线应为迁移链唯一根：{bases}"
