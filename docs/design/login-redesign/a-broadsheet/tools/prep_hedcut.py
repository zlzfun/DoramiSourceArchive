"""方向 A · 晨刊:把 codex 生成的 hedcut 点刻肖像(1254² RGB,约 2.6 MB / 张)压成页面用的小图。

    python tools/prep_hedcut.py <原图目录>   # 目录里放 hedcut-dorami.png 与 hedcut-dorami-cover-eyes.png

点刻图本质是黑点白底:裁到墨迹外接框(留约 30px)→ 缩到 600×686 → 量化成 4 级灰
(0/85/170/255,2-bit 调色板 PNG)。肉眼与原图几乎无差,单张约 60 KB;WebP / AVIF 同体量下会把点抹糊。
"""
import pathlib
import sys

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent.parent
CROP = (128, 28, 1164, 1212)  # 两张图墨迹外接框一致:x 157–1135, y 55–1184
SIZE = (600, 686)
LEVELS = np.array([0, 85, 170, 255], float)


def squeeze(src: pathlib.Path, dst: pathlib.Path) -> None:
    im = Image.open(src).convert("L").crop(CROP).resize(SIZE, Image.LANCZOS)
    a = np.asarray(im, float)
    idx = np.abs(a[..., None] - LEVELS).argmin(-1).astype(np.uint8)
    out = Image.fromarray(idx, "P")
    pal = [int(v) for v in LEVELS for _ in range(3)]
    out.putpalette(pal + [0] * (768 - len(pal)))
    out.save(dst, optimize=True)
    print(f"{src.name} -> {dst.relative_to(HERE)} ({dst.stat().st_size} bytes)")


def main() -> None:
    src_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "assets"
    squeeze(src_dir / "hedcut-dorami.png", HERE / "assets/hedcut-a.png")
    squeeze(src_dir / "hedcut-dorami-cover-eyes.png", HERE / "assets/hedcut-b.png")


if __name__ == "__main__":
    main()
