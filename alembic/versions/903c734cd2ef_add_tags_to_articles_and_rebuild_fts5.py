"""add tags to articles and rebuild fts5

Revision ID: 903c734cd2ef
Revises: 344ba9944fe9
Create Date: 2026-09-27 16:59:01.435234

本迁移为 articles 表添加 tags 列以聚合规范与动态标签，
并重建 FTS5 虚拟表 articles_fts(title, tags, content) 及同步 triggers，
使搜索支持标题 (100)、标签 (10)、正文 (1) 多列加权检索与短词匹配。
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '903c734cd2ef'
down_revision: Union[str, Sequence[str], None] = '344ba9944fe9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "articles" not in tables:
        return

    columns = {c["name"] for c in sa.inspect(bind).get_columns("articles")}
    if "tags" not in columns:
        op.add_column(
            "articles",
            sa.Column("tags", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        )

    # 回填存量文章的 tags 字段（规范标签 + 提取展示标签）
    if "article_tag_assignments" in tables:
        canonical_rows = bind.exec_driver_sql(
            "SELECT ata.article_id, ct.name_zh, ct.name_en "
            "FROM article_tag_assignments ata "
            "JOIN cms_tags ct ON ata.tag_id = ct.id"
        ).all()
        tags_by_article: dict[str, list[str]] = {}
        for aid, n_zh, n_en in canonical_rows:
            tag_list = tags_by_article.setdefault(aid, [])
            if n_zh and n_zh not in tag_list:
                tag_list.append(n_zh)
            if n_en and n_en not in tag_list:
                tag_list.append(n_en)

        if "article_analyses" in tables:
            analysis_rows = bind.exec_driver_sql(
                "SELECT article_id, display_tags_json FROM article_analyses "
                "WHERE display_tags_json IS NOT NULL AND display_tags_json != '[]' AND display_tags_json != ''"
            ).all()
            for aid, dt_json in analysis_rows:
                try:
                    parsed = json.loads(dt_json)
                    if isinstance(parsed, list):
                        tag_list = tags_by_article.setdefault(aid, [])
                        for item in parsed:
                            if isinstance(item, dict) and item.get("label"):
                                lbl = str(item["label"]).strip()
                                if lbl and lbl not in tag_list:
                                    tag_list.append(lbl)
                except Exception:
                    pass

        for aid, t_list in tags_by_article.items():
            if t_list:
                tag_str = " ".join(t_list)
                bind.execute(
                    text("UPDATE articles SET tags = :t WHERE id = :aid"),
                    {"t": tag_str, "aid": aid},
                )

    if bind.dialect.name == "sqlite":
        from storage.fts import rebuild_fts_normalized
        rebuild_fts_normalized(bind)


def downgrade() -> None:
    # Like the parent revision, reject before any DDL above the one-way boundary.
    raise RuntimeError(
        "FTS tags extension sits above a one-way boundary; restore the pre-upgrade database to downgrade"
    )
