"""Validate publisher enclosure bytes without retaining the source recording."""

from __future__ import annotations

import asyncio
import json
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
from models.db import ArticleRecord, PodcastSourceMediaSnapshotRecord
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
    download_limit = enclosure.declared_bytes or store.max_bytes
    if download_limit > store.max_bytes:
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
                except http_safety.PublicDownloadError as exc:
                    if "大小上限" in str(exc):
                        raise SourceMediaTooLarge(
                            "Podcast enclosure 超过配置的音频大小上限"
                        ) from None
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
) -> dict[str, Any]:
    """Download to unique staging, persist metadata, and always erase bytes."""

    policy = PodcastStagePolicy(podcast_config)
    if podcast_config.installation != "external":
        raise SourceMediaConflict(
            "source media 只能由 external Podcast installation 校验"
        )
    policy.require_stage("fetch", boundary="enqueue")
    with Session(engine) as session:
        _episode, enclosure = _episode_and_enclosure(session, episode_id)
    if enclosure.declared_bytes is not None and enclosure.declared_bytes > store.max_bytes:
        raise SourceMediaTooLarge("Podcast enclosure 超过配置的音频大小上限")

    policy.require_stage("fetch", boundary="provider_submit")
    download_limit = enclosure.declared_bytes or store.max_bytes
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
                except http_safety.PublicDownloadError as exc:
                    if "大小上限" in str(exc):
                        raise SourceMediaTooLarge(
                            "Podcast enclosure 超过配置的音频大小上限"
                        ) from None
                    raise SourceMediaFetchFailed("Podcast enclosure 下载失败") from None
                destination.flush()
                os.fsync(destination.fileno())
            if result.size <= 0:
                raise SourceMediaFetchFailed("Podcast enclosure 返回空内容")

            declared_mime = _download_mime(result.content_type, enclosure.mime)
            with Path(path).open("rb") as source:
                mime = store.validate_audio(source.read(64), declared_mime)
            duration = store.probe_audio(Path(path))
            if duration is None or duration <= 0:
                raise PodcastArtifactUnsupportedMedia("音频时长不可用")
            if (
                isinstance(max_audio_seconds_per_file, bool)
                or not isinstance(max_audio_seconds_per_file, int)
                or max_audio_seconds_per_file <= 0
            ):
                raise SourceMediaConflict("ASR 单集音频时长上限配置无效")
            if float(duration) > max_audio_seconds_per_file:
                raise SourceMediaTooLong(
                    "Podcast enclosure 超过 ASR 单任务音频时长上限"
                )

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
                        payload = serialize_snapshot(existing)
                        session.rollback()
                        return payload
                    session.refresh(existing)
                    payload = serialize_snapshot(existing)
                else:
                    payload = serialize_snapshot(existing)
                    session.rollback()
                return payload
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
