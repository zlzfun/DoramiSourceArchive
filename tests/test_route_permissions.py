"""路由权限审计（阶段1 安全网）。

枚举所有 ``/api/*`` 路由，按 ``app.py`` 中间件的判定优先级归类为
public / admin / collector / reader / authenticated-any，并冻结
「authenticated-any（任意登录账户可访问、无更细鉴权）」白名单。

价值：新增端点若忘了纳入任何鉴权前缀/特例，会落入 authenticated-any 而被本测试
拦下，强制开发者显式归类——正是这条测试发现了 ``/api/fetch/*`` 因前缀尾斜杠
漏过 collector 鉴权的越权缺口。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# 任意登录账户即可访问、且无更细鉴权的路由（method, path）。
# 任何落入此类但不在本集合的路由都会让测试失败——要么是真·公开读接口（在此登记），
# 要么是漏配了 collector/reader/admin 鉴权（应去补前缀/特例，而非往这里加）。
EXPECTED_AUTHENTICATED_ANY = {
    ("GET", "/api/articles"),
    # 台账分面聚合:对已是 authenticated-any 的 GET /api/articles 的只读 group-by
    # 视图,暴露面不超过列表本身(台账优化波新增时漏登记,此处补记)。
    ("GET", "/api/articles/facets"),
    ("GET", "/api/articles/{article_id:path}"),
    ("GET", "/api/runtime"),
    ("POST", "/api/auth/avatar"),
    ("POST", "/api/auth/change-password"),
    # 自助设置登录默认落地界面（任意登录账户，不属任何 surface 闸）。
    ("POST", "/api/auth/preferences"),
}


def _classify(app_module, method: str, path: str) -> str:
    """镜像 require_admin_session 中间件的判定优先级。"""
    a = app_module
    if a.is_public_auth_path(path):
        return "public-auth"
    if a.is_public_subscription_path(path):
        return "public-sub"
    # disabled_runtime_surface：reader 前缀短路 → reader；否则 collector 前缀 → collector
    is_reader = (
        path == "/mcp"
        or path.startswith("/mcp/")
        or a._path_matches(path, a.READER_API_PREFIXES)
    )
    if is_reader:
        return "reader"
    if a._path_matches(path, a.COLLECTOR_API_PREFIXES):
        return "collector"
    # 仅当未命中 runtime 面时，再叠加方法级特例
    if a.account_admin_required(path):
        return "admin"
    if a.article_write_requires_collector(path, method):
        return "collector"
    if a.archive_import_requires_admin(path, method):
        return "admin"
    return "authenticated-any"


def _iter_api_routes(app_module):
    for route in app_module.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not path.startswith("/api/"):
            continue
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            yield method, path


def test_authenticated_any_routes_are_frozen():
    import api.app as app_module

    found = {
        (method, path)
        for method, path in _iter_api_routes(app_module)
        if _classify(app_module, method, path) == "authenticated-any"
    }
    unexpected = found - EXPECTED_AUTHENTICATED_ANY
    missing = EXPECTED_AUTHENTICATED_ANY - found
    assert not unexpected, (
        f"发现未登记的 authenticated-any 路由（可能漏配鉴权）：{sorted(unexpected)}"
    )
    assert not missing, (
        f"白名单中的路由已不存在或已被改鉴权，请更新白名单：{sorted(missing)}"
    )


def test_fetch_trigger_routes_are_collector_gated():
    """回归保护：/api/fetch/* 触发采集，必须归 collector（曾因前缀尾斜杠漏过）。"""
    import api.app as app_module

    for path in ("/api/fetch/batch", "/api/fetch/{fetcher_id}"):
        assert _classify(app_module, "POST", path) == "collector", path
    # 兄弟路径不应被误伤
    assert _classify(app_module, "GET", "/api/fetchers") == "collector"
    assert _classify(app_module, "GET", "/api/fetch-runs") == "collector"


def test_account_and_admin_surfaces_are_admin_gated():
    import api.app as app_module

    for method, path in _iter_api_routes(app_module):
        if path.startswith("/api/accounts") or path.startswith("/api/admin"):
            assert _classify(app_module, method, path) == "admin", (method, path)
