"""旁路对比脚本：对若干网页源，比较 Legacy(httpx) 与 crawl4ai 两个详情后端。

阶段一交付物，**不改动生产抓取路径**。它用现有 fetcher 发现文章 URL，再分别用两个
``WebContentBackend`` 提取详情，输出统一对比指标。crawl4ai 未安装时仅跑 Legacy（脚手架仍可用）。

用法：
    # 项目默认环境（通常无 crawl4ai）：只跑 Legacy 基线
    python scripts/compare_web_backends.py

    # 带 crawl4ai 的环境（如 ~/Codes/crawl4ai/.venv）：双路对比
    ~/Codes/crawl4ai/.venv/bin/python scripts/compare_web_backends.py --json data/web_backend_compare.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fetchers.web_content.backend import DetailResult  # noqa: E402
from fetchers.web_content.compare import compare_detail  # noqa: E402
from fetchers.web_content.crawl4ai_backend import Crawl4AIContentBackend  # noqa: E402


@dataclass
class SourceCase:
    label: str
    module: str
    class_name: str
    fetch_kwargs: Dict[str, Any]


CASES = (
    SourceCase("Anthropic News", "fetchers.impl.webpage_fetcher",
               "AnthropicNewsWebFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
    SourceCase("IT之家 AI", "fetchers.impl.webpage_fetcher",
               "IThomeAiWebFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
    SourceCase("DeepSeek API Change Log", "fetchers.impl.curated_core_fetcher",
               "DeepSeekApiChangeLogFetcher",
               {"limit": 1, "detail_max_chars": 8_000}),
    # —— 批次 2（B 类）——
    SourceCase("Claude Blog", "fetchers.impl.webpage_fetcher",
               "ClaudeBlogWebFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
    SourceCase("Cursor Changelog", "fetchers.impl.curated_core_fetcher",
               "CursorChangelogWebFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
    SourceCase("量子位", "fetchers.impl.curated_core_fetcher",
               "QbitAiWebsiteFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
    SourceCase("新智元", "fetchers.impl.curated_core_fetcher",
               "AieraWebsiteFetcher",
               {"limit": 1, "fetch_detail": True, "detail_max_chars": 8_000}),
)


async def discover_item(case: SourceCase):
    """跑一次 fetcher 取首条，返回 (url, 生产正文, 生产提取方法)。

    生产正文即该节点**当前线上路径**的输出（可能是节点专用提取器，而非通用提取器），
    这才是迁移验收的正确基线。"""
    module = __import__(case.module, fromlist=[case.class_name])
    fetcher = getattr(module, case.class_name)()
    fetcher.web_backend_enabled = False  # 基线必须是 legacy 生产路径，强制关掉 backend
    async for item in fetcher.fetch(**case.fetch_kwargs):
        method = (item.raw_data or {}).get("detail_extraction_method", "") if hasattr(item, "raw_data") else ""
        return item.source_url, (item.content or ""), method
    return None, "", ""


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None, help="把对比结果写入 JSON 文件")
    parser.add_argument("--max-chars", type=int, default=8_000)
    args = parser.parse_args()

    crawl_ok = Crawl4AIContentBackend.is_available()
    print(f"crawl4ai available: {crawl_ok}\n")

    discovered = []
    for case in CASES:
        url, prod_content, prod_method = await discover_item(case)
        discovered.append((case, url, prod_content, prod_method))

    rows = []
    async with Crawl4AIContentBackend() as crawl:
        for case, url, prod_content, prod_method in discovered:
            if not url:
                print(f"=== {case.label} === (跳过：未发现 URL)\n")
                continue
            # 基线 = 节点生产路径实际产出的正文（节点专用提取器优先）
            legacy_res = DetailResult(
                text=prod_content, method=prod_method or "production",
                url=url, success=bool(prod_content), backend="production",
            )
            crawl_res = (
                await crawl.extract(url, max_chars=args.max_chars)
                if crawl_ok
                else DetailResult(url=url, backend="crawl4ai", error="not installed")
            )
            row = compare_detail(case.label, url, legacy_res, crawl_res)
            rows.append(row)

            print(f"=== {case.label} ===")
            print(f"URL: {url}")
            print(f"Legacy : chars={row.legacy_chars}, method={row.legacy_method}, ok={row.legacy_success}")
            print(f"Crawl4 : chars={row.crawl_chars}, method={row.crawl_method}, "
                  f"profile={row.crawl_profile}, status={row.crawl_status}, ok={row.crawl_success}")
            print(f"similarity={row.similarity}, len_ratio={row.len_ratio}")
            if row.note:
                print(f"note: {row.note}")
            print()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已写入 {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
