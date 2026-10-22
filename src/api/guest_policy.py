"""游客门控白名单 (src/api/guest_policy.py, issue #213)

游客会话(role=guest)只能访问这里登记的 (方法, 路由模板);其余一律由鉴权中间件回
403 ``{"code": "login_required"}``——不回 401,否则前端会当成登录过期踢回登录页。
fail-closed:新端点默认不对游客开放,要开放必须在这里显式登记;
``tests/test_route_authz_audit.py`` 钉住「登记项真实存在 + 其余路由对游客一律 403」。

判定按路由模板而非路径前缀:先用 Starlette 的路由匹配找到请求真正落到的那条路由
(与框架分发同序:首个完全匹配),再查表——不会因为前缀相同而误放写端点。
"""
from __future__ import annotations

from typing import Any, FrozenSet, Iterable, Optional, Tuple

from starlette.routing import Match

# (方法, 路由模板)。分组即方案 docs/archive/issue-213-guest-mode.md §3.2 的白名单表。
GUEST_ALLOWED_ROUTES: FrozenSet[Tuple[str, str]] = frozenset({
    # 会话与能力(/api/auth/{session,logout,guest} 在中间件里本就公开,不必登记)
    ("GET", "/api/runtime"),
    # 读内容
    ("GET", "/api/articles"),
    ("GET", "/api/articles/facets"),
    ("GET", "/api/articles/{article_id:path}"),
    ("GET", "/api/articles/{article_id:path}/analysis"),
    ("GET", "/api/reader/sources"),
    ("GET", "/api/reader/collections"),
    ("GET", "/api/reader/rankings"),
    ("GET", "/api/reader/rankings/history"),
    ("GET", "/api/reader/rankings/{date}/tags/{tag_code:path}"),
    ("GET", "/api/reader/announcements"),
    ("GET", "/api/reader/favorites"),            # 游客恒空:前端挂载即拉,放行免得报错
    ("GET", "/api/reader/interests"),            # 同上
    ("GET", "/api/reader/interests/catalog"),
    ("GET", "/api/podcasts/episodes/{episode_id}/texts"),
    ("GET", "/api/reader/articles/{article_id}/listen-audio"),
    ("GET", "/api/reader/podcast-artifacts/{artifact_id}/audio"),
    ("GET", "/api/media/proxy"),                 # 游客只取已缓存图片(routers/media.py)
    # 早报(共享游客版;重编不在此列)
    ("GET", "/api/reader/briefs"),
    ("GET", "/api/reader/briefs/today"),
    ("GET", "/api/reader/briefs/{report_date}"),
    ("POST", "/api/reader/briefs/today/ensure"),
    # 已读(按访客身份记)
    ("POST", "/api/reader/articles/{article_id}/read"),
    ("POST", "/api/reader/articles/{article_id}/mark-read"),
    ("POST", "/api/reader/articles/{article_id}/mark-unread"),
    ("GET", "/api/reader/unread-counts"),
    ("POST", "/api/reader/mark-all-read"),
    ("POST", "/api/reader/mark-scope-read"),
    ("POST", "/api/reader/sources/{source_id}/mark-all-read"),
    # 公告关闭(按访客身份记)
    ("POST", "/api/reader/announcements/{announcement_id}/dismiss"),
    # AI(每位访客分别计次)
    ("POST", "/api/reader/ai/translate"),
    ("POST", "/api/reader/ai/social/{article_id}/translate"),
    ("POST", "/api/reader/ai/translation-jobs"),
    ("GET", "/api/reader/ai/translation-jobs"),
    ("POST", "/api/reader/ai/translation-jobs/{job_id}/resume"),
    ("POST", "/api/reader/ai/translation-jobs/{job_id}/cancel"),
    ("POST", "/api/reader/ai/podcasts/{episode_id}/translate-transcript"),
    ("POST", "/api/reader/ai/podcasts/{episode_id}/ondemand"),
    ("POST", "/api/reader/ai/articles/{article_id}/ondemand"),
    ("POST", "/api/reader/ai/summarize"),
    ("GET", "/api/reader/ai/ask/progress"),
    ("POST", "/api/reader/ai/ask"),
})

LOGIN_REQUIRED_BODY = {"code": "login_required", "detail": "登录后可用"}


def matched_route_template(routes: Iterable[Any], scope: dict) -> Optional[Tuple[str, str]]:
    """请求真正会落到的路由 (方法, 模板);框架分发同序取首个完全匹配,没有则 None。"""
    method = str(scope.get("method") or "").upper()
    for route in routes:
        try:
            match, _child = route.matches(scope)
        except Exception:  # noqa: BLE001 - 个别自定义路由不支持该 scope,跳过
            continue
        if match == Match.FULL:
            return method, str(getattr(route, "path", ""))
    return None


def guest_route_allowed(routes: Iterable[Any], scope: dict) -> bool:
    template = matched_route_template(routes, scope)
    return template is not None and template in GUEST_ALLOWED_ROUTES
