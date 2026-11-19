#!/usr/bin/env python3
"""样页自查:逐个状态打开原型,截图并收集控制台报错。

用法:python3 tools/shots.py <输出目录> [基地址]
先在仓库根起静态服务:python3 -m http.server 8765 --bind 127.0.0.1
"""
from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1])
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8765") + "/docs/design/issue-312-token-usage/"

CASES = [
    ("a-page", "app.html?dir=a&bare", True),
    ("b-page", "app.html?dir=b&bare", True),
    ("b-api", "app.html?dir=b&focus=api&bare", False),
    ("b-api-dark", "app.html?dir=b&focus=api&theme=dark&bare", False),
    ("b-empty", "app.html?dir=b&focus=api&state=empty&bare", False),
    ("drawer-pipeline", "app.html?dir=b&view=drawer&user=brief-pipeline&bare", False),
    ("drawer-bot", "app.html?dir=b&view=drawer&user=newsroom-bot&bare", False),
    ("drawer-admin", "app.html?dir=b&view=drawer&user=admin&bare", False),
    ("drawer-unused", "app.html?dir=b&view=drawer&user=wu.tong&bare", False),
    ("drawer-none", "app.html?dir=b&view=drawer&user=reader-demo&bare", False),
    ("drawer-dark", "app.html?dir=b&view=drawer&user=brief-pipeline&theme=dark&bare", False),
    ("settings-now", "app.html?view=settings&card=now&bare", False),
    ("settings-a", "app.html?view=settings&card=a&bare", False),
    ("settings-b", "app.html?view=settings&card=b&bare", False),
    ("settings-b-dark", "app.html?view=settings&card=b&theme=dark&bare", False),
    ("bar", "app.html?dir=b&view=settings&card=b", False),
    ("index", "index.html", True),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, path, full in CASES:
            if only and name not in only:
                continue
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            page = ctx.new_page()
            page.on("pageerror", lambda e, n=name: problems.append(f"{n}: pageerror {e}"))
            page.on("console", lambda m, n=name: problems.append(f"{n}: console {m.text}") if m.type == "error" else None)
            page.on("requestfailed", lambda r, n=name: problems.append(f"{n}: failed {r.url}"))
            page.on("response", lambda r, n=name: problems.append(f"{n}: {r.status} {r.url}") if r.status >= 400 else None)
            page.goto(BASE + path, wait_until="networkidle")
            if name == "index":
                # 画框是懒加载的:先滚到底让它们都进来
                page.evaluate("async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 250)); } window.scrollTo(0, 0); }")
                page.wait_for_load_state("networkidle")
            page.wait_for_timeout(900)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            ctx.close()
        browser.close()
    print("\n".join(problems) if problems else "no console errors / failed requests")


if __name__ == "__main__":
    main()
