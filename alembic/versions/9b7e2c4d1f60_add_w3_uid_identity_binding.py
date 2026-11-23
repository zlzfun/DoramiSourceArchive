"""add users.w3_uid unique identity binding (letter-prefixed W3 ID e.g. l00976966, distinct from w3_employee_number)

Revision ID: 9b7e2c4d1f60
Revises: 3cfd474ffd83
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9b7e2c4d1f60"
down_revision: Union[str, Sequence[str], None] = "3cfd474ffd83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_users_w3_uid"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols_by_name = {column["name"]: column for column in inspector.get_columns("users")}
    if "w3_uid" not in cols_by_name:
        op.add_column("users", sa.Column("w3_uid", sa.String(), nullable=True))
    else:
        if cols_by_name["w3_uid"].get("nullable", True):
            bind.execute(
                sa.text("UPDATE users SET w3_uid = NULL WHERE w3_uid IS NOT NULL AND TRIM(w3_uid) = ''")
            )
        duplicates = bind.execute(
            sa.text(
                "SELECT w3_uid, COUNT(*) AS cnt FROM users "
                "WHERE w3_uid IS NOT NULL AND w3_uid != '' "
                "GROUP BY w3_uid HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if duplicates:
            values = ", ".join(f"{row[0]!r} (x{row[1]})" for row in duplicates)
            raise RuntimeError(
                f"Cannot create unique index {INDEX_NAME}: duplicate users.w3_uid values found: {values}"
            )
    indexes = {index.get("name") for index in sa.inspect(bind).get_indexes("users")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "users", ["w3_uid"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index.get("name") for index in sa.inspect(bind).get_indexes("users")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="users")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "w3_uid" in columns:
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_column("w3_uid")
