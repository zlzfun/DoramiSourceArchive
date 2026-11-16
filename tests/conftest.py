"""共享测试夹具与帮手。

各测试文件历来自举 `sys.path` 到 `src/`；conftest 由 pytest 在收集期最先导入，
这里同样兜底插入，保证 `seed_default_accounts` 内的 `services` 导入可解析。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── 会话级数据库沙箱:任何测试(含疏漏)都不得触碰真实开发库 ──
# api.app 在 import 期就按 settings.storage.database_url 建立 db_sink;个别测试若忘记
# monkeypatch db_sink,写操作会直接落到 data/cms_data.db——2026-08-12 实录:test_mcp
# 三处播种把 dev 库的 admin 密码/AI 开关/头像整行重置,表象是「重启后 AI 已开启状态
# 丢失」,真凶是跑测试。此处在任何 src 导入之前把 DORAMI_CONFIG_FILE 指向临时 ini,
# 使整个测试会话的「settings 库」本身就是一次性沙箱;显式自建 tmp sink 的测试不受影响。
# (若外部已显式设置 DORAMI_CONFIG_FILE,尊重之——CI 可能有意注入专用配置。)
#
# pytest-xdist(issue #150):worker 是主进程的子进程,原样继承主进程在这里铸出的沙箱路径。
# 若照旧「已设置就尊重」,4 个 worker 就共用同一个 SQLite,import api.app 期的 create_all
# 与播种互撞(2026-09-23 实录:test_podcast_landing.py 收集期 `database is locked`)。
# 于是用 DORAMI_TEST_SANDBOX 标记「这个值是 conftest 铸的」:worker 看到标记就另铸一份
# 带 worker id 的沙箱;外部显式注入的值(无标记)在 worker 里仍被尊重,是否共用由注入方负责。
_SANDBOX_MARK = "DORAMI_TEST_SANDBOX"


def _mint_sandbox(label: str) -> str:
    sandbox_dir = tempfile.mkdtemp(prefix=f"dorami-test-sandbox-{label}-")
    ini = os.path.join(sandbox_dir, "test.ini")
    with open(ini, "w", encoding="utf-8") as f:
        f.write(
            "[storage]\n"
            f"database_url = sqlite:///{sandbox_dir}/settings_sandbox.db\n"
        )
    return ini


def ensure_settings_sandbox(environ=os.environ) -> str | None:
    """按上面的规则决定要不要铸沙箱;铸了返回 ini 路径,沿用既有值返回 None。

    拆成函数(environ 可注入)是为了让 tests/test_ci_workflow.py 直接验证四种组合,
    而不必真起 xdist worker。
    """
    worker = environ.get("PYTEST_XDIST_WORKER", "").strip()
    explicit = environ.get("DORAMI_CONFIG_FILE", "").strip()
    minted_upstream = environ.get(_SANDBOX_MARK) == "1"
    if explicit and not (worker and minted_upstream):
        return None
    ini = _mint_sandbox(worker or "main")
    environ["DORAMI_CONFIG_FILE"] = ini
    environ[_SANDBOX_MARK] = "1"
    return ini


ensure_settings_sandbox()


def seed_default_accounts(engine, accounts=(("admin", "admin", "admin"), ("user", "user", "user"))):
    """把测试账户播种进给定引擎的 users 表（取代旧的 seed_users_if_empty + _auth_config 两件套）。

    直调 create_user（v3.19 放开后可直建 admin），accounts 每项为
    (username, password, role)。默认播种 admin/admin(admin) 与 user/user(user)。
    """
    from sqlmodel import Session
    from services import accounts as accounts_service

    with Session(engine) as session:
        for username, password, role in accounts:
            accounts_service.create_user(session, username, password, role)


def add_on_current_schema(session, *records):
    """在停在旧迁移版本的库里插入今天的 ORM 对象:只写库里已有的列。

    迁移测试常先 ``upgrade`` 到某个旧版本再造数据;之后的迁移给同一张表加的列(如 issue #262 给
    ``articles`` 加的来路列)在那个版本还不存在,直接 ``session.add`` 会撞上 ``no such column``。
    按当前库表结构裁掉多出来的列,用 Core insert 写入(不触发 ORM 事件;数据库触发器照常)。
    """
    from sqlalchemy import column as sa_column, insert, inspect as sa_inspect, table as sa_table

    bind = session.get_bind()
    for record in records:
        table = record.__table__
        existing = {column["name"] for column in sa_inspect(bind).get_columns(table.name)}
        values = {
            column.name: getattr(record, column.key, None)
            for column in table.columns
            if column.name in existing
            # 与 ORM 同口径:值为 None 且列有 server_default 时交给数据库默认值。
            and not (getattr(record, column.key, None) is None and column.server_default is not None)
        }
        # 轻量 table 对象:不带模型列的 Python 侧默认值,Core 不会替缺席的列补值。
        light = sa_table(table.name, *(sa_column(name) for name in values))
        session.execute(insert(light).values(**values))


# Ordinary API tests may configure fake LLM credentials and assert exact request
# counts. Background title generation is driven explicitly in its service tests;
# do not let lifespan race the endpoint under test or send an unstubbed request.
import pytest


@pytest.fixture(autouse=True)
def idle_automatic_title_worker(monkeypatch):
    import asyncio
    from services import title_translation

    async def idle(_engine):
        await asyncio.Event().wait()

    monkeypatch.setattr(title_translation, "worker_loop", idle)


# 测试里的密码哈希(issue #289):生产是 PBKDF2 20 万轮(单次约 25~70 ms),而 unit 套件一轮要哈希 / 校验约 2400 次,
# 占 worker 时间约 7%。测试只关心「能登录 / 口令对不对」,不关心轮数——整个会话把缺省轮数调到 1000;
# 需要真轮数的用例显式传 `iterations=`。夹具顺带钉住生产缺省不被调低。
TEST_PBKDF2_ITERATIONS = 1_000


@pytest.fixture(autouse=True, scope="session")
def fast_password_hashing():
    from services import accounts

    production = accounts._PBKDF2_ITERATIONS
    assert production >= 200_000, f"生产缺省轮数是 {production}:低于 20 万——快哈希只该来自这个夹具,不该改生产常量"
    accounts._PBKDF2_ITERATIONS = TEST_PBKDF2_ITERATIONS
    yield production
    accounts._PBKDF2_ITERATIONS = production


# ── 读取审计(issue #288):「CI 跳过重型后端 job」不丢覆盖 ──
# PR 只动 frontend/ docs/ docs-site/ 与仓根 *.md 时,CI 不跑 backend-unit / backend-deploy,只跑 scripts/ci_scope.py 的
# LIGHT_TESTS。这成立的前提是:除了那几条,没有别的后端测试依赖这些路径下的文件。前提靠人记会烂——新写的测试读了
# docs/ 或 frontend/ 却没登记,以后一次纯文档 / 纯前端 PR 就能带着绿灯把它改红。所以这里把前提变成被执行的:
# 进程级审计钩子记下每次落在这些路径上的 open / listdir / scandir,归到当前收集的模块 / 类或当前用例的阶段;
# 所在测试文件不在 LIGHT_TESTS 里,这一阶段就判失败(通过、跳过、xfail 都算)。新增这样的测试必然改 tests/,那次 PR 走全量,当场就红。
# 看不见的两类:子进程里的读取(用例起的脚本自己去读)——现有这类用例都在临时仓里跑,不读真实仓的这些路径;
# 只判存在不打开(Path.exists / os.stat 不产生审计事件)。两类都由 push 到 main 的全量兜底。
import importlib.util

_REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_scope_spec = importlib.util.spec_from_file_location("ci_scope", os.path.join(_REPO_ROOT, "scripts", "ci_scope.py"))
ci_scope = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(ci_scope)

_SCOPE_EVENTS = frozenset({"open", "os.listdir", "os.scandir"})
_SCOPE_DIRS = tuple(d.rstrip("/") for d in ci_scope.SKIP_SAFE_DIRS)
# 审计钩子装上就卸不掉。本文件若在同一进程里被执行不止一次(换一个模块名再导入、`-p conftest` 与 `tests.conftest` 并存),
# 各装一个钩子、各记各的列表就乱了——所以读取记录与「已安装」标记挂在进程级(sys)上:共用一个列表、钩子只装一次。
_scope_reads: list[str] = sys.__dict__.setdefault("_dorami_scope_reads", [])


def scope_guarded_path(target) -> str | None:
    """`target` 落在 CI 会据以跳过重型 job 的路径上时,返回它的仓根相对路径;否则 None。

    相对路径按当前工作目录解析(用例常 chdir);先用字符串做一次便宜的预筛,只有像是这些路径的才付 realpath 的代价——
    钩子对进程里每一次 open 都会被调用。
    """
    if target is None:
        target = os.getcwd()
    elif isinstance(target, int):
        return None  # 文件描述符:路径在打开它的那一次已经审计过
    target = os.fsdecode(os.fspath(target))
    if not os.path.isabs(target):
        target = os.path.join(os.getcwd(), target)
    if not (target.endswith(".md") or any(name in target for name in _SCOPE_DIRS)):
        return None
    rel = os.path.relpath(os.path.realpath(target), _REPO_ROOT).replace(os.sep, "/")
    return rel if rel in _SCOPE_DIRS or ci_scope.is_skip_safe(rel) else None


def _scope_audit_hook(event, args):
    if event not in _SCOPE_EVENTS:
        return
    try:
        rel = scope_guarded_path(args[0] if args else None)
    except Exception:  # noqa: BLE001 — 审计钩子里抛异常会变成被审计的那次 open 抛异常
        return
    if rel is not None:
        _scope_reads.append(rel)


def _scope_take() -> list[str]:
    reads = sorted(set(_scope_reads))
    del _scope_reads[:]
    return reads


def _scope_listed(path) -> bool:
    rel = os.path.relpath(os.path.realpath(str(path)), _REPO_ROOT).replace(os.sep, "/")
    return rel in ci_scope.LIGHT_TESTS


def _scope_message(where: str, reads: list[str]) -> str:
    return (
        f"{where} 读了 {', '.join(reads)}。\n"
        "PR 只改这些路径时 CI 会跳过 backend-unit / backend-deploy(scripts/ci_scope.py 的 SKIP_SAFE_DIRS 与仓根 *.md),"
        "只跑 LIGHT_TESTS——这条测试不在其中,届时不会运行。\n"
        "把所在测试文件登记进 scripts/ci_scope.py 的 LIGHT_TESTS(并同步 .github/workflows/ci.yml 里 backend-light 的命令),"
        "或者改成不读这些文件。"
    )


def _scope_flag(report, where: str, reads: list[str]) -> None:
    """把这一阶段判失败——除非它本来就失败(保留原来的失败原因;用例修好后这里照样会再拦)。

    通过、跳过、xfail / xpass 一视同仁:读了文件再按内容 `pytest.skip()` / `pytest.xfail()` 同样是依赖,
    以后那份文件一改,它就可能走进失败分支,而轻量 CI 不会运行它(Codex R1-2)。
    """
    if report.outcome == "failed":
        return
    report.outcome = "failed"
    report.longrepr = _scope_message(where, reads)
    if hasattr(report, "wasxfail"):
        del report.wasxfail  # 否则终端仍把它记成 xfailed / xpassed


def pytest_runtest_logstart(nodeid, location):
    del _scope_reads[:]


# tryfirst:做最外层的包装,yield 之后最后一个执行——那时内置的 skip / xfail 处理已经把报告改完了。
@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    reads = _scope_take()
    if not reads or _scope_listed(item.path):
        return
    _scope_flag(outcome.get_result(), item.nodeid, reads)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_make_collect_report(collector):
    del _scope_reads[:]
    outcome = yield
    reads = _scope_take()
    # 归到所在的测试文件:模块本身(顶层语句、模块级参数生成)与它下面的类(类里的参数生成在收集类时才跑,Codex R1-1)
    # 的 path 都是那个 .py;目录 / 会话级收集器的 path 是目录,那里的读取归不到哪个测试文件,不管。
    path = getattr(collector, "path", None)
    if not reads or path is None or not path.is_file() or _scope_listed(path):
        return
    _scope_flag(outcome.get_result(), collector.nodeid, reads)


if not getattr(sys, "_dorami_scope_audit_installed", False):
    sys.addaudithook(_scope_audit_hook)
    sys._dorami_scope_audit_installed = True


# ── 分片(issue #289):`--shard I/N` 只跑落在第 I 片的用例 ──
# CI 把 backend-unit / backend-deploy 各拆成 N 个 job(矩阵)并行,每个 job 带同一条命令、只差 I。划分必须满足:
# 每条用例恰好落在一片里——漏掉的用例哪个 job 都不跑,而 CI 照样是绿的。所以划分是纯函数(用例函数的 nodeid 做 CRC32),
# 不依赖收集顺序、不依赖别的片跑了什么;同一个测试函数的全部参数化落在同一片(参数 id 不参与哈希:个别 id 来自对象
# repr,不能指望它跨进程稳定)。xdist 下每个 worker 各自收集、各自按同一函数筛,结果一致。
# ci.yml 里矩阵取值与分母的一致性由 tests/test_ci_workflow.py 守卫。
import re
import zlib


def shard_of(nodeid: str, total: int) -> int:
    """用例属于第几片(1 起)。"""
    return zlib.crc32(nodeid.split("[", 1)[0].encode("utf-8")) % total + 1


def parse_shard(spec: str) -> tuple[int, int] | None:
    """`I/N` → (I, N);空串 = 不分片。形状不对直接报用法错误,不能默默退回全量或空集。"""
    if not spec:
        return None
    m = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec)
    if not m or int(m.group(1)) > int(m.group(2)):
        raise pytest.UsageError(f"--shard 须为 I/N 且 1 <= I <= N,实得 {spec!r}")
    return int(m.group(1)), int(m.group(2))


def pytest_addoption(parser):
    parser.addoption("--shard", default="", metavar="I/N",
                     help="只跑按用例函数哈希落在第 I 片(共 N 片,I 从 1 起)的用例;CI 用它把一个 job 拆成 N 个并行")


def pytest_configure(config):
    parse_shard(config.getoption("--shard"))  # 形状错在收集前就报


def pytest_collection_modifyitems(config, items):
    shard = parse_shard(config.getoption("--shard"))
    if shard is None:
        return
    index, total = shard
    kept = [item for item in items if shard_of(item.nodeid, total) == index]
    dropped = [item for item in items if shard_of(item.nodeid, total) != index]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
