"""Alembic 迁移的编程式入口（供部署/运维与测试调用）。

阶段 2「数据层固化」引入 Alembic 作为**版本化**迁移机制，取代此前散落在
`DatabaseStorage._ensure_compatible_schema` 里的手写 `ALTER TABLE`（无版本、无回滚）。

运行期建表仍走 `SQLModel.metadata.create_all()`（对全新库/内存库是最快的引导，
且与 metadata 天然一致）；Alembic 负责两件运行期之外的事：

1. **为已有库采纳基线**：老部署的库已有全部表但无 `alembic_version`，直接
   `upgrade head` 会重跑基线建表而失败——故先 `stamp` 基线，再 `upgrade`。
   `ensure_migrated()` 封装了这套「有表无版本→stamp，然后 upgrade」逻辑。
2. **回放后续迁移**：基线之后的每次 schema 变更都是一个迁移，部署时
   `alembic upgrade head`（deploy.sh 已接入）把已有库演进到最新。

`create_all`(=metadata) 与 `upgrade head` 的一致性由 `tests/test_migrations.py`
的漂移守卫强制保证，故双通道不会漂移。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"

# 基线迁移的 revision（首个、down_revision 为空）。有表无版本的老库统一 stamp 到这里。
BASELINE_REVISION = "5ee31a7c5393"


def make_alembic_config(db_url: Optional[str] = None) -> Config:
    """构造指向本项目 alembic/ 目录的 Config；可覆盖数据库 URL。"""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _current_revision(db_url: str) -> Optional[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _has_user_tables(db_url: str) -> bool:
    """库里是否已有业务表（用 articles 作代表，它是核心且最早出现的表）。"""
    engine = create_engine(db_url)
    try:
        return "articles" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def ensure_migrated(db_url: str) -> None:
    """把库演进到最新迁移；对「有表无版本」的老库先采纳基线再升级。

    内存库无迁移意义（每次进程新建），直接跳过。
    """
    if ":memory:" in db_url:
        return
    cfg = make_alembic_config(db_url)
    if _current_revision(db_url) is None and _has_user_tables(db_url):
        # 老库：已有全部表但从未纳入 Alembic —— 打上基线戳，避免重跑建表。
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")
