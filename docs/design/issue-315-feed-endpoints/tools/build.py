#!/usr/bin/env python3
"""把沙箱里抓到的现网设置弹窗整理成样页底页(issue #315)。

用法:python3 tools/build.py <快照目录>

快照目录是 Playwright 在隔离栈抓的两份文件(抓取时的代码 = issue #312 分支,即令牌下已有「最近调用」那一行):
    settings-feed.html   设置 → 聚合接口 的遮罩 + 弹窗(outerHTML)
    app.css              document.styleSheets 导出(两张字体表 + 应用样式)

产物:
    app.html             现网弹窗的 DOM;方向 A / B 的卡片正文由 assets/proto.js 在运行时换上去
    assets/app.css       应用样式原样(去掉登录页专用的 @font-face)

弹窗后面的页面没有放:现网那里是被遮罩压暗、虚化的阅读器,看不出内容,这里是一块空底。
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
VERSION = "2"


def main() -> None:
    snap = pathlib.Path(sys.argv[1])
    modal = (snap / "settings-feed.html").read_text(encoding="utf-8")
    css = (snap / "app.css").read_text(encoding="utf-8").split("\n/* ---- sheet ---- */\n")[-1]

    # 沙箱地址、令牌尾号、使用情况换成示意值
    modal = modal.replace("http://localhost:5174", "https://dorami.example")
    modal, n = re.subn(r'(<span class="token-val"[^>]*>)[^<]*(</span>)', r"\1...Qm7xK2\2", modal)
    assert n == 1, n
    modal, n = re.subn(r'(<div class="token-meta">)[^<]*(</div>)', r"\1签发于 2026-06-17 · 最近调用 11-20 09:12 · 近 30 天 214 次\2", modal)
    assert n == 1, n
    assert "localhost" not in modal and "127.0.0.1" not in modal

    css = re.sub(r'@font-face \{[^}]*"/src/assets/fonts/[^}]*\}\s*', "", css)
    assert "/src/assets/" not in css

    html = f"""<!doctype html>
<html lang="zh-CN" data-theme="light" data-palette="porcelain">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>聚合接口列全 · 原型</title>
<link rel="stylesheet" href="/frontend/public/fonts/dorami-serif-v1/dorami-serif.css">
<link rel="stylesheet" href="/frontend/public/fonts/dorami-latin-v1/dorami-latin.css">
<link rel="stylesheet" href="assets/app.css?v={VERSION}">
<link rel="stylesheet" href="assets/proto.css?v={VERSION}">
</head>
<body>
{modal}
<script src="assets/proto.js?v={VERSION}"></script>
</body>
</html>
"""
    (HERE / "assets").mkdir(exist_ok=True)
    (HERE / "app.html").write_text(html, encoding="utf-8")
    (HERE / "assets" / "app.css").write_text(css, encoding="utf-8")
    print(f"app.html {len(html):,} bytes · assets/app.css {len(css):,} bytes")


if __name__ == "__main__":
    main()
