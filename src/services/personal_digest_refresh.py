"""个人早报的系统换版(issue #343):读者面不留重编入口,三种情况由巡检自动处理。

1. **新内容落地**:同步的分析 / 事件流在本机落地,或本机分析跑完一批——今天还带「未就绪」标记的
   版按当时的内容重新选篇,条目有变才换版(原因 ``content_arrived``)。
2. **读者改了兴趣 / 订阅**:写入点在今日版本上记意向(``request_preference_rebuild``),这里
   静置合并后落成一版;漏记的由补扫兜底。
3. **生成失败**:按递增间隔重试(原因 ``recovery``)。

1、2 不建「编排中」的中间版本:先试算、把标题译文备进文章缓存,再拿写锁——锁内重新核对前提、
用当下的范围重新选篇,终态、最终条目(含标题)、机器人投影与旧版让位在一个事务里提交。读者端与
机器人接口(只取最大 revision、不看状态)都看不到中间态。失败重试例外:当前版本本来就是失败的,
走既有的带租约生成通道。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from models.analysis_contracts import DigestGenerationReason, PersonalDigestStatus
from models.db import (
    AppSettingRecord,
    ArticleRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    SourceConfigRecord,
    UserRecord,
)
from services import personal_digest as digest
from services import personal_digest_titles as digest_titles


logger = logging.getLogger("dorami.personal_digest")

SHANGHAI = digest.SHANGHAI

# 巡检的处理水位与本机分析落地时刻(节点本地,不同步)。
CONTENT_REFRESH_STATE_KEY = "personal_digest:content_refresh"
LOCAL_ANALYSIS_MARK_KEY = "personal_digest:local_analysis_at"
REMOTE_SYNC_STATE_KEY = "remote_sync:state"
# 同步落库处记下的「分析 / 事件流最近一次确有变化」的本机时刻(remote_sync.record_v2_stream_success)。
SYNC_LANDED_AT_KEY = "v2_content_landed_at"
SYNC_LANDING_STREAMS = ("analyses", "events")

# 内容落地后先等一等再补编:同步的几条流、相邻的采集任务会连着来。
CONTENT_QUIET_SECONDS = 180
# 内容一直零星地来、总也静不下来时,最多等这么久就补编(否则一上午都轮不到)。
CONTENT_MAX_WAIT_SECONDS = 900
# 早报是上午的东西:过了这个时刻不再因新内容换版(偏好改动与失败重试不受限)。
CONTENT_CUTOFF = dt.time(12, 0)
# 一批内容里有账号补编失败时,整批最多再试几轮;之后放弃这一批,等下一批内容。
CONTENT_PASS_MAX_TRIES = 3
# 读者连点几个标签、连订几个源:停手这么久才落成一版。静置一满就编(announce_preference_intent 让宿主
# 到点跑一次),不等 30 秒巡检的下一拍;没有宿主时由巡检接手。
PREFERENCE_QUIET_SECONDS = 10
# 到点任务比静置期晚这么一点:保证它读到的「已静置时长」不小于静置期。
PREFERENCE_DUE_SLACK_SECONDS = 0.2
# 偏好换版失败后,把意向往后推这么久再试。
PREFERENCE_RETRY_SECONDS = 600
# 补扫「偏好变了却没记上意向」的间隔(写入点记意向失败、管理员侧改动了读者的范围等)。
PREFERENCE_SWEEP_SECONDS = 600
# 生成失败后的重试间隔(距上次失败);用尽即当天不再试。
FAILURE_RETRY_DELAYS = (120, 600, 1800)

_IN_FLIGHT = (
    PersonalDigestStatus.PENDING.value,
    PersonalDigestStatus.GENERATING.value,
)
_TERMINAL = (
    PersonalDigestStatus.READY.value,
    PersonalDigestStatus.DEGRADED.value,
    PersonalDigestStatus.FAILED.value,
)
_PUBLISHED = (
    PersonalDigestStatus.READY.value,
    PersonalDigestStatus.DEGRADED.value,
)


def _now() -> dt.datetime:
    return dt.datetime.now(SHANGHAI)


def _parse(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _setting(session: Session, key: str) -> str:
    row = session.get(AppSettingRecord, key)
    return str(row.value or "") if row is not None else ""


def _write_setting(session: Session, key: str, value: str) -> None:
    row = session.get(AppSettingRecord, key)
    if row is None:
        row = AppSettingRecord(key=key, value=value)
    else:
        row.value = value
    session.add(row)
    session.commit()


def _enabled(session: Session) -> bool:
    value = _setting(session, digest.PERSONAL_DIGEST_ENABLED_KEY).strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _active(session: Session, username: str) -> bool:
    user = session.get(UserRecord, username)
    return bool(user is not None and user.is_active)


# --------------------------------------------------------------------------- 落地信号


def _checkpoint_changed(checkpoint: object) -> bool:
    if not isinstance(checkpoint, dict):
        return False
    total = 0
    for key in ("inserted", "updated", "deleted"):
        try:
            total += int(checkpoint.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total > 0


def sync_landing_at(session: Session) -> dt.datetime | None:
    """同步的分析 / 事件流最近一次**带来了变化**的本机完成时刻。

    用本机时钟——同步进来的行带的是上游时间,不能拿来和本机的编排时刻比。优先读同步落库时
    记下的单调时刻(与 checkpoint 同一事务,后续无变化的轮次盖不掉它);升级前写下的进度里没有
    这一项,退回按当前 checkpoint 推算。
    """

    raw = _setting(session, REMOTE_SYNC_STATE_KEY)
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    targets = state.get("targets") if isinstance(state, dict) else None
    if not isinstance(targets, dict):
        return None
    candidates: list[dt.datetime] = []
    for target in targets.values():
        if not isinstance(target, dict):
            continue
        recorded = _parse(target.get(SYNC_LANDED_AT_KEY))
        if recorded is not None:
            candidates.append(recorded)
        streams = target.get("v2_streams")
        if not isinstance(streams, dict):
            continue
        for stream in SYNC_LANDING_STREAMS:
            checkpoint = streams.get(stream)
            if not _checkpoint_changed(checkpoint):
                continue
            completed = _parse(checkpoint.get("completed_at"))
            if completed is not None:
                candidates.append(completed)
    return max(candidates, default=None)


def local_landing_at(session: Session) -> dt.datetime | None:
    return _parse(_setting(session, LOCAL_ANALYSIS_MARK_KEY))


def note_local_analysis(
    engine: Engine,
    article_ids: Iterable[str],
    *,
    now: dt.datetime | None = None,
) -> bool:
    """本机分析跑完一批后记落地时刻;只有公共源的文章才算。

    读者自定义源每小时刷新一次,若也算落地,全员的早报会跟着某个人的自定义源一起重算。
    自定义源的新内容不单独触发补编(记 backlog),下一次落地或次日编排时带上。
    """

    ids = sorted({str(value) for value in article_ids if value})
    if not ids:
        return False
    with Session(engine) as session:
        rows = session.exec(
            select(ArticleRecord.source_id, SourceConfigRecord.owner_username)
            .join(
                SourceConfigRecord,
                SourceConfigRecord.source_id == ArticleRecord.source_id,
                isouter=True,
            )
            .where(ArticleRecord.id.in_(ids))
        ).all()
        public = any(
            not str(source_id or "").startswith(digest.PRIVATE_SOURCE_PREFIX)
            and not (owner or "").strip()
            for source_id, owner in rows
        )
        if not public:
            return False
        _write_setting(
            session,
            LOCAL_ANALYSIS_MARK_KEY,
            (now or _now()).astimezone(SHANGHAI).isoformat(),
        )
    return True


# --------------------------------------------------------------------------- 试算与发布


def _layout(items: Sequence[PersonalDigestItemRecord]) -> list[tuple[str, str]]:
    ordered = sorted(items, key=lambda item: item.position)
    return [(str(item.article_id), str(item.section)) for item in ordered]


def _stored_layout(session: Session, edition_id: int) -> list[tuple[str, str]]:
    rows = session.exec(
        select(PersonalDigestItemRecord)
        .where(PersonalDigestItemRecord.edition_id == edition_id)
        .order_by(PersonalDigestItemRecord.position)
    ).all()
    return _layout(rows)


def _readiness(
    session: Session,
    username: str,
    report_date: str,
    scope: digest.FrozenDigestScope,
    current: dt.datetime,
) -> tuple[bool, bool]:
    """按这次要冻结的范围算两个就绪事实,与定时 / 打开生成的口径同源。"""

    from api.routers import personal_briefs as briefs

    stamp = current.isoformat()
    probe = PersonalDigestEditionRecord(
        owner_username=username,
        report_date=report_date,
        expected_source_ids_json=json.dumps(list(scope.expected_source_ids)),
        due_source_ids_json=json.dumps(list(scope.due_source_ids)),
        created_at=stamp,
        updated_at=stamp,
    )
    return (
        briefs._sources_ready(session, probe, current),  # noqa: SLF001 - shared readiness
        briefs._analysis_ready(session, probe, current),  # noqa: SLF001 - shared readiness
    )


def is_empty_preferences_edition(
    session: Session, edition: PersonalDigestEditionRecord
) -> bool:
    """这一版是不是「订阅与兴趣都空」时落成的空版:冻结的范围空、兴趣快照空,而且确实零条目。

    读者端据此仍显示空订阅的引导(而不是一版零条目的早报);机器人接口按契约投影为 ``empty``。
    三个条件缺一不可——只看范围与兴趣会把实际有条目的版本藏起来。
    """

    if edition.id is None or edition.status not in _PUBLISHED:
        return False
    try:
        scope = json.loads(edition.expected_source_ids_json or "[]")
        interests = json.loads(edition.interest_snapshot_json or "[]")
    except (TypeError, ValueError):
        return False
    if scope or interests:
        return False
    return session.exec(
        select(PersonalDigestItemRecord.id)
        .where(PersonalDigestItemRecord.edition_id == edition.id)
        .limit(1)
    ).first() is None


def publish_refresh(
    session: Session,
    username: str,
    *,
    reason: DigestGenerationReason | str,
    expected_current_id: int | None,
    clock: Callable[[], dt.datetime] = _now,
    unchanged_from: PersonalDigestEditionRecord | None = None,
    cutoff: dt.time | None = None,
) -> str:
    """试算 → 备标题 → 写锁内复核并一次性落成终态。

    返回 ``published`` / ``empty``(订阅与兴趣都空,落成一版空版)/ ``unchanged``(``unchanged_from``
    给定且条目——文章与分区顺序——与它完全相同,什么都不写)/ ``superseded``(拿到写锁时今天的当前
    版本已不是 ``expected_current_id``:别的触发先换了版,这次放弃)/ ``skipped``(跨了零点或过了
    ``cutoff``)。

    试算只用于比对与备标题;发布用的范围、就绪事实与选篇一律在写锁内按当下重算——试算到拿锁
    之间(备标题可能调模型,几秒到几十秒)管理员下架了来源、读者又改了偏好,都不会被旧输入盖回去。
    """

    reason_value = DigestGenerationReason(str(getattr(reason, "value", reason))).value
    current = clock().astimezone(SHANGHAI)
    report_date = current.date().isoformat()
    scope = digest.freeze_personal_digest_scope(session, username, as_of=current)
    sources_ready, analysis_ready = _readiness(session, username, report_date, scope, current)
    planned = digest.generate_personal_digest(
        session,
        username,
        report_date=report_date,
        now=current,
        generation_reason=reason_value,
        frozen_scope=scope,
        sync_stale=not sources_ready,
        analysis_incomplete=not analysis_ready,
        dry_run=True,
    )
    if planned.status != "empty_subscriptions":
        plan_items = list(planned.items)
        if (
            unchanged_from is not None
            and unchanged_from.id is not None
            and _layout(plan_items) == _stored_layout(session, unchanged_from.id)
        ):
            session.rollback()
            return "unchanged"
        # 标题译文先备进文章自身的缓存:发布事务里只读缓存补标题,不调模型。失败只是回退原标题。
        try:
            digest_titles.localize_edition_titles(
                session,
                unchanged_from or PersonalDigestEditionRecord(
                    owner_username=username, report_date=report_date,
                    created_at=current.isoformat(), updated_at=current.isoformat(),
                ),
                items=plan_items,
                persist=False,
            )
        except Exception:  # noqa: BLE001 - display-only enrichment
            logger.warning("个人早报换版前备标题失败 username=%s", username, exc_info=True)

    # ---- 写锁内:核对前提,按当下的输入发布 ----
    digest.begin_immediate(session)
    published_at = clock().astimezone(SHANGHAI)
    if published_at.date().isoformat() != report_date or (
        cutoff is not None and published_at.time() >= cutoff
    ):
        session.rollback()
        return "skipped"
    latest = digest._latest_edition(session, username, report_date)  # noqa: SLF001
    if (latest.id if latest is not None else None) != expected_current_id:
        session.rollback()
        return "superseded"
    scope = digest.freeze_personal_digest_scope(session, username, as_of=published_at)
    sources_ready, analysis_ready = _readiness(session, username, report_date, scope, published_at)
    result = digest.generate_personal_digest(
        session,
        username,
        report_date=report_date,
        now=published_at,
        generation_reason=reason_value,
        frozen_scope=scope,
        sync_stale=not sources_ready,
        analysis_incomplete=not analysis_ready,
        localize_before_publish=True,
        allow_empty=True,
    )
    edition = result.edition
    if edition is None or edition.id is None or edition.status not in _PUBLISHED:
        raise RuntimeError("个人早报换版没有落成终态")
    return "empty" if is_empty_preferences_edition(session, edition) else "published"


# --------------------------------------------------------------------------- 1. 新内容落地


def _load_state(session: Session) -> dict[str, Any]:
    raw = _setting(session, CONTENT_REFRESH_STATE_KEY)
    try:
        state = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def _active_usernames(session: Session) -> set[str]:
    return set(
        session.exec(select(UserRecord.username).where(UserRecord.is_active.is_(True))).all()
    )


def _current_editions(session: Session, report_date: str) -> list[PersonalDigestEditionRecord]:
    """每个账号今天「当前」的那一版(未被换下的最大 revision)。"""

    rows = session.exec(
        select(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.report_date == report_date,
            PersonalDigestEditionRecord.status != PersonalDigestStatus.SUPERSEDED.value,
        )
        .order_by(
            PersonalDigestEditionRecord.owner_username,
            PersonalDigestEditionRecord.revision.desc(),
        )
    ).all()
    current: dict[str, PersonalDigestEditionRecord] = {}
    for row in rows:
        current.setdefault(row.owner_username, row)
    return list(current.values())


def _awaiting_content(edition: PersonalDigestEditionRecord, landing: dt.datetime) -> bool:
    if edition.status not in _PUBLISHED:
        return False
    if not (edition.sync_stale or edition.analysis_incomplete):
        return False  # 内容齐备时编的版,当天不再因新内容而动
    if edition.desired_requested_at:
        return False  # 挂着偏好改动的意向:那条路会按最新内容一并重编
    generated = _parse(edition.generated_at)
    return generated is not None and generated < landing


def refresh_for_new_content(
    engine: Engine,
    *,
    now: dt.datetime | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> dict[str, int]:
    """新内容落地后的补编巡检;返回本轮各结果的计数(没有动作时为空)。"""

    current = (now or _now()).astimezone(SHANGHAI)
    publish_clock = clock or ((lambda: current) if now is not None else _now)
    if current.time() >= CONTENT_CUTOFF:
        return {}
    report_date = current.date().isoformat()
    with Session(engine) as session:
        if not _enabled(session):
            return {}
        landing = max(
            (value for value in (sync_landing_at(session), local_landing_at(session)) if value),
            default=None,
        )
        state = _load_state(session)
        mark = _parse(state.get("mark"))
        if landing is None or (mark is not None and landing <= mark):
            return {}
        if (current - landing).total_seconds() < CONTENT_QUIET_SECONDS:
            waiting_since = _parse(state.get("waiting_since"))
            if (
                waiting_since is None
                or waiting_since > current
                or waiting_since.date() != current.date()  # 昨天没等到处理就过了截止,作废
            ):
                # 这一批内容第一次被看到:记下开始等的时刻
                _write_setting(
                    session,
                    CONTENT_REFRESH_STATE_KEY,
                    json.dumps({**state, "waiting_since": current.isoformat()}, ensure_ascii=False),
                )
                return {}
            if (current - waiting_since).total_seconds() < CONTENT_MAX_WAIT_SECONDS:
                return {}
        active = _active_usernames(session)
        targets = [
            (edition.owner_username, edition.id)
            for edition in _current_editions(session, report_date)
            if edition.id is not None
            and edition.owner_username in active
            and _awaiting_content(edition, landing)
        ]

    counts = {"published": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    past_cutoff = False
    for username, edition_id in targets:
        if publish_clock().astimezone(SHANGHAI).time() >= CONTENT_CUTOFF:
            # 全员串行处理会跨过截止:到点就停,剩下的今天不再因新内容换版
            past_cutoff = True
            counts["skipped"] += 1
            continue
        try:
            with Session(engine) as session:
                edition = digest._latest_edition(session, username, report_date)  # noqa: SLF001
                if (
                    edition is None
                    or edition.id != edition_id
                    or not _awaiting_content(edition, landing)
                ):
                    counts["skipped"] += 1  # 这期间读者自己或别的触发已经换过版
                    continue
                outcome = publish_refresh(
                    session,
                    username,
                    reason=DigestGenerationReason.CONTENT_ARRIVED,
                    expected_current_id=edition_id,
                    clock=publish_clock,
                    unchanged_from=edition,
                    cutoff=CONTENT_CUTOFF,
                )
            if outcome in {"published", "empty"}:
                counts["published"] += 1
            elif outcome == "unchanged":
                counts["unchanged"] += 1
            else:
                counts["skipped"] += 1
        except Exception:  # noqa: BLE001 - 一个账号失败不拦其余账号
            counts["failed"] += 1
            logger.warning("个人早报新内容补编失败 username=%s", username, exc_info=True)

    landing_iso = landing.isoformat()
    tries = int(state.get("tries") or 0) if state.get("retry_for") == landing_iso else 0
    with Session(engine) as session:
        if counts["failed"] and not past_cutoff and tries + 1 < CONTENT_PASS_MAX_TRIES:
            # 水位不动:下一轮重来。已换版的账号因生成时刻晚于落地时刻自然跳过。
            next_state = {
                key: value for key, value in state.items() if key != "waiting_since"
            } | {"retry_for": landing_iso, "tries": tries + 1}
        else:
            next_state = {"mark": landing_iso}
        _write_setting(
            session, CONTENT_REFRESH_STATE_KEY, json.dumps(next_state, ensure_ascii=False)
        )
    if targets:
        logger.info("个人早报新内容补编 landing=%s %s", landing_iso, counts)
    return counts


# --------------------------------------------------------------------------- 2. 偏好改动


def _clear_intent(session: Session, edition_id: int, marker: str) -> None:
    session.exec(
        update(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.id == edition_id,
            PersonalDigestEditionRecord.desired_requested_at == marker,
        )
        .values(
            desired_generation_reason=None,
            desired_requested_at=None,
            desired_first_open_at=None,
        )
    )
    session.commit()


def _stale_reason(session: Session, edition: PersonalDigestEditionRecord) -> str | None:
    """这一版相对读者当前的偏好落后在哪;没落后返回 ``None``。"""

    freshness = digest.edition_freshness(session, edition)
    if freshness["scope_stale"]:
        return DigestGenerationReason.SUBSCRIPTION_CHANGED.value
    if freshness["interest_stale"]:
        return DigestGenerationReason.INTEREST_CHANGED.value
    return None


def is_preference_intent(edition: PersonalDigestEditionRecord, report_date: str) -> bool:
    """今天、已不在途的版本上挂着的兴趣 / 订阅意向归这里处理(不走带租约的意向落成)。"""

    return bool(
        edition.desired_requested_at
        and edition.desired_generation_reason in digest.PREFERENCE_REASON_VALUES
        and edition.report_date == report_date
        and edition.status not in _IN_FLIGHT
    )


def _apply_one_preference_intent(
    engine: Engine,
    edition_id: int,
    current: dt.datetime,
    publish_clock: Callable[[], dt.datetime],
) -> str:
    report_date = current.date().isoformat()
    with Session(engine) as session:
        carrier = session.get(PersonalDigestEditionRecord, edition_id)
        if carrier is None or not is_preference_intent(carrier, report_date):
            return "skipped"
        marker = str(carrier.desired_requested_at)
        reason = str(carrier.desired_generation_reason)
        username = carrier.owner_username
        if not _active(session, username):
            _clear_intent(session, edition_id, marker)  # 账号已停用:不再替它换版
            return "cleared"
        latest = digest._latest_edition(session, username, report_date)  # noqa: SLF001
        if latest is None or latest.id is None:
            _clear_intent(session, edition_id, marker)
            return "cleared"
        if latest.id != edition_id:
            # 意向留在了已被换下的版本上。
            if latest.status in _IN_FLIGHT:
                # 换下它的那一版还在编(它冻结的可能是旧偏好):意向原样留着,等它落成后再判。
                return "waiting"
            if latest.desired_requested_at or _stale_reason(session, latest) is None:
                # 当前版本自己已挂着意向,或这次改动已经被它带上了:旧意向作废。
                _clear_intent(session, edition_id, marker)
                return "cleared"
            # 当前版本仍落后于读者的偏好(意向是在它冻结之后才记的):把意向挪过去,挪成了才清旧的。
            moved = session.exec(
                update(PersonalDigestEditionRecord)
                .where(
                    PersonalDigestEditionRecord.id == latest.id,
                    PersonalDigestEditionRecord.status.in_(_TERMINAL),
                    PersonalDigestEditionRecord.desired_requested_at.is_(None),
                )
                .values(desired_generation_reason=reason, desired_requested_at=marker)
            )
            session.commit()
            if moved.rowcount != 1:
                return "waiting"  # 当前版本又变了:下一轮按新的状况再判
            _clear_intent(session, edition_id, marker)
            return "moved"
        requested = _parse(marker)
        if requested is not None and (current - requested).total_seconds() < PREFERENCE_QUIET_SECONDS:
            return "waiting"
        if _stale_reason(session, carrier) is None:
            _clear_intent(session, edition_id, marker)  # 改了又改回来
            return "cleared"
        try:
            outcome = publish_refresh(
                session,
                username,
                reason=reason,
                expected_current_id=edition_id,
                clock=publish_clock,
            )
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("个人早报偏好换版失败 username=%s", username, exc_info=True)
            # 把意向往后推一段再试,不每 30 秒撞一次;期间读者再改偏好不会把它拉回来,
            # 到点后按当时最新的偏好编。
            retry_at = (current + dt.timedelta(seconds=PREFERENCE_RETRY_SECONDS)).isoformat()
            session.exec(
                update(PersonalDigestEditionRecord)
                .where(
                    PersonalDigestEditionRecord.id == edition_id,
                    PersonalDigestEditionRecord.desired_requested_at == marker,
                )
                .values(desired_requested_at=retry_at)
            )
            session.commit()
            return "failed"
        if outcome in {"superseded", "skipped"}:
            # 拿到写锁时当前版本已换(或跨了零点):意向留着,下一轮走「留在被换下的版本上」那条路。
            return "waiting"
        _clear_intent(session, edition_id, marker)
        return outcome


# 到点任务与 30 秒巡检可能同时来:串行跑。并发本身是安全的(发布在写锁内复核当前版本,后到的一方落空),
# 串行只是不把试算和备标题白做一遍;后进来的一方等前一方跑完再按最新状况判。
_PREFERENCE_PASS_LOCK = threading.Lock()

# 宿主(进程里的调度器)登记的回调:(账号, 到点时刻) → 到点跑一次 apply_preference_intents。
_preference_due_hook: Callable[[str, dt.datetime], None] | None = None


def set_preference_due_hook(hook: Callable[[str, dt.datetime], None] | None) -> None:
    global _preference_due_hook
    _preference_due_hook = hook


def preference_due_at(edition: PersonalDigestEditionRecord) -> dt.datetime | None:
    """这条兴趣 / 订阅意向几时静置期满(意向时刻 + 静置期)。"""

    requested = _parse(str(edition.desired_requested_at)) if edition.desired_requested_at else None
    if requested is None:
        return None
    return requested + dt.timedelta(seconds=PREFERENCE_QUIET_SECONDS + PREFERENCE_DUE_SLACK_SECONDS)


def announce_preference_intent(
    edition: PersonalDigestEditionRecord | None,
    *,
    now: dt.datetime | None = None,
) -> dt.datetime | None:
    """记下意向之后调用:告诉宿主这条意向几时到点,到点就编,不等巡检的下一拍。

    意向挂在在途版本上的不叫(那一版落成后由巡检判);没有宿主(测试、调度器没起)也不叫——
    30 秒巡检始终是兜底,这里只是让正常情况快一点。回调出错不影响调用方。
    """

    hook = _preference_due_hook
    if edition is None or hook is None:
        return None
    report_date = (now or _now()).astimezone(SHANGHAI).date().isoformat()
    if not is_preference_intent(edition, report_date):
        return None
    due = preference_due_at(edition)
    if due is None:
        return None
    try:
        hook(edition.owner_username, due)
    except Exception:  # noqa: BLE001
        logger.warning("个人早报偏好到点任务登记失败 username=%s", edition.owner_username, exc_info=True)
    return due


def apply_preference_intents(
    engine: Engine,
    *,
    now: dt.datetime | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> dict[str, int]:
    """把静置够久的兴趣 / 订阅意向落成新版。"""

    with _PREFERENCE_PASS_LOCK:
        return _apply_preference_intents(engine, now=now, clock=clock)


def _apply_preference_intents(
    engine: Engine,
    *,
    now: dt.datetime | None,
    clock: Callable[[], dt.datetime] | None,
) -> dict[str, int]:
    current = (now or _now()).astimezone(SHANGHAI)
    publish_clock = clock or ((lambda: current) if now is not None else _now)
    report_date = current.date().isoformat()
    with Session(engine) as session:
        if not _enabled(session):
            return {}
        carriers = [
            row.id
            for row in session.exec(
                select(PersonalDigestEditionRecord).where(
                    PersonalDigestEditionRecord.report_date == report_date,
                    PersonalDigestEditionRecord.desired_requested_at.is_not(None),
                    PersonalDigestEditionRecord.desired_generation_reason.in_(
                        sorted(digest.PREFERENCE_REASON_VALUES)
                    ),
                    PersonalDigestEditionRecord.status.notin_(list(_IN_FLIGHT)),
                )
            ).all()
            if row.id is not None
        ]
    counts: dict[str, int] = {}
    for edition_id in carriers:
        try:
            outcome = _apply_one_preference_intent(engine, edition_id, current, publish_clock)
        except Exception:  # noqa: BLE001
            outcome = "failed"
            logger.warning("个人早报偏好意向处理失败 edition=%s", edition_id, exc_info=True)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def sweep_stale_preferences(engine: Engine, *, now: dt.datetime | None = None) -> int:
    """补扫:今天的当前版本落后于读者的偏好、却没有挂着意向的,补记一条。返回补记数。

    写入点记意向是「偏好已提交之后」的另一步(订阅那条还是响应之后的后台任务),进程在两步之间
    停了、或记意向那一下没拿到写锁,都会留下一个没人管的落后版本;管理员侧改动读者范围的操作
    (恢复来源可见性、调整合集成员等)也不经过那些写入点。失败的版本同样要扫:重试次数用尽之后
    读者再改偏好,靠的就是这条意向。

    这里扫出来的只是候选;真正补记由 ``request_preference_rebuild`` 在写锁内重新确认——仍是当前
    版本、确实落后、没有已有意向(失败后退避到未来的那种尤其不能被提前)。
    """

    current = (now or _now()).astimezone(SHANGHAI)
    report_date = current.date().isoformat()
    candidates: list[tuple[str, str]] = []
    with Session(engine) as session:
        if not _enabled(session):
            return 0
        active = _active_usernames(session)
        for edition in _current_editions(session, report_date):
            if (
                edition.status not in _TERMINAL
                or edition.desired_requested_at
                or edition.owner_username not in active
            ):
                continue
            reason = _stale_reason(session, edition)
            if reason is not None:
                candidates.append((edition.owner_username, reason))
    recorded = 0
    for username, reason in candidates:
        try:
            with Session(engine) as session:
                if not _active(session, username):
                    continue
                edition = digest.request_preference_rebuild(
                    session, username, reason=reason, now=current, only_if_absent=True
                )
                if edition is not None and edition.desired_requested_at == current.isoformat():
                    recorded += 1
        except Exception:  # noqa: BLE001 - 下一次补扫再来
            logger.warning("个人早报偏好补扫失败 username=%s", username, exc_info=True)
    if recorded:
        logger.info("个人早报偏好补扫:补记 %s 条意向", recorded)
    return recorded


# --------------------------------------------------------------------------- 3. 失败重试


def _recovery_attempts(session: Session, username: str, report_date: str) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(PersonalDigestEditionRecord)
            .where(
                PersonalDigestEditionRecord.owner_username == username,
                PersonalDigestEditionRecord.report_date == report_date,
                PersonalDigestEditionRecord.generation_reason
                == DigestGenerationReason.RECOVERY.value,
            )
        ).one()
        or 0
    )


def failure_retry_due_at(
    session: Session, edition: PersonalDigestEditionRecord
) -> dt.datetime | None:
    """这版失败的早报下一次自动重试的时刻;重试机会用尽返回 ``None``。"""

    if edition.status != PersonalDigestStatus.FAILED.value:
        return None
    attempts = _recovery_attempts(session, edition.owner_username, edition.report_date)
    if attempts >= len(FAILURE_RETRY_DELAYS):
        return None
    failed_at = _parse(edition.updated_at) or _parse(edition.created_at)
    if failed_at is None:
        return None
    return failed_at + dt.timedelta(seconds=FAILURE_RETRY_DELAYS[attempts])


def _retry_due(
    session: Session, edition: PersonalDigestEditionRecord | None, current: dt.datetime
) -> bool:
    if edition is None or edition.status != PersonalDigestStatus.FAILED.value:
        return False
    if edition.desired_requested_at:
        return False  # 偏好意向会按最新偏好重编,同样是一次重试
    due_at = failure_retry_due_at(session, edition)
    return due_at is not None and current >= due_at


def retry_failed_editions(
    engine: Engine,
    *,
    now: dt.datetime | None = None,
    run: Callable[[Engine, int], None] | None = None,
) -> int:
    """今天当前版本是失败的账号:到点重试一次。返回本轮发起的重试数。"""

    from services.briefs_runtime import run_attempt

    runner = run or (lambda eng, edition_id: run_attempt(eng, edition_id, recovery=False))
    current = (now or _now()).astimezone(SHANGHAI)
    report_date = current.date().isoformat()
    with Session(engine) as session:
        if not _enabled(session):
            return 0
        active = _active_usernames(session)
        candidates = [
            (edition.owner_username, edition.id)
            for edition in _current_editions(session, report_date)
            if edition.id is not None
            and edition.owner_username in active
            and _retry_due(session, edition, current)
        ]
    started: list[int] = []
    for username, edition_id in candidates:
        try:
            with Session(engine) as session:
                # 写锁内复核:当前版本仍是那条失败版、没有意向、已到期、次数未用尽,才建重试版。
                # 否则读到失败版之后别的请求刚发布了一版好的,这里会在它上面再压一条在途版本。
                digest.begin_immediate(session)
                latest = digest._latest_edition(session, username, report_date)  # noqa: SLF001
                if (
                    latest is None
                    or latest.id != edition_id
                    or not _active(session, username)
                    or not _retry_due(session, latest, current)
                ):
                    session.rollback()
                    continue
                result = digest.start_personal_digest_edition(
                    session,
                    username,
                    report_date=report_date,
                    now=current,
                    generation_reason=DigestGenerationReason.RECOVERY,
                )
                if result.edition is not None and result.edition.id is not None:
                    started.append(result.edition.id)
        except Exception:  # noqa: BLE001 - 下一轮再试
            logger.warning("个人早报失败重试未能发起 username=%s", username, exc_info=True)
    for edition_id in started:
        try:
            runner(engine, edition_id)
        except Exception:  # noqa: BLE001 - 巡检的租约恢复是兜底
            logger.warning("个人早报失败重试未完成 edition=%s", edition_id, exc_info=True)
    return len(started)


# --------------------------------------------------------------------------- 读者端状态


def refresh_pending(
    session: Session,
    edition: PersonalDigestEditionRecord,
    *,
    now: dt.datetime | None = None,
    stale: bool = False,
) -> str | None:
    """今天这一版接下来还会不会被系统换掉,页面据此决定要不要再来取。

    ``soon``:挂着偏好意向、落后于读者当前的偏好(``stale``,意向稍后由写入点或补扫记上)、在途、
    或失败后还有重试机会——几十秒到几分钟内会有新版;``content``:带「未就绪」标记且未过上午
    截止——等新内容落地;``None``:不会再动。
    """

    current = (now or _now()).astimezone(SHANGHAI)
    if edition.report_date != current.date().isoformat():
        return None
    if edition.status == PersonalDigestStatus.SUPERSEDED.value:
        return None
    if edition.status in _IN_FLIGHT or edition.desired_requested_at or stale:
        return "soon"
    if edition.status == PersonalDigestStatus.FAILED.value:
        return "soon" if failure_retry_due_at(session, edition) is not None else None
    if (edition.sync_stale or edition.analysis_incomplete) and current.time() < CONTENT_CUTOFF:
        return "content"
    return None


# --------------------------------------------------------------------------- 巡检入口

_last_sweep_at: float | None = None


def _sweep_when_due(engine: Engine, *, now: dt.datetime | None = None) -> None:
    global _last_sweep_at
    elapsed = None if _last_sweep_at is None else time.monotonic() - _last_sweep_at
    if elapsed is not None and elapsed < PREFERENCE_SWEEP_SECONDS:
        return
    _last_sweep_at = time.monotonic()
    sweep_stale_preferences(engine, now=now)


def run_refresh_cycle(engine: Engine, *, now: dt.datetime | None = None) -> None:
    """30 秒任务里的一轮:各步自己兜底,一步出错不拦其余几步。"""

    for name, step in (
        ("偏好补扫", _sweep_when_due),
        ("偏好改动", apply_preference_intents),
        ("失败重试", retry_failed_editions),
        ("新内容补编", refresh_for_new_content),
    ):
        try:
            step(engine, now=now)
        except Exception:  # noqa: BLE001
            logger.warning("个人早报巡检「%s」失败", name, exc_info=True)


__all__ = [
    "CONTENT_CUTOFF",
    "CONTENT_MAX_WAIT_SECONDS",
    "CONTENT_QUIET_SECONDS",
    "CONTENT_REFRESH_STATE_KEY",
    "FAILURE_RETRY_DELAYS",
    "LOCAL_ANALYSIS_MARK_KEY",
    "PREFERENCE_DUE_SLACK_SECONDS",
    "PREFERENCE_QUIET_SECONDS",
    "PREFERENCE_SWEEP_SECONDS",
    "announce_preference_intent",
    "apply_preference_intents",
    "failure_retry_due_at",
    "is_empty_preferences_edition",
    "is_preference_intent",
    "local_landing_at",
    "note_local_analysis",
    "preference_due_at",
    "publish_refresh",
    "refresh_for_new_content",
    "refresh_pending",
    "retry_failed_editions",
    "run_refresh_cycle",
    "set_preference_due_hook",
    "sweep_stale_preferences",
    "sync_landing_at",
]
