"""Alembic 迁移运行环境。

数据层的单一事实源是 `SQLModel.metadata`（`src/models/db.py` 里的 ORM 表），
迁移只在此之上做增量演进（阶段 2「数据层固化」）。运行期建库仍走
`DatabaseStorage`，Alembic 负责：① 生成/回放版本化迁移；② 为已有库
`stamp` 基线。数据库 URL 复用 `settings.storage.database_url`（可被
`DORAMI_CONFIG_FILE` / 环境变量覆盖），保证 CLI 与应用指向同一库。

SQLite 无原生 ALTER 支持，故 `render_as_batch=True` 让所有变更走 batch
（建临时表→拷贝→替换）模式。
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic 能 import 到 src/ 下的模型与配置（与 tests 的 sys.path 自举一致）。
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from config import settings  # noqa: E402
from models.db import SQLModel  # noqa: E402  —— 导入即注册所有表到 metadata
from storage.fts import fts_include_object  # noqa: E402  —— 排除 FTS 虚拟/shadow 表

config = context.config

# 未显式给定 URL 时（CLI 直接调用）回落到运行期真实库，保证 CLI 与应用同库；
# 编程式 make_alembic_config(db_url=...) 已注入 URL 的场景则尊重其取值（测试/部署指定库）。
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.storage.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(url or ""),
        compare_type=True,
        include_object=fts_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(str(connectable.url)),
            compare_type=True,
            include_object=fts_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
