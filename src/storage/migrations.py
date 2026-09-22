"""Alembic 迁移的编程式入口（供部署/运维与测试调用）。

阶段 2「数据层固化」引入 Alembic 作为**版本化**迁移机制，取代此前散落在
`DatabaseStorage._ensure_compatible_schema` 里的手写 `ALTER TABLE`（无版本、无回滚）。

运行期建表仍走 `SQLModel.metadata.create_all()`（对全新库/内存库是最快的引导，
且与 metadata 天然一致）；Alembic 负责两件运行期之外的事：

1. **为已有库采纳基线**：老部署的库已有全部表但无 `alembic_version`，直接
   `upgrade head` 会重跑基线建表而失败——故先 `stamp` 基线，再 `upgrade`。
   `ensure_migrated()` 封装了这套「有表无版本→stamp，然后 upgrade」逻辑。
2. **回放后续迁移**：基线之后的每次 schema 变更都是一个迁移，部署时
   `alembic upgrade head`（容器入口 docker/entrypoint.py 与 dev 裸起均已接入）把已有库演进到最新。

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
from alembic.script import ScriptDirectory
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


def _current_heads(db_url: str) -> tuple[str, ...]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_heads()
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
    current = _current_heads(db_url)
    if _has_user_tables(db_url) and (not current or current == (BASELINE_REVISION,)):
        # 老库(无版本)或停在基线的库(如上次收养后升级中途失败):断面可能早于
        # 基线(缺表缺列),先对齐到基线 schema 再继续——对已对齐库是幂等零操作。
        # revision 已越过基线的库禁止再对齐:后续迁移可能已删除基线列
        # (如 d41acead77b0 删 per_fetcher_cron_json),对齐会把它们错误加回。
        _align_legacy_to_baseline(db_url)
        if not current:
            command.stamp(cfg, BASELINE_REVISION)
    # 多头容忍(下游分叉仓形态):内网 master(曾名 intranet)类分叉仓自带迁移支线时,合入 main 的
    # 新迁移后 DAG 出现两个 head——git 零冲突,但 upgrade("head") 会无条件报错
    # "Multiple head revisions",应用在启动路径上直接起不来。"heads" 并行全升是
    # Alembic 原生语义;main 自身迁移链恒为单链(漂移守卫使然),此分支在本仓
    # 等价于 "head"、纯为下游分叉仓兜底。
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) > 1:
        print(f"⚠️ 迁移链存在 {len(heads)} 个 head(分叉仓形态),并行全升: {', '.join(heads)}")
    command.upgrade(cfg, "heads" if len(heads) > 1 else "head")


# ── 只读迁移计划(issue #102 自动部署,部署前在目标镜像里执行) ──
#
# 部署脚本要在切换之前知道「这个库对目标代码来说是领先 / 落后 / 全新 / 待收养」,
# 但 **不能** shell `alembic current`:在线 alembic 命令会加载 alembic/env.py,其 online
# 路径在 begin_transaction 内先 drop 再 reinstall Archive Sync 触发器且 BEGIN IMMEDIATE——
# 不是只读。本函数只用 MigrationContext 读 alembic_version(复数 heads)与 ScriptDirectory
# 的 revision 图做 DAG 闭包比较,SQLite 连接 PRAGMA query_only,库文件不存在时不连接(否则
# sqlite 会把它建出来)。状态语义(与 docs/auto-deploy-plan.md §4.6 一致):
#   fresh                    无库文件 / 无业务表:pending 为完整目标链;是否放行由部署侧首装门决定
#   legacy_adoption_required 有业务表无 alembic_version:ensure_migrated 会对齐基线并收养
#   compatible               DB 当前 heads 都在目标图里:pending = 目标闭包 − 已应用闭包(拓扑序;多头/merge 自然成立)
#   incompatible             DB 当前 head 不在目标脚本图里:典型是「DB 领先于目标代码」(降级撞迁移——旧 tag 的脚本
#                            目录没有新 revision 文件),也可能是目标缺支线或迁移文件损坏,不武断断言具体原因
# 注:没有单独的「已知 head 但闭包不是目标闭包子集」状态——目标图里的每个 revision 必是某个 head 的祖先
#(叶子本身就是 head),所以 heads 全部已知即蕴含子集关系,该状态不可达。

PLAN_DEPLOYABLE_STATUSES = frozenset({"fresh", "legacy_adoption_required", "compatible"})


class SqliteTarget:
    """`_sqlite_target` 的结果:是否 sqlite / 文件路径(内存库为 None)/ 是否内存库。"""

    __slots__ = ("is_sqlite", "path", "is_memory")

    def __init__(self, is_sqlite: bool, path: Optional[Path], is_memory: bool) -> None:
        self.is_sqlite = is_sqlite
        self.path = path
        self.is_memory = is_memory


def _sqlite_target(db_url: str) -> SqliteTarget:
    """用 SQLAlchemy 自己的 URL 解析判断 sqlite 目标文件,与实际连接指向同一路径。

    覆盖四种写法:普通文件 `sqlite:///rel/or/abs.db`、显式驱动 `sqlite+pysqlite:////abs.db`、
    `file:` URI(`sqlite:///file:/abs.db?mode=ro&uri=true`)、内存库(无 database / `:memory:` /
    `file::memory:` / `mode=memory`)。字符串切割会把显式驱动与 URI 判错(codex PR #111 R1 P1-2)。
    """
    from urllib.parse import parse_qs, unquote, urlsplit

    from sqlalchemy.engine import make_url

    url = make_url(db_url)
    if url.get_backend_name() != "sqlite":
        return SqliteTarget(False, None, False)
    database = url.database or ""
    if database in ("", ":memory:"):
        return SqliteTarget(True, None, True)
    uri_flag = str(url.query.get("uri", "")).strip().lower() in {"1", "true", "yes", "on"}
    if uri_flag:
        # make_url 会把 `?mode=memory&uri=true` 整段挪到 url.query,database 里只剩 `file:...`——内存判定要
        # 同时看 url.query(codex PR #111 复检 P2);但只有 uri 生效时驱动才会把 mode 交给 sqlite,
        # 没有 `uri=true` 的 `?mode=memory` 会被忽略、仍连磁盘文件(复检 2 新增 P2),故这段必须在 uri 分支内。
        if str(url.query.get("mode", "")).strip().lower() == "memory":
            return SqliteTarget(True, None, True)
        if database.startswith("file:"):
            parts = urlsplit(database)
            params = parse_qs(parts.query)
            path = unquote(parts.path)
            if not path or path == ":memory:" or params.get("mode", [""])[0] == "memory":
                return SqliteTarget(True, None, True)
            return SqliteTarget(True, Path(path), False)
    return SqliteTarget(True, Path(database), False)


def readonly_engine(db_url: str):
    """只读引擎。

    已存在的 SQLite 文件用 URI 连接 `sqlite:///file:<path>?mode=ro&uri=true`:`mode=ro` 让底层文件
    连接本身只读——关闭最后一个连接时 sqlite 不会 checkpoint WAL、不会改写主库或删 `-wal/-shm`
    (`PRAGMA query_only` 只挡 SQL 写,挡不住这一步;codex PR #111 R1 P1-1 实测主库 sha 会变)。
    不用 `immutable=1`:它会让 sqlite 忽略 WAL 里尚未 checkpoint 的数据。每连接再加 `query_only`
    作第二道约束。内存库无可保护;非 sqlite 后端原样建引擎(本项目生产即 sqlite)。
    """
    from urllib.parse import quote

    from sqlalchemy import event
    from sqlalchemy.engine import URL

    target = _sqlite_target(db_url)
    if not target.is_sqlite or target.is_memory:
        return create_engine(db_url)
    ro_url = URL.create(
        "sqlite",
        database=f"file:{quote(str(target.path), safe='/')}",
        query={"mode": "ro", "uri": "true"},
    )
    engine = create_engine(ro_url)

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA query_only=ON")
        cursor.close()

    return engine


def _revision_closure(script: ScriptDirectory, heads) -> set:
    """给定若干 revision,返回它们及全部祖先的 revision id 集合。"""
    closure: set = set()
    for head in heads:
        for rev in script.walk_revisions(base="base", head=head):
            closure.add(rev.revision)
    return closure


def _ordered(script: ScriptDirectory, wanted: set) -> list:
    """按脚本图拓扑序(base → heads)排列 wanted 中的 revision。"""
    descending = list(script.walk_revisions(base="base", head="heads"))
    return [rev.revision for rev in reversed(descending) if rev.revision in wanted]


def plan_migrations(db_url: str, *, script_location: Optional[str] = None) -> dict:
    """只读地算出「目标代码 vs 当前库」的迁移计划,返回可 JSON 化的字典。

    script_location 仅供测试注入另一份脚本目录(模拟目标 tag 缺 / 多支线)。
    """
    from alembic.script.revision import ResolutionError

    cfg = make_alembic_config(db_url)
    if script_location:
        cfg.set_main_option("script_location", script_location)
    script = ScriptDirectory.from_config(cfg)
    target_heads = sorted(script.get_heads())
    required = _revision_closure(script, target_heads)
    plan = {
        "status": "",
        "detail": "",
        "current_heads": [],
        "target_heads": target_heads,
        "pending": [],
        "pending_count": 0,
        "extra": [],
        "database_exists": True,
    }

    def finish(status: str, detail: str, *, pending=None, extra=None) -> dict:
        plan["status"] = status
        plan["detail"] = detail
        plan["pending"] = list(pending or [])
        plan["pending_count"] = len(plan["pending"])
        plan["extra"] = sorted(extra or [])
        return plan

    target = _sqlite_target(db_url)
    if target.is_sqlite and (target.is_memory or not target.path.exists()):
        plan["database_exists"] = False
        return finish("fresh", "数据库不存在:目标链将从头建立(是否放行由部署侧首装门决定)",
                      pending=_ordered(script, required))

    engine = readonly_engine(db_url)
    try:
        with engine.connect() as conn:
            current_heads = sorted(MigrationContext.configure(conn).get_current_heads())
            has_tables = "articles" in inspect(conn).get_table_names()
    finally:
        engine.dispose()
    plan["current_heads"] = current_heads

    if not current_heads:
        if has_tables:
            baseline_closure = _revision_closure(script, [BASELINE_REVISION])
            return finish(
                "legacy_adoption_required",
                "有业务表但无 alembic_version:启动时 ensure_migrated 会对齐基线并收养后升级",
                pending=_ordered(script, required - baseline_closure),
            )
        return finish("fresh", "库文件存在但无业务表:目标链将从头建立(是否放行由部署侧首装门决定)",
                      pending=_ordered(script, required))

    unknown = []
    for head in current_heads:
        try:
            script.revision_map.get_revision(head)
        except ResolutionError:
            unknown.append(head)
    if unknown:
        return finish(
            "incompatible",
            f"数据库当前 revision 不在目标代码的迁移图里: {unknown}——可能是 DB 领先于目标代码、"
            "目标缺少支线或迁移文件损坏;按 docs/release-process.md 恢复对应备份后重跑",
            extra=unknown,
        )
    applied = _revision_closure(script, current_heads)
    pending = _ordered(script, required - applied)
    return finish(
        "compatible",
        "已在目标 revision 集合" if not pending else f"待执行 {len(pending)} 个迁移",
        pending=pending,
    )

def main_chain_heads(script) -> list:
    """main 自己那条链的 head 列表(issue #130):去掉「声明了 branch_labels 的 revision 及其全部后代」
    (下游分叉仓的支线,如内网 SSO 迁移)之后,剩余子图的叶子。

    注意不能用 ``Script.branch_labels`` 判定——alembic 会把标签同时传给后代**与祖先**(直到分叉点),
    下游直接延伸 main 末端时整条主链都会被打上标签;这里读迁移文件里声明的 ``branch_labels``。
    正常的 main 恒返回恰好一个 head;两个即 main 自己分叉(下游未加 label 的支线也按分叉计,有意如此)。
    """
    revisions = {rev.revision: rev for rev in script.walk_revisions()}

    def parents(rev):
        down = rev.down_revision
        if not down:
            return ()
        return (down,) if isinstance(down, str) else tuple(down)

    children: dict = {rid: [] for rid in revisions}
    for rid, rev in revisions.items():
        for parent in parents(rev):
            children.setdefault(parent, []).append(rid)
    downstream = set()
    stack = [rid for rid, rev in revisions.items() if getattr(rev.module, "branch_labels", None)]
    while stack:
        rid = stack.pop()
        if rid in downstream:
            continue
        downstream.add(rid)
        stack.extend(children.get(rid, ()))
    main = set(revisions) - downstream
    return sorted(rid for rid in main if not any(child in main for child in children.get(rid, ())))
