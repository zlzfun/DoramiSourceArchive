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
            // issue #230:每个 Tab 只有一行 52px 页头(拍板 31 B)
            heads: [...document.querySelectorAll('.m-content .m-head')].map(head => head.getBoundingClientRect().height),
            targets: [...document.querySelectorAll('.m-tabbar .m-tab')].map(button => {
                const box = button.getBoundingClientRect();
                return {name: button.getAttribute('aria-label'),
                    reachable: button.contains(document.elementFromPoint(box.x + box.width/2, box.y + box.height/2))};
            })
        };
    }""")


def reveal_chrome(page):
    """底栏随滚动退场(issue #230 样页 11):往下读越过 140px 底栏收起,往上一划回来。"""
    if page.locator(".m-shell[data-receded]").count():
        page.mouse.wheel(0, -40)
        expect(page.locator(".m-shell[data-receded]")).to_have_count(0)
    # 等底栏的回位过渡跑完再量(退场 / 回来都是 transform 弹簧);只等动画结束、不预设位置,几何仍由下面的断言判
    page.wait_for_function("() => document.querySelector('.m-tabbar').getAnimations().length === 0")


def assert_viewport(page):
    reveal_chrome(page)
    expect(page.locator(".m-tabbar")).to_be_visible()
    metrics = viewport_metrics(page)
    # 底栏是浮动胶囊(issue #230):离可见区底边 8px(测试视口无安全区)
    assert abs(metrics["visibleBottom"] - metrics["bar"]["bottom"] - 8) <= 1, metrics
    assert 0 <= metrics["bar"]["left"] < metrics["bar"]["right"] <= metrics["client"][0] + 1, metrics
    assert all(scroll <= client + 1 for scroll, client in zip(metrics["root"], metrics["client"])), metrics
    assert metrics["scroll"] == [0, 0], metrics
    assert all(height == 52 for height in metrics["heads"]), metrics
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
    # issue #230 批 3a:来源列在底栏「订阅」Tab;点一行在订阅 Tab 里推入它自己的条目流(第二个列表实例,
    # 盖住底栏、不动「内容」Tab);留在推入页,由调用方 leave_source 出栈
    page.locator(".m-tabbar .m-tab", has_text="订阅").tap()
    rows = page.locator(".m-subs-scroll .m-src-row", has_text=SOURCE_NAMES[source])
    expect(rows).to_have_count(1)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                              and f"source_id={source}" in response.url) as response:
        rows.tap()
    assert response.value.status == 200
    data = response.value.json()
    assert data["items"] and all(item["source_id"] == source for item in data["items"]), data
    flow = page.get_by_role("dialog", name=SOURCE_NAMES[source], exact=True)
    expect(flow).to_be_visible()
    expect(flow.locator(".m-flow-title")).to_have_text(SOURCE_NAMES[source])
    expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "订阅")
    return flow


def leave_source(page):
    """系统返回出栈:推入页关掉,回到订阅 Tab 根页(底栏重新可达)。"""
    page.go_back()
    expect(page.locator(".m-flow")).to_have_count(0)
    expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "订阅")


def open_content(page):
    page.locator(".m-tabbar .m-tab", has_text="内容").tap()
    expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "内容")


def scroll_content(page, selector, distance):
    area = page.locator(selector)
    # issue #230 批 3c: the article page enters by expanding a clip-path from the tapped card (420ms); until it settles
    # a wheel at the page centre can land on the still-uncovered (inert) list. Wait for the read layer to finish moving.
    # (Script-driven Animation objects only: the reading-progress line is a CSS scroll-timeline animation that always runs.)
    page.wait_for_function("""() => !document.getAnimations().some(a => a.constructor === Animation && a.playState === 'running'
        && a.effect && a.effect.target && a.effect.target.matches && a.effect.target.matches('.m-read, .m-read-scroll'))""")
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

        first_content_title = page.locator(".m-list .reader-entry-title").first.text_content()
        flow = pick_source(page, SOURCE_B)
        expect(flow.locator(".reader-entry-title")).to_have_count(3)
        assert all(title.startswith("另一来源") for title in flow.locator(".reader-entry-title").all_text_contents())
        leave_source(page)
        flow = pick_source(page, SOURCE_A)
        expect(flow.locator(".reader-entry")).to_have_count(30)
        leave_source(page)
        page.set_viewport_size({"width": 320, "height": 568})
        assert_viewport(page)
        page.set_viewport_size({"width": 390, "height": 844})
        assert_viewport(page)
        # 内容 Tab 仍是订阅聚合(两个来源),没有被推入页改写
        open_content(page)
        expect(page.locator(".m-list .reader-entry")).to_have_count(30)
        expect(page.locator(".m-list .reader-entry-title").first).to_have_text(first_content_title)
        expect(page.locator(".m-scope-chip")).to_have_count(0)
        checks.append("sources in the subscriptions tab push their own real-API list; the content tab is unchanged")

        scroll_content(page, ".m-list", 650)
        expect(page.locator(".m-shell[data-receded]")).to_have_count(1)   # 往下读:底栏退场
        assert_viewport(page)
        # 列表从浮动页头与底栏下面滚过(issue #230):只挑页头下沿与底栏上沿之间完整露出的一条
        title = page.locator(".m-list").evaluate("""el => {
            const box = el.getBoundingClientRect();
            const top = document.querySelector('.m-content .m-head').getBoundingClientRect().bottom;
            const bottom = document.querySelector('.m-tabbar').getBoundingClientRect().top;
            return [...el.querySelectorAll('.reader-entry')].find(row => {
                const r = row.getBoundingClientRect(); return r.top >= Math.max(box.top, top) && r.bottom <= Math.min(box.bottom, bottom);
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

        # 内容 Tab = 两个来源的订阅聚合(甲 60 + 乙 3):三页,滚到底两次
        scroll_content(page, ".m-list", 10000)
        page.wait_for_function("n => document.querySelectorAll('.m-list .reader-entry').length >= n", arg=ARTICLE_COUNT)
        if page.locator(".reader-entry").count() < ARTICLE_COUNT + 3:
            scroll_content(page, ".m-list", 10000)
        expect(page.locator(".reader-entry")).to_have_count(ARTICLE_COUNT + 3)
        assert_viewport(page)
        checks.append("real server pagination loads the next page without scrolling the document")

        # issue #178 撤销「动态」容器后,用无订阅内容的播客容器覆盖空容器的视口约束
        page.get_by_role("tab", name="播客", exact=True).tap()
        expect(page.get_by_text("暂无播客", exact=True)).to_be_visible()
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
                page.get_by_role("tab", name="文章", exact=True).tap()
            expect(page.locator(".toast-pop")).to_contain_text(re.compile(r"fetch|fail|network|失败|网络", re.I))
            expect(page.locator(".reader-entry")).to_have_count(0)
            assert_viewport(page)
            page.screenshot(path=str(artifacts / "mobile-offline.png"), animations="disabled")
        finally:
            page.context.set_offline(False)
            audit["offline"] = False
        flow = pick_source(page, SOURCE_A)
        expect(flow.locator(".reader-entry")).to_have_count(30)
        leave_source(page)
        # 回内容 Tab 再点一次「内容」= 回当前视图聚合并重拉:失败后的空列表恢复
        open_content(page)
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                                  and response.status == 200):
            page.locator(".m-tabbar .m-tab", has_text="内容").tap()
        expect(page.locator(".m-list .reader-entry")).to_have_count(30)
        checks.append("offline request failure shows feedback without stale rows; a source page and the content tab both recover")

        # issue #230:「我的」Tab 退役,设置从页头头像推入(盖住底栏);手机设置不列 OPML 与接入集成
        avatar = page.get_by_role("button", name=re.compile("打开设置$"))
        avatar_box = avatar.bounding_box()
        avatar.tap()
        settings = page.get_by_role("dialog", name="设置", exact=True)
        expect(settings).to_be_visible()
        expect(settings).not_to_contain_text("OPML")
        expect(settings).not_to_contain_text("接入集成")
        # 返回钮落在头像原位(拍板 27);设置页从右推入,等推入动画落定再比
        page.wait_for_function("""box => { const r = document.querySelector('.m-sett .m-head-lead').getBoundingClientRect();
            return Math.abs(r.x - box.x) < 0.5 && Math.abs(r.y - box.y) < 0.5 && r.width === box.width && r.height === box.height; }""",
                               arg=avatar_box)
        page.go_back()
        expect(page.get_by_role("dialog", name="设置", exact=True)).to_have_count(0)
        expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "内容")
        expect(page.get_by_role("tab", name="文章", exact=True)).to_have_attribute("aria-selected", "true")
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
