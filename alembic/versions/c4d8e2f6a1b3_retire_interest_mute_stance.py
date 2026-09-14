"""retire interest mute stance (issue #27, v3.55)

兴趣只剩「关注」一极:删除存量屏蔽行,并把 user_interest_tags.stance 的 CHECK 收窄为 follow。
列本身保留(与 priority 同为旧库兼容字段),模型侧默认值恒 follow。

被删的屏蔽行会改变该读者的兴趣版本哈希(personal_digest._interest_version),今日早报
据此如实标出 interest_stale——这是「兴趣确实变了」的诚实结果,不作特殊处理。

Revision ID: c4d8e2f6a1b3
Revises: 8be5beaf1307
Create Date: 2026-09-14 12:00:00

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "c4d8e2f6a1b3"
down_revision: Union[str, Sequence[str], None] = "8be5beaf1307"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK = "ck_user_interest_tags_stance"


def _replace_stance_constraint(sqltext: str) -> None:
    bind = op.get_bind()
    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("user_interest_tags", recreate=recreate) as batch:
        batch.drop_constraint(_CK, type_="check")
        batch.create_check_constraint(_CK, sqltext)


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("user_interest_tags"):
        return  # 收养回放守卫:更早的基线库尚无此表时由后续 create 承担
    op.execute("DELETE FROM user_interest_tags WHERE stance <> 'follow'")
    _replace_stance_constraint("stance = 'follow'")


def downgrade() -> None:
    # 屏蔽行已物理删除,不可恢复;只把约束放回两极。
    _replace_stance_constraint("stance IN ('follow','mute')")
