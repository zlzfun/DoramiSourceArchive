"""Crash-safe local content-addressed storage for Podcast audio artifacts."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Optional

from sqlalchemy import func, or_, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.db import (
    ArticleRecord,
    AppSettingRecord,
    PodcastArtifactRecord,
    PodcastBudgetReservationRecord,
    PodcastProcessingRecord,
    PodcastStageAttemptRecord,
    PodcastTextArtifactRecord,
    PodcastTextPublicationRecord,
)


ARTIFACT_KINDS = frozenset({"source_audio", "digest_audio_zh"})
ARTIFACT_STATUSES = frozenset({"ready", "published", "withdrawn", "expired"})
ACTIVE_PROCESSING_STATUSES = frozenset(
    {
        "queued",
        "running",
        "retry_wait",
        "reconciliation_required",
        "awaiting_review",
    }
)
ACTIVE_PROVIDER_ATTEMPT_STATES = frozenset(
    {"prepared", "submitted", "request_unknown", "reconciling"}
)
LAST_RECONCILED_SETTING = "podcast_artifacts:last_reconciled_at"
_MIME_ALIASES = {
    "audio/mpeg": "audio/mpeg", "audio/mp3": "audio/mpeg",
    "audio/wav": "audio/wav", "audio/wave": "audio/wav", "audio/x-wav": "audio/wav",
    "audio/mp4": "audio/mp4", "audio/m4a": "audio/mp4", "audio/x-m4a": "audio/mp4",
    "audio/ogg": "audio/ogg", "application/ogg": "audio/ogg",
    "audio/webm": "audio/webm", "video/webm": "audio/webm",
}
_EXTENSIONS = {
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/mp4": ".m4a",
    "audio/ogg": ".ogg", "audio/webm": ".webm",
}
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class PodcastArtifactError(ValueError):
    """Artifact input or lifecycle transition is invalid."""


class PodcastArtifactTooLarge(PodcastArtifactError):
    pass


class PodcastArtifactUnsupportedMedia(PodcastArtifactError):
    pass


class PodcastArtifactProbeUnavailable(PodcastArtifactError):
    pass


class PodcastArtifactNotFound(PodcastArtifactError):
    pass


class PodcastArtifactConflict(PodcastArtifactError):
    pass


class PodcastArtifactRecoveryConflict(PodcastArtifactConflict):
    """A processing-id recovery row exists but cannot be reused safely."""

    pass


class PodcastArtifactStorageFull(PodcastArtifactError):
    """A new unique blob would violate the configured storage guardrails."""

    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _canonical_mime(value: str) -> str:
    return _MIME_ALIASES.get((value or "").split(";", 1)[0].strip().lower(), "")


def sniff_audio_mime(data: bytes) -> str:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    return ""


def _parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _retention_state(
    record: PodcastArtifactRecord, *, active_processing_refs: int = 0
) -> str:
    if record.kind != "source_audio":
        return "durable"
    if record.status == "expired" or record.expired_at:
        return "expired"
    expires = _parse_timestamp(record.expires_at)
    if expires is not None and expires <= dt.datetime.now(dt.timezone.utc):
        return "protected" if active_processing_refs else "due"
    return "temporary"


def serialize_artifact(
    record: PodcastArtifactRecord, *, active_processing_refs: int = 0
) -> dict:
    return {
        "id": record.id, "episode_id": record.episode_id, "kind": record.kind,
        "content_hash": record.content_hash, "mime": record.mime,
        "size_bytes": record.size_bytes, "duration_seconds": record.duration_seconds,
        "status": record.status, "provenance": record.provenance,
        "authority_id": record.authority_id, "created_at": record.created_at,
        "narration_artifact_id": record.narration_artifact_id,
        "narration_content_hash": record.narration_content_hash,
        "processing_id": record.processing_id,
        "producing_attempt_id": record.producing_attempt_id,
        "updated_at": record.updated_at, "published_at": record.published_at,
        "withdrawn_at": record.withdrawn_at,
        "source_locator_hash": record.source_locator_hash,
        "expires_at": record.expires_at,
        "expired_at": record.expired_at,
        "active_processing_refs": int(active_processing_refs),
        "retention_state": _retention_state(
            record, active_processing_refs=active_processing_refs
        ),
    }


def require_current_narration_dependency(
    session: Session,
    *,
    episode_id: str,
    narration_artifact_id: str | None,
    narration_content_hash: str | None,
) -> PodcastTextArtifactRecord:
    """Resolve an exact, currently-published narration input or fail closed."""

    artifact_id = str(narration_artifact_id or "").strip()
    content_hash = str(narration_content_hash or "").strip().lower()
    if not artifact_id or len(content_hash) != 64 or any(
        char not in "0123456789abcdef" for char in content_hash
    ):
        raise PodcastArtifactConflict(
            "精简音频必须绑定当前已发布口播稿的 artifact id 与 SHA-256"
        )
    identity = f"{episode_id}:narration_script_zh"
    publication = session.get(PodcastTextPublicationRecord, identity)
    artifact = session.get(PodcastTextArtifactRecord, artifact_id)
    if (
        publication is None
        or publication.episode_id != episode_id
        or publication.kind != "narration_script_zh"
        or publication.status != "published"
        or publication.artifact_id != artifact_id
        or artifact is None
        or artifact.episode_id != episode_id
        or artifact.kind != "narration_script_zh"
        or artifact.content_hash != content_hash
    ):
        raise PodcastArtifactConflict("精简音频绑定的口播稿不是当前已发布版本")
    return artifact


def withdraw_digest_audio_for_script_change(
    session: Session,
    *,
    episode_id: str,
    current_artifact_id: str | None,
) -> int:
    """Withdraw local audio that no longer matches a narration publication.

    This mutates registry state only. CAS bytes remain referenced and are never
    synchronized; ordinary transcript/blog updates never call this boundary.
    """

    now = _now()
    mismatch = PodcastArtifactRecord.narration_artifact_id != current_artifact_id
    if current_artifact_id is None:
        mismatch = PodcastArtifactRecord.id.is_not(None)
    result = session.exec(
        update(PodcastArtifactRecord)
        .where(
            PodcastArtifactRecord.episode_id == episode_id,
            PodcastArtifactRecord.kind == "digest_audio_zh",
            PodcastArtifactRecord.status.in_(("ready", "published")),
            or_(
                PodcastArtifactRecord.narration_artifact_id.is_(None),
                mismatch,
            ),
        )
        .values(status="withdrawn", withdrawn_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return max(int(getattr(result, "rowcount", 0) or 0), 0)


class PodcastArtifactStore:
    def __init__(
        self,
        engine: Engine,
        root: str | Path,
        *,
        max_bytes: int,
        total_quota_bytes: int,
        source_audio_quota_bytes: int | None = None,
        source_audio_ttl_seconds: int = 7 * 24 * 60 * 60,
        minimum_free_bytes: int,
        staging_ttl_seconds: int,
        allowed_mime_types: Iterable[str],
        ffprobe_binary: str = "ffprobe",
        probe_timeout_seconds: int = 15,
        orphan_grace_seconds: int = 3600,
        probe_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        disk_usage_provider: Optional[Callable[[Path], object]] = None,
    ) -> None:
        self.engine = engine
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = int(max_bytes)
        self.total_quota_bytes = int(total_quota_bytes)
        self.source_audio_quota_bytes = int(
            source_audio_quota_bytes
            if source_audio_quota_bytes is not None
            else total_quota_bytes
        )
        self.source_audio_ttl_seconds = int(source_audio_ttl_seconds)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.staging_ttl_seconds = int(staging_ttl_seconds)
        self.ffprobe_binary = ffprobe_binary.strip()
        self.probe_timeout_seconds = int(probe_timeout_seconds)
        self.orphan_grace_seconds = int(orphan_grace_seconds)
        self._probe_runner = probe_runner or subprocess.run
        self._disk_usage_provider = disk_usage_provider or shutil.disk_usage
        allowed = {_canonical_mime(value) for value in allowed_mime_types}
        self.allowed_mime_types = tuple(sorted(value for value in allowed if value))
        if (
            self.max_bytes <= 0
            or self.total_quota_bytes <= 0
            or self.source_audio_quota_bytes <= 0
            or self.source_audio_ttl_seconds <= 0
            or self.probe_timeout_seconds <= 0
            or self.staging_ttl_seconds < 0
        ):
            raise ValueError("Podcast artifact limits must be positive")
        if (
            self.minimum_free_bytes < 0
            or self.orphan_grace_seconds < 0
            or not self.ffprobe_binary
        ):
            raise ValueError("Invalid Podcast artifact storage configuration")
        if not self.allowed_mime_types:
            raise ValueError("Podcast artifact allowed MIME list cannot be empty")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".incoming").mkdir(exist_ok=True)
        key = str(self.root)
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(key, threading.RLock())

    @contextmanager
    def _cas_lock(self) -> Iterator[None]:
        """Serialize CAS mutation across threads and worker processes."""
        with self._lock:
            lock_fd = os.open(self.root / ".cas.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def create_upload_temp(self) -> tuple[int, Path]:
        fd, raw = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=self.root / ".incoming")
        path = Path(raw)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise
        return fd, path

    def _download_reservations(self) -> list[tuple[Path, str, int]]:
        reservations: list[tuple[Path, str, int]] = []
        for path in (self.root / ".incoming").glob("download-*.reserve"):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="ascii"))
                if isinstance(payload, bool):
                    raise ValueError("invalid reservation")
                if isinstance(payload, int):
                    # Backward compatibility for source-only markers written by
                    # releases before reservation kinds were persisted.
                    kind = "source_audio"
                    reserved = payload
                elif (
                    isinstance(payload, dict)
                    and set(payload) == {"bytes", "kind"}
                    and payload.get("kind") in ARTIFACT_KINDS
                    and isinstance(payload.get("bytes"), int)
                    and not isinstance(payload.get("bytes"), bool)
                ):
                    kind = str(payload["kind"])
                    reserved = int(payload["bytes"])
                else:
                    raise ValueError("invalid reservation")
                if reserved <= 0 or reserved > self.max_bytes:
                    raise ValueError("invalid reservation")
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                # A torn or unknown marker must fail safe for both the total and
                # the source-audio sub-quota until reconciliation removes it.
                kind = "source_audio"
                reserved = self.max_bytes
            reservations.append((path, kind, reserved))
        return reservations

    @contextmanager
    def reserve_download(
        self,
        kind: str,
        max_download_bytes: int,
    ) -> Iterator[None]:
        """Atomically reserve staging, artifact-kind, and free-disk capacity.

        The marker remains exclusively locked for the whole download. A crashed
        worker leaves an unlocked marker that normal staging reconciliation can
        reclaim after the configured TTL; a live slow download is never reaped.
        """

        normalized_kind = str(kind or "").strip()
        if normalized_kind not in ARTIFACT_KINDS:
            raise PodcastArtifactError("不支持的 Podcast artifact kind")
        requested = int(max_download_bytes)
        if requested <= 0 or requested > self.max_bytes:
            raise PodcastArtifactTooLarge(
                f"音频超出大小上限 {self.max_bytes} 字节"
            )
        marker_fd: int | None = None
        marker_path: Path | None = None
        marker_locked = False
        try:
            with self._cas_lock(), Session(self.engine) as session:
                blobs = sum(path.stat().st_size for path in self._blob_files())
                staging = sum(path.stat().st_size for path in self._staging_files())
                reservations = self._download_reservations()
                reserved_total = sum(
                    size for _path, _kind, size in reservations
                )
                if (
                    blobs + staging + reserved_total + requested
                    > self.total_quota_bytes
                ):
                    raise PodcastArtifactStorageFull(
                        "Podcast 音频存储配额不足，拒绝开始下载"
                    )
                source_rows = list(
                    session.exec(
                        select(PodcastArtifactRecord).where(
                            PodcastArtifactRecord.kind == "source_audio",
                            PodcastArtifactRecord.status != "expired",
                        )
                    ).all()
                )
                source_bytes = sum(
                    {row.content_hash: row.size_bytes for row in source_rows}.values()
                )
                reserved_source = sum(
                    size
                    for _path, reservation_kind, size in reservations
                    if reservation_kind == "source_audio"
                )
                if normalized_kind == "source_audio" and (
                    source_bytes + reserved_source + requested
                    > self.source_audio_quota_bytes
                ):
                    raise PodcastArtifactStorageFull(
                        "Podcast 原始音频临时缓存配额不足，拒绝开始下载"
                    )
                _capacity, _used, free = self._disk_usage()
                if free - reserved_total - requested < self.minimum_free_bytes:
                    raise PodcastArtifactStorageFull(
                        "Podcast 音频磁盘可用空间不足，拒绝开始下载"
                    )
                marker_fd, raw = tempfile.mkstemp(
                    prefix="download-",
                    suffix=".reserve",
                    dir=self.root / ".incoming",
                )
                marker_path = Path(raw)
                marker = json.dumps(
                    {"bytes": requested, "kind": normalized_kind},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
                if os.write(marker_fd, marker) != len(marker):
                    raise OSError("short reservation marker write")
                os.fsync(marker_fd)
                fcntl.flock(marker_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                marker_locked = True
            yield
        finally:
            if marker_fd is not None:
                try:
                    if marker_locked:
                        fcntl.flock(marker_fd, fcntl.LOCK_UN)
                finally:
                    os.close(marker_fd)
            if marker_path is not None:
                with self._cas_lock():
                    marker_path.unlink(missing_ok=True)

    @contextmanager
    def reserve_source_download(self, max_download_bytes: int) -> Iterator[None]:
        """Backward-compatible source-audio reservation wrapper."""

        with self.reserve_download("source_audio", max_download_bytes):
            yield

    def _disk_usage(self) -> tuple[int, int, int]:
        usage = self._disk_usage_provider(self.root)
        try:
            return int(usage.total), int(usage.used), int(usage.free)  # type: ignore[attr-defined]
        except AttributeError:
            total, used, free = usage  # type: ignore[misc]
            return int(total), int(used), int(free)

    def validate_audio(self, data: bytes, declared_mime: str) -> str:
        if not data:
            raise PodcastArtifactUnsupportedMedia("音频内容为空")
        if len(data) > self.max_bytes:
            raise PodcastArtifactTooLarge(f"音频超出大小上限 {self.max_bytes} 字节")
        return self._validate_signature(data[:64], declared_mime)

    def _validate_signature(self, header: bytes, declared_mime: str) -> str:
        declared = _canonical_mime(declared_mime)
        if not declared or declared not in self.allowed_mime_types:
            raise PodcastArtifactUnsupportedMedia("Content-Type 不在允许的音频 MIME 列表")
        sniffed = sniff_audio_mime(header)
        if not sniffed or sniffed != declared:
            raise PodcastArtifactUnsupportedMedia("音频格式魔数与 Content-Type 不匹配")
        return sniffed

    def probe_audio(self, path: Path) -> Optional[float]:
        command = [
            self.ffprobe_binary, "-v", "error", "-show_entries",
            "stream=codec_type,duration:format=duration", "-of", "json", str(path),
        ]
        try:
            result = self._probe_runner(
                command, capture_output=True, text=True,
                timeout=self.probe_timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            raise PodcastArtifactProbeUnavailable(
                f"音频探测器不可用: {self.ffprobe_binary}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PodcastArtifactProbeUnavailable("音频探测超时") from exc
        if result.returncode != 0:
            raise PodcastArtifactUnsupportedMedia("ffprobe 无法解码该音频")
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise PodcastArtifactUnsupportedMedia("ffprobe 返回无效结果") from exc
        streams = payload.get("streams") if isinstance(payload, dict) else None
        audio_streams = [
            stream for stream in (streams or [])
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ]
        if not audio_streams:
            raise PodcastArtifactUnsupportedMedia("文件不包含可解码的音频流")
        candidates = [audio_streams[0].get("duration")]
        if isinstance(payload.get("format"), dict):
            candidates.append(payload["format"].get("duration"))
        for value in candidates:
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if duration >= 0:
                return duration
        return None

    def file_path_for_hash(self, content_hash: str, mime: str) -> Path:
        if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
            raise PodcastArtifactError("无效的内容哈希")
        canonical = _canonical_mime(mime)
        ext = _EXTENSIONS.get(canonical)
        if ext is None:
            raise PodcastArtifactError("无效的音频 MIME")
        return self.root / content_hash[:2] / f"{content_hash}{ext}"

    def file_path_for(self, record: PodcastArtifactRecord) -> Path:
        return self.file_path_for_hash(record.content_hash, record.mime)

    def is_intact(self, record: PodcastArtifactRecord) -> bool:
        """Verify that a registry row still resolves to its exact immutable blob."""

        path = self.file_path_for(record)
        try:
            if not path.is_file() or path.stat().st_size != record.size_bytes:
                return False
            content_hash, size = self._hash_file(path)
        except OSError:
            return False
        return size == record.size_bytes and content_hash == record.content_hash

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def import_file(
        self, *, episode_id: str, kind: str, path: Path,
        content_hash: str, size_bytes: int, declared_mime: str,
        provenance: str = "manual_upload", authority_id: str,
        narration_artifact_id: str | None = None,
        narration_content_hash: str | None = None,
        processing_id: str | None = None,
        producing_attempt_id: str | None = None,
        source_locator_hash: str | None = None,
        expires_at: str | None = None,
        commit_validator: Callable[[Session, ArticleRecord], None] | None = None,
    ) -> PodcastArtifactRecord:
        if kind not in ARTIFACT_KINDS:
            raise PodcastArtifactError("不支持的 Podcast artifact kind")
        if size_bytes <= 0:
            raise PodcastArtifactUnsupportedMedia("音频内容为空")
        if size_bytes > self.max_bytes:
            raise PodcastArtifactTooLarge(f"音频超出大小上限 {self.max_bytes} 字节")
        actual_hash, actual_size = self._hash_file(path)
        if actual_hash != content_hash or actual_size != size_bytes:
            raise PodcastArtifactConflict("上传文件在登记前发生变化")
        with path.open("rb") as handle:
            mime = self._validate_signature(handle.read(64), declared_mime)
        duration = self.probe_audio(path)
        target = self.file_path_for_hash(content_hash, mime)
        now = _now()
        normalized_locator_hash = str(source_locator_hash or "").strip().lower() or None
        if normalized_locator_hash is not None and (
            len(normalized_locator_hash) != 64
            or any(char not in "0123456789abcdef" for char in normalized_locator_hash)
        ):
            raise PodcastArtifactError("无效的 source locator 哈希")
        if kind == "source_audio":
            parsed_expiry = _parse_timestamp(expires_at)
            if parsed_expiry is None:
                parsed_expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                    seconds=self.source_audio_ttl_seconds
                )
            if parsed_expiry <= dt.datetime.now(dt.timezone.utc):
                raise PodcastArtifactConflict("source_audio 到期时间必须晚于登记时间")
            normalized_expires_at = parsed_expiry.isoformat(timespec="microseconds")
        else:
            if normalized_locator_hash is not None or expires_at is not None:
                raise PodcastArtifactConflict("精简音频不能绑定外部 locator 或缓存到期时间")
            normalized_expires_at = None

        with self._cas_lock(), Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                preliminary = session.get(ArticleRecord, episode_id)
                if preliminary is None:
                    raise PodcastArtifactNotFound("Podcast 单集不存在")
                if preliminary.content_type != "podcast_episode":
                    raise PodcastArtifactError("episode_id 不是 podcast_episode")
                episode_statement = select(ArticleRecord).where(
                    ArticleRecord.id == episode_id
                )
                if self.engine.dialect.name == "postgresql":
                    episode_statement = episode_statement.with_for_update().execution_options(
                        populate_existing=True
                    )
                episode = session.exec(episode_statement).first()
                if episode is None:
                    raise PodcastArtifactNotFound("Podcast 单集不存在")
                if episode.content_type != "podcast_episode":
                    raise PodcastArtifactError("episode_id 不是 podcast_episode")
                if episode.source_id != preliminary.source_id:
                    raise PodcastArtifactConflict(
                        "Podcast 单集所属数据源已变化，请重试"
                    )
                if commit_validator is not None:
                    commit_validator(session, episode)
                if kind == "digest_audio_zh":
                    narration_dependency = require_current_narration_dependency(
                        session,
                        episode_id=episode_id,
                        narration_artifact_id=narration_artifact_id,
                        narration_content_hash=narration_content_hash,
                    )
                elif (
                    narration_artifact_id is not None
                    or narration_content_hash is not None
                    or processing_id is not None
                ):
                    raise PodcastArtifactConflict(
                        "source_audio 不能绑定口播稿或处理任务依赖"
                    )
                repair_candidate: PodcastArtifactRecord | None = None
                if kind == "source_audio" and normalized_locator_hash:
                    existing_statement = (
                        select(PodcastArtifactRecord)
                        .where(
                            PodcastArtifactRecord.episode_id == episode_id,
                            PodcastArtifactRecord.kind == "source_audio",
                            PodcastArtifactRecord.status == "ready",
                            PodcastArtifactRecord.source_locator_hash
                            == normalized_locator_hash,
                            PodcastArtifactRecord.expires_at > now,
                        )
                        .order_by(
                            PodcastArtifactRecord.created_at.desc(),
                            PodcastArtifactRecord.id.desc(),
                        )
                    )
                    if self.engine.dialect.name == "postgresql":
                        existing_statement = existing_statement.with_for_update()
                    existing_rows = list(
                        session.exec(existing_statement).all()
                    )
                    for existing in existing_rows:
                        if self.is_intact(existing):
                            session.expunge(existing)
                            session.commit()
                            return existing
                        if (
                            repair_candidate is None
                            and existing.content_hash == content_hash
                            and existing.ext == _EXTENSIONS[mime]
                            and existing.size_bytes == size_bytes
                        ):
                            repair_candidate = existing
                normalized_processing_id = str(processing_id or "").strip() or None
                normalized_attempt_id = (
                    str(producing_attempt_id or "").strip() or None
                )
                if normalized_attempt_id is not None and normalized_processing_id is None:
                    raise PodcastArtifactConflict(
                        "自动精简音频必须同时绑定 processing 与 TTS attempt"
                    )
                if normalized_attempt_id is not None and kind != "digest_audio_zh":
                    raise PodcastArtifactConflict(
                        "只有自动精简音频可以绑定 TTS attempt"
                    )
                normalized_provenance = (provenance or "manual_upload").strip()[:200]
                if normalized_provenance.casefold() == "tts":
                    normalized_provenance = "tts"
                if normalized_provenance == "tts" and normalized_attempt_id is None:
                    raise PodcastArtifactConflict(
                        "自动 TTS 音频必须绑定 producing attempt"
                    )
                if normalized_processing_id is not None:
                    processing = session.get(
                        PodcastProcessingRecord, normalized_processing_id
                    )
                    if (
                        processing is None
                        or processing.episode_id != episode_id
                        or processing.requested_target != "digest_audio"
                        or processing.narration_artifact_id
                        != narration_dependency.id
                        or processing.narration_content_hash
                        != narration_dependency.content_hash
                    ):
                        raise PodcastArtifactConflict("processing_id 不属于该精简音频任务")
                producing_attempt: PodcastStageAttemptRecord | None = None
                if normalized_attempt_id is not None:
                    producing_attempt = session.get(
                        PodcastStageAttemptRecord, normalized_attempt_id
                    )
                    reservation = session.exec(
                        select(PodcastBudgetReservationRecord).where(
                            PodcastBudgetReservationRecord.attempt_id
                            == normalized_attempt_id
                        )
                    ).first()
                    if (
                        producing_attempt is None
                        or producing_attempt.processing_id != normalized_processing_id
                        or producing_attempt.stage != "tts"
                        or producing_attempt.execution_kind != "provider"
                        or producing_attempt.submission_state != "submitted"
                        or producing_attempt.request_unknown
                        or producing_attempt.input_hash
                        != narration_dependency.content_hash
                        or producing_attempt.output_authority_id
                        != (authority_id or "").strip()[:200]
                        or reservation is None
                        or reservation.processing_id != normalized_processing_id
                        or reservation.status != "settled"
                    ):
                        raise PodcastArtifactConflict(
                            "TTS attempt 尚未结算或不属于该精简音频任务"
                        )
                    existing_attempt_output = session.exec(
                        select(PodcastArtifactRecord).where(
                            PodcastArtifactRecord.producing_attempt_id
                            == normalized_attempt_id
                        )
                    ).first()
                    if existing_attempt_output is not None:
                        exact_replay = (
                            existing_attempt_output.processing_id
                            == normalized_processing_id
                            and existing_attempt_output.episode_id == episode_id
                            and existing_attempt_output.kind == "digest_audio_zh"
                            and existing_attempt_output.content_hash == content_hash
                            and existing_attempt_output.size_bytes == size_bytes
                            and existing_attempt_output.mime == mime
                            and existing_attempt_output.narration_artifact_id
                            == narration_dependency.id
                            and existing_attempt_output.narration_content_hash
                            == narration_dependency.content_hash
                            and producing_attempt.output_artifact_id
                            == existing_attempt_output.id
                            and producing_attempt.output_artifact_kind
                            == "digest_audio_zh"
                            and producing_attempt.output_hash == content_hash
                            and producing_attempt.output_authority_id
                            == (authority_id or "").strip()[:200]
                            and self.is_intact(existing_attempt_output)
                        )
                        if not exact_replay:
                            raise PodcastArtifactRecoveryConflict(
                                "TTS attempt 已绑定冲突或损坏的精简音频"
                            )
                        session.expunge(existing_attempt_output)
                        session.commit()
                        return existing_attempt_output
                    if (
                        producing_attempt.output_hash
                        or producing_attempt.output_artifact_id
                        or producing_attempt.output_artifact_kind
                    ):
                        raise PodcastArtifactRecoveryConflict(
                            "TTS attempt 已绑定不同输出"
                        )
                target.parent.mkdir(parents=True, exist_ok=True)
                valid_existing = False
                replaced_bytes = 0
                if target.is_file() and target.stat().st_size == size_bytes:
                    existing_hash, _ = self._hash_file(target)
                    valid_existing = existing_hash == content_hash
                if not valid_existing:
                    if target.is_file():
                        replaced_bytes = target.stat().st_size
                    current_blob_bytes = sum(
                        blob.stat().st_size for blob in self._blob_files()
                    )
                    projected_blob_bytes = current_blob_bytes - replaced_bytes + size_bytes
                    if projected_blob_bytes > self.total_quota_bytes:
                        raise PodcastArtifactStorageFull(
                            "Podcast 音频存储配额不足，拒绝写入新的内容 blob"
                        )
                    _capacity, _used, free = self._disk_usage()
                    # `.incoming` and CAS live below the same root, so the staged
                    # file is already reflected in current free space. Atomic rename
                    # consumes no additional bytes; replacing a corrupt target only
                    # releases that target's previous allocation.
                    projected_free = free + replaced_bytes
                    if projected_free < self.minimum_free_bytes:
                        raise PodcastArtifactStorageFull(
                            "Podcast 音频磁盘可用空间不足，拒绝写入新的内容 blob"
                        )
                if kind == "source_audio":
                    active_source_rows = list(
                        session.exec(
                            select(PodcastArtifactRecord).where(
                                PodcastArtifactRecord.kind == "source_audio",
                                PodcastArtifactRecord.status != "expired",
                            )
                        ).all()
                    )
                    source_bytes_by_hash = {
                        row.content_hash: row.size_bytes for row in active_source_rows
                    }
                    projected_source_bytes = sum(source_bytes_by_hash.values())
                    if content_hash not in source_bytes_by_hash:
                        projected_source_bytes += size_bytes
                    if projected_source_bytes > self.source_audio_quota_bytes:
                        raise PodcastArtifactStorageFull(
                            "Podcast 原始音频临时缓存配额不足，拒绝写入"
                        )
                if not valid_existing:
                    os.replace(path, target)
                    self._fsync_parent(target)
                if repair_candidate is not None:
                    # The registry row is immutable audit metadata. Repair the
                    # same-address CAS blob and reuse that row without extending
                    # its TTL or manufacturing a duplicate record.
                    session.expunge(repair_candidate)
                    session.commit()
                    return repair_candidate
                record = PodcastArtifactRecord(
                    id=(
                        "podcast-tts-"
                        + hashlib.sha256(normalized_attempt_id.encode("utf-8")).hexdigest()[:32]
                        if normalized_attempt_id is not None
                        else uuid.uuid4().hex
                    ), episode_id=episode_id, kind=kind,
                    content_hash=content_hash, mime=mime, ext=_EXTENSIONS[mime],
                    size_bytes=size_bytes, duration_seconds=duration, status="ready",
                    provenance=normalized_provenance,
                    authority_id=(authority_id or "").strip()[:200],
                    narration_artifact_id=(
                        str(narration_artifact_id).strip()
                        if narration_artifact_id is not None else None
                    ),
                    narration_content_hash=(
                        str(narration_content_hash).strip().lower()
                        if narration_content_hash is not None else None
                    ),
                    processing_id=normalized_processing_id,
                    producing_attempt_id=normalized_attempt_id,
                    source_locator_hash=normalized_locator_hash,
                    expires_at=normalized_expires_at,
                    expired_at=None,
                    created_at=now, updated_at=now,
                )
                if producing_attempt is not None:
                    producing_attempt.output_hash = content_hash
                    producing_attempt.output_artifact_id = record.id
                    producing_attempt.output_artifact_kind = "digest_audio_zh"
                    producing_attempt.updated_at = now
                    session.add(producing_attempt)
                    session.flush()
                session.add(record)
                session.commit()
                session.refresh(record)
                return record
            except IntegrityError as exc:
                session.rollback()
                raise PodcastArtifactConflict(
                    "精简音频依赖在登记过程中发生变化"
                ) from exc
            except Exception:
                session.rollback()
                raise

    def import_bytes(
        self, *, episode_id: str, kind: str, data: bytes, declared_mime: str,
        duration_seconds: Optional[float] = None, status: str = "ready",
        provenance: str = "manual_upload", authority_id: str = "",
        narration_artifact_id: str | None = None,
        narration_content_hash: str | None = None,
        processing_id: str | None = None,
        source_locator_hash: str | None = None,
        expires_at: str | None = None,
    ) -> PodcastArtifactRecord:
        """Compatibility helper for trusted callers; HTTP imports use streaming."""
        if status != "ready":
            raise PodcastArtifactError("原始导入状态只能是 ready")
        self.validate_audio(data, declared_mime)
        fd, path = self.create_upload_temp()
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return self.import_file(
                episode_id=episode_id, kind=kind, path=path,
                content_hash=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                declared_mime=declared_mime, provenance=provenance,
                authority_id=authority_id,
                narration_artifact_id=narration_artifact_id,
                narration_content_hash=narration_content_hash,
                processing_id=processing_id,
                source_locator_hash=source_locator_hash,
                expires_at=expires_at,
            )
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)

    def get(self, artifact_id: str) -> Optional[PodcastArtifactRecord]:
        with Session(self.engine) as session:
            return session.get(PodcastArtifactRecord, artifact_id)

    def find_digest_audio_by_processing_id(
        self,
        *,
        processing_id: str,
        episode_id: str,
        narration_artifact_id: str,
        narration_content_hash: str,
        content_hash: str | None = None,
        size_bytes: int | None = None,
        mime: str | None = None,
    ) -> Optional[PodcastArtifactRecord]:
        """Recover an already-registered TTS result without another provider call.

        A missing processing binding is a normal cache miss.  Once any row uses
        the processing id, however, every supplied identity field must match and
        the immutable CAS blob must still be intact; callers must not silently
        replace or re-submit an inconsistent result.
        """

        normalized_processing_id = str(processing_id or "").strip()
        normalized_episode_id = str(episode_id or "").strip()
        normalized_narration_id = str(narration_artifact_id or "").strip()
        normalized_narration_hash = str(narration_content_hash or "").strip().lower()
        if (
            not normalized_processing_id
            or not normalized_episode_id
            or not normalized_narration_id
        ):
            raise PodcastArtifactError("精简音频恢复身份字段不能为空")
        if len(normalized_narration_hash) != 64 or any(
            char not in "0123456789abcdef" for char in normalized_narration_hash
        ):
            raise PodcastArtifactError("精简音频恢复口播稿哈希无效")

        normalized_content_hash: str | None = None
        if content_hash is not None:
            normalized_content_hash = str(content_hash).strip().lower()
            if len(normalized_content_hash) != 64 or any(
                char not in "0123456789abcdef" for char in normalized_content_hash
            ):
                raise PodcastArtifactError("精简音频恢复内容哈希无效")
        if size_bytes is not None and (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise PodcastArtifactError("精简音频恢复大小必须是正整数")
        normalized_mime: str | None = None
        if mime is not None:
            normalized_mime = _canonical_mime(mime)
            if not normalized_mime:
                raise PodcastArtifactError("精简音频恢复 MIME 无效")

        with self._cas_lock(), Session(self.engine) as session:
            rows = list(
                session.exec(
                    select(PodcastArtifactRecord).where(
                        PodcastArtifactRecord.processing_id
                        == normalized_processing_id
                    )
                ).all()
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise PodcastArtifactRecoveryConflict(
                    "processing_id 关联了多个 Podcast 音频，拒绝恢复"
                )
            record = rows[0]
            processing = session.get(
                PodcastProcessingRecord, normalized_processing_id
            )
            identity_matches = (
                record.kind == "digest_audio_zh"
                and record.processing_id == normalized_processing_id
                and record.episode_id == normalized_episode_id
                and record.narration_artifact_id == normalized_narration_id
                and record.narration_content_hash == normalized_narration_hash
                and record.status in {"ready", "published"}
                and record.mime in _EXTENSIONS
                and record.ext == _EXTENSIONS.get(record.mime)
                and processing is not None
                and processing.episode_id == normalized_episode_id
                and processing.requested_target == "digest_audio"
                and processing.narration_artifact_id == normalized_narration_id
                and processing.narration_content_hash
                == normalized_narration_hash
                and (
                    normalized_content_hash is None
                    or record.content_hash == normalized_content_hash
                )
                and (size_bytes is None or record.size_bytes == size_bytes)
                and (normalized_mime is None or record.mime == normalized_mime)
            )
            if not identity_matches:
                raise PodcastArtifactRecoveryConflict(
                    "processing_id 已绑定到不同的精简音频产物，拒绝恢复"
                )
            try:
                intact = self.is_intact(record)
            except PodcastArtifactError as exc:
                raise PodcastArtifactRecoveryConflict(
                    "processing_id 对应的精简音频登记无效，拒绝恢复"
                ) from exc
            if not intact:
                raise PodcastArtifactRecoveryConflict(
                    "processing_id 对应的精简音频 CAS 缺失或损坏，拒绝恢复"
                )
            session.expunge(record)
            return record

    def find_ready_source(
        self, *, episode_id: str, source_locator_hash: str
    ) -> Optional[PodcastArtifactRecord]:
        """Return a still-live local enclosure cache, never an expired row."""

        now = _now()
        with self._cas_lock(), Session(self.engine) as session:
            rows = list(
                session.exec(
                    select(PodcastArtifactRecord)
                    .where(
                        PodcastArtifactRecord.episode_id == episode_id,
                        PodcastArtifactRecord.kind == "source_audio",
                        PodcastArtifactRecord.status == "ready",
                        PodcastArtifactRecord.source_locator_hash
                        == source_locator_hash,
                        PodcastArtifactRecord.expires_at > now,
                    )
                    .order_by(
                        PodcastArtifactRecord.created_at.desc(),
                        PodcastArtifactRecord.id.desc(),
                    )
                ).all()
            )
            for row in rows:
                if self.is_intact(row):
                    session.expunge(row)
                    return row
        return None

    @staticmethod
    def _get_readable_in_session(
        session: Session,
        artifact_id: str,
        *,
        admin: bool,
    ) -> PodcastArtifactRecord:
        record = session.exec(
            select(PodcastArtifactRecord)
            .where(PodcastArtifactRecord.id == artifact_id)
            .execution_options(populate_existing=True)
        ).first()
        allowed = {"ready", "published"} if admin else {"published"}
        if record is None or record.status not in allowed:
            raise PodcastArtifactNotFound("Podcast 音频不存在或尚未发布")
        if not admin and record.kind != "digest_audio_zh":
            raise PodcastArtifactNotFound("Podcast 音频不存在或尚未发布")
        if record.kind == "digest_audio_zh":
            try:
                require_current_narration_dependency(
                    session,
                    episode_id=record.episode_id,
                    narration_artifact_id=record.narration_artifact_id,
                    narration_content_hash=record.narration_content_hash,
                )
            except PodcastArtifactConflict as exc:
                raise PodcastArtifactNotFound(
                    "Podcast 音频不存在或尚未发布"
                ) from exc
        return record

    def get_readable(self, artifact_id: str, *, admin: bool = False) -> PodcastArtifactRecord:
        with Session(self.engine) as session:
            return self._get_readable_in_session(
                session, artifact_id, admin=admin
            )

    def open_readable_audio(
        self,
        artifact_id: str,
        *,
        admin: bool,
        authorize: Callable[[Session, PodcastArtifactRecord], None] | None = None,
        opener: Callable[[PodcastArtifactRecord], BinaryIO] | None = None,
    ) -> tuple[PodcastArtifactRecord, BinaryIO]:
        """Authorize and open one audio file against a serialized DB snapshot.

        PostgreSQL shares the same episode advisory-lock key used by narration
        publication triggers. SQLite takes its existing immediate transaction
        lock. A second dependency/authorization pass after opening catches any
        in-transaction invalidation and keeps GET, HEAD and Range on one path.
        """

        open_file = opener or (lambda row: self.file_path_for(row).open("rb"))
        handle: BinaryIO | None = None
        with Session(self.engine) as session:
            try:
                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                preliminary = session.get(PodcastArtifactRecord, artifact_id)
                if preliminary is None:
                    raise PodcastArtifactNotFound(
                        "Podcast 音频不存在或尚未发布"
                    )
                if self.engine.dialect.name == "postgresql":
                    session.execute(text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:lock_key, 0))"
                    ), {
                        "lock_key": (
                            f"dorami:podcast-audio:{preliminary.episode_id}"
                        )
                    })
                record = self._get_readable_in_session(
                    session, artifact_id, admin=admin
                )
                if authorize is not None:
                    authorize(session, record)
                handle = open_file(record)
                session.flush()
                session.expire_all()
                record = self._get_readable_in_session(
                    session, artifact_id, admin=admin
                )
                if authorize is not None:
                    authorize(session, record)
                session.expunge(record)
                session.commit()
                return record, handle
            except Exception:
                session.rollback()
                if handle is not None:
                    handle.close()
                raise

    def open_asr_source_audio(
        self,
        *,
        verify: Callable[[Session], Any],
        authority_id: str,
        authorize: Callable[
            [Session, PodcastArtifactRecord, PodcastProcessingRecord, ArticleRecord],
            None,
        ],
        opener: Callable[[PodcastArtifactRecord], BinaryIO] | None = None,
    ) -> tuple[PodcastArtifactRecord, BinaryIO]:
        """Verify, authorize and open one provider-fetchable source blob.

        Signature verification deliberately happens before any serialization
        lock, so unauthenticated traffic cannot occupy SQLite's writer slot.
        Once verified, PostgreSQL acquires the episode audio lock; SQLite uses
        one immediate transaction.  The
        immutable provider capability, processing input, current provider
        attempt and opened CAS file are rechecked in that serialized snapshot
        before a byte can leave the deployment.
        """

        expected_authority = str(authority_id or "").strip()
        open_file = opener or (lambda row: self.file_path_for(row).open("rb"))
        handle: BinaryIO | None = None
        with Session(self.engine) as session:
            try:
                # Verification may read a KV-overridden signing key.  End that
                # read transaction before entering the serialized authorization
                # snapshot; invalid capabilities never acquire a write lock.
                claims = verify(session)
                session.rollback()

                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")

                artifact_id = str(getattr(claims, "artifact_id", "") or "")
                processing_id = str(getattr(claims, "processing_id", "") or "")
                preliminary = session.get(PodcastArtifactRecord, artifact_id)
                preliminary_episode = (
                    session.get(ArticleRecord, preliminary.episode_id)
                    if preliminary is not None
                    else None
                )
                if preliminary is None or preliminary_episode is None:
                    raise PodcastArtifactNotFound("Podcast ASR 音频不存在")
                locked_episode_id = preliminary_episode.id
                locked_source_id = preliminary_episode.source_id

                if self.engine.dialect.name == "postgresql":
                    session.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtextextended(:lock_key, 0))"
                        ),
                        {
                            "lock_key": (
                                f"dorami:podcast-audio:{locked_episode_id}"
                            )
                        },
                    )
                    session.expire_all()

                def load_authorized() -> PodcastArtifactRecord:
                    record = session.exec(
                        select(PodcastArtifactRecord)
                        .where(PodcastArtifactRecord.id == artifact_id)
                        .execution_options(populate_existing=True)
                    ).first()
                    process = session.exec(
                        select(PodcastProcessingRecord)
                        .where(PodcastProcessingRecord.id == processing_id)
                        .execution_options(populate_existing=True)
                    ).first()
                    episode = (
                        session.exec(
                            select(ArticleRecord)
                            .where(ArticleRecord.id == record.episode_id)
                            .execution_options(populate_existing=True)
                        ).first()
                        if record is not None
                        else None
                    )
                    attempts = list(
                        session.exec(
                            select(PodcastStageAttemptRecord)
                            .where(
                                PodcastStageAttemptRecord.processing_id
                                == processing_id,
                                PodcastStageAttemptRecord.submission_state.in_(
                                    ACTIVE_PROVIDER_ATTEMPT_STATES
                                ),
                            )
                            .order_by(
                                PodcastStageAttemptRecord.attempt_no.desc(),
                                PodcastStageAttemptRecord.id.desc(),
                            )
                            .execution_options(populate_existing=True)
                        ).all()
                    )
                    claim_digest = str(
                        getattr(claims, "content_sha256", "") or ""
                    )
                    claim_authority = str(
                        getattr(claims, "authority_id", "") or ""
                    )
                    expires_at = (
                        _parse_timestamp(record.expires_at)
                        if record is not None
                        else None
                    )
                    claim_expires_at_raw = getattr(claims, "expires_at", None)
                    claim_expires_at = (
                        dt.datetime.fromtimestamp(
                            claim_expires_at_raw, tz=dt.timezone.utc
                        )
                        if isinstance(claim_expires_at_raw, int)
                        and not isinstance(claim_expires_at_raw, bool)
                        else None
                    )
                    if (
                        not expected_authority
                        or claim_authority != expected_authority
                        or record is None
                        or process is None
                        or episode is None
                        or episode.content_type != "podcast_episode"
                        or episode.id != locked_episode_id
                        or episode.source_id != locked_source_id
                        or record.episode_id != episode.id
                        or record.kind != "source_audio"
                        or record.status != "ready"
                        or record.authority_id != expected_authority
                        or record.content_hash != claim_digest
                        or record.size_bytes <= 0
                        or expires_at is None
                        or expires_at <= dt.datetime.now(dt.timezone.utc)
                        or claim_expires_at is None
                        or claim_expires_at > expires_at
                        or process.episode_id != episode.id
                        or process.stage != "asr"
                        or process.processing_status
                        not in {
                            "running",
                            "retry_wait",
                            "reconciliation_required",
                        }
                        or process.input_artifact_id != record.id
                        or process.input_artifact_kind != "source_audio"
                        or process.input_content_hash != record.content_hash
                        or len(attempts) != 1
                        or attempts[0].stage != "asr"
                        or attempts[0].execution_kind != "provider"
                        or attempts[0].input_hash != record.content_hash
                    ):
                        raise PodcastArtifactNotFound("Podcast ASR 音频不存在")
                    authorize(session, record, process, episode)
                    return record

                record = load_authorized()
                try:
                    handle = open_file(record)
                    stat_result = os.fstat(handle.fileno())
                    digest = hashlib.sha256()
                    size = 0
                    handle.seek(0)
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                    handle.seek(0)
                    if (
                        size != record.size_bytes
                        or stat_result.st_size != record.size_bytes
                        or digest.hexdigest() != record.content_hash
                    ):
                        raise PodcastArtifactNotFound("Podcast ASR 音频不存在")
                except (OSError, PodcastArtifactError) as exc:
                    raise PodcastArtifactNotFound(
                        "Podcast ASR 音频不存在"
                    ) from exc

                session.flush()
                session.expire_all()
                record = load_authorized()
                session.expunge(record)
                session.commit()
                return record, handle
            except Exception:
                session.rollback()
                if handle is not None:
                    handle.close()
                raise

    def list(self, *, episode_id: str = "", status: str = "", kind: str = "", limit: int = 100) -> list[PodcastArtifactRecord]:
        statement = select(PodcastArtifactRecord)
        if episode_id:
            statement = statement.where(PodcastArtifactRecord.episode_id == episode_id)
        if status:
            if status not in ARTIFACT_STATUSES:
                raise PodcastArtifactError("无效的 artifact status")
            statement = statement.where(PodcastArtifactRecord.status == status)
        if kind:
            if kind not in ARTIFACT_KINDS:
                raise PodcastArtifactError("无效的 artifact kind")
            statement = statement.where(PodcastArtifactRecord.kind == kind)
        statement = statement.order_by(
            PodcastArtifactRecord.created_at.desc(), PodcastArtifactRecord.id.desc()
        ).limit(min(max(int(limit), 1), 500))
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def _referenced_paths(self) -> set[Path]:
        with Session(self.engine) as session:
            rows = list(
                session.exec(
                    select(PodcastArtifactRecord).where(
                        PodcastArtifactRecord.status != "expired"
                    )
                ).all()
            )
        return {self.file_path_for(row) for row in rows}

    def active_processing_reference_counts(
        self, artifact_ids: Iterable[str]
    ) -> dict[str, int]:
        ids = tuple(dict.fromkeys(str(value) for value in artifact_ids if value))
        if not ids:
            return {}
        with Session(self.engine) as session:
            return self._active_processing_reference_counts(session, ids)

    @staticmethod
    def _active_processing_reference_counts(
        session: Session, artifact_ids: Iterable[str]
    ) -> dict[str, int]:
        ids = tuple(dict.fromkeys(str(value) for value in artifact_ids if value))
        if not ids:
            return {}
        rows = session.exec(
            select(
                PodcastProcessingRecord.input_artifact_id,
                func.count(PodcastProcessingRecord.id),
            )
            .where(
                PodcastProcessingRecord.input_artifact_id.in_(ids),
                PodcastProcessingRecord.processing_status.in_(
                    ACTIVE_PROCESSING_STATUSES
                ),
            )
            .group_by(PodcastProcessingRecord.input_artifact_id)
        ).all()
        return {str(artifact_id): int(count) for artifact_id, count in rows}

    def _blob_files(self) -> list[Path]:
        suffixes = set(_EXTENSIONS.values())
        return [path for path in self.root.glob("*/*") if path.is_file() and path.suffix in suffixes]

    def _staging_files(self) -> list[Path]:
        return [path for path in (self.root / ".incoming").glob("*.part") if path.is_file()]

    def _staging_stats(self) -> tuple[int, int, int, int]:
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        count = total_bytes = stale_count = stale_bytes = 0
        for path in self._staging_files():
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            count += 1
            total_bytes += stat.st_size
            if now - stat.st_mtime >= self.staging_ttl_seconds:
                stale_count += 1
                stale_bytes += stat.st_size
        return count, total_bytes, stale_count, stale_bytes

    def _reservation_stats(self) -> tuple[int, int]:
        reservations = self._download_reservations()
        return len(reservations), sum(
            size for _path, _kind, size in reservations
        )

    def _orphan_stats(self) -> tuple[int, int, int]:
        referenced = self._referenced_paths()
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        orphan_count = orphan_bytes = reclaimable = 0
        for path in self._blob_files():
            if path in referenced:
                continue
            stat = path.stat()
            orphan_count += 1
            orphan_bytes += stat.st_size
            if now - stat.st_mtime >= self.orphan_grace_seconds:
                reclaimable += 1
        return orphan_count, orphan_bytes, reclaimable

    def stats(self) -> dict[str, int | bool | str | None]:
        with self._cas_lock():
            with Session(self.engine) as session:
                rows = list(session.exec(select(PodcastArtifactRecord)).all())
            counts = {status: 0 for status in ARTIFACT_STATUSES}
            for row in rows:
                counts[row.status] = counts.get(row.status, 0) + 1
            files = self._blob_files()
            disk_bytes = sum(path.stat().st_size for path in files)
            missing_files = sum(
                1
                for row in rows
                if row.status != "expired" and not self.file_path_for(row).is_file()
            )
            orphan_count, orphan_bytes, reclaimable = self._orphan_stats()
            staging_count, staging_bytes, stale_count, stale_bytes = self._staging_stats()
            reservation_count, reservation_bytes = self._reservation_stats()
            capacity, used, free = self._disk_usage()
            source_rows = [
                row
                for row in rows
                if row.kind == "source_audio" and row.status != "expired"
            ]
            source_bytes = sum(
                {row.content_hash: row.size_bytes for row in source_rows}.values()
            )
            active_counts = self.active_processing_reference_counts(
                row.id for row in source_rows
            )
            now_dt = dt.datetime.now(dt.timezone.utc)
            due_rows = [
                row
                for row in source_rows
                if (_parse_timestamp(row.expires_at) or dt.datetime.max.replace(
                    tzinfo=dt.timezone.utc
                ))
                <= now_dt
            ]
            future_expiries = sorted(
                expiry
                for row in source_rows
                if (expiry := _parse_timestamp(row.expires_at)) is not None
                and expiry > now_dt
            )
            with Session(self.engine) as session:
                reconciled = session.get(AppSettingRecord, LAST_RECONCILED_SETTING)
            quota_pressure = (
                disk_bytes + staging_bytes + reservation_bytes
                >= self.total_quota_bytes
            )
            source_quota_pressure = (
                source_bytes + reservation_bytes >= self.source_audio_quota_bytes
            )
            disk_pressure = (
                free - reservation_bytes < self.minimum_free_bytes
            )
            return {
                "artifacts": len(rows), "ready": counts["ready"],
                "published": counts["published"], "withdrawn": counts["withdrawn"],
                "expired": counts["expired"],
                "logical_bytes": sum(row.size_bytes for row in rows),
                "disk_bytes": disk_bytes,
                "missing_files": missing_files,
                "orphan_blobs": orphan_count, "orphan_bytes": orphan_bytes,
                "reclaimable_orphan_blobs": reclaimable,
                "quota_bytes": self.total_quota_bytes,
                "quota_remaining_bytes": max(
                    self.total_quota_bytes
                    - disk_bytes
                    - staging_bytes
                    - reservation_bytes,
                    0,
                ),
                "minimum_free_bytes": self.minimum_free_bytes,
                "disk_capacity_bytes": capacity,
                "disk_used_bytes": used,
                "disk_free_bytes": free,
                "quota_pressure": quota_pressure,
                "disk_pressure": disk_pressure,
                "storage_pressure": (
                    quota_pressure or source_quota_pressure or disk_pressure
                ),
                "source_audio_bytes": source_bytes,
                "source_audio_quota_bytes": self.source_audio_quota_bytes,
                "source_audio_quota_remaining_bytes": max(
                    self.source_audio_quota_bytes
                    - source_bytes
                    - reservation_bytes,
                    0,
                ),
                "source_audio_quota_pressure": source_quota_pressure,
                "expired_source_audio": sum(
                    1
                    for row in rows
                    if row.kind == "source_audio" and row.status == "expired"
                ),
                "expired_protected": sum(
                    1 for row in due_rows if active_counts.get(row.id, 0) > 0
                ),
                "source_audio_due": len(due_rows),
                "next_expiry_at": (
                    future_expiries[0].isoformat(timespec="microseconds")
                    if future_expiries
                    else None
                ),
                "last_reconciled_at": reconciled.value if reconciled else None,
                "staging_ttl_seconds": self.staging_ttl_seconds,
                "staging_files": staging_count,
                "staging_bytes": staging_bytes,
                "stale_staging_files": stale_count,
                "stale_staging_bytes": stale_bytes,
                "download_reservations": reservation_count,
                "download_reserved_bytes": reservation_bytes,
            }

    def publish(self, artifact_id: str, *, expected_updated_at: str) -> PodcastArtifactRecord:
        with Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(PodcastArtifactRecord, artifact_id)
            if record is None:
                raise PodcastArtifactNotFound("Podcast artifact 不存在")
            if record.updated_at != expected_updated_at:
                raise PodcastArtifactConflict("Podcast artifact 已被其他操作更新")
            if record.kind != "digest_audio_zh":
                raise PodcastArtifactConflict("source_audio 永远不能发布到 Reader")
            if record.status != "ready":
                raise PodcastArtifactConflict("只有 ready 的精简音频可以发布")
            if not self.file_path_for(record).is_file():
                raise PodcastArtifactConflict("Podcast 音频文件不存在，不能发布")
            episode = session.get(ArticleRecord, record.episode_id)
            if episode is None or episode.content_type != "podcast_episode":
                raise PodcastArtifactNotFound("Podcast 单集不存在")
            source_id = episode.source_id
            if self.engine.dialect.name == "postgresql":
                session.expire_all()
                record = session.get(PodcastArtifactRecord, artifact_id)
                episode = (
                    session.get(ArticleRecord, record.episode_id)
                    if record is not None
                    else None
                )
                if (
                    record is None
                    or record.updated_at != expected_updated_at
                    or record.kind != "digest_audio_zh"
                    or record.status != "ready"
                ):
                    raise PodcastArtifactConflict(
                        "Podcast artifact 已被其他操作更新"
                    )
                if (
                    episode is None
                    or episode.content_type != "podcast_episode"
                    or episode.source_id != source_id
                ):
                    raise PodcastArtifactConflict(
                        "Podcast 单集所属数据源已变化，请重试"
                    )
            require_current_narration_dependency(
                session,
                episode_id=record.episode_id,
                narration_artifact_id=record.narration_artifact_id,
                narration_content_hash=record.narration_content_hash,
            )
            now = _now()
            result = session.exec(
                update(PodcastArtifactRecord)
                .where(
                    PodcastArtifactRecord.id == artifact_id,
                    PodcastArtifactRecord.status == "ready",
                    PodcastArtifactRecord.updated_at == expected_updated_at,
                )
                .values(status="published", published_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                raise PodcastArtifactConflict("Podcast artifact 已被其他操作更新")
            session.commit()
            published = session.get(PodcastArtifactRecord, artifact_id)
            if published is None:  # pragma: no cover - protected by update above
                raise PodcastArtifactNotFound("Podcast artifact 不存在")
            return published

    def withdraw(self, artifact_id: str) -> PodcastArtifactRecord:
        with Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(PodcastArtifactRecord, artifact_id)
            if record is None:
                raise PodcastArtifactNotFound("Podcast artifact 不存在")
            if record.status == "withdrawn":
                return record
            if record.status not in {"ready", "published"}:
                raise PodcastArtifactConflict("无效的撤下状态")
            now = _now()
            result = session.exec(
                update(PodcastArtifactRecord)
                .where(
                    PodcastArtifactRecord.id == artifact_id,
                    PodcastArtifactRecord.status == record.status,
                    PodcastArtifactRecord.updated_at == record.updated_at,
                )
                .values(status="withdrawn", withdrawn_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
                raise PodcastArtifactConflict("Podcast artifact 已被其他操作更新")
            session.commit()
            withdrawn = session.get(PodcastArtifactRecord, artifact_id)
            if withdrawn is None:  # pragma: no cover - protected by update above
                raise PodcastArtifactNotFound("Podcast artifact 不存在")
            return withdrawn

    def delete(self, artifact_id: str) -> bool:
        """Delete registry state only; physical bytes are reclaimed after a grace period."""
        with self._cas_lock(), Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            statement = select(PodcastArtifactRecord).where(
                PodcastArtifactRecord.id == artifact_id
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update()
            record = session.exec(statement).first()
            if record is None:
                raise PodcastArtifactNotFound("Podcast artifact 不存在")
            allowed = {"withdrawn", "expired"}
            if record.status not in allowed:
                raise PodcastArtifactConflict("必须先撤下或等待过期才能安全删除")
            active_reference = session.exec(
                select(PodcastProcessingRecord.id).where(
                    PodcastProcessingRecord.input_artifact_id == artifact_id,
                    PodcastProcessingRecord.processing_status.in_(
                        ACTIVE_PROCESSING_STATUSES
                    ),
                )
            ).first()
            if active_reference is not None:
                raise PodcastArtifactConflict("处理中任务仍在引用该缓存，不能删除")
            session.delete(record)
            session.commit()
        return False

    def reconcile_storage(self) -> dict[str, int]:
        """Reclaim only grace-expired unreferenced blobs and stale upload files."""

        deleted = deleted_bytes = 0
        deleted_staging = deleted_staging_bytes = 0
        expired_records = expired_protected = 0
        with self._cas_lock():
            stamp = _now()
            with Session(self.engine) as session:
                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                statement = select(PodcastArtifactRecord).where(
                    PodcastArtifactRecord.kind == "source_audio",
                    PodcastArtifactRecord.status != "expired",
                    PodcastArtifactRecord.expires_at <= stamp,
                )
                if self.engine.dialect.name == "postgresql":
                    statement = statement.with_for_update()
                candidates = list(session.exec(statement).all())
                refs = self._active_processing_reference_counts(
                    session,
                    (row.id for row in candidates),
                )
                for row in candidates:
                    if refs.get(row.id, 0):
                        expired_protected += 1
                        continue
                    row.status = "expired"
                    row.expired_at = stamp
                    row.updated_at = stamp
                    session.add(row)
                    expired_records += 1
                session.commit()
            referenced = self._referenced_paths()
            now = dt.datetime.now(dt.timezone.utc).timestamp()
            for path in self._blob_files():
                if path in referenced:
                    continue
                stat = path.stat()
                if now - stat.st_mtime < self.orphan_grace_seconds:
                    continue
                path.unlink(missing_ok=True)
                deleted += 1
                deleted_bytes += stat.st_size
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            now = dt.datetime.now(dt.timezone.utc).timestamp()
            for path in self._staging_files():
                try:
                    stat = path.stat()
                    if now - stat.st_mtime < self.staging_ttl_seconds:
                        continue
                    fd = os.open(path, os.O_RDWR)
                except FileNotFoundError:
                    continue
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    path.unlink(missing_ok=True)
                    deleted_staging += 1
                    deleted_staging_bytes += stat.st_size
                finally:
                    os.close(fd)
            for path, _kind, _reserved in self._download_reservations():
                try:
                    stat = path.stat()
                    if now - stat.st_mtime < self.staging_ttl_seconds:
                        continue
                    fd = os.open(path, os.O_RDWR)
                except FileNotFoundError:
                    continue
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    path.unlink(missing_ok=True)
                    deleted_staging += 1
                    deleted_staging_bytes += stat.st_size
                finally:
                    os.close(fd)
            with Session(self.engine) as session:
                setting = session.get(AppSettingRecord, LAST_RECONCILED_SETTING)
                if setting is None:
                    setting = AppSettingRecord(
                        key=LAST_RECONCILED_SETTING, value=stamp
                    )
                else:
                    setting.value = stamp
                session.add(setting)
                session.commit()
        return {
            "expired_source_records": expired_records,
            "expired_protected": expired_protected,
            "deleted_orphan_blobs": deleted,
            "deleted_bytes": deleted_bytes,
            "deleted_staging_files": deleted_staging,
            "deleted_staging_bytes": deleted_staging_bytes,
        }

    def reconcile_orphans(self) -> dict[str, int]:
        """Backward-compatible name for callers predating staging cleanup."""

        return self.reconcile_storage()
