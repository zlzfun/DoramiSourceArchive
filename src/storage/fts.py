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

**Unicode 归一化（issue #94）**：trigram 按原始字符匹配，排版用的 Unicode 变体
（如 U+2011 非破坏性连字符 vs 键盘输入的 U+002D）会导致搜不中。两端用**同一张
映射表** `_NORMALIZE_MAP` 归一化：查询词走 `normalize_for_search`（Python
`str.translate`），索引内容走 trigger 内的 `REPLACE(..., char(cp), ...)` 链。
因此索引内容 ≠ `articles` 原文——**不得再对本表发 FTS5 `'rebuild'` /
`'integrity-check'`**（二者直读原文，会写回/比对未归一化文本）；存量回填统一走
`_populate_fts_normalized`，整表重建走 `rebuild_fts_normalized`。

**短词**：短于 trigram 下限的词（如 `AI`）无法进 MATCH；`build_search_components`
把它们单独拆出，交由调用方追加标题 LIKE，避免「AI Agent」里的 `AI` 被静默丢弃。
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

# ── Unicode 归一化：查询端与索引端共用的唯一映射表 ─────────────────────────────
# 码点 → ASCII 等价字符。Python 端据此生成 str.translate 表，SQL 端据此生成
# trigger 内的 REPLACE(..., char(cp), ...) 链——两端同源，不会漂移。
# 只放「同义排版变体」：替换前后语义不变、且替换不改变字符数（trigram 依赖长度）。
_NORMALIZE_MAP: dict[int, str] = {
    0x2010: "-",   # HYPHEN
    0x2011: "-",   # NON-BREAKING HYPHEN（实际案例：AI‑Native）
    0x2012: "-",   # FIGURE DASH
    0x2013: "-",   # EN DASH
    0x2014: "-",   # EM DASH
    0x2015: "-",   # HORIZONTAL BAR
    0x2212: "-",   # MINUS SIGN
    0xFE63: "-",   # SMALL HYPHEN-MINUS
    0xFF0D: "-",   # FULLWIDTH HYPHEN-MINUS
    0x2018: "'",   # LEFT SINGLE QUOTATION MARK
    0x2019: "'",   # RIGHT SINGLE QUOTATION MARK
    0x201C: '"',   # LEFT DOUBLE QUOTATION MARK
    0x201D: '"',   # RIGHT DOUBLE QUOTATION MARK
    0xFF0C: ",",   # FULLWIDTH COMMA
    0xFF1A: ":",   # FULLWIDTH COLON
    0xFF1B: ";",   # FULLWIDTH SEMICOLON
    0x3000: " ",   # IDEOGRAPHIC SPACE
}
_NORMALIZE_TABLE = str.maketrans(_NORMALIZE_MAP)


def normalize_for_search(text_value: Optional[str]) -> Optional[str]:
    """把排版用 Unicode 变体映射为 ASCII 等价字符（与 trigger 端同一映射）。"""
    if not text_value:
        return text_value
    return text_value.translate(_NORMALIZE_TABLE)


def _sql_normalize_expr(col: str) -> str:
    """把 SQL 列表达式包进嵌套 REPLACE，实现与 `normalize_for_search` 相同的归一化。

    用 SQLite 内建 `char(codepoint)` 表示被替换字符，DDL 保持纯 ASCII。
    NULL 经 REPLACE 仍为 NULL，与归一化前语义一致。
    """
    expr = col
    for codepoint, repl in _NORMALIZE_MAP.items():
        expr = f"REPLACE({expr}, char({codepoint}), '{repl.replace(chr(39), chr(39) * 2)}')"
    return expr


_CREATE_TABLE = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    f"title, content, content='{_SOURCE_TABLE}', content_rowid='rowid', "
    f"tokenize='trigram')"
)

_NEW_TITLE = _sql_normalize_expr("new.title")
_NEW_CONTENT = _sql_normalize_expr("new.content")
_OLD_TITLE = _sql_normalize_expr("old.title")
_OLD_CONTENT = _sql_normalize_expr("old.content")

# external-content 标准同步 trigger 模板：insert 直插；delete/update 需先发
# 'delete' 特殊指令告知 FTS 撤旧行（external content 不留正文副本，删除须带
# **与入索引时相同的**旧值——故 delete 侧同样归一化），update = delete 旧 + insert 新。
_TRIGGER_DDL = (
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, {_NEW_TITLE}, {_NEW_CONTENT});
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, {_OLD_TITLE}, {_OLD_CONTENT});
END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON {_SOURCE_TABLE} BEGIN
  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, title, content) VALUES('delete', old.rowid, {_OLD_TITLE}, {_OLD_CONTENT});
  INSERT INTO {FTS_TABLE}(rowid, title, content) VALUES (new.rowid, {_NEW_TITLE}, {_NEW_CONTENT});
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


def _populate_fts_normalized(conn: Connection) -> None:
    """把存量文章按归一化形式灌入索引。

    不用 FTS5 的 'rebuild' 指令：它直读 `articles` 原文、绕过 trigger 的 REPLACE
    归一化，灌进去的索引与之后 trigger 写入/撤销的形式不一致（'delete' 撤不干净）。
    """
    conn.exec_driver_sql(
        f"INSERT INTO {FTS_TABLE}(rowid, title, content) "
        f"SELECT rowid, {_sql_normalize_expr('title')}, {_sql_normalize_expr('content')} "
        f"FROM {_SOURCE_TABLE}"
    )


def _install_fts(conn: Connection) -> None:
    """在一个已开事务的 Connection 上幂等安装 FTS 表 + triggers；首次创建时回填存量。

    trigger 用 IF NOT EXISTS：已存在的旧 trigger 不在这里替换——旧索引内容与旧
    trigger 是配套的，只换 trigger 会让 'delete' 撤不掉旧形式的行。升级旧索引走
    `rebuild_fts_normalized`（整表 drop → 重建 → 回填）。
    """
    existed = _table_exists(conn)
    conn.exec_driver_sql(_CREATE_TABLE)
    for ddl in _TRIGGER_DDL:
        conn.exec_driver_sql(ddl)
    if not existed:
        _populate_fts_normalized(conn)


def ensure_fts(bind) -> bool:
    """幂等创建 FTS 虚拟表 + 同步 triggers（首次创建时归一化回填存量）。

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
    """整表重建 FTS：drop 旧表与旧 trigger → 按当前（归一化）DDL 重建并回填。

    供迁移把「未归一化 trigger + 原文索引」的存量库一次性升级；返回值同 `ensure_fts`。
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


def build_search_components(search: Optional[str]) -> tuple[Optional[str], list[str]]:
    """把用户输入拆成 ``(fts_match, short_words)``，两者均已 Unicode 归一化。

    - *fts_match*：≥ 3 字符的词各包成双引号短语（内部双引号翻倍转义，规避 FTS5
      运算符 AND/OR/NOT/*/( 被误解释），以 AND 连接；无达标词时为 ``None``；
    - *short_words*：< 3 字符的词（如 ``["AI"]``）——trigram 无法匹配，调用方应
      追加标题 LIKE 约束，而不是静默丢弃。
    """
    if not search:
        return None, []
    tokens = normalize_for_search(search).split()
    long_tokens = [t for t in tokens if len(t) >= MIN_TRIGRAM_CHARS]
    short_tokens = [t for t in tokens if len(t) < MIN_TRIGRAM_CHARS]
    if not long_tokens:
        return None, short_tokens
    phrases = ['"' + t.replace('"', '""') + '"' for t in long_tokens]
    return " AND ".join(phrases), short_tokens


def build_match_query(search: Optional[str]) -> Optional[str]:
    """把用户输入安全包装成 FTS5 短语（phrase）查询（先做 Unicode 归一化）。

    短于 trigram 下限的词丢弃（留着会拖垮整条 AND；需要它们的调用方用
    `build_search_components` 取回短词自行 LIKE）。无可用词时返回 None。
    """
    return build_search_components(search)[0]


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
