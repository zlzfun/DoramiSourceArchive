#!/usr/bin/env python3
"""自查:逐个状态打开样页截图,收集控制台报错与失败请求(issue #344)。

用法:python3 tools/shots.py <样页根地址> <输出目录>
例:  python3 tools/shots.py http://127.0.0.1:8766/docs/design/issue-344-push-binding /tmp/shots
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1].rstrip("/"), Path(sys.argv[2])
STATES = [
    ("tokens-now", "tokens.html?state=now&bare"),
    ("tokens-new", "tokens.html?bare"),
    ("tokens-drawer", "tokens.html?open=token&bare"),
    ("tokens-drawer-self", "tokens.html?open=token&self=1&bare"),
    ("tokens-drawer-dark", "tokens.html?open=token&theme=dark&bare"),
    ("sources-now", "sources.html?state=now&bare"),
    ("sources-new", "sources.html?bare"),
    ("sources-new-dark", "sources.html?theme=dark&bare"),
    ("sources-x-new", "sources-x.html?bare"),
    ("register-open", "sources.html?open=register&bare"),
    ("register-account", "sources.html?open=register&reg=one&bare"),
    ("register-article", "sources.html?open=register&kind=article&bare"),
    ("register-podcast", "sources.html?open=register&kind=podcast&bare"),
    ("with-bar", "tokens.html"),
    ("index", "index.html"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, path in STATES:
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            problems = []
            page.on("pageerror", lambda exc, problems=problems: problems.append(f"pageerror: {exc}"))
            page.on("console", lambda msg, problems=problems: problems.append(f"console: {msg.text}") if msg.type == "error" else None)
            page.on("requestfailed", lambda req, problems=problems: problems.append(f"failed: {req.url}"))
            page.on("response", lambda res, problems=problems: problems.append(f"{res.status}: {res.url}") if res.status >= 400 else None)
            page.goto(f"{BASE}/{path}", wait_until="networkidle")
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=(name == "index"))
            report[name] = problems
            page.close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    sys.exit(1 if any(report.values()) else 0)


if __name__ == "__main__":
    main()
