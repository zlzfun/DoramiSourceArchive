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
if not os.environ.get("DORAMI_CONFIG_FILE", "").strip():
    _sandbox_dir = tempfile.mkdtemp(prefix="dorami-test-sandbox-")
    _sandbox_ini = os.path.join(_sandbox_dir, "test.ini")
    with open(_sandbox_ini, "w", encoding="utf-8") as _f:
        _f.write(
            "[storage]\n"
            f"database_url = sqlite:///{_sandbox_dir}/settings_sandbox.db\n"
        )
    os.environ["DORAMI_CONFIG_FILE"] = _sandbox_ini


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
