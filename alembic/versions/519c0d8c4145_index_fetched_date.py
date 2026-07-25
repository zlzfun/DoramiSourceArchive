"""index fetched_date

Revision ID: 519c0d8c4145
Revises: 6cb03105eec2
Create Date: 2026-07-25 00:54:01.504183

为 articles.fetched_date 补索引：它是读者 feed 主排序、媒体热点图逐日聚合、
未读水位判定的高频过滤/排序键，此前无索引。同时建 (source_id, fetched_date)
复合索引——单源按时间倒序取条目（阅读器源栏、feed 交付、热点图逐源统计）走它
即可命中，免二次排序。

注：autogenerate 曾额外报出若干列的 NOT NULL 差异——那是**运行库既存漂移**
（旧手写 ALTER 路径加列时未落 NOT NULL 约束），与本次索引改动无关，故此处**不
纳入**，仅保留两条 create_index，避免把无关的 schema 变更夹带进本迁移。
create_index 对已含同名索引的库幂等失败，故走存在性判断（batch 前用 inspect）。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '519c0d8c4145'
down_revision: Union[str, Sequence[str], None] = '6cb03105eec2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 articles 补 fetched_date 单列索引与 (source_id, fetched_date) 复合索引。"""
    existing = {ix["name"] for ix in inspect(op.get_bind()).get_indexes("articles")}
    with op.batch_alter_table("articles", schema=None) as batch_op:
        if "ix_articles_fetched_date" not in existing:
            batch_op.create_index(
                batch_op.f("ix_articles_fetched_date"), ["fetched_date"], unique=False
            )
        if "ix_articles_source_id_fetched_date" not in existing:
            batch_op.create_index(
                "ix_articles_source_id_fetched_date",
                ["source_id", "fetched_date"],
                unique=False,
            )


def downgrade() -> None:
    """回退两条索引。"""
    existing = {ix["name"] for ix in inspect(op.get_bind()).get_indexes("articles")}
    with op.batch_alter_table("articles", schema=None) as batch_op:
        if "ix_articles_source_id_fetched_date" in existing:
            batch_op.drop_index("ix_articles_source_id_fetched_date")
        if "ix_articles_fetched_date" in existing:
            batch_op.drop_index(batch_op.f("ix_articles_fetched_date"))
