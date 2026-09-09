"""个人早报条目标题中文化(issue #33 §4,v3.51.2)。

早报条目快照的 ``title`` 原样取自文章标题,英文源条目在读者面就是英文。本模块在
edition 生成完成后为每条补一个 ``title_zh``(写进 ``snapshot_json``),页面展示为
「中文主标题 + 英文原标题作副标题」。中文标题来源按成本从低到高复用:

1. 文章 ``extensions_json.translation_zh_title`` 缓存(v3.45 阅读窗标题同译,指纹须与
   当前标题一致);
2. 近三期公共日报里同一篇的 ``title_cn``(编辑标题,只作展示、**不写回**翻译缓存——
   它是编辑改写的头条名,不是译文);
3. 都没有则在编排后批量译标题(aux 轻模型,与阅读窗同一提示词,并发 + 总时长预算),
   写回 ``translation_zh_title`` 缓存让阅读窗与其他读者的早报都受益。

纪律:中文标题(``looks_chinese``)不译不画副标题;带凭证的自定源条目不送外部 LLM
(与阅读窗 AI 同一道 ``external_ai_allowed_source_ids`` 闸,只走 1/2 两级);任何一步失败
都回退原标题,绝不拖垮编排(edition 早已 ready,本步只补展示字段);LLM 未配置时只做 1/2 两级。
不并入入库分析调用(issue #22 结论:点评/中文标题不进分析)。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import Any, Awaitable, Mapping, Sequence

from sqlmodel import Session, select

from config import LLMConfig
from llm.client import UsageMeta, client_session
from models.db import ArticleRecord, PersonalDigestEditionRecord, PersonalDigestItemRecord
from services import user_sources as user_sources_service
from services.reader_ai import (
    TRANSLATION_TITLE_FP_KEY,
    TRANSLATION_TITLE_KEY,
    _body_fingerprint,
    _cache_valid,
    _translate_title,
    looks_chinese,
)


logger = logging.getLogger(__name__)

PUBLIC_DAILY_BRIEF_SOURCE_ID = "dorami_daily_brief"
USAGE_PURPOSE = "personal_digest_title"
# 一期早报 ≤ 12 条标题,aux 模型单条通常 1–2s;总预算兜住端点抖动——超时的条目回退原标题,
# 下次(其他读者/重编)再补,不让「正在编排」等太久。
TRANSLATE_BUDGET_SECONDS = 20.0
# 只看最近几期公共日报的 title_cn:早报条目本就是近 36h 内的文章。
RECENT_BRIEF_EDITIONS = 3


def _run_async(coro: Awaitable[Any]) -> Any:
    """在同步编排路径里跑一段协程。

    请求线程(FastAPI 同步端点跑在线程池)与 APScheduler 的同步 job 都没有事件循环,
    直接 ``asyncio.run``;若被误从事件循环线程调用,则退到独立线程再起循环,
    避免 "cannot be called from a running event loop"。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _llm_config(session: Session) -> LLMConfig:
    from services.daily_brief import resolve_llm_config  # 延迟导入:避免路由/服务层循环依赖

    return resolve_llm_config(session)


def _extensions(record: ArticleRecord) -> dict[str, Any]:
    try:
        ext = json.loads(record.extensions_json or "{}")
    except (ValueError, TypeError):
        return {}
    return ext if isinstance(ext, dict) else {}


def _snapshot(item: PersonalDigestItemRecord) -> dict[str, Any]:
    try:
        payload = json.loads(item.snapshot_json or "{}")
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _recent_public_brief_titles(session: Session, article_ids: Sequence[str]) -> dict[str, str]:
    """近几期公共日报 ``extensions.items`` 里 id → title_cn(只收中文且异于原题的)。"""

    wanted = set(article_ids)
    if not wanted:
        return {}
    rows = session.exec(
        select(ArticleRecord)
        .where(ArticleRecord.source_id == PUBLIC_DAILY_BRIEF_SOURCE_ID)
        .order_by(ArticleRecord.publish_date.desc())
        .limit(RECENT_BRIEF_EDITIONS)
    ).all()
    found: dict[str, str] = {}
    for row in rows:
        items = _extensions(row).get("items")
        for entry in items or []:
            if not isinstance(entry, dict):
                continue
            article_id = str(entry.get("id") or "").strip()
            title_cn = str(entry.get("title_cn") or "").strip()
            if article_id in wanted and article_id not in found and title_cn and looks_chinese(title_cn):
                found[article_id] = title_cn
    return found


async def _translate_batch(
    titles: Mapping[str, str],
    llm_config: LLMConfig,
    *,
    username: str | None,
    budget_seconds: float,
) -> dict[str, str]:
    """并发译一批标题,整体受预算约束;超时/失败的条目缺席(回退原标题)。"""

    config = llm_config.for_aux()
    concurrency = max(1, getattr(config, "map_concurrency", 4))
    semaphore = asyncio.Semaphore(concurrency)
    usage_meta = UsageMeta(purpose=USAGE_PURPOSE, username=username)
    out: dict[str, str] = {}

    async def _one(article_id: str, title: str, http_client) -> None:
        try:
            async with semaphore:
                translated = await _translate_title(title, config, usage_meta, http_client)
        except Exception as exc:  # noqa: BLE001 - 单条失败只回退原标题
            logger.warning("早报标题翻译失败 article=%s: %s", article_id, exc)
            return
        translated = (translated or "").strip()
        if translated and translated != title:
            out[article_id] = translated

    async with client_session(config) as http_client:
        tasks = [asyncio.create_task(_one(aid, title, http_client)) for aid, title in titles.items()]
        if not tasks:
            return out
        done, pending = await asyncio.wait(tasks, timeout=budget_seconds)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning(
                "早报标题翻译超出预算 %.0fs:%d/%d 条回退原标题",
                budget_seconds, len(pending), len(tasks),
            )
            await asyncio.gather(*pending, return_exceptions=True)
    return out


def localize_edition_titles(
    session: Session,
    edition: PersonalDigestEditionRecord,
    *,
    llm_config: LLMConfig | None = None,
    budget_seconds: float = TRANSLATE_BUDGET_SECONDS,
) -> dict[str, int]:
    """为一期早报的条目快照补 ``title_zh``;返回各来源计数(观测用)。

    幂等:已带 ``title_zh`` 的条目跳过。调用方应在 edition 落成终态后调用并自行兜底
    异常——本函数内部已把 LLM 失败降级为回退原标题,只有 DB 写入失败会向外抛。
    """

    stats = {"chinese": 0, "cached": 0, "public_brief": 0, "translated": 0, "fallback": 0}
    items = list(session.exec(
        select(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id == edition.id)
    ).all())
    pending: dict[str, str] = {}  # article_id → 原标题
    by_article: dict[str, list[PersonalDigestItemRecord]] = {}
    for item in items:
        snapshot = _snapshot(item)
        title = str(snapshot.get("title") or "").strip()
        if not title or snapshot.get("title_zh"):
            continue
        if looks_chinese(title):
            stats["chinese"] += 1
            continue
        article_id = str(item.article_id or snapshot.get("article_id") or "").strip()
        if not article_id:
            stats["fallback"] += 1
            continue
        pending[article_id] = title
        by_article.setdefault(article_id, []).append(item)
    if not pending:
        return stats

    resolved: dict[str, str] = {}
    records = {
        row.id: row for row in session.exec(
            select(ArticleRecord).where(ArticleRecord.id.in_(sorted(pending)))
        ).all()
    }
    # ① 文章自身的标题译文缓存(指纹与当前标题一致才算)
    for article_id, title in pending.items():
        record = records.get(article_id)
        if record is None:
            continue
        ext = _extensions(record)
        if _cache_valid(ext, TRANSLATION_TITLE_KEY, TRANSLATION_TITLE_FP_KEY, _body_fingerprint(title)):
            cached = str(ext[TRANSLATION_TITLE_KEY]).strip()
            if cached and cached != title:
                resolved[article_id] = cached
                stats["cached"] += 1
    # ② 近期公共日报同篇的编辑标题
    remaining = [aid for aid in pending if aid not in resolved]
    for article_id, title_cn in _recent_public_brief_titles(session, remaining).items():
        resolved[article_id] = title_cn
        stats["public_brief"] += 1
    # ③ 批量翻译并写回文章缓存。带凭证的自定源(token/签名 feed)内容不得离开部署——
    # 与阅读窗 translate/summarize 的 _ensure_articles_exportable 同一道闸(codex 检视 P1):
    # 早报会收录读者自己的私有源,其标题若送外部 LLM 就泄露了;这类条目只走 ①②,否则原标题。
    # 文章行缺失时来源不可判定,同样不送(fail closed)。
    remaining = {aid: pending[aid] for aid in pending if aid not in resolved}
    if remaining:
        exportable = set(user_sources_service.external_ai_allowed_source_ids(
            session, [records[aid].source_id for aid in remaining if aid in records],
        ))
        remaining = {
            aid: title for aid, title in remaining.items()
            if aid in records and records[aid].source_id in exportable
        }
    config = llm_config if llm_config is not None else _llm_config(session)
    if remaining and config.configured:
        translated = _run_async(_translate_batch(
            remaining, config, username=None, budget_seconds=budget_seconds,
        ))
        for article_id, title_zh in translated.items():
            resolved[article_id] = title_zh
            stats["translated"] += 1
            record = records.get(article_id)
            if record is None:
                continue
            ext = _extensions(record)
            ext[TRANSLATION_TITLE_KEY] = title_zh
            ext[TRANSLATION_TITLE_FP_KEY] = _body_fingerprint(remaining[article_id])
            record.extensions_json = json.dumps(ext, ensure_ascii=False)
            session.add(record)
    stats["fallback"] += sum(1 for aid in pending if aid not in resolved)

    for article_id, title_zh in resolved.items():
        for item in by_article.get(article_id, ()):
            snapshot = _snapshot(item)
            snapshot["title_zh"] = title_zh
            item.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
            session.add(item)
    session.commit()
    return stats
