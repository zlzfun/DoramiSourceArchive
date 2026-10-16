#!/usr/bin/env python3
"""生成自托管西文衬线「Dorami Latin Serif」(Source Serif 4,SIL OFL 1.1)。

issue #184 目检返修:宋体(Dorami Serif)排西文时,弯撇号与弯引号按中文全角排,「Life’s」成了「Life’ s」;
原「夜航」样页的英文原标题用 Source Serif 4 斜体。英文文本(lang="en")的宋体角色换成本字体在前的栈,
与 Dorami Serif 一样随前端构建同源发出,不依赖外部字体域名(公司内网打不开 Google Fonts)。

做法:
1. 取 google/fonts 固定提交里的 Source Serif 4 正体 / 斜体可变字体(下载后校验 sha256);
2. 用 fontTools 实例化静态样式,光学尺寸按用途定死:正体 600 排 18–30px 的标题(opsz 24),
   正体 900 只给早报海报头条(opsz 60),斜体 400 排 15–18px 的英文原标题(opsz 16);
3. 按 Google Fonts 的 latin / latin-ext 两组 unicode-range 切 WOFF2;
4. 字体内部名称改为 Dorami Latin Serif:原字体声明了保留字体名「Source」,OFL 1.1 第 3 条不许改动版
   (子集化、实例化都算改动)以保留名作主名;版权、商标、许可字段原样保留;
5. 写出同目录的 dorami-latin.css(每样式每片一条 @font-face)与许可文件 LICENSE.md。

产物在 frontend/public/fonts/dorami-latin-<版本>/,index.html 直接引用;目录名带版本号,nginx 对 /fonts/ 给长缓存——
换样式或字体版本时把 FONT_VERSION 加一。

运行(fontTools / brotli 不是项目依赖,用 uv 临时带上即可):
    uv run --with fonttools --with brotli python scripts/build_dorami_latin.py
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "2e61f4355afd22b801791b0df176065082423b87"  # google/fonts,与 Dorami Serif 同一提交;Source Serif 4 4.004
SOURCE_BASE = f"https://github.com/google/fonts/raw/{SOURCE_COMMIT}/ofl/sourceserif4"
SOURCES = {
    "roman": (f"{SOURCE_BASE}/SourceSerif4%5Bopsz%2Cwght%5D.ttf",
              "97b2d4da6e3cb494b5a1e66ae176914d852ccabef49e0c02c0df25f3e39aca0b"),
    "italic": (f"{SOURCE_BASE}/SourceSerif4-Italic%5Bopsz%2Cwght%5D.ttf",
               "15fbc7e4679489a501998c3669272637a6646388ef7e4bd77eebb5bf967a1f42"),
}
# google/fonts 这一目录没带许可文件;取 Adobe 官方仓库与字体同版本(4.004)的发布标签
LICENSE_URL = "https://raw.githubusercontent.com/adobe-fonts/source-serif/4.004R/LICENSE.md"
FAMILY = "Dorami Latin Serif"
SLUG = "dorami-latin"
FONT_VERSION = "v1"
# (源, CSS font-style, 字重, 光学尺寸, 样式名, 文件名里的样式段)
STYLES = (
    ("roman", "normal", 600, 24, "SemiBold", "600"),
    ("roman", "normal", 900, 60, "Black", "900"),
    ("italic", "italic", 400, 16, "Italic", "400i"),
)
# Google Fonts 的 latin / latin-ext 分片口径
SUBSETS = {
    "latin": (
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
        "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"
    ),
    "latin-ext": (
        "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, "
        "U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF"
    ),
}
# 改名时覆写 / 清掉的 name 记录:族名、样式名、唯一标识、全名、PostScript 名、排版族名与样式名、可变字体前缀
RENAMED_IDS = (1, 2, 3, 4, 6, 16, 17, 18, 21, 22, 25)
# 子集化后保留的名称:版权、族名 / 样式名 / 标识 / 全名 / 版本 / PostScript 名、商标、厂商、设计师、链接、许可、排版族名
KEPT_NAME_IDS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17]
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


def rename(font, style_name: str, italic: bool) -> None:
    """内部名称换成 Dorami Latin Serif(OFL 保留字体名「Source」不得作改动版主名);其余字段原样。"""
    table = font["name"]
    version = (table.getDebugName(5) or "").split(";")[0].replace("Version ", "").strip() or "1.0"
    ps_name = f"{FAMILY.replace(' ', '')}-{style_name}"
    full_name = f"{FAMILY} {style_name}"
    # 旧式族名只分「常规 / 斜体 / 粗体 / 粗斜体」四格,其余字重并进族名;排版族名(16/17)给完整样式
    legacy_family = FAMILY if style_name in ("Regular", "Italic") else f"{FAMILY} {style_name}"
    legacy_style = "Italic" if italic else "Regular"
    for name_id in RENAMED_IDS:
        table.removeNames(nameID=name_id)
    records = {
        1: legacy_family,
        2: legacy_style,
        3: f"{version};DORAMI;{ps_name}",
        4: full_name,
        6: ps_name,
        16: FAMILY,
        17: style_name,
    }
    for name_id, value in records.items():
        table.setName(value, name_id, 3, 1, 0x409)


def assert_renamed(font, label: str) -> None:
    """版权(0)与商标(7)声明里提到 Source 是应当的;其余任何名称记录都不得再带保留字体名。"""
    leftover = sorted({r.nameID for r in font["name"].names if r.nameID not in (0, 7) and "Source" in r.toUnicode()})
    if leftover:
        raise SystemExit(f"{label} 改名后仍有保留字体名:nameID {leftover}")


def main() -> int:
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError:
        print("需要 fontTools 与 brotli:uv run --with fonttools --with brotli python scripts/build_dorami_latin.py", file=sys.stderr)
        return 2

    sources: dict[str, Path] = {}
    for key, (url, expected) in SOURCES.items():
        path = CACHE_DIR / f"SourceSerif4-{key}-{SOURCE_COMMIT[:12]}.ttf"
        if not path.exists():
            print(f"下载 {url}")
            fetch(url, path)
        actual = sha256_of(path)
        if actual != expected:
            print(f"{key} 源字体 sha256 不符:{actual}(期望 {expected})", file=sys.stderr)
            return 1
        sources[key] = path

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    license_path = OUT_DIR / "LICENSE.md"
    fetch(LICENSE_URL, license_path)
    # 上游许可文件是 CRLF;内容逐字保留,只统一成 LF(仓库文本一律 LF,git diff --check 不报行尾空白)
    license_path.write_bytes(license_path.read_bytes().replace(b"\r\n", b"\n"))

    faces: list[str] = []
    total = 0
    for source_key, css_style, weight, opsz, style_name, file_style in STYLES:
        static = instancer.instantiateVariableFont(
            TTFont(sources[source_key]), {"wght": weight, "opsz": opsz}, updateFontNames=False
        )
        if "STAT" in static:
            del static["STAT"]
        rename(static, style_name, italic=css_style == "italic")
        static_path = CACHE_DIR / f"{SLUG}-{file_style}.ttf"
        static.save(static_path)
        cmap = set(static.getBestCmap())
        for subset_name, text in SUBSETS.items():
            unicodes = sorted(parse_range(text) & cmap)
            options = subset.Options()
            options.flavor = "woff2"
            options.layout_features = ["*"]
            # 只留声明类名称;被其它表(如 GSUB 风格集)引用的高位名称由子集器自动保留,命名实例与轴名随 fvar 一起去掉
            options.name_IDs = KEPT_NAME_IDS
            options.name_languages = [0x409]
            options.notdef_outline = False
            font = TTFont(static_path)
            subsetter = subset.Subsetter(options)
            subsetter.populate(unicodes=unicodes)
            subsetter.subset(font)
            name = f"{SLUG}-{file_style}-{subset_name}.woff2"
            assert_renamed(font, name)
            font.flavor = "woff2"
            font.save(OUT_DIR / name)
            size = (OUT_DIR / name).stat().st_size
            total += size
            print(f"{name}: {len(unicodes)} 字符,{size / 1024:.1f} KB")
            faces.append(
                f'@font-face{{font-family:"{FAMILY}";font-style:{css_style};font-weight:{weight};font-display:swap;'
                f'src:url("./{name}") format("woff2");unicode-range:{text};}}'
            )

    header = [
        "/* 自动生成,勿手改:scripts/build_dorami_latin.py(issue #184 目检返修)。",
        f"   {FAMILY} = Source Serif 4 4.004(Adobe,SIL OFL 1.1,许可见同目录 LICENSE.md;",
        "   原字体声明保留字体名「Source」,改动版按 OFL 第 3 条改名),来源 google/fonts@"
        f"{SOURCE_COMMIT[:12]};",
        "   正体 600 / 900、斜体 400 三个静态实例,各切 latin / latin-ext 两片。同源发出、不请求外部字体域名。 */",
    ]
    CSS_FILE.write_text("\n".join(header + faces) + "\n", encoding="utf-8")
    print(f"共 {total / 1024:.1f} KB;写出 {CSS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
