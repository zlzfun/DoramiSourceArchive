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
