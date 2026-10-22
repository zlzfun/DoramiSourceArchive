"""登录页重设计样页 · 统一截图与控制台自检(样页工具)。

用法(静态服务须从 worktree 根起在 5191 端口):
    .venv/bin/python docs/design/login-redesign/shared/shoot.py <slug> [--film 300,900,1800,3200,5000] [--base URL]

输出到 docs/design/login-redesign/<slug>/shots/:
    film-<ms>.jpg        桌面 1440×900 开场逐帧(不跳过开场)
    desktop.jpg          开场完成态
    desktop-password.jpg 密码框聚焦(「蒙眼」类彩蛋)
    desktop-loading.jpg / desktop-error.jpg / desktop-success.jpg
    desktop-reduce.jpg   减弱动效完成态
    mobile.jpg           390×844 @2x 完成态
最后打印每个画面收集到的 console error / pageerror;退出码 = 错误条数(0 为干净)。
"""

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--base", default="http://127.0.0.1:5191/docs/design/login-redesign")
    ap.add_argument("--film", default="300,900,1800,3200,5000")
    ap.add_argument("--settle", type=int, default=1600, help="跳过开场后的等待毫秒")
    args = ap.parse_args()

    out = HERE / args.slug / "shots"
    out.mkdir(parents=True, exist_ok=True)
    url = f"{args.base}/{args.slug}/index.html"
    errors: list[str] = []

    def page_for(browser, w, h, dpr=1):
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr)
        page = ctx.new_page()
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        return ctx, page

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=metal", "--enable-gpu-rasterization", "--ignore-gpu-blocklist"])

        # 开场逐帧
        ctx, page = page_for(browser, 1440, 900)
        page.goto(f"{url}?kit=0", wait_until="load")
        t0 = 0
        for ms in sorted(int(x) for x in args.film.split(",") if x):
            page.wait_for_timeout(ms - t0)
            t0 = ms
            page.screenshot(path=str(out / f"film-{ms:05d}.jpg"), type="jpeg", quality=84)
        ctx.close()

        shots = [
            ("desktop", "intro=skip", None),
            ("desktop-password", "intro=skip", "password"),
            ("desktop-loading", "state=loading", None),
            ("desktop-error", "state=error", None),
            ("desktop-success", "state=success", None),
            ("desktop-reduce", "intro=skip&motion=reduce", None),
        ]
        for name, query, action in shots:
            ctx, page = page_for(browser, 1440, 900)
            page.goto(f"{url}?kit=0&{query}", wait_until="load")
            page.wait_for_timeout(args.settle)
            if action == "password":
                page.locator("input[type=password]").first.focus()
                page.wait_for_timeout(1000)
            if name == "desktop-success":
                page.wait_for_timeout(1800)
            page.screenshot(path=str(out / f"{name}.jpg"), type="jpeg", quality=84)
            ctx.close()

        ctx, page = page_for(browser, 390, 844, dpr=2)
        page.goto(f"{url}?kit=0&intro=skip", wait_until="load")
        page.wait_for_timeout(args.settle)
        page.screenshot(path=str(out / "mobile.jpg"), type="jpeg", quality=84)
        ctx.close()
        browser.close()

    print(f"shots -> {out}")
    for e in errors:
        print(e)
    print(f"errors: {len(errors)}")
    return len(errors)


if __name__ == "__main__":
    sys.exit(main())
