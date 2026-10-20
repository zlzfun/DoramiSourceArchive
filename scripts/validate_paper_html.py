#!/usr/bin/env python3
"""Read-only, rate-limited arXiv sample validation; no database or LLM calls."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.paper_content import ARXIV_ID_RE
from services.paper_fulltext import FetchFailure, download_html
from fetchers.impl.arxiv_paper_extractor import extract_arxiv_html

# Deliberate mix of dates and document structures, not a coverage estimate.
SAMPLES = (
    "2501.12948v1", "2408.09869v5", "2407.10671v1", "2412.19437v1",
    "2307.09288v2", "2407.21783v1", "2403.05527v1", "2312.00752v2",
    "2406.02528v1", "2402.03300v1", "2406.17557v1", "2501.00663v1",
    "2410.24258v1", "2402.19427v1", "2502.09992v1", "2404.14219v1",
    "2309.17453v1", "2303.08774v6", "1706.03762v7", "1810.04805v2",
)


async def validate(ids, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    report = []
    for arxiv_id in ids:
        try:
            cache = output / f"{arxiv_id.replace('/', '_')}.html"
            location = cache.with_suffix(".url")
            if cache.exists() and location.exists() and "v" in arxiv_id:
                html, url = cache.read_text(), location.read_text()
            else:
                html, url = await asyncio.wait_for(download_html(arxiv_id), timeout=120)
                cache.write_text(html)
                location.write_text(url)
            result = extract_arxiv_html(html, url)
        except FetchFailure as exc:
            row = {"arxiv_id": arxiv_id, "status": "abstract_fallback", "reason": exc.code}
        except ValueError as exc:
            row = {"arxiv_id": arxiv_id, "status": "abstract_fallback", "reason": str(exc)}
        except Exception as exc:
            row = {"arxiv_id": arxiv_id, "status": "abstract_fallback", "reason": type(exc).__name__}
        else:
            row = asdict(result)
            (output / f"{arxiv_id.replace('/', '_')}.md").write_text(row.pop("content"))
            row.pop("abstract")
            row["characters"] = len(result.content)
            row["status"] = "partial" if result.warnings else "converted"
        report.append(row)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("arxiv_ids", nargs="*")
    args = parser.parse_args()
    ids = args.arxiv_ids or SAMPLES
    if any(not ARXIV_ID_RE.fullmatch(value) for value in ids):
        parser.error("invalid arXiv identifier")
    asyncio.run(validate(ids, args.output))
