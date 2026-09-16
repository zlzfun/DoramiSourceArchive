"""Track durable OSS media locations separately from portable content metadata.

Revision ID: c92a8f01d6b3
Revises: b715a91c4e02
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "c92a8f01d6b3"
down_revision = "b715a91c4e02"
branch_labels = None
depends_on = None


def upgrade():
    if "object_blobs" in sa.inspect(op.get_bind()).get_table_names():
        return
    strings = ("id", "namespace", "content_hash", "ext", "mime", "bucket", "region", "object_key", "created_at")
    op.create_table(
        "object_blobs",
        *(sa.Column(name, sqlmodel.sql.sqltypes.AutoString(), nullable=False) for name in strings),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_object_blobs_namespace", "object_blobs", ["namespace"])


def downgrade():
    bind = op.get_bind()
    if "object_blobs" not in sa.inspect(bind).get_table_names():
        return
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("LOCK TABLE object_blobs IN ACCESS EXCLUSIVE MODE")
    elif bind.dialect.name == "sqlite":
        bind.exec_driver_sql("UPDATE object_blobs SET id=id WHERE 0")
    if bind.execute(sa.text("SELECT 1 FROM object_blobs LIMIT 1")).first() is not None:
        raise RuntimeError(
            "restore and verify all media, then finalize-local before downgrading; "
            "or restore the pre-upgrade database and all media"
        )
    op.drop_index("ix_object_blobs_namespace", table_name="object_blobs")
    op.drop_table("object_blobs")
