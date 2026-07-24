"""共享测试夹具与帮手。

各测试文件历来自举 `sys.path` 到 `src/`；conftest 由 pytest 在收集期最先导入，
这里同样兜底插入，保证 `seed_default_accounts` 内的 `services` 导入可解析。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
