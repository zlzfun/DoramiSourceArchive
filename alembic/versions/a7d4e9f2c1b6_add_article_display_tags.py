"""add article display tags and candidate deletion audit action

Revision ID: a7d4e9f2c1b6
Revises: f6a2c8d91e4b
Create Date: 2026-09-02 19:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy import inspect


revision: str = "a7d4e9f2c1b6"
down_revision: Union[str, Sequence[str], None] = "f6a2c8d91e4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    analysis_columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("article_analyses")
    }
    if "display_tags_json" not in analysis_columns:
        with op.batch_alter_table("article_analyses", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "display_tags_json",
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default="[]",
                )
            )

    with op.batch_alter_table("cms_tag_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_cms_tag_events_action", type_="check")
        batch_op.create_check_constraint(
            "ck_cms_tag_events_action",
            "action IN ('activate','rename','merge','deprecate','reject','change_flags','delete_candidate')",
        )


def downgrade() -> None:
    with op.batch_alter_table("cms_tag_events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_cms_tag_events_action", type_="check")
        batch_op.create_check_constraint(
            "ck_cms_tag_events_action",
            "action IN ('activate','rename','merge','deprecate','reject','change_flags')",
        )
    with op.batch_alter_table("article_analyses", schema=None) as batch_op:
        batch_op.drop_column("display_tags_json")
