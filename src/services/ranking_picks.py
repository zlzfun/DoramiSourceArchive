"""榜单页「深度阅读」「播客精选」(issue #221):全站近 7 天按内容价值挑选的前 10。

与个人早报同名分区同一把尺子、同一道门槛(``digest_selection.READ_KINDS`` / 内容价值当前版本 ≥ 7.0;
播客单集按播客分 = 内容价值投影 ≥ 7.5),区别只在范围:早报从读者订阅里挑,这里是全站、与订阅无关。

- 深度阅读 = 非播客、推导出的内容类型为论文 / 教程 / 观点 / 汇编;同一事件只留内容价值最高的一篇,
  热点事件 7 天榜上已有的事件整件不出现(读者在热点事件里已经看到它)。
- 播客精选 = 播客单集;播客不入事件层(#121),不做事件去重。
- 读取时现算(读者面滚动 7 天窗,不落快照):可见性与热点事件榜同一判定(隐藏 / 私有 / 凭据源不出现),
  公共日报记录不出现;分析行与早报候选池同一条件(重新分析进行中 / 失败时沿用上一次的权威结果)。
- 播客条目带公开事实 ``full_analysis_ready``(评分依据是整期内容还是节目简介,与 ``articles_view`` 同判定)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from sqlalchemy import func, or_
from sqlmodel import Session, select

from llm.article_analysis_prompt import CONTENT_VALUE_SCORING_VERSION
from models.analysis_contracts import PERSONAL_DIGEST_LISTEN_MIN_VALUE, PERSONAL_DIGEST_READ_MIN_VALUE
from models.db import ArticleAnalysisRecord, ArticleRecord, EventMemberRecord
from services import event_board
from services.article_time import in_time_window, parse_article_time
from services.content_classification import ITEM_KIND_LABELS, classify_item, score_projection, source_default_kinds
from services.digest_selection import READ_KINDS
from services.reader_defaults import PRIVATE_SOURCE_PREFIX
from services.title_cache import cached_title


Shape = Literal["article", "podcast"]
WINDOW_DAYS = 7
LIMIT = 10
PODCAST_CONTENT_TYPE = "podcast_episode"
EXCLUDED_SOURCE_IDS = ("dorami_daily_brief",)        # 公共日报记录(与早报候选池同一排除)
EXCLUDED_CONTENT_TYPES = ("daily_brief",)
MIN_VALUE: dict[str, float] = {
    "article": PERSONAL_DIGEST_READ_MIN_VALUE,
    "podcast": PERSONAL_DIGEST_LISTEN_MIN_VALUE,
}
EMPTY_REASON_NO_ITEMS = "no_items"
FULL_ANALYSIS_BASES = frozenset({"publisher_transcript", "asr_transcript"})


def _utcnow(now: dt.datetime | None) -> dt.datetime:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def _candidate_rows(
    session: Session,
    *,
    shape: Shape,
    start: dt.datetime,
    end: dt.datetime,
) -> list[tuple[ArticleRecord, ArticleAnalysisRecord]]:
    min_value = MIN_VALUE[shape]
    # 字符串日期只做带余量的粗筛(库内时间格式可能混杂),精筛按解析后的时刻(与早报候选池同一做法)。
    coarse_start = (start - dt.timedelta(days=1)).date().isoformat()
    coarse_end = (end + dt.timedelta(days=1)).date().isoformat()
    query = (
        select(ArticleRecord, ArticleAnalysisRecord)
        .join(ArticleAnalysisRecord, ArticleAnalysisRecord.article_id == ArticleRecord.id)
        .where(
            func.substr(ArticleRecord.publish_date, 1, 10) >= coarse_start,
            func.substr(ArticleRecord.publish_date, 1, 10) <= coarse_end,
            # 与早报候选池同一条件:强制重分析期间(pending / running / failed)上一次的权威结果仍有效
            or_(ArticleAnalysisRecord.status == "succeeded", ArticleAnalysisRecord.analyzed_at.is_not(None)),
            ArticleAnalysisRecord.quality_score.is_not(None),
            ArticleRecord.source_id.is_not(None),
            ArticleRecord.source_id.not_like(f"{PRIVATE_SOURCE_PREFIX}%"),
            ArticleRecord.source_id.notin_(EXCLUDED_SOURCE_IDS),
        )
    )
    if shape == "podcast":
        query = query.where(
            ArticleRecord.content_type == PODCAST_CONTENT_TYPE,
            ArticleAnalysisRecord.quality_score >= min_value,
        )
    else:
        query = query.where(
            or_(
                ArticleRecord.content_type.is_(None),
                ArticleRecord.content_type.notin_((PODCAST_CONTENT_TYPE, *EXCLUDED_CONTENT_TYPES)),
            ),
            ArticleAnalysisRecord.content_value.is_not(None),
            ArticleAnalysisRecord.content_value >= min_value,
            ArticleAnalysisRecord.content_value_version == CONTENT_VALUE_SCORING_VERSION,
        )
    return [
        row for row in session.exec(query).all()
        if in_time_window(row[0].publish_date, start=start, end=end)
    ]


def _event_ids(session: Session, article_ids: list[str]) -> dict[str, int]:
    if not article_ids:
        return {}
    return {
        str(row.article_id): int(row.event_id)
        for row in session.exec(
            select(EventMemberRecord).where(EventMemberRecord.article_id.in_(article_ids))
        ).all()
    }


def _publish_ts(article: ArticleRecord) -> float:
    parsed = parse_article_time(article.publish_date)
    return parsed.timestamp() if parsed else 0.0


def read_picks(
    session: Session,
    *,
    shape: Shape,
    now: dt.datetime | None = None,
    limit: int = LIMIT,
) -> dict[str, Any]:
    """近 7 天按内容价值的前 ``limit`` 条(不足不填);空列表 ``empty_reason="no_items"``。"""

    current = _utcnow(now)
    start = current - dt.timedelta(days=WINDOW_DAYS)
    rows = _candidate_rows(session, shape=shape, start=start, end=current)
    visibility = event_board.reader_source_infos(session, {article.source_id for article, _a in rows})
    source_kinds = source_default_kinds() if rows else {}
    scored: list[tuple[ArticleRecord, ArticleAnalysisRecord, str, float]] = []
    for article, analysis in rows:
        name_visible = visibility.get(article.source_id)
        if name_visible is None or not name_visible[1]:
            continue
        kind = classify_item(
            source_id=article.source_id,
            source_url=article.source_url,
            content_type=article.content_type,
            content_genre=analysis.content_genre,
            source_kinds=source_kinds,
            content_kind=getattr(analysis, "content_kind", None),
        ).kind or ""
        if shape == "article" and kind not in READ_KINDS:
            continue
        value = score_projection(content_type=article.content_type, kind=kind, analysis=analysis)["content_value"]
        if value is None or value < MIN_VALUE[shape] - 1e-9:
            continue
        scored.append((article, analysis, kind, float(value)))
    scored.sort(key=lambda item: (-item[3], -_publish_ts(item[0]), item[0].id))

    events = _event_ids(session, [article.id for article, *_rest in scored]) if shape == "article" else {}
    on_board = set(event_board.board_event_ids(session, now=current)) if events else set()
    seen_events: set[int] = set()
    items: list[dict[str, Any]] = []
    for article, analysis, kind, value in scored:
        event_id = events.get(article.id)
        if event_id is not None:
            if event_id in on_board or event_id in seen_events:
                continue
            seen_events.add(event_id)
        item = {
            "id": article.id,
            "title": article.title,
            "title_zh": cached_title(article),
            "source_id": article.source_id,
            "source_name": visibility[article.source_id][0],
            "content_type": article.content_type,
            "publish_date": article.publish_date,
            "content_value": round(value, 1),
            "kind": kind or None,
            "kind_label": ITEM_KIND_LABELS.get(kind) or None,
            "summary": str(analysis.summary or ""),
            "event_id": event_id,
        }
        if shape == "podcast":
            item["full_analysis_ready"] = str(getattr(analysis, "analysis_basis", "") or "") in FULL_ANALYSIS_BASES
        items.append(item)
        if len(items) >= limit:
            break
    return {
        "generated_at": current.isoformat(timespec="seconds"),
        "shape": shape,
        "window_days": WINDOW_DAYS,
        "min_value": MIN_VALUE[shape],
        "empty_reason": None if items else EMPTY_REASON_NO_ITEMS,
        "items": items,
    }


__all__ = ["EMPTY_REASON_NO_ITEMS", "LIMIT", "MIN_VALUE", "WINDOW_DAYS", "read_picks"]
