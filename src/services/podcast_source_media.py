"""Validate publisher enclosure bytes without retaining the source recording."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from config import PodcastArtifactStorageConfig, PodcastConfig
from models.db import AppSettingRecord, ArticleRecord, PodcastSourceMediaSnapshotRecord
from services import http_safety
from services.podcast_artifacts import (
    PodcastArtifactError,
    PodcastArtifactProbeUnavailable,
    PodcastArtifactStorageFull,
    PodcastArtifactTooLarge,
    PodcastArtifactUnsupportedMedia,
    PodcastArtifactStore,
)
from services.podcast_stage_policy import PodcastStagePolicy

logger = logging.getLogger("dorami.podcast_source_media")

# 「这段音频过大 / 过长」是关于音频本身的事实:量过一次,同一个地址、上限又没有放宽
# 到能容下它,再下载一遍也是同一个结论——而「过长」要把整个文件下载下来才量得出
# (issue #323:生产上 21 期节目每天被完整下载一遍再拒绝一次)。所以把量到的值记在
# 这里,校验时先比一比。记的是事实,不是「这一期不要再试」的决定:入队那一层照常按
# 自己的节奏复查,官方逐字稿照常重取,只是走到音频校验这一步时不再重复下载。
REJECTION_MEMORY_KEY = "podcast_source_media:rejections"
# 量过的拒绝保留 30 天:到期重新量一次,覆盖「发布方在同一地址下换了文件」。
REJECTION_RETENTION_SECONDS = 30 * 24 * 60 * 60
# 校验通过也留一个时间水位(不带拒绝),防止另一次更早开始、更晚结束的校验把过时的
# 拒绝写回来。它只需要活过一次校验的最长耗时。
VALIDATED_WATERMARK_SECONDS = 24 * 60 * 60
_TOO_LARGE_MESSAGE = "Podcast enclosure 超过配置的音频大小上限"
_TOO_LONG_MESSAGE = "Podcast enclosure 超过 ASR 单任务音频时长上限"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SourceMediaError(ValueError):
    """Safe-to-display enclosure validation failure."""


class SourceMediaNotFound(SourceMediaError):
    pass


class SourceMediaConflict(SourceMediaError):
    pass


class SourceMediaTooLarge(SourceMediaError):
    pass


class SourceMediaTooLong(SourceMediaError):
    pass


class SourceMediaTimeout(SourceMediaError):
    pass


class SourceMediaFetchFailed(SourceMediaError):
    pass


def _valid_seconds_limit(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _evidence_time(entry: object) -> dt.datetime | None:
    raw = entry.get("fetched_at") if isinstance(entry, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.timezone.utc)


def _load_rejection_memory(session: Session, *, for_update: bool = False) -> dict[str, dict]:
    statement = select(AppSettingRecord).where(AppSettingRecord.key == REJECTION_MEMORY_KEY)
    if for_update:
        statement = statement.with_for_update()
    record = session.exec(statement).first()
    try:
        value = json.loads(record.value) if record is not None and record.value else {}
    except (TypeError, ValueError):
        value = {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): entry for key, entry in value.items() if isinstance(entry, dict)
    }


def remembered_rejection(
    engine: Engine,
    *,
    episode_id: str,
    enclosure: EnclosureSnapshot,
    max_bytes: int,
    max_audio_seconds: object,
) -> SourceMediaError | None:
    """The rejection a fresh download of this enclosure would end in, if we measured it before.

    Holds only while the audio address is the same, the measurement is younger
    than the retention window (a hit never renews it) and the current limits
    still exclude the measured value. ``bytes`` is a lower bound of the real
    size — the response's own ``Content-Length`` or "one more than the cap the
    stream outgrew" — so only ``bytes > max_bytes`` rejects.
    """

    with Session(engine) as session:
        entry = _load_rejection_memory(session).get(episode_id)
    if not entry or entry.get("locator_hash") != enclosure.locator_hash:
        return None
    measured_at = _evidence_time(entry)
    if (
        measured_at is None
        or (_utc_now() - measured_at).total_seconds() > REJECTION_RETENTION_SECONDS
    ):
        return None
    kind = entry.get("kind")
    if kind == "too_long":
        seconds = entry.get("seconds")
        if (
            isinstance(seconds, (int, float))
            and not isinstance(seconds, bool)
            and _valid_seconds_limit(max_audio_seconds)
            and float(seconds) > max_audio_seconds
        ):
            return SourceMediaTooLong(_TOO_LONG_MESSAGE)
    elif kind == "too_large":
        size = entry.get("bytes")
        if isinstance(size, int) and not isinstance(size, bool) and size > max_bytes:
            return SourceMediaTooLarge(_TOO_LARGE_MESSAGE)
    return None


def _record_evidence(
    engine: Engine,
    *,
    episode_id: str,
    enclosure: EnclosureSnapshot,
    fetched_at: dt.datetime,
    kind: str = "",
    seconds: float | None = None,
    size: int | None = None,
) -> None:
    """Remember what one download round established; ``kind=""`` means it validated.

    Evidence is ordered by when its download began: a round that started earlier
    never overwrites what a later round established, whichever finishes first.
    Best effort — a failed write only means the next validation downloads again.
    """

    try:
        with Session(engine) as session:
            if engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            state = _load_rejection_memory(
                session, for_update=engine.dialect.name == "postgresql"
            )
            held_at = _evidence_time(state.get(episode_id))
            if held_at is not None and held_at > fetched_at:
                session.rollback()
                return
            entry: dict[str, Any] = {
                "locator_hash": enclosure.locator_hash,
                "kind": kind,
                "fetched_at": fetched_at.isoformat(timespec="microseconds"),
            }
            if seconds is not None:
                entry["seconds"] = float(seconds)
            if size is not None:
                entry["bytes"] = int(size)
            state[episode_id] = entry
            now = _utc_now()
            for key, other in list(state.items()):
                seen_at = _evidence_time(other)
                keep_for = (
                    REJECTION_RETENTION_SECONDS
                    if other.get("kind")
                    else VALIDATED_WATERMARK_SECONDS
                )
                if seen_at is None or (now - seen_at).total_seconds() > keep_for:
                    state.pop(key, None)
            value = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record = session.get(AppSettingRecord, REJECTION_MEMORY_KEY)
            if record is None:
                session.add(AppSettingRecord(key=REJECTION_MEMORY_KEY, value=value))
            else:
                record.value = value
                session.add(record)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - the verdict itself must still surface
        logger.warning(
            "Podcast 源音频校验结论没能记下 episode=%s:%s: %s",
            episode_id, type(exc).__name__, exc,
        )


class _DeclaredSizeExceeded(Exception):
    """The file is larger than the feed said, but may still fit the configured cap.

    Deliberately not a ``SourceMediaError``: it never leaves this module.
    ``actual_bytes`` is the response's ``Content-Length`` when it was known.
    """

    def __init__(self, actual_bytes: int | None) -> None:
        super().__init__("declared enclosure length exceeded")
        self.actual_bytes = actual_bytes


@dataclass(frozen=True)
class EnclosureSnapshot:
    url: str
    mime: str
    declared_bytes: int | None

    @property
    def locator_hash(self) -> str:
        import hashlib

        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidatedSourceMediaFile:
    path: Path
    mime: str
    size_bytes: int
    content_hash: str


@contextmanager
def download_snapshot_media(
    store: PodcastArtifactStore,
    *,
    enclosure: EnclosureSnapshot,
    expected: PodcastSourceMediaSnapshotRecord,
    storage_config: PodcastArtifactStorageConfig,
    client_factory: http_safety.AsyncClientFactory,
) -> Iterator[ValidatedSourceMediaFile]:
    """Redownload one bound enclosure into staging without retaining it.

    This is the same bounded public downloader, staging reservation, magic-byte
    validation and ffprobe gate used by initial source-media validation. The
    immutable snapshot also proves the bytes did not change between admission
    and an OSS fallback.
    """

    if enclosure.locator_hash != expected.locator_hash:
        raise SourceMediaConflict("Podcast enclosure 元数据已变化，请重试")
    # 这份快照是校验过的:字节数必须与它一致,所以按它预留。订阅源声明的 length
    # 不可靠(issue #323),不能再拿来当上限。
    download_limit = int(expected.size_bytes)
    if download_limit <= 0 or download_limit > store.max_bytes:
        raise SourceMediaTooLarge("Podcast enclosure 超过配置的音频大小上限")
    with store.reserve_validation_download(download_limit):
        fd, path = store.create_upload_temp()
        try:
            with os.fdopen(fd, "wb", closefd=False) as destination:
                try:
                    result = asyncio.run(
                        http_safety.stream_public_url_to_file(
                            enclosure.url,
                            destination,
                            max_bytes=download_limit,
                            max_redirects=storage_config.download_max_redirects,
                            timeout_seconds=storage_config.download_timeout_seconds,
                            client_factory=client_factory,
                        )
                    )
                except http_safety.PublicDownloadTimeout:
                    raise SourceMediaTimeout("Podcast enclosure 下载超时") from None
                except http_safety.PublicDownloadTooLarge:
                    # 比校验时的快照还大:内容变了,不是「超过配置上限」。
                    raise SourceMediaConflict(
                        "Podcast enclosure 内容已变化，请重新校验"
                    ) from None
                except http_safety.PublicDownloadError:
                    raise SourceMediaFetchFailed("Podcast enclosure 下载失败") from None
                destination.flush()
                os.fsync(destination.fileno())
            if result.size <= 0:
                raise SourceMediaFetchFailed("Podcast enclosure 返回空内容")
            declared_mime = _download_mime(result.content_type, enclosure.mime)
            with path.open("rb") as source:
                mime = store.validate_audio(source.read(64), declared_mime)
            duration = store.probe_audio(path)
            if duration is None or duration <= 0:
                raise PodcastArtifactUnsupportedMedia("音频时长不可用")
            if (
                result.sha256 != expected.content_hash
                or result.size != expected.size_bytes
                or mime != expected.mime
                or abs(float(duration) - float(expected.duration_seconds)) > 0.001
            ):
                raise SourceMediaConflict("Podcast enclosure 内容已变化，请重新校验")
            yield ValidatedSourceMediaFile(
                path=path,
                mime=mime,
                size_bytes=result.size,
                content_hash=result.sha256,
            )
        except PodcastArtifactTooLarge as exc:
            raise SourceMediaTooLarge(str(exc)) from None
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)


def _extensions(episode: ArticleRecord) -> dict[str, Any]:
    try:
        value = json.loads(episode.extensions_json or "{}")
    except (TypeError, ValueError) as exc:
        raise SourceMediaConflict("Podcast 单集 enclosure 元数据无效") from exc
    if not isinstance(value, dict):
        raise SourceMediaConflict("Podcast 单集 enclosure 元数据无效")
    return value


def enclosure_snapshot(episode: ArticleRecord) -> EnclosureSnapshot:
    if episode.content_type != "podcast_episode":
        raise SourceMediaNotFound("Podcast 单集不存在")
    metadata = _extensions(episode)
    # The exact raw string is the locator identity. In particular, query order
    # and encoding are not normalized because signed publisher URLs can depend
    # on either.
    raw_url = metadata.get("audio_url")
    if not isinstance(raw_url, str) or not raw_url:
        raise SourceMediaNotFound("Podcast 单集没有 enclosure 音频地址")
    url = raw_url
    mime = str(metadata.get("audio_mime") or "").strip()
    raw_bytes = metadata.get("audio_bytes")
    if raw_bytes in (None, ""):
        declared_bytes = None
    else:
        try:
            declared_bytes = int(raw_bytes)
        except (TypeError, ValueError) as exc:
            raise SourceMediaConflict("Podcast enclosure 大小元数据无效") from exc
        if declared_bytes < 0:
            raise SourceMediaConflict("Podcast enclosure 大小元数据无效")
    return EnclosureSnapshot(url=url, mime=mime, declared_bytes=declared_bytes)


def _episode_and_enclosure(
    session: Session, episode_id: str
) -> tuple[ArticleRecord, EnclosureSnapshot]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None:
        raise SourceMediaNotFound("Podcast 单集不存在")
    return episode, enclosure_snapshot(episode)


def _download_mime(result_mime: str, publisher_mime: str) -> str:
    response = str(result_mime or "").split(";", 1)[0].strip().lower()
    publisher = str(publisher_mime or "").split(";", 1)[0].strip().lower()
    if response.startswith("audio/") or response == "application/ogg":
        return response
    return publisher or response


def serialize_snapshot(record: PodcastSourceMediaSnapshotRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "episode_id": record.episode_id,
        "locator_hash": record.locator_hash,
        "content_hash": record.content_hash,
        "mime": record.mime,
        "size_bytes": record.size_bytes,
        "duration_seconds": record.duration_seconds,
        "created_at": record.created_at,
    }


async def validate_source_media(
    engine: Engine,
    store: PodcastArtifactStore,
    *,
    episode_id: str,
    podcast_config: PodcastConfig,
    storage_config: PodcastArtifactStorageConfig,
    max_audio_seconds_per_file: int,
    client_factory: http_safety.AsyncClientFactory,
    use_rejection_memory: bool = True,
) -> dict[str, Any]:
    """Download to unique staging, persist metadata, and always erase bytes.

    ``use_rejection_memory=False`` is for an operator who explicitly asks for a
    fresh validation: the remembered measurement is not consulted, the new
    outcome is still recorded.
    """

    policy = PodcastStagePolicy(podcast_config)
    if podcast_config.installation != "external":
        raise SourceMediaConflict(
            "source media 只能由 external Podcast installation 校验"
        )
    policy.require_stage("fetch", boundary="enqueue")
    with Session(engine) as session:
        _episode, enclosure = _episode_and_enclosure(session, episode_id)
    if enclosure.declared_bytes is not None and enclosure.declared_bytes > store.max_bytes:
        raise SourceMediaTooLarge(_TOO_LARGE_MESSAGE)

    policy.require_stage("fetch", boundary="provider_submit")
    if use_rejection_memory:
        remembered = remembered_rejection(
            engine,
            episode_id=episode_id,
            enclosure=enclosure,
            max_bytes=store.max_bytes,
            max_audio_seconds=max_audio_seconds_per_file,
        )
        if remembered is not None:
            raise remembered

    def remember(kind: str, **measured: Any) -> None:
        _record_evidence(
            engine, episode_id=episode_id, enclosure=enclosure,
            fetched_at=fetched_at, kind=kind, **measured,
        )

    # 订阅源声明的 ``length`` 只是预留的参考,不是上限(issue #323):它经常不准——
    # 实测有的源真实文件是声明值的 1.5 倍——此前流一超过声明值就被判成「超过配置的
    # 音频大小上限」,而真正的上限是 ``store.max_bytes``。先按声明值预留;文件比它
    # 大而仍在上限之内时,换一个够用的预留额重新下载:响应自报了长度就按它,但只
    # 采信一次(发布方可以每次多报一个字节);再不够就直接按配置上限。所以最多三轮:
    # 声明值 → 响应自报的长度 → 配置上限。
    download_limit = enclosure.declared_bytes or store.max_bytes
    trusted_response_length = False
    validated: dict[str, Any]
    while True:
        fetched_at = _utc_now()
        try:
            with store.reserve_validation_download(download_limit):
                fd, path = store.create_upload_temp()
                try:
                    with os.fdopen(fd, "wb", closefd=False) as destination:
                        try:
                            result = await http_safety.stream_public_url_to_file(
                                enclosure.url,
                                destination,
                                max_bytes=download_limit,
                                max_redirects=storage_config.download_max_redirects,
                                timeout_seconds=storage_config.download_timeout_seconds,
                                client_factory=client_factory,
                            )
                        except http_safety.PublicDownloadTimeout:
                            raise SourceMediaTimeout("Podcast enclosure 下载超时") from None
                        except http_safety.PublicDownloadTooLarge as exc:
                            if download_limit >= store.max_bytes:
                                # 已知大小的下界:响应自报的长度,或「流到上限仍没完」。
                                remember(
                                    "too_large",
                                    size=(
                                        exc.declared_length
                                        if exc.declared_length is not None
                                        else store.max_bytes + 1
                                    ),
                                )
                                raise SourceMediaTooLarge(_TOO_LARGE_MESSAGE) from None
                            raise _DeclaredSizeExceeded(exc.declared_length) from None
                        except http_safety.PublicDownloadError:
                            raise SourceMediaFetchFailed("Podcast enclosure 下载失败") from None
                        destination.flush()
                        os.fsync(destination.fileno())
                    if result.size <= 0:
                        raise SourceMediaFetchFailed("Podcast enclosure 返回空内容")

                    declared_mime = _download_mime(result.content_type, enclosure.mime)
                    with Path(path).open("rb") as source:
                        mime = store.validate_audio(source.read(64), declared_mime)
                    # ffprobe is a blocking subprocess: keep it off the event loop.
                    duration = await asyncio.to_thread(store.probe_audio, Path(path))
                    if duration is None or duration <= 0:
                        raise PodcastArtifactUnsupportedMedia("音频时长不可用")
                    if (
                        isinstance(max_audio_seconds_per_file, bool)
                        or not isinstance(max_audio_seconds_per_file, int)
                        or max_audio_seconds_per_file <= 0
                    ):
                        raise SourceMediaConflict("ASR 单集音频时长上限配置无效")
                    if float(duration) > max_audio_seconds_per_file:
                        remember("too_long", seconds=float(duration), size=result.size)
                        raise SourceMediaTooLong(_TOO_LONG_MESSAGE)

                    policy.require_stage("fetch", boundary="commit")
                    stamp = dt.datetime.now(dt.timezone.utc).isoformat(
                        timespec="microseconds"
                    )
                    with Session(engine) as session:
                        if engine.dialect.name == "sqlite":
                            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                        episode, current = _episode_and_enclosure(session, episode_id)
                        if current != enclosure:
                            raise SourceMediaConflict("Podcast enclosure 元数据已变化，请重试")
                        if engine.dialect.name == "postgresql":
                            session.expire_all()
                            locked = session.exec(
                                select(ArticleRecord)
                                .where(ArticleRecord.id == episode_id)
                                .with_for_update()
                            ).first()
                            if locked is None or enclosure_snapshot(locked) != enclosure:
                                raise SourceMediaConflict("Podcast enclosure 元数据已变化，请重试")
                        existing = session.exec(
                            select(PodcastSourceMediaSnapshotRecord).where(
                                PodcastSourceMediaSnapshotRecord.episode_id == episode_id,
                                PodcastSourceMediaSnapshotRecord.locator_hash
                                == enclosure.locator_hash,
                                PodcastSourceMediaSnapshotRecord.content_hash == result.sha256,
                            )
                        ).first()
                        if existing is not None and (
                            existing.mime != mime
                            or existing.size_bytes != result.size
                            or existing.duration_seconds != float(duration)
                        ):
                            raise SourceMediaConflict(
                                "Podcast 来源媒体快照与重复校验结果冲突"
                            )
                        if existing is None:
                            existing = PodcastSourceMediaSnapshotRecord(
                                id=f"podcast-source-media-{uuid.uuid4().hex}",
                                episode_id=episode.id,
                                locator_hash=enclosure.locator_hash,
                                content_hash=result.sha256,
                                mime=mime,
                                size_bytes=result.size,
                                duration_seconds=float(duration),
                                created_at=stamp,
                            )
                            session.add(existing)
                            try:
                                session.commit()
                            except IntegrityError:
                                session.rollback()
                                existing = session.exec(
                                    select(PodcastSourceMediaSnapshotRecord).where(
                                        PodcastSourceMediaSnapshotRecord.episode_id == episode_id,
                                        PodcastSourceMediaSnapshotRecord.locator_hash
                                        == enclosure.locator_hash,
                                        PodcastSourceMediaSnapshotRecord.content_hash == result.sha256,
                                    )
                                ).one()
                                validated = serialize_snapshot(existing)
                                session.rollback()
                                break
                            session.refresh(existing)
                            validated = serialize_snapshot(existing)
                        else:
                            validated = serialize_snapshot(existing)
                            session.rollback()
                        break
                except PodcastArtifactTooLarge as exc:
                    raise SourceMediaTooLarge(str(exc)) from None
                except (
                    PodcastArtifactProbeUnavailable,
                    PodcastArtifactStorageFull,
                    PodcastArtifactUnsupportedMedia,
                ):
                    raise
                except PodcastArtifactError:
                    raise
                finally:
                    os.close(fd)
                    Path(path).unlink(missing_ok=True)
        except _DeclaredSizeExceeded as grown:
            actual = grown.actual_bytes
            if actual is not None and actual > store.max_bytes:
                remember("too_large", size=actual)
                raise SourceMediaTooLarge(_TOO_LARGE_MESSAGE) from None
            if (
                actual is not None
                and actual > download_limit
                and not trusted_response_length
            ):
                trusted_response_length = True
                download_limit = actual
            else:
                download_limit = store.max_bytes
    # 通过了:留下这次取证的时间水位,并顺带清掉这一期此前的拒绝。
    remember("")
    return validated
