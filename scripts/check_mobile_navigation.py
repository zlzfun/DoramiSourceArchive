"""Browser regression for the responsive reader; all /api calls use synthetic fixtures.

Run against a local Vite dev/preview server. No credentials or database writes.
Requires the project's Playwright dependency and a local Chrome installation.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

SOURCE = "mobile-layout-fixture"
ARTICLES = [
    {
        "id": f"layout-{i}", "title": f"移动布局回归样例 {i:02d}：让资讯阅读保持连续",
        "source_id": SOURCE, "content_type": "rss_article", "content_shape": "article",
        "source_url": f"https://example.test/article/{i}", "has_content": True,
        "publish_date": "2026-09-15T08:00:00", "fetched_date": "2026-09-15T09:00:00",
        "summary_zh": "这是用于检查窄屏列表、滚动及阅读状态的合成内容。导航应保持可见，阅读不应被窗口变化打断。",
        "analysis_status": "completed", "quality_score": 7.5, "unread": True,
    }
    for i in range(60)
]
BODY = "\n\n".join(
    f"## 第 {i} 节\n\n" + "这是移动布局回归测试使用的长正文。切换窗口宽度后，仍应留在同一篇文章，保留阅读进度。" * 5
    for i in range(1, 25)
)


def fixtures(route, calls, unexpected):
    request = route.request
    url = urlsplit(request.url)
    path = url.path.removeprefix("/api")
    params = parse_qs(url.query)
    calls[(request.method, path)] += 1
    if path == "/auth/session":
        data = {"authenticated": True, "user": {"username": "layout-test", "role": "user", "interest_onboarding_completed": True}}
    elif path == "/runtime":
        data = {"version": "layout-fixture", "role": "reader", "account_role": "user", "reader_enabled": True,
                "collector_enabled": False, "personal_digest_enabled": False, "ai_beta_enabled": False,
                "user_sources_enabled": False, "default_surface": "reader"}
    elif path == "/reader/sources":
        data = {"sources": [{"source_id": SOURCE, "name": "布局测试来源", "content_shape": "article", "subscribed": True}],
                "subscribed_source_ids": [SOURCE]}
    elif path == "/reader/collections":
        data = {"collections": []}
    elif path == "/reader/favorites":
        data = {"favorite_ids": [], "items": [], "total": 0}
    elif path == "/reader/unread-counts":
        data = {"by_source": {SOURCE: 60}, "total": 60}
    elif path == "/reader/announcements":
        data = {"items": []}
    elif path == "/reader/feedback/unread-count":
        data = {"unread": 0}
    elif path == "/articles":
        skip = int(params.get("skip", [0])[0])
        limit = int(params.get("limit", [30])[0])
        items = ARTICLES if params.get("shape", ["article"])[0] == "article" else []
        data = {"items": items[skip:skip + limit], "total": len(items)}
    elif path.startswith("/articles/layout-"):
        article_id = path.split("/")[2]
        data = {**next(item for item in ARTICLES if item["id"] == article_id), "content": BODY}
    elif path.startswith("/reader/articles/layout-") and path.endswith("/read"):
        data = {"read_count": 1}
    else:
        unexpected.append(f"{request.method} {path}")
        route.fulfill(status=500, json={"detail": "Unexpected fixture request"})
        return
    route.fulfill(json=data)


def check_footer(page):
    footer = page.locator(".m-tabbar")
    expect(footer).to_be_visible()
    metrics = page.evaluate("""() => {
        const bar = document.querySelector('.m-tabbar').getBoundingClientRect();
        return {bottom:bar.bottom, width:innerWidth, height:innerHeight,
            scrollWidth:document.documentElement.scrollWidth,
            scrollHeight:document.documentElement.scrollHeight,
            canHitLastButton:document.elementFromPoint(innerWidth-30,bar.top+25)?.closest('.m-tab')?.getAttribute('aria-label')};
    }""")
    assert abs(metrics["bottom"] - metrics["height"]) <= 1, metrics
    assert metrics["scrollWidth"] <= metrics["width"] + 1, metrics
    assert metrics["scrollHeight"] <= metrics["height"] + 1, metrics
    assert metrics["canHitLastButton"] == "我的", metrics
    assert page.locator(".reader-vrail").count() == 0
    return metrics


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors, unexpected = [], [], []
    calls = Counter()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        context.route("**/api/**", lambda route: fixtures(route, calls, unexpected))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(args.base_url, wait_until="networkidle")
        expect(page.locator(".reader-vrail")).to_be_visible()
        expect(page.locator(".reader-entry").first).to_be_visible()
        page.get_by_role("button", name="动态", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        check_footer(page)
        expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "动态")
        checks.append("wide-to-narrow keeps selected view and pins footer")

        for width, height in [(320, 568), (767, 844), (768, 844), (900, 700), (1024, 768), (844, 390), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            check_footer(page)
        checks.append("compact breakpoints, short landscape and touch targets")

        page.get_by_role("button", name="文章", exact=True).click()
        expect(page.locator(".reader-entry").first).to_be_visible()
        listing = page.locator(".m-list")
        listing.evaluate("el => el.scrollTop = 700")
        page.wait_for_timeout(100)
        assert listing.evaluate("el => el.scrollTop") > 0
        check_footer(page)
        page.screenshot(path=str(args.output / "mobile-list.png"))
        count_before = calls[("GET", "/reader/sources")]
        page.set_viewport_size({"width": 1365, "height": 900})
        expect(page.locator(".reader-vrail")).to_be_visible()
        expect(page.locator(".reader-entry").first).to_be_attached()
        page.wait_for_timeout(100)
        assert page.locator(".reader-list-scroll").evaluate("el => el.scrollTop") >= 650
        assert calls[("GET", "/reader/sources")] == count_before > 0, dict(calls)
        checks.append("shared reader state: no duplicate source load; list position survives layout change")

        page.locator(".reader-list-scroll").evaluate("el => el.scrollTop = 0")
        page.locator(".reader-entry").first.click()
        expect(page.locator(".reader-pane-body")).to_contain_text("第 24 节")
        pane = page.locator(".reader-pane")
        pane.evaluate("el => el.scrollTop = (el.scrollHeight-el.clientHeight)*0.4")
        page.wait_for_timeout(100)
        body_calls = calls[("GET", "/articles/layout-0")]
        page.screenshot(path=str(args.output / "desktop-reader.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(".m-read-scroll")).to_be_visible()
        expect(page.locator(".m-read-scroll h1")).to_contain_text("移动布局回归样例 00")
        page.wait_for_timeout(150)
        progress = page.locator(".m-read-scroll").evaluate("el => el.scrollTop/(el.scrollHeight-el.clientHeight)")
        assert abs(progress - 0.4) < 0.03, progress
        assert calls[("GET", "/articles/layout-0")] == body_calls
        page.screenshot(path=str(args.output / "mobile-article.png"))
        checks.append("same article and reading progress without refetch after resize")
        history_length = page.evaluate("history.length")
        for width in (1365, 390, 1365, 390):
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_timeout(100)
            assert page.evaluate("history.length") == history_length
            if width > 1024:
                assert page.evaluate("history.state?.mLayer") is None
        checks.append("repeated layout changes do not accumulate empty history entries")

        page.go_back()
        expect(page.locator(".m-read-scroll")).to_have_count(0)
        check_footer(page)
        page.get_by_role("button", name="订阅源", exact=True).click()
        expect(page.locator(".m-drawer")).to_be_visible()
        page.go_back()
        expect(page.locator(".m-drawer")).to_have_count(0)
        page.get_by_role("button", name="我的", exact=True).click()
        page.get_by_role("button", name="设置", exact=False).click()
        expect(page.get_by_role("dialog", name="设置", exact=True)).to_be_visible()
        page.go_back()
        expect(page.get_by_role("dialog", name="设置", exact=True)).to_have_count(0)
        check_footer(page)
        checks.append("system back closes article, source drawer and settings in order")

        page.get_by_role("button", name="文章", exact=True).click()
        expect(page.locator(".m-list .reader-entry")).to_have_count(30)
        page.wait_for_timeout(100)
        listing = page.locator(".m-list")
        listing.evaluate("el => el.scrollTop = el.scrollHeight")
        expect(page.locator(".reader-entry")).to_have_count(60)
        checks.append("infinite scrolling observes the new layout's sentinel")

        page.evaluate("document.documentElement.dataset.theme='dark'")
        check_footer(page)
        page.screenshot(path=str(args.output / "mobile-dark.png"))
        checks.append("dark-theme mobile render")

        for width in (390, 900, 1024, 1025, 1365):
            fresh = context.new_page()
            fresh.on("pageerror", lambda error: errors.append(str(error)))
            fresh.set_viewport_size({"width": width, "height": 844})
            fresh.goto(args.base_url, wait_until="networkidle")
            if width <= 1024:
                check_footer(fresh)
            else:
                expect(fresh.locator(".reader-vrail")).to_be_visible()
                assert len(fresh.locator(".reader-shell").evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ')") ) == 4
            fresh.close()
        checks.append("fresh launches on both sides of the breakpoint")
        touch = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        touch.route("**/api/**", lambda route: fixtures(route, calls, unexpected))
        phone = touch.new_page()
        phone.on("pageerror", lambda error: errors.append(str(error)))
        phone.goto(args.base_url, wait_until="networkidle")
        phone.get_by_role("button", name="我的", exact=True).tap()
        expect(phone.get_by_role("region", name="我的", exact=True)).to_be_visible()
        check_footer(phone)
        phone.get_by_role("button", name="文章", exact=True).tap()
        phone.get_by_role("button", name="订阅源", exact=True).tap()
        expect(phone.locator(".m-drawer")).to_be_visible()
        phone.go_back()
        check_footer(phone)
        checks.append("touch emulation: navigation taps and source drawer")
        browser.close()
    assert not errors, errors
    assert not unexpected, unexpected
    result = {"base_url": args.base_url, "browser": args.channel, "checks": checks,
              "page_errors": errors, "unexpected_requests": unexpected,
              "scope": "Real frontend, synthetic API fixtures; not a real-device or backend acceptance test."}
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("tmp/mobile-navigation"))
    parser.add_argument("--channel", default="chrome")
    options = parser.parse_args()
    url = urlsplit(options.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("--base-url must be a local HTTP dev/preview server")
    run(options)
