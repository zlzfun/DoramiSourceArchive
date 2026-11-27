"""话题热度上下文(issue #318):只取聚合所需的列、SQL 里先按日期粗筛。

改写前这里整行加载「历史上所有带相关标签的文章 + 分析记录」再在 Python 里按
7 天过滤,一次调用能读入数百 MB 正文。本文件守三件事:

1. 新实现与改写前的实现逐例对拍(含各种日期写法),结果一字不差;
2. 发往数据库的语句不再取正文 / 扩展 / 分析的大字段;
3. 日期粗筛确实把窗口外的行挡在数据库里,而不是摆设。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
from typing import Any, Optional, Sequence

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.analysis_contracts import TaxonomyTagDTO  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
)
from services import article_analysis  # noqa: E402
from services.article_analysis import build_topic_heat_context  # noqa: E402
from services.article_time import parse_article_time  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


# 快照时刻由调用方显式传入,测试不读系统时钟。
NOW = dt.datetime(2026, 9, 1, 1, 0, tzinfo=dt.timezone.utc)
START = NOW - dt.timedelta(days=7)
STAMP = NOW.isoformat(timespec="seconds")


@pytest.fixture
def storage(tmp_path):
    value = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'heat.db'}")
    try:
        yield value
    finally:
        value.engine.dispose()


def _reference_heat_context(
    session: Session,
    *,
    article_id: str,
    active_tags: Sequence[TaxonomyTagDTO],
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """改写前(v3.70.0)的实现,原样保留作对拍基准。"""

    snapshot = article_analysis._as_utc(now)
    start = snapshot - dt.timedelta(days=7)
    relevant = {
        int(tag.id): tag
        for tag in active_tags
        if str(tag.kind) in {"topic", "entity"}
    }
    context: dict[str, Any] = {
        "window_days": 7,
        "snapshot_at": article_analysis._iso(snapshot),
        "signals": [],
    }
    if not relevant:
        return context

    public_ids: set[str] = set()
    briefs = session.exec(
        select(ArticleRecord).where(ArticleRecord.source_id == "dorami_daily_brief")
    ).all()
    for brief in briefs:
        brief_time = parse_article_time(brief.publish_date) or parse_article_time(
            brief.fetched_date
        )
        if brief_time is None or not (start <= brief_time <= snapshot):
            continue
        included = article_analysis._article_extensions(brief).get("included_article_ids")
        if isinstance(included, list):
            public_ids.update(str(value) for value in included if value)

    rows = session.exec(
        select(ArticleTagAssignmentRecord, ArticleRecord, ArticleAnalysisRecord)
        .join(ArticleRecord, ArticleRecord.id == ArticleTagAssignmentRecord.article_id)
        .outerjoin(
            ArticleAnalysisRecord,
            ArticleAnalysisRecord.article_id == ArticleRecord.id,
        )
        .where(
            ArticleTagAssignmentRecord.tag_id.in_(list(relevant)),
            ArticleRecord.id != article_id,
            ArticleRecord.source_id != "dorami_daily_brief",
        )
    ).all()
    aggregates: dict[int, dict[str, Any]] = {}
    for assignment, evidence_article, analysis in rows:
        seen_at = parse_article_time(evidence_article.publish_date) or parse_article_time(
            evidence_article.fetched_date
        )
        if seen_at is None or not (start <= seen_at <= snapshot):
            continue
        tag_id = int(assignment.tag_id)
        aggregate = aggregates.setdefault(
            tag_id,
            {"sources": set(), "max_quality_score": None, "latest_seen_at": seen_at,
             "in_public_daily_brief": False},
        )
        aggregate["sources"].add(evidence_article.source_id)
        aggregate["latest_seen_at"] = max(aggregate["latest_seen_at"], seen_at)
        if evidence_article.id in public_ids:
            aggregate["in_public_daily_brief"] = True
        score = analysis.quality_score if analysis is not None else None
        if score is not None:
            current = aggregate["max_quality_score"]
            aggregate["max_quality_score"] = max(float(score), current or float(score))

    for tag_id in sorted(aggregates, key=lambda value: relevant[value].code):
        tag = relevant[tag_id]
        aggregate = aggregates[tag_id]
        context["signals"].append(
            {
                "code": tag.code,
                "kind": str(tag.kind),
                "name": tag.name_zh or tag.name_en or tag.code,
                "distinct_source_count": len(aggregate["sources"]),
                "max_quality_score": aggregate["max_quality_score"],
                "latest_seen_at": article_analysis._iso(aggregate["latest_seen_at"]),
                "in_public_daily_brief": aggregate["in_public_daily_brief"],
            }
        )
    return context


def _renderings(instant: dt.datetime) -> list[str]:
    """同一个时刻的各种落库写法(历史采集器写过的,加上解析器接受的冷门写法)。"""

    shanghai = instant.astimezone(dt.timezone(dt.timedelta(hours=8)))
    far_east = instant.astimezone(dt.timezone(dt.timedelta(hours=14)))
    far_west = instant.astimezone(dt.timezone(dt.timedelta(hours=-12)))
    extreme = instant.astimezone(dt.timezone(dt.timedelta(hours=-23, minutes=-59)))
    return [
        instant.isoformat(timespec="seconds"),                      # +00:00
        instant.isoformat(timespec="seconds").replace("+00:00", "Z"),
        instant.isoformat(timespec="microseconds"),
        shanghai.replace(tzinfo=None).isoformat(timespec="seconds"),  # 无时区 = 上海本地
        shanghai.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds"),
        shanghai.isoformat(timespec="seconds"),
        far_east.isoformat(timespec="seconds"),
        far_west.isoformat(timespec="seconds"),
        extreme.isoformat(timespec="seconds"),
        instant.strftime("%Y%m%dT%H%M%S") + "+00:00",                # 紧凑写法
        "  " + instant.isoformat(timespec="seconds") + "  ",          # 两侧空白
    ]


_JUNK = [
    "",
    "   ",
    "\t",
    "not a date",
    "2026-13-45",                # 形状像日期但解析失败
    "2026-08-01 garbage",        # 同上,且前 10 位早于窗口
    "2099-01-01 garbage",        # 同上,前 10 位晚于窗口
    "0000-00-00",
    "  26-08-01x",               # 带前导空白却恰好凑出日期形状
    "Mon, 31 Aug 2026 10:00:00 GMT",
    "1788224400",
    "2026-W35-2",                # 周历写法
]


def _instants(rng: random.Random) -> list[dt.datetime]:
    edges = [
        START, START - dt.timedelta(seconds=1), START + dt.timedelta(seconds=1),
        NOW, NOW - dt.timedelta(seconds=1), NOW + dt.timedelta(seconds=1),
        START - dt.timedelta(hours=23, minutes=59), START - dt.timedelta(days=1),
        START - dt.timedelta(days=1, seconds=1), START - dt.timedelta(days=2),
        NOW + dt.timedelta(days=1), NOW - dt.timedelta(days=30), NOW - dt.timedelta(days=400),
    ]
    spread = [
        NOW - dt.timedelta(seconds=rng.randrange(0, 20 * 86400)) for _ in range(40)
    ]
    return edges + spread


def _seed(
    session: Session, rng: random.Random, *, unique_sources: bool = True
) -> tuple[list[TaxonomyTagDTO], int]:
    """造一批日期写法各异的文章。

    ``unique_sources`` 为真时每篇文章自成一个来源、并且都挂第一个标签:该标签的
    「不同来源数」就等于计入的文章数,任何一行被多算或少算都会改变结果。只比
    聚合值而来源只有几个时,少算几行往往看不出来(来源数早已饱和)。
    """

    tags = []
    for index, (code, kind) in enumerate(
        [("agents", "topic"), ("llm", "topic"), ("openai", "entity"), ("chips", "industry")]
    ):
        record = CmsTagRecord(
            code=code, kind=kind, name_zh=f"标签{index}", name_en=code,
            normalized_name=code, status="active", created_at=STAMP, updated_at=STAMP,
        )
        session.add(record)
        tags.append(record)
    session.flush()

    instants = _instants(rng)
    date_values: list[str] = list(_JUNK)
    for instant in instants:
        date_values.extend(_renderings(instant))

    count = 0
    article_ids: list[str] = []
    # 发布时间与抓取时间各自遍历全部写法,再随机配对一批,覆盖「发布时间解析
    # 失败回退抓取时间」的每种组合。
    pairs = [(value, rng.choice(date_values)) for value in date_values]
    pairs += [(rng.choice(date_values), value) for value in date_values]
    pairs += [(junk, value) for junk in _JUNK for value in rng.sample(date_values, 12)]
    for publish, fetched in pairs:
        article_id = f"evidence-{count:05d}"
        count += 1
        article_ids.append(article_id)
        session.add(ArticleRecord(
            id=article_id, title=f"t{count}", content_type="article",
            source_id=f"feed-{count}" if unique_sources else f"feed-{rng.randrange(7)}",
            source_url=f"https://example.invalid/{count}",
            publish_date=publish, fetched_date=fetched, has_content=True,
            content="正文" * 200, extensions_json=json.dumps({"k": "v" * 50}),
        ))
    session.flush()  # 子表有外键,文章先落库
    for article_id in article_ids:
        chosen = {0, *rng.sample(range(len(tags)), rng.randrange(1, 4))}
        for tag in (tags[index] for index in sorted(chosen)):
            session.add(ArticleTagAssignmentRecord(
                article_id=article_id, tag_id=tag.id, tag_kind=tag.kind, relevance=0.8,
                assignment_source="llm", created_at=STAMP, updated_at=STAMP,
            ))
        if rng.random() < 0.8:
            session.add(ArticleAnalysisRecord(
                article_id=article_id, status="succeeded",
                quality_score=None if rng.random() < 0.15 else round(rng.uniform(1, 10), 1),
                summary="摘要" * 100, created_at=STAMP, updated_at=STAMP,
            ))

    # 公共日报:窗口内外各种写法都有;收录名单随机指向上面的文章。
    brief_ids: list[str] = []
    for index, publish in enumerate(rng.sample(date_values, 40) + _JUNK):
        extensions: Any = {"included_article_ids": rng.sample(article_ids, 15)}
        if index % 9 == 0:
            extensions = ["不是对象"]
        raw = json.dumps(extensions) if index % 11 else "{坏 JSON"
        brief_id = f"brief-{index:03d}"
        session.add(ArticleRecord(
            id=brief_id, title="brief", content_type="daily_brief",
            source_id="dorami_daily_brief", source_url="", publish_date=publish,
            fetched_date=rng.choice(date_values), has_content=True,
            content="日报正文" * 500, extensions_json=raw,
        ))
        brief_ids.append(brief_id)
    session.flush()
    # 日报自己也可能被打上标签:它只是收录标记,不算一个来源。
    for brief_id in brief_ids:
        session.add(ArticleTagAssignmentRecord(
            article_id=brief_id, tag_id=tags[0].id, tag_kind="topic", relevance=1.0,
            assignment_source="llm", created_at=STAMP, updated_at=STAMP,
        ))
    session.commit()
    dtos = [TaxonomyTagDTO(id=t.id, code=t.code, kind=t.kind, name_zh=t.name_zh) for t in tags]
    return dtos, count


def _kept_by_reference(session: Session, article_id: str) -> bool:
    row = session.get(ArticleRecord, article_id)
    seen_at = parse_article_time(row.publish_date) or parse_article_time(row.fetched_date)
    return seen_at is not None and START <= seen_at <= NOW


@pytest.mark.parametrize("unique_sources", [True, False])
@pytest.mark.parametrize("seed", [318, 20261120, 7])
def test_heat_context_matches_the_pre_318_implementation(storage, seed, unique_sources):
    rng = random.Random(seed)
    with Session(storage.engine) as session:
        tags, count = _seed(session, rng, unique_sources=unique_sources)
        assert count > 1000
        ids = [f"evidence-{index:05d}" for index in range(count)]
        inside = next(i for i in ids if _kept_by_reference(session, i))
        outside = next(i for i in ids if not _kept_by_reference(session, i))
        for exclude in (inside, outside, "no-such-article"):
            for subset in (tags, tags[:1], tags[1:3], tags[3:]):
                expected = _reference_heat_context(
                    session, article_id=exclude, active_tags=subset, now=NOW
                )
                actual = build_topic_heat_context(
                    session, article_id=exclude, active_tags=subset, now=NOW
                )
                assert actual == expected
        # 数据里确有命中,否则上面的相等是空对空。
        full = build_topic_heat_context(
            session, article_id="no-such-article", active_tags=tags, now=NOW
        )
        assert {signal["code"] for signal in full["signals"]} == {"agents", "llm", "openai"}
        assert any(signal["in_public_daily_brief"] for signal in full["signals"])
        if unique_sources:
            # 每篇自成一个来源且都挂 agents:来源数 = 计入的文章数,逐行可辨。
            kept = sum(1 for i in ids if _kept_by_reference(session, i))
            agents = next(s for s in full["signals"] if s["code"] == "agents")
            assert agents["distinct_source_count"] == kept > 100
            without = build_topic_heat_context(
                session, article_id=inside, active_tags=tags[:1], now=NOW
            )
            assert without["signals"][0]["distinct_source_count"] == kept - 1


def test_heat_context_reads_no_large_columns(storage):
    rng = random.Random(1)
    statements: list[str] = []
    with Session(storage.engine) as session:
        tags, _count = _seed(session, rng)

        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(" ".join(statement.split()))

        event.listen(storage.engine, "before_cursor_execute", capture)
        try:
            build_topic_heat_context(
                session, article_id="evidence-00000", active_tags=tags, now=NOW
            )
        finally:
            event.remove(storage.engine, "before_cursor_execute", capture)

    selects = [s for s in statements if s.upper().startswith("SELECT")]
    assert len(selects) == 2
    joined = next(s for s in selects if "article_tag_assignments" in s)
    brief = next(s for s in selects if "article_tag_assignments" not in s)
    select_list = joined[: joined.upper().index(" FROM ")]
    # 连接查询只取六个小列:正文、扩展、分析的长文本一律不出现在取列里。
    for heavy in (
        "articles.content", "articles.extensions_json", "articles.title",
        "article_analyses.summary", "article_analyses.score_reason",
        "article_analyses.entities_json", "article_analyses.analysis_diagnostics_json",
    ):
        assert heavy not in select_list
    assert select_list.count(",") == 5
    # 公共日报只为读收录名单,不取正文;并且带着日期粗筛。
    brief_list = brief[: brief.upper().index(" FROM ")]
    assert "articles.content" not in brief_list
    assert "articles.extensions_json" in brief_list
    for statement in (joined, brief):
        assert "substr(" in statement.lower() and " like " in statement.lower()


def test_heat_context_filters_old_rows_inside_the_database(storage, monkeypatch):
    """窗口外的行不该回到 Python:解析调用次数只与窗口内的行数同量级。"""

    with Session(storage.engine) as session:
        tag = CmsTagRecord(
            code="agents", kind="topic", name_zh="智能体", name_en="Agents",
            normalized_name="agents", status="active", created_at=STAMP, updated_at=STAMP,
        )
        session.add(tag)
        session.flush()
        for index in range(600):
            recent = index < 5
            moment = NOW - dt.timedelta(days=1 if recent else 30 + index % 300)
            session.add(ArticleRecord(
                id=f"a-{index:04d}", title="t", content_type="article",
                source_id=f"feed-{index % 4}", source_url="https://example.invalid/x",
                publish_date=moment.isoformat(timespec="seconds"),
                fetched_date=moment.isoformat(timespec="seconds"),
                has_content=True, content="正文" * 100, extensions_json="{}",
            ))
        session.flush()
        for index in range(600):
            session.add(ArticleTagAssignmentRecord(
                article_id=f"a-{index:04d}", tag_id=tag.id, tag_kind="topic", relevance=0.9,
                assignment_source="llm", created_at=STAMP, updated_at=STAMP,
            ))
        session.commit()
        dto = TaxonomyTagDTO(id=tag.id, code="agents", kind="topic", name_zh="智能体")

        calls: list[str] = []
        real = article_analysis.parse_article_time

        def counting(value):
            calls.append(value)
            return real(value)

        monkeypatch.setattr(article_analysis, "parse_article_time", counting)
        heat = build_topic_heat_context(
            session, article_id="none", active_tags=[dto], now=NOW
        )

    assert heat["signals"][0]["distinct_source_count"] == 4
    # 595 篇旧文章被 SQL 挡掉;回到 Python 的只有 5 篇窗口内的。
    assert len(calls) <= 10


def _tagged(session: Session, tag, article_id: str, source_id: str) -> None:
    stamp = (NOW - dt.timedelta(days=1)).isoformat(timespec="seconds")
    session.add(ArticleRecord(
        id=article_id, title=article_id, content_type="article", source_id=source_id,
        source_url=f"https://example.invalid/{article_id}", publish_date=stamp, fetched_date=stamp,
        has_content=True, content="正文", extensions_json="{}",
    ))
    session.flush()
    session.add(ArticleTagAssignmentRecord(
        article_id=article_id, tag_id=tag.id, tag_kind=tag.kind, relevance=0.8,
        assignment_source="llm", created_at=STAMP, updated_at=STAMP,
    ))


def _one_tag(session: Session):
    tag = CmsTagRecord(
        code="topic-agents", kind="topic", name_zh="智能体", name_en="Agents",
        normalized_name="agents", status="active", created_at=STAMP, updated_at=STAMP,
    )
    session.add(tag)
    session.flush()
    return tag, [TaxonomyTagDTO(id=tag.id, code=tag.code, kind=tag.kind, name_zh=tag.name_zh)]


def test_heat_context_does_not_count_any_system_source(storage, second_system_source):
    """issue #72:话题热度数的是「有几个来源在报」,系统源的条目(日报、其它生成流程的产出)不算来源。"""
    with Session(storage.engine) as session:
        tag, dtos = _one_tag(session)
        _tagged(session, tag, "from-feed", "feed-a")
        _tagged(session, tag, "from-brief", "dorami_daily_brief")
        _tagged(session, tag, "from-other-flow", second_system_source)
        session.commit()
        context = build_topic_heat_context(session, article_id="someone-else", active_tags=dtos, now=NOW)
    assert [signal["distinct_source_count"] for signal in context["signals"]] == [1]


def test_heat_context_counts_an_unlisted_dorami_id(storage):
    """反向对照:名单里没有它时,同一个 id 照常算一个来源。"""
    with Session(storage.engine) as session:
        tag, dtos = _one_tag(session)
        _tagged(session, tag, "from-feed", "feed-a")
        _tagged(session, tag, "from-other-flow", "dorami_test_flow")
        session.commit()
        context = build_topic_heat_context(session, article_id="someone-else", active_tags=dtos, now=NOW)
    assert [signal["distinct_source_count"] for signal in context["signals"]] == [2]
