"""社交归档的纯本地 metadata 回填。

只读取 ``ArticleRecord.extensions_json.raw_data`` 并重放纯归一化函数；本模块不导入
``httpx``、fetcher 或 X 配置，因此不会触发任何平台请求。正文与向量状态从不写入。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from sqlmodel import Session, select

from models.db import ArticleRecord
from services.x_api_config import read_user_cache, write_user_cache
from services.x_social_normalizer import InvalidXRawData, normalize_x_raw_extensions


ProgressCallback = Optional[Callable[..., None]]


def _call(callback: ProgressCallback, *args: Any) -> None:
    if callback is not None:
        callback(*args)


def backfill_social_posts(
    engine,
    *,
    set_total: ProgressCallback = None,
    advance: ProgressCallback = None,
) -> Dict[str, int]:
    """从本地 raw_data 回填 social extensions 与源头像缓存。

    重复执行只会得到 ``extensions_unchanged``，不会改写正文、is_vectorized、
    index_status，也不会刷新未变化缓存的 updated_at。
    """
    result = {
        "articles_scanned": 0,
        "articles_processed": 0,
        "extensions_updated": 0,
        "extensions_unchanged": 0,
        "skipped_total": 0,
        "skipped_missing_raw": 0,
        "skipped_invalid_extensions": 0,
        "skipped_invalid_raw": 0,
        "records_with_avatar": 0,
        "quoted_records": 0,
        "reposted_records": 0,
        "sources_with_avatar": 0,
        "user_caches_updated": 0,
    }
    source_profiles: Dict[str, Dict[str, Any]] = {}

    with Session(engine) as session:
        records = session.exec(
            select(ArticleRecord)
            .where(ArticleRecord.content_type == "social_post")
            .order_by(ArticleRecord.fetched_date.asc(), ArticleRecord.id.asc())
        ).all()
        _call(set_total, len(records))
        for record in records:
            result["articles_scanned"] += 1
            try:
                extensions = json.loads(record.extensions_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                result["skipped_invalid_extensions"] += 1
                result["skipped_total"] += 1
                _call(advance)
                continue
            if not isinstance(extensions, dict):
                result["skipped_invalid_extensions"] += 1
                result["skipped_total"] += 1
                _call(advance)
                continue
            if "raw_data" not in extensions or extensions.get("raw_data") is None:
                result["skipped_missing_raw"] += 1
                result["skipped_total"] += 1
                _call(advance)
                continue
            try:
                merged, author = normalize_x_raw_extensions(
                    extensions["raw_data"], existing_extensions=extensions
                )
            except InvalidXRawData:
                result["skipped_invalid_raw"] += 1
                result["skipped_total"] += 1
                _call(advance)
                continue

            result["articles_processed"] += 1
            avatar_url = str(author.get("profile_image_url") or "").strip()
            if avatar_url:
                result["records_with_avatar"] += 1
            if "quoted" in merged:
                result["quoted_records"] += 1
            if "reposted" in merged:
                result["reposted_records"] += 1
            if record.source_id:
                # fetched_date 升序，后看到的资料覆盖旧资料，保证选用该源最新快照。
                source_profiles[record.source_id] = author

            if merged == extensions:
                result["extensions_unchanged"] += 1
            else:
                record.extensions_json = json.dumps(merged, ensure_ascii=False)
                session.add(record)
                result["extensions_updated"] += 1
            _call(advance)
        if result["extensions_updated"]:
            session.commit()

    result["sources_with_avatar"] = sum(
        1 for user in source_profiles.values()
        if str(user.get("profile_image_url") or "").strip()
    )
    for source_id, user in sorted(source_profiles.items()):
        with Session(engine) as session:
            before = read_user_cache(session, source_id)
            after = write_user_cache(
                session,
                source_id,
                handle=str(user.get("username") or ""),
                user_id=str(user.get("id") or ""),
                user=user,
            )
        if before != after:
            result["user_caches_updated"] += 1
    return result
