#!/usr/bin/env python3
"""把沙箱里抓到的现网页面整理成样页底页(issue #312)。

用法:python3 tools/build.py <快照目录>

快照目录是 Playwright 在隔离栈(v3.69.0)抓的四份文件:
    accounts.body.html   运维 → 账户 整页的 <body>
    drawer.html          单用户活动抽屉(打开态)
    settings-feed.html   设置 → 聚合接口 弹窗
    app.css              document.styleSheets 导出(三张表:两张字体表 + 应用样式)

产物:
    app.html             外壳 + 账户页 + 抽屉 + 设置弹窗(现网 DOM;新增部分由 assets/proto.js 在运行时挂上去)
    assets/app.css       应用样式原样(去掉登录页专用的三条 @font-face)

底页里凡是带真实账号名的地方(账户表、活跃榜、抽屉标题)都在这里清空,由 proto.js 用示意数据重画。
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
VERSION = "1"

REAL_NAMES = ["260914", "Lizylu", "dorami-bot-dev", "testnew", "testtest", "deeplink_test_user", "zztmp_verify", "probe_fresh"]


def cut(src: str, start: int, end: int, repl: str = "") -> str:
    return src[:start] + repl + src[end:]


def main() -> None:
    snap = pathlib.Path(sys.argv[1])
    body = (snap / "accounts.body.html").read_text(encoding="utf-8")
    drawer = (snap / "drawer.html").read_text(encoding="utf-8")
    settings = (snap / "settings-feed.html").read_text(encoding="utf-8")
    css = (snap / "app.css").read_text(encoding="utf-8").split("\n/* ---- sheet ---- */\n")[-1]

    # 1. 只留外壳与管理面:去掉隐藏着的「归档」标签页
    panels = [m.start() for m in re.finditer(r'<div class="tab-panel', body)]
    assert len(panels) == 2, panels
    body = cut(body, panels[0], panels[1])

    # 2. 去掉脚本与 recharts 的量字宽元素;<body> 外壳自己写
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    body = re.sub(r'<span id="recharts_measurement_span".*?</span>', "", body, flags=re.S)
    body = re.sub(r"^<body[^>]*>", "", body.strip())
    body = re.sub(r"</body>\s*</html>\s*$", "", body)

    # 3. 账户表表体清空(proto.js 重画)
    t = body.index('<table class="acct-table is-fixed">')
    a, b = body.index("<tbody>", t) + len("<tbody>"), body.index("</tbody>", t)
    body = cut(body, a, b)

    # 4. 活跃榜图表清空
    t = body.index('aria-label="活跃用户 Top"')
    a = body.index('<section class="surface-card card-pad', t)
    a = body.index(">", a) + 1
    b = body.index("</section>", a)
    body = cut(body, a, b, '<div data-proto="top-chart"></div>')

    # 5. 关着的抽屉换成打开态快照(仍关着,proto.js 按账号填)
    a = body.index('<aside class="ledger-drawer " role="dialog" aria-modal="true" aria-label="用户活动详情"')
    b = body.index("</aside>", a) + len("</aside>")
    drawer = drawer.replace('class="ledger-drawer is-open"', 'class="ledger-drawer " data-proto="drawer"', 1)
    drawer = drawer.replace('aria-hidden="false"', 'aria-hidden="true"', 1)
    drawer = drawer.replace('aria-label="admin · 活动详情"', 'aria-label="用户活动详情"', 1)
    body = cut(body, a, b, drawer)

    # 6. 设置弹窗(默认不显示)
    settings = settings.replace("http://localhost:5174", "https://dorami.example").replace("...DwoENA", "...Qm7xK2")
    body += f'\n<div class="modal-overlay" data-proto="settings" hidden>{settings}</div>\n'

    # 7. 品牌图走仓内路径(从仓库根起静态服务)
    body = body.replace('"/brand/', '"/frontend/public/brand/').replace(" /brand/", " /frontend/public/brand/")

    leftovers = [n for n in REAL_NAMES if n in body]
    assert not leftovers, f"底页里还有真实账号名: {leftovers}"

    css = re.sub(r'@font-face \{[^}]*"/src/assets/fonts/[^}]*\}\s*', "", css)
    assert "/src/assets/" not in css

    html = f"""<!doctype html>
<html lang="zh-CN" data-theme="light" data-palette="porcelain">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>令牌交付计量 · 原型</title>
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
    (HERE / "assets").mkdir(exist_ok=True)
    (HERE / "app.html").write_text(html, encoding="utf-8")
    (HERE / "assets" / "app.css").write_text(css, encoding="utf-8")
    print(f"app.html {len(html):,} bytes · assets/app.css {len(css):,} bytes")


if __name__ == "__main__":
    main()
