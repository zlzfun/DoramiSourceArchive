"""Focus-ring regression checks for issue #108 (Chromium, desktop + mobile shell).

Two layers of assurance:

* Structural guard: the global ``button/input/select/textarea:focus-visible`` fallback ring
  must live in ``@layer base`` (read from the live CSSOM). Inputs whose container draws the
  focus ring set ``outline: none`` inside ``@layer components``; an unlayered fallback would
  win the cascade and paint a second rectangle inside the container ring. Four known
  container-ring inputs are mouse-focused and must show no outline of their own while the
  ring host changes a ring-like property (box-shadow / border colour / outline).
* Keyboard smoke sweep: Tab through a page until focus wraps back to the first stop (directly,
  or after leaving the document once and re-entering at the first stop). Every stop must match
  ``:focus-visible`` and change some sampled style on itself,
  a child, an ancestor or a pseudo-element, and no text control may paint the fallback outline
  on top of a container ring. The sweep samples computed styles only; it does not judge
  contrast or clipping, which stay with visual acceptance.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from e2e.mobile_reader import login

NO_MOTION = "*, *::before, *::after { transition: none !important; animation: none !important; }"
# 页面侧助手:ring = 环类属性(外环 / 阴影 / 四边描边),any = 环类 + 颜色 / 透明度 / 变形 / 装饰;都含 ::before / ::after。
HELPERS = """() => {
    const RING = ['outlineStyle', 'outlineWidth', 'outlineColor', 'boxShadow',
        'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor',
        'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth'];
    const ANY = RING.concat(['backgroundColor', 'backgroundImage', 'color', 'opacity', 'filter', 'transform',
        'textDecorationLine']);
    const read = (el, props) => ['', '::before', '::after'].map(pseudo => {
        const cs = getComputedStyle(el, pseudo || null);
        return props.map(p => cs[p]).join('|');
    }).join('#');
    window.__ring = el => read(el, RING);
    window.__any = el => read(el, ANY);
    window.__ancestors = el => { const out = []; let e = el.parentElement;
        for (let i = 0; i < 3 && e && e !== document.body; i++) { out.push(e); e = e.parentElement; } return out; };
    window.__children = el => [...el.children].slice(0, 6);
}"""


def install_helpers(page):
    page.add_style_tag(content=NO_MOTION)
    page.evaluate(HELPERS)


def focus_rule_layers(page):
    """Every stylesheet rule that sets an outline for ``input:focus-visible`` with its @layer chain."""
    return page.evaluate("""() => {
        const out = [];
        const walk = (rules, layers) => { for (const rule of rules) {
            if (rule instanceof CSSLayerBlockRule) walk(rule.cssRules, [...layers, rule.name]);
            else if (rule instanceof CSSStyleRule) {
                // outline 简写含 var() 时长手属性读出空串,按 cssText 判定是否声明了 outline
                if (/(^|,)\\s*input:focus-visible\\s*(,|$)/.test(rule.selectorText)
                        && /(^|;\\s*)outline(-[a-z]+)?\\s*:/.test(rule.style.cssText))
                    out.push({selector: rule.selectorText, layers});
                if (rule.cssRules) walk(rule.cssRules, layers);
            } else if (rule.cssRules) walk(rule.cssRules, layers);
        } };
        for (const sheet of document.styleSheets) { try { walk(sheet.cssRules, []); } catch (e) { /* cross-origin */ } }
        return out; }""")


def assert_container_ring(page, name, input_selector, container_selector, checks):
    """Mouse-focus the input: no outline of its own; the ring host must change a ring-like property."""
    box = page.locator(input_selector).first
    expect(box).to_be_visible()
    box.click()
    verdict = page.evaluate("""([isel, csel]) => {
        const input = document.querySelector(isel);
        const host = csel ? input.closest(csel) : input;
        const focusedRing = host ? window.__ring(host) : null;
        const out = {active: document.activeElement === input, focusVisible: input.matches(':focus-visible'),
                     outline: getComputedStyle(input).outlineStyle, host: !!host};
        input.blur();
        out.hostRingChanged = host ? window.__ring(host) !== focusedRing : false;
        return out; }""", [input_selector, container_selector])
    assert verdict["active"] and verdict["focusVisible"] and verdict["host"], (name, verdict)
    assert verdict["outline"] == "none", (name, "input paints its own outline inside the container ring", verdict)
    assert verdict["hostRingChanged"], (name, "ring host shows no box-shadow / border / outline change on focus", verdict)
    checks.append(f"focus-ring:{name}")


def assert_tab_stops_visible(page, name, checks, limit=200):
    """Tab through the whole page; see the module docstring for what counts as a pass."""
    # 把顺序聚焦起点重置到文档开头(否则从刚点过的输入框往后扫,只覆盖半页)。
    page.evaluate("""() => { window.__focusStops = []; scrollTo(0, 0);
        document.body.setAttribute('tabindex', '-1'); document.body.focus(); document.body.removeAttribute('tabindex'); }""")
    # 完整一圈的证据只有回到首个停靠元素:直接回绕(cycle),或焦点离开文档一次后再按 Tab 回到首元素
    # (left-document-cycle)。焦点落到 body 本身不算走完——控件自退焦 / 重渲染卸载也会出现同样状态,
    # 此时继续扫;连续两次落在 body 或触到上限都算失败。
    reason = None
    body_streak = 0
    for _ in range(limit):
        page.keyboard.press("Tab")
        code = page.evaluate("""() => {
            const active = document.activeElement;
            if (!active || active === document.body) return 'body';
            const stops = window.__focusStops;
            if (stops.length && stops[0].el === active) return 'cycle';
            if (stops.some(stop => stop.el === active)) return 'trap';
            const kids = window.__children(active), ancestors = window.__ancestors(active);
            stops.push({el: active, kids, ancestors, tag: active.tagName.toLowerCase(),
                focusVisible: active.matches(':focus-visible'), outline: getComputedStyle(active).outlineStyle,
                label: (active.getAttribute('aria-label') || active.textContent || '').trim().slice(0, 30),
                self: window.__any(active), kidsAny: kids.map(window.__any),
                ancAny: ancestors.map(window.__any), ancRing: ancestors.map(window.__ring)});
            return null; }""")
        if code == "body":
            body_streak += 1
            if body_streak >= 2:
                reason = "stuck-on-body"
                break
            continue
        if code == "cycle":
            reason = "cycle" if body_streak == 0 else "left-document-cycle"
            break
        if code == "trap":
            reason = "trap"
            break
        body_streak = 0
    page.evaluate("document.activeElement && document.activeElement.blur()")
    count = page.evaluate("window.__focusStops.length")
    assert reason in ("cycle", "left-document-cycle"), (name, f"sweep ended by {reason or 'limit'} after {count} stops")
    problems = page.evaluate("""() => window.__focusStops.flatMap(stop => {
        if (!stop.el.isConnected) return [];
        const who = stop.tag + ' ' + stop.label;
        const out = [];
        if (!stop.focusVisible) out.push('keyboard stop not :focus-visible: ' + who);
        const selfChanged = window.__any(stop.el) !== stop.self;
        const kidsChanged = stop.kids.some((el, i) => window.__any(el) !== stop.kidsAny[i]);
        const ancChanged = stop.ancestors.some((el, i) => window.__any(el) !== stop.ancAny[i]);
        if (!selfChanged && !kidsChanged && !ancChanged) out.push('no focus indicator: ' + who);
        const ancRingChanged = stop.ancestors.some((el, i) => window.__ring(el) !== stop.ancRing[i]);
        if (['input', 'textarea', 'select'].includes(stop.tag) && stop.outline !== 'none' && ancRingChanged)
            out.push('double ring: ' + who);
        return out; })""")
    assert count >= 8 and not problems, (name, count, reason, problems)
    checks.append(f"tab-sweep:{name}:{count}:{reason}")


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
        install_helpers(page)
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
        install_helpers(page)
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
        install_helpers(page)
        page.locator(".m-tabbar .m-tab", has_text="文章").first.click()
        page.get_by_role("button", name=re.compile("搜索")).first.click()
        assert_container_ring(page, "mobile-search", ".m-search-inline input", None, checks)
        assert not errors, errors
    except Exception:
        page.screenshot(path=str(artifacts / "focus-ring-mobile-failure.png"))
        raise
    finally:
        context.close()
