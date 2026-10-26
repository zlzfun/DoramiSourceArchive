"""event layer tables, retire duplicate_groups (issue #121)

事件层模型层:新建 events / event_members / event_redirects / event_assignment_ledger /
event_rank_snapshots 五表,并删除退役的 duplicate_groups / duplicate_group_members
(两表自 v3.3x 建表起全库零写入方,无数据可搬;早报去重改读 event_members)。

- 老库经 create_all 启动再被 Alembic 收养时五表已存在:整组已齐则跳过建表;只齐一部分视为损坏
  (fail-closed,与 a3f64d8c2e11 同姿态)。
- 两张退役表只在存在时删除:新版 create_all 的库天然没有它们。
- downgrade 按 a3f64d8c2e11 + f3a8c1d9e2b4 的最终形状重建两张空表,并删掉五表。

Revision ID: ed530f8224f9
Revises: 660028d4ab6f
Create Date: 2026-10-23 18:57:39.227005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel autogen 会产出 sqlmodel.sql.sqltypes.AutoString 等类型


# revision identifiers, used by Alembic.
revision: str = 'ed530f8224f9'
down_revision: Union[str, Sequence[str], None] = '660028d4ab6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVENT_TABLES = frozenset({
    "events",
    "event_members",
    "event_redirects",
    "event_assignment_ledger",
    "event_rank_snapshots",
})
RETIRED_TABLES = ("duplicate_group_members", "duplicate_groups")  # 先子后父


def _create_event_tables() -> None:
    op.create_table('events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('title_source', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('representative_article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('member_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('publisher_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('first_seen_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('last_member_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('story_id', sa.Integer(), nullable=True),
    sa.Column('follows_event_id', sa.Integer(), nullable=True),
    sa.Column('assignment_version', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('authority_id', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.CheckConstraint("status IN ('open','closed','merged')", name='ck_events_status'),
    sa.ForeignKeyConstraint(['follows_event_id'], ['events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['representative_article_id'], ['articles.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['story_id'], ['events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_authority_id'), ['authority_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_first_seen_at'), ['first_seen_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_last_member_at'), ['last_member_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_representative_article_id'), ['representative_article_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_status'), ['status'], unique=False)

    op.create_table('event_members',
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('publisher_key', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('report_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('first_seen_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('assigned_by', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'llm'"), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('prompt_version', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('is_representative', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.CheckConstraint("assigned_by IN ('llm','url','title','singleton','manual','sync')", name='ck_event_members_assigned_by'),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)', name='ck_event_members_confidence'),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('event_id', 'article_id'),
    sa.UniqueConstraint('article_id', name='uq_event_members_article')
    )
    with op.batch_alter_table('event_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_members_publisher_key'), ['publisher_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_event_members_report_at'), ['report_at'], unique=False)

    op.create_table('event_redirects',
    sa.Column('old_event_id', sa.Integer(), nullable=False),
    sa.Column('new_event_id', sa.Integer(), nullable=False),
    sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['new_event_id'], ['events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['old_event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('old_event_id')
    )
    with op.batch_alter_table('event_redirects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_redirects_new_event_id'), ['new_event_id'], unique=False)

    op.create_table('event_assignment_ledger',
    sa.Column('article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_attempt_at', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("''"), nullable=False),
    sa.Column('blocked_event_ids_json', sqlmodel.sql.sqltypes.AutoString(), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.CheckConstraint("status IN ('pending','assigned','skipped','blocked')", name='ck_event_assignment_ledger_status'),
    sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('article_id')
    )
    with op.batch_alter_table('event_assignment_ledger', schema=None) as batch_op:
        batch_op.create_index('ix_event_assignment_ledger_status_next_attempt', ['status', 'next_attempt_at'], unique=False)

    op.create_table('event_rank_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('snapshot_date', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('publisher_count', sa.Integer(), nullable=False),
    sa.Column('member_count', sa.Integer(), nullable=False),
    sa.Column('heat', sa.Float(), server_default=sa.text('0'), nullable=False),
    sa.Column('generated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('snapshot_date', 'event_id', name='uq_event_rank_snapshots_date_event')
    )
    with op.batch_alter_table('event_rank_snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_event_rank_snapshots_date_rank', ['snapshot_date', 'rank'], unique=False)


def upgrade() -> None:
    """建事件层五表;删退役的 duplicate_groups 两表(只在存在时)。"""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    overlap = EVENT_TABLES & existing
    if overlap and overlap != EVENT_TABLES:
        raise RuntimeError(f"partial event-layer schema exists: {sorted(overlap)}")
    if not overlap:
        _create_event_tables()
    # 两表恒空(全库零写入方),直接 DROP;SQLite 随表删除其索引
    # (ix_duplicate_groups_representative_article_id 由 f3a8c1d9e2b4 建)。
    for table in RETIRED_TABLES:
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    """删事件层五表;按 a3f64d8c2e11 + f3a8c1d9e2b4 的最终形状重建两张空的退役表。"""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    # 先删引用 events 的子表,events 最后
    for table in (
        "event_redirects",
        "event_rank_snapshots",
        "event_members",
        "event_assignment_ledger",
        "events",
    ):
        if table in existing:
            op.drop_table(table)

    if "duplicate_groups" not in existing:
        op.create_table('duplicate_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fingerprint', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('strategy', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('representative_article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['representative_article_id'], ['articles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fingerprint', name='uq_duplicate_groups_fingerprint')
        )
        with op.batch_alter_table('duplicate_groups', schema=None) as batch_op:
            batch_op.create_index('ix_duplicate_groups_representative_article_id', ['representative_article_id'], unique=False)
    if "duplicate_group_members" not in existing:
        op.create_table('duplicate_group_members',
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('article_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('similarity', sa.Float(), nullable=True),
        sa.Column('is_representative', sa.Boolean(), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.CheckConstraint('similarity IS NULL OR (similarity >= 0.0 AND similarity <= 1.0)', name='ck_duplicate_group_members_similarity'),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['duplicate_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('group_id', 'article_id'),
        sa.UniqueConstraint('article_id', name='uq_duplicate_group_members_article')
        )
