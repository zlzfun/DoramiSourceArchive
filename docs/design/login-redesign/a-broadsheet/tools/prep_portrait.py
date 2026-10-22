"""方向 A · 晨刊:把品牌吉祥物(正常 / 捂眼)抠成同一构图的一对网点源图。

输出 assets/portrait-pair.png(两张并排、等宽,高 256,左 = 正常,右 = 捂眼,RGBA 透明底),
页面在 Canvas 里按明度做双色(墨 + 靛)网点。只依赖 Pillow + numpy:
    python tools/prep_portrait.py
步骤:按亮紫贴纸描边从四边泛洪出外部 → 去掉贴近外部的描边带 → 只留最大连通块
→ 以黄色头罩的外接框对齐两张图(捂眼图原画偏大约 8%)→ 缩到 256 并排打包。
"""
import pathlib
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

HERE = pathlib.Path(__file__).resolve().parent.parent
BRAND = HERE.parents[3] / "frontend/public/brand"
WORK = 512


def rim_mask(a):
    r, g, b = (a[..., i].astype(int) for i in range(3))
    return (b > 180) & (r > 90) & (r < 215) & (g < 140) & (b - g > 110)


def flood_outside(rim):
    h, w = rim.shape
    out = np.zeros((h, w), bool)
    q = deque()
    for y in range(h):
        for x in range(w):
            if (y in (0, h - 1) or x in (0, w - 1)) and not rim[y, x]:
                out[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not out[ny, nx] and not rim[ny, nx]:
                out[ny, nx] = True
                q.append((ny, nx))
    return out


def near(mask, steps):
    cur = mask.copy()
    for _ in range(steps):
        nb = cur.copy()
        nb[1:, :] |= cur[:-1, :]
        nb[:-1, :] |= cur[1:, :]
        nb[:, 1:] |= cur[:, :-1]
        nb[:, :-1] |= cur[:, 1:]
        cur = nb
    return cur


def largest_component(m):
    h, w = m.shape
    lab = np.zeros((h, w), np.int32)
    best, best_n, cur = 0, 0, 0
    for y0, x0 in zip(*np.nonzero(m)):
        if lab[y0, x0]:
            continue
        cur += 1
        n = 0
        q = deque([(y0, x0)])
        lab[y0, x0] = cur
        while q:
            y, x = q.popleft()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = cur
                    q.append((ny, nx))
        if n > best_n:
            best, best_n = cur, n
    return lab == best


def cutout(path):
    img = Image.open(path).convert("RGB").resize((WORK, WORK), Image.LANCZOS)
    a = np.array(img)
    rim = rim_mask(a)
    out = flood_outside(rim)
    keep = ~out & ~(rim & near(out, 12))
    keep = np.array(Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3))) > 0
    keep = largest_component(keep)
    return np.dstack([a, (keep * 255).astype(np.uint8)])


def hood_box(im):
    r, g, b, al = (im[..., i].astype(int) for i in range(4))
    ys, xs = np.nonzero((al > 0) & (r > 215) & (g > 140) & (b < 110))
    return xs.min(), xs.max(), ys.min(), ys.max()


def main():
    base = cutout(BRAND / "dorami-logo-512.png")
    cover = cutout(BRAND / "dorami-logo-cover-eyes-256.jpg")
    bx0, bx1, by0, by1 = hood_box(base)
    cx0, cx1, cy0, cy1 = hood_box(cover)
    s = (bx1 - bx0) / (cx1 - cx0)
    tx = (bx0 + bx1) / 2 - (cx0 + cx1) / 2 * s
    ty = (by0 + by1) / 2 - (cy0 + cy1) / 2 * s
    cov = Image.fromarray(cover, "RGBA").transform(
        (WORK, WORK), Image.AFFINE, (1 / s, 0, -tx / s, 0, 1 / s, -ty / s), resample=Image.BICUBIC
    )
    # 裁到两张图的并集外接框(留 4%),让角色撑满肖像栏;两张同裁,对位不变
    A = Image.fromarray(base, "RGBA")
    boxes = [A.getchannel("A").getbbox(), cov.getchannel("A").getbbox()]
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
    m = round(0.04 * max(x1 - x0, y1 - y0))
    crop = (max(0, x0 - m), max(0, y0 - m), min(WORK, x1 + m), min(WORK, y1 + m))
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    h = 256
    w = round(h * cw / ch)
    pair = Image.new("RGBA", (w * 2, h), (0, 0, 0, 0))
    pair.paste(A.crop(crop).resize((w, h), Image.LANCZOS), (0, 0))
    pair.paste(cov.crop(crop).resize((w, h), Image.LANCZOS), (w, 0))
    out = HERE / "assets/portrait-pair.png"
    # 像素画本身色数很少:八叉树量化到 96 色(保留 alpha)足够做网点源,体量约降到三分之一
    pair.quantize(colors=96, method=Image.Quantize.FASTOCTREE).save(out, optimize=True)
    print(f"scale={s:.3f} shift=({tx:.1f},{ty:.1f}) -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
