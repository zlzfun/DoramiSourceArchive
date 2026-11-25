#!/usr/bin/env python3
"""把沙箱里抓到的现网页面整理成样页底页(issue #344)。

用法:python3 tools/build.py <快照目录>

快照目录是 Playwright 在隔离栈(主干 46cc0c5d,空库 + 虚构账号的示意数据)抓的四份文件:
    ingest.body.html        运维 → 自定源与收录 整页的 <body>
    fetch.nova.body.html    源管理 · 社交,选中一个收录临时源
    fetch.openai.body.html  源管理 · 社交,选中一个预设 X 源
    app.css                 document.styleSheets 导出(三张表:两张字体表 + 应用样式)

产物:
    tokens.html / sources.html / sources-x.html   外壳 + 当前页(现网 DOM;新增部分由 assets/proto.js 在运行时挂上去)
    assets/app.css                                应用样式原样(去掉登录页专用的三条 @font-face)
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
VERSION = "1"

PAGES = (
    ("tokens", "ingest.body.html", "收录令牌"),
    ("sources", "fetch.nova.body.html", "源管理 · 只推送的源"),
    ("sources-x", "fetch.openai.body.html", "源管理 · 又抓又推的源"),
)


def div_end(src: str, start: int) -> int:
    """从 ``start`` 处的 <div 起,找到与它配对的 </div> 之后的位置。"""
    depth = 0
    for m in re.finditer(r"<div\b|</div>", src[start:]):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            return start + m.end()
    raise AssertionError("div 不配对")


def clean(body: str) -> str:
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    body = re.sub(r'<span id="recharts_measurement_span".*?</span>', "", body, flags=re.S)
    body = re.sub(r"^<body[^>]*>", "", body.strip())
    body = re.sub(r"</body>\s*$", "", body)
    # 只留当前页:去掉隐藏着的其它标签页
    while True:
        m = re.search(r'<div class="tab-panel is-off"', body)
        if not m:
            break
        body = body[: m.start()] + body[div_end(body, m.start()):]
    assert len(re.findall(r'<div class="tab-panel"', body)) == 1
    # 品牌图走仓内路径(从仓库根起静态服务)
    return body.replace('"/brand/', '"/frontend/public/brand/').replace(" /brand/", " /frontend/public/brand/")


def main() -> None:
    snap = pathlib.Path(sys.argv[1])
    css = (snap / "app.css").read_text(encoding="utf-8").split("\n/* ---- sheet ---- */\n")[-1]
    css = re.sub(r'@font-face \{[^}]*"/src/assets/fonts/[^}]*\}\s*', "", css)
    assert "/src/assets/" not in css
    (HERE / "assets").mkdir(exist_ok=True)
    (HERE / "assets" / "app.css").write_text(css, encoding="utf-8")
    for key, source, title in PAGES:
        body = clean((snap / source).read_text(encoding="utf-8"))
        html = f"""<!doctype html>
<html lang="zh-CN" data-theme="light" data-palette="porcelain" data-page="{key}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>推送绑定 · {title}</title>
<link rel="stylesheet" href="/frontend/public/fonts/dorami-serif-v1/dorami-serif.css">
<link rel="stylesheet" href="/frontend/public/fonts/dorami-latin-v1/dorami-latin.css">
<link rel="stylesheet" href="assets/app.css?v={VERSION}">
<link rel="stylesheet" href="assets/proto.css?v={VERSION}">
</head>
<body>
{body}
<script src="assets/proto.js?v={VERSION}"></script>
</body>
</html>
"""
        (HERE / f"{key}.html").write_text(html, encoding="utf-8")
        print(f"{key}.html {len(html):,} bytes")
    print(f"assets/app.css {len(css):,} bytes")


if __name__ == "__main__":
    main()
