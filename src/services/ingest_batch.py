"""收录批处理核心(issue #262 推送入口统一)。

推送令牌(``ding_``,通道 ``push``)与管理员「单条收录」(会话,通道 ``manual``)共用这一条路径:
身份解析 → 服务端建条目 → 链接查重 → 同源内容判重 → 入库 → 收录记录。HTTP 鉴权、额度与审计留在路由。

两段式:先在一个会话里把整批的出处解析完(批内新建的临时源互相可见、身份冲突可核对),
再逐条入库——每条独立事务,单条载荷问题只拒收这一条。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlmodel import Session

from models.db import ArticleRecord
from services import content_fingerprint, ingest_policy, ingest_push
from storage.impl import db_storage


CHANNEL_MANUAL = "manual"


@dataclass
class IngestCaller:
    actor: str
    channel: str = ingest_push.CHANNEL_PUSH
    remaining: Optional[int] = None  # 今日剩余额度;None = 不限(管理员会话)


def social_source(item: dict[str, Any]) -> dict[str, Any]:
    """社交帖的出处默认就是作者:没给 source 时用 author 填。"""
    source = dict(item.get("source") or {})
    author = dict(item.get("author") or {})
    platform = item.get("platform") or "x"
    if not (source.get("source_id") or source.get("handle") or source.get("user_id")):
        source.update({
            "platform": platform,
            "handle": author.get("handle", ""),
            "user_id": author.get("user_id", ""),
            "name": source.get("name") or author.get("name", ""),
            "avatar_url": source.get("avatar_url") or author.get("avatar_url", ""),
        })
    if not source.get("platform"):
        source["platform"] = platform
    return source


def _resolve_all(
    db_sink, items: list[dict[str, Any]], caller: IngestCaller
) -> list[tuple[int, dict[str, Any], Optional[ingest_push.ResolvedSource], Optional[str]]]:
    prepared = []
    with Session(db_sink.engine) as session:
        ctx = ingest_push.new_context(session, caller.actor)
        for index, item in enumerate(items):
            if caller.remaining is not None and index >= caller.remaining:
                prepared.append((index, item, None, "超出今日收录额度"))
                continue
            kind = item.get("kind")
            source = social_source(item) if kind == "social_post" else dict(item.get("source") or {})
            try:
                resolved = ingest_push.resolve_source(ctx, kind, source, item_url=str(item.get("url") or ""))
                prepared.append((index, item, resolved, None))
            except ingest_push.IngestRejected as exc:
                prepared.append((index, item, None, str(exc)))
    return prepared


async def ingest(
    db_sink,
    items: list[dict[str, Any]],
    caller: IngestCaller,
    *,
    on_created: Optional[Callable[[list[str]], None]] = None,
) -> list[dict[str, Any]]:
    """逐条返回 created / duplicate / duplicate_url / duplicate_content / refreshed / rejected。"""
    results: list[dict[str, Any]] = []
    created_ids: list[str] = []
    for index, item, resolved, reason in _resolve_all(db_sink, items, caller):
        kind = item.get("kind")
        base = {"index": index, "kind": kind}
        if resolved is None:
            results.append({**base, "status": "rejected", "reason": reason})
            continue
        try:
            with Session(db_sink.engine) as session:
                id_prefix = ingest_push.item_id_prefix(session, resolved.source_id)
            content = ingest_push.build_content(
                kind, resolved.source_id, item, id_prefix=id_prefix, channel=caller.channel
            )
        except (ingest_push.IngestRejected, ValueError, TypeError, OverflowError) as exc:
            # 单条载荷问题只拒收这一条;整批照常回执、审计照常执行(实现检视 R1 #12)。
            results.append({**base, "status": "rejected", "reason": str(exc), "source_id": resolved.source_id})
            continue
        content._ingest_actor = caller.actor
        if resolved.created:
            hint = dict(resolved.hint)
            hint.pop("podcast_feed_url", None)
            content._source_hint = hint
        note = str(item.get("note") or "")
        duplicate: Optional[tuple[str, str]] = None
        status = ""
        with Session(db_sink.engine) as session:
            existing = session.get(ArticleRecord, content.id)
            # 链接即身份只对文章与社交帖成立;播客单集常共用节目页链接,只认单集身份(guid / 单集
            # 链接 → 条目 id),否则会把同节目的不同单集并成一条(检视复检新 P1)。
            if existing is None and kind != "podcast_episode":
                row = ingest_push.existing_article_by_url(session, content.source_url)
                if row is not None:
                    duplicate = (row.id, row.source_id)
                    # 同一链接已在库里:别的源 → duplicate_url;同一个源但条目 id 公式不同 → duplicate。
                    status = "duplicate_url" if row.source_id != resolved.source_id else "duplicate"
        if existing is None and duplicate is None and not resolved.created and kind == "article":
            # 同源内容判重:同一个源里换了链接的同一篇(只在推送时、只比同源)。目标源第一次被推送时
            # 要现场补齐指纹(上千条约数秒),放到工作线程里,不阻塞事件循环。
            duplicate = await asyncio.to_thread(
                content_fingerprint.find_same_source_duplicate_id,
                db_sink.engine,
                resolved.source_id,
                content.content or "",
                content.content_type,
            )
            status = "duplicate_content" if duplicate is not None else ""
        with Session(db_sink.engine) as session:
            if duplicate is not None:
                ingest_push.record_pick(session, duplicate[0], caller.actor, note, channel=caller.channel)
                session.commit()
                results.append({**base, "status": status, "id": duplicate[0], "source_id": duplicate[1]})
                continue
            if kind == "podcast_episode" and resolved.created:
                ingest_push.ensure_podcast_source(session, resolved)
                session.commit()
        outcome = await db_sink.save_with_outcome(content)
        if outcome in db_storage.SAVE_STORED_OUTCOMES or outcome in {
            db_storage.SAVE_DUPLICATE, db_storage.SAVE_REFRESHED,
        }:
            with Session(db_sink.engine) as session:
                ingest_push.record_pick(session, content.id, caller.actor, note, channel=caller.channel)
                session.commit()
        if outcome == db_storage.SAVE_CREATED:
            created_ids.append(content.id)
            status = "created"
        elif outcome in {db_storage.SAVE_CONTENT_FILLED, db_storage.SAVE_METADATA_REPAIRED,
                         db_storage.SAVE_REFRESHED}:
            status = "refreshed"
        elif outcome == db_storage.SAVE_DUPLICATE:
            status = "duplicate"
        else:
            _code, detail = ingest_policy.outcome_error(outcome) or (400, outcome)
            results.append({**base, "status": "rejected", "reason": detail, "source_id": resolved.source_id})
            continue
        results.append({
            **base, "status": status, "id": content.id, "source_id": resolved.source_id,
            "source_created": bool(resolved.created and status == "created"),
        })
    if created_ids and on_created is not None:
        on_created(created_ids)
    return results
