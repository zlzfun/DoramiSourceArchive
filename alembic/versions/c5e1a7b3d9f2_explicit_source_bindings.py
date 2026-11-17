"""write implicit source bindings explicitly (issue #262 R1b)

今天空 ``fetcher_id`` 并不表示「没有采集绑定」:``resolve_source_fetcher_id`` 按
``source_type`` 推导模板(rss/atom → generic_rss 等)。源模型重构要让「空 = 无绑定」成立
(Agent 推送的临时源、手工导入源没有定时抓取通道),所以先把推导结果写实。

- 只改本机自有的行:带 ``collection_authority_id`` 的同步行按设计没有绑定(接收端会清空
  ``fetcher_id``),跳过。
- 映射是 ``collection_nodes.resolve_source_fetcher_id`` 在本迁移时刻的冻结副本。
- 写实的值与推导结果相同,旧代码照常工作;downgrade 不回退数据。

Revision ID: c5e1a7b3d9f2
Revises: 8166665976c4
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "c5e1a7b3d9f2"
down_revision: Union[str, Sequence[str], None] = "8166665976c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BINDING_BY_TYPE = (
    (("podcast", "podcast_rss"), "generic_podcast_rss"),
    (("rss", "atom"), "generic_rss"),
    (("web", "webpage"), "generic_web"),
    (("x", "twitter", "x_timeline"), "generic_x_timeline"),
)


def upgrade() -> None:
    for source_types, fetcher_id in _BINDING_BY_TYPE:
        quoted = ", ".join(f"'{value}'" for value in source_types)
        op.execute(
            "UPDATE source_configs SET fetcher_id = "
            f"'{fetcher_id}' "
            "WHERE COALESCE(fetcher_id, '') = '' "
            "AND COALESCE(collection_authority_id, '') = '' "
            f"AND lower(trim(COALESCE(source_type, ''))) IN ({quoted})"
        )


def downgrade() -> None:
    # 写实的值等于旧代码的推导结果,保留不影响旧版本;无需回退。
    pass
