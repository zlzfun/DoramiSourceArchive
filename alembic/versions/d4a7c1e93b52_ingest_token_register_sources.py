"""ingest tokens: permission to register sources, with a per-day count (issue #344, second PR)

``ingest_tokens`` 加三列:

- ``can_register_sources``:这个令牌可以自己登记来源(``POST /api/public/ingest/v1/sources``)。存量令牌
  一律是关的,要管理员在运维「自定源与收录」里逐个打开。
- ``register_day`` / ``register_count``:当天(UTC)已登记的个数,用来卡每日上限;记在令牌行上而不是数
  绑定行,源被删掉或解绑后计数不回退。

``ingest_tokens`` 只在收录入口所在的节点用,不随 Archive Sync 导出,与混版本窗口无关。downgrade 删列。

Revision ID: d4a7c1e93b52
Revises: c7e2a94b5d18
Create Date: 2026-11-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "d4a7c1e93b52"
down_revision: Union[str, Sequence[str], None] = "c7e2a94b5d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    ("can_register_sources", lambda: sa.Column(
        "can_register_sources", sa.Boolean(), nullable=False, server_default=sa.text("0"))),
    ("register_day", lambda: sa.Column(
        "register_day", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="")),
    ("register_count", lambda: sa.Column(
        "register_count", sa.Integer(), nullable=False, server_default="0")),
)


def upgrade() -> None:
    # 收养路径(有表、没有版本表的库先盖基线章再升级)上这些列可能已经由 create_all 建好:只补缺的。
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ingest_tokens")}
    missing = [build for name, build in _COLUMNS if name not in existing]
    if not missing:
        return
    with op.batch_alter_table("ingest_tokens", schema=None) as batch_op:
        for build in missing:
            batch_op.add_column(build())


def downgrade() -> None:
    with op.batch_alter_table("ingest_tokens", schema=None) as batch_op:
        for name, _build in reversed(_COLUMNS):
            batch_op.drop_column(name)
