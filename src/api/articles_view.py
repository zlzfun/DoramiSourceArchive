"""文章查询过滤与对外视图序列化（阶段1 共享 helper 模块化）。

把原本散落在 app.py、被 articles/feed/reader/vector/public 多域共享的文章相关 helper
集中到此：查询过滤器拼装、列表/feed 视图序列化、Markdown 导出，以及 ArticleRecord →
可向量化 GenericContent 的转换。仅依赖 ORM、内容模型与 textutils 的纯工具，不依赖
app 级可变全局（db_sink 等），故可被任意 Router 安全 import、不与 api.app 成环。
"""

import json
from typing import Any, Dict, Optional

from api.textutils import _date_end_value, _json_loads, _split_csv
from models.content import BaseContent
from models.db import ArticleRecord


class GenericContent(BaseContent):
    # 拆分为结构类型与来源通道
    content_type = "restored_from_db"
    source_id = "database_restore"


def _record_to_content(record: ArticleRecord) -> GenericContent:
    """将 ArticleRecord 转换为可向量化的 GenericContent 对象。"""
    obj = GenericContent(
        id=record.id, title=record.title, publish_date=record.publish_date,
        source_url=record.source_url, content=record.content,
        fetched_date=record.fetched_date, has_content=record.has_content,
    )
    obj.content_type = record.content_type
    obj.source_id = record.source_id
    return obj


def apply_article_query_filters(
        query,
        content_type: Optional[str] = None,
        content_types: Optional[str] = None,
        source_id: Optional[str] = None,
        source_ids: Optional[str] = None,
        exclude_source_ids: Optional[str] = None,
        job_id: Optional[int] = None,
        job_run_id: Optional[int] = None,
        fetch_run_id: Optional[int] = None,
        run_scope: Optional[str] = None,
        is_vectorized: Optional[bool] = None,
        has_content: Optional[bool] = None,
        search: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        fetched_date_start: Optional[str] = None,
        fetched_date_end: Optional[str] = None,
):
    if content_type:
        query = query.where(ArticleRecord.content_type == content_type)
    content_type_list = _split_csv(content_types)
    if content_type_list:
        query = query.where(ArticleRecord.content_type.in_(content_type_list))

    if source_id:
        query = query.where(ArticleRecord.source_id == source_id)
    source_id_list = _split_csv(source_ids)
    if source_id_list:
        query = query.where(ArticleRecord.source_id.in_(source_id_list))
    exclude_source_id_list = _split_csv(exclude_source_ids)
    if exclude_source_id_list:
        query = query.where(ArticleRecord.source_id.notin_(exclude_source_id_list))

    if job_id is not None:
        query = query.where(ArticleRecord.job_id == job_id)
    if job_run_id is not None:
        query = query.where(ArticleRecord.job_run_id == job_run_id)
    if fetch_run_id is not None:
        query = query.where(ArticleRecord.fetch_run_id == fetch_run_id)
    if run_scope:
        query = query.where(ArticleRecord.run_scope == run_scope)

    if is_vectorized is not None:
        query = query.where(ArticleRecord.is_vectorized == is_vectorized)
    if has_content is not None:
        query = query.where(ArticleRecord.has_content == has_content)
    if search:
        query = query.where(ArticleRecord.title.contains(search))

    if publish_date_start:
        query = query.where(ArticleRecord.publish_date >= publish_date_start)
    if publish_date_end:
        query = query.where(ArticleRecord.publish_date <= _date_end_value(publish_date_end))

    if fetched_date_start:
        query = query.where(ArticleRecord.fetched_date >= fetched_date_start)
    if fetched_date_end:
        query = query.where(ArticleRecord.fetched_date <= _date_end_value(fetched_date_end))

    return query


def serialize_feed_article(record: ArticleRecord, include_content: bool = True) -> Dict[str, Any]:
    extensions = _json_loads(record.extensions_json, {})
    metadata = {
        "id": record.id,
        "title": record.title,
        "source_url": record.source_url,
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "fetch_run_id": record.fetch_run_id,
        "job_id": record.job_id,
        "job_run_id": record.job_run_id,
        "source_group_id": record.source_group_id,
        "run_scope": record.run_scope,
        "has_content": record.has_content,
        "is_vectorized": record.is_vectorized,
        "extensions": extensions,
    }

    item = {
        "id": record.id,
        "title": record.title,
        "url": record.source_url,
        "metadata": metadata,
    }
    if include_content:
        item["content"] = record.content or ""
    return item


def serialize_article_list_item(record: ArticleRecord, include_content: bool = True) -> Dict[str, Any]:
    content = record.content or ""
    item = {
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
        "is_vectorized": record.is_vectorized,
        "content_preview": content[:280],
    }
    if include_content:
        item["content"] = content
        item["extensions_json"] = record.extensions_json or "{}"
    return item


def article_recency_order(*prefix_ordering):
    """Canonical newest-first ordering for cross-source archive views."""
    return (
        *prefix_ordering,
        ArticleRecord.publish_date.desc(),
        ArticleRecord.fetched_date.desc(),
        ArticleRecord.id.desc(),
    )


def article_to_markdown(record: ArticleRecord) -> str:
    metadata = serialize_feed_article(record, include_content=False)["metadata"]
    frontmatter = json.dumps(metadata, ensure_ascii=False, indent=2)
    content = record.content or ""
    return f"---\n{frontmatter}\n---\n\n# {record.title}\n\n{content}".strip()
