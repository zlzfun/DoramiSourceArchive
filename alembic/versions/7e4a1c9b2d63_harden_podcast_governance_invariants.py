"""harden Podcast governance and text publication invariants

Revision ID: 7e4a1c9b2d63
Revises: 6c1f8a2d4e90
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "7e4a1c9b2d63"
down_revision: Union[str, Sequence[str], None] = "6c1f8a2d4e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_PUBLICATION_FK = "fk_podcast_text_publications_artifact_slot"
_NEW_PUBLICATION_FK = "fk_podcast_text_publications_artifact_authority_slot"
_AUTHORITY_SLOT_INDEX = "uq_podcast_text_artifacts_authority_slot"


def _foreign_key_names() -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_foreign_keys(
            "podcast_text_publications"
        )
        if item.get("name")
    }


def _index_names() -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes("podcast_text_artifacts")
        if item.get("name")
    }


def _assert_existing_authorities_match() -> None:
    mismatch = op.get_bind().execute(sa.text(
        "SELECT p.identity FROM podcast_text_publications p "
        "JOIN podcast_text_artifacts a ON a.id = p.artifact_id "
        "WHERE p.authority_id IS NOT a.authority_id LIMIT 1"
    )).scalar_one_or_none()
    if mismatch is not None:
        raise RuntimeError(
            "拒绝升级 Podcast 文本发布约束：发布槽与产物 authority 不一致："
            f"{mismatch}"
        )


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in ("podcast_text_publications", "podcast_text_artifacts"):
        if bind.execute(sa.text(
            f"SELECT 1 FROM {table} "
            "WHERE authority_id IS NOT NULL AND authority_id <> '' LIMIT 1"
        )).first() is not None:
            raise RuntimeError(
                "拒绝降级 Podcast 文本完整性约束：数据库仍含远端 authority。"
                "请先停止同步 worker，并恢复升级前备份。"
            )


def upgrade() -> None:
    _assert_existing_authorities_match()
    drop_archive_sync_revision_triggers(op.get_bind())

    indexes = _index_names()
    if _AUTHORITY_SLOT_INDEX not in indexes:
        op.create_index(
            _AUTHORITY_SLOT_INDEX,
            "podcast_text_artifacts",
            ["id", "episode_id", "kind", "authority_id"],
            unique=True,
        )

    foreign_keys = _foreign_key_names()
    if _NEW_PUBLICATION_FK not in foreign_keys:
        with op.batch_alter_table(
            "podcast_text_publications", recreate="always"
        ) as batch_op:
            if _OLD_PUBLICATION_FK in foreign_keys:
                batch_op.drop_constraint(_OLD_PUBLICATION_FK, type_="foreignkey")
            batch_op.create_foreign_key(
                _NEW_PUBLICATION_FK,
                "podcast_text_artifacts",
                ["artifact_id", "episode_id", "kind", "authority_id"],
                ["id", "episode_id", "kind", "authority_id"],
                ondelete="CASCADE",
            )

    install_archive_sync_revision_triggers(op.get_bind())


def downgrade() -> None:
    _assert_downgrade_safe()
    drop_archive_sync_revision_triggers(op.get_bind())

    foreign_keys = _foreign_key_names()
    if _OLD_PUBLICATION_FK not in foreign_keys:
        with op.batch_alter_table(
            "podcast_text_publications", recreate="always"
        ) as batch_op:
            if _NEW_PUBLICATION_FK in foreign_keys:
                batch_op.drop_constraint(_NEW_PUBLICATION_FK, type_="foreignkey")
            batch_op.create_foreign_key(
                _OLD_PUBLICATION_FK,
                "podcast_text_artifacts",
                ["artifact_id", "episode_id", "kind"],
                ["id", "episode_id", "kind"],
                ondelete="CASCADE",
            )

    if _AUTHORITY_SLOT_INDEX in _index_names():
        op.drop_index(_AUTHORITY_SLOT_INDEX, table_name="podcast_text_artifacts")

    install_archive_sync_revision_triggers(
        op.get_bind(), include_podcast_integrity=False
    )
