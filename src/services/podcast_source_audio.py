"""Controlled publisher-enclosure download into the external temporary CAS."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from config import PodcastArtifactStorageConfig, PodcastConfig
from models.db import ArticleRecord
from services import http_safety
from services.podcast_artifacts import (
    PodcastArtifactConflict,
    PodcastArtifactError,
    PodcastArtifactStore,
    PodcastArtifactTooLarge,
    serialize_artifact,
)
from services.podcast_stage_policy import PodcastStagePolicy


class SourceAudioError(ValueError):
    """Safe-to-display enclosure cache failure."""


class SourceAudioNotFound(SourceAudioError):
    pass


class SourceAudioConflict(SourceAudioError):
    pass


class SourceAudioTooLarge(SourceAudioError):
    pass


class SourceAudioTimeout(SourceAudioError):
    pass


class SourceAudioFetchFailed(SourceAudioError):
    pass


@dataclass(frozen=True)
class EnclosureSnapshot:
    url: str
    mime: str
    declared_bytes: int | None

    @property
    def locator_hash(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


def _extensions(episode: ArticleRecord) -> dict[str, Any]:
    try:
        value = json.loads(episode.extensions_json or "{}")
    except (TypeError, ValueError) as exc:
        raise SourceAudioConflict("Podcast 单集 enclosure 元数据无效") from exc
    if not isinstance(value, dict):
        raise SourceAudioConflict("Podcast 单集 enclosure 元数据无效")
    return value


def _enclosure(episode: ArticleRecord) -> EnclosureSnapshot:
    if episode.content_type != "podcast_episode":
        raise SourceAudioNotFound("Podcast 单集不存在")
    metadata = _extensions(episode)
    url = str(metadata.get("audio_url") or "").strip()
    if not url:
        raise SourceAudioNotFound("Podcast 单集没有 enclosure 音频地址")
    mime = str(metadata.get("audio_mime") or "").strip()
    raw_bytes = metadata.get("audio_bytes")
    if raw_bytes in (None, ""):
        declared_bytes = None
    else:
        try:
            declared_bytes = int(raw_bytes)
        except (TypeError, ValueError) as exc:
            raise SourceAudioConflict("Podcast enclosure 大小元数据无效") from exc
        if declared_bytes < 0:
            raise SourceAudioConflict("Podcast enclosure 大小元数据无效")
    return EnclosureSnapshot(url=url, mime=mime, declared_bytes=declared_bytes)


def _episode_and_enclosure(
    session: Session, episode_id: str
) -> tuple[ArticleRecord, EnclosureSnapshot]:
    episode = session.get(ArticleRecord, episode_id)
    if episode is None:
        raise SourceAudioNotFound("Podcast 单集不存在")
    return episode, _enclosure(episode)


def _download_mime(result_mime: str, publisher_mime: str) -> str:
    response = str(result_mime or "").split(";", 1)[0].strip().lower()
    publisher = str(publisher_mime or "").split(";", 1)[0].strip().lower()
    if response.startswith("audio/") or response in {"application/ogg"}:
        return response
    return publisher or response


async def cache_source_audio(
    engine: Engine,
    store: PodcastArtifactStore,
    *,
    episode_id: str,
    podcast_config: PodcastConfig,
    storage_config: PodcastArtifactStorageConfig,
    client_factory: http_safety.AsyncClientFactory,
) -> dict[str, Any]:
    """Cache one enclosure after repeated authority and snapshot checks.

    The artifact registry receives only the enclosure locator's SHA-256 identity;
    it never duplicates a potentially signed URL from publisher metadata.
    """

    policy = PodcastStagePolicy(podcast_config)
    if podcast_config.installation != "external":
        raise SourceAudioConflict(
            "source_audio 只能由 external Podcast installation 抓取"
        )
    policy.require_artifact_writer("source_audio", boundary="enqueue")
    with Session(engine) as session:
        episode, snapshot = _episode_and_enclosure(session, episode_id)
    if (
        snapshot.declared_bytes is not None
        and snapshot.declared_bytes > store.max_bytes
    ):
        raise SourceAudioTooLarge("Podcast enclosure 超过配置的音频大小上限")

    existing = store.find_ready_source(
        episode_id=episode_id, source_locator_hash=snapshot.locator_hash
    )
    if existing is not None:
        refs = store.active_processing_reference_counts((existing.id,))
        payload = serialize_artifact(
            existing, active_processing_refs=refs.get(existing.id, 0)
        )
        return payload

    policy.require_artifact_writer("source_audio", boundary="provider_submit")
    with Session(engine) as session:
        episode, current = _episode_and_enclosure(session, episode_id)
        if current != snapshot:
            raise SourceAudioConflict("Podcast enclosure 元数据已变化，请重试")

    download_limit = snapshot.declared_bytes or store.max_bytes
    with store.reserve_source_download(download_limit):
        fd, path = store.create_upload_temp()
        try:
            with os.fdopen(fd, "wb", closefd=False) as destination:
                try:
                    result = await http_safety.stream_public_url_to_file(
                        snapshot.url,
                        destination,
                        max_bytes=download_limit,
                        max_redirects=storage_config.download_max_redirects,
                        timeout_seconds=storage_config.download_timeout_seconds,
                        client_factory=client_factory,
                    )
                except http_safety.PublicDownloadTimeout:
                    raise SourceAudioTimeout(
                        "Podcast enclosure 下载超时"
                    ) from None
                except http_safety.PublicDownloadError as exc:
                    message = str(exc)
                    if "大小上限" in message:
                        raise SourceAudioTooLarge(
                            "Podcast enclosure 超过配置的音频大小上限"
                        ) from None
                    raise SourceAudioFetchFailed("Podcast enclosure 下载失败") from None
                destination.flush()
                os.fsync(destination.fileno())
            if result.size <= 0:
                raise SourceAudioFetchFailed("Podcast enclosure 返回空内容")

            def validate_commit(session: Session, episode: ArticleRecord) -> None:
                if _enclosure(episode) != snapshot:
                    raise PodcastArtifactConflict("Podcast enclosure 元数据已变化，请重试")
                policy.require_artifact_writer("source_audio", boundary="commit")

            record = store.import_file(
                episode_id=episode_id,
                kind="source_audio",
                path=Path(path),
                content_hash=result.sha256,
                size_bytes=result.size,
                declared_mime=_download_mime(result.content_type, snapshot.mime),
                provenance="publisher_enclosure_cache",
                authority_id=policy.config.authority_id,
                source_locator_hash=snapshot.locator_hash,
                commit_validator=validate_commit,
            )
        except PodcastArtifactTooLarge as exc:
            raise SourceAudioTooLarge(str(exc)) from None
        except PodcastArtifactError:
            raise
        finally:
            os.close(fd)
            Path(path).unlink(missing_ok=True)

    refs = store.active_processing_reference_counts((record.id,))
    payload = serialize_artifact(
        record, active_processing_refs=refs.get(record.id, 0)
    )
    return payload
