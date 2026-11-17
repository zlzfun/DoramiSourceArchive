"""source table becomes the full set of sources (issue #262 R1c)

源模型重构:源 / 采集绑定 / 抓取器三层分离。本迁移只做结构与本机存量行的确定性回填:

- ``source_configs`` 加列 origin / shape / platform / icon / avatar_url / homepage_url /
  default_kind / language / daily / legacy_export(SQLite 原生 ADD COLUMN,不重建表,
  表上的归档同步触发器不受影响);
- 本机自有的存量行回填 origin(读者自定源 → reader,其余 → admin)与 shape / platform
  (按 source_type);带 ``collection_authority_id`` 的同步行不在本机改写,它们的新字段只经
  ``source-catalog-v1`` 投影从外网到达;
- legacy_export 默认真:R1c 之前就存在的公共行照旧向旧接收端导出;
- 新表 ``source_identity_keys``(身份键 → 源)与 ``source_migration_ledger``(迁移台账)。

62 个预设类的镜像行、只在 articles 里出现的 id 的回填、墓碑退役由外网启动时的对账完成
(``services.source_rows``),不在迁移里:它们依赖抓取器注册表这份代码。

downgrade:删两张新表与新列(新列不被触发器引用)。按方案,正常回退不走 downgrade,
而是把 ``[sources] model`` 切回 compat、保留数据(F12)。

Revision ID: d7f3b9e2a614
Revises: c5e1a7b3d9f2
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "d7f3b9e2a614"
down_revision: Union[str, Sequence[str], None] = "c5e1a7b3d9f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TEXT_COLUMNS = (
    "origin",
    "shape",
    "platform",
    "icon",
    "avatar_url",
    "homepage_url",
    "default_kind",
    "language",
)


def _columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("source_configs")}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    for name in _TEXT_COLUMNS:
        if name not in existing:
            op.add_column(
                "source_configs",
                sa.Column(
                    name,
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default=sa.text("''"),
                ),
            )
    if "daily" not in existing:
        op.add_column(
            "source_configs",
            sa.Column("daily", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if "legacy_export" not in existing:
        op.add_column(
            "source_configs",
            sa.Column("legacy_export", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )
    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("source_configs")}
    if "ix_source_configs_origin" not in index_names:
        op.create_index("ix_source_configs_origin", "source_configs", ["origin"], unique=False)

    tables = set(sa.inspect(bind).get_table_names())
    if "source_identity_keys" not in tables:
        op.create_table(
            "source_identity_keys",
            sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )
        op.create_index(
            "ix_source_identity_keys_source_id", "source_identity_keys", ["source_id"], unique=False
        )
    if "source_migration_ledger" not in tables:
        op.create_table(
            "source_migration_ledger",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("batch", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("source_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("pre_image_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_source_migration_ledger_batch", "source_migration_ledger", ["batch"], unique=False
        )
        op.create_index(
            "ix_source_migration_ledger_source_id",
            "source_migration_ledger",
            ["source_id"],
            unique=False,
        )

    local = "COALESCE(collection_authority_id, '') = '' AND COALESCE(origin, '') = ''"
    op.execute(
        "UPDATE source_configs SET origin = 'reader' WHERE "
        f"{local} AND (COALESCE(owner_username, '') != '' OR source_id LIKE 'user\\_rss\\_%' ESCAPE '\\')"
    )
    op.execute(f"UPDATE source_configs SET origin = 'admin' WHERE {local}")
    local_shape = "COALESCE(collection_authority_id, '') = '' AND COALESCE(shape, '') = ''"
    op.execute(
        "UPDATE source_configs SET shape = 'social', platform = CASE WHEN COALESCE(platform, '') = '' "
        "THEN 'x' ELSE platform END WHERE "
        f"{local_shape} AND lower(trim(COALESCE(source_type, ''))) IN ('x', 'twitter', 'x_timeline')"
    )
    op.execute(
        "UPDATE source_configs SET shape = 'podcast' WHERE "
        f"{local_shape} AND lower(trim(COALESCE(source_type, ''))) IN ('podcast', 'podcast_rss')"
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "source_migration_ledger" in tables:
        op.drop_index("ix_source_migration_ledger_source_id", table_name="source_migration_ledger")
        op.drop_index("ix_source_migration_ledger_batch", table_name="source_migration_ledger")
        op.drop_table("source_migration_ledger")
    if "source_identity_keys" in tables:
        op.drop_index("ix_source_identity_keys_source_id", table_name="source_identity_keys")
        op.drop_table("source_identity_keys")
    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("source_configs")}
    if "ix_source_configs_origin" in index_names:
        op.drop_index("ix_source_configs_origin", table_name="source_configs")
    existing = _columns(bind)
    for name in (*_TEXT_COLUMNS, "daily", "legacy_export"):
        if name in existing:
            if bind.dialect.name == "sqlite":
                op.execute(f"ALTER TABLE source_configs DROP COLUMN {name}")
            else:
                op.drop_column("source_configs", name)
