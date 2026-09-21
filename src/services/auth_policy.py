"""登录方式策略:密码登录是否可用——下游外部身份源的唯一覆盖点(issue #130)。

公网主线只有密码登录,``password_login_enabled`` 恒为 True,main 的行为逐字不变。
下游接入外部身份源(内网 SSO / OIDC)时**只改这一个函数**,按账号判定(例如由外部身份源
供给的账号返回 False、本地管理员仍可密码登录),不要再把信号当 prop 穿过共享组件签名
(``App.jsx`` / ``SettingsModal.jsx``),那是 main 每次改签名都撞冲突的根源。

接线(main 已铺好,下游零改动即生效):

- 后端:``POST /api/auth/login`` 对返回 False 的账号拒绝密码登录(403);
  ``POST /api/auth/change-password`` 同样 403。
- 透出:``runtime_capabilities()`` 的 ``password_login_enabled``(按当前会话账号判定)与
  ``GET /api/auth/session``(匿名时取全局姿态,供登录页读取)。
- 前端:设置柜「账户」区据能力位隐藏改密表单(``settings/AccountSection.jsx``);登录页照旧。

``record`` 为 None 表示尚无会话(登录页 / 匿名 session 探测)时的全局姿态。
"""
from __future__ import annotations

from typing import Any, Optional


def password_login_enabled(record: Optional[Any] = None) -> bool:
    """该账号(None = 全局姿态)是否可用密码登录 / 修改密码。main 上恒 True。"""
    return True
