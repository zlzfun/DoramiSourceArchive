"""启动期安全配置校验（阶段4 D10 安全硬化）。

集中检查易被漏配的安全项，并按**部署姿态**分级处置：
- 开发姿态（`[auth] cookie_secure = false`，即无 HTTPS）：仅告警，不阻断本地启动；
- 生产姿态（`cookie_secure = true`，只有走 HTTPS 才会开）：关键项升级为**错误**，拒绝启动。

检查项：
1. `[auth] secret` —— 未设或仍是占位符（change-me…）。未设时会话/令牌 HMAC 回退到
   非口令派生的本地弱密钥；生产必须显式设长随机串。
2. `[cors] allow_origins=* 且 allow_credentials=true` —— 不安全组合（凭证型跨域应用
   具体域名白名单）。
3. `[network] disable_ca_bundle=true` —— 生产环境禁用 TLS 证书校验有中间人风险。

`evaluate_security_config` 为纯函数（返回 (errors, warnings)，便于测试）；
`enforce_security_config` 记录日志，若有 error 则抛 RuntimeError 拒绝启动。
"""

from __future__ import annotations

import logging
from typing import List, Tuple

_logger = logging.getLogger("dorami.security")

# 占位符/明显未改的 secret 取值（大小写不敏感，含子串匹配 change-me）。
_PLACEHOLDER_SECRETS = {"change-me", "change-me-to-a-long-random-string", "changeme", "secret"}


def _is_placeholder_secret(secret: str) -> bool:
    low = secret.strip().lower()
    return low in _PLACEHOLDER_SECRETS or "change-me" in low or "changeme" in low


def evaluate_security_config(settings) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。生产姿态（cookie_secure）下关键项落入 errors。"""
    errors: List[str] = []
    warnings: List[str] = []
    strict = bool(settings.auth.cookie_secure)  # HTTPS 生产姿态代理

    def flag(message: str) -> None:
        (errors if strict else warnings).append(message)

    secret = (settings.auth.secret or "").strip()
    if not secret:
        flag("[auth] secret 未设置：会话与订阅/聚合令牌的 HMAC 回退到本地弱密钥"
             "（非口令派生，但可从配置推导）。生产必须设为长随机串。")
    elif _is_placeholder_secret(secret):
        # 占位符（如 backend.ini/production.example 里的 change-me…）：dev 告警、生产拒绝。
        flag("[auth] secret 仍是占位符（change-me…）：生产必须改为长随机串。")

    if "*" in settings.cors.allow_origins and settings.cors.allow_credentials:
        flag("[cors] allow_origins=* 且 allow_credentials=true 是不安全组合："
             "凭证型跨域应改为具体域名白名单。")

    if settings.network.disable_ca_bundle and strict:
        warnings.append("[network] disable_ca_bundle=true：生产环境已禁用 TLS 证书校验，"
                        "存在中间人风险，建议置 false。")

    return errors, warnings


def enforce_security_config(settings) -> None:
    """记录告警；若存在 error（生产姿态下的关键漏配）则抛 RuntimeError 拒绝启动。"""
    errors, warnings = evaluate_security_config(settings)
    for message in warnings:
        _logger.warning("⚠️  安全配置告警：%s", message)
    if errors:
        for message in errors:
            _logger.error("⛔ 安全配置错误：%s", message)
        raise RuntimeError(
            "启动被安全配置校验阻断（生产姿态下存在关键漏配）：\n  - "
            + "\n  - ".join(errors)
        )
