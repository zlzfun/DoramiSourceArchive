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
    # 全站 MCP 总闸（admin-only 服务级熔断，v3.40.4 审计 M01 补审计）。
    "/api/mcp/toggle",
    # v3.42 审计 M11 补覆盖：文章 CRUD/批量删除、手动触发采集、归档导入
    # 都是改写全站归档的管理动作（只审计非只读方法，GET 浏览不入）。
    "/api/articles",
    "/api/fetch",
    "/api/archive/import",
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
    if method.upper() == "POST" and path == "/api/admin/analysis/backfills/estimate":
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


def _batch_accounts(_: re.Match[str], body: dict | None) -> RenderResult:
    # body 缺失(超过采集上限等)时如实记「数量未知」,不伪造 0(v3.43.2 codex 检视)。
    if body is None:
        return "批量更新账户（数量未知，请求体未采集）", None
    payload = body
    # 人数统计与服务层 batch_update_users 同一规范化口径:strip→去空→去重
    # (原始数组含重复/空白项时曾多计,与实际处理数不符)。
    stripped = [str(u or "").strip() for u in (payload.get("usernames") or [])]
    names = list(dict.fromkeys(u for u in stripped if u))
    parts: list[str] = []
    if payload.get("role") is not None:
        parts.append(f"角色改为{_role_label(payload['role'])}")
    if payload.get("is_active") is not None:
        parts.append("启用" if payload["is_active"] else "停用")
    if payload.get("ai_beta_enabled") is not None:
        parts.append("开启 AI" if payload["ai_beta_enabled"] else "关闭 AI")
    action = "、".join(parts) or "更新"
    preview = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
    return f"批量{action} {len(names)} 个账户（{preview}）", None


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
    ("POST", re.compile(r"^/api/accounts/batch$"), _batch_accounts),
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
    (
        "PUT",
        re.compile(r"^/api/admin/analysis/config$"),
        lambda _m, body: (
            "更新分析功能开关：" + "、".join(
                f"{key}={'开' if value else '关'}"
                for key, value in sorted((body or {}).items())
                if value is not None
            ),
            None,
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/analysis/backfills$"),
        lambda _m, body: (
            f"创建 full_analysis 回填（范围={(body or {}).get('days', '未知')} 天，"
            f"策略={(body or {}).get('selection', '未知')}）",
            None,
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/analysis/backfills/(?P<target>[^/]+)/(?P<action>[^/]+)$"),
        lambda match, _body: (
            f"{match.group('action')} full_analysis 回填 {match.group('target')}",
            match.group("target"),
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/cms-tags$"),
        lambda _m, body: (
            f"创建规范标签 {(body or {}).get('code') or (body or {}).get('name_zh') or ''}".rstrip(),
            str((body or {}).get("code") or "") or None,
        ),
    ),
    (
        "PATCH",
        re.compile(r"^/api/admin/cms-tags/(?P<target>[^/]+)$"),
        lambda match, _body: _id_target(match, None, noun="规范标签", action="更新"),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/cms-tags/(?P<target>[^/]+)/(?:merge|deprecate|retag)$"),
        lambda match, _body: _id_target(match, None, noun="规范标签", action="治理"),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/cms-tags/(?P<target>[^/]+)/aliases$"),
        lambda match, _body: _id_target(match, None, noun="规范标签同义词", action="新增"),
    ),
    (
        "DELETE",
        re.compile(r"^/api/admin/cms-tags/(?P<target>[^/]+)/aliases/[^/]+$"),
        lambda match, _body: _id_target(match, None, noun="规范标签同义词", action="删除"),
    ),
    (
        "PATCH",
        re.compile(r"^/api/admin/cms-tag-candidates/(?P<target>[^/]+)$"),
        lambda match, _body: _id_target(match, None, noun="候选标签", action="更新"),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/cms-tag-candidates/(?P<target>[^/]+)/(?:resolve|activate|reject)$"),
        lambda match, _body: _id_target(match, None, noun="候选标签", action="治理"),
    ),
    (
        "DELETE",
        re.compile(r"^/api/admin/cms-tag-candidates/(?P<target>[^/]+)$"),
        lambda match, _body: _id_target(match, None, noun="候选标签", action="删除"),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/taxonomy/v1/publish$"),
        lambda _m, _body: ("发布 Taxonomy v1", "v1"),
    ),
    (
        "PATCH",
        re.compile(r"^/api/admin/taxonomy/interest-catalog-policy$"),
        lambda _m, _body: ("更新用户兴趣目录策略", None),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/taxonomy/aliases/backfill$"),
        lambda _m, _body: ("补齐规范标签当前名称同义词", None),
    ),
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
    (
        "POST",
        re.compile(r"^/api/admin/source-visibility/(?P<target>[^/]+)$"),
        lambda match, body: (
            f"{'在读者面隐藏源' if (body or {}).get('hidden') else '恢复源读者面可见'} "
            f"{unquote(match.group('target'))}",
            unquote(match.group("target")),
        ),
    ),
    ("POST", re.compile(r"^/api/llm/config$"), lambda _m, _b: ("更新 LLM 配置", None)),
    ("POST", re.compile(r"^/api/mcp/toggle$"), lambda _m, _b: ("切换全站 MCP 总闸", None)),
    # ── 归档写入口(v3.42 M11)──
    (
        "POST",
        re.compile(r"^/api/articles/batch-delete$"),
        # body 缺失(超采集上限)时如实记「数量未知」,不伪造 0 篇(v3.43.2)。
        lambda _m, body: (
            "批量删除文章（数量未知，请求体未采集）" if body is None
            else f"批量删除文章 {len(body.get('ids') or body.get('article_ids') or [])} 篇",
            None,
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/articles$"),
        # 正文可能超 body 采集上限(body=None),标题尽力而为。
        lambda _m, body: (
            f"手工录入文章 {str((body or {}).get('title') or '')[:30]}".rstrip(),
            None,
        ),
    ),
    # 文章路由是 {article_id:path}(手工录入 ID 可含斜杠),规则须吞完整剩余路径,
    # 否则含 / 的合法 ID 退化为空摘要(v3.43.2 codex 检视)。
    (
        "PUT",
        re.compile(r"^/api/articles/(?P<target>.+)$"),
        lambda match, body: _id_target(match, body, noun="文章", action="编辑"),
    ),
    (
        "DELETE",
        re.compile(r"^/api/articles/(?P<target>.+)$"),
        lambda match, body: _id_target(match, body, noun="文章", action="删除"),
    ),
    # /api/fetch/batch 的精确规则必须先于下方的通用单节点规则,否则被记成
    # 「手动触发采集节点 batch」(v3.43.2 codex 检视)。
    (
        "POST",
        re.compile(r"^/api/fetch/batch$"),
        lambda _m, body: (
            "批量触发采集（节点数未知，请求体未采集）" if body is None
            else f"批量触发采集 {len(body.get('items') or [])} 个节点",
            None,
        ),
    ),
    (
        "POST",
        re.compile(r"^/api/fetch/(?P<target>[^/]+)$"),
        lambda match, body: _id_target(match, body, noun="采集节点", action="手动触发"),
    ),
    (
        "POST",
        re.compile(r"^/api/archive/import/"),
        lambda _m, _b: ("归档导入（archive sync）", None),
    ),
    (
        "POST",
        re.compile(r"^/api/admin/remote-sync/schedule$"),
        # 绝不读 body 的 password 字段——仅记开关状态。
        lambda _m, body: (
            f"{'启用' if (body or {}).get('enabled') else '停用'}远程定时同步",
            None,
        ),
    ),
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
    except Exception as exc:  # noqa: BLE001 - 审计绝不能阻断业务请求
        # 只记异常摘要，绝不带请求体（可能含密码/token）——审计写库失败需可见，但不静默。
        _logger.warning("管理员操作审计写库失败（忽略）: %s", exc)
