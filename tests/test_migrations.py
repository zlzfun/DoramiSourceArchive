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


def test_baseline_revision_is_migration_root():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(make_alembic_config())
    bases = list(script.get_bases())
    assert bases == [BASELINE_REVISION], f"基线应为迁移链唯一根：{bases}"
