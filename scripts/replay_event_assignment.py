#!/usr/bin/env python3
"""事件分配回放(issue #121 wave 0 / wave 1 门禁):在临时库里按时间顺序重放 ``services/event_assignment`` 全流程。

    # 1) 从一个库(只读)导出日期窗内的分析成功文章为 JSONL(生产节点上跑,产物拷回本机)
    .venv/bin/python scripts/replay_event_assignment.py --database-url sqlite:////abs/cms_data.db \\
        --since 2026-10-01 --until 2026-10-14 --export replay.jsonl
    # 2) 回放:真实辅助模型,逐批模拟「召回 + 提示词 + 规则 + 落库」。LLM 配置从 --llm-from 指向的库只读解析
    #    (ini / env 基线 + 该库 app_settings 的运行期覆盖;base_url / model 通常只存在库里);
    #    --database-url 模式不给 --llm-from 时默认取同一库
    .venv/bin/python scripts/replay_event_assignment.py --jsonl replay.jsonl --llm-from sqlite:////abs/cms_data.db \
        [--batch-size 20] [--out report.json]
    # 3) 只做召回统计(不调模型,零候选 / 有候选一律单例落库)
    .venv/bin/python scripts/replay_event_assignment.py --jsonl replay.jsonl --dry-run
    # 也可直接从库回放:--database-url … --since … --until …(不带 --export)

源库一律经只读引擎打开(SQLite ``mode=ro``,不经 ``DatabaseStorage`` 的建表 / 升级 / 触发器安装),导出前后比对主文件摘要。
JSONL 每行字段:id / title / source_id / publish_date / fetched_date / url / summary / entity_codes /
title_zh / content_type;可选 source_owner / source_role / source_shape / quality_score / content_kind /
content_genre(导出时自动带上,手工构造可省略)。输出:每天事件数、≥ 2 发布主体事件数、规模直方图、最大簇、
每个多成员事件的成员标题(供目检误并)、模型调用数与 token。只写临时目录,不碰源库;不打印任何密钥。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import logging
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sqlalchemy.engine import make_url  # noqa: E402
from sqlmodel import Session, create_engine, select  # noqa: E402

from config import LLMConfig, settings  # noqa: E402
from llm import client as llm_client  # noqa: E402
from models.db import (  # noqa: E402
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    EventMemberRecord,
    EventRecord,
    SourceConfigRecord,
)
from services import event_assignment as ea  # noqa: E402
from services import user_sources  # noqa: E402
from services.article_time import SHANGHAI, parse_article_time  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402

FIELDS = (
    "id", "title", "source_id", "publish_date", "fetched_date", "url", "summary", "entity_codes",
    "title_zh", "content_type", "source_owner", "source_role", "source_shape", "quality_score",
    "content_kind", "content_genre",
)
ROLE_TO_SCOPE = {
    "official": ("company", "tier0_primary"),
    "media": ("ai_media", "tier1_curated"),
    "personal": ("personal_commentary", "tier2_personal_social"),
    "leaderboard": ("community", "tier1_curated"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按天流式回放事件分配(召回 + 辅助模型 + 规则)。")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--jsonl", help="导出的 JSONL 文件")
    source.add_argument("--database-url", help="直接读库(只读),配合 --since / --until")
    parser.add_argument("--since", help="起始日期 YYYY-MM-DD(按 fetched_date,上海日)")
    parser.add_argument("--until", help="截止日期 YYYY-MM-DD(含)")
    parser.add_argument("--export", help="只导出 JSONL 到该路径后退出(不回放、不调模型)")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0, help="只回放前 N 篇(调试)")
    parser.add_argument("--dry-run", action="store_true", help="不调模型,只做召回统计")
    parser.add_argument("--llm-from", default="", metavar="DATABASE_URL",
                        help="只读打开该库解析 LLM 配置(ini / env + app_settings 覆盖);--database-url 模式默认同一库")
    parser.add_argument("--model", default="", help="覆盖模型名(主 / 辅助档一并换成它)")
    parser.add_argument("--out", default="", help="报告 JSON 输出路径")
    parser.add_argument("--keep-db", action="store_true", help="结束后保留临时库并打印路径")
    args = parser.parse_args()
    if args.database_url and not (args.since and args.until):
        parser.error("--database-url 需要同时给 --since 与 --until")
    return args


# ── 导出 ──────────────────────────────────────────────────────────────────────

def _day_bounds(since: str, until: str) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.fromisoformat(since).replace(tzinfo=SHANGHAI)
    end = dt.datetime.fromisoformat(until).replace(tzinfo=SHANGHAI) + dt.timedelta(days=1)
    return start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)


def _sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def readonly_engine(database_url: str):
    """只读引擎:SQLite 走 ``file:…?mode=ro`` URI(连接层拒写),且**不经** ``DatabaseStorage``——它的构造器会
    create_all / 兼容升级 / 装 FTS 触发器,对「只读导出」的生产库也是写操作(R1 #5)。其它方言原样连接、只发 SELECT。"""
    path = _sqlite_path(database_url)
    if path is not None:
        return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", connect_args={"check_same_thread": False})
    return create_engine(database_url)


def sqlite_digest(database_url: str) -> str | None:
    """SQLite 主文件的 SHA-256(导出前后对照的「源库字节不变」守卫);非 SQLite 文件库返回 None。"""
    path = _sqlite_path(database_url)
    if path is None or not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_from_database(database_url: str, since: str, until: str) -> list[dict[str, Any]]:
    """只读取库里日期窗内分析成功、非私有、本机权威的文章(与 scan_pending 同口径,论文 / 汇编 / 播客留给服务判)。
    走只读引擎(拒写是硬保证);导出前后比对 SQLite 主文件摘要,不一致只告警(活库并发写入会让它变)。"""
    start, end = _day_bounds(since, until)
    digest_before = sqlite_digest(database_url)
    engine = readonly_engine(database_url)
    try:
        items = _load_items(engine, start, end)
    finally:
        engine.dispose()
    digest_after = sqlite_digest(database_url)
    if digest_before != digest_after:
        # 只读引擎拒写是硬保证;摘要变化几乎总是别的进程在写活库(抓取 / 分析 / WAL checkpoint),只提示不中断。
        print("[warn] 导出前后源库主文件摘要不一致:多半是活库有其它进程并发写入;只读连接本身不会写。"
              "若确认库无人写入,再检查连接方式。", file=sys.stderr)
    return items


def _load_items(engine, start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    with Session(engine) as session:
        rows = session.exec(
            select(ArticleRecord, ArticleAnalysisRecord)
            .join(ArticleAnalysisRecord, ArticleAnalysisRecord.article_id == ArticleRecord.id)
            .where(
                ArticleAnalysisRecord.status == "succeeded",
                ArticleRecord.fetched_date >= (start - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
                ArticleRecord.fetched_date <= (end + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            .order_by(ArticleRecord.fetched_date.asc())
        ).all()
        rows = [
            (article, analysis) for article, analysis in rows
            if (fetched := parse_article_time(article.fetched_date)) is not None and start <= fetched < end
        ]
        owner_backed = user_sources.user_source_ids(session)
        source_ids = sorted({article.source_id for article, _analysis in rows})
        configs = {
            row.source_id: row for row in session.exec(
                select(SourceConfigRecord).where(SourceConfigRecord.source_id.in_(source_ids))
            ).all()
        } if source_ids else {}
        meta = ea.load_source_meta(session, source_ids)
        entity_codes = ea.entity_codes_by_article(session, [article.id for article, _analysis in rows])
        items: list[dict[str, Any]] = []
        for article, analysis in rows:
            sid = article.source_id
            if user_sources.is_user_source(sid) or sid in owner_backed or user_sources.source_is_credentialed(configs.get(sid)):
                continue
            source_meta = meta.get(sid) or ea.SourceMeta()
            items.append({
                "id": article.id, "title": article.title or "", "source_id": sid,
                "publish_date": article.publish_date or "", "fetched_date": article.fetched_date or "",
                "url": article.source_url or "", "summary": analysis.summary or "",
                "entity_codes": sorted(entity_codes.get(article.id, ())),
                "title_zh": ea.translated_title(article), "content_type": article.content_type or "",
                "source_owner": source_meta.owner, "source_role": source_meta.role,
                "source_shape": source_meta.shape,
                "quality_score": float(analysis.quality_score) if analysis.quality_score is not None else None,
                "content_kind": analysis.content_kind, "content_genre": analysis.content_genre,
            })
    return items


def load_jsonl(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} 不是合法 JSON:{exc}") from exc
            missing = [key for key in ("id", "title", "source_id", "fetched_date") if not data.get(key)]
            if missing:
                raise ValueError(f"{path}:{line_no} 缺字段 {missing}")
            items.append({key: data.get(key) for key in FIELDS})
    return items


def write_jsonl(path: str, items: Iterable[dict[str, Any]]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count


# ── 临时库播种 ─────────────────────────────────────────────────────────────────

def _stamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def seed_sources(engine, items: list[dict[str, Any]], now: dt.datetime) -> None:
    stamp = _stamp(now)
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        seen.setdefault(str(item["source_id"]), item)
    with Session(engine) as session:
        for source_id, item in seen.items():
            role = str(item.get("source_role") or "media")
            shape = str(item.get("source_shape") or "article")
            scope, tier = ROLE_TO_SCOPE.get(role, ROLE_TO_SCOPE["media"])
            source_type = "x" if shape == "social" else ("podcast" if shape == "podcast" else "rss")
            session.add(SourceConfigRecord(
                source_id=source_id, name=source_id, source_type=source_type, url="",
                source_scope=scope, provenance_tier=tier, source_owner=str(item.get("source_owner") or ""),
                created_at=stamp, updated_at=stamp,
            ))
        session.commit()


def seed_chunk(engine, chunk: list[dict[str, Any]], tag_ids: dict[str, int], now: dt.datetime) -> None:
    stamp = _stamp(now)
    with Session(engine) as session:
        for item in chunk:
            extensions = {"translation_zh_title": item["title_zh"]} if item.get("title_zh") else {}
            summary = str(item.get("summary") or "")
            session.add(ArticleRecord(
                id=str(item["id"]), title=str(item["title"]), content_type=str(item.get("content_type") or "rss_article"),
                source_id=str(item["source_id"]), source_url=str(item.get("url") or ""),
                publish_date=str(item.get("publish_date") or item["fetched_date"]),
                fetched_date=str(item["fetched_date"]), has_content=bool(summary),
                content=summary or str(item["title"]), extensions_json=json.dumps(extensions, ensure_ascii=False),
            ))
        session.flush()  # 文章先落,分析与标签指派的外键才成立(两者无 relationship,UoW 不知顺序)
        for item in chunk:
            summary = str(item.get("summary") or "")
            session.add(ArticleAnalysisRecord(
                article_id=str(item["id"]), status="succeeded", tagging_status="succeeded",
                quality_score=float(item.get("quality_score") or 5.0), summary=summary,
                content_genre=item.get("content_genre") or None, content_kind=item.get("content_kind") or None,
                analyzed_at=stamp, created_at=stamp, updated_at=stamp,
            ))
            for code in item.get("entity_codes") or ():
                code = str(code)
                if code not in tag_ids:
                    tag = CmsTagRecord(
                        code=code, kind="entity", name_zh=code, name_en=code, normalized_name=code,
                        status="active", user_selectable=True, created_at=stamp, updated_at=stamp,
                    )
                    session.add(tag)
                    session.flush()
                    tag_ids[code] = int(tag.id)
                session.add(ArticleTagAssignmentRecord(
                    article_id=str(item["id"]), tag_id=tag_ids[code], tag_kind="entity", is_primary=False,
                    relevance=0.9, assignment_source="llm", prompt_version="replay", taxonomy_version=1,
                    created_at=stamp, updated_at=stamp,
                ))
        session.commit()


def chunked_by_day(items: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    """按 fetched_date 顺序切批:满 batch_size 或跨上海日即切,模拟分析周期逐批到达。"""
    chunk: list[dict[str, Any]] = []
    current_day = None
    for item in items:
        day = parse_article_time(item["fetched_date"]).astimezone(SHANGHAI).date()
        if chunk and (len(chunk) >= batch_size or day != current_day):
            yield chunk
            chunk = []
        current_day = day
        chunk.append(item)
    if chunk:
        yield chunk


# ── 模型配置与计量 ─────────────────────────────────────────────────────────────

def resolve_config(llm_from: str, model_override: str = "") -> LLMConfig:
    """从 ``llm_from`` 指向的库**只读**解析项目 LLM 配置(ini / env 基线 + 该库 ``app_settings`` 的运行期覆盖,
    与后端 ``daily_brief.resolve_llm_config`` 同一函数)。走 :func:`readonly_engine`(SQLite ``mode=ro``,
    不经 ``DatabaseStorage``);库不可用时退回 ini / env。永不打印 api_key。"""
    from services.daily_brief import resolve_llm_config

    cfg = settings.llm
    if llm_from:
        try:
            engine = readonly_engine(llm_from)
            with Session(engine) as session:
                cfg = resolve_llm_config(session)
            engine.dispose()
        except Exception as exc:  # noqa: BLE001 - 配置库不可用时退回 ini / env
            print(f"[warn] 从 {llm_from.split('://', 1)[0]} 库读取 LLM 配置失败,退回 ini / env:{type(exc).__name__}",
                  file=sys.stderr)
    if model_override:
        cfg = replace(cfg, model=model_override, aux_model="", thinking_mode="disabled")
    return cfg


def _sqlite_file_exists(database_url: str) -> bool:
    path = _sqlite_path(database_url)
    return path is not None and path.exists()


class UsageTotals:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def record(self, meta, usage: dict[str, Any], _model: str) -> None:
        if getattr(meta, "purpose", "") != "event_assignment":
            return
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.total_tokens += int(usage.get("total_tokens") or 0)

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens, "total_tokens": self.total_tokens,
        }


# ── 报告 ──────────────────────────────────────────────────────────────────────

def _merge_stats(total: dict[str, Any], stats: dict[str, Any]) -> None:
    for key, value in stats.items():
        if isinstance(value, bool):
            total[key] = bool(total.get(key)) or value
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict):
            total.setdefault(key, {})
            _merge_stats(total[key], value)
        elif isinstance(value, list):
            total.setdefault(key, []).extend(value)


def build_report(engine, totals: dict[str, Any], usage: UsageTotals, *, dry_run: bool) -> dict[str, Any]:
    with Session(engine) as session:
        events = session.exec(select(EventRecord)).all()
        rows = session.exec(
            select(EventMemberRecord, ArticleRecord)
            .join(ArticleRecord, ArticleRecord.id == EventMemberRecord.article_id)
        ).all()
    members: dict[int, list[tuple[EventMemberRecord, ArticleRecord]]] = collections.defaultdict(list)
    for member, article in rows:
        members[int(member.event_id)].append((member, article))
    per_day: dict[str, dict[str, int]] = collections.defaultdict(lambda: {
        "events": 0, "multi_member": 0, "multi_publisher": 0, "max_size": 0,
    })
    histogram: collections.Counter = collections.Counter()
    groups: list[dict[str, Any]] = []
    for event in events:
        day = (parse_article_time(event.first_seen_at) or dt.datetime.now(dt.timezone.utc)).astimezone(SHANGHAI).date().isoformat()
        bucket = per_day[day]
        bucket["events"] += 1
        bucket["max_size"] = max(bucket["max_size"], event.member_count)
        histogram[event.member_count] += 1
        if event.member_count >= 2:
            bucket["multi_member"] += 1
        if event.publisher_count >= 2:
            bucket["multi_publisher"] += 1
        if event.member_count >= 2:
            ordered = sorted(members.get(int(event.id), []), key=lambda pair: pair[0].report_at)
            groups.append({
                "event_id": int(event.id), "day": day, "title": event.title, "title_source": event.title_source,
                "member_count": event.member_count, "publisher_count": event.publisher_count,
                "members": [
                    {
                        "article_id": article.id, "source_id": article.source_id,
                        "publisher": member.publisher_key, "assigned_by": member.assigned_by,
                        "confidence": member.confidence, "report_at": member.report_at,
                        "title": article.title, "representative": bool(member.is_representative),
                    }
                    for member, article in ordered
                ],
            })
    groups.sort(key=lambda g: (-g["member_count"], g["day"], g["event_id"]))
    multi_days = [bucket["multi_publisher"] for bucket in per_day.values()]
    multi_days.sort()
    median = multi_days[len(multi_days) // 2] if multi_days else 0
    return {
        "dry_run": dry_run,
        "days": dict(sorted(per_day.items())),
        "size_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "multi_publisher_per_day_median": median,
        "largest": groups[0] if groups else None,
        "groups": groups,
        "stats": totals,
        "llm_usage": usage.as_dict(),
    }


def print_report(report: dict[str, Any]) -> None:
    print("=" * 72)
    print("事件分配回放报告" + ("(dry-run:只做召回统计,全部单例落库)" if report["dry_run"] else ""))
    print("=" * 72)
    print(f"{'日期':<12}{'事件数':>8}{'≥2成员':>8}{'≥2主体':>8}{'最大簇':>8}")
    for day, bucket in report["days"].items():
        print(f"{day:<12}{bucket['events']:>8}{bucket['multi_member']:>8}{bucket['multi_publisher']:>8}{bucket['max_size']:>8}")
    print(f"≥ 2 发布主体事件 / 天 中位数:{report['multi_publisher_per_day_median']}")
    print("规模直方图(成员数: 事件数):" + ", ".join(f"{k}: {v}" for k, v in report["size_histogram"].items()))
    stats = report["stats"]
    recall = stats.get("recall", {})
    print(
        f"分配:直连 {stats.get('direct', 0)} · 单例 {stats.get('singleton', 0)} · 模型归并 {stats.get('llm_assigned', 0)}"
        f" · 退避 {stats.get('failed', 0)} · 回显失配 {stats.get('echo_mismatch', 0)}"
        f" · 非法目标 {stats.get('illegal_target', 0)} · 低置信跨日 {stats.get('low_confidence', 0)}"
        f" · 空事实 {stats.get('empty_fact', 0)} · 72h 兜底 {stats.get('skipped', 0)}"
    )
    articles = max(1, recall.get("articles", 0))
    print(
        f"召回:{recall.get('with_candidates', 0)}/{recall.get('articles', 0)} 篇有候选"
        f"(均 {recall.get('candidates_total', 0) / max(1, recall.get('with_candidates', 0)):.1f} 个)"
        f" · 实体 {recall.get('entity', 0)} · 标题 {recall.get('fts_title', 0)} · 摘要 {recall.get('fts_summary', 0)}"
        f" · 译题 {recall.get('fts_zh', 0)} · 仅批内相似 {recall.get('batch_similar', 0)}"
        f" · 候选覆盖率 {recall.get('with_candidates', 0) / articles:.0%}"
    )
    samples = stats.get("echo_samples") or []
    if samples:
        print(f"回显失配样本({len(samples)} 条,标题前 12 字 vs 模型 echo):")
        for sample in samples[:12]:
            print(f"   「{sample['title']}」 vs 「{sample['echo']}」")
    usage = report["llm_usage"]
    print(
        f"模型:调用 {stats.get('llm_calls', 0)} 次 / {stats.get('llm_items', 0)} 篇;计量 {usage['calls']} 次,"
        f" prompt {usage['prompt_tokens']} + completion {usage['completion_tokens']} = {usage['total_tokens']} token"
    )
    if report["largest"]:
        largest = report["largest"]
        print(f"最大簇:#{largest['event_id']} {largest['title']}({largest['member_count']} 篇 / {largest['publisher_count']} 主体)")
    print("-" * 72)
    print(f"多成员事件 {len(report['groups'])} 组(供目检误并):")
    for group in report["groups"]:
        print(f"[#{group['event_id']} {group['day']}] {group['title']}  ({group['member_count']} 篇 / {group['publisher_count']} 主体,题源 {group['title_source']})")
        for member in group["members"]:
            mark = "*" if member["representative"] else " "
            conf = f" c={member['confidence']:.2f}" if member["confidence"] is not None else ""
            print(f"   {mark} {member['source_id']:<28} {member['assigned_by']:<9}{conf}  {member['title']}")


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.jsonl:
        items = load_jsonl(args.jsonl)
    else:
        items = load_from_database(args.database_url, args.since, args.until)
    items = [item for item in items if parse_article_time(item.get("fetched_date")) is not None]
    items.sort(key=lambda item: parse_article_time(item["fetched_date"]))
    if args.limit:
        items = items[: args.limit]
    if args.export:
        count = write_jsonl(args.export, items)
        print(f"已导出 {count} 篇到 {args.export}")
        return 0
    if not items:
        print("没有可回放的文章", file=sys.stderr)
        return 2

    if args.dry_run:
        cfg = LLMConfig()
    else:
        llm_from = args.llm_from or args.database_url or ""
        if not llm_from and _sqlite_file_exists(settings.storage.database_url):
            llm_from = settings.storage.database_url  # --jsonl 模式未指定:退回当前配置库(存在时)
        cfg = resolve_config(llm_from, args.model)
        if not cfg.configured:
            print("LLM 未配置(需 base_url / api_key / model;运行期覆盖存在库的 app_settings,"
                  "请用 --llm-from 指向真实库);或加 --dry-run 只做召回统计", file=sys.stderr)
            return 2
        print(f"模型:{cfg.for_aux().model}(辅助档){' · 主档 ' + cfg.model if cfg.for_aux().model != cfg.model else ''}")

    usage = UsageTotals()
    llm_client.set_usage_recorder(usage.record)
    tmp_dir = Path(tempfile.mkdtemp(prefix="dorami-event-replay-"))
    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_dir / 'replay.db'}").engine
    first = parse_article_time(items[0]["fetched_date"])
    seed_sources(engine, items, first)
    tag_ids: dict[str, int] = {}
    totals: dict[str, Any] = {}
    chunks = list(chunked_by_day(items, max(1, args.batch_size)))
    print(f"回放 {len(items)} 篇 / {len(chunks)} 批(batch_size={args.batch_size}),临时库 {tmp_dir}")
    for index, chunk in enumerate(chunks, 1):
        now = max(parse_article_time(item["fetched_date"]) for item in chunk) + dt.timedelta(seconds=1)
        seed_chunk(engine, chunk, tag_ids, now)
        stats = ea.assign_pending(engine, cfg, now=now, batch_size=len(chunk))
        _merge_stats(totals, stats)
        if index % 10 == 0 or index == len(chunks):
            print(f"  批 {index}/{len(chunks)} @ {now.astimezone(SHANGHAI).strftime('%m-%d %H:%M')}:"
                  f" 直连 {totals.get('direct', 0)} · 单例 {totals.get('singleton', 0)}"
                  f" · 模型归并 {totals.get('llm_assigned', 0)} · 退避 {totals.get('failed', 0)}", flush=True)
    # 收尾:把仍在退避中的 pending 推进到 72h 兜底,避免报告漏篇
    tail_now = max(parse_article_time(item["fetched_date"]) for item in items) + dt.timedelta(hours=ea.PENDING_MAX_AGE_HOURS + 1)
    _merge_stats(totals, ea.assign_pending(engine, LLMConfig(), now=tail_now, batch_size=args.batch_size))
    llm_client.set_usage_recorder(None)

    report = build_report(engine, totals, usage, dry_run=args.dry_run)
    print_report(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.out}")
    if args.keep_db:
        print(f"临时库保留在 {tmp_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
