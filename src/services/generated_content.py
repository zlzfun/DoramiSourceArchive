"""生成流程的产出落库(issue #72):把一期内容写成系统源下的一条条目。

公共日报有自己的落库函数(``daily_brief._persist_brief``,与游标、消费台账、去重审计同一事务,不动);
其它生成流程共用这里。两处守的是同样三件事:

- 正文为空不写——空正文放行的话,阅读器显示「暂无正文」而运行记录是成功(2026-08 日报事故);
- 源表全集不变量(issue #262 R1c):同一事务里确保系统源那一行在;
- 同一期重跑是覆盖,不是另起一行(期的 id 由流程自己定,按日期幂等)。
"""

from __future__ import annotations

import json

from sqlmodel import Session

from models.content import BaseContent, serialize_to_metadata
from models.db import ArticleRecord


def persist_generated_article(session: Session, content_obj: BaseContent) -> ArticleRecord:
    """写一期产出;已有同 id 的就覆盖。不提交(调用方与自己的运行记录一起提交)。"""
    if not (content_obj.content or "").strip():
        raise RuntimeError("正文为空,拒绝写库")

    from services import source_rows, sync_consumer_policy

    if not sync_consumer_policy.local_source_operation_allowed(
        session, content_obj.source_id, operation="collection"
    ):
        raise RuntimeError("当前节点不允许写入这个源")
    metadata = serialize_to_metadata(content_obj)
    source_rows.ensure_source_row(session, content_obj.source_id, content_type=content_obj.content_type)
    record = session.get(ArticleRecord, content_obj.id)
    if record is None:
        record = ArticleRecord(
            id=content_obj.id, title=content_obj.title,
            source_id=content_obj.source_id, source_url=content_obj.source_url,
            content_type=content_obj.content_type, publish_date=content_obj.publish_date,
            fetched_date=content_obj.fetched_date, archive_updated_at=content_obj.fetched_date,
        )
    for key in ("title", "content_type", "source_id", "source_url", "publish_date", "fetched_date", "content"):
        setattr(record, key, getattr(content_obj, key))
    record.has_content = True
    record.extensions_json = json.dumps(metadata.get("extensions", {}), ensure_ascii=False)
    session.add(record)
    session.flush()
    return record
