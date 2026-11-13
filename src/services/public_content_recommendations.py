"""Separate read/listen recommendations for the public brief; no model calls.

The legacy ``extensions.items`` news contract remains unchanged. Recommendations
are additive snapshots in ``extensions.content_recommendations`` and the Markdown
body, so an older structured consumer cannot mistake an episode score for news.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models.analysis_contracts import SelectionLane
from models.db import ArticleAnalysisRecord, ArticleRecord, EventMemberRecord, PodcastArtifactRecord
from services.content_recommendations import positive_duration
from services.article_time import parse_article_time
from services.digest_selection import ValueLanePolicy
from services.personal_digest import load_public_value_candidates
from services.source_naming import friendly_source_name
from services.title_cache import cached_title

POLICIES = {
    SelectionLane.READ: ValueLanePolicy(min_value=7.0, max_items=3),
    SelectionLane.LISTEN: ValueLanePolicy(min_value=7.5, max_items=2),
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _mapping(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _entries(ext):
    value = ext.get("content_recommendations")
    return [v for v in value if isinstance(v, dict) and v.get("id")] if isinstance(value, list) else []


def load_pool(session: Session, *, report_date: str, source_ids: list[str] | None) -> list[dict]:
    """Seven-day supply independent of the news cursor (full analysis can finish later)."""
    end = dt.datetime.combine(dt.date.fromisoformat(report_date) + dt.timedelta(days=1), dt.time(), SHANGHAI)
    cutoff = min(dt.datetime.now(SHANGHAI), end - dt.timedelta(microseconds=1))
    candidates = []
    for lane, policy in POLICIES.items():
        for candidate in load_public_value_candidates(
            session, lane=lane, source_ids=source_ids, cutoff_at=cutoff, policy=policy,
        ):
            # Papers keep their existing, shared daily quota of three. Do not add
            # another paper quota through the deep-reading section.
            if lane == SelectionLane.READ and candidate.content_kind == "paper":
                continue
            candidates.append((lane, candidate))
    ids = [c.article_id for _, c in candidates]
    if not ids:
        return []
    articles = {a.id: a for a in session.exec(select(ArticleRecord).where(ArticleRecord.id.in_(ids))).all()}
    analyses = {a.article_id: a for a in session.exec(select(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(ids))).all()}
    audio_durations = {
        (audio.episode_id, audio.id): positive_duration(audio.duration_seconds)
        for audio in session.exec(select(PodcastArtifactRecord).where(
            PodcastArtifactRecord.episode_id.in_(ids), PodcastArtifactRecord.kind == "digest_audio_zh",
            PodcastArtifactRecord.status == "published",
        )).all()
    }
    result = []
    for lane, candidate in candidates:
        article, analysis = articles[candidate.article_id], analyses[candidate.article_id]
        ext = _mapping(article.extensions_json)
        guide = ext.get("premium_guide")
        guide = guide if isinstance(guide, dict) else {}
        result.append({
            "id": article.id, "selection_lane": lane.value,
            "title": cached_title(article) or article.title, "source_id": article.source_id,
            "source": friendly_source_name(article.source_id), "source_url": article.source_url,
            "publish_date": article.publish_date, "event_id": candidate.event_id,
            "content_type": article.content_type, "content_kind": candidate.content_kind,
            "content_value": candidate.content_value, "score_dimension": "content",
            "analysis_basis": analysis.analysis_basis,
            "summary": analysis.summary or "",
            "reason": (analysis.score_reason if lane == SelectionLane.LISTEN else analysis.content_value_reason) or "",
            "duration_seconds": positive_duration(ext.get("duration_seconds")) if lane == SelectionLane.LISTEN else None,
            "guide_duration_seconds": audio_durations.get((article.id, guide.get("audio_artifact_id"))),
        })
    return result


def select_recommendations(session: Session, pool: list[dict], *, report_date: str, excluded_ids=()) -> list[dict]:
    """Keep today's snapshots first, then fill by content value; never fill with weak items."""
    since = (dt.date.fromisoformat(report_date) - dt.timedelta(days=7)).isoformat()
    tomorrow = (dt.date.fromisoformat(report_date) + dt.timedelta(days=1)).isoformat()
    reports = session.exec(select(ArticleRecord).where(
        ArticleRecord.source_id == "dorami_daily_brief",
        ArticleRecord.publish_date >= since, ArticleRecord.publish_date < tomorrow,
    )).all()
    prior, excluded, excluded_events = [], set(excluded_ids), set()
    for report in reports:
        ext = _mapping(report.extensions_json)
        if report.id == f"daily_brief_{report_date}":
            prior = _entries(ext)
        elif str(report.publish_date)[:10] < report_date:
            included = ext.get("included_article_ids")
            if isinstance(included, list):
                excluded.update(i for i in included if isinstance(i, str))
            for entry in _entries(ext):
                excluded.add(entry["id"])
                if entry.get("event_id") is not None:
                    excluded_events.add(entry["event_id"])
    if excluded:
        excluded_events.update(session.exec(select(EventMemberRecord.event_id).where(
            EventMemberRecord.article_id.in_(sorted(excluded)),
        )).all())
    available = {entry["id"]: entry for entry in pool}
    # A changed source scope, visibility or missing evidence removes a previous
    # recommendation on an explicit same-day rebuild. Old dates are never edited.
    prior = [entry for entry in prior if entry["id"] in available]
    def rank(entry):
        published = parse_article_time(entry.get("publish_date"))
        return (-float(entry["content_value"]), -published.timestamp() if published else 0, entry["id"])

    order = prior + sorted(pool, key=rank)
    chosen, counts, sources = [], {}, set()
    for entry in order:
        lane = entry.get("selection_lane")
        policy = POLICIES.get(lane)
        if policy is None or entry["id"] in excluded:
            continue
        if entry.get("event_id") is not None and entry["event_id"] in excluded_events:
            continue
        key = (lane, entry.get("source_id"))
        if counts.get(lane, 0) >= policy.max_items or key in sources:
            continue
        chosen.append(entry)
        counts[lane] = counts.get(lane, 0) + 1
        sources.add(key)
        excluded.add(entry["id"])
        if entry.get("event_id") is not None:
            excluded_events.add(entry["event_id"])
    return chosen


def _text(value) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", str(value or "").replace("\n", " "))


def render_sections(entries: list[dict]) -> list[str]:
    parts = []
    for lane, title, unit in (("read", "深度阅读", "篇"), ("listen", "播客精选", "集")):
        selected = [entry for entry in entries if entry.get("selection_lane") == lane]
        if not selected:
            continue
        parts.extend([f"## {title}（{len(selected)} {unit}）", ""])
        for entry in selected:
            title_text = _text(entry.get("title")) or "（无标题）"
            url = str(entry.get("source_url") or "")
            try:
                parsed_url = urlsplit(url)
                safe_url = parsed_url.scheme.lower() in {"http", "https"} and bool(parsed_url.netloc)
            except ValueError:
                safe_url = False
            if safe_url:
                url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20").replace("\n", "")
                parts.append(f"### [{title_text}]({url})")
            else:
                parts.append(f"### {title_text}")
            basis = "全文内容价值" if lane == "listen" else "内容价值"
            parts.append(f"**来源**: {_text(entry.get('source'))} · {basis} {float(entry['content_value']):.1f}")
            durations = []
            for key, label in (("duration_seconds", "原节目"), ("guide_duration_seconds", "导读")):
                seconds = positive_duration(entry.get(key))
                if seconds:
                    durations.append(f"{label}约 {math.ceil(seconds / 60)} 分钟")
            if durations:
                parts.append(" · ".join(durations))
            if entry.get("reason"):
                parts.append(f"**推荐理由**：{_text(entry['reason'])}")
            if entry.get("summary"):
                parts.append(_text(entry["summary"]))
            parts.extend(["", "---", ""])
    return parts
