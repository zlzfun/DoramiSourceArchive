"""录帧连拍(CDP screencast,拍得到 View Transitions 的中间帧)。
cast.py <名字> <dir> <hash> <span_ms> <crop x,y,w,h> <动作> [<动作>…]   环境变量 SPEED(默认 0.2)、FRAMES(默认 8)、CELL(默认 460)、OUT(输出目录)、P(在场选项)
动作:click:<sel> / hover:<sel> / boot / wait:<ms> / key:<k>;最后一个动作之后开始取帧。"""
import asyncio
import base64
import io
import os
import pathlib
import sys

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765/docs/design/issue-301-motion-language/app.html"
OUT = pathlib.Path(os.environ.get("OUT", "/tmp/motion-cast"))
W, H = 1440, 810


async def main(name, d, h, span, crop, acts):
    speed = float(os.environ.get("SPEED", "0.2")); n = int(os.environ.get("FRAMES", "8")); cell = int(os.environ.get("CELL", "460"))
    x, y, w, hh = [int(v) for v in crop.split(",")]
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": W, "height": H})
        pg = await ctx.new_page()
        logs = []
        pg.on("console", lambda m: logs.append(m.text[:200]) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: logs.append(str(e)[:300]))
        await pg.goto(f"{BASE}?dir={d}&speed={speed}&p={os.environ.get('P', '')}#{h}.bare")
        await pg.wait_for_function("window.M && window.__proto")
        await pg.wait_for_timeout(900)
        cdp = await ctx.new_cdp_session(pg)
        frames = []
        loop = asyncio.get_event_loop()

        async def ack(sid):
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": sid})
            except Exception:  # noqa: BLE001
                pass

        def on_frame(ev):
            frames.append((loop.time(), ev["data"]))
            asyncio.ensure_future(ack(ev["sessionId"]))

        cdp.on("Page.screencastFrame", on_frame)
        t0 = None
        for i, a in enumerate(acts):
            op, _, rest = a.partition(":")
            last = i == len(acts) - 1
            if op in ("click", "hover"):
                await pg.hover(rest)
                await pg.wait_for_timeout(150)
            if last:
                await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 82, "everyNthFrame": 1})
                await pg.wait_for_timeout(120)
                t0 = loop.time()
            if op == "wait":
                await pg.wait_for_timeout(int(rest))
            elif op == "boot":
                await pg.evaluate("window.__proto.reboot()")
            elif op == "key":
                await pg.keyboard.press(rest)
            elif op == "click":
                await pg.mouse.down()
                await pg.mouse.up()
            if not last:
                await pg.wait_for_timeout(900)
        await pg.wait_for_timeout(int(span / speed) + 250)
        await cdp.send("Page.stopScreencast")
        await b.close()
    got = [(t - t0, data) for t, data in frames if t >= t0 - 0.02]
    if not got:
        print(name, "NO FRAMES", logs[:4]); return
    picks = []
    for i in range(n):
        want = (span / speed / 1000) * i / max(1, n - 1)
        picks.append(min(got, key=lambda f: abs(f[0] - want)))
    ims = [Image.open(io.BytesIO(base64.b64decode(dta))).convert("RGB") for _, dta in picks]
    sx = ims[0].width / W
    ims = [im.crop((int(x * sx), int(y * sx), int((x + w) * sx), int((y + hh) * sx))) for im in ims]
    sc = min(1.0, cell / ims[0].width); tw, th = int(ims[0].width * sc), int(ims[0].height * sc)
    cols = 4; rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 6, rows * (th + 22) + 6), (30, 30, 34)); dr = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        px = 6 + (i % cols) * (tw + 6); py = 6 + (i // cols) * (th + 22)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (px, py))
        dr.text((px + 2, py + th + 4), f"{picks[i][0] * 1000 * speed:.0f} ms", fill=(210, 210, 220))
    (OUT / name).mkdir(parents=True, exist_ok=True)
    sheet.save(OUT / name / "cast.png")
    print(name, "frames", len(got), "logs", logs[:4])


a = sys.argv
asyncio.run(main(a[1], a[2], a[3], float(a[4]), a[5], a[6:]))
