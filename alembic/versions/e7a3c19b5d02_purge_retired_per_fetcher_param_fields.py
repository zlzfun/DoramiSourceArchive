"""清洗采集任务参数里的已退场字段(参数固化波)。

参数退场/固化两波后,节点参数以 schema 为契约(BaseFetcher.fetch 白名单过滤兜底);
本迁移把历史迁移(8f6d 从旧节点组搬运)与旧编辑器写入的已退场字段从
collection_jobs.per_fetcher_params_json 中剔除,保持库面干净——
否则残留会随编辑器保存永续携带(spread 回写)。

模板节点(generic_*)的参数面即 schema 全集,不在清洗范围(防御:按节点 id 前缀跳过)。

Revision ID: e7a3c19b5d02
Revises: d41acead77b0
Create Date: 2026-07-11
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7a3c19b5d02'
down_revision: Union[str, Sequence[str], None] = 'd41acead77b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 已退场的用户参数字段(参数退场波 + 参数固化波 + 早期化石字段)
RETIRED_FIELDS = {
    "fetch_detail", "detail_max_chars", "detail_min_chars", "fetch_detail_if_missing",
    "include_forks", "include_archived", "fetch_readme", "readme_max_chars",
    "include_prereleases", "min_points", "min_comments",
    "article_lookback_days", "sitemap_scan_limit",
}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, per_fetcher_params_json FROM collection_jobs")).fetchall()
    for job_id, raw in rows:
        try:
            per_params = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(per_params, dict):
            continue
        changed = False
        for fetcher_id, params in per_params.items():
            if str(fetcher_id).startswith("generic_") or not isinstance(params, dict):
                continue
            for field in RETIRED_FIELDS & set(params):
                params.pop(field)
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE collection_jobs SET per_fetcher_params_json = :pp WHERE id = :id"),
                {"id": job_id, "pp": json.dumps(per_params, ensure_ascii=False)},
            )


def downgrade() -> None:
    # 数据清洗不可逆(被剔字段的旧值不保留);无 schema 变更。
    pass
