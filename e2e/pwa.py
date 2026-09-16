"""Production-build PWA checks. Browser-event/UA simulations are NOT device certification."""
from pathlib import Path

from playwright.sync_api import expect

from e2e.mobile_reader import login


def assert_install_visible(page):
    dialog = page.get_by_role("dialog", name="添加到主屏幕")
    expect(dialog).to_have_css("opacity", "1")
    expect(dialog.locator("..")).to_have_css("opacity", "1")
    metrics = dialog.evaluate("""el => {
        const box = el.getBoundingClientRect();
        const close = el.querySelector('button').getBoundingClientRect();
        return {inside: box.top >= 0 && box.left >= 0 && box.bottom <= innerHeight && box.right <= innerWidth,
            reachable: el.contains(document.elementFromPoint(close.x + close.width/2, close.y + close.height/2))};
    }""")
    assert metrics["inside"] and metrics["reachable"], metrics


def run_pwa_flows(browser, base_url: str, site: Path, artifacts: Path, result: dict):
    checks = result.setdefault("checks", [])
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    context.set_default_timeout(12000)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    errors = []
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    passed = False
    worker_path = site / "sw.js"
    original_worker = worker_path.read_bytes()
    try:
        page.goto(base_url)
        assert login(page).status == 200
        expect(page.locator(".m-tabbar")).to_be_visible(timeout=30000)
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        assert page.evaluate("async () => (await caches.keys()).length") == 0
        manifest = page.request.get(base_url + "/manifest.webmanifest")
        assert manifest.ok and manifest.json()["display"] == "standalone"
        page.get_by_role("button", name="我的", exact=True).click()
        install = page.get_by_role("button", name="添加到主屏幕", exact=False)
        expect(install).to_be_visible()
        # A deliberately rejected/cancelled browser prompt, not an API response mock.
        def prompt(outcome):
            page.evaluate("""outcome => {
                const event = new Event('beforeinstallprompt', {cancelable: true});
                event.prompt = () => { window.__promptCalls = (window.__promptCalls || 0) + 1;
                    return outcome === 'error' ? Promise.reject(new Error('blocked')) : Promise.resolve(); };
                event.userChoice = Promise.resolve({outcome}); window.dispatchEvent(event);
            }""", outcome)
        prompt("dismissed")
        install.click()
        expect(install).to_contain_text("查看步骤")
        expect(page.get_by_role("dialog", name="添加到主屏幕")).to_have_count(0)
        assert page.evaluate("window.__promptCalls") == 1
        checks.append("simulated install cancellation: one system prompt, no follow-up nag")
        prompt("error")
        install.click()
        dialog = page.get_by_role("dialog", name="添加到主屏幕")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("暂未完成安装")
        dialog.get_by_role("button", name="关闭安装指引").click()
        expect(dialog).to_have_count(0)
        install.click()
        expect(dialog).to_be_visible()
        assert_install_visible(page)
        # Negative control catches the clipped-overlay regression seen in the first render review.
        dialog.evaluate("el => el.style.setProperty('transform', 'translateY(-200vh)', 'important')")
        try:
            try:
                assert_install_visible(page)
            except AssertionError:
                checks.append("negative control: off-screen installation dialog rejected")
            else:
                raise RuntimeError("installation geometry check accepted a clipped dialog")
        finally:
            dialog.evaluate("el => el.style.removeProperty('transform')")
        select = dialog.get_by_label("安装指引平台")
        select.select_option("chromium")
        expect(dialog).to_contain_text("安装应用")
        expect(select.locator('option[value="huawei"]')).to_have_count(0)
        page.screenshot(path=str(artifacts / "pwa-android-guide.png"), animations="disabled")
        select.select_option("ios")
        expect(dialog).to_contain_text("作为 Web App 打开")
        page.emulate_media(color_scheme="dark")
        page.screenshot(path=str(artifacts / "pwa-ios-dark.png"), animations="disabled")
        page.set_viewport_size({"width": 568, "height": 320})
        close = dialog.get_by_role("button", name="关闭安装指引")
        expect(close).to_be_in_viewport()
        assert_install_visible(page)
        close.click()
        expect(dialog).to_have_count(0)
        page.emulate_media(color_scheme="light")
        page.set_viewport_size({"width": 1365, "height": 900})
        page.get_by_role("button", name="设置", exact=True).click()
        settings = page.get_by_role("dialog", name="设置", exact=True)
        settings.get_by_role("button", name="外观", exact=False).click()
        settings.get_by_role("button", name="查看步骤", exact=True).click()
        expect(settings.get_by_label("安装指引平台")).to_be_visible()
        assert page.get_by_role("dialog").count() == 1
        page.screenshot(path=str(artifacts / "pwa-tablet-settings.png"), animations="disabled")
        page.get_by_role("button", name="关闭设置").click()
        checks.append("manual Android/iOS guidance, dark mode, short landscape, tablet inline guide (no nested modal)")

        # Changing the actually served worker models a deployment; do not mutate source/evidence.
        page.evaluate("window.__sameDocument = true")
        worker_path.write_bytes(original_worker + b"\n// E2E replacement deployment\n")
        page.evaluate("async () => (await navigator.serviceWorker.getRegistration()).update()")
        expect(page.get_by_role("status", name="版本更新")).to_be_visible()
        assert page.evaluate("window.__sameDocument") is True
        page.screenshot(path=str(artifacts / "pwa-update.png"), animations="disabled")
        page.get_by_role("button", name="刷新", exact=True).click()
        page.wait_for_function("window.__sameDocument === undefined")
        expect(page.get_by_role("status", name="版本更新")).to_have_count(0)
        expect(page.locator(".reader-vrail")).to_be_visible()
        checks.append("real SW update: offers refresh without replacing current document; login survives refresh")

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="我的", exact=True).click()
        prompt("accepted")
        install.click()
        page.evaluate("window.dispatchEvent(new Event('appinstalled'))")
        expect(install).to_have_count(0)
        storage = context.storage_state()
        checks.append("simulated appinstalled hides installation entry")
        context.set_offline(True)
        expect(page.get_by_role("status", name="网络状态")).to_be_visible()
        assert page.evaluate("async () => { try { await fetch('/api/auth/session'); return false; } catch { return true; } }")
        page.goto(base_url + "/pwa-navigation-probe#offline")
        expect(page.get_by_role("heading", name="暂时无法连接")).to_be_visible()
        page.screenshot(path=str(artifacts / "pwa-offline.png"))
        assert page.evaluate("async () => (await caches.keys()).length") == 0
        context.set_offline(False)
        page.get_by_role("link", name="重新连接").click()
        expect(page.locator(".m-tabbar")).to_be_visible(timeout=30000)
        assert "/pwa-navigation-probe" in page.url
        assert page.evaluate("async () => (await fetch('/api/auth/logout', {method:'POST'})).status") == 200
        page.reload()
        expect(page.get_by_placeholder("输入登录账号")).to_be_visible()
        assert page.evaluate("async () => (await caches.keys()).length") == 0
        checks.append("offline API fails closed; navigation fallback retries deep path; no CacheStorage before/after logout")

        standalone = browser.new_context(storage_state=storage, viewport={"width": 390, "height": 844})
        try:
            standalone.add_init_script("""const original = window.matchMedia.bind(window);
                window.matchMedia = query => query.includes('display-mode')
                  ? {matches:true, addEventListener(){}, removeEventListener(){}} : original(query);""")
            app = standalone.new_page()
            app.goto(base_url)
            expect(app.locator(".m-tabbar")).to_be_visible(timeout=30000)
            app.get_by_role("button", name="我的", exact=True).click()
            expect(app.get_by_role("button", name="添加到主屏幕", exact=False)).to_have_count(0)
            checks.append("simulated standalone launch: persisted session, installation entry hidden")
        finally:
            standalone.close()
        # Synthetic UA inputs test the product gate, not Huawei system capabilities.
        for index, (ua, platform) in enumerate([
            ("Mozilla/5.0 (OpenHarmony 7.0) Chrome/144.0.0.0 HuaweiBrowser/6.1.7.303", None),
            ("Mozilla/5.0 (Linux; Android 12; wv) Chrome/132.0.0.0 HuaweiBrowser/6.1.7.303", None),
            ("Mozilla/5.0 (HarmonyOS 7.0) Chrome/144.0.0.0", None),
            ("Mozilla/5.0 Chrome/144.0.0.0", "HarmonyOS"),
        ]):
            excluded = browser.new_context(storage_state=storage, user_agent=ua, viewport={"width": 390, "height": 844})
            try:
                if platform:
                    excluded.add_init_script("Object.defineProperty(navigator, 'userAgentData', {value:{platform:'HarmonyOS'}})")
                app = excluded.new_page()
                app.on("pageerror", lambda error: errors.append(str(error)))
                app.goto(base_url)
                expect(app.locator(".m-tabbar")).to_be_visible(timeout=30000)
                app.wait_for_function("navigator.serviceWorker.controller !== null")
                app.get_by_role("button", name="我的", exact=True).click()
                assert app.evaluate("""() => {
                    window.__promptCalls = 0;
                    for (let i = 0; i < 3; i++) {
                        const event = new Event('beforeinstallprompt', {cancelable:true});
                        event.prompt = () => { window.__promptCalls++; return Promise.resolve(); };
                        event.userChoice = Promise.resolve({outcome:'accepted'});
                        window.dispatchEvent(event);
                        if (!event.defaultPrevented) return false;
                    }
                    return true;
                }""")
                expect(app.get_by_role("button", name="添加到主屏幕", exact=False)).to_have_count(0)
                app.set_viewport_size({"width": 1365, "height": 900})
                app.get_by_role("button", name="设置", exact=True).click()
                drawer = app.get_by_role("dialog", name="设置", exact=True)
                drawer.get_by_role("button", name="外观", exact=False).click()
                expect(drawer).not_to_contain_text("添加到主屏幕")
                if index == 0:
                    app.screenshot(path=str(artifacts / "pwa-harmony-reading-only.png"), animations="disabled")
                app.get_by_role("button", name="关闭设置").click()
                expect(app.locator(".reader-vrail")).to_be_visible()
                assert app.evaluate("window.__promptCalls") == 0
            finally:
                excluded.close()
        checks.append("HarmonyOS/Huawei policy: repeated install events cannot expose mobile/tablet entry; reading and SW remain available (synthetic UA)")
        assert not errors, errors
        passed = True
    finally:
        if not passed:
            page.screenshot(path=str(artifacts / "pwa-failure.png"))
        worker_path.write_bytes(original_worker)
        context.tracing.stop(path=str(artifacts / "pwa-trace.zip"))
        context.close()
        result["pwa_browser_errors"] = errors
