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
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from sqlalchemy import create_engine, event, inspect  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from models.db import SQLModel  # noqa: E402
from storage.fts import fts_include_object  # noqa: E402
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
                conn,
                opts={
                    "compare_type": True,
                    "render_as_batch": True,
                    # 排除 FTS5 虚拟表及 shadow 表（articles_fts*）——它们不在
                    # metadata 里，与 env.py 的 autogenerate 过滤保持一致；
                    # 只排该前缀，真实模型漂移照常被本测试捕获。
                    "include_object": fts_include_object,
                },
            )
            diffs = compare_metadata(ctx, SQLModel.metadata)
    finally:
        engine.dispose()

    assert diffs == [], f"迁移链与模型 metadata 出现漂移（改了 model 却漏写迁移？）：{diffs}"


def test_parallel_release_heads_converge_without_replay(tmp_path):
    """v3.44 合并节点须同时兼容已跑功能支线与已跑主干支线的库。"""
    for parent in ("a7d4e9f2c1b6", "a7e2f95c1d40"):
        db_url = f"sqlite:///{tmp_path / f'{parent}.db'}"
        cfg = make_alembic_config(db_url)
        command.upgrade(cfg, parent)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
            assert current == _head_revision()
            tables = set(inspect(engine).get_table_names())
            assert "article_analyses" in tables
            assert "personal_digest_editions" in tables
            user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
            assert "session_epoch" in user_columns
            assert "interest_onboarding_completed_at" in user_columns
        finally:
            engine.dispose()


def test_sqlite_revision_rolls_back_all_ddl_on_interruption(tmp_path):
    """A failed revision must not leave columns/indexes ahead of its version row."""

    db_url = f"sqlite:///{tmp_path / 'interrupted.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "d2c4f6a8b0e1")

    def fail_mid_revision(_conn, _cursor, statement, _params, _context, _many):
        if "ix_tag_retag_job_items_article_id" in statement and "CREATE" in statement.upper():
            raise RuntimeError("simulated migration interruption")

    event.listen(Engine, "before_cursor_execute", fail_mid_revision)
    try:
        with pytest.raises(RuntimeError, match="simulated migration interruption"):
            command.upgrade(cfg, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_mid_revision)

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == "d2c4f6a8b0e1"
            columns = {
                column["name"]
                for column in inspect(conn).get_columns("personal_digest_editions")
            }
            assert "interest_snapshot_json" not in columns
            indexes = {
                index["name"]
                for index in inspect(conn).get_indexes("tag_retag_job_items")
            }
            assert "ix_tag_retag_job_items_article_id" not in indexes
    finally:
        engine.dispose()

def test_intermediate_pr_data_cleanup_is_scoped_and_privacy_minimizing(tmp_path):
    import json

    import sqlalchemy as sa
    from sqlalchemy import text
    from sqlmodel import Session

    from models.db import (
        ArticleAnalysisAttemptRecord,
        ArticleAnalysisRecord,
        PersonalDigestEditionRecord,
        PersonalDigestItemRecord,
        TagRetagJobItemRecord,
        TagRetagJobRecord,
        TaxonomyVersionRecord,
        UserRecord,
    )

    db_url = f"sqlite:///{tmp_path / 'intermediate-pr.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "a4b9d2e6f1c3")
    stamp = "2026-09-03T00:00:00+00:00"

    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            def insert_against_physical_schema(model):
                """Insert a current model into the intentionally older schema."""

                table = sa.Table(
                    model.__tablename__,
                    sa.MetaData(),
                    autoload_with=session.connection(),
                )
                values = {
                    column.name: getattr(model, column.name)
                    for column in table.columns
                    if not (column.primary_key and getattr(model, column.name) is None)
                }
                result = session.execute(table.insert().values(**values))
                if "id" in table.c and getattr(model, "id", None) is None:
                    model.id = result.inserted_primary_key[0]

            session.add(UserRecord(
                username="alice",
                password_hash="test",
                created_at=stamp,
                updated_at=stamp,
            ))
            session.add(TaxonomyVersionRecord(
                version=1,
                status="active",
                created_at=stamp,
            ))
            for article_id in ("stale-only", "shared-live"):
                # This fixture intentionally stops at an older revision.  Insert
                # through that revision's physical schema instead of the current
                # ORM, which may declare columns added by later migrations.
                session.execute(
                    text(
                        "INSERT INTO articles "
                        "(id,title,content_type,source_id,source_url,publish_date,"
                        "fetched_date,run_scope,has_content,content,extensions_json,read_count) "
                        "VALUES (:id,:id,'article','source-a',:url,:stamp,:stamp,"
                        "'ad_hoc',1,'body','{}',0)"
                    ),
                    {
                        "id": article_id,
                        "url": "https://example.test/article",
                        "stamp": stamp,
                    },
                )
            session.flush()
            # 同理走物理 schema:该旧版本仍有 NOT NULL 的 one_sentence_summary 列
            # (d8b3f1a6c9e2 删除),当前 ORM 已不声明它。
            analysis_sql = text(
                "INSERT INTO article_analyses "
                "(article_id,status,tagging_status,quality_score,dimension_scores_json,"
                "score_reason,one_sentence_summary,summary,content_features_json,"
                "entities_json,display_tags_json,content_hash,model_name,prompt_version,"
                "scoring_version,taxonomy_version,attempt_count,lease_owner,"
                "lease_expires_at,analyzed_at,created_at,updated_at) "
                "VALUES (:id,:status,:tagging,:score,'{}','','','','[]','[]','[]',"
                ":hash,'','','',0,0,:lease_owner,:lease_expires,:analyzed_at,:stamp,:stamp)"
            )
            session.execute(analysis_sql, {
                "id": "stale-only", "status": "running", "tagging": "succeeded",
                "score": 8.0, "hash": "hash-stale", "lease_owner": "old-worker",
                "lease_expires": "2099-01-01T00:00:00+00:00", "analyzed_at": stamp,
                "stamp": stamp,
            })
            session.execute(analysis_sql, {
                "id": "shared-live", "status": "pending", "tagging": "pending",
                "score": None, "hash": "hash-shared", "lease_owner": None,
                "lease_expires": None, "analyzed_at": None, "stamp": stamp,
            })
            session.add(ArticleAnalysisAttemptRecord(
                article_id="stale-only",
                attempt_no=1,
                operation="full_analysis",
                status="running",
                content_hash="hash-stale",
                started_at=stamp,
                created_at=stamp,
            ))
            stale_job = TagRetagJobRecord(
                taxonomy_version=1,
                operation="full_analysis",
                status="cancelled",
                last_error="superseded while enforcing one active full-analysis job",
                created_at=stamp,
                updated_at=stamp,
            )
            live_job = TagRetagJobRecord(
                taxonomy_version=1,
                operation="full_analysis",
                status="queued",
                created_at=stamp,
                updated_at=stamp,
            )
            session.add(stale_job)
            session.add(live_job)
            session.flush()
            session.add(TagRetagJobItemRecord(
                job_id=stale_job.id,
                article_id="stale-only",
                article_id_snapshot="stale-only",
                status="queued",
                target_content_hash="hash-stale",
                created_at=stamp,
                updated_at=stamp,
            ))
            session.add(TagRetagJobItemRecord(
                job_id=stale_job.id,
                article_id="shared-live",
                article_id_snapshot="shared-live",
                status="queued",
                target_content_hash="hash-shared",
                created_at=stamp,
                updated_at=stamp,
            ))
            session.add(TagRetagJobItemRecord(
                job_id=live_job.id,
                article_id="shared-live",
                article_id_snapshot="shared-live",
                status="pending",
                target_content_hash="hash-shared",
                created_at=stamp,
                updated_at=stamp,
            ))
            pending = PersonalDigestEditionRecord(
                owner_username="alice",
                report_date="2026-09-03",
                revision=1,
                status="generating",
                check_after=stamp,
                cutoff_at=stamp,
                interest_snapshot_json="[]",
                generation_token="old-token",
                generation_lease_expires_at="2099-01-01T00:00:00+00:00",
                generation_reason="first_open",
                created_at=stamp,
                updated_at=stamp,
            )
            ready = PersonalDigestEditionRecord(
                owner_username="alice",
                report_date="2026-09-02",
                revision=1,
                status="ready",
                check_after=stamp,
                cutoff_at=stamp,
                interest_snapshot_json="[]",
                generation_reason="scheduled",
                created_at=stamp,
                updated_at=stamp,
            )
            insert_against_physical_schema(pending)
            insert_against_physical_schema(ready)
            for edition_id, position in ((pending.id, 0), (ready.id, 0)):
                session.add(PersonalDigestItemRecord(
                    edition_id=edition_id,
                    article_id="stale-only",
                    position=position,
                    section="test",
                    selection_lane="quality",
                    snapshot_json=json.dumps({"title": "kept", "content": "remove me"}),
                    created_at=stamp,
                ))
            session.commit()
            stale_job_id = int(stale_job.id)
            live_job_id = int(live_job.id)
            pending_id = int(pending.id)
            ready_id = int(ready.id)
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            stale_items = session.exec(
                TagRetagJobItemRecord.__table__.select().where(
                    TagRetagJobItemRecord.job_id == stale_job_id
                )
            ).mappings().all()
            live_item = session.exec(
                TagRetagJobItemRecord.__table__.select().where(
                    TagRetagJobItemRecord.job_id == live_job_id
                )
            ).mappings().one()
            assert {row["status"] for row in stale_items} == {"skipped"}
            assert {row["last_error"] for row in stale_items} == {
                "job_superseded_during_migration"
            }
            assert live_item["status"] == "pending"

            stale_analysis = session.get(ArticleAnalysisRecord, "stale-only")
            shared_analysis = session.get(ArticleAnalysisRecord, "shared-live")
            assert stale_analysis.status == "succeeded"
            assert stale_analysis.lease_owner is None
            assert shared_analysis.status == "pending"
            attempt = session.exec(
                ArticleAnalysisAttemptRecord.__table__.select().where(
                    ArticleAnalysisAttemptRecord.article_id == "stale-only"
                )
            ).mappings().one()
            assert attempt["status"] == "skipped"

            assert session.get(PersonalDigestEditionRecord, pending_id).status == "superseded"
            assert session.get(PersonalDigestEditionRecord, pending_id).generation_token is None
            assert session.get(PersonalDigestEditionRecord, ready_id).status == "ready"
            snapshots = session.exec(
                PersonalDigestItemRecord.__table__.select()
            ).mappings().all()
            assert [json.loads(row["snapshot_json"]) for row in snapshots] == [
                {"title": "kept"},
                {"title": "kept"},
            ]
    finally:
        engine.dispose()


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


def test_digest_intent_migration_coalesces_legacy_active_revisions(tmp_path):
    import sqlalchemy as sa
    from models.db import PersonalDigestEditionRecord, UserRecord

    db_url = f"sqlite:///{tmp_path / 'legacy-digest-active.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "d8b3f1a6c9e2")
    stamp = "2026-09-04T08:30:00+08:00"
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            users = sa.Table("users", metadata, autoload_with=conn)
            editions = sa.Table("personal_digest_editions", metadata, autoload_with=conn)

            user = UserRecord(
                username="alice",
                password_hash="hash",
                created_at=stamp,
                updated_at=stamp,
            )
            conn.execute(users.insert().values(**{
                column.name: getattr(user, column.name)
                for column in users.columns
                if not (column.primary_key and getattr(user, column.name, None) is None)
            }))
            fixtures = (
                ("2026-09-04", 1, "pending"),
                ("2026-09-04", 2, "generating"),
                ("2026-09-04", 3, "ready"),
                ("2026-09-05", 1, "pending"),
                ("2026-09-05", 2, "generating"),
            )
            for report_date, revision, status in fixtures:
                edition = PersonalDigestEditionRecord(
                    owner_username="alice",
                    report_date=report_date,
                    revision=revision,
                    status=status,
                    check_after=stamp,
                    cutoff_at=stamp,
                    generation_token=f"token-{revision}" if status == "generating" else None,
                    generation_lease_expires_at=(
                        "2099-01-01T00:00:00+08:00" if status == "generating" else None
                    ),
                    generation_reason="manual_rebuild",
                    generated_at=stamp if status == "ready" else None,
                    created_at=stamp,
                    updated_at=stamp,
                )
                conn.execute(editions.insert().values(**{
                    column.name: getattr(edition, column.name)
                    for column in editions.columns
                    if not (column.primary_key and getattr(edition, column.name, None) is None)
                }))
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT report_date, revision, status, generation_token, generation_lease_expires_at, "
                "desired_requested_at, sync_stale, analysis_incomplete "
                "FROM personal_digest_editions ORDER BY report_date, revision"
            )).mappings().all()
            cleanup_marker = conn.execute(sa.text(
                "SELECT value FROM app_settings "
                "WHERE key='migration:a7d4e2f9c1b8:digest_cleanup'"
            )).scalar_one_or_none()
        assert [
            (row["report_date"], row["revision"], row["status"]) for row in rows
        ] == [
            ("2026-09-04", 1, "superseded"),
            ("2026-09-04", 2, "superseded"),
            ("2026-09-04", 3, "ready"),
            ("2026-09-05", 1, "superseded"),
            ("2026-09-05", 2, "generating"),
        ]
        for row in (rows[0], rows[1], rows[3]):
            assert row["generation_token"] is None
            assert row["generation_lease_expires_at"] is None
            assert row["desired_requested_at"] is None
        assert all(not bool(row["sync_stale"]) for row in rows)
        assert all(not bool(row["analysis_incomplete"]) for row in rows)
        assert cleanup_marker is not None
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="恢复升级前备份"):
        command.downgrade(cfg, "d8b3f1a6c9e2")


def _make_pre_baseline_legacy_db(tmp_path, name):
    """构造比基线更老的断代老库:create_all 后删掉一列一表(模拟旧手写 ALTER
    路径时代的历史断面——生产实例:users 缺 ai_beta_enabled、缺 login_events 表)。"""
    db_url = f"sqlite:///{tmp_path / name}"
    DatabaseStorage(db_url=db_url)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            # 先删索引再删列(SQLite 拒删被索引引用的列;真实老库本就无此索引)
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_users_ai_beta_enabled")
            conn.exec_driver_sql("ALTER TABLE users DROP COLUMN ai_beta_enabled")
            conn.exec_driver_sql("DROP TABLE login_events")
    finally:
        engine.dispose()
    return db_url


def _assert_healed_to_head(db_url):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == _head_revision()
        insp = inspect(engine)
        assert "login_events" in insp.get_table_names()
        user_cols = {c["name"] for c in insp.get_columns("users")}
        assert "ai_beta_enabled" in user_cols
        assert "ix_users_ai_beta_enabled" in {ix["name"] for ix in insp.get_indexes("users")}
    finally:
        engine.dispose()


def test_ensure_migrated_aligns_pre_baseline_legacy_db(tmp_path):
    """断代早于基线的老库(缺列缺表):收养前对齐到基线,再升级到 head 不崩。

    肇因:生产库 users 缺 ai_beta_enabled,ccae184ca0a1 对不存在的列建索引而崩。"""
    db_url = _make_pre_baseline_legacy_db(tmp_path, "prebaseline.db")
    ensure_migrated(db_url)
    _assert_healed_to_head(db_url)


def test_ensure_migrated_resumes_after_stamped_then_failed(tmp_path):
    """生产现场形态:上次收养已 stamp 到基线、升级中途崩——重跑要能继续对齐并修复。"""
    db_url = _make_pre_baseline_legacy_db(tmp_path, "stamped.db")
    command.stamp(make_alembic_config(db_url), BASELINE_REVISION)
    ensure_migrated(db_url)
    _assert_healed_to_head(db_url)


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
    """历史迁移回放守卫：升级到含 index_status 的迁移前旧库无该列；升级后
    is_vectorized=1 应回填为 indexed。两列已在 f2c9d4e07a11（v3.31 退役清仓）
    删除，故断言停在删列迁移之前的 9548caa15eea，不再升到 head。"""
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

    alembic_command.upgrade(cfg, "9548caa15eea")  # 停在 f2c9d4e07a11 删列迁移之前

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


def test_per_fetcher_cron_retirement_splits_overrides(tmp_path):
    """单节点 cron 退役迁移(d41acead77b0):带覆盖的任务按 distinct cron 拆成独立任务
    (faithful 保调度),覆盖节点与其参数移交新任务、原任务保留其余;列随后 DROP。"""
    import json as _json

    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'cronsplit.db'}"
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "8f6d93196258")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO collection_jobs (id,name,description,fetcher_ids_json,params_json,"
                "per_fetcher_params_json,cron_expr,per_fetcher_cron_json,is_active,downstream_policy_json,"
                "created_at,updated_at) "
                "VALUES (1,'混排任务','说明','[\"a\",\"b\",\"c\",\"d\"]','{\"limit\": 5}',"
                "'{\"a\": {\"limit\": 9}, \"c\": {\"limit\": 2}}','0 9 * * *',"
                "'{\"a\": \"0 */4 * * *\", \"c\": \"0 */4 * * *\", \"d\": \"30 8 * * 1-5\"}',"
                "1,'{}','2026-01-01','2026-01-01')"
            ))
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(collection_jobs)"))}
            assert "per_fetcher_cron_json" not in cols
            rows = conn.execute(text(
                "SELECT name, fetcher_ids_json, per_fetcher_params_json, cron_expr FROM collection_jobs ORDER BY id"
            )).fetchall()
        assert len(rows) == 3  # 原任务 + 两个 distinct cron 拆分任务
        by_cron = {r[3]: r for r in rows}
        # 原任务:剩余节点 b,保留整体 cron 与 b 无关的参数剔除
        origin = by_cron["0 9 * * *"]
        assert _json.loads(origin[1]) == ["b"] and _json.loads(origin[2]) == {}
        # 拆分任务 1:a+c 同 cron 同组,参数随节点移交
        split1 = by_cron["0 */4 * * *"]
        assert _json.loads(split1[1]) == ["a", "c"]
        assert _json.loads(split1[2]) == {"a": {"limit": 9}, "c": {"limit": 2}}
        assert split1[0].startswith("混排任务 · 独立时刻")
        # 拆分任务 2:d 单节点
        split2 = by_cron["30 8 * * 1-5"]
        assert _json.loads(split2[1]) == ["d"] and _json.loads(split2[2]) == {}
    finally:
        engine.dispose()


def test_retired_param_fields_purged_from_jobs(tmp_path):
    """参数固化波清洗迁移(e7a3c19b5d02):已退场字段从任务参数剔除,
    存活字段保留,generic_* 节点(模板参数面)不清洗。"""
    import json as _json

    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'purge.db'}"
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "d41acead77b0")

    per_params = {
        "web_anthropic_news": {"limit": 20, "fetch_detail": True, "detail_max_chars": 12000},
        "github_deepseek_repositories": {"limit": 20, "include_forks": False, "readme_max_chars": 1200},
        "rss_hn_ai": {"limit": 20, "min_points": 25, "fetch_detail_if_missing": True},
        "generic_rss": {"limit": 5, "detail_max_chars": 9000},
    }
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO collection_jobs (id,name,description,fetcher_ids_json,params_json,"
                "per_fetcher_params_json,cron_expr,is_active,downstream_policy_json,created_at,updated_at) "
                "VALUES (1,'脏参任务','','[]','{}',:pp,'',1,'{}','2026-01-01','2026-01-01')"
            ), {"pp": _json.dumps(per_params)})
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            raw = conn.execute(text("SELECT per_fetcher_params_json FROM collection_jobs WHERE id=1")).scalar()
    finally:
        engine.dispose()
    cleaned = _json.loads(raw)
    assert cleaned["web_anthropic_news"] == {"limit": 20}
    assert cleaned["github_deepseek_repositories"] == {"limit": 20}
    assert cleaned["rss_hn_ai"] == {"limit": 20}
    # 模板节点参数面不清洗
    assert cleaned["generic_rss"] == {"limit": 5, "detail_max_chars": 9000}


def test_reader_read_states_migration_adds_missing_is_read(tmp_path):
    """老形状收养:运行期 create_all 抢先建出早期形状的 reader_article_read_states
    (无 is_read 列)时,未读体系迁移应补列对齐,存量行回填为显式已读(=1)。"""
    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'oldshape.db'}"
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "e7a3c19b5d02")  # 未读体系迁移的前一版

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE reader_article_read_states ("
                "owner_username VARCHAR NOT NULL, article_id VARCHAR NOT NULL, "
                "read_at VARCHAR NOT NULL, PRIMARY KEY (owner_username, article_id))"
            ))
            conn.execute(text(
                "CREATE INDEX ix_reader_article_read_states_article_id "
                "ON reader_article_read_states (article_id)"
            ))
            conn.execute(text(
                "INSERT INTO reader_article_read_states VALUES ('u', 'a1', '2026-07-16T00:00:00')"
            ))
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("reader_article_read_states")}
        assert "is_read" in columns
        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT is_read FROM reader_article_read_states WHERE article_id='a1'"
            )).scalar()
        assert row == 1  # 存量行回填为显式已读
        assert "reader_read_cursors" in inspect(engine).get_table_names()  # 另一表照常创建
    finally:
        engine.dispose()


def test_analysis_migrations_upgrade_existing_source_configs_with_default_on(tmp_path):
    """公开自定源默认分析，签名源保持关闭，且新表无 FK 漂移。"""
    import sqlalchemy as sa
    from alembic import command as alembic_command
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path / 'analysis-upgrade.db'}"
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "d9de7994582c")

    engine = create_engine(db_url)
    try:
        metadata = sa.MetaData()
        source_configs = sa.Table("source_configs", metadata, autoload_with=engine)
        values = {}
        for column in source_configs.columns:
            if column.nullable or column.server_default is not None:
                continue
            if isinstance(column.type, sa.Boolean):
                values[column.name] = True
            elif isinstance(column.type, sa.Integer):
                values[column.name] = 0
            else:
                values[column.name] = ""
        values.update(
            source_id="legacy-rss",
            name="Legacy RSS",
            source_type="rss",
            url="https://example.com/feed.xml",
            created_at="2026-08-31T00:00:00+08:00",
            updated_at="2026-08-31T00:00:00+08:00",
        )
        with engine.begin() as conn:
            conn.execute(source_configs.insert().values(**values))
            private_values = dict(values)
            private_values.update(
                source_id="private-rss",
                name="Unlisted public RSS",
                url="https://private.example.com/feed.xml",
                owner_username="alice",
            )
            conn.execute(source_configs.insert().values(**private_values))
            signed_values = dict(values)
            signed_values.update(
                source_id="signed-rss",
                name="Signed RSS",
                url="https://private.example.com/feed.xml?token=secret",
                owner_username="alice",
            )
            conn.execute(source_configs.insert().values(**signed_values))
            string_private_values = dict(values)
            string_private_values.update(
                source_id="string-private-rss",
                name="String Classified RSS",
                url="https://private.example.com/feed.xml",
                owner_username="alice",
                params_json=json.dumps({"credentialed_private": "true"}),
            )
            conn.execute(source_configs.insert().values(**string_private_values))
            path_signed_values = dict(values)
            path_signed_values.update(
                source_id="path-signed-rss",
                name="Path Signed RSS",
                url="https://private.example.com/feeds/a81f9c4d38bb479ca09372fe/token.xml",
                owner_username="alice",
            )
            conn.execute(source_configs.insert().values(**path_signed_values))
            unknown_query_signed_values = dict(values)
            unknown_query_signed_values.update(
                source_id="unknown-query-signed-rss",
                name="Unknown Query Signed RSS",
                url="https://private.example.com/feed.xml?subscriber=Abc123Def456Ghi789Jkl012",
                owner_username="alice",
            )
            conn.execute(source_configs.insert().values(**unknown_query_signed_values))
            public_query_values = dict(values)
            public_query_values.update(
                source_id="public-query-rss",
                name="Public Channel RSS",
                url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw",
                owner_username="alice",
            )
            conn.execute(source_configs.insert().values(**public_query_values))
            for source_id, url in (
                ("authkey-rss", "https://private.example.com/feed.xml?authkey=shortsecret"),
                ("letter-session-rss", "https://private.example.com/feed.xml?session=abcdefghijklmnopqrstuvwxyzabcdef"),
                ("letter-path-rss", "https://private.example.com/abcdefghijklmnopqrstuvwxyzabcdef"),
            ):
                credential_values = dict(values)
                credential_values.update(
                    source_id=source_id,
                    name=source_id,
                    url=url,
                    owner_username="alice",
                )
                conn.execute(source_configs.insert().values(**credential_values))
            malformed_values = dict(values)
            malformed_values.update(
                source_id="malformed-params-rss",
                name="Malformed params RSS",
                url="https://feeds.example.com/public.xml",
                owner_username="alice",
                params_json="{legacy-secret-not-json",
            )
            conn.execute(source_configs.insert().values(**malformed_values))
    finally:
        engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        expected_tables = {
            "article_analyses",
            "cms_tags",
            "cms_tag_candidate_evidence",
            "taxonomy_versions",
            "tag_retag_jobs",
            "user_interest_tags",
            "personal_digest_editions",
            "personal_digest_items",
        }
        assert expected_tables.issubset(set(insp.get_table_names()))
        with engine.connect() as conn:
            enabled = conn.execute(
                text(
                    "SELECT ai_analysis_enabled FROM source_configs "
                    "WHERE source_id='legacy-rss'"
                )
            ).scalar_one()
            assert bool(enabled) is True
            custom_enabled = conn.execute(
                text(
                    "SELECT ai_analysis_enabled FROM source_configs "
                    "WHERE source_id='private-rss'"
                )
            ).scalar_one()
            assert bool(custom_enabled) is True
            signed_enabled, signed_params = conn.execute(
                text(
                    "SELECT ai_analysis_enabled, params_json FROM source_configs "
                    "WHERE source_id='signed-rss'"
                )
            ).one()
            assert bool(signed_enabled) is False
            assert json.loads(signed_params)["credentialed_private"] is True
            for source_id in (
                "string-private-rss",
                "path-signed-rss",
                "unknown-query-signed-rss",
                "authkey-rss",
                "letter-session-rss",
                "letter-path-rss",
            ):
                row_enabled, row_params = conn.execute(
                    text(
                        "SELECT ai_analysis_enabled, params_json FROM source_configs "
                        "WHERE source_id=:source_id"
                    ),
                    {"source_id": source_id},
                ).one()
                assert bool(row_enabled) is False
                assert json.loads(row_params)["credentialed_private"] is True
            public_enabled, public_params = conn.execute(
                text(
                    "SELECT ai_analysis_enabled, params_json FROM source_configs "
                    "WHERE source_id='public-query-rss'"
                )
            ).one()
            assert bool(public_enabled) is True
            assert json.loads(public_params)["credentialed_private"] is False
            malformed_enabled, malformed_params = conn.execute(
                text(
                    "SELECT ai_analysis_enabled, params_json FROM source_configs "
                    "WHERE source_id='malformed-params-rss'"
                )
            ).one()
            assert bool(malformed_enabled) is False
            assert malformed_params == "{legacy-secret-not-json"
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_ensure_migrated_tolerates_forked_heads(tmp_path, monkeypatch):
    """下游分叉仓形态(内网 intranet):分叉仓自带迁移支线,合入 main 新迁移后
    DAG 双头——git 零冲突,但 upgrade("head") 无条件报错、启动路径直接炸。
    ensure_migrated 应检测多头并 upgrade("heads") 并行全升两条支线。"""
    import shutil

    import storage.migrations as migrations_module

    main_head = _head_revision()

    # 复制真实迁移链,追加一个从基线分叉的支线迁移 → 双头
    script_dir = tmp_path / "alembic"
    shutil.copytree(migrations_module._PROJECT_ROOT / "alembic", script_dir)
    (script_dir / "versions" / "zzzz_fork_branch.py").write_text(
        '"""模拟内网本地迁移支线(与主线同父,git 无冲突但 DAG 分叉)。"""\n'
        "import sqlalchemy as sa\n"
        "from alembic import op\n\n"
        "revision = 'aaaafork0001'\n"
        f"down_revision = '{BASELINE_REVISION}'\n"
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade():\n"
        "    op.create_table('intranet_only', sa.Column('id', sa.Integer, primary_key=True))\n\n\n"
        "def downgrade():\n"
        "    op.drop_table('intranet_only')\n",
        encoding="utf-8",
    )

    real_make = migrations_module.make_alembic_config

    def patched_make(db_url=None):
        cfg = real_make(db_url)
        cfg.set_main_option("script_location", str(script_dir))
        return cfg

    monkeypatch.setattr(migrations_module, "make_alembic_config", patched_make)

    db_url = f"sqlite:///{tmp_path / 'forked.db'}"
    ensure_migrated(db_url)  # 双头下不应报错

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "intranet_only" in insp.get_table_names()  # 支线头已应用
        assert "articles" in insp.get_table_names()       # 主线头已应用
        with engine.connect() as conn:
            heads = set(MigrationContext.configure(conn).get_current_heads())
        assert heads == {"aaaafork0001", main_head}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("schedule", "expected_marker"),
    [
        ({"enabled": True, "protocol": "v2", "source_ids": []}, True),
        ({"enabled": True, "source_ids": []}, True),
        ({"enabled": True, "source_ids": ["rss_platform"]}, False),
        ({"enabled": True, "protocol": "v1", "source_ids": ["rss_platform"]}, False),
    ],
)
def test_archive_sync_v2_migration_fences_only_explicit_v2_schedule(
    tmp_path,
    schedule,
    expected_marker,
):
    """Upgrade closes the startup window without guessing legacy protocol intent."""

    from sqlalchemy import text

    suffix = "-".join(sorted(schedule)) + str(schedule.get("protocol") or "legacy")
    db_url = f"sqlite:///{tmp_path / f'consumer-{suffix}.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "d8b3f1a6c9e2")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:key, :value)"),
                {
                    "key": "remote_sync:schedule",
                    "value": json.dumps(schedule),
                },
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            marker = conn.execute(
                text(
                    "SELECT value FROM app_settings "
                    "WHERE key='remote_sync:v2_consumer_mode'"
                )
            ).scalar_one_or_none()
            stored_schedule = conn.execute(
                text("SELECT value FROM app_settings WHERE key='remote_sync:schedule'")
            ).scalar_one()
        assert (marker is not None) is expected_marker
        assert json.loads(stored_schedule) == schedule
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "blocker",
    [
        "consumer_marker",
        "source_authority",
        "analysis_authority",
        "remote_candidate",
        "digest_intent",
        "enabled_v2_schedule",
    ],
)
def test_archive_sync_v2_downgrade_refuses_to_reopen_live_writers(tmp_path, blocker):
    from sqlalchemy import text
    from sqlmodel import Session
    from models.db import (
        ArticleAnalysisRecord,
        ArticleRecord,
        CmsTagCandidateRecord,
        PersonalDigestEditionRecord,
        RemoteCandidateEvidenceRecord,
        SourceConfigRecord,
    )

    db_url = f"sqlite:///{tmp_path / f'downgrade-{blocker}.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            if blocker == "consumer_marker":
                conn.execute(
                    text("INSERT INTO app_settings (key, value) VALUES (:key, :value)"),
                    {
                        "key": "remote_sync:v2_consumer_mode",
                        "value": '{"active":true}',
                    },
                )
            elif blocker == "remote_candidate":
                pass
            elif blocker == "digest_intent":
                pass
            elif blocker == "enabled_v2_schedule":
                conn.execute(
                    text("INSERT INTO app_settings (key, value) VALUES (:key, :value)"),
                    {
                        "key": "remote_sync:schedule",
                        "value": '{"enabled":true,"protocol":"v2"}',
                    },
                )
        if blocker == "source_authority":
            with Session(engine) as session:
                session.add(SourceConfigRecord(
                    source_id="remote",
                    name="Remote",
                    collection_authority_id="producer-a",
                    created_at="2026-09-04",
                    updated_at="2026-09-04",
                ))
                session.commit()
        elif blocker == "analysis_authority":
            with Session(engine) as session:
                session.add(ArticleRecord(
                    id="remote-analysis", title="Remote", content_type="article",
                    source_id="remote", source_url="", publish_date="now",
                    fetched_date="now", has_content=True, content="body",
                ))
                session.add(ArticleAnalysisRecord(
                    article_id="remote-analysis", status="succeeded",
                    tagging_status="succeeded", content_hash="hash",
                    authority_id="producer-a", authority_revision="rev-1",
                    created_at="now", updated_at="now",
                ))
                session.commit()
        elif blocker == "remote_candidate":
            with Session(engine) as session:
                candidate = CmsTagCandidateRecord(
                    label="Agents", normalized_label="agents", proposed_kind="topic",
                    first_seen_at="now", last_seen_at="now", created_at="now", updated_at="now",
                )
                session.add(candidate)
                session.flush()
                session.add(RemoteCandidateEvidenceRecord(
                    candidate_id=candidate.id, authority_id="producer-a",
                    article_fingerprint="fingerprint", source_provenance="rss",
                    label="Agents", normalized_label="agents", proposed_kind="topic",
                    confidence=0.9, prompt_version="v1", sync_snapshot="snap",
                    created_at="now",
                ))
                session.commit()
        elif blocker == "digest_intent":
            with Session(engine) as session:
                session.add(PersonalDigestEditionRecord(
                    owner_username="alice", report_date="2026-09-04", revision=1,
                    status="pending", check_after="2026-09-04", cutoff_at="2026-09-04",
                    desired_generation_reason="manual_rebuild",
                    desired_requested_at="2026-09-04", created_at="now", updated_at="now",
                ))
                session.commit()
    finally:
        engine.dispose()


    with pytest.raises(RuntimeError, match="恢复升级前备份"):
        command.downgrade(cfg, "d8b3f1a6c9e2")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == _head_revision()
    finally:
        engine.dispose()


def test_archive_sync_v2_downgrade_allows_empty_non_consumer_database(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'downgrade-clean.db'}"
    cfg = make_alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "d8b3f1a6c9e2")
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() == "d8b3f1a6c9e2"
    finally:
        engine.dispose()
