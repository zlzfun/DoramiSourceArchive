"""Transactional promotion of a private custom Podcast into a public catalog node.

The Article primary key is the durable identity of every downstream Podcast asset.
Promotion therefore changes only source-level identities and deliberately leaves the
article/episode IDs untouched.  The caller owns the transaction and must roll it back
on every exception.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleRecord,
    CmsTagCandidateEvidenceRecord,
    CollectionJobRecord,
    FetchRunRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderReadCursorRecord,
    ReaderReadRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
    SourceStateRecord,
)
from services.user_sources import canonical_feed_url


_SOURCE_ID_SETTING_KEYS = frozenset(
    {
        "daily_brief_source_ids",
        "reader_default_source_ids",
        "reader_hidden_source_ids",
    }
)


@dataclass(frozen=True)
class AdoptionResult:
    old_source_id: str
    new_source_id: str
    article_count: int
    subscription_count: int


def _json(raw: str, *, context: str) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} 包含无效 JSON，停止播客源收养") from exc


def _replace_values(values: list[Any], old: str, new: str) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        replacement = new if str(value) == old else value
        key = str(replacement)
        if key in seen:
            continue
        seen.add(key)
        result.append(replacement)
    return result


def _replace_filter_source_ids(raw: str, old: str, new: str, *, context: str) -> tuple[str, bool]:
    if old not in (raw or ""):
        return raw, False
    data = _json(raw, context=context)
    if not isinstance(data, dict):
        raise ValueError(f"{context} 不是 JSON 对象，停止播客源收养")
    changed = False
    if str(data.get("source_id") or "") == old:
        data["source_id"] = new
        changed = True
    values = data.get("source_ids")
    if isinstance(values, str):
        source_ids = [item.strip() for item in values.split(",") if item.strip()]
        replaced = [str(item) for item in _replace_values(source_ids, old, new)]
        if source_ids != replaced:
            data["source_ids"] = ",".join(replaced)
            changed = True
    elif isinstance(values, list):
        replaced = _replace_values(values, old, new)
        if values != replaced:
            data["source_ids"] = replaced
            changed = True
    elif values is not None:
        raise ValueError(f"{context}.source_ids 类型无效，停止播客源收养")
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True) if changed else raw,
        changed,
    )


def find_custom_podcast_for_feed(
    session: Session,
    *,
    feed_url: str,
    target_source_id: str,
) -> SourceConfigRecord | None:
    """Return the unique compatible custom source, failing closed on ambiguity."""

    canonical = canonical_feed_url(feed_url)
    matches: list[SourceConfigRecord] = []
    for record in session.exec(
        select(SourceConfigRecord).where(SourceConfigRecord.owner_username != "")
    ).all():
        try:
            same_feed = canonical_feed_url(record.url) == canonical
        except ValueError:
            same_feed = False
        if same_feed:
            matches.append(record)
    if len(matches) > 1:
        ids = ", ".join(sorted(row.source_id for row in matches))
        raise ValueError(f"同一 Podcast feed 存在多个自定源（{ids}），停止自动收养")
    if not matches:
        return None

    record = matches[0]
    if session.get(SourceConfigRecord, target_source_id) is not None:
        raise ValueError(
            f"公共源 {target_source_id} 与自定源 {record.source_id} 同时存在，停止自动收养"
        )
    if not record.source_id.startswith("user_rss_"):
        raise ValueError(f"存量源 {record.source_id} 不在自定源命名空间，停止自动收养")
    if (record.source_type or "").strip().lower() not in {"podcast", "podcast_rss"}:
        raise ValueError(f"存量源 {record.source_id} 不是 Podcast 类型，停止自动收养")
    if (record.fetcher_id or "").strip() not in {"", "generic_podcast_rss"}:
        raise ValueError(f"存量源 {record.source_id} 使用了非 Podcast 抓取器，停止自动收养")
    if not record.is_active or record.retired_at is not None:
        raise ValueError(f"存量源 {record.source_id} 已停用或进入退役流程，停止自动收养")
    if (record.collection_authority_id or "").strip():
        raise ValueError(f"存量源 {record.source_id} 由远端采集权威管理，停止自动收养")
    state = session.get(SourceStateRecord, record.source_id)
    if state is not None and (state.authority_id or "").strip():
        raise ValueError(f"存量源 {record.source_id} 的状态由远端权威管理，停止自动收养")
    return record


def _preflight_source_level_state(session: Session, old: str, new: str) -> None:
    if session.get(SourceStateRecord, new) is not None:
        raise ValueError(f"公共源 {new} 已存在 SourceState，停止自动收养")
    if session.exec(
        select(ArticleRecord.id).where(ArticleRecord.source_id == new).limit(1)
    ).first() is not None:
        raise ValueError(f"公共源 {new} 已存在文章，停止自动收养")

    for subscription in session.exec(select(ReaderSubscriptionRecord)).all():
        _replace_filter_source_ids(
            subscription.filters_json,
            old,
            new,
            context=f"订阅 {subscription.id}",
        )
    for job in session.exec(select(CollectionJobRecord)).all():
        if old in (job.fetcher_ids_json or ""):
            values = _json(job.fetcher_ids_json, context=f"采集任务 {job.id}.fetcher_ids")
            if not isinstance(values, list):
                raise ValueError(f"采集任务 {job.id}.fetcher_ids 不是数组，停止自动收养")
        if old in (job.per_fetcher_params_json or ""):
            values = _json(job.per_fetcher_params_json, context=f"采集任务 {job.id}.params")
            if not isinstance(values, dict):
                raise ValueError(f"采集任务 {job.id}.params 不是对象，停止自动收养")
            if old in values and new in values and values[old] != values[new]:
                raise ValueError(f"采集任务 {job.id} 同时含有冲突的新旧源参数，停止自动收养")
    for setting in session.exec(
        select(AppSettingRecord).where(AppSettingRecord.key.in_(_SOURCE_ID_SETTING_KEYS))
    ).all():
        if old in (setting.value or ""):
            values = _json(setting.value, context=f"设置 {setting.key}")
            if not isinstance(values, list):
                raise ValueError(f"设置 {setting.key} 不是数组，停止自动收养")


def _migrate_subscriptions(session: Session, old: str, new: str, now: str) -> int:
    count = 0
    for record in session.exec(select(ReaderSubscriptionRecord)).all():
        replacement, changed = _replace_filter_source_ids(
            record.filters_json,
            old,
            new,
            context=f"订阅 {record.id}",
        )
        if changed:
            record.filters_json = replacement
            record.updated_at = now
            session.add(record)
            count += 1
    return count


def _migrate_read_cursors(session: Session, old: str, new: str) -> None:
    old_rows = session.exec(
        select(ReaderReadCursorRecord).where(ReaderReadCursorRecord.source_id == old)
    ).all()
    for row in old_rows:
        target = session.get(ReaderReadCursorRecord, (row.owner_username, new))
        if target is None:
            row.source_id = new
            session.add(row)
            continue
        target.mark_read_before = max(target.mark_read_before or "", row.mark_read_before or "")
        target.updated_at = max(target.updated_at or "", row.updated_at or "")
        session.add(target)
        session.delete(row)


def _migrate_read_metrics(session: Session, old: str, new: str) -> None:
    old_rows = session.exec(
        select(ReaderReadRecord).where(ReaderReadRecord.source_id == old)
    ).all()
    for row in old_rows:
        target = session.exec(
            select(ReaderReadRecord).where(
                ReaderReadRecord.day == row.day,
                ReaderReadRecord.username == row.username,
                ReaderReadRecord.source_id == new,
            )
        ).first()
        if target is None:
            row.source_id = new
            session.add(row)
            continue
        target.reads += row.reads
        target.updated_at = max(target.updated_at or "", row.updated_at or "")
        session.add(target)
        session.delete(row)


def _migrate_json_source_lists(session: Session, old: str, new: str) -> None:
    for edition in session.exec(select(PersonalDigestEditionRecord)).all():
        for field in ("expected_source_ids_json", "due_source_ids_json"):
            raw = getattr(edition, field)
            if old not in (raw or ""):
                continue
            values = _json(raw, context=f"个人早报 {edition.id}.{field}")
            if not isinstance(values, list):
                raise ValueError(f"个人早报 {edition.id}.{field} 不是数组，停止自动收养")
            setattr(
                edition,
                field,
                json.dumps(_replace_values(values, old, new), ensure_ascii=False),
            )
            session.add(edition)
        snapshot_raw = edition.source_state_snapshot_json
        if old in (snapshot_raw or ""):
            snapshot = _json(
                snapshot_raw, context=f"个人早报 {edition.id}.source_state_snapshot"
            )
            if not isinstance(snapshot, dict):
                raise ValueError(
                    f"个人早报 {edition.id}.source_state_snapshot 不是对象，停止自动收养"
                )
            if old in snapshot:
                if new in snapshot and snapshot[new] != snapshot[old]:
                    raise ValueError(
                        f"个人早报 {edition.id} 同时含有冲突的新旧源快照，停止自动收养"
                    )
                snapshot.setdefault(new, snapshot.pop(old))
                edition.source_state_snapshot_json = json.dumps(
                    snapshot, ensure_ascii=False, sort_keys=True
                )
                session.add(edition)

    for item in session.exec(select(PersonalDigestItemRecord)).all():
        if old not in (item.snapshot_json or ""):
            continue
        snapshot = _json(item.snapshot_json, context=f"个人早报条目 {item.id}.snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"个人早报条目 {item.id}.snapshot 不是对象，停止自动收养")
        if str(snapshot.get("source_id") or "") == old:
            snapshot["source_id"] = new
            item.snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            session.add(item)

    for setting in session.exec(
        select(AppSettingRecord).where(AppSettingRecord.key.in_(_SOURCE_ID_SETTING_KEYS))
    ).all():
        if old not in (setting.value or ""):
            continue
        values = _json(setting.value, context=f"设置 {setting.key}")
        setting.value = json.dumps(
            _replace_values(values, old, new), ensure_ascii=False, sort_keys=True
        )
        session.add(setting)


def _migrate_collection_jobs(session: Session, old: str, new: str, now: str) -> None:
    for job in session.exec(select(CollectionJobRecord)).all():
        changed = False
        if old in (job.fetcher_ids_json or ""):
            values = _json(job.fetcher_ids_json, context=f"采集任务 {job.id}.fetcher_ids")
            replacement = _replace_values(values, old, new)
            if values != replacement:
                job.fetcher_ids_json = json.dumps(replacement, ensure_ascii=False, sort_keys=True)
                changed = True
        if old in (job.per_fetcher_params_json or ""):
            params = _json(job.per_fetcher_params_json, context=f"采集任务 {job.id}.params")
            if old in params:
                old_params = params.pop(old)
                params.setdefault(new, old_params)
                job.per_fetcher_params_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
                changed = True
        if changed:
            job.updated_at = now
            session.add(job)


def adopt_custom_podcast_source(
    session: Session,
    *,
    custom_source: SourceConfigRecord,
    public_source: SourceConfigRecord,
    now: str,
) -> AdoptionResult:
    """Move every mutable source-level reference and remove the private config last."""

    old = custom_source.source_id
    new = public_source.source_id
    _preflight_source_level_state(session, old, new)
    session.flush()  # Archive Sync triggers must see the new public SourceConfig.

    articles = list(
        session.exec(select(ArticleRecord).where(ArticleRecord.source_id == old)).all()
    )
    article_ids = [row.id for row in articles]
    subscription_count = _migrate_subscriptions(session, old, new, now)
    _migrate_read_cursors(session, old, new)
    _migrate_read_metrics(session, old, new)
    _migrate_json_source_lists(session, old, new)
    _migrate_collection_jobs(session, old, new, now)

    session.exec(
        update(CmsTagCandidateEvidenceRecord)
        .where(CmsTagCandidateEvidenceRecord.source_id == old)
        .values(source_id=new)
    )
    # Keep the node's operational history reachable from the promoted public
    # identity.  params_json is an immutable run snapshot and deliberately keeps
    # the original entry namespace/feed facts.
    session.exec(
        update(FetchRunRecord)
        .where(FetchRunRecord.fetcher_id == old)
        .values(fetcher_id=new)
    )
    for article in articles:
        try:
            extensions = json.loads(article.extensions_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"单集 {article.id} 扩展字段损坏，停止自动收养"
            ) from exc
        if not isinstance(extensions, dict):
            raise ValueError(f"单集 {article.id} 扩展字段不是对象，停止自动收养")
        # Promotion must not turn an existing private archive into an automatic
        # historical TTS backfill.  Forced/on-demand guide generation remains
        # available; newly fetched public episodes do not carry this marker.
        extensions["premium_guide_auto_suppressed"] = True
        extensions["premium_guide_auto_suppressed_reason"] = (
            "custom_source_promotion_history"
        )
        article.extensions_json = json.dumps(
            extensions, ensure_ascii=False, sort_keys=True
        )
        article.source_id = new
        session.add(article)
    session.exec(
        update(SourceStateRecord).where(SourceStateRecord.source_id == old).values(source_id=new)
    )
    session.flush()

    if session.exec(
        select(ArticleRecord.id).where(ArticleRecord.source_id == old).limit(1)
    ).first() is not None:
        raise ValueError("仍有文章引用旧自定源，停止自动收养")
    if session.get(SourceStateRecord, old) is not None:
        raise ValueError("仍有 SourceState 引用旧自定源，停止自动收养")
    if session.exec(
        select(ReaderReadCursorRecord.owner_username)
        .where(ReaderReadCursorRecord.source_id == old)
        .limit(1)
    ).first() is not None:
        raise ValueError("仍有未读水位引用旧自定源，停止自动收养")
    if session.exec(
        select(ReaderReadRecord.id).where(ReaderReadRecord.source_id == old).limit(1)
    ).first() is not None:
        raise ValueError("仍有阅读计量引用旧自定源，停止自动收养")
    if session.exec(
        select(CmsTagCandidateEvidenceRecord.article_id)
        .where(CmsTagCandidateEvidenceRecord.source_id == old)
        .limit(1)
    ).first() is not None:
        raise ValueError("仍有标签证据引用旧自定源，停止自动收养")
    if session.exec(
        select(FetchRunRecord.id).where(FetchRunRecord.fetcher_id == old).limit(1)
    ).first() is not None:
        raise ValueError("仍有采集运行史引用旧自定源，停止自动收养")
    for record in session.exec(select(ReaderSubscriptionRecord)).all():
        if old in (record.filters_json or ""):
            raise ValueError("仍有订阅引用旧自定源，停止自动收养")
    for job in session.exec(select(CollectionJobRecord)).all():
        if old in (job.fetcher_ids_json or "") or old in (
            job.per_fetcher_params_json or ""
        ):
            raise ValueError("仍有采集任务引用旧自定源，停止自动收养")
    for setting in session.exec(
        select(AppSettingRecord).where(AppSettingRecord.key.in_(_SOURCE_ID_SETTING_KEYS))
    ).all():
        if old in (setting.value or ""):
            raise ValueError("仍有运行设置引用旧自定源，停止自动收养")

    session.delete(custom_source)
    session.flush()
    return AdoptionResult(
        old_source_id=old,
        new_source_id=new,
        article_count=len(article_ids),
        subscription_count=subscription_count,
    )
