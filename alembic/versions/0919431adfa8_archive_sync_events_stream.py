"""archive sync events stream (issue #121)

事件层 Archive Sync `events` 流:把 `archive_sync_entity_states.stream` 的 CHECK 放宽到含
'events',并重装全套同步触发器——事件 / 成员 / 重定向三表的变更归到所属事件、事件物理删除落
tombstone、接收端 authority 事件只接受同一 authority 的文章做成员。触发器本体在
`storage.archive_sync_revision`;装载条件是事件三表齐全且 CHECK 已含 'events'(按 DDL 文本判断),
所以 ed530f8224f9 建表到本迁移放宽 CHECK 之间不会装上会撞 CHECK 的触发器。

Revision ID: 0919431adfa8
Revises: ed530f8224f9
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from storage.archive_sync_revision import (
    drop_archive_sync_revision_triggers,
    install_archive_sync_revision_triggers,
)


revision: str = "0919431adfa8"
down_revision: Union[str, Sequence[str], None] = "ed530f8224f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_STREAM_CHECK = (
    "stream IN ('sources','articles','analyses','media','source_states',"
    "'podcast_texts','podcast_audio')"
)
_NEW_STREAM_CHECK = (
    "stream IN ('sources','articles','analyses','media','source_states',"
    "'podcast_texts','podcast_audio','events')"
)


def _replace_stream_check(expression: str) -> None:
    bind = op.get_bind()
    drop_archive_sync_revision_triggers(bind)
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "archive_sync_entity_states", recreate=recreate
    ) as batch:
        batch.drop_constraint(
            "ck_archive_sync_entity_states_stream", type_="check"
        )
        batch.create_check_constraint(
            "ck_archive_sync_entity_states_stream", expression
        )
    # 9c5e2a7d1b40 之后 podcast_artifacts 不再重建,音频触发器在这里可以装。
    install_archive_sync_revision_triggers(bind, include_podcast_audio=True)


def upgrade() -> None:
    _replace_stream_check(_NEW_STREAM_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "DELETE FROM archive_sync_entity_states WHERE stream = 'events'"
    )
    _replace_stream_check(_OLD_STREAM_CHECK)
