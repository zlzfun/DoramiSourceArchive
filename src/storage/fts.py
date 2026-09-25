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

**Unicode 归一化**：trigram 索引按原始字节匹配，排版用的 Unicode 变体
（如 U+2011 非破坏性连字符 vs U+002D 普通连字符）会导致搜索不命中。
``normalize_for_search`` 在 Python 端归一化搜索词，trigger 内用 SQLite REPLACE
链归一化索引内容，两端同时处理保证一致。短于 trigram 下限的词（如 "AI"、"as"）
由 ``build_search_components`` 分离出来交给调用方做 LIKE 回退。
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

# ── Unicode 归一化 ──────────────────────────────────────────────────────────
# Python 端：str.translate 映射表，用于搜索词归一化。
_NORMALIZE_TABLE = str.maketrans({
    0x2010: '-',   # HYPHEN
    0x2011: '-',   # NON-BREAKING HYPHEN (实际案例：AI‑Native 中的 ‑)
    0x2012: '-',   # FIGURE DASH
    0x2013: '-',   # EN DASH
    0x2014: '-',   # EM DASH
    0x2015: '-',   # HORIZONTAL BAR
    0x2212: '-',   # MINUS SIGN
    0xFF0D: '-',   # FULLWIDTH HYPHEN-MINUS
    0xFE63: '-',   # SMALL HYPHEN-MINUS
    0x2018: "'",   # LEFT SINGLE QUOTATION MARK
    0x2019: "'",   # RIGHT SINGLE QUOTATION MARK
    0x201C: '"',   # LEFT DOUBLE QUOTATION MARK
    0x201D: '"',   # RIGHT DOUBLE QUOTATION MARK
    0xFF1A: ':',   # FULLWIDTH COLON
    0xFF0C: ',',   # FULLWIDTH COMMA
    0xFF1B: ';',   # FULLWIDTH SEMICOLON
    0x3000: ' ',   # IDEOGRAPHIC SPACE
})

# SQLite 端：trigger 内用 REPLACE() 链实现相同的归一化（UTF-8 十六进制 -> 替换字符）。
_SQL_NORMALIZE_PAIRS: tuple[tuple[str, str], ...] = (
    ("E28090", "-"),   # U+2010 HYPHEN
    ("E28091", "-"),   # U+2011 NON-BREAKING HYPHEN
    ("E28092", "-"),   # U+2012 FIGURE DASH
    ("E28093", "-"),   # U+2013 EN DASH
    ("E28094", "-"),   # U+2014 EM DASH
    ("E28095", "-"),   # U+2015 HORIZONTAL BAR
    ("E28892", "-"),   # U+2212 MINUS SIGN
    ("EFBC8D", "-"),   # U+FF0D FULLWIDTH HYPHEN-MINUS
    ("E28098", "'"),   # U+2018 LEFT SINGLE QUOTATION MARK
    ("E28099", "'"),   # U+2019 RIGHT SINGLE QUOTATION MARK
    ("E2809C", '"'),   # U+201C LEFT DOUBLE QUOTATION MARK
    ("E2809D", '"'),   # U+201D RIGHT DOUBLE QUOTATION MARK
)


def normalize_for_search(text_value: str) -> str:
    """Python 端 Unicode 归一化：把排版用的 Unicode 变体映射到 ASCII 等价字符。

    用于搜索词预处理，与 trigger 端的 REPLACE 链保持同一映射，保证搜索词的
    trigram 与索引内容的 trigram 一致。
    """
    if not text_value:
        return text_value
    return text_value.translate(_NORMALIZE_TABLE)


def _sql_normalize_expr(col: str) -> str:
    """为 SQLite 列表达式 *col* 包裹嵌套 REPLACE() 调用，实现 Unicode 归一化。"""
    expr = col
    for hex_bytes, repl in _SQL_NORMALIZE_PAIRS:
        safe_repl = repl.replace("'", "''")
        expr = f"REPLACE({expr}, X'{hex_bytes}', '{safe_repl}')"
    return expr


# ── FTS DDL ─────────────────────────────────────────────────────────────────

_CREATE_TABLE = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    f"title, content, content='{_SOURCE_TABLE}', content_rowid='rowid', "
    f"tokenize='trigram')"
)


def _build_trigger_ddl() -> tuple[str, ...]:
    """构建包含 Unicode 归一化的 FTS 同步 trigger。

    insert 直插归一化后的 title/content；delete/update 需先发 'delete' 特殊指令
    告知 FTS 撤旧行（external content 不留正文副本，删除须带旧值的归一化形式），
    update = delete 旧 + insert 新两条。
    """
    nt_new = _sql_normalize_expr("new.title")
    nc_new = _sql_normalize_expr("new.content")
    nt_old = _sql_normalize_expr("old.title")
    nc_old = _sql_normalize_expr("old.content")
    return (
        f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, {nt_new}, {nc_new});
END""",
        f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, {nt_old}, {nc_old});
END""",
        f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, {nt_old}, {nc_old});
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, {nt_new}, {nc_new});
END""",
    )


_TRIGGER_DDL = _build_trigger_ddl()

_DROP_STMTS = (
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ai",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ad",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_au",
    f"DROP TABLE IF EXISTS {FTS_TABLE}",  # 虚拟表 DROP 会连带清掉 shadow 表
)


# ── 内部工具 ────────────────────────────────────────────────────────────────

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


def _populate_fts_normalized(conn: Connection) -> None:
    """批量灌入存量文章（带 Unicode 归一化）。

    不使用 FTS5 的 'rebuild' 指令——rebuild 直读 content 表原始文本，跳过
    trigger 中的 REPLACE 归一化，索引仍含排版用 Unicode 变体。改用 SELECT
    手动灌入，与 trigger 保持同一归一化路径。
    """
    norm_title = _sql_normalize_expr("title")
    norm_content = _sql_normalize_expr("content")
    conn.exec_driver_sql(
        f"INSERT INTO {FTS_TABLE}(rowid, title, content) "
        f"SELECT rowid, {norm_title}, {norm_content} FROM {_SOURCE_TABLE}"
    )


def _install_fts(conn: Connection) -> None:
    """在一个已开事务的 Connection 上幂等安装 FTS 表 + triggers；首次创建时回填存量。"""
    existed = _table_exists(conn)
    conn.exec_driver_sql(_CREATE_TABLE)
    for ddl in _TRIGGER_DDL:
        conn.exec_driver_sql(ddl)
    if not existed:
        _populate_fts_normalized(conn)


# ── 公共 API ───────────────────────────────────────────────────────────────

def ensure_fts(bind) -> bool:
    """幂等创建 FTS 虚拟表 + 同步 triggers（首次创建时回填存量）。

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


def rebuild_fts_normalized(bind) -> bool:
    """删除并重建 FTS 索引（含 Unicode 归一化 trigger 和索引内容）。

    现有索引可能用旧 trigger（无归一化）建立，排版用 Unicode 变体仍在索引中。
    本函数 drop -> ensure 一步完成，新索引与新 trigger 均带归一化。
    """
    drop_fts(bind)
    return ensure_fts(bind)


def fts_available(bind) -> bool:
    """探测 FTS 虚拟表是否已建（表不存在 / 探测异常均视为不可用）。"""
    try:
        with _as_connection(bind) as conn:
            return _table_exists(conn)
    except Exception:  # noqa: BLE001
        return False


def build_match_query(search: str) -> Optional[str]:
    """把用户输入安全包装成 FTS5 短语（phrase）查询。

    先做 Unicode 归一化，再按空白切词，每词包成双引号短语（内部双引号翻倍转义）
    以规避 FTS5 运算符（AND/OR/NOT/*/(/) 等）被误解释；短于 trigram 下限的词
    丢弃（trigram 无法匹配 < 3 字的短语，留着会拖垮整条 AND）。多词以 AND 连接。
    无可用词时返回 None。
    """
    if not search:
        return None
    normalized = normalize_for_search(search)
    tokens = [t for t in normalized.split() if len(t) >= MIN_TRIGRAM_CHARS]
    if not tokens:
        return None
    phrases = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " AND ".join(phrases)


def build_search_components(search: str) -> tuple[Optional[str], list[str]]:
    """把用户输入拆分成 FTS5 可处理的长词和需要 LIKE 回退的短词。

    返回 ``(fts_match, short_words)``：

    - *fts_match*：>= 3 字符的词组成的 FTS5 MATCH 表达式，或 ``None``（无达标词）；
    - *short_words*：< 3 字符的词列表（如 ``['AI', 'as']``），调用方应为这些词
      追加 ``title LIKE '%word%'`` 条件。

    两者均经过 Unicode 归一化。
    """
    if not search:
        return None, []
    normalized = normalize_for_search(search.strip())
    all_tokens = normalized.split()
    long_tokens = [t for t in all_tokens if len(t) >= MIN_TRIGRAM_CHARS]
    short_tokens = [t for t in all_tokens if 0 < len(t) < MIN_TRIGRAM_CHARS]
    fts_match = None
    if long_tokens:
        phrases = ['"' + t.replace('"', '""') + '"' for t in long_tokens]
        fts_match = " AND ".join(phrases)
    return fts_match, short_tokens


def fts_search_ids(bind, search: Optional[str]) -> Optional[list]:
    """FTS 检索标题 + 正文，返回命中的 `articles.rowid` 列表。

    返回值语义：
    - `list`（含空 `[]`）：FTS 可用，列表即命中的 rowid（空 = 零命中，仍属 FTS 语义）；
    - `None`：不可用（表不存在 / 输入短于 3 字 / 切词后无达标词 / 执行异常）——
      调用方据此回退到标题 LIKE。
    """
    ranked = fts_search_ranked(bind, search)
    if ranked is None:
        return None
    return list(ranked.keys())


def fts_search_ranked(bind, search: Optional[str]) -> Optional[dict]:
    """FTS 检索标题 + 正文，返回 ``{rowid: rank}``（bm25，**越小越相关**）。

    可用性语义与 :func:`fts_search_ids` 完全一致（``None`` = 不可用回退 LIKE，
    空 dict = FTS 可用但零命中）。rank 供检索管线做相关性排序——此前召回只按
    发布日期倒序截断，bm25 分数被整个丢弃（v3.34 检索质量修复）。
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
                text(f"SELECT rowid, rank FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q"),
                {"q": match},
            ).all()
        return {r[0]: float(r[1]) for r in rows}
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
