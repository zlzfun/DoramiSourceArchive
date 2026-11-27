#!/usr/bin/env python3
"""把沙箱里抓到的现网页面整理成样页底页(issue #72)。

用法:python3 tools/build.py <快照目录>

快照目录是 Playwright 在隔离栈(主干 b4836bac,开发库副本)抓的两份文件:
    fetch.sys.body.html   停在「源管理 · 系统」时整页的 <body>;此前依次打开过「AI 日报」「任务与运行」,
                          三个页签的面板都挂在里面
    app.css               document.styleSheets 导出(字体表 + 应用样式)

产物:
    app.html         外壳 + 三个页签的面板(现网 DOM;各方向的改动由 assets/proto.js 在运行时做)
    assets/app.css   应用样式原样(去掉登录页专用的 @font-face)
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
VERSION = "3"
KEEP = {"nodes-shell": "fetch", "runs-shell": "runs", "integ-page": "brief"}


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
    # 只留三个要用的页签面板,各自标上名字;其余(知识台账)去掉
    out, pos, seen = [], 0, []
    for m in re.finditer(r'<div class="tab-panel[^"]*"><div class="([\w-]+)', body):
        if m.start() < pos:
            continue
        end = div_end(body, m.start())
        out.append(body[pos:m.start()])
        name = KEEP.get(m.group(1))
        if name:
            seen.append(name)
            panel = body[m.start():end]
            out.append(re.sub(r'^<div class="tab-panel[^"]*"', f'<div class="tab-panel is-off" data-tab="{name}"', panel))
        pos = end
    out.append(body[pos:])
    assert sorted(seen) == ["brief", "fetch", "runs"], seen
    body = "".join(out)
    # 品牌图走仓内路径(从仓库根起静态服务)
    return body.replace('"/brand/', '"/frontend/public/brand/').replace(" /brand/", " /frontend/public/brand/")


def main() -> None:
    snap = pathlib.Path(sys.argv[1])
    css = (snap / "app.css").read_text(encoding="utf-8").split("\n/* ---- sheet ---- */\n")[-1]
    css = re.sub(r'@font-face \{[^}]*"/src/assets/fonts/[^}]*\}\s*', "", css)
    assert "/src/assets/" not in css
    (HERE / "assets").mkdir(exist_ok=True)
    (HERE / "assets" / "app.css").write_text(css, encoding="utf-8")
    body = clean((snap / "fetch.sys.body.html").read_text(encoding="utf-8"))
    html = f"""<!doctype html>
<html lang="zh-CN" data-theme="light" data-palette="porcelain">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 产出 · 样页</title>
<link rel="stylesheet" href="/frontend/public/fonts/dorami-serif-v1/dorami-serif.css">
<link rel="stylesheet" href="/frontend/public/fonts/dorami-latin-v1/dorami-latin.css">
<link rel="stylesheet" href="assets/app.css?v={VERSION}">
<link rel="stylesheet" href="assets/proto.css?v={VERSION}">
<link rel="stylesheet" href="assets/n.css?v={VERSION}">
</head>
<body>
{body}
<script src="assets/data.js?v={VERSION}"></script>
<script src="assets/proto.js?v={VERSION}"></script>
</body>
</html>
"""
    (HERE / "app.html").write_text(html, encoding="utf-8")
    print(f"app.html {len(html):,} bytes")
    print(f"assets/app.css {len(css):,} bytes")


if __name__ == "__main__":
    main()
