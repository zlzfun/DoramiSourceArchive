"""路由鉴权覆盖审计（阶段1 Router 化的护栏）。

阶段1 把上百个端点从 app.py 迁到按域拆分的 APIRouter，鉴权仍由 app.py 的中间件
``require_admin_session`` 统一强制——它对每条 ``/api/*`` 路由依次套用：
  ① 公开白名单（登录/登出/会话、/api/public/* 令牌消费端）→ 放行；
  ② 其余一律要求登录会话（401）；
  ③ collector/reader surface 前缀表（disabled_runtime_surface）；
  ④ account_admin_required（/api/accounts、/api/admin → 仅 admin）；
  ⑤ article_write_requires_collector（/api/articles 写 → collector）；
  ⑥ archive_import_requires_admin（/api/archive/import → 仅 admin）。

迁移端点最大的隐性风险是「某路由因前缀漏配，从应有的 collector/admin 闸悄悄降级为
任意登录账户可访问」。本测试遍历真实注册的所有 ``/api/*`` 路由，要求每条都落入下列
某一明确分类（复用 app.py 的真实判定函数，与中间件逐项对齐），否则失败——逼迫新增
端点时做出有意识的鉴权归类，而非默默漏过。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.app as app_module  # noqa: E402


# 显式「仅需登录、任意账户」白名单：自助类与运行时能力探测，不属于任何 surface 闸。
AUTHENTICATED_ANY_ALLOWLIST = {
    "/api/auth/avatar",
    "/api/auth/change-password",
    "/api/auth/preferences",
    "/api/runtime",
}


def _all_api_paths() -> set[str]:
    return {
        route.path
        for route in app_module.app.routes
        if getattr(route, "path", "").startswith("/api/")
    }


def _classify(path: str) -> str | None:
    """把一条 /api 路由归入唯一鉴权类别；无法归类返回 None。"""
    # ① 公开（无需登录）。
    if app_module.is_public_auth_path(path) or app_module.is_public_subscription_path(path):
        return "public"
    # ④ admin-only（账号管理 / 运维看板）。
    if app_module.account_admin_required(path):
        return "admin"
    # ③ collector / reader surface 前缀表。
    if app_module._path_matches(path, app_module.READER_API_PREFIXES):
        return "reader"
    if app_module._path_matches(path, app_module.COLLECTOR_API_PREFIXES):
        return "collector"
    # ⑤ 文章写操作按方法升级为 collector（读为任意登录；写覆盖 POST/PUT/DELETE）。
    if any(app_module.article_write_requires_collector(path, m) for m in ("POST", "PUT", "DELETE")):
        return "article_write_collector"
    # ⑥ 归档导入按方法升级为 admin。
    if app_module.archive_import_requires_admin(path, "POST"):
        return "archive_import_admin"
    # ⑦ 显式「仅登录」白名单。
    if path in AUTHENTICATED_ANY_ALLOWLIST:
        return "authenticated_any"
    return None


def test_every_api_route_has_explicit_authz_classification():
    """每条 /api 路由都必须有明确鉴权归类，杜绝迁移端点静默丢闸。"""
    unclassified = sorted(p for p in _all_api_paths() if _classify(p) is None)
    assert not unclassified, (
        "以下 /api 路由未落入任何鉴权分类（疑似迁移后丢失 collector/admin 闸，"
        "或为新端点需显式归类）：\n  " + "\n  ".join(unclassified)
    )


def test_non_public_api_routes_require_login():
    """非公开的 /api 路由在无会话时必须被中间件拦为 401（鉴权兜底未被绕过）。"""
    from starlette.testclient import TestClient

    with TestClient(app_module.app) as client:
        for path in sorted(_all_api_paths()):
            if app_module.is_public_auth_path(path) or app_module.is_public_subscription_path(path):
                continue
            # 用具体路径替换路径参数占位，避免 404 早于鉴权返回。
            probe = (
                path.replace("{username}", "probe")
                .replace("{article_id:path}", "probe")
                .replace("{job_id}", "probe")
                .replace("{job_run_id}", "1")
                .replace("{run_id}", "1")
                .replace("{group_id}", "1")
                .replace("{task_id}", "1")
                .replace("{source_id}", "probe")
                .replace("{fetcher_id}", "probe")
                .replace("{subscription_id}", "1")
                .replace("{article_id}", "probe")
            )
            resp = client.get(probe)
            # 未登录：要么 401（鉴权拦截），要么 405（方法不匹配但仍说明未绕过到处理器）。
            assert resp.status_code in (401, 405), (
                f"{probe} 未登录访问返回 {resp.status_code}，预期 401（鉴权兜底）"
            )


def test_collector_and_reader_prefixes_are_disjoint_enough():
    """reader 前缀短路优先于 collector：确保 /api/vector 的 search/stats 例外不被 collector 吞。

    （回归保护：CLAUDE.md 记录的 /api/vector 拆分——search/stats/subscribed-stats 归 reader，
    其余 vector/* 归 collector。）"""
    reader_vector = ["/api/vector/search", "/api/vector/stats", "/api/vector/subscribed-stats"]
    for path in reader_vector:
        assert app_module._path_matches(path, app_module.READER_API_PREFIXES), path
    # 构建/管理类仍归 collector。
    assert app_module._path_matches("/api/vector/reindex-all", app_module.COLLECTOR_API_PREFIXES)
    assert app_module._path_matches("/api/vectorize/all-pending", app_module.COLLECTOR_API_PREFIXES)
