#!/usr/bin/env python3
"""Preview HF paper enrichment; --apply enqueues, the normal worker fetches."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine
from sqlmodel import Session, select

from models.db import ArticleRecord, PaperFulltextJobRecord
from services.paper_fulltext import eligible, enqueue
from services.paper_content import hf_arxiv_id


def backfill(engine, *, limit: int = 20, apply: bool = False, retry: bool = False,
             since: str = "", source_id: str = "web_huggingface_daily_papers") -> dict:
    """Bounded preview is read-only, including job/metadata/lease state."""
    selected = []
    with Session(engine) as session:
        query = select(ArticleRecord).where(ArticleRecord.source_id == source_id)
        if since:
            query = query.where(ArticleRecord.publish_date >= since)
        # Filter settled tasks in SQL so a newer completed page cannot starve
        # older eligible papers. Scanning itself remains bounded.
        existing = select(PaperFulltextJobRecord.article_id)
        if retry:
            existing = existing.where(PaperFulltextJobRecord.status.notin_(["failed", "unavailable"]))
        query = query.where(ArticleRecord.id.notin_(existing)).order_by(
            ArticleRecord.publish_date.desc(), ArticleRecord.id
        ).limit(limit)
        for article in session.exec(query).all():
            if not eligible(session, article):
                continue
            if not apply or enqueue(session, article, retry_terminal=retry):
                selected.append({"article_id": article.id, "arxiv_id": hf_arxiv_id(article)})
        if apply:
            session.commit()
    return {"mode": "apply" if apply else "dry-run", "count": len(selected), "items": selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="Explicit target DB; migrate it before applying")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--since", default="", help="Publication date lower bound (ISO format)")
    parser.add_argument("--source-id", default="web_huggingface_daily_papers")
    parser.add_argument("--apply", action="store_true", help="Enqueue; does not call arXiv or any LLM")
    parser.add_argument("--retry", action="store_true", help="Include failed/unavailable jobs; edited/skipped records stay protected")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    engine = create_engine(args.database_url)
    try:
        print(json.dumps(backfill(engine, limit=args.limit, apply=args.apply, retry=args.retry,
                                 since=args.since, source_id=args.source_id), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
