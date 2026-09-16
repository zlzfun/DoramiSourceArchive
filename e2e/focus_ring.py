"""Focus-ring regression checks for issue #108 (Chromium, desktop + mobile shell).

The global ``button/input/select/textarea:focus-visible`` fallback ring must live in
``@layer base``. Inputs whose container draws the focus ring set ``outline: none``
inside ``@layer components``; an unlayered fallback would win the cascade and paint a
second rectangle inside the container ring. These checks read the live CSSOM and the
rendered focus states, so they fail if the rule ever leaves the base layer or a
container-ring input loses its visible indicator.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from e2e.mobile_reader import login

NO_MOTION = "*, *::before, *::after { transition: none !important; animation: none !important; }"
SNAP = """el => { const cs = getComputedStyle(el);
    return [cs.outlineStyle, cs.outlineWidth, cs.boxShadow, cs.borderTopColor, cs.borderBottomColor,
            cs.backgroundColor, cs.color, cs.opacity].join('|'); }"""


def focus_rule_layers(page):
    """Every stylesheet rule that sets an outline for ``input:focus-visible`` with its @layer chain."""
    return page.evaluate("""() => {
        const out = [];
        const walk = (rules, layers) => { for (const rule of rules) {
            if (rule instanceof CSSLayerBlockRule) walk(rule.cssRules, [...layers, rule.name]);
            else if (rule instanceof CSSStyleRule) {
                if (/(^|,)\\s*input:focus-visible\\s*(,|$)/.test(rule.selectorText) && /(^|;\\s*)outline(-[a-z]+)?\\s*:/.test(rule.style.cssText))
                    out.push({selector: rule.selectorText, layers});
                if (rule.cssRules) walk(rule.cssRules, layers);
            } else if (rule.cssRules) walk(rule.cssRules, layers);
        } };
        for (const sheet of document.styleSheets) { try { walk(sheet.cssRules, []); } catch (e) { /* cross-origin */ } }
        return out; }""")


def assert_container_ring(page, name, input_selector, container_selector, checks):
    """Mouse-focus the input: it must paint no outline of its own while its ring host changes."""
    box = page.locator(input_selector).first
    expect(box).to_be_visible()
    box.click()
    verdict = page.evaluate("""([isel, csel, snapSrc]) => {
        const snap = eval(snapSrc);
        const input = document.querySelector(isel);
        const host = csel ? input.closest(csel) : input;
        const focused = snap(host);
        const out = {active: document.activeElement === input, focusVisible: input.matches(':focus-visible'),
                     outline: getComputedStyle(input).outlineStyle, host: !!host};
        input.blur();
        out.hostChanged = snap(host) !== focused;
        return out; }""", [input_selector, container_selector, SNAP])
    assert verdict["active"] and verdict["focusVisible"] and verdict["host"], (name, verdict)
    assert verdict["outline"] == "none", (name, "input paints its own outline inside the container ring", verdict)
    assert verdict["hostChanged"], (name, "no visible focus indicator on the ring host", verdict)
    checks.append(f"focus-ring:{name}")


def assert_tab_stops_visible(page, name, checks, limit=160):
    """Tab through the page: every :focus-visible stop needs a visible change on itself, a child or an ancestor,
    and no text control may paint the fallback outline on top of a container ring."""
    # 把顺序聚焦起点重置到文档开头(否则从刚点过的输入框往后扫,只覆盖半页)。
    page.evaluate("""() => { window.__focusStops = []; scrollTo(0, 0);
        document.body.setAttribute('tabindex', '-1'); document.body.focus(); document.body.removeAttribute('tabindex'); }""")
    seen = set()
    for _ in range(limit):
        page.keyboard.press("Tab")
        key = page.evaluate("""snapSrc => { const snap = eval(snapSrc);
            const active = document.activeElement; if (!active || active === document.body) return null;
            const chain = [active, ...[...active.children].slice(0, 6)]; let el = active.parentElement;
            for (let i = 0; i < 3 && el && el !== document.body; i++) { chain.push(el); el = el.parentElement; }
            const r = active.getBoundingClientRect();
            window.__focusStops.push({chain, focusVisible: active.matches(':focus-visible'), focused: chain.map(snap),
                tag: active.tagName.toLowerCase(), outline: getComputedStyle(active).outlineStyle,
                label: (active.getAttribute('aria-label') || active.textContent || '').trim().slice(0, 30)});
            return active.tagName + '|' + Math.round(r.x) + ',' + Math.round(r.y) + '|' + (active.className || ''); }""", SNAP)
        if key is None or key in seen:
            break
        seen.add(key)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    problems = page.evaluate("""snapSrc => { const snap = eval(snapSrc);
        return window.__focusStops.flatMap(stop => {
            if (!stop.chain[0].isConnected) return [];
            const blurred = stop.chain.map(snap);
            const selfChanged = stop.focused[0] !== blurred[0];
            const otherChanged = stop.focused.some((v, k) => k > 0 && v !== blurred[k]);
            const out = [];
            if (stop.focusVisible && !selfChanged && !otherChanged) out.push('no focus indicator: ' + stop.tag + ' ' + stop.label);
            if (['input', 'textarea', 'select'].includes(stop.tag) && stop.outline !== 'none' && otherChanged)
                out.push('double ring: ' + stop.tag + ' ' + stop.label);
            return out; }); }""", SNAP)
    assert len(seen) >= 8 and not problems, (name, len(seen), problems)
    checks.append(f"tab-sweep:{name}:{len(seen)}")


def run_focus_flows(browser, base_url: str, artifacts, result: dict):
    checks = result.setdefault("checks", [])
    errors = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(12000)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(base_url)
        expect(page.locator(".auth-input").first).to_be_visible()
        page.add_style_tag(content=NO_MOTION)
        layers = focus_rule_layers(page)
        assert layers and all(rule["layers"] == ["base"] for rule in layers), layers
        checks.append("focus-ring:global-rule-in-base-layer")
        assert_container_ring(page, "login-username", ".auth-input", ".auth-input-wrap", checks)
        assert login(page).status == 200
        expect(page.locator(".reader-vrail")).to_be_visible(timeout=30000)
        # 兴趣引导完成后再看发现页 / 文章容器的常态(只改本会话的读者,不改共用夹具)。
        page.request.post(f"{base_url}/api/reader/interests/onboarding/complete")
        page.reload()
        expect(page.locator(".reader-vrail")).to_be_visible(timeout=30000)
        page.add_style_tag(content=NO_MOTION)
        page.get_by_role("button", name=re.compile("^发现")).first.click()
        assert_container_ring(page, "discover-filter", ".reader-disc-search input", ".reader-disc-search", checks)
        assert_tab_stops_visible(page, "discover", checks)
        page.get_by_role("button", name=re.compile("^文章")).first.click()
        page.get_by_role("button", name="搜索", exact=True).first.click()
        assert_container_ring(page, "list-search", ".reader-search-input", ".reader-search-inline", checks)
        assert_tab_stops_visible(page, "articles", checks)
        assert not errors, errors
    except Exception:
        page.screenshot(path=str(artifacts / "focus-ring-desktop-failure.png"))
        raise
    finally:
        context.close()

    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    context.set_default_timeout(12000)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(base_url)
        assert login(page).status == 200
        expect(page.locator(".m-tabbar")).to_be_visible(timeout=30000)
        page.add_style_tag(content=NO_MOTION)
        page.locator(".m-tabbar .m-tab", has_text="文章").first.click()
        page.get_by_role("button", name=re.compile("搜索")).first.click()
        assert_container_ring(page, "mobile-search", ".m-search-inline input", None, checks)
        assert not errors, errors
    except Exception:
        page.screenshot(path=str(artifacts / "focus-ring-mobile-failure.png"))
        raise
    finally:
        context.close()
