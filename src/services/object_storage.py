"""Immutable OSS objects with a verified local working copy.

The database stores object locations, not URLs or credentials. Uploads finish
before business metadata may become visible. Downloads use atomic replacement
and verify SHA-256, keeping FileResponse, ffprobe and image analysis compatible.
Online cache eviction shares process-independent leases with runtime readers.
Remote garbage collection remains an explicit offline operation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from services.file_lock import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock
import json
import time
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading

import oss2
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config_oss import OssConfig
from models.db import ObjectBlobRecord
from services.oss_credentials import EcsRoleCredentialsProvider


class ObjectStorageError(OSError):
    """Safe diagnostic: SDK errors may contain signed requests or credentials."""


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _cloud_operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            value = method(self, *args, **kwargs)
        except ObjectStorageError as exc:
            if self.enabled:
                self._state_update("storage_health", last_error=str(exc), last_error_at=self._now())
            raise
        return value
    return wrapped


class ObjectStorage:
    def __init__(self, engine, root: Path, namespace: str, config: OssConfig, *, bucket_factory=None):
        if namespace not in {"media", "podcast"}:
            raise ValueError("Unknown object namespace")
        self.engine = engine
        self.root = Path(root).resolve()
        self.namespace = namespace
        self.config = config
        self.enabled = config.backend(namespace) == "oss"
        self._bucket_factory = bucket_factory
        self._role_provider = EcsRoleCredentialsProvider(config.ecs_role_name)
        # Stripes bound memory and serialize same-content downloads in this process.
        self._locks = [threading.RLock() for _ in range(64)]

    @staticmethod
    def _now():
        return dt.datetime.now(dt.timezone.utc).isoformat()

    @contextmanager
    def pin(self, content_hash: str, *, exclusive=False, blocking=True):
        """Cross-process lease; hold until pathname consumers finish or open an fd.

        Fixed 256 stripes avoid unbounded lock files. Lock files are never removed:
        unlinking one would let a new worker lock a different inode.
        """
        if not self.enabled:
            yield True
            return
        self.identity(content_hash, ".bin")
        directory = self.root / ".oss-locks"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / content_hash[:2]).open("a+b") as handle:
            operation = LOCK_EX if exclusive else LOCK_SH
            try:
                flock(handle.fileno(), operation | (0 if blocking else LOCK_NB))
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                flock(handle.fileno(), LOCK_UN)

    def _state(self):
        try:
            state = json.loads((self.root / ".oss-status.json").read_text())
            return {key: value for key, value in state.items() if isinstance(value, dict)} if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _state_update(self, section, **values):
        # Diagnostics must never turn a successful storage operation into a failure.
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".oss-status.lock").open("a+b") as lock:
                flock(lock.fileno(), LOCK_EX)
                state = self._state()
                state.setdefault(section, {}).update(values)
                fd, name = tempfile.mkstemp(prefix=".oss-status-", dir=self.root)
                try:
                    with os.fdopen(fd, "w") as output:
                        json.dump(state, output)
                    os.replace(name, self.root / ".oss-status.json")
                finally:
                    Path(name).unlink(missing_ok=True)
        except OSError:
            pass

    def _healthy(self):
        self._state_update("storage_health", last_error=None, last_success_at=self._now())

    def local_bytes(self):
        total = 0
        for path in self.root.glob("[0-9a-f][0-9a-f]/*"):
            try:
                if path.is_file() and not path.is_symlink() and not path.name.endswith(".part"):
                    total += path.stat().st_size
            except OSError:
                # Best-effort occupancy; maintenance records inaccessible
                # candidates separately and still reclaims readable files.
                pass
        return total

    def identity(self, content_hash: str, ext: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash) or not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
            raise ObjectStorageError("object_storage_invalid_identity")
        return f"{self.namespace}:{content_hash}{ext}"

    def location(self, content_hash: str, ext: str) -> ObjectBlobRecord | None:
        identity = self.identity(content_hash, ext)
        with Session(self.engine) as session:
            return session.get(ObjectBlobRecord, identity)

    def _lock(self, content_hash: str):
        return self._locks[int(content_hash[:2], 16) % len(self._locks)]

    def _bucket(self, record: ObjectBlobRecord):
        if record.bucket != self.config.bucket or record.region != self.config.region:
            raise ObjectStorageError("object_storage_location_config_mismatch")
        if self.config.credential_provider == "static" and (not self.config.access_key_id or not self.config.access_key_secret):
            raise ObjectStorageError("object_storage_credentials_unavailable")
        if self._bucket_factory:
            return self._bucket_factory(record)
        provider = self._role_provider if self.config.credential_provider == "ecs_role" else oss2.credentials.StaticCredentialsProvider(
            self.config.access_key_id, self.config.access_key_secret, self.config.security_token,
        )
        session = oss2.Session()
        session.session.trust_env = False
        session.session.max_redirects = 0
        return oss2.Bucket(
            oss2.ProviderAuthV4(provider), self.config.endpoint, record.bucket,
            region=record.region, connect_timeout=self.config.timeout_seconds, session=session,
        )

    def _head(self, bucket, record: ObjectBlobRecord) -> bool:
        try:
            result = bucket.head_object(record.object_key)
        except oss2.exceptions.NoSuchKey:
            return False
        except Exception:
            raise ObjectStorageError("object_storage_head_failed") from None
        headers = {k.lower(): v for k, v in result.headers.items()}
        if (int(result.content_length) != record.size_bytes
                or headers.get("x-oss-meta-sha256") != record.content_hash):
            raise ObjectStorageError("object_storage_remote_identity_mismatch")
        return True

    @_cloud_operation
    def persist(self, path: Path, content_hash: str, ext: str, size: int, mime: str) -> None:
        """Write through to OSS. Safe to repeat after a process/DB failure."""
        if not self.enabled:
            return
        identity = self.identity(content_hash, ext)
        with self.pin(content_hash), self._lock(content_hash):
            if hash_file(path) != (content_hash, size):
                raise ObjectStorageError("object_storage_local_checksum_mismatch")
            record = self.location(content_hash, ext)
            if record is not None and (record.size_bytes != size or record.mime != mime):
                raise ObjectStorageError("object_storage_registry_conflict")
            record = record or ObjectBlobRecord(
                id=identity, namespace=self.namespace, content_hash=content_hash,
                ext=ext, size_bytes=size, mime=mime, bucket=self.config.bucket,
                region=self.config.region,
                object_key=f"{self.config.prefix}/{self.namespace}/{content_hash[:2]}/{content_hash}{ext}",
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
            bucket = self._bucket(record)
            if not self._head(bucket, record):
                try:
                    result = bucket.put_object_from_file(record.object_key, str(path), headers={
                        "Content-Type": mime,
                        "x-oss-meta-sha256": content_hash,
                        "x-oss-forbid-overwrite": "true",
                    })
                    if not 200 <= result.status < 300:
                        raise ObjectStorageError("object_storage_upload_failed")
                except oss2.exceptions.ObjectAlreadyExists:
                    pass  # Concurrent upload of the same immutable identity.
                except Exception:
                    raise ObjectStorageError("object_storage_upload_failed") from None
                if not self._head(bucket, record):
                    raise ObjectStorageError("object_storage_upload_not_visible")
            with Session(self.engine) as session:
                previous = session.get(ObjectBlobRecord, identity)
                if previous is None:
                    session.add(record)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        previous = session.get(ObjectBlobRecord, identity)
                        if previous is None or previous.object_key != record.object_key or previous.bucket != record.bucket:
                            raise ObjectStorageError("object_storage_registry_conflict") from None

            self._healthy()

    @_cloud_operation
    def materialize(self, path: Path, content_hash: str, ext: str, size: int) -> Path:
        """Restore a cold working copy. Never refetch a mutable origin URL."""
        self.identity(content_hash, ext)
        with self.pin(content_hash), self._lock(content_hash):
            if path.is_file() and path.stat().st_size == size:
                if self.enabled and time.time() - path.stat().st_mtime > 60:
                    os.utime(path, None)
                return path
            record = self.location(content_hash, ext)
            if record is None:
                return path
            if not self.enabled:
                raise ObjectStorageError("object_storage_local_copy_missing")
            if record.size_bytes != size:
                raise ObjectStorageError("object_storage_registry_conflict")
            path.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(path.parent).free - size < self.config.minimum_free_mb * 1024 * 1024:
                raise ObjectStorageError("object_storage_local_disk_full")
            fd, filename = tempfile.mkstemp(prefix=".oss-", suffix=".part", dir=path.parent)
            temporary = Path(filename)
            try:
                digest = hashlib.sha256()
                received = 0
                try:
                    response = self._bucket(record).get_object(record.object_key)
                    try:
                        with os.fdopen(fd, "wb") as output:
                            fd = -1
                            while True:
                                chunk = response.read(1024 * 1024)
                                if not chunk:
                                    break
                                received += len(chunk)
                                if received > size:
                                    raise ObjectStorageError("object_storage_download_too_large")
                                digest.update(chunk)
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                    finally:
                        response.close()
                except ObjectStorageError:
                    raise
                except Exception:
                    raise ObjectStorageError("object_storage_download_failed") from None
                if received != size or digest.hexdigest() != content_hash:
                    raise ObjectStorageError("object_storage_download_checksum_mismatch")
                os.replace(temporary, path)
            finally:
                if fd >= 0:
                    os.close(fd)
                temporary.unlink(missing_ok=True)
            self._healthy()
            return path

    def stats(self) -> dict:
        with Session(self.engine) as session:
            rows = session.exec(select(ObjectBlobRecord).where(ObjectBlobRecord.namespace == self.namespace)).all()
        state = self._state()
        return {
            "cache": {**state.get("cache", {}), "enabled": self.enabled and self.config.cache_enabled,
                      "max_bytes": getattr(self.config, f"{self.namespace}_cache_max_mb") * 1024 * 1024,
                      "local_bytes": self.local_bytes()},
            "storage_health": state.get("storage_health", {}),
            "storage_backend": self.config.backend(self.namespace),
            "remote_objects": len(rows),
            "remote_bytes": sum(row.size_bytes for row in rows),
        }

    @_cloud_operation
    def verify_remote(self, record: ObjectBlobRecord) -> None:
        """Read every remote byte before permitting local eviction or reporting recovery success."""
        try:
            bucket = self._bucket(record)
            if not self._head(bucket, record):
                raise ObjectStorageError("object_storage_remote_missing")
            response = bucket.get_object(record.object_key)
            size, digest = 0, hashlib.sha256()
            try:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > record.size_bytes:
                        raise ObjectStorageError("object_storage_download_too_large")
                    digest.update(chunk)
            finally:
                response.close()
            if size != record.size_bytes or digest.hexdigest() != record.content_hash:
                raise ObjectStorageError("object_storage_download_checksum_mismatch")
            self._healthy()
        except ObjectStorageError:
            raise
        except Exception:
            raise ObjectStorageError("object_storage_verify_failed") from None

    def evict_cache(self):
        """Best-effort target, never a quota: active/new/unverified files stay local."""
        if not self.enabled or not self.config.cache_enabled:
            return {"evicted_files": 0, "evicted_bytes": 0, "skipped": 0}
        result = {"evicted_files": 0, "evicted_bytes": 0, "skipped": 0}
        limit = getattr(self.config, f"{self.namespace}_cache_max_mb") * 1024 * 1024
        total = self.local_bytes()
        with Session(self.engine) as session:
            rows = session.exec(select(ObjectBlobRecord).where(ObjectBlobRecord.namespace == self.namespace)).all()

        def file_stat(path):
            try:
                value = path.lstat()
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(value.st_mode) or not path.resolve().is_relative_to(self.root):
                return None
            return value

        def unchanged(before, after):
            return before is not None and after is not None and (
                before.st_ino, before.st_mtime_ns, before.st_size
            ) == (after.st_ino, after.st_mtime_ns, after.st_size)

        candidates = []
        last_error = None
        for row in rows:
            try:
                self.identity(row.content_hash, row.ext)
                path = self.root / row.content_hash[:2] / f"{row.content_hash}{row.ext}"
                # A non-media suffix keeps diagnostic copies out of Podcast's
                # blob quota and orphan GC, while local_bytes still counts them.
                quarantine = path.with_name(path.name + ".corrupt")
                before, quarantined = file_stat(path), file_stat(quarantine)
                existing = [value for value in (before, quarantined) if value is not None]
                if existing:
                    candidates.append((min(value.st_mtime for value in existing), row, path, quarantine,
                                       quarantined is not None))
            except OSError:
                last_error = "object_storage_cache_failed"
                continue
        for _, row, path, quarantine, had_quarantine in sorted(candidates, key=lambda value: value[0]):
            # Quarantine cleanup must also run below the target and when the
            # canonical path is absent or freshly restored; otherwise it leaks.
            if total <= limit and not had_quarantine:
                continue
            try:
                # Snapshot under a short exclusive lease, verify remotely with
                # no reader blocked, then recheck identity/access time to evict.
                with self.pin(row.content_hash, exclusive=True, blocking=False) as acquired:
                    if not acquired:
                        result["skipped"] += 1
                        continue
                    before, quarantined = file_stat(path), file_stat(quarantine)
                    evict = (total > limit and before is not None
                             and time.time() - before.st_mtime >= self.config.cache_min_age_seconds)
                    if evict and hash_file(path) != (row.content_hash, row.size_bytes):
                        # Never upgrade a reader's shared lease. Only this EX
                        # maintenance path isolates known-bad bytes, atomically
                        # replacing at most one diagnostic copy per identity.
                        os.replace(path, quarantine)
                        last_error = "object_storage_local_checksum_mismatch"
                        continue  # Renaming is not reclaimed space.
                    if not evict and quarantined is None:
                        if before is not None and total > limit:
                            result["skipped"] += 1
                        continue
            except OSError as exc:
                last_error = str(exc) if isinstance(exc, ObjectStorageError) else "object_storage_cache_failed"
                continue  # One unreadable local file must not pin the whole cache.
            try:
                self.verify_remote(row)
            except OSError as exc:
                last_error = str(exc) if isinstance(exc, ObjectStorageError) else "object_storage_cache_failed"
                # Outages must not turn into a request storm over the entire cache.
                break
            try:
                with self.pin(row.content_hash, exclusive=True, blocking=False) as acquired:
                    if not acquired:
                        result["skipped"] += 1
                        continue
                    for candidate, snapshot in ((path, before if evict else None), (quarantine, quarantined)):
                        if unchanged(snapshot, file_stat(candidate)):
                            candidate.unlink()
                            total -= snapshot.st_size
                            result["evicted_files"] += 1
                            result["evicted_bytes"] += snapshot.st_size
            except OSError as exc:
                last_error = str(exc) if isinstance(exc, ObjectStorageError) else "object_storage_cache_failed"
                continue
        self._state_update("cache", **result, last_run_at=self._now(), last_error=last_error)
        return result

    def durable_bytes(self, paths) -> int:
        with Session(self.engine) as session:
            rows = session.exec(select(ObjectBlobRecord).where(ObjectBlobRecord.namespace == self.namespace)).all()
        objects = {f"{row.content_hash}{row.ext}": row.size_bytes for row in rows}
        for path in paths:
            try:
                objects[path.name] = max(objects.get(path.name, 0), path.stat().st_size)
            except FileNotFoundError:
                pass
        return sum(objects.values())
