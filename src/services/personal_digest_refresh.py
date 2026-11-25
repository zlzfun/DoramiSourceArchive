"""个人早报的系统换版(issue #343):读者面不留重编入口,三种情况由巡检自动处理。

1. **新内容落地**:同步的分析流在本机落地,或本机分析跑完一批——今天还带「未就绪」标记的
   版按当时的内容重新选篇,条目有变才换版(原因 ``content_arrived``)。
2. **读者改了兴趣 / 订阅**:写入点在今日版本上记意向(``request_preference_rebuild``),这里
   静置合并后落成一版。
3. **生成失败**:按递增间隔重试(原因 ``recovery``)。

换版不建「编排中」的中间版本:先试算、把标题译文备好,再在一个事务里落成终态并让旧版让位。
读者端与机器人接口(只取最大 revision)都看不到中间态。失败重试例外——当前版本本来就是失败的,
走既有的带租约生成通道。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
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

# 内容落地后先等一等再补编:同步的几条流、相邻的采集任务会连着来。
CONTENT_QUIET_SECONDS = 180
# 内容一直零星地来、总也静不下来时,最多等这么久就补编(否则一上午都轮不到)。
CONTENT_MAX_WAIT_SECONDS = 900
# 早报是上午的东西:过了这个时刻不再因新内容换版(偏好改动与失败重试不受限)。
CONTENT_CUTOFF = dt.time(12, 0)
# 一批内容里有账号补编失败时,整批最多再试几轮;之后放弃这一批,等下一批内容。
CONTENT_PASS_MAX_TRIES = 3
# 读者连点几个标签、连订几个源:停手这么久才落成一版。
PREFERENCE_QUIET_SECONDS = 30
# 偏好换版失败后,把意向往后推这么久再试。
PREFERENCE_RETRY_SECONDS = 600
# 生成失败后的重试间隔(距上次失败);用尽即当天不再试。
FAILURE_RETRY_DELAYS = (120, 600, 1800)

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


def _begin_write(session: Session) -> None:
    """先拿写锁再读(SQLite):读完再升级成写,会撞上其间别的写者提交(同步、分析都在写)。

    与 ``ensure_first_personal_digest_edition`` 同一手法;持锁期间只有选篇与落库,没有模型调用。
    """

    session.rollback()
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _enabled(session: Session) -> bool:
    value = _setting(session, digest.PERSONAL_DIGEST_ENABLED_KEY).strip().casefold()
    return value in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- 落地信号


def sync_landing_at(session: Session) -> dt.datetime | None:
    """同步的分析流最近一次**带来了变化**的本机完成时刻。

    用本机时钟的 ``completed_at``——同步进来的分析行带的是上游时间,不能拿来和本机的编排时刻比。
    一轮同步里分析流没有任何新增 / 更新 / 删除时不算落地(每轮都会刷新完成时刻)。
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
    latest: dt.datetime | None = None
    for target in targets.values():
        streams = target.get("v2_streams") if isinstance(target, dict) else None
        checkpoint = streams.get("analyses") if isinstance(streams, dict) else None
        if not isinstance(checkpoint, dict):
            continue
        changed = sum(
            int(checkpoint.get(key) or 0) for key in ("inserted", "updated", "deleted")
        )
        if changed <= 0:
            continue
        completed = _parse(checkpoint.get("completed_at"))
        if completed is not None and (latest is None or completed > latest):
            latest = completed
    return latest


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


def _freeze_bot_payload(session: Session, edition_id: int) -> None:
    """把机器人接口的投影固化到版本行(同 revision 输出恒定)。

    输入与带租约通道发布时逐字段相同(``generate_personal_digest`` 的 fenced 分支),只是那边
    用的是尚未落库的条目、这里用补过标题的已落库条目。
    """

    from services.briefs_contract import adapt_edition

    _begin_write(session)
    edition = session.get(PersonalDigestEditionRecord, edition_id)
    if edition is None or edition.status not in _PUBLISHED:
        session.rollback()
        return
    rows = session.exec(
        select(PersonalDigestItemRecord)
        .where(PersonalDigestItemRecord.edition_id == edition_id)
        .order_by(PersonalDigestItemRecord.position)
    ).all()
    try:
        selection_stats = json.loads(edition.selection_stats_json or "{}")
    except (TypeError, ValueError):
        selection_stats = {}
    frozen = adapt_edition({
        "report_date": edition.report_date,
        "revision": edition.revision,
        "status": edition.status,
        "generated_at": edition.generated_at,
        "degraded_reason": edition.degraded_reason,
        "sync_stale": bool(edition.sync_stale),
        "analysis_incomplete": bool(edition.analysis_incomplete),
        "selection_stats": selection_stats,
        "items": [
            {
                "article_id": row.article_id,
                "position": row.position,
                "section": row.section,
                "selection_lane": row.selection_lane,
                "quality_score": row.quality_score_snapshot,
                "selection_reason": row.selection_reason,
                "snapshot": json.loads(row.snapshot_json),
            }
            for row in rows
        ],
    })
    session.exec(
        update(PersonalDigestEditionRecord)
        .where(
            PersonalDigestEditionRecord.id == edition_id,
            PersonalDigestEditionRecord.status.in_(_PUBLISHED),
            PersonalDigestEditionRecord.briefs_payload_json.is_(None),
        )
        .values(briefs_payload_json=json.dumps(frozen, ensure_ascii=False))
    )
    session.commit()


def publish_refresh(
    session: Session,
    username: str,
    *,
    reason: DigestGenerationReason | str,
    clock: Callable[[], dt.datetime] = _now,
    unchanged_from: PersonalDigestEditionRecord | None = None,
) -> str:
    """试算 → 备标题 → 一次性落成终态。返回 ``published`` / ``unchanged`` / ``empty``。

    ``unchanged_from`` 给定时,条目(文章与分区顺序)与它完全相同就什么都不写。
    """

    reason_value = DigestGenerationReason(str(getattr(reason, "value", reason))).value
    current = clock().astimezone(SHANGHAI)
    report_date = current.date().isoformat()
    scope = digest.freeze_personal_digest_scope(session, username, as_of=current)
    sources_ready, analysis_ready = _readiness(session, username, report_date, scope, current)
    common: dict[str, Any] = {
        "report_date": report_date,
        "generation_reason": reason_value,
        "frozen_scope": scope,
        "sync_stale": not sources_ready,
        "analysis_incomplete": not analysis_ready,
    }
    planned = digest.generate_personal_digest(
        session, username, now=current, dry_run=True, **common
    )
    if planned.status == "empty_subscriptions":
        # 订阅与兴趣都空了:沿用既有重编口径,当日版本清成空订阅状态。
        digest.start_personal_digest_edition(
            session, username, report_date=report_date, now=current,
            generation_reason=reason_value,
        )
        return "empty"
    plan_items = list(planned.items)
    if (
        unchanged_from is not None
        and unchanged_from.id is not None
        and _layout(plan_items) == _stored_layout(session, unchanged_from.id)
    ):
        return "unchanged"
    # 标题译文先备好(翻译结果写回文章自身的缓存):发布后那次补标题全部命中缓存,
    # 读者不会先看到一版没译的标题。失败只是回退原标题,不拦发布。
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
        session.rollback()
        logger.warning("个人早报换版前备标题失败 username=%s", username, exc_info=True)
    session.rollback()

    published_at = clock().astimezone(SHANGHAI)
    if published_at.date().isoformat() != report_date:
        return "unchanged"  # 跨了零点:今天这版不再动,新的一天自有首版
    _begin_write(session)
    result = digest.generate_personal_digest(session, username, now=published_at, **common)
    edition = result.edition
    if edition is None or edition.id is None or edition.status not in _PUBLISHED:
        raise RuntimeError("个人早报换版没有落成终态")
    edition_id = edition.id
    try:
        _begin_write(session)
        digest_titles.localize_edition_titles(
            session, session.get(PersonalDigestEditionRecord, edition_id) or edition
        )
    except Exception:  # noqa: BLE001 - display-only enrichment
        session.rollback()
        logger.warning("个人早报换版后补标题失败 edition=%s", edition_id, exc_info=True)
    try:
        _freeze_bot_payload(session, edition_id)
    except Exception:  # noqa: BLE001 - 没固化时机器人接口按实时投影读,同样可用
        session.rollback()
        logger.warning("个人早报换版后固化投影失败 edition=%s", edition_id, exc_info=True)
    return "published"


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
    for username, edition_id in targets:
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
                    clock=publish_clock,
                    unchanged_from=edition,
                )
            counts["published" if outcome == "published" else "unchanged"] += 1
        except Exception:  # noqa: BLE001 - 一个账号失败不拦其余账号
            counts["failed"] += 1
            logger.warning("个人早报新内容补编失败 username=%s", username, exc_info=True)

    landing_iso = landing.isoformat()
    tries = int(state.get("tries") or 0) if state.get("retry_for") == landing_iso else 0
    with Session(engine) as session:
        if counts["failed"] and tries + 1 < CONTENT_PASS_MAX_TRIES:
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


def _is_stale(session: Session, edition: PersonalDigestEditionRecord) -> bool:
    freshness = digest.edition_freshness(session, edition)
    return bool(freshness["interest_stale"] or freshness["scope_stale"])


def is_preference_intent(edition: PersonalDigestEditionRecord, report_date: str) -> bool:
    """今天、终态版本上挂着的兴趣 / 订阅意向归这里处理(不走带租约的意向落成)。"""

    return bool(
        edition.desired_requested_at
        and edition.desired_generation_reason in digest.PREFERENCE_REASON_VALUES
        and edition.report_date == report_date
        and edition.status
        not in {PersonalDigestStatus.PENDING.value, PersonalDigestStatus.GENERATING.value}
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
        latest = digest._latest_edition(session, username, report_date)  # noqa: SLF001
        if latest is None or latest.id is None:
            _clear_intent(session, edition_id, marker)
            return "cleared"
        if latest.id != edition_id:
            # 意向留在了已被换下的版本上。换下它的那一版若仍落后于当前偏好(意向是在它
            # 冻结之后才记的),把意向挪过去;否则这次改动已经被它带上了。
            if (
                latest.status in _TERMINAL
                and not latest.desired_requested_at
                and _is_stale(session, latest)
            ):
                session.exec(
                    update(PersonalDigestEditionRecord)
                    .where(
                        PersonalDigestEditionRecord.id == latest.id,
                        PersonalDigestEditionRecord.status.in_(_TERMINAL),
                        PersonalDigestEditionRecord.desired_requested_at.is_(None),
                    )
                    .values(desired_generation_reason=reason, desired_requested_at=marker)
                )
                session.commit()
            _clear_intent(session, edition_id, marker)
            return "moved"
        requested = _parse(marker)
        if requested is not None and (current - requested).total_seconds() < PREFERENCE_QUIET_SECONDS:
            return "waiting"
        if not _is_stale(session, carrier):
            _clear_intent(session, edition_id, marker)  # 改了又改回来
            return "cleared"
        try:
            outcome = publish_refresh(session, username, reason=reason, clock=publish_clock)
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
        _clear_intent(session, edition_id, marker)
        return outcome


def apply_preference_intents(
    engine: Engine,
    *,
    now: dt.datetime | None = None,
    clock: Callable[[], dt.datetime] | None = None,
) -> dict[str, int]:
    """把静置够久的兴趣 / 订阅意向落成新版。"""

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
                    PersonalDigestEditionRecord.status.notin_([
                        PersonalDigestStatus.PENDING.value,
                        PersonalDigestStatus.GENERATING.value,
                    ]),
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
    started: list[int] = []
    with Session(engine) as session:
        if not _enabled(session):
            return 0
        active = _active_usernames(session)
        for edition in _current_editions(session, report_date):
            if (
                edition.status != PersonalDigestStatus.FAILED.value
                or edition.owner_username not in active
                or edition.desired_requested_at  # 偏好意向会按最新偏好重编,同样是一次重试
            ):
                continue
            due_at = failure_retry_due_at(session, edition)
            if due_at is None or current < due_at:
                continue
            result = digest.start_personal_digest_edition(
                session,
                edition.owner_username,
                report_date=report_date,
                now=current,
                generation_reason=DigestGenerationReason.RECOVERY,
            )
            if result.edition is not None and result.edition.id is not None:
                started.append(result.edition.id)
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
) -> str | None:
    """今天这一版接下来还会不会被系统换掉,页面据此决定要不要再来取。

    ``soon``:挂着偏好意向、在途、或失败后还有重试机会——几十秒到几分钟内会有新版;
    ``content``:带「未就绪」标记且未过上午截止——等新内容落地;``None``:不会再动。
    """

    current = (now or _now()).astimezone(SHANGHAI)
    if edition.report_date != current.date().isoformat():
        return None
    if edition.status == PersonalDigestStatus.SUPERSEDED.value:
        return None
    if edition.status in {
        PersonalDigestStatus.PENDING.value,
        PersonalDigestStatus.GENERATING.value,
    } or edition.desired_requested_at:
        return "soon"
    if edition.status == PersonalDigestStatus.FAILED.value:
        return "soon" if failure_retry_due_at(session, edition) is not None else None
    if (edition.sync_stale or edition.analysis_incomplete) and current.time() < CONTENT_CUTOFF:
        return "content"
    return None


# --------------------------------------------------------------------------- 巡检入口


def run_refresh_cycle(engine: Engine, *, now: dt.datetime | None = None) -> None:
    """30 秒任务里的一轮:三步各自兜底,一步出错不拦另两步。"""

    for name, step in (
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
    "PREFERENCE_QUIET_SECONDS",
    "apply_preference_intents",
    "failure_retry_due_at",
    "is_preference_intent",
    "local_landing_at",
    "note_local_analysis",
    "publish_refresh",
    "refresh_for_new_content",
    "refresh_pending",
    "retry_failed_editions",
    "run_refresh_cycle",
    "sync_landing_at",
]
