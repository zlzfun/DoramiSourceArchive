"""管理员管理写操作审计。

只落经过管理前缀的写请求元数据与按白名单规则渲染的语义摘要；请求体全文永不落库。
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Callable
from urllib.parse import unquote

from sqlmodel import Session

from models.db import AdminAuditRecord


AUDIT_PATH_PREFIXES = (
    "/api/accounts",
    "/api/admin",
    "/api/x-api",
    "/api/source-configs",
    "/api/collection-jobs",
    "/api/llm",
    "/api/daily-brief",
)
# /api/reader/* 与 /api/auth/* 刻意豁免：管理员自己的阅读、订阅与自助改密
# 属于个人操作，不是需要管理员互相审阅的“管理操作”。

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_logger = logging.getLogger("dorami.admin_audit")

RenderResult = tuple[str, str | None]
RenderFn = Callable[[re.Match[str], dict | None], RenderResult]


def should_audit(path: str, method: str) -> bool:
    """仅审计管理前缀下的非只读请求，且前缀匹配必须落在路径段边界。"""
    if method.upper() in _READ_ONLY_METHODS:
        return False
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in AUDIT_PATH_PREFIXES
    )


def _role_label(value: object) -> str:
    role = str(value or "user")
    return {"admin": "管理员", "user": "读者"}.get(role, role)


def _create_account(_: re.Match[str], body: dict | None) -> RenderResult:
    payload = body or {}
    username = str(payload.get("username") or "").strip()
    if not username:
        return "新建账户", None
    return (
        f"新建账户 {username}(角色 {_role_label(payload.get('role'))})",
        username,
    )


def _update_account(match: re.Match[str], body: dict | None) -> RenderResult:
    username = unquote(match.group("username"))
    payload = body or {}
    parts: list[str] = []
    if payload.get("role") is not None:
        parts.append(f"将 {username} 角色改为 {_role_label(payload['role'])}")
    if payload.get("is_active") is not None:
        parts.append(f"{'启用' if payload['is_active'] else '停用'} {username}")
    if payload.get("ai_beta_enabled") is not None:
        parts.append(
            f"{'开启' if payload['ai_beta_enabled'] else '关闭'} {username} 的 AI"
        )
    return "；".join(parts), username


def _account_target(
    match: re.Match[str], _body: dict | None, *, action: str
) -> RenderResult:
    username = unquote(match.group("username"))
    return f"{action} {username}", username


def _reset_password(match: re.Match[str], _body: dict | None) -> RenderResult:
    username = unquote(match.group("username"))
    return f"重置 {username} 的密码", username


def _global_ai_beta(_: re.Match[str], body: dict | None) -> RenderResult:
    if not body or body.get("enabled") is None:
        return "更新全局 AI Beta 开关", None
    action = "开启" if body["enabled"] else "关闭"
    return f"{action}全局 AI Beta", None


def _id_target(
    match: re.Match[str], _body: dict | None, *, noun: str, action: str
) -> RenderResult:
    target = unquote(match.group("target"))
    return f"{action}{noun} {target}", target


# 语义摘要注册表：顺序即优先级，首个 (method, path regex) 命中即停止。
AUDIT_SUMMARY_RULES: list[tuple[str, re.Pattern[str], RenderFn]] = [
    ("POST", re.compile(r"^/api/accounts$"), _create_account),
    (
        "PUT",
        re.compile(r"^/api/accounts/(?P<username>[^/]+)$"),
        _update_account,
    ),
    (
        "POST",
        re.compile(r"^/api/accounts/(?P<username>[^/]+)/reset-password$"),
        _reset_password,
    ),
    (
        "DELETE",
        re.compile(r"^/api/accounts/(?P<username>[^/]+)$"),
        lambda match, body: _account_target(
            match, body, action="删除账户"
        ),
    ),
    ("POST", re.compile(r"^/api/x-api/config/test$"), lambda _m, _b: ("测试 X API 连通", None)),
    ("POST", re.compile(r"^/api/x-api/config$"), lambda _m, _b: ("更新 X API 配置", None)),
    ("POST", re.compile(r"^/api/admin/ai-beta/global$"), _global_ai_beta),
    ("POST", re.compile(r"^/api/admin/announcements$"), lambda _m, _b: ("发布公告", None)),
    (
        "PUT",
        re.compile(r"^/api/admin/announcements/(?P<target>[^/]+)$"),
        lambda match, body: _id_target(
            match, body, noun="公告", action="更新"
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/announcements/(?P<target>[^/]+)/toggle$"),
        lambda match, body: _id_target(
            match, body, noun="公告", action="切换"
        ),
    ),
    (
        "DELETE",
        re.compile(r"^/api/admin/announcements/(?P<target>[^/]+)$"),
        lambda match, body: _id_target(
            match, body, noun="公告", action="删除"
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/feedback/(?P<target>[^/]+)/status$"),
        lambda match, body: _id_target(
            match, body, noun="反馈", action="更新"
        ),
    ),
    ("POST", re.compile(r"^/api/llm/config$"), lambda _m, _b: ("更新 LLM 配置", None)),
]


def record_audit(
    engine,
    *,
    username: str,
    method: str,
    path: str,
    status_code: int,
    body: dict | None,
) -> None:
    """写一条管理审计记录；任何失败都仅记 debug，绝不影响原请求。"""
    try:
        normalized_method = method.upper()
        summary = ""
        target = None
        for rule_method, path_pattern, render in AUDIT_SUMMARY_RULES:
            if normalized_method != rule_method:
                continue
            match = path_pattern.match(path)
            if match is None:
                continue
            summary, target = render(match, body)
            break

        with Session(engine) as session:
            session.add(
                AdminAuditRecord(
                    username=username,
                    method=normalized_method,
                    path=path,
                    status_code=int(status_code),
                    summary=summary,
                    target=target,
                    at=datetime.datetime.now().isoformat(),
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 - 审计绝不能阻断业务请求
        _logger.debug("管理员操作审计写库失败（忽略）", exc_info=True)
