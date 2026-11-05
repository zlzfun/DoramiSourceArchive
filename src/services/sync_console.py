"""节点同步控制台(issue #261):只读聚合 + 单流重置。

控制台把「本机作为接收端从其它 server 拉取」的 Archive Sync v2 状态摊开给管理员看:
目标与协商、各流 checkpoint、最近一次测试连接、本机各类内容条数、首页摘要;唯一的写
操作是「重置单流」(重放分析流 = 重置 analyses)。

出网不扩大:本模块不发任何网络请求;页面打开时读的都是本机库与 KV。外网信息只来自管理员
点「测试连接」或同步任务本身落下的 KV(``remote_sync:probes``)。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, or_
from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArchiveSyncEntityStateRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagCandidateEvidenceRecord,
    CmsTagRecord,
    EventRecord,
    JobRecord,
    MediaAssetRecord,
    PodcastArtifactRecord,
    PodcastTextPublicationRecord,
    SourceConfigRecord,
    SourceStateRecord,
)
from services import archive_sync_v2
from services import remote_sync
from services import sync_consumer_policy

PROBE_STATE_KEY = "remote_sync:probes"

# 播客文本按读者能认出的三类汇总(kind 取值见 podcast_text_reader / archive_sync_v2)。
_PODCAST_TEXT_GROUPS = {
    "podcast_transcripts": ("transcript_zh", "publisher_transcript", "normalized_transcript"),
    "podcast_blogs": ("digest_blog_zh",),
    "podcast_scripts": ("narration_script_zh",),
}

_ACTIVE_JOB_STATUSES = ("queued", "running")


def _load_json_kv(session: Session, key: str) -> Dict[str, Any]:
    record = session.get(AppSettingRecord, key)
    if record is None or not record.value:
        return {}
    try:
        value = json.loads(record.value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _save_json_kv(session: Session, key: str, value: Dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False)
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=raw)
    else:
        record.value = raw
    session.add(record)


# ── 测试连接结果(页头常驻显示用) ──

_PROBE_FIELDS = (
    "version", "schema_version", "capabilities", "authority_id", "taxonomy_version",
    "taxonomy_ready", "article_total", "snapshot", "pending", "pending_since",
)


def record_probe(engine, probe: Dict[str, Any], *, probed_at: str) -> None:
    """把一次成功的 v2 探针结果按 base_url 落 KV(不含凭据)。"""

    base_url = str(probe.get("base_url") or "")
    if not base_url or probe.get("protocol") != "v2":
        return
    with Session(engine) as session:
        probes = _load_json_kv(session, PROBE_STATE_KEY)
        probes[base_url] = {
            **{key: probe.get(key) for key in _PROBE_FIELDS},
            "probed_at": probed_at,
        }
        _save_json_kv(session, PROBE_STATE_KEY, probes)
        session.commit()


def load_probe(engine, base_url: str) -> Optional[Dict[str, Any]]:
    if not base_url:
        return None
    with Session(engine) as session:
        value = _load_json_kv(session, PROBE_STATE_KEY).get(base_url)
    return value if isinstance(value, dict) else None


# ── 目标解析 ──

def resolve_target(state: Dict[str, Any], schedule: Dict[str, Any]) -> str:
    """控制台的「当前目标」:定时配置的远端优先,否则最近一次同步过的目标。"""

    targets = state.get("targets") or {}
    scheduled = str(schedule.get("base_url") or "").strip()
    if scheduled:
        try:
            return remote_sync.normalize_base_url(scheduled)
        except remote_sync.RemoteSyncError:
            pass
    best = ""
    best_at = ""
    for base_url, target in targets.items():
        if not isinstance(target, dict):
            continue
        synced_at = str(target.get("last_synced_at") or "")
        if not best or synced_at > best_at:
            best, best_at = base_url, synced_at
    return best


def sync_checkpoint_since(engine, base_url: str) -> str:
    """测试连接用的 since:目标 sources 流已完成的 snapshot(没有则空 = 全量计数)。"""

    target = (remote_sync.load_sync_state(engine).get("targets") or {}).get(base_url) or {}
    checkpoint = (target.get("v2_streams") or {}).get("sources") or {}
    return str(checkpoint.get("snapshot") or "") if isinstance(checkpoint, dict) else ""


def _consumer_fence(session: Session) -> Optional[Dict[str, Any]]:
    value = _load_json_kv(session, sync_consumer_policy.V2_CONSUMER_MODE_KEY)
    if not value:
        return None
    return {
        "active": bool(value.get("active")),
        "activated_at": str(value.get("activated_at") or ""),
        "reason": str(value.get("reason") or ""),
    }


def console_status(
    engine,
    *,
    media_store_ready: bool,
    podcast_store_ready: bool,
) -> Dict[str, Any]:
    """``GET /api/admin/remote-sync/status`` 的 ``console`` 块(契约见方案 §4.3)。"""

    state = remote_sync.load_sync_state(engine)
    schedule = remote_sync.load_schedule(engine)
    base_url = resolve_target(state, schedule)
    target = (state.get("targets") or {}).get(base_url) or {} if base_url else {}
    capabilities = list(target.get("v2_capabilities") or [])
    order = (
        list(remote_sync.negotiated_v2_stream_order(capabilities))
        if capabilities
        else list(remote_sync.V2_STREAM_ORDER)
    )
    with Session(engine) as session:
        fence = _consumer_fence(session)
    generation = target.get("v2_analysis_payload_generation")
    # 控制台是 v2 流水线:只有已与该目标建立 v2 同步(有 authority)才算接入;只有 v1 兼容记录的老目标
    # 显示接入卡(预填地址 / 账号),运行记录照常列出(issue #261)。
    v2_established = bool(target.get("v2_authority_id"))
    return {
        "target": base_url or None,
        "configured": bool(base_url and v2_established),
        "protocol": "v2" if v2_established else ("v1" if target else ""),
        "stream_order": order,
        "all_streams": list(remote_sync.V2_STREAM_ORDER),
        "authority_id": str(target.get("v2_authority_id") or ""),
        "schema_version": str(target.get("v2_schema_version") or ""),
        "capabilities": capabilities,
        "username": str(target.get("username") or ""),
        "last_synced_at": str(target.get("last_synced_at") or ""),
        "rebased_at": str(target.get("v2_rebased_at") or ""),
        "checkpoints": {
            stream: checkpoint
            for stream, checkpoint in (target.get("v2_streams") or {}).items()
            if isinstance(checkpoint, dict)
        },
        "consumer_fence": fence,
        "local_stores": {"media": bool(media_store_ready), "podcast": bool(podcast_store_ready)},
        "probe": load_probe(engine, base_url) if base_url else None,
        "resets": dict(target.get("v2_stream_resets") or {}),
        "analysis_generation": {
            "current": archive_sync_v2.ANALYSIS_PAYLOAD_GENERATION,
            "recorded": int(generation) if isinstance(generation, int) else None,
            "refetch_at": str(target.get("v2_analysis_refetch_at") or ""),
            "refetch_by": str(target.get("v2_analysis_refetch_by") or ""),
        },
        "schedule": {
            key: schedule.get(key)
            for key in (
                "enabled", "cron", "protocol", "base_url", "username", "password_set",
                "updated_at", "migration_required", "protocol_downgrade_blocked",
            )
        },
    }


def job_creators(engine, job_ids: list[str]) -> Dict[str, Optional[str]]:
    """同步任务的发起人(定时任务为空)。``jobs._record_to_dict`` 是全任务类型共用形状,不在那里加列。"""

    ids = [job_id for job_id in job_ids if job_id]
    if not ids:
        return {}
    with Session(engine) as session:
        rows = session.exec(
            select(JobRecord.id, JobRecord.created_by).where(JobRecord.id.in_(ids))
        ).all()
    return {str(job_id): created_by for job_id, created_by in rows}


# ── 本机条数 ──

def _count(session: Session, statement) -> int:
    return int(session.exec(statement).one() or 0)


def inventory(engine) -> Dict[str, Any]:
    """本机各类同步内容条数(契约见方案 §4.4)。按需取,不随运行中轮询。"""

    state = remote_sync.load_sync_state(engine)
    schedule = remote_sync.load_schedule(engine)
    base_url = resolve_target(state, schedule)
    target = (state.get("targets") or {}).get(base_url) or {} if base_url else {}
    authority = str(target.get("v2_authority_id") or "")
    result: Dict[str, Any] = {"target": base_url or None, "authority_id": authority, "streams": {}, "items": {}}
    if not authority:
        return result
    with Session(engine) as session:
        streams: Dict[str, Dict[str, int]] = {}
        for stream, operation, count in session.exec(
            select(
                ArchiveSyncEntityStateRecord.stream,
                ArchiveSyncEntityStateRecord.operation,
                func.count(),
            )
            .where(ArchiveSyncEntityStateRecord.authority_id == authority)
            .group_by(ArchiveSyncEntityStateRecord.stream, ArchiveSyncEntityStateRecord.operation)
        ).all():
            streams.setdefault(str(stream), {"upsert": 0, "tombstone": 0})[str(operation)] = int(count)
        result["streams"] = streams

        articles_q = select(func.count()).select_from(ArticleRecord).where(
            ArticleRecord.analysis_authority_id == authority
        )
        translation_like = or_(
            ArticleRecord.extensions_json.like('%"translation_zh"%'),
            ArticleRecord.extensions_json.like('%"translation_zh_social"%'),
        )
        analyses_base = select(func.count()).select_from(ArticleAnalysisRecord).where(
            ArticleAnalysisRecord.authority_id == authority
        )
        items: Dict[str, int] = {
            "articles": _count(session, articles_q),
            "daily_briefs": _count(
                session,
                articles_q.where(ArticleRecord.source_id == "dorami_daily_brief"),
            ),
            "translations": _count(session, articles_q.where(translation_like)),
            "analyses": _count(session, analyses_base),
            "summaries": _count(session, analyses_base.where(ArticleAnalysisRecord.summary != "")),
            "content_values": _count(
                session, analyses_base.where(ArticleAnalysisRecord.content_value_version != "")
            ),
            "llm_tags": _count(
                session,
                select(func.count())
                .select_from(ArticleTagAssignmentRecord)
                .join(ArticleRecord, ArticleRecord.id == ArticleTagAssignmentRecord.article_id)
                .where(
                    ArticleRecord.analysis_authority_id == authority,
                    ArticleTagAssignmentRecord.assignment_source == "llm",
                ),
            ),
            "events": _count(
                session,
                select(func.count()).select_from(EventRecord).where(EventRecord.authority_id == authority),
            ),
            "podcast_audio": _count(
                session,
                select(func.count()).select_from(PodcastArtifactRecord).where(
                    PodcastArtifactRecord.authority_id == authority,
                    PodcastArtifactRecord.kind == "digest_audio_zh",
                    PodcastArtifactRecord.status == "published",
                ),
            ),
            "media": _count(
                session,
                select(func.count()).select_from(MediaAssetRecord).where(
                    MediaAssetRecord.sync_authority_id == authority
                ),
            ),
            "sources": _count(
                session,
                select(func.count()).select_from(SourceConfigRecord).where(
                    SourceConfigRecord.collection_authority_id == authority
                ),
            ),
            "source_states": _count(
                session,
                select(func.count()).select_from(SourceStateRecord).where(
                    SourceStateRecord.authority_id == authority
                ),
            ),
            "taxonomy_tags": _count(session, select(func.count()).select_from(CmsTagRecord)),
            "candidate_evidence": _count(
                session,
                select(func.count())
                .select_from(CmsTagCandidateEvidenceRecord)
                .where(CmsTagCandidateEvidenceRecord.source_id.startswith("user_rss_", autoescape=True)),
            ),
        }
        by_kind: Dict[str, int] = {}
        for kind, count in session.exec(
            select(PodcastTextPublicationRecord.kind, func.count())
            .where(
                PodcastTextPublicationRecord.authority_id == authority,
                PodcastTextPublicationRecord.status == "published",
            )
            .group_by(PodcastTextPublicationRecord.kind)
        ).all():
            by_kind[str(kind)] = int(count)
        for key, kinds in _PODCAST_TEXT_GROUPS.items():
            items[key] = sum(by_kind.get(kind, 0) for kind in kinds)
        result["items"] = items
        result["podcast_texts_by_kind"] = by_kind
    return result


# ── 单流重置 ──

class SyncConsoleError(Exception):
    """面向管理员界面直接展示的冲突 / 前置条件错误(路由转 409)。"""


def reset_stream(engine, stream: str, *, by: str, at: str) -> Dict[str, Any]:
    """清掉当前目标某条流的 checkpoint 与本机对该 authority 的逐行应用记账。

    与 ``remote_sync._refetch_outdated_analysis_payloads`` 同一做法、推广到任一流:下次同步该流
    从头整流重拉、逐行覆盖并重建记账,收尾按协议只删经生产者 presence 确认不存在的行
    (articles / analyses / events / 两条播客流)。taxonomy 没有逐行记账,只清 checkpoint。
    """

    if stream not in remote_sync.V2_STREAM_ORDER:
        raise ValueError(f"unknown stream: {stream}")
    schedule = remote_sync.load_schedule(engine)
    with Session(engine) as session:
        active = session.exec(
            select(JobRecord.id).where(
                JobRecord.type == remote_sync.REMOTE_SYNC_JOB_TYPE,
                JobRecord.status.in_(_ACTIVE_JOB_STATUSES),
            )
        ).first()
        if active is not None:
            raise SyncConsoleError("同步任务运行中,不能重置")
        record = session.get(AppSettingRecord, remote_sync.REMOTE_SYNC_STATE_KEY)
        state = remote_sync._decode_sync_state_record(record)
        base_url = resolve_target(state, schedule)
        targets = dict(state["targets"])
        target = dict(targets.get(base_url) or {}) if base_url else {}
        authority = str(target.get("v2_authority_id") or "")
        if not authority:
            raise SyncConsoleError("尚未与远端建立 v2 同步")
        capabilities = list(target.get("v2_capabilities") or [])
        if capabilities and stream not in remote_sync.negotiated_v2_stream_order(capabilities):
            raise SyncConsoleError("远端未协商这条流")
        streams = dict(target.get("v2_streams") or {})
        streams.pop(stream, None)
        target["v2_streams"] = streams
        if stream != "taxonomy":
            session.exec(
                delete(ArchiveSyncEntityStateRecord).where(
                    ArchiveSyncEntityStateRecord.stream == stream,
                    ArchiveSyncEntityStateRecord.authority_id == authority,
                )
            )
        resets = dict(target.get("v2_stream_resets") or {})
        resets[stream] = {"at": at, "by": by}
        target["v2_stream_resets"] = resets
        if stream == "analyses":
            target["v2_analysis_refetch_at"] = at
            target["v2_analysis_refetch_by"] = by
        targets[base_url] = target
        state["targets"] = targets
        value = json.dumps(state, ensure_ascii=False)
        if record is None:
            record = AppSettingRecord(key=remote_sync.REMOTE_SYNC_STATE_KEY, value=value)
        else:
            record.value = value
        session.add(record)
        session.commit()
    return {"stream": stream, "reset_at": at, "target": base_url}


# ── 首页摘要 ──

def home_summary(engine) -> Dict[str, Any]:
    """``GET /api/admin/home`` 的 ``sync`` 块(契约见方案 §4.7)。"""

    state = remote_sync.load_sync_state(engine)
    schedule = remote_sync.load_schedule(engine)
    base_url = resolve_target(state, schedule)
    target = (state.get("targets") or {}).get(base_url) or {} if base_url else {}
    summary: Dict[str, Any] = {
        "configured": bool(base_url and target.get("v2_authority_id")),
        "status": "none",
        "last_success_at": "",
        "last_run_at": "",
        "running": None,
        "failed": None,
    }
    if not summary["configured"]:
        return summary
    with Session(engine) as session:
        jobs = session.exec(
            select(JobRecord)
            .where(JobRecord.type == remote_sync.REMOTE_SYNC_JOB_TYPE)
            .order_by(JobRecord.created_at.desc())
            .limit(10)
        ).all()
    last_success = next((job for job in jobs if job.status == "succeeded"), None)
    if last_success is not None and last_success.ended_at:
        summary["last_success_at"] = _epoch_iso(last_success.ended_at)
    elif target.get("last_synced_at"):
        summary["last_success_at"] = str(target.get("last_synced_at"))
    latest = jobs[0] if jobs else None
    if latest is None:
        summary["status"] = "idle"
        return summary
    summary["last_run_at"] = _epoch_iso(latest.started_at or latest.created_at)
    progress = _json_or_none(latest.progress_json) or {}
    order = progress.get("order") or list(
        remote_sync.negotiated_v2_stream_order(list(target.get("v2_capabilities") or []))
        if target.get("v2_capabilities")
        else remote_sync.V2_STREAM_ORDER
    )
    done = len(progress.get("streams_done") or {})
    if latest.status in _ACTIVE_JOB_STATUSES:
        summary["status"] = "running"
        summary["running"] = {"done": done, "total": len(order), "stream": progress.get("stream")}
    elif latest.status == "failed":
        summary["status"] = "failed"
        summary["failed"] = {
            "stream": progress.get("stream"),
            "page": progress.get("page") or None,
            "error": (latest.error or "")[:200],
        }
    else:
        summary["status"] = "idle"
    return summary


def _json_or_none(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _epoch_iso(value: Any) -> str:
    import datetime as dt

    try:
        return dt.datetime.fromtimestamp(float(value), dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
