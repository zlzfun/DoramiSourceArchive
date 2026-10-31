"""Mobile-shell guards for issue #230 (audit M-18 / M-01 / M-46; Chromium phone emulation, four phone sizes).

Per viewport (360x780 / 375x667 / 390x844 / 430x932, run in parallel, one browser each):

* Back-key matrix — system Back closes only the topmost layer and never leaves the app: brief history sheet,
  player card, long-press action sheet, settings stack (section -> root -> closed), and a stacked
  push page -> article -> "more" sheet unwound one layer per Back.
* Long-press (M-01): the action sheet opened by a held touch is still open after the finger lifts.
* Touch targets: sampled primary controls of each layer have an effective hit area >= 44x44
  (hit-tested with elementFromPoint, so ::after expansions count).
* Inputs: every visible text control in the shell renders at >= 16px (iOS focus zoom).
* No horizontal overflow on any visited page.

Once (390 wide): scrolling the content list to 200+ entries keeps the DOM under 6,000 nodes (M-46), and fresh
launches at 1024 / 1025 land on the phone shell / the desktop four columns.

The fixture is opt-in (``DORAMI_E2E_GUARDS=1``, see ``e2e/reader_fixture.seed_guards``). The personal brief is a
global switch, so this flow turns it on in SQLite for its own duration and restores it afterwards; run it after
the other flows (the runner does). The restored player session points at ``/e2e-silence.wav`` (written next to
the built site by the runner); nothing is played.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from e2e.reader_fixture import GUARD_PASSWORD, GUARD_WIDTHS, SOURCE_A, SOURCE_NAMES, guard_username

VIEWPORTS = tuple(zip(GUARD_WIDTHS, (780, 667, 844, 932)))
MIN_TAP = 43.5          # 44 with sub-pixel tolerance
MIN_INLINE = 31.5       # --tap-inline 32: secondary controls that also have another exit
MIN_INPUT_FONT = 16
DOM_LIMIT = 6000
LONG_LIST = 200
SILENCE = "/e2e-silence.wav"
# 已知未修(审计项归并行的批 4a):照常测量、写进 result.json,但不判失败;条目必须仍在至少一档复现,
# 否则整个流程失败并提示把它删掉(严格 xfail——修好之后守卫自动收紧)。键 = (检查点, 元素名)。
KNOWN_OPEN = {
    ("discover filter", "筛选来源"): "M-06",
    ("account section", "当前密码"): "M-06",
    ("account section", "新密码"): "M-06",
    ("account section", "确认新密码"): "M-06",
    ("feedback section", "反馈内容"): "M-06",
}

PLAYER_SESSION = {
    "item": {"articleId": f"{SOURCE_A}-00", "track": "original", "src": SILENCE,
             "title": "端到端守卫节目", "show": "端到端测试来源甲", "duration": 1800},
    "form": "bar", "rate": 1,
}

# Effective hit area of each matching, visible, non-inert element (bbox centre, scanning outwards px by px).
HITS_JS = """(selector) => {
    const vw = innerWidth, vh = innerHeight;
    const owns = (el, hit) => hit && (hit === el || el.contains(hit));
    const extent = (el, cx, cy, dx, dy) => { let n = 0;
        for (let i = 1; i <= 40; i++) { const x = cx + dx * i, y = cy + dy * i;
            if (x < 0 || y < 0 || x >= vw || y >= vh || !owns(el, document.elementFromPoint(x, y))) break; n = i; }
        return n; };
    return [...document.querySelectorAll(selector)].filter(el => el.getClientRects().length && !el.closest('[inert]'))
        .map(el => { const r = el.getBoundingClientRect();
            const cx = Math.min(Math.max(r.left + r.width / 2, 0), vw - 1), cy = Math.min(Math.max(r.top + r.height / 2, 0), vh - 1);
            const reachable = owns(el, document.elementFromPoint(cx, cy));
            return {name: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24), cls: String(el.className).slice(0, 40),
                w: Math.round(r.width), h: Math.round(r.height), reachable,
                ew: reachable ? extent(el, cx, cy, -1, 0) + extent(el, cx, cy, 1, 0) + 1 : 0,
                eh: reachable ? extent(el, cx, cy, 0, -1) + extent(el, cx, cy, 0, 1) + 1 : 0}; });
}"""

INPUTS_JS = """() => [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]), textarea, select')]
    .filter(el => el.getClientRects().length && !el.closest('[inert]'))
    .map(el => ({name: el.getAttribute('aria-label') || el.placeholder || el.name || el.type, size: parseFloat(getComputedStyle(el).fontSize)}))"""

OVERFLOW_JS = """() => { const d = document.documentElement;
    return {doc: [d.scrollWidth, d.clientWidth], shell: [...document.querySelectorAll('.m-shell, .m-list, .m-read-scroll, .m-flow, .m-sett')]
        .filter(el => el.getClientRects().length).map(el => [String(el.className).split(' ')[0], el.scrollWidth, el.clientWidth])}; }"""


class Guard:
    def __init__(self, page, cdp, width, checks, known):
        self.page, self.cdp, self.width, self.checks, self.known = page, cdp, width, checks, known

    def note(self, text):
        self.checks.append(f"{self.width}: {text}")

    def alive(self):
        # Back never pops past the app: the document (and the marker set after the first load) survives.
        assert urlsplit(self.page.url).path in ("", "/"), self.page.url
        assert self.page.evaluate("window.__guardAlive === true"), "Back left the app document"

    def back(self):
        self.page.go_back()
        self.alive()

    def offenders(self, label, items):
        # Known-open audit items are recorded (not failed); anything else fails the viewport.
        unknown = []
        for item in items:
            if (label, item["name"]) in KNOWN_OPEN:
                self.known.append({"check": label, **item, "audit": KNOWN_OPEN[(label, item["name"])]})
            else:
                unknown.append(item)
        assert not unknown, (self.width, label, unknown)

    def taps(self, selector, label, *, minimum=MIN_TAP):
        hits = self.page.evaluate(HITS_JS, selector)
        assert hits, (self.width, label, f"no visible element for {selector}")
        self.offenders(label, [hit for hit in hits if not hit["reachable"] or hit["ew"] < minimum or hit["eh"] < minimum])
        return len(hits)

    def inputs(self, label):
        fonts = self.page.evaluate(INPUTS_JS)
        self.offenders(label, [item for item in fonts if item["size"] < MIN_INPUT_FONT])
        return len(fonts)

    def no_overflow(self, label):
        data = self.page.evaluate(OVERFLOW_JS)
        bad = [row for row in data["shell"] if row[1] > row[2] + 1]
        assert data["doc"][0] <= data["doc"][1] + 1 and not bad, (self.width, label, data)

    def long_press(self, locator, hold=750):
        box = locator.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 40)
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
        self.page.wait_for_timeout(hold)
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def settle(self):
        # Layer entrances (sheet / push page / card / settings slide) are CSS or WAAPI animations on the document
        # timeline; wait until the finite ones finish before measuring geometry. Infinite ones (spinners, skeleton
        # shimmer) and scroll-driven ones (reading progress) never finish and are ignored.
        self.page.wait_for_function("""() => !document.getAnimations().some(a => a.playState === 'running'
            && a.timeline === document.timeline && a.effect && a.effect.getComputedTiming().endTime !== Infinity)""")
        self.page.wait_for_timeout(80)


def sheet(page):
    return page.locator(".m-sheet[role=dialog]")


def run_viewport(launch, base_url, artifacts, width, height, out):
    checks, known, errors = [], [], []
    username = guard_username(width)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True, locale="zh-CN")
        context.set_default_timeout(12000)
        context.add_init_script(
            "try { if (!sessionStorage.getItem('dorami-player-session-v1')) sessionStorage.setItem("
            f"'dorami-player-session-v1', {json.dumps(json.dumps({'user': username, **PLAYER_SESSION}, ensure_ascii=False))}); }}"
            " catch (e) {}")
        response = context.request.post(f"{base_url}/api/auth/login",
                                        data={"username": username, "password": GUARD_PASSWORD})
        assert response.ok, response.status
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text)
                if message.type == "error" and "Failed to load resource" not in message.text else None)
        page.on("response", lambda resp: errors.append(f"HTTP {resp.status} {urlsplit(resp.url).path}")
                if resp.status >= 400 and urlsplit(resp.url).path.startswith("/api/") else None)
        cdp = context.new_cdp_session(page)
        g = Guard(page, cdp, width, checks, known)
        started = time.monotonic()
        try:
            page.goto(base_url)
            expect(page.locator(".m-shell")).to_be_visible(timeout=30000)
            expect(page.locator(".app-arrive")).to_have_count(0, timeout=15000)
            page.evaluate("window.__guardAlive = true")

            # ── 早报 Tab(落地页)+ 往期底页 ──
            expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "早报")
            expect(page.locator(".bp-item-open").first).to_be_visible()
            g.settle()
            g.taps(".m-tabbar .m-tab", "tab bar")
            g.taps(".m-content .m-head button", "brief header")
            g.no_overflow("brief")
            page.locator(".m-hist").tap()
            history = sheet(page)
            expect(history).to_be_visible()
            g.settle()
            g.taps(".m-sheet .bp-cal-day:not([disabled]), .m-sheet .m-hist-navbtn:not([disabled])", "history sheet")
            # 往期底页的抓手被翻月钮压住一部分(批 3b 有意:翻月钮叠在抓手之上),有效命中只按行内次要 32 守
            g.taps(".m-sheet .m-sheet-grab", "history sheet grab", minimum=MIN_INLINE)
            g.back()
            expect(history).to_have_count(0)
            expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "早报")
            g.note("brief history sheet: Back closes only the sheet")

            # ── 播放卡(会话恢复的坞 → 卡)──
            dock = page.get_by_role("button", name=re.compile("^展开播放器"))
            expect(dock).to_be_visible()
            g.taps(".player-dock button, [aria-label^='展开播放器']", "player dock")
            dock.tap()
            card = page.get_by_role("dialog", name="播放器", exact=True)
            expect(card).to_be_visible()
            g.settle()
            g.taps(".player-card button", "player card")
            g.back()
            expect(card).to_have_count(0)
            expect(dock).to_be_visible()
            g.note("player card: Back collapses the card, the dock stays")

            # ── 内容 Tab:长按动作单(M-01)──
            page.locator(".m-tabbar .m-tab", has_text="内容").tap()
            entries = page.locator(".m-list .reader-entry")
            expect(entries.first).to_be_visible()
            g.settle()
            g.taps(".m-content .m-head button", "content header")
            g.no_overflow("content")
            g.long_press(entries.nth(1))
            action = sheet(page)
            expect(action).to_be_visible()
            page.wait_for_timeout(700)   # the synthetic click after lifting the finger must not close it
            expect(action).to_be_visible()
            assert page.locator(".m-sheet-layer.is-closing, .m-sheet.is-closing").count() == 0
            g.settle()
            g.taps(".m-sheet [role=menuitem], .m-sheet .m-sheet-grab", "action sheet")
            g.note("long-press: the action sheet stays open after the finger lifts")
            g.back()
            expect(action).to_have_count(0)
            expect(entries.first).to_be_visible()
            g.note("action sheet: Back closes only the sheet")

            # ── 搜索输入 ≥ 16px ──
            page.get_by_role("button", name="搜索", exact=True).first.tap()
            expect(page.locator(".m-search-field input")).to_be_visible()
            g.inputs("content search")
            g.back()
            expect(page.locator(".m-search-field input")).to_have_count(0)

            # ── 订阅 Tab → 推入页 → 正文 → ⋯:系统返回逐层出栈 ──
            page.locator(".m-tabbar .m-tab", has_text="订阅").tap()
            row = page.locator(".m-subs-scroll .m-src-row", has_text=SOURCE_NAMES[SOURCE_A])
            expect(row).to_have_count(1)
            g.taps(".m-subs-scroll .m-src-row", "subscription rows")
            g.no_overflow("subs")
            row.tap()
            flow = page.get_by_role("dialog", name=SOURCE_NAMES[SOURCE_A], exact=True)
            expect(flow).to_be_visible()
            expect(flow.locator(".reader-entry").first).to_be_visible()
            g.settle()
            g.taps(".m-flow .m-head button", "push page header")
            flow.locator(".reader-entry").first.tap()
            article = page.get_by_role("region", name="正文", exact=True)
            expect(article.locator("h1")).to_be_visible()
            g.settle()
            g.taps(".m-read .m-head button, .m-read .m-head-lead", "article top bar")
            g.no_overflow("article")
            page.get_by_role("button", name="更多操作", exact=True).tap()
            more = sheet(page)
            expect(more).to_be_visible()
            g.settle()
            g.taps(".m-sheet [role=menuitem], .m-sheet .m-sheet-grab", "article more sheet")
            g.back()
            expect(more).to_have_count(0)
            expect(article.locator("h1")).to_be_visible()
            g.back()
            expect(page.locator(".m-read")).to_have_count(0)
            expect(flow).to_be_visible()
            g.back()
            expect(flow).to_have_count(0)
            expect(page.locator('.m-tab[aria-current="page"]')).to_have_attribute("aria-label", "订阅")
            g.note("push page -> article -> more sheet: each Back closes one layer")

            # ── 发现页 / 兴趣标签搜索输入 ──
            page.get_by_text("发现更多", exact=False).first.tap()
            expect(page.locator(".reader-disc-search input")).to_be_visible()
            g.settle()
            g.inputs("discover filter")
            g.no_overflow("discover")
            g.back()
            expect(page.locator(".reader-disc-search input")).to_have_count(0)
            page.get_by_role("tab", name="兴趣", exact=True).tap()
            expect(page.get_by_placeholder("搜索标签")).to_be_visible()
            g.inputs("interest tag search")

            # ── 设置栈:分区 → 设置根 → 关闭 ──
            page.locator(".m-me-btn").first.tap()
            settings = page.get_by_role("dialog", name="设置", exact=True)
            expect(settings).to_be_visible()
            g.settle()
            g.taps(".m-sett .m-row, .m-sett .m-head-lead", "settings rows")
            settings.locator(".m-row", has_text="账户").first.tap()
            expect(page.locator(".m-sett input[type=password]").first).to_be_visible()
            g.settle()
            g.inputs("account section")
            g.no_overflow("settings account")
            g.back()
            expect(settings.locator(".m-row", has_text="账户").first).to_be_visible()
            settings.locator(".m-row", has_text="反馈").first.tap()
            expect(page.locator(".m-sett textarea")).to_be_visible()
            g.settle()
            g.inputs("feedback section")
            g.back()
            expect(settings).to_be_visible()
            g.back()
            expect(settings).to_have_count(0)
            g.note("settings stack: Back returns section -> settings root -> closed")
            g.alive()

            if width == 390:
                negative_controls(page, g, out)
                long_list_and_breakpoints(page, base_url, context, g, out)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(artifacts / f"guards-{width}-failure.png"))
            raise
        finally:
            out[width] = {"checks": checks, "known_open": known, "errors": errors,
                          "seconds": round(time.monotonic() - started, 1)}
            page.screenshot(path=str(artifacts / f"guards-{width}.png"), animations="disabled")
            context.close()
            browser.close()


def negative_controls(page, g, out):
    """Prove the measurements bite: a half-size tab and a 13px search field must both be rejected."""
    rejected = []
    page.locator(".m-tabbar .m-tab", has_text="内容").tap()
    expect(page.locator(".m-list .reader-entry").first).to_be_visible()
    tab = page.locator(".m-tabbar .m-tab").first
    tab.evaluate("el => el.style.transform = 'scale(0.5)'")
    try:
        g.taps(".m-tabbar .m-tab", "negative control: shrunken tab")
    except AssertionError:
        rejected.append("tap target")
    finally:
        tab.evaluate("el => el.style.removeProperty('transform')")
    page.get_by_role("button", name="搜索", exact=True).first.tap()
    field = page.locator(".m-search-field input")
    field.evaluate("el => el.style.fontSize = '13px'")
    try:
        g.inputs("negative control: 13px search")
    except AssertionError:
        rejected.append("input font")
    finally:
        field.evaluate("el => el.style.removeProperty('font-size')")
    g.back()
    assert rejected == ["tap target", "input font"], rejected
    out["negative_controls"] = rejected
    g.note("negative controls: a half-size tab and a 13px input are rejected")


def long_list_and_breakpoints(page, base_url, context, g, out):
    """M-46 DOM-size guard on the real long list, and fresh launches on both sides of the shell breakpoint."""
    page.locator(".m-tabbar .m-tab", has_text="内容").tap()
    listing = page.locator(".m-list")
    expect(listing.locator(".reader-entry").first).to_be_visible()
    for _ in range(20):
        count = listing.locator(".reader-entry").count()
        if count >= LONG_LIST:
            break
        listing.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_function("n => document.querySelectorAll('.m-list .reader-entry').length > n", arg=count)
    count = listing.locator(".reader-entry").count()
    nodes = page.evaluate("document.getElementsByTagName('*').length")
    assert count >= LONG_LIST, count
    assert nodes < DOM_LIMIT, (count, nodes)
    out["long_list"] = {"entries": count, "dom_nodes": nodes}
    g.note(f"long list: {count} entries, {nodes} DOM nodes (< {DOM_LIMIT})")
    for width, shell in ((1024, "phone"), (1025, "desktop")):
        fresh = context.new_page()
        fresh.set_viewport_size({"width": width, "height": 768})
        fresh.goto(base_url)
        if shell == "phone":
            expect(fresh.locator(".m-shell")).to_be_visible(timeout=30000)
            expect(fresh.locator(".reader-vrail")).to_have_count(0)
        else:
            expect(fresh.locator(".reader-vrail")).to_be_visible(timeout=30000)
            expect(fresh.locator(".m-shell")).to_have_count(0)
        fresh.close()
    g.note("fresh launch: 1024 = phone shell, 1025 = desktop shell")


def set_brief(database, enabled):
    with sqlite3.connect(database) as db:
        db.execute("UPDATE app_settings SET value = ? WHERE key = 'personal_digest_enabled'", ("true" if enabled else "false",))


def run_guard_flows(launch, base_url, database, artifacts, result):
    out = {}
    started = time.monotonic()
    set_brief(database, True)
    try:
        with ThreadPoolExecutor(max_workers=len(VIEWPORTS)) as pool:
            futures = [pool.submit(run_viewport, launch, base_url, artifacts, w, h, out) for w, h in VIEWPORTS]
            failures = []
            for (w, h), future in zip(VIEWPORTS, futures):
                try:
                    future.result()
                except Exception as error:  # report every viewport, not just the first failure
                    failures.append(f"{w}x{h}: {type(error).__name__}: {error}")
            if failures:
                raise AssertionError("; ".join(failures))
        seen = {(item["check"], item["name"]) for w, _ in VIEWPORTS for item in out.get(w, {}).get("known_open", [])}
        stale = sorted(set(KNOWN_OPEN) - seen)
        assert not stale, f"KNOWN_OPEN 条目已不再复现(已修好),请从 e2e/mobile_guards.py 删掉:{stale}"
    finally:
        set_brief(database, False)
        result["guards"] = {"viewports": [f"{w}x{h}" for w, h in VIEWPORTS],
                            "seconds": round(time.monotonic() - started, 1),
                            **{str(k): v for k, v in out.items()}}
    result.setdefault("checks", []).extend(check for w, _ in VIEWPORTS for check in out.get(w, {}).get("checks", []))
