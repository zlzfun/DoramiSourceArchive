#!/usr/bin/env python3
"""生成自托管标题字体「Dorami Serif」(思源宋体 / Noto Serif SC,SIL OFL 1.1)。

issue #184(含 #176 W0b):标题与分数用自托管宋体,字体随前端构建同源发出,
不依赖 Google Fonts 等外部字体域名——公司内网打不开外部字体,公网与内网要看到同一套字形。

做法:
1. 取 google/fonts 仓库固定提交里的思源宋体可变字体(下载后校验 sha256);
2. 用 fontTools 实例化出静态字重(默认 600 与 900);
3. 按 scripts/fonts/noto_serif_sc_ranges.json 的 101 组 unicode-range(按字频分片)切 WOFF2,
   覆盖字库全部字符,浏览器只下载页面实际用到的分片;
4. 写出同目录的 dorami-serif.css(每片一条 @font-face + unicode-range)。

产物在 frontend/public/fonts/dorami-serif-<版本>/,Vite 原样拷进 dist,index.html 直接引用;
目录名带版本号,nginx 对 /fonts/ 给长缓存——换字体或字重时把 FONT_VERSION 加一。
字体样式表单独成文件而不并进主样式:两档字重的 unicode-range 声明约 200KB,不该拖累主样式。

运行(fontTools / brotli 不是项目依赖,用 uv 临时带上即可):
    uv run --with fonttools --with brotli python scripts/build_dorami_serif.py
    uv run --with fonttools --with brotli python scripts/build_dorami_serif.py --source /path/NotoSerifSC[wght].ttf

换字重或换字体版本时改下面的常量后重跑;输出目录会先清空再写。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "2e61f4355afd22b801791b0df176065082423b87"  # google/fonts:Noto Serif SC 2.003(2024-08-19)
SOURCE_URL = f"https://github.com/google/fonts/raw/{SOURCE_COMMIT}/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"
LICENSE_URL = f"https://github.com/google/fonts/raw/{SOURCE_COMMIT}/ofl/notoserifsc/OFL.txt"
SOURCE_SHA256 = "050080d9255a86808f2945bffac582b31ef32bc36411ce29563b4961670c66f9"
FAMILY = "Dorami Serif"
SLUG = "dorami-serif"
FONT_VERSION = "v1"
DEFAULT_WEIGHTS = (600, 900)
RANGES_FILE = ROOT / "scripts" / "fonts" / "noto_serif_sc_ranges.json"
OUT_DIR = ROOT / "frontend" / "public" / "fonts" / f"{SLUG}-{FONT_VERSION}"
CSS_FILE = OUT_DIR / f"{SLUG}.css"
CACHE_DIR = Path(tempfile.gettempdir()) / "dorami-font-build"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, tmp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    tmp.replace(dest)


def parse_range(text: str) -> set[int]:
    codepoints: set[int] = set()
    for part in text.split(","):
        part = part.strip().upper().replace("U+", "")
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            codepoints.update(range(int(start, 16), int(end, 16) + 1))
        else:
            codepoints.add(int(part, 16))
    return codepoints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, help="本地的思源宋体可变字体;缺省时下载固定提交并校验 sha256")
    parser.add_argument("--weights", default=",".join(str(w) for w in DEFAULT_WEIGHTS), help="逗号分隔的静态字重")
    args = parser.parse_args()

    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError:
        print("需要 fontTools 与 brotli:uv run --with fonttools --with brotli python scripts/build_dorami_serif.py", file=sys.stderr)
        return 2

    source = args.source
    if source is None:
        source = CACHE_DIR / f"NotoSerifSC-VF-{SOURCE_COMMIT[:12]}.ttf"
        if not source.exists():
            print(f"下载 {SOURCE_URL}")
            fetch(SOURCE_URL, source)
    actual = sha256_of(source)
    if actual != SOURCE_SHA256:
        print(f"源字体 sha256 不符:{actual}(期望 {SOURCE_SHA256})", file=sys.stderr)
        return 1

    weights = [int(w) for w in args.weights.split(",") if w.strip()]
    ranges = json.loads(RANGES_FILE.read_text(encoding="utf-8"))["ranges"]

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    license_path = OUT_DIR / "OFL.txt"
    fetch(LICENSE_URL, license_path)

    faces: list[str] = []
    totals: dict[int, tuple[int, int]] = {}
    for weight in weights:
        static = instancer.instantiateVariableFont(TTFont(source), {"wght": weight}, updateFontNames=True)
        static_path = CACHE_DIR / f"{SLUG}-{weight}.ttf"
        static_path.parent.mkdir(parents=True, exist_ok=True)
        static.save(static_path)
        cmap = set(static.getBestCmap())
        count = size = 0
        for index, text in enumerate(ranges):
            unicodes = sorted(parse_range(text) & cmap)
            if not unicodes:
                continue
            options = subset.Options()
            options.flavor = "woff2"
            options.layout_features = ["*"]
            options.name_IDs = ["*"]
            options.hinting = False
            options.notdef_outline = False
            font = TTFont(static_path)
            subsetter = subset.Subsetter(options)
            subsetter.populate(unicodes=unicodes)
            subsetter.subset(font)
            name = f"{SLUG}-{weight}-{index:03d}.woff2"
            font.flavor = "woff2"
            font.save(OUT_DIR / name)
            count += 1
            size += (OUT_DIR / name).stat().st_size
            faces.append(
                f'@font-face{{font-family:"{FAMILY}";font-style:normal;font-weight:{weight};font-display:swap;'
                f'src:url("./{name}") format("woff2");unicode-range:{text};}}'
            )
        totals[weight] = (count, size)

    header = [
        "/* 自动生成,勿手改:scripts/build_dorami_serif.py(issue #184,含 #176 W0b)。",
        f"   {FAMILY} = 思源宋体 Noto Serif SC 2.003(SIL OFL 1.1,许可见同目录 OFL.txt),",
        f"   来源 google/fonts@{SOURCE_COMMIT[:12]},sha256 {SOURCE_SHA256[:16]}…;",
        f"   静态字重 {', '.join(str(w) for w in weights)},每档按字频切 {len(ranges)} 组 unicode-range,覆盖全部字符。",
        "   同源发出、不请求外部字体域名;浏览器只下载页面实际用到的分片。 */",
    ]
    CSS_FILE.write_text("\n".join(header + faces) + "\n", encoding="utf-8")
    for weight, (count, size) in totals.items():
        print(f"{weight}: {count} 片,{size / 1024 / 1024:.2f} MB")
    print(f"写出 {CSS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
