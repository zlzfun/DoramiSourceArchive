"""Admin console main path for #90: login → node management → runs, key states (Chromium).

Loading states are observed by holding the real request until the skeleton or placeholder is
asserted, then letting the untouched request reach the backend; responses are never replaced.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from urllib.parse import urlsplit

from playwright.sync_api import expect

from e2e.mobile_reader import login, observed_page
from e2e.reader_fixture import ADMIN_USERNAME

SHAPE_LABELS = {"article": "文章", "bulletin": "动态", "social": "社交", "podcast": "播客"}


def path_is(path):
    return lambda response: urlsplit(response.url).path == path


@contextmanager
def held_requests(page, path):
    """Hold requests to ``path`` until the block exits, then forward them unchanged."""
    held = []
    matcher = lambda url: urlsplit(url).path == path  # noqa: E731
    page.route(matcher, lambda route: held.append(route))
    try:
        yield held
    finally:
        # Forward before unrouting: unroute settles still-pending routes itself.
        for route in held:
            route.continue_()
        page.unroute(matcher)


def run_admin_flows(browser, base_url, artifacts, result):
    checks = result.setdefault("checks", [])
    with observed_page(browser, base_url, artifacts, "admin", result, desktop=True) as (page, _audit):
        page.goto(base_url)
        with page.expect_response(path_is("/api/fetchers"), timeout=30000) as fetchers:
            assert login(page, username=ADMIN_USERNAME).status == 200
        catalog = [fetcher for fetcher in fetchers.value.json() if not fetcher.get("is_template")]
        shapes = Counter(fetcher.get("shape") or "article" for fetcher in catalog)
        nav = page.get_by_role("navigation", name="页面")
        expect(nav.get_by_role("button", name="节点管理")).to_be_visible(timeout=30000)
        expect(page.locator(".app-arrive")).to_have_count(0, timeout=15000)
        checks.append("admin login opens the console with collector pages")

        with held_requests(page, "/api/fetch-runs") as held:
            nav.get_by_role("button", name="节点管理").click()
            inspector = page.get_by_role("complementary", name="节点检视器")
            expect(inspector.locator(".inspector-empty-mini")).to_have_text("加载中…")
            assert held and all("fetcher_id=" in route.request.url for route in held), [r.request.url for r in held]
            page.screenshot(path=str(artifacts / "admin-nodes-loading.png"), animations="disabled")
        expect(inspector.locator(".inspector-empty-mini")).to_have_text("尚无运行记录，首次运行后这里会出现批次时间线")
        for shape, label in SHAPE_LABELS.items():
            tab = page.get_by_role("tab", name=label)
            expect(tab.locator(".node-shape-count")).to_have_text(str(shapes.get(shape, 0)))
        expect(page.locator(".board-node")).to_have_count(shapes["article"])
        selected = page.locator(".board-node.board-node-sel")
        expect(selected).to_have_count(1)
        expect(inspector.locator(".inspector-title")).to_have_text(selected.locator(".board-node-name-text").inner_text())
        page.screenshot(path=str(artifacts / "admin-nodes.png"), animations="disabled")
        checks.append("node board: per-shape counts match /api/fetchers, one node auto-selected into the inspector, runs load then empty")

        with held_requests(page, "/api/collection-job-runs"), \
                page.expect_request(lambda request: urlsplit(request.url).path == "/api/collection-job-runs"):
            nav.get_by_role("button", name="任务与运行").click()
            expect(page.locator(".flow-skel")).to_have_count(6)
            page.screenshot(path=str(artifacts / "admin-runs-loading.png"), animations="disabled")
        expect(page.locator(".flow-skel")).to_have_count(0)
        expect(page.locator(".flow-empty")).to_contain_text("当前筛选下暂无运行记录")
        # The only saved job is the catalog's podcast job, which the fixture stores disabled.
        rows = page.locator(".tt-row")
        expect(rows.locator(".tt-next", has_text="距下次")).to_have_count(0)
        disabled = page.locator(".tt-row.is-off")
        expect(disabled).to_have_count(1)
        expect(disabled.locator(".tt-next")).to_have_text("已停用")
        expect(rows.filter(has_text="临时抓取").locator(".tt-next")).to_have_text("暂无")
        expect(page.locator(".flow-stat", has_text="全部运行").locator(".flow-stat-num")).to_have_text("0")
        page.screenshot(path=str(artifacts / "admin-runs.png"), animations="disabled")
        checks.append("runs page shows skeleton rows while loading, then no scheduled job, the disabled job, "
                      "and the empty run flow")
