"""归档同步 Router（collector → reader）：忠实档案的 JSON Lines 导出 / 导入。

阶段1 从 app.py 迁出的 /api/archive/* 端点（路径不变）：
- GET  /api/archive/export/articles.jsonl —— collector 把档案记录导出为 JSON Lines
- POST /api/archive/import/articles.jsonl —— reader 导入（admin-only，不触发任何公网抓取）

契约见 docs/contracts/archive_sync.md。导入鉴权（admin-only，因其改写整库）仍由中间件
统一强制。序列化/校验/构建 helper 随迁入本文件，经 app.py re-export 保持 api.app.X
兼容（test_archive_sync 直接 from api.app import archive_sync_line 等）。数据访问经
deps.get_db_sink()。
"""

import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session, select
from starlette.responses import JSONResponse as StarletteJSONResponse

from api import deps
from api.articles_view import apply_article_query_filters
from api.textutils import _coerce_bool, _json_loads, _now_iso
from models.db import ArticleRecord

router = APIRouter(tags=["archive-sync"])

ARCHIVE_SYNC_SCHEMA_VERSION = "articles-jsonl-v1"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def archive_article_payload(record: ArticleRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "content_type": record.content_type,
        "source_id": record.source_id,
        "source_url": record.source_url,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "fetch_run_id": record.fetch_run_id,
        "job_id": record.job_id,
        "job_run_id": record.job_run_id,
        "source_group_id": record.source_group_id,
        "run_scope": record.run_scope,
        "has_content": record.has_content,
        "content": record.content or "",
        "extensions": _json_loads(record.extensions_json, {}),
    }


def archive_article_checksum(article: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(article).encode("utf-8")).hexdigest()


def archive_sync_line(record: ArticleRecord) -> Dict[str, Any]:
    article = archive_article_payload(record)
    return {
        "kind": "article",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "checksum": archive_article_checksum(article),
        "article": article,
    }


def archive_manifest_line(count: int, filters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "manifest",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "content": "articles",
        "count": count,
        "filters": {key: value for key, value in filters.items() if value not in (None, "")},
    }


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def build_import_article_record(article: Dict[str, Any]) -> ArticleRecord:
    required_fields = ["id", "content_type", "source_id", "publish_date", "fetched_date"]
    missing = [field for field in required_fields if article.get(field) in (None, "")]
    if missing:
        raise ValueError(f"article missing required fields: {', '.join(missing)}")

    has_content = _coerce_bool(article.get("has_content", bool(article.get("content"))))
    extensions = article.get("extensions") or {}
    if not isinstance(extensions, dict):
        raise ValueError("article.extensions must be an object")

    return ArticleRecord(
        id=str(article["id"]),
        title=str(article.get("title") or article["id"]),
        content_type=str(article["content_type"]),
        source_id=str(article["source_id"]),
        source_url=str(article.get("source_url") or ""),
        publish_date=str(article["publish_date"]),
        fetched_date=str(article["fetched_date"]),
        fetch_run_id=_coerce_optional_int(article.get("fetch_run_id")),
        job_id=_coerce_optional_int(article.get("job_id")),
        job_run_id=_coerce_optional_int(article.get("job_run_id")),
        source_group_id=_coerce_optional_int(article.get("source_group_id")),
        run_scope=str(article.get("run_scope") or "ad_hoc"),
        has_content=has_content,
        content=str(article.get("content") or ""),
        extensions_json=json.dumps(extensions, ensure_ascii=False),
        is_vectorized=False,
    )


def import_archive_sync_jsonl(raw_text: str) -> Dict[str, Any]:
    imported_count = 0
    skipped_count = 0
    updated_count = 0
    error_count = 0
    manifest: Optional[Dict[str, Any]] = None
    errors = []

    with Session(deps.get_db_sink().engine) as session:
        for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                kind = item.get("kind")
                if kind == "manifest":
                    manifest = item
                    if item.get("schema_version") != ARCHIVE_SYNC_SCHEMA_VERSION:
                        raise ValueError(f"unsupported schema_version: {item.get('schema_version')}")
                    continue
                if kind != "article":
                    raise ValueError(f"unsupported line kind: {kind}")
                if item.get("schema_version") != ARCHIVE_SYNC_SCHEMA_VERSION:
                    raise ValueError(f"unsupported schema_version: {item.get('schema_version')}")

                article = item.get("article")
                if not isinstance(article, dict):
                    raise ValueError("article line missing article object")
                expected_checksum = item.get("checksum", "")
                actual_checksum = archive_article_checksum(article)
                if expected_checksum and not hmac.compare_digest(str(expected_checksum), actual_checksum):
                    raise ValueError("checksum mismatch")

                incoming = build_import_article_record(article)
                existing = session.get(ArticleRecord, incoming.id)
                if not existing:
                    session.add(incoming)
                    imported_count += 1
                    continue
                if not existing.has_content and incoming.has_content and incoming.content:
                    existing.title = incoming.title
                    existing.content_type = incoming.content_type
                    existing.source_id = incoming.source_id
                    existing.source_url = incoming.source_url
                    existing.publish_date = incoming.publish_date
                    existing.fetched_date = incoming.fetched_date
                    existing.fetch_run_id = incoming.fetch_run_id
                    existing.job_id = incoming.job_id
                    existing.job_run_id = incoming.job_run_id
                    existing.source_group_id = incoming.source_group_id
                    existing.run_scope = incoming.run_scope
                    existing.has_content = True
                    existing.content = incoming.content
                    existing.extensions_json = incoming.extensions_json
                    existing.is_vectorized = False
                    existing.index_status = "stale"  # 内容更新使旧向量失效，待 reader 侧重索引
                    session.add(existing)
                    updated_count += 1
                else:
                    skipped_count += 1
            except Exception as exc:
                error_count += 1
                errors.append({"line": line_number, "error": str(exc)})
        session.commit()

    return {
        "status": "partial_success" if error_count else "success",
        "schema_version": ARCHIVE_SYNC_SCHEMA_VERSION,
        "manifest": manifest,
        "imported_count": imported_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "errors": errors[:20],
    }


# ==================== 端点 ====================

@router.get("/api/archive/export/articles.jsonl")
def export_archive_articles_jsonl(
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
        search: Optional[str] = None,
        has_content: Optional[bool] = None,
        skip: int = 0,
        limit: int = 1000,
):
    safe_limit = min(max(limit, 1), 5000)
    filters = {
        "content_type": content_type,
        "content_types": content_types,
        "source_id": source_id,
        "source_ids": source_ids,
        "job_id": job_id,
        "job_run_id": job_run_id,
        "fetch_run_id": fetch_run_id,
        "run_scope": run_scope,
        "publish_date_start": publish_date_start,
        "publish_date_end": publish_date_end,
        "fetched_date_start": fetched_date_start,
        "fetched_date_end": fetched_date_end,
        "search": search,
        "has_content": has_content,
        "skip": skip,
        "limit": safe_limit,
    }
    with Session(deps.get_db_sink().engine) as session:
        query = apply_article_query_filters(
            select(ArticleRecord),
            content_type=content_type,
            content_types=content_types,
            source_id=source_id,
            source_ids=source_ids,
            job_id=job_id,
            job_run_id=job_run_id,
            fetch_run_id=fetch_run_id,
            run_scope=run_scope,
            has_content=has_content,
            search=search,
            publish_date_start=publish_date_start,
            publish_date_end=publish_date_end,
            fetched_date_start=fetched_date_start,
            fetched_date_end=fetched_date_end,
        )
        records = session.exec(
            query.order_by(ArticleRecord.fetched_date.asc(), ArticleRecord.id.asc()).offset(skip).limit(safe_limit)
        ).all()

    lines = [archive_manifest_line(len(records), filters)]
    lines.extend(archive_sync_line(record) for record in records)
    body = "\n".join(_canonical_json(line) for line in lines) + "\n"
    return Response(content=body, media_type="application/x-ndjson; charset=utf-8")


@router.post("/api/archive/import/articles.jsonl")
async def import_archive_articles_jsonl(request: Request):
    raw_text = (await request.body()).decode("utf-8")
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="导入内容不能为空")
    result = import_archive_sync_jsonl(raw_text)
    status_code = 400 if result["error_count"] and not (result["imported_count"] or result["updated_count"]) else 200
    return StarletteJSONResponse(result, status_code=status_code)
