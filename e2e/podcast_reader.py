"""#245: real reader UI, real API and published synthetic text/audio; no response mocks."""
import json
import re
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

from e2e.podcast_fixture import SOURCE, STATES
from e2e.reader_fixture import USERNAME, PASSWORD


def run_podcast_flows(browser, base_url, database, artifacts, result):
    cases = json.loads(database.with_name("podcast-cases.json").read_text())
    checks = result.setdefault("podcast_checks", [])
    matrix = [(1280, "light", "user"), (1280, "dark", "guest"),
              (360, "light", "user"), (390, "dark", "user"), (430, "light", "guest")]
    for width, theme, role in matrix:
        mobile = width < 1025
        name = f"podcast-{width}-{theme}-{role}"
        context = browser.new_context(viewport={"width": width, "height": 844},
                                      is_mobile=mobile, has_touch=mobile, service_workers="block")
        context.set_default_timeout(12000)
        context.add_init_script(f"localStorage.setItem('dorami-theme', '{theme}'); localStorage.setItem('dorami-motion', 'reduce')")
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        row = {"viewport": width, "theme": theme, "role": role, "status": "failed",
               "page_errors": [], "api_errors": [], "external_requests": [], "list_requests": []}
        checks.append(row)
        page.on("pageerror", lambda error: row["page_errors"].append(str(error)))

        def observe(response):
            path = urlsplit(response.url).path
            if path.startswith("/api/") and response.status >= 400:
                row["api_errors"].append({"path": path, "status": response.status})
            if path == "/api/articles" and response.status == 200:
                row["list_requests"].append(parse_qs(urlsplit(response.url).query))

        page.on("response", observe)

        def local_only(route):
            url = urlsplit(route.request.url)
            if route.request.resource_type == "image" and url.hostname == "www.google.com" and url.path == "/s2/favicons":
                # Guest defaults contain remote favicons; keep this test entirely local.
                route.fulfill(status=200, content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>')
                return
            if urlsplit(route.request.url).netloc != urlsplit(base_url).netloc:
                row["external_requests"].append(route.request.url)
                route.abort()
            elif route.request.method == "POST" and "/ai/" in urlsplit(route.request.url).path:
                raise AssertionError("this regression suite must not request paid generation")
            else:
                route.continue_()

        context.route("**/*", local_only)
        try:
            if role == "user":
                response = context.request.post(base_url + "/api/auth/login", data={"username": USERNAME, "password": PASSWORD})
                assert response.status == 200
            page.goto(base_url + f"/#/reader/a/{SOURCE}-both-1")
            card = page.locator(".reader-ai-summary:visible").first
            expect(card.locator(".reader-ai-card-head > .reader-ai-layer-title")).to_have_text("AI 速读")
            expect(page.get_by_role("button", name="导读稿", exact=True)).to_be_visible()
            expect(page.get_by_text(f"中文导读测试正文 {SOURCE}-both-1", exact=True)).to_be_visible()
            page.get_by_role("button", name="节目简介", exact=True).click()
            expect(page.get_by_text("节目简介 both-1", exact=True)).to_be_visible()
            expect(card).to_be_visible()
            page.get_by_role("button", name="导读稿", exact=True).click()
            expect(page.get_by_text(f"中文导读测试正文 {SOURCE}-both-1", exact=True)).to_be_visible()
            runtime = context.request.get(base_url + "/api/runtime").json()
            assert runtime["account_role"] == role
            if mobile:
                expect(card.locator(".reader-ai-reason-inline")).to_contain_text("基于整期内容")
            else:
                score = card.locator(".reader-ai-score-btn")
                score.focus()
                expect(card.locator(".reader-ai-score-tip .reader-ai-layer-note")).to_be_visible()
                expect(card.locator(".reader-ai-score-tip")).to_contain_text("基于整期内容")
                score.press("Escape")
                expect(card.locator(".reader-ai-score-tip")).to_be_hidden()
                score.blur()
            expect(card).not_to_contain_text(re.compile("简介导读|全文导读|全文深度分析|简介初评"))
            page.screenshot(path=str(artifacts / f"{name}-detail.png"))
            # Play an actual same-origin published synthetic WAV; no TTS or external audio.
            page.locator(".listen-entries:visible").get_by_role("button", name=re.compile("收听精品导读")).click()
            page.wait_for_function("[...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0)")
            page.get_by_role("button", name=re.compile("^暂停")).click()
            row["published_audio_played"] = True
            if mobile:
                page.get_by_role("button", name="返回列表", exact=True).click()

            entries = page.locator(".reader-entry:visible")
            expect(entries).to_have_count(30)
            for state, readable, listenable, status in STATES:
                for premium in (False, True):
                    title = f"导读样例 {state}-{int(premium)}"
                    entry = entries.filter(has=page.get_by_text(title, exact=True))
                    expect(entry).to_have_count(1)
                    expect(entry.locator(".podcast-premium-badge")).to_have_count(int(premium))
                    available = entry.locator(".reader-podcast-availability")
                    label = "有声导读" if listenable else "中文导读" if readable else "导读生成中…" if status in {"queued", "retry_wait"} else ""
                    expect(available).to_have_count(int(bool(label)))
                    if label:
                        expect(available).to_contain_text(label)
                        expect(available.locator(".lucide-book-open-text")).to_have_count(int(readable))
                        expect(available.locator(".lucide-headphones")).to_have_count(int(listenable))
                    expect(entry).not_to_contain_text(re.compile("暂无中文导读|仅提供原节目|internal-secret-error|简介初评"))
            row["label_combinations"] = len(STATES) * 2
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "horizontal overflow"

            # Real search controls drive server-side filtering; not a first-page client filter.
            page.get_by_role("button", name="搜索", exact=True).click()
            group = page.get_by_role("group", name="按导读筛选", exact=True)
            expect(group).to_be_visible()
            for value, label in [("any", "有中文导读"), ("listenable", "可听"), ("readable", "可看")]:
                with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                                          and parse_qs(urlsplit(response.url).query).get("podcast_guide") == [value]) as response:
                    group.get_by_role("button", name=label, exact=True).click()
                payload = response.value.json()
                expected = {aid for aid, item in cases.items() if
                            (item["readable"] or item["listenable"]) and (value == "any" or item[value])}
                assert payload["total"] == len(expected), payload
                expect(entries).to_have_count(30)
                assert {item["id"] for item in payload["items"]} <= expected
                expect(group.get_by_role("button", name=label, exact=True)).to_have_attribute("aria-pressed", "true")
                if width == 1280 and role == "user":
                    entries.last.scroll_into_view_if_needed()
                    expect(entries).to_have_count(len(expected))
                    assert set(entries.locator(".reader-entry-title").all_text_contents()) == {
                        aid.replace(SOURCE + "-", "导读样例 ", 1) for aid in expected
                    }
            page.screenshot(path=str(artifacts / f"{name}-filter.png"))
            search = page.get_by_placeholder("搜索我的阅读…")
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                                      and parse_qs(urlsplit(response.url).query).get("search") == ["both"]):
                search.fill("both")
            expect(entries).to_have_count(2)
            expect(entries.first).to_contain_text("both")
            # Empty state keeps its filter controls and a route back to all results.
            search.fill("missing-episode-zzzz")
            expect(page.get_by_text("没有匹配的播客", exact=True)).to_be_visible()
            expect(group).to_be_visible()
            # Close during debounce; no stale guide or keyword request may revive.
            search.fill("late-keyword")
            page.get_by_role("button", name="取消" if mobile else "关闭搜索", exact=True).click()
            expect(group).to_have_count(0)
            expect(entries).to_have_count(30)
            page.wait_for_timeout(400)  # measured >300ms debounce window, not a page-ready sleep
            assert "podcast_guide" not in row["list_requests"][-1] and "search" not in row["list_requests"][-1]
            page.get_by_role("button", name="搜索", exact=True).click()
            expect(group.get_by_role("button", name="全部", exact=True)).to_have_attribute("aria-pressed", "true")
            row["keyword_paging_and_clear"] = True
            group.get_by_role("button", name="可听", exact=True).click()
            if mobile:
                page.get_by_role("button", name="取消", exact=True).click()
                page.get_by_role("tab", name="文章", exact=True).click()
            else:
                # Aggregate navigation clears the source-bound filter; choosing a source clears it again.
                page.locator(".reader-vrail").get_by_role("button", name="播客", exact=True).click()
                page.get_by_role("button", name="搜索", exact=True).click()
                group.get_by_role("button", name="可看", exact=True).click()
                page.locator(".reader-source-row").filter(has_text="播客导读测试").first.click()
                expect(group.get_by_role("button", name="全部", exact=True)).to_have_attribute("aria-pressed", "true")
                group.get_by_role("button", name="可听", exact=True).click()
                page.locator(".reader-vrail").get_by_role("button", name="文章", exact=True).click()
            expect(group).to_have_count(0)
            page.get_by_role("button", name="搜索", exact=True).click()
            expect(page.get_by_placeholder("搜索我的阅读…")).to_be_visible()
            expect(group).to_have_count(0)
            page.get_by_role("button", name="取消" if mobile else "关闭搜索", exact=True).click()
            if mobile:
                page.get_by_role("tab", name="播客", exact=True).click()
            else:
                page.locator(".reader-vrail").get_by_role("button", name="播客", exact=True).click()
            page.get_by_role("button", name="搜索", exact=True).click()
            expect(group.get_by_role("button", name="全部", exact=True)).to_have_attribute("aria-pressed", "true")
            row["scope_reset"] = True
            assert not row["page_errors"], row["page_errors"]
            assert not row["api_errors"], row["api_errors"]
            assert not row["external_requests"], row["external_requests"]
            row["status"] = "passed"
        except Exception:
            page.screenshot(path=str(artifacts / f"{name}-failure.png"))
            (artifacts / f"{name}-failure.txt").write_text(page.locator("body").inner_text())
            raise
        finally:
            context.tracing.stop(path=str(artifacts / f"{name}-trace.zip") if row["status"] != "passed" else None)
            context.close()
