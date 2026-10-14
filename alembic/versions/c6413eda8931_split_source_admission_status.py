"""split admission status out of source_configs.category (issue #178)

Revision ID: c6413eda8931
Revises: 7ec8c19b1356
Create Date: 2026-10-12 16:00:00

`category` 混着三件事:准入状态(incubating 观察期)、来源角色(official / media …)与节点用途。
角色由 source_naming.source_role 推导、用途看 is_template,只有准入状态需要落成独立列。

upgrade:只加列(SQLite 原生 ADD COLUMN,不重建表,表上的归档同步触发器不受影响),
并把 category='incubating' 的行(播客精选目录装入的源)映射为观察期。旧 category /
content_tags_json 列保留但不再读写,物理删除留给后续结构清理(删列要重建表、重装触发器)。

downgrade:SQLite 原生 DROP COLUMN 去掉新列(新列无索引、不被触发器引用),旧列一直都在。
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision = "c6413eda8931"
down_revision = "7ec8c19b1356"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("source_configs")}
    if "admission_status" not in columns:
        op.add_column(
            "source_configs",
            sa.Column(
                "admission_status",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default=sa.text("'admitted'"),
            ),
        )
    op.execute(
        "UPDATE source_configs SET admission_status = 'incubating' "
        "WHERE category = 'incubating' AND admission_status != 'incubating'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("source_configs")}
    if "admission_status" not in columns:
        return
    if bind.dialect.name == "sqlite":
        op.execute("ALTER TABLE source_configs DROP COLUMN admission_status")
    else:
        op.drop_column("source_configs", "admission_status")
