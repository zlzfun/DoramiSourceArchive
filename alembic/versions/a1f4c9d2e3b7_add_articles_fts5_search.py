"""add articles FTS5 full-text search (title + content)

Revision ID: a1f4c9d2e3b7
Revises: 519c0d8c4145
Create Date: 2026-07-25 01:30:00.000000

文章搜索此前只对标题 LIKE（搜不到正文、前置通配全表扫描）。本迁移建立
FTS5 external-content 虚拟表 `articles_fts`（trigram tokenizer，天然子串匹配、
中英文皆宜）+ 三个同步 trigger，承接标题 + 正文全文检索。

DDL 是运行期与迁移的共享单一实现：`storage.fts.ensure_fts` 同时被
`DatabaseStorage.__init__`（create_all 后）与本迁移调用，保证两条建库通道
拿到同一 FTS 结构（漂移守卫要求 create_all == upgrade head）。FTS 虚拟/shadow 表
（`articles_fts*`）不在 SQLModel metadata 里，`alembic/env.py` 与漂移测试经
`fts_include_object` 排除该前缀，故不触发漂移。

手写迁移（非 autogenerate）：ensure_fts 幂等（IF NOT EXISTS + 首建 rebuild
回填存量），老 SQLite 无 trigram 时内部吞异常降级，迁移照常通过。
downgrade 删表 + triggers（drop_fts）。
"""
from typing import Sequence, Union

from alembic import op

from storage.fts import drop_fts, ensure_fts

# revision identifiers, used by Alembic.
revision: str = 'a1f4c9d2e3b7'
down_revision: Union[str, Sequence[str], None] = '519c0d8c4145'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建 FTS5 虚拟表 + 同步 triggers，并回填存量文章（仅 SQLite）。"""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    ensure_fts(bind)


def downgrade() -> None:
    """删除 FTS5 虚拟表与其 triggers。"""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    drop_fts(bind)
