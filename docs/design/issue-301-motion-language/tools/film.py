"""动效样页自查工具(不入产品,只给做样页的人用)。

    film.py smoke  --dirs 0,a,b,c,d [--scenes open,nav] [--theme light]
        每个方向把对照页的场景各跑一遍(假光标脚本,不循环),收集控制台报错,末帧存图。
    film.py strip  --dir a --hash light.article --do "click:.list-b article:nth-of-type(3)" \
                   [--do ...] [--span 500] [--frames 12] [--speed 0.1] [--crop x,y,w,h] --out DIR
        真鼠标依次执行 --do(click / hover / scroll:选择器:top / key:j / boot / wait:ms),
        最后一个动作之后按动画时间均匀取帧(span 毫秒 ÷ speed = 实际等待),拼成一张连拍图 strip.png。

跑法:在仓库根起 `python3 -m http.server 8765 --bind 127.0.0.1`,再用主仓 .venv 的 python 跑本脚本。"""
import argparse
import asyncio
import json
import pathlib
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765/docs/design/issue-301-motion-language/app.html"
W, H = 1440, 810


async def smoke(args):
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    bad = 0
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for d in args.dirs.split(","):
            pg = await b.new_page(viewport={"width": W, "height": H})
            logs = []
            pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            pg.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
            await pg.goto(f"{BASE}?dir={d}&p={args.p}#{args.theme}.brief.bare")
            await pg.wait_for_function("window.M && window.__proto")
            scenes = args.scenes.split(",") if args.scenes else await pg.evaluate("M.sceneOrder")
            for s in scenes:
                n0 = len(logs)
                try:
                    await pg.evaluate("(s) => M.demo.play(s, { loop: false })", s)
                except Exception as e:  # noqa: BLE001
                    logs.append(f"exception: {e}")
                await pg.wait_for_timeout(300)
                await pg.screenshot(path=str(out / f"smoke-{d}-{s}.png"))
                new = logs[n0:]
                bad += len(new)
                print(f"[{d}] {s:7s} {'ok' if not new else 'ISSUES'}")
                for ln in new:
                    print("      ", ln[:300])
            await pg.close()
        await b.close()
    print("issues:", bad)
    return 1 if bad else 0


async def strip(args):
    from PIL import Image, ImageDraw

    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    crop = [int(v) for v in args.crop.split(",")] if args.crop else [0, 0, W, H]
    clip = {"x": crop[0], "y": crop[1], "width": crop[2], "height": crop[3]}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": H})
        logs = []
        pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        extra = f"&motion=reduce" if args.reduce else ""
        await pg.goto(f"{BASE}?dir={args.dir}&speed={args.speed}&p={args.p}{extra}#{args.hash}.bare")
        await pg.wait_for_function("window.M && window.__proto")
        await pg.wait_for_timeout(args.settle)
        acts = args.do or []
        for i, a in enumerate(acts):
            op, _, rest = a.partition(":")
            last = i == len(acts) - 1
            if op == "wait":
                await pg.wait_for_timeout(int(rest))
            elif op == "boot":
                await pg.evaluate("window.__proto.reboot()")
            elif op == "key":
                await pg.keyboard.press(rest)
            elif op == "hover":
                await pg.hover(rest)
            elif op == "click":
                await pg.hover(rest)
                await pg.wait_for_timeout(120)
                await pg.mouse.down()
                await pg.wait_for_timeout(60)
                await pg.mouse.up()
            elif op == "scroll":
                sel, _, top = rest.rpartition(":")
                await pg.evaluate("([s, t]) => document.querySelector(s).scrollTo({ top: t, behavior: 'smooth' })", [sel, int(top)])
            if not last:
                await pg.wait_for_timeout(args.gap)
        gap = args.span / args.speed / max(1, args.frames - 1)
        frames = []
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for i in range(args.frames):
            target = t0 + i * gap / 1000
            now = loop.time()
            if target > now:
                await asyncio.sleep(target - now)
            f = out / f"f{i:02d}.png"
            await pg.screenshot(path=str(f), clip=clip)
            frames.append((f, (loop.time() - t0) * 1000 * args.speed))
        await b.close()
    cols = args.cols
    ims = [Image.open(f) for f, _ in frames]
    scale = min(1.0, args.cell / ims[0].width)
    tw, th = int(ims[0].width * scale), int(ims[0].height * scale)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 6, rows * (th + 22) + 6), (30, 30, 34))
    dr = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        x = 6 + (i % cols) * (tw + 6); y = 6 + (i // cols) * (th + 22)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
        dr.text((x + 2, y + th + 4), f"{frames[i][1]:.0f} ms", fill=(210, 210, 220))
    sheet.save(out / "strip.png")
    print(json.dumps({"strip": str(out / "strip.png"), "frames": len(ims), "logs": logs[:8]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke"); s.add_argument("--dirs", default="0,a,b,c,d"); s.add_argument("--scenes", default=""); s.add_argument("--theme", default="light"); s.add_argument("--p", default=""); s.add_argument("--out", required=True)
    t = sub.add_parser("strip")
    t.add_argument("--dir", required=True); t.add_argument("--hash", default="light.article"); t.add_argument("--do", action="append")
    t.add_argument("--span", type=float, default=500); t.add_argument("--frames", type=int, default=12); t.add_argument("--speed", type=float, default=0.1)
    t.add_argument("--crop", default=""); t.add_argument("--cols", type=int, default=4); t.add_argument("--cell", type=int, default=460)
    t.add_argument("--gap", type=int, default=900); t.add_argument("--settle", type=int, default=900); t.add_argument("--reduce", action="store_true"); t.add_argument("--p", default=""); t.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.exit(asyncio.run(smoke(args) if args.cmd == "smoke" else strip(args)))


if __name__ == "__main__":
    main()
