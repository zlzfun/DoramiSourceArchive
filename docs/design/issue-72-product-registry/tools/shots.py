#!/usr/bin/env python3
"""自查:逐个状态打开原型截图,收集控制台报错、失败请求与溢出。

用法:python3 tools/shots.py <静态服务地址> <输出目录> [只跑名字里含这段的]
静态服务要从仓库根起(字体与品牌图引用 frontend/public/)。
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") + "/docs/design/issue-72-product-registry"
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
ONLY = sys.argv[3] if len(sys.argv) > 3 else ""

B = "app.html?dir=b&page=brief"
C1 = f"{B}&lay=tiles&det=pipe&hist=fold"
SHOTS = [
    ("now-brief", "app.html?dir=now&page=brief", 900),
    # 第二轮:名册三种放法 × 详情两种排法(r2/index.html 还在用)
    ("r2-left-cols", f"{B}&lay=left&det=cols", 900),
    ("r2-tiles-pipe", f"{B}&lay=tiles&det=pipe", 900),
    ("r2-table-cols", f"{B}&lay=table&det=cols", 900),
    ("r2-left-pipe-narrow", f"{B}&lay=left&det=pipe", 900, 1280),
    # C1 往期收起
    ("c1", C1, 900),
    ("c1-picks", f"{C1}&flow=picks", 900),
    ("c1-open", f"{C1}&histopen", 900),
    ("c1-run", f"{C1}&flow=picks&state=run", 900),
    ("c1-bad", f"{C1}&state=bad", 900),
    ("c1-bad-picks", f"{C1}&state=bad&flow=picks", 900),
    ("c1-dark", f"{C1}&theme=dark", 900),
    ("c1-narrow", C1, 900, 1280),
    # C2 泳道(定稿方向)
    ("c2", "n1.html", 900),
    ("c2-open-brief", "n1.html?open=brief", 900),
    ("c2-open-picks", "n1.html?open=picks", 900),
    ("c2-open-all", "n1.html?open=all", 900),
    ("c2-run", "n1.html?state=run", 900),
    ("c2-bad", "n1.html?state=bad&open=picks", 900),
    ("c2-dark", "n1.html?open=brief&theme=dark", 900),
    ("c2-narrow", "n1.html?open=brief", 900, 1280),
    ("c2-short", "n1.html?open=brief", 640),
    ("c2-new", "n1.html?state=new", 900),
    ("c2-new-open", "n1.html?state=new&open=picks", 900),
    ("c2-scope", "n1.html?open=brief&scope=custom", 900),
    ("c2-scope-narrow", "n1.html?open=brief&scope=custom", 900, 1280),
    ("c2-scope-dark", "n1.html?open=all&scope=custom&theme=dark", 900),
    # C3 台账
    ("c3", "n2.html", 900),
    ("c3-picks", "n2.html?flow=picks", 900),
    ("c3-skip-cfg", "n2.html?flow=picks&date=2026-11-24&cfg=brief", 900),
    ("c3-cfg-picks", "n2.html?cfg=picks", 900),
    ("c3-run", "n2.html?state=run&flow=picks", 900),
    ("c3-bad", "n2.html?state=bad", 900),
    ("c3-dark", "n2.html?theme=dark&cfg=brief", 900),
    ("c3-narrow", "n2.html?cfg=brief", 900, 1280),
    ("c3-short", "n2.html", 640),
    # Gemini 的三个方向
    ("g1", "gemini/g1.html", 900), ("g1-run", "gemini/g1.html?state=run", 900), ("g1-bad", "gemini/g1.html?state=bad", 900), ("g1-dark", "gemini/g1.html?theme=dark", 900), ("g1-narrow", "gemini/g1.html", 900, 1280),
    ("g2", "gemini/g2.html", 900), ("g2-run", "gemini/g2.html?state=run", 900), ("g2-bad", "gemini/g2.html?state=bad", 900), ("g2-dark", "gemini/g2.html?theme=dark", 900), ("g2-narrow", "gemini/g2.html", 900, 1280),
    ("g3", "gemini/g3.html", 900), ("g3-run", "gemini/g3.html?state=run", 900), ("g3-bad", "gemini/g3.html?state=bad", 900), ("g3-dark", "gemini/g3.html?theme=dark", 900), ("g3-narrow", "gemini/g3.html", 900, 1280),
    ("c2-bad-dark", "n1.html?state=bad&theme=dark", 900),
    # 定稿:泳道 + 右侧抽屉(470 宽,配置一列)
    ("lane", "lane.html", 900),
    ("lane-run", "lane.html?state=run", 900),
    ("lane-bad", "lane.html?state=bad", 900),
    ("lane-new", "lane.html?state=new", 900),
    ("lane-custom-narrow", "lane.html?scope=custom", 900, 1280),
    ("lane-dark", "lane.html?theme=dark", 900),
    ("drawer", "lane.html?open=brief", 900),
    ("drawer-picks", "lane.html?open=picks", 900),
    ("drawer-custom", "lane.html?open=brief&scope=custom", 900),
    ("drawer-run", "lane.html?open=picks&state=run", 900),
    ("drawer-bad", "lane.html?open=brief&state=bad", 900),
    ("drawer-new", "lane.html?open=picks&state=new", 900),
    ("drawer-dark", "lane.html?open=picks&theme=dark", 900),
    ("drawer-narrow", "lane.html?open=brief", 800, 1280),
    ("drawer-short", "lane.html?open=brief&scope=custom", 640),
    # 比较用:加宽到 640、配置两列(没有选)
    ("drawer-two-640", "lane.html?open=brief&cfg=two&dw=640", 900),
    ("drawer-two-640-custom", "lane.html?open=brief&cfg=two&dw=640&scope=custom", 900),
    # 细节样页里的演示:假想的复杂流程(三种画法、抽屉里的「流程」框)
    ("ph-a", "details/phases-proto.html?ph=a", 900),
    ("ph-a-run", "details/phases-proto.html?ph=a&state=run", 900),
    ("ph-b", "details/phases-proto.html?ph=b", 900),
    ("ph-b-run-dark", "details/phases-proto.html?ph=b&state=run&theme=dark", 900),
    ("ph-c-run", "details/phases-proto.html?ph=c&state=run", 900),
    ("ph-open", "details/phases-proto.html?ph=a&open=weekly", 900),
    ("ph-open-run-narrow", "details/phases-proto.html?ph=a&open=weekly&state=run", 800, 1280),
    # 第五轮存档(r5/):三种容器并排时的原型
    ("r5-drawer-470", "r5/lane.html?open=brief", 900),
    ("r5-drawer-wide", "r5/lane.html?open=brief&dw=wide", 900),
    ("r5-modal", "r5/lane.html?open=brief&det=modal", 900),
    ("r5-modal-run-dark", "r5/lane.html?open=picks&det=modal&state=run&theme=dark", 900),
    ("r5-modal-narrow-short", "r5/lane.html?open=brief&det=modal&scope=custom", 640, 1280),
    ("r5-page", "r5/lane.html?open=brief&det=page", 900),
    ("r5-page-custom", "r5/lane.html?open=brief&det=page&scope=custom", 900),
    ("r5-page-new-narrow", "r5/lane.html?open=picks&det=page&state=new", 640, 1280),
    ("index", "index.html", 900),
    ("index-r3", "r3/index.html", 900),
    ("index-r4", "r4/index.html", 900),
    ("index-r5", "r5/index.html", 900),
    ("index-title", "details/title.html", 900),
    ("index-config", "details/config.html", 900),
    ("index-phases", "details/phases.html", 900),
]

with sync_playwright() as p:
    b = p.chromium.launch()
    bad = 0
    ran = 0
    for name, path, height, *rest in SHOTS:
        if ONLY and ONLY not in name:
            continue
        page = b.new_page(viewport={"width": rest[0] if rest else 1440, "height": height})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror {str(e)[:160]}"))
        page.on("console", lambda m: problems.append(f"console {m.text[:160]}") if m.type == "error" else None)
        page.on("requestfailed", lambda r: problems.append(f"failed {r.url[-80:]}"))
        page.on("response", lambda r: problems.append(f"{r.status} {r.url[-80:]}") if r.status >= 400 else None)
        page.goto(f"{BASE}/{path}", wait_until="networkidle")
        page.wait_for_timeout(500)
        problems += page.evaluate("""() => {
            const out = [];
            const de = document.documentElement;
            if (de.scrollWidth > de.clientWidth + 1) out.push('页面横向溢出 ' + de.scrollWidth);
            // 横向:纸里面任何一块都不该比自己的可视宽度宽(排除本来就要横向滚的)
            const paper = document.querySelector('.tab-panel:not(.is-off) .runs-paper');
            if (paper) {
                const r = paper.getBoundingClientRect();
                if (r.bottom > window.innerHeight + 1) out.push('纸超出视口底 ' + Math.round(r.bottom) + '>' + window.innerHeight);
                if (!paper.children.length) out.push('纸是空的');
                // 泳道的滚动容器只该竖着滚
                const wrap = paper.querySelector('.lane-wrap, .gf-scroll');
                if (wrap && wrap.scrollWidth > wrap.clientWidth) out.push('泳道横向溢出 ' + wrap.scrollWidth + '>' + wrap.clientWidth);
                for (const el of paper.querySelectorAll('*')) {
                    if (!el.offsetParent || el.closest('svg')) continue;
                    const cs = getComputedStyle(el);
                    if (el.scrollWidth > el.clientWidth + 1 && cs.overflowX === 'visible' && cs.display !== 'inline' && !el.matches('input')) {
                        const b = el.getBoundingClientRect();
                        if (b.right > r.right + 1) out.push('越出纸的右边 ' + (el.className.toString().slice(0, 40) || el.tagName));
                    }
                }
            }
            for (const el of document.querySelectorAll('.gf-drawer, .gf-modal')) {
                const b = el.getBoundingClientRect();
                if (b.right > window.innerWidth + 1 || b.bottom > window.innerHeight + 1 || b.left < -1 || b.top < -1) out.push('浮层伸出视口 ' + el.className.toString().slice(0, 30));
                for (const kid of el.querySelectorAll('.gf-box')) { const k = kid.getBoundingClientRect(); if (k.right > b.right + 1) out.push('框越出浮层 ' + kid.className.toString().slice(0, 30)); }
            }
            return out.slice(0, 6);
        }""")
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=name.startswith("index"))
        print(f"{name:22s} {'OK' if not problems else problems[:4]}")
        bad += bool(problems)
        ran += 1
        page.close()
    b.close()
    print(f"共 {ran} 个状态,有问题的:{bad}")
