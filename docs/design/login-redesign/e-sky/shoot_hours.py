"""E 天色 · 四个时段的补充截图(样页工具)。

在 worktree 根执行(静态服务须在 5191):
    .venv/bin/python docs/design/login-redesign/e-sky/shoot_hours.py
输出 e-sky/shots/hour-06.jpg(06:30 拂晓)/ hour-12.jpg(正午)/ hour-17.jpg(17:36 黄昏)/ hour-23.jpg(23:30 深夜),
另附同时刻的手机竖屏 hour-XX-mobile.jpg。云层用固定 seed,便于对比。退出码 = console 错误条数。
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
URL = "http://127.0.0.1:5191/docs/design/login-redesign/e-sky/index.html"
HOURS = [("06", 6.5), ("12", 12), ("17", 17.6), ("23", 23.5)]


def main() -> int:
    out = HERE / "shots"
    out.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=metal", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"])
        for tag, hour in HOURS:
            for suffix, vp, dpr in [("", (1440, 900), 1), ("-mobile", (390, 844), 2)]:
                ctx = browser.new_context(viewport={"width": vp[0], "height": vp[1]}, device_scale_factor=dpr)
                page = ctx.new_page()
                page.on("console", lambda m: m.type == "error" and errors.append(m.text))
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"{URL}?kit=0&intro=skip&seed=3&hour={hour}", wait_until="load")
                page.wait_for_timeout(1800)
                page.screenshot(path=str(out / f"hour-{tag}{suffix}.jpg"), type="jpeg", quality=84)
                ctx.close()
        browser.close()
    for e in errors:
        print(e)
    print(f"errors: {len(errors)}")
    return len(errors)


if __name__ == "__main__":
    sys.exit(main())
