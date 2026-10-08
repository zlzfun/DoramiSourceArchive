"""Issue #93: real reader requests, projected tag identity and rapid-clear regression."""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from e2e.mobile_reader import login
from e2e.reader_fixture import FREE_LABEL, TAG_LABEL, TAG_MATCH_IDS


def run_tag_search_flows(browser, base_url, database, artifacts, result):
    cases = result.setdefault("tag_search_checks", [])
    for mobile in [False, True]:
        name = "mobile" if mobile else "desktop"
        context = browser.new_context(viewport={"width": 390 if mobile else 1600, "height": 844 if mobile else 1000},
                                      is_mobile=mobile, has_touch=mobile)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.set_default_timeout(10000)
        successful_lists = []
        errors = []

        def capture(response):
            if urlsplit(response.url).path == "/api/articles" and response.status == 200:
                successful_lists.append({"url": response.url, "query": parse_qs(urlsplit(response.url).query)})

        page.on("response", capture)
        page.on("pageerror", lambda error: errors.append(str(error)))
        passed = False
        try:
            page.goto(base_url)
            login(page)
            expect(page.locator(".reader-entry")).to_have_count(30)
            # Article 01 has a canonical display projection but no assignment.
            first = page.locator(".reader-entry").filter(has=page.get_by_text("连续阅读 01：移动阅读端到端样例", exact=True))
            for label, filter_key in [(TAG_LABEL, "display_tag_id"), (FREE_LABEL, "display_tag")]:
                first.click()
                chip = page.get_by_role("button", name=f"检索「{label}」", exact=True)
                expect(chip).to_be_visible()
                tags = page.locator(".reader-pane-tags .reader-tag-chip")
                expect(tags).to_have_text([TAG_LABEL, FREE_LABEL])
                with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                                          and filter_key in parse_qs(urlsplit(response.url).query)
                                          and response.status == 200) as response:
                    chip.click()
                payload = response.value.json()
                rows = payload["items"] if isinstance(payload, dict) else payload
                assert {row["id"] for row in rows} == set(TAG_MATCH_IDS), rows
                query = parse_qs(urlsplit(response.value.url).query)
                assert "search" not in query and query.get("shape") == ["article"], query
                expect(page.locator(".reader-entry")).to_have_count(2)
                cases.append({"viewport": name, "tag": label, "check": "identity_and_noise_exclusion", "query": query})
                page.get_by_role("button", name="关闭搜索", exact=True).click()
                expect(page.locator(".reader-entry")).to_have_count(30)

                # Use the browser's timer to exercise the actual <300ms debounce window,
                # independent of Playwright actionability or mobile page animations.
                first.click()
                expect(chip).to_be_visible()
                with page.expect_response(lambda response: urlsplit(response.url).path == "/api/articles"
                                          and not any(key in parse_qs(urlsplit(response.url).query)
                                                      for key in ["display_tag_id", "tag_ids", "display_tag", "search"])
                                          and response.status == 200):
                    elapsed = chip.evaluate("""el => new Promise((resolve, reject) => {
                        const started = performance.now();
                        el.click();
                        setTimeout(() => {
                            const close = document.querySelector('button[aria-label="关闭搜索"]');
                            if (!close) { reject(new Error('search close control missing')); return; }
                            close.click();
                            resolve(performance.now() - started);
                        }, 25);
                    })""")
                assert elapsed < 300, f"rapid-close timing outside debounce window: {elapsed}ms"
                expect(page.get_by_placeholder("搜索我的阅读…")).to_have_count(0)
                expect(page.locator(".reader-entry")).to_have_count(30)
                # This is a measurement window, not a wait for page readiness: an old
                # debounced effect must not restore a hidden filter after the clear.
                page.wait_for_timeout(600)
                query = successful_lists[-1]["query"]
                assert not any(key in query for key in ["display_tag_id", "tag_ids", "display_tag", "search"]), query
                expect(page.locator(".reader-entry")).to_have_count(30)
                cases.append({"viewport": name, "tag": label, "check": "rapid_close", "elapsed_ms": elapsed, "query": query})
            assert not errors, errors
            page.screenshot(path=str(artifacts / f"tag-search-{name}.png"))
            passed = True
        except Exception:
            page.screenshot(path=str(artifacts / f"tag-search-{name}-failure.png"))
            raise
        finally:
            result.setdefault("tag_search_audits", {})[name] = {"page_errors": errors, "requests": successful_lists}
            context.tracing.stop(path=str(artifacts / f"tag-search-{name}-trace.zip") if not passed else None)
            context.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM article_tag_assignments").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM user_interest_tags").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM reader_subscriptions").fetchone()[0] == 2
    (artifacts / "tag-search-checks.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2)+"\n")
