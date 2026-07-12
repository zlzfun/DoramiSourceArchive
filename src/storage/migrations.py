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

import tempfile
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

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


def _sqlite_default_literal(col_type: str, dflt_value: Optional[str]) -> str:
    """NOT NULL 补列的 DEFAULT 字面量：优先样本库声明值，否则按类型兜底。"""
    if dflt_value is not None:
        return str(dflt_value)
    upper = (col_type or "").upper()
    if any(tok in upper for tok in ("INT", "BOOL", "REAL", "FLOA", "NUM")):
        return "0"
    return "''"


def _align_legacy_to_baseline(db_url: str) -> None:
    """收养前把断代老库对齐到基线 schema（缺表建表、缺列补列）。

    老库来自旧手写 `ALTER TABLE ADD COLUMN` 路径的**任意历史断面**，可能比基线
    metadata 更老（实例：生产库 users 缺 ai_beta_enabled，索引重建迁移
    ccae184ca0a1 对不存在的列建索引而崩）。`stamp 基线` 的前提是「库 ⊇ 基线
    schema」——本函数用一个临时库回放基线迁移得到基线 schema 的活样本，据此
    只补「基线有而老库缺」的表与列：

    - 对齐目标是**基线**而非 head：基线之后的列/表由后续迁移自己创建，在这里
      预补会让那些迁移撞「duplicate column」；
    - 缺表照抄样本库 sqlite_master 的 CREATE TABLE / CREATE INDEX 原文；
    - 缺列 ADD COLUMN 照抄样本 pragma 的类型/NOT NULL，NOT NULL 给 DEFAULT
      字面量（样本声明值优先，否则按类型兜底 0/''）；
    - 老库里多出的表列（更古的遗产，如 node_groups）原样保留，交给后续迁移处置。

    仅对 SQLite 生效（本项目生产即 SQLite）；其它方言直接返回。
    """
    if not db_url.startswith("sqlite"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        sample_url = f"sqlite:///{Path(tmp) / 'baseline_sample.db'}"
        command.upgrade(make_alembic_config(sample_url), BASELINE_REVISION)
        sample = create_engine(sample_url)
        target = create_engine(db_url)
        try:
            sample_insp = inspect(sample)
            target_insp = inspect(target)
            target_tables = set(target_insp.get_table_names())
            with sample.connect() as sconn, target.begin() as tconn:
                for table in sample_insp.get_table_names():
                    if table == "alembic_version":
                        continue
                    if table not in target_tables:
                        # 缺表：照抄样本 DDL（表 + 显式索引）
                        rows = sconn.execute(text(
                            "SELECT sql FROM sqlite_master WHERE tbl_name = :t "
                            "AND sql IS NOT NULL ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
                        ), {"t": table}).all()
                        for (ddl,) in rows:
                            tconn.execute(text(ddl))
                        continue
                    # 缺列：ADD COLUMN 补齐
                    existing = {c["name"] for c in target_insp.get_columns(table)}
                    pragma = sconn.execute(text(f'PRAGMA table_info("{table}")')).all()
                    for _cid, name, col_type, notnull, dflt_value, _pk in pragma:
                        if name in existing:
                            continue
                        ddl = f'ALTER TABLE "{table}" ADD COLUMN "{name}" {col_type or ""}'.rstrip()
                        if notnull:
                            ddl += f" NOT NULL DEFAULT {_sqlite_default_literal(col_type, dflt_value)}"
                        tconn.execute(text(ddl))
        finally:
            sample.dispose()
            target.dispose()


def ensure_migrated(db_url: str) -> None:
    """把库演进到最新迁移；对「有表无版本」的老库先对齐基线、采纳基线、再升级。

    内存库无迁移意义（每次进程新建），直接跳过。
    """
    if ":memory:" in db_url:
        return
    cfg = make_alembic_config(db_url)
    current = _current_revision(db_url)
    if _has_user_tables(db_url) and current in (None, BASELINE_REVISION):
        # 老库(无版本)或停在基线的库(如上次收养后升级中途失败):断面可能早于
        # 基线(缺表缺列),先对齐到基线 schema 再继续——对已对齐库是幂等零操作。
        # revision 已越过基线的库禁止再对齐:后续迁移可能已删除基线列
        # (如 d41acead77b0 删 per_fetcher_cron_json),对齐会把它们错误加回。
        _align_legacy_to_baseline(db_url)
        if current is None:
            command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")
