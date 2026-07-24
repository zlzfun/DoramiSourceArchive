"""SQLite FTS5 全文搜索（标题 + 正文）的建表 DDL 与查询 helper。

文章搜索此前只对标题做 LIKE（前置通配 → 全表扫描、且搜不到正文）。本模块用
**FTS5 external-content 虚拟表** `articles_fts`（`content='articles'`，行随
`articles.rowid` 对齐）+ **trigram tokenizer**（SQLite ≥ 3.34；天然子串匹配、
中英文皆宜，替代 LIKE 语义最平滑）承接标题 + 正文全文检索，三个同步 trigger
（insert/delete/update）保证与 `articles` 表实时一致。

**建表 DDL 是运行期与迁移的共享单一实现**：`DatabaseStorage.__init__` 在
`create_all()` 后调用 `ensure_fts`（仅 SQLite），新 Alembic 迁移也调用同一
`ensure_fts`——两条建库通道（create_all / upgrade head）拿到同一 FTS 结构。
老 SQLite 无 fts5/trigram 时 `ensure_fts` 吞异常返回 False，搜索优雅降级回
标题 LIKE，**绝不影响启动**。

**drift 守卫兼容**：FTS 虚拟表及其 shadow 表（`articles_fts_data/_idx/_docsize/
_config`）与 triggers 不在 `SQLModel.metadata` 里，autogenerate 会误报为漂移。
`fts_include_object` 供 `alembic/env.py` 与漂移测试排除以 `articles_fts` 开头的
对象——只排该前缀，真实模型漂移照常捕获。

**查询降级契约**：`fts_search_ids(session_or_conn, search)` 返回命中 rowid 列表
（`[]` = FTS 可用但零命中，仍走 FTS 语义），或 `None` = 不可用/输入过短/异常，
调用方据此回退 LIKE。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

# FTS 虚拟表名（其 shadow 表以此为前缀：articles_fts_data/_idx/_docsize/_config）。
FTS_TABLE = "articles_fts"
_SOURCE_TABLE = "articles"

# trigram tokenizer 的硬下限：短于 3 个字符的短语无法匹配（实测返回空而非报错），
# 故整串短于此长度、或切词后无一词达标时直接判不可用、回退 LIKE。
MIN_TRIGRAM_CHARS = 3

_CREATE_TABLE = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    f"title, content, content='{_SOURCE_TABLE}', content_rowid='rowid', "
    f"tokenize='trigram')"
)

# external-content 标准同步 trigger 模板：insert 直插；delete/update 需先发
# 'delete' 特殊指令告知 FTS 撤旧行（external content 不留正文副本，删除须带旧值），
# update = delete 旧 + insert 新两条。
_TRIGGER_DDL = (
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, old.title, old.content);
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, old.title, old.content);
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END""",
)

_DROP_STMTS = (
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ai",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ad",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_au",
    f"DROP TABLE IF EXISTS {FTS_TABLE}",  # 虚拟表 DROP 会连带清掉 shadow 表
)


@contextmanager
def _as_connection(bind):
    """把 Engine / Connection / Session 归一成可执行的 Connection。

    Engine 时开一个短连接（用完关闭）；Connection 直接透传；其余按 Session 处理，
    取其绑定的 Connection（属会话事务，不在此关闭）。
    """
    if isinstance(bind, Engine):
        with bind.connect() as conn:
            yield conn
    elif isinstance(bind, Connection):
        yield bind
    else:  # Session-like
        yield bind.connection()


def _table_exists(conn: Connection) -> bool:
    return conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": FTS_TABLE},
    ).first() is not None


def _install_fts(conn: Connection) -> None:
    """在一个已开事务的 Connection 上幂等安装 FTS 表 + triggers；首次创建时回填存量。"""
    existed = _table_exists(conn)
    conn.exec_driver_sql(_CREATE_TABLE)
    for ddl in _TRIGGER_DDL:
        conn.exec_driver_sql(ddl)
    if not existed:
        # 首次创建：把存量文章批量灌入索引（external content 的 'rebuild' 指令）。
        conn.exec_driver_sql(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")


def ensure_fts(bind) -> bool:
    """幂等创建 FTS 虚拟表 + 同步 triggers（首次创建时 rebuild 回填存量）。

    `bind` 可为 Engine（运行期 `DatabaseStorage`：自开事务）或 Connection
    （Alembic 迁移 `op.get_bind()`：复用其事务）。老 SQLite 无 fts5/trigram 时
    捕获异常、记 warning 并返回 False——搜索降级为标题 LIKE，启动不受影响。

    返回 True=FTS 可用，False=不可用（已降级）。
    """
    try:
        if isinstance(bind, Engine):
            with bind.begin() as conn:
                _install_fts(conn)
        else:  # Connection（如 alembic op.get_bind()），已在事务中
            _install_fts(bind)
        return True
    except Exception as exc:  # noqa: BLE001 —— 建索引失败绝不能拖垮启动/迁移
        logger.warning("FTS5 全文索引不可用，搜索降级为标题 LIKE：%s", exc)
        return False


def drop_fts(bind) -> None:
    """删除 FTS 虚拟表与其 triggers（迁移 downgrade 用）。"""
    def _run(conn: Connection) -> None:
        for stmt in _DROP_STMTS:
            conn.exec_driver_sql(stmt)

    if isinstance(bind, Engine):
        with bind.begin() as conn:
            _run(conn)
    else:
        _run(bind)


def fts_available(bind) -> bool:
    """探测 FTS 虚拟表是否已建（表不存在 / 探测异常均视为不可用）。"""
    try:
        with _as_connection(bind) as conn:
            return _table_exists(conn)
    except Exception:  # noqa: BLE001
        return False


def build_match_query(search: str) -> Optional[str]:
    """把用户输入安全包装成 FTS5 短语（phrase）查询。

    按空白切词，每词包成双引号短语（内部双引号翻倍转义）以规避 FTS5 运算符
    （AND/OR/NOT/*/(/) 等）被误解释；短于 trigram 下限的词丢弃（trigram 无法
    匹配 < 3 字的短语，留着会拖垮整条 AND）。多词以 AND 连接。无可用词时返回 None。
    """
    if not search:
        return None
    tokens = [t for t in search.split() if len(t) >= MIN_TRIGRAM_CHARS]
    if not tokens:
        return None
    phrases = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " AND ".join(phrases)


def fts_search_ids(bind, search: Optional[str]) -> Optional[list]:
    """FTS 检索标题 + 正文，返回命中的 `articles.rowid` 列表。

    返回值语义：
    - `list`（含空 `[]`）：FTS 可用，列表即命中的 rowid（空 = 零命中，仍属 FTS 语义）；
    - `None`：不可用（表不存在 / 输入短于 3 字 / 切词后无达标词 / 执行异常）——
      调用方据此回退到标题 LIKE。
    """
    if not search or len(search.strip()) < MIN_TRIGRAM_CHARS:
        return None
    match = build_match_query(search.strip())
    if match is None:
        return None
    try:
        with _as_connection(bind) as conn:
            if not _table_exists(conn):
                return None
            rows = conn.execute(
                text(f"SELECT rowid FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q"),
                {"q": match},
            ).all()
        return [r[0] for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("FTS 搜索失败，降级为标题 LIKE：%s", exc)
        return None


def fts_include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic autogenerate `include_object` 过滤：排除 FTS 虚拟表及其 shadow 表。

    `articles_fts` 及 `articles_fts_data/_idx/_docsize/_config` 不在 SQLModel
    metadata 里，不排除会被误报为「多出的表」漂移。**只排 `articles_fts` 前缀**，
    其它一切照常比较——真实模型漂移仍被漂移测试捕获。
    """
    if type_ == "table" and name and name.startswith(FTS_TABLE):
        return False
    return True
