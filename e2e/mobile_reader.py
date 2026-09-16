"""Interaction tests for #86 and the mobile-reader slice of #90 (Chromium)."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
import re
import sqlite3
from urllib.parse import urlsplit

from playwright.sync_api import expect

from e2e.reader_fixture import ARTICLE_COUNT, PASSWORD, SOURCE_A, SOURCE_B, SOURCE_NAMES, USERNAME


def viewport_metrics(page):
    return page.evaluate("""() => {
        const root = document.documentElement;
        const viewport = window.visualViewport;
        const bar = document.querySelector('.m-tabbar').getBoundingClientRect();
        return {
            visibleBottom: viewport.offsetTop + viewport.height,
            bar: {top: bar.top, bottom: bar.bottom, left: bar.left, right: bar.right},
            root: [root.scrollWidth, root.scrollHeight], client: [root.clientWidth, root.clientHeight],
            scroll: [scrollX, scrollY],
            filterLines: [...document.querySelectorAll('.m-topbar .mini-seg-btn')].map(button => {
                const range = document.createRange(); range.selectNodeContents(button);
                return range.getClientRects().length;
            }),
            targets: [...document.querySelectorAll('.m-tabbar .m-tab')].map(button => {
                const box = button.getBoundingClientRect();
                return {name: button.getAttribute('aria-label'),
                    reachable: button.contains(document.elementFromPoint(box.x + box.width/2, box.y + box.height/2))};
            })
        };
    }""")


def assert_viewport(page):
    expect(page.locator(".m-tabbar")).to_be_visible()
    metrics = viewport_metrics(page)
    assert abs(metrics["bar"]["bottom"] - metrics["visibleBottom"]) <= 1, metrics
    assert 0 <= metrics["bar"]["left"] < metrics["bar"]["right"] <= metrics["client"][0] + 1, metrics
    assert all(scroll <= client + 1 for scroll, client in zip(metrics["root"], metrics["client"])), metrics
    assert metrics["scroll"] == [0, 0], metrics
    assert all(lines == 1 for lines in metrics["filterLines"]), metrics
    assert metrics["targets"] and all(target["reachable"] for target in metrics["targets"]), metrics
    expect(page.locator(".reader-vrail")).to_have_count(0)
    return metrics


@contextmanager
def observed_page(browser, base_url, artifacts, name, result):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    context.set_default_timeout(8000)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    audit = {"reject_login": False, "offline": False, "offline_requests": set(),
             "page_errors": [], "unexpected": [], "requests": Counter(), "cancelled_brand_images": []}
    page.on("pageerror", lambda error: audit["page_errors"].append(str(error)))

    def response_seen(response):
        path = urlsplit(response.url).path
        expected = audit["reject_login"] and path == "/api/auth/login" and response.status == 401
        if response.status >= 400 and not expected:
            audit["unexpected"].append(f"HTTP {response.status} {path}")

    def request_failed(request):
        # A fast session response unmounts the loading logo during reload; Chromium
        # aborts that image. Retain evidence and check it is not a visible broken image.
        if (request.failure == "net::ERR_ABORTED" and request.resource_type == "image"
                and urlsplit(request.url).path.startswith("/brand/")):
            audit["cancelled_brand_images"].append(request.url)
            return
        if request not in audit["offline_requests"] or request.failure != "net::ERR_INTERNET_DISCONNECTED":
            audit["unexpected"].append(f"Request failed: {request.url} {request.failure}")

    def request_seen(request):
        if audit["offline"]:
            audit["offline_requests"].add(request)
        if request.url.startswith(base_url):
            audit["requests"][(request.method, urlsplit(request.url).path)] += 1
        elif not request.url.startswith(("data:", "blob:")):
            audit["unexpected"].append(f"External request: {request.url}")

    page.on("response", response_seen)
    page.on("requestfailed", request_failed)
    page.on("request", request_seen)
    passed = False
    try:
        yield page, audit
        assert page.evaluate("""urls => [...document.images].every(img =>
            !urls.includes(img.currentSrc || img.src) || !img.getClientRects().length || img.naturalWidth > 0)
        """, audit["cancelled_brand_images"]), audit["cancelled_brand_images"]
        assert not audit["page_errors"], audit["page_errors"]
        assert not audit["unexpected"], audit["unexpected"]
        passed = True
    except Exception:
        try:
            page.screenshot(path=str(artifacts / f"{name}-failure.png"))
            (artifacts / f"{name}-failure-dom.json").write_text(json.dumps(page.evaluate("""() => ({
                viewport: [innerWidth, innerHeight, visualViewport.width, visualViewport.height],
                areas: [...document.querySelectorAll('.reader-pane,.m-read-scroll,.reader-list-scroll,.m-list')]
                    .map(el => ({class: el.className, top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight}))
            })"""), indent=2))
        except Exception:
            pass  # Preserve the original failure if the browser itself has exited.
        raise
    finally:
        result.setdefault("browser_audits", {})[name] = {
            "page_errors": audit["page_errors"], "unexpected": audit["unexpected"],
            "cancelled_brand_images": audit["cancelled_brand_images"],
            "requests": {f"{method} {path}": count for (method, path), count in audit["requests"].items()},
            "injected_offline_requests": sorted({urlsplit(request.url).path for request in audit["offline_requests"]}),
        }
        try:
            context.tracing.stop(path=str(artifacts / f"{name}-trace.zip") if not passed else None)
        finally:
            context.close()


def login(page, password=PASSWORD):
    page.get_by_placeholder("输入登录账号").fill(USERNAME)
    page.get_by_placeholder("输入登录密码").fill(password)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/auth/login") as response:
        page.get_by_role("button", name="登录", exact=True).click()
    return response.value


def pick_source(page, source):
    page.get_by_role("button", name="订阅源", exact=True).tap()
    drawer = page.get_by_role("complementary", name="过滤条件与来源")
    expect(drawer).to_be_visible()
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                              and f"source_id={source}" in response.url) as response:
        drawer.get_by_role("button", name=SOURCE_NAMES[source]).tap()
    assert response.value.status == 200
    data = response.value.json()
    assert data["items"] and all(item["source_id"] == source for item in data["items"]), data
    expect(drawer).to_have_count(0)
    expect(page.locator(".m-title")).to_have_text(SOURCE_NAMES[source])


def scroll_content(page, selector, distance):
    area = page.locator(selector)
    box = area.bounding_box()
    before = area.evaluate("el => el.scrollTop")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, distance)
    page.wait_for_function("([selector, before]) => document.querySelector(selector).scrollTop !== before",
                           arg=[selector, before])


def article_position(page, selector):
    return page.locator(selector).evaluate("""el => {
        const top = el.getBoundingClientRect().top;
        const heading = [...el.querySelectorAll('h2')].find(h => h.getBoundingClientRect().top >= top);
        return {progress: el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight), heading: heading?.textContent};
    }""")


def run_flows(browser, base_url, database, artifacts, result):
    checks = result.setdefault("checks", [])
    with observed_page(browser, base_url, artifacts, "reader", result) as (page, audit):
        page.goto(base_url)
        audit["reject_login"] = True
        assert login(page, PASSWORD + "-wrong").status == 401
        expect(page.locator(".auth-error")).to_be_visible()
        expect(page.get_by_placeholder("输入登录账号")).to_be_visible()
        audit["reject_login"] = False
        assert login(page).status == 200
        expect(page.locator(".m-tabbar")).to_be_visible(timeout=30000)
        expect(page.locator(".app-arrive")).to_have_count(0, timeout=15000)
        expect(page.locator(".reader-entry")).to_have_count(30)
        assert_viewport(page)
        checks.append("real login: wrong password rejected, correct password opens the mobile reader")

        pick_source(page, SOURCE_B)
        expect(page.locator(".reader-entry-title")).to_have_count(3)
        assert all(title.startswith("另一来源") for title in page.locator(".reader-entry-title").all_text_contents())
        pick_source(page, SOURCE_A)
        expect(page.locator(".reader-entry")).to_have_count(30)
        page.set_viewport_size({"width": 320, "height": 568})
        assert_viewport(page)
        page.set_viewport_size({"width": 390, "height": 844})
        assert_viewport(page)
        checks.append("source selection closes the drawer and changes real API results and visible content")

        scroll_content(page, ".m-list", 650)
        assert_viewport(page)
        title = page.locator(".m-list").evaluate("""el => {
            const box = el.getBoundingClientRect();
            return [...el.querySelectorAll('.reader-entry')].find(row => {
                const r = row.getBoundingClientRect(); return r.top >= box.top && r.bottom <= box.bottom;
            }).querySelector('.reader-entry-title').textContent;
        }""")
        before_open = page.locator(".m-list").evaluate("el => el.scrollTop")
        page.screenshot(path=str(artifacts / "mobile-list.png"), animations="disabled")
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and "/reader/articles/" in response.url and response.url.endswith("/read")) as read:
            page.get_by_text(title, exact=True).tap()
        assert read.value.status == 200
        article_id = urlsplit(read.value.url).path.split("/")[-2]
        expect(page.locator(".m-read-scroll h1")).to_have_text(title)
        expect(page.locator(".m-read-scroll")).to_contain_text("阅读章节 24")
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and response.url.endswith(f"/reader/favorites/{article_id}")) as favorite:
            page.get_by_role("region", name="正文", exact=True).get_by_role("button", name="收藏", exact=True).tap()
        assert favorite.value.status == 200
        expect(page.get_by_role("region", name="正文", exact=True).get_by_role("button", name="取消收藏", exact=True)).to_be_visible()
        page.go_back()
        expect(page.locator(".m-read-scroll")).to_have_count(0)
        page.wait_for_function("expected => Math.abs(document.querySelector('.m-list').scrollTop - expected) <= 5", arg=before_open)
        assert_viewport(page)
        checks.append("browser Back restores the original list offset after reading")
        page.get_by_text(title, exact=True).tap()
        expect(page.locator(".m-read-scroll")).to_contain_text("阅读章节 24")
        scroll_content(page, ".m-read-scroll", 2400)
        page.wait_for_function("document.querySelector('.m-read-scroll').scrollTop >= 2300")
        position = article_position(page, ".m-read-scroll")
        result["reading_positions"] = [{"width": 390, **position}]
        page.screenshot(path=str(artifacts / "mobile-article.png"), animations="disabled")
        calls = audit["requests"].copy()
        history_length = page.evaluate("history.length")
        for width in (1365, 390, 1025, 1024):
            page.set_viewport_size({"width": width, "height": 844})
            selector = ".reader-pane" if width > 1024 else ".m-read-scroll"
            expect(page.locator(f"{selector} h1")).to_have_text(title)
            page.wait_for_function("""([selector, progress]) => {
                const el = document.querySelector(selector);
                return el && Math.abs(el.scrollTop/(el.scrollHeight-el.clientHeight)-progress) < 0.04;
            }""", arg=[selector, position["progress"]])
            current = article_position(page, selector)
            result["reading_positions"].append({"width": width, **current})
            assert abs(int(current["heading"].split()[-1]) - int(position["heading"].split()[-1])) <= 1, (position, current)
            if width == 1365:
                page.screenshot(path=str(artifacts / "desktop-reader.png"), animations="disabled")
            if width > 1024:
                page.wait_for_function("history.state?.mLayer == null")
                assert len(page.locator(".reader-shell").evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ')")) == 4
            assert page.evaluate("history.length") == history_length
        assert audit["requests"][("GET", "/api/reader/sources")] == calls[("GET", "/api/reader/sources")]
        assert audit["requests"][("GET", f"/api/articles/{article_id}")] == calls[("GET", f"/api/articles/{article_id}")]
        checks.append("same article, nearby reading section and cached data across layout changes; no empty history steps")
        page.set_viewport_size({"width": 390, "height": 844})
        page.go_back()
        expect(page.locator(".m-read-scroll")).to_have_count(0)
        # Width changes reflow row heights: keep the article reachable, not an identical pixel offset.
        expect(page.get_by_text(title, exact=True)).to_be_in_viewport(ratio=1)
        assert_viewport(page)
        checks.append("after resizing, Back returns to the same article nearby with reachable bottom navigation")

        scroll_content(page, ".m-list", 10000)
        expect(page.locator(".reader-entry")).to_have_count(ARTICLE_COUNT)
        assert_viewport(page)
        checks.append("real server pagination loads the next page without scrolling the document")

        page.get_by_role("button", name="动态", exact=True).tap()
        expect(page.get_by_text("暂无动态", exact=True)).to_be_visible()
        expect(page.locator(".reader-entry")).to_have_count(0)
        for width, height in ((320, 568), (900, 700), (844, 390), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            assert_viewport(page)
        checks.append("empty container, narrow/medium widths and landscape keep the viewport contained")

        # Do not mistake an earlier successful action's toast for network-error feedback.
        if page.get_by_role("button", name="关闭提示", exact=True).count():
            page.get_by_role("button", name="关闭提示", exact=True).tap()
        expect(page.locator(".toast-pop")).to_have_count(0)
        audit["offline"] = True
        page.context.set_offline(True)
        try:
            with page.expect_event("requestfailed", predicate=lambda request: urlsplit(request.url).path == "/api/articles"):
                page.get_by_role("button", name="文章", exact=True).tap()
            expect(page.locator(".toast-pop")).to_contain_text(re.compile(r"fetch|fail|network|失败|网络", re.I))
            expect(page.locator(".reader-entry")).to_have_count(0)
            assert_viewport(page)
            page.screenshot(path=str(artifacts / "mobile-offline.png"), animations="disabled")
        finally:
            page.context.set_offline(False)
            audit["offline"] = False
        pick_source(page, SOURCE_A)
        expect(page.locator(".reader-entry")).to_have_count(30)
        checks.append("offline request failure shows feedback without stale rows; switching source recovers")

        page.get_by_role("button", name="我的", exact=True).tap()
        expect(page.get_by_role("region", name="我的", exact=True)).to_be_visible()
        page.get_by_role("button", name="设置", exact=False).tap()
        expect(page.get_by_role("dialog", name="设置", exact=True)).to_be_visible()
        page.go_back()
        expect(page.get_by_role("dialog", name="设置", exact=True)).to_have_count(0)
        page.get_by_role("button", name="文章", exact=True).tap()
        expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "文章")
        page.reload()
        expect(page.locator(".reader-entry")).to_have_count(30)
        page.get_by_text(title, exact=True).tap()
        expect(page.get_by_role("region", name="正文", exact=True).get_by_role("button", name="取消收藏", exact=True)).to_be_visible()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            assert db.execute("SELECT is_read FROM reader_article_read_states WHERE owner_username=? AND article_id=?",
                              (USERNAME, article_id)).fetchone() == (1,)
            assert db.execute("SELECT COUNT(*) FROM reader_favorites WHERE owner_username=? AND article_id=?",
                              (USERNAME, article_id)).fetchone() == (1,)
        checks.append("settings Back and reload work; read state and favorite persist in SQLite")
        page.get_by_role("button", name="返回列表", exact=True).tap()
        expect(page.locator(".m-read-scroll")).to_have_count(0)
        assert_viewport(page)
        # Negative control: prove the layout assertion rejects an actual off-screen footer.
        page.locator(".m-shell").evaluate("el => el.style.height = 'calc(100dvh + 96px)'")
        try:
            try:
                assert_viewport(page)
            except AssertionError:
                result["negative_control"] = "rejected: footer deliberately moved 96px below viewport"
            else:
                raise RuntimeError("layout assertion accepted the deliberately broken footer")
        finally:
            page.locator(".m-shell").evaluate("el => el.style.removeProperty('height')")
        assert_viewport(page)
        page.screenshot(path=str(artifacts / "mobile-final.png"), animations="disabled")

    with observed_page(browser, base_url, artifacts, "deep-link", result) as (page, _audit):
        page.goto(base_url)
        expect(page.get_by_placeholder("输入登录账号")).to_be_visible()
        # #2 regression: hash-only navigation while already waiting at the login gate.
        page.goto(f"{base_url}/#/reader/a/{article_id}")
        assert login(page).status == 200
        expect(page.locator(".m-read-scroll h1")).to_have_text(title, timeout=30000)
        expect(page.locator(".app-arrive")).to_have_count(0, timeout=15000)
        page.go_back()
        expect(page.locator(".m-read-scroll")).to_have_count(0)
        assert_viewport(page)
        checks.append("a deep link received at the login gate opens the target article after real login")
