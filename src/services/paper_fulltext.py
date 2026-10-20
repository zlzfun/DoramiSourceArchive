"""Bounded official HTML enrichment, independent of ingestion and LLM work."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import and_, or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from fetchers.impl.arxiv_paper_extractor import (
    CONVERTER_VERSION, MAX_HTML_BYTES, PaperExtraction, extract_arxiv_html,
)
from models.db import ArticleRecord, PaperFulltextJobRecord
from services.arxiv_access import arxiv_request
from services.paper_content import ARXIV_ID_RE, analysis_body, hf_arxiv_id, metadata, paper_metadata
from services.sync_consumer_policy import local_source_operation_allowed

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _body_hash(article: ArticleRecord) -> str:
    return hashlib.sha256((article.content or "").encode("utf-8")).hexdigest()


def _allowed(session: Session, article: ArticleRecord) -> bool:
    return not article.analysis_authority_id and local_source_operation_allowed(
        session, article.source_id, operation="collection",
    )


def eligible(session: Session, article: ArticleRecord) -> bool:
    return bool(
        hf_arxiv_id(article) and analysis_body(article).strip()
        and paper_metadata(article).get("content_level") not in {"fulltext", "partial"}
        and _allowed(session, article)
    )


def enqueue(session: Session, article: ArticleRecord, *, retry_terminal: bool = False) -> bool:
    """Enqueue in the caller's article transaction; never performs HTTP."""
    if not eligible(session, article):
        return False
    session.flush()
    job = PaperFulltextJobRecord(
        article_id=article.id, arxiv_id=hf_arxiv_id(article),
        abstract=analysis_body(article), original_body_hash=_body_hash(article),
        updated_at=_stamp(),
    )
    values = job.model_dump()
    statement = insert(PaperFulltextJobRecord).values(**values)
    if retry_terminal:
        statement = statement.on_conflict_do_update(
            index_elements=["article_id"],
            set_={key: value for key, value in values.items() if key != "article_id"},
            where=PaperFulltextJobRecord.status.in_(["failed", "unavailable"]),
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=["article_id"])
    result = session.execute(statement)
    return result.rowcount == 1


def _input_unchanged(article: ArticleRecord, job: PaperFulltextJobRecord) -> bool:
    return (_body_hash(article) == job.original_body_hash
            and hf_arxiv_id(article) == job.arxiv_id
            and analysis_body(article) == job.abstract)


def _due(now: float):
    return or_(
        and_(PaperFulltextJobRecord.status == "pending", PaperFulltextJobRecord.retry_at <= now),
        and_(PaperFulltextJobRecord.status == "processing", PaperFulltextJobRecord.lease_until <= now),
    )


def claim_one(engine, *, now: float | None = None) -> PaperFulltextJobRecord | None:
    now = time.time() if now is None else now
    with Session(engine) as session:
        # The update is the claim fence, including competing worker processes.
        ids = session.exec(select(PaperFulltextJobRecord.article_id).where(_due(now))
                           .order_by(PaperFulltextJobRecord.retry_at, PaperFulltextJobRecord.article_id)
                           .limit(20)).all()
        for article_id in ids:
            token = uuid.uuid4().hex
            result = session.execute(
                update(PaperFulltextJobRecord)
                .where(PaperFulltextJobRecord.article_id == article_id, _due(now))
                .values(status="processing", lease_token=token, lease_until=now + LEASE_SECONDS,
                        attempts=PaperFulltextJobRecord.attempts + 1, updated_at=_stamp())
            )
            if result.rowcount != 1:
                session.rollback()
                continue
            job = session.get(PaperFulltextJobRecord, article_id)
            article = session.get(ArticleRecord, article_id)
            if article is None or not eligible(session, article) or not _input_unchanged(article, job):
                job.status, job.last_error, job.lease_token = "skipped", "article_changed_or_remote", ""
                session.add(job)
                session.commit()
                continue
            if job.attempts > MAX_ATTEMPTS:
                job.status, job.last_error, job.lease_token = "failed", "lease_retries_exhausted", ""
                session.add(job)
                session.commit()
                continue
            session.commit()
            session.refresh(job)
            session.expunge(job)
            return job
    return None


@dataclass
class FetchFailure(Exception):
    code: str
    retry_seconds: float = 0
    unavailable: bool = False


async def download_html(arxiv_id: str) -> tuple[str, str]:
    if not ARXIV_ID_RE.fullmatch(arxiv_id):
        raise FetchFailure("invalid_arxiv_id")
    url = f"https://arxiv.org/html/{arxiv_id}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers={
        "User-Agent": "DoramiSourceArchive/1.0 (paper HTML reader; https://github.com/zlzfun/DoramiSourceArchive)",
    }) as client:
        for _ in range(4):
            async with arxiv_request():
                async with client.stream("GET", url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        redirected = urljoin(url, response.headers.get("location", ""))
                        parsed = urlsplit(redirected)
                        if (parsed.scheme != "https" or parsed.netloc != "arxiv.org"
                                or not parsed.path.startswith("/html/")):
                            raise FetchFailure("unsafe_redirect")
                        redirected_id = parsed.path.removeprefix("/html/").rstrip("/")
                        if (not ARXIV_ID_RE.fullmatch(redirected_id)
                                or re.sub(r"v\d+$", "", redirected_id) != re.sub(r"v\d+$", "", arxiv_id)
                                or (re.search(r"v\d+$", arxiv_id) and redirected_id != arxiv_id)):
                            raise FetchFailure("paper_identity_changed")
                        url = redirected
                        continue
                    if response.status_code in {404, 422}:
                        raise FetchFailure(f"http_{response.status_code}", 86400, True)
                    if response.status_code == 429 or response.status_code >= 500:
                        try:
                            wait = float(response.headers.get("retry-after", "3600"))
                        except ValueError:
                            wait = 3600
                        raise FetchFailure(f"http_{response.status_code}", min(86400, max(60, wait)))
                    if response.status_code != 200:
                        raise FetchFailure(f"http_{response.status_code}")
                    if "html" not in response.headers.get("content-type", "").lower():
                        raise FetchFailure("not_html")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_HTML_BYTES:
                            raise FetchFailure("html_too_large")
                    try:
                        return data.decode("utf-8"), url
                    except (ValueError, UnicodeError) as exc:
                        raise FetchFailure(str(exc)[:100]) from exc
        raise FetchFailure("too_many_redirects")


async def fetch_html(arxiv_id: str) -> PaperExtraction:
    html, url = await download_html(arxiv_id)
    try:
        return await asyncio.to_thread(extract_arxiv_html, html, url)
    except ValueError as exc:
        raise FetchFailure(str(exc)[:100]) from exc


def finish(engine, task: PaperFulltextJobRecord, *, extraction: PaperExtraction | None = None,
           failure: FetchFailure | None = None, now: float | None = None) -> bool:
    """Lease and original-body guards protect edits, sync and newer attempts."""
    now = time.time() if now is None else now
    with Session(engine) as session:
        # Take a write fence before reading article state, so sync/manual edits
        # cannot race a read-modify-write during this short SQLite transaction.
        result = session.execute(
            update(PaperFulltextJobRecord)
            .where(PaperFulltextJobRecord.article_id == task.article_id,
                   PaperFulltextJobRecord.status == "processing",
                   PaperFulltextJobRecord.lease_token == task.lease_token,
                   PaperFulltextJobRecord.lease_until > now)
            .values(updated_at=_stamp())
        )
        if result.rowcount != 1:
            session.rollback()
            return False
        job = session.get(PaperFulltextJobRecord, task.article_id)
        article = session.get(ArticleRecord, task.article_id)
        job.lease_token, job.lease_until = "", 0
        if article is None or not eligible(session, article) or not _input_unchanged(article, job):
            job.status, job.last_error = "skipped", "article_changed_or_remote"
            session.add(job)
            session.commit()
            return False
        ext = metadata(article)
        paper = {
            **paper_metadata(article), "abstract": job.abstract, "arxiv_id": job.arxiv_id,
            "content_level": "abstract", "analysis_basis": "paper_abstract",
        }
        if extraction is not None:
            article.content, article.has_content = extraction.content, True
            paper.update(
                content_level="partial" if extraction.warnings else "fulltext",
                arxiv_id=extraction.arxiv_id, source_url=extraction.source_url,
                license_url=extraction.license_url, converter_version=CONVERTER_VERSION,
                math_count=extraction.math_count, image_count=extraction.image_count,
                table_count=extraction.table_count, warnings=list(extraction.warnings),
                fetched_at=_stamp(),
            )
            # Legacy caches without fingerprints would otherwise stay "valid".
            for key in ("translation_zh", "translation_zh_fp", "summary_zh", "summary_zh_fp"):
                ext.pop(key, None)
            job.status, job.last_error = "succeeded", ""
        else:
            failure = failure or FetchFailure("unknown_error")
            retry = failure.retry_seconds and job.attempts < MAX_ATTEMPTS
            job.status = "pending" if retry else ("unavailable" if failure.unavailable else "failed")
            job.retry_at = now + failure.retry_seconds * (2 ** (job.attempts - 1)) if retry else 0
            job.last_error = failure.code
        paper["fetch_status"] = job.status
        ext["paper"] = paper
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.add(article)
        session.add(job)
        session.commit()
        return extraction is not None


async def run_batch(engine, *, limit: int = 2, fetcher=fetch_html) -> list[str]:
    """Small sequential batches; interrupted jobs recover after their lease."""
    completed = []
    for _ in range(limit):
        task = await asyncio.to_thread(claim_one, engine)
        if task is None:
            break
        try:
            extraction = await asyncio.wait_for(fetcher(task.arxiv_id), timeout=120)
        except FetchFailure as exc:
            await asyncio.to_thread(finish, engine, task, failure=exc)
        except (httpx.HTTPError, TimeoutError):
            await asyncio.to_thread(finish, engine, task, failure=FetchFailure("network_error", 3600))
        except Exception:
            # Unexpected parser failures stay visible; do not strand the lease.
            await asyncio.to_thread(finish, engine, task, failure=FetchFailure("extraction_error"))
        else:
            if await asyncio.to_thread(finish, engine, task, extraction=extraction):
                completed.append(task.article_id)
    return completed
