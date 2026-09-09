"""podcast: persist the initial-assessment basis and actual input

Revision ID: e9c4b7a1d2f6
Revises: c7e1a9d4b2f6
Create Date: 2026-09-09
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e9c4b7a1d2f6"
down_revision: Union[str, Sequence[str], None] = "c7e1a9d4b2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("article_analyses")}


def _indexes() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("article_analyses")}


def upgrade() -> None:
    columns = _columns()
    additions = (
        ("analysis_basis", sa.String(), sa.text("''")),
        ("analysis_input_hash", sa.String(), sa.text("''")),
        ("transcript_artifact_id", sa.String(), None),
        ("analysis_diagnostics_json", sa.String(), sa.text("'{}'")),
    )
    for name, type_, default in additions:
        if name in columns:
            continue
        with op.batch_alter_table("article_analyses") as batch:
            batch.add_column(
                sa.Column(name, type_, nullable=default is None, server_default=default)
            )
    if "ix_article_analyses_analysis_input_hash" not in _indexes():
        op.create_index(
            "ix_article_analyses_analysis_input_hash",
            "article_analyses",
            ["analysis_input_hash"],
            unique=False,
        )
    # Old rows predate the analysis column. The retired premium workflow wrote
    # an extensions marker in the same transaction as its ASR-based score, so
    # preserve that known provenance; every other historical podcast row was
    # produced by the article worker from show notes.
    bind = op.get_bind()
    legacy_rows = bind.execute(
        sa.text(
            """
            SELECT aa.article_id, a.content_type, a.extensions_json
            FROM article_analyses AS aa
            JOIN articles AS a ON a.id = aa.article_id
            WHERE aa.analysis_basis = ''
            """
        )
    ).all()
    for article_id, content_type, extensions_json in legacy_rows:
        basis = "article_body"
        if content_type == "podcast_episode":
            try:
                extensions = json.loads(extensions_json or "{}")
            except (TypeError, ValueError):
                extensions = {}
            legacy_basis = (
                str(extensions.get("analysis_basis") or "").strip()
                if isinstance(extensions, dict)
                else ""
            )
            basis = (
                "asr_transcript"
                if legacy_basis in {"transcript", "asr_transcript"}
                else "publisher_transcript"
                if legacy_basis == "publisher_transcript"
                else "podcast_show_notes"
            )
        bind.execute(
            sa.text(
                "UPDATE article_analyses SET analysis_basis = :basis "
                "WHERE article_id = :article_id"
            ),
            {"basis": basis, "article_id": article_id},
        )


def downgrade() -> None:
    if "ix_article_analyses_analysis_input_hash" in _indexes():
        op.drop_index(
            "ix_article_analyses_analysis_input_hash", table_name="article_analyses"
        )
    columns = _columns()
    for name in (
        "analysis_diagnostics_json",
        "transcript_artifact_id",
        "analysis_input_hash",
        "analysis_basis",
    ):
        if name in columns:
            with op.batch_alter_table("article_analyses") as batch:
                batch.drop_column(name)
