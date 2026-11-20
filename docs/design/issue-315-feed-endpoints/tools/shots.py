#!/usr/bin/env python3
"""样页自查(issue #315):逐个状态开页、截图、量尺寸;控制台报错与失败请求须为零。

用法:先在仓库根起静态服务(python3 -m http.server 8766 --bind 127.0.0.1),再
    python3 tools/shots.py <输出目录> [http://127.0.0.1:8766]
"""
from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1])
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8766").rstrip("/")
PAGE = f"{BASE}/docs/design/issue-315-feed-endpoints"

STATES = {
    "now-reader": "dir=now&bar=0",
    "now-admin": "dir=now&role=admin&bar=0",
    "a-reader": "dir=a&bar=0",
    "a-open": "dir=a&open=articles&bar=0",
    "a-md": "dir=a&open=articles.md&bar=0",
    "a-atom": "dir=a&open=articles.xml&bar=0",
    "a-events-bottom": "dir=a&open=events&scroll=bottom&bar=0",
    "a-detail-bottom": "dir=a&open=events/{event_id}&scroll=bottom&bar=0",
    "a-admin": "dir=a&role=admin&bar=0",
    "a-none": "dir=a&token=none&bar=0",
    "a-nolink": "dir=a&mcp=0&bar=0",
    "a-dark": "dir=a&theme=dark&bar=0",
    "a-dark-open": "dir=a&open=articles&theme=dark&bar=0",
    "b-reader": "dir=b&bar=0",
    "b-atom": "dir=b&fmt=articles.xml&bar=0",
    "b-detail-bottom": "dir=b&ev=events/{event_id}&scroll=bottom&bar=0",
    "b-params-bottom": "dir=b&params=both&scroll=bottom&bar=0",
    "b-admin": "dir=b&role=admin&bar=0",
    "b-none": "dir=b&token=none&bar=0",
    "b-dark": "dir=b&theme=dark&bar=0",
    "a-with-bar": "dir=a",
}

GEO = """() => {
  const r = el => (b => ({y: Math.round(b.y * 10) / 10, h: Math.round(b.height * 10) / 10}))(el.getBoundingClientRect());
  const all = s => Array.from(document.querySelectorAll(s));
  const sc = document.querySelector('.sett-scroll');
  return {
    panel: (b => [b.x, b.y, b.width, b.height])(document.querySelector('.modal-panel').getBoundingClientRect()),
    scrollH: sc.scrollHeight, clientH: sc.clientHeight,
    rows: all('.endpoint').map(r), groups: all('.ep-group').map(r), token: all('.token-row').map(r),
    urlClipped: all('.endpoint-url').filter(el => el.scrollWidth > el.clientWidth).map(el => el.textContent),
  };
}"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    report: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        for name, query in STATES.items():
            page = ctx.new_page()
            page.on("pageerror", lambda e, n=name: problems.append(f"{n}: pageerror {e}"))
            page.on("console", lambda m, n=name: problems.append(f"{n}: console {m.text}") if m.type == "error" else None)
            page.on("requestfailed", lambda r, n=name: problems.append(f"{n}: failed {r.url}"))
            page.on("response", lambda r, n=name: problems.append(f"{n}: {r.status} {r.url}") if r.status >= 400 else None)
            page.goto(f"{PAGE}/app.html?{query}", wait_until="networkidle")
            page.wait_for_timeout(350)
            report[name] = page.evaluate(GEO)
            page.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 268, "y": 84, "width": 904, "height": 600})
            page.close()
        page = ctx.new_page()
        page.on("pageerror", lambda e: problems.append(f"index: pageerror {e}"))
        page.on("console", lambda m: problems.append(f"index: console {m.text}") if m.type == "error" else None)
        page.goto(f"{PAGE}/index.html", wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "index.png"), full_page=True)
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, geo in report.items():
        heights = sorted({row["h"] for row in geo["rows"]})
        print(f"{name:18s} rows={len(geo['rows'])} h={heights} scroll={geo['scrollH']}/{geo['clientH']} clipped={geo['urlClipped']}")
    print(f"SHOTS {len(STATES)} states · problems={len(problems)}")
    for line in problems[:12]:
        print("  ", line[:220])
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
