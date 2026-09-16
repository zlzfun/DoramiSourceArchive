"""Immutable OSS objects with a verified local working copy.

The database stores object locations, not URLs or credentials. Uploads finish
before business metadata may become visible. Downloads use atomic replacement
and verify SHA-256, keeping FileResponse, ffprobe and image analysis compatible.
Cache eviction and remote garbage collection are explicit offline operations:
runtime readers must never lose a pathname before opening it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import shutil
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
        return oss2.Bucket(
            oss2.ProviderAuthV4(provider), self.config.endpoint, record.bucket,
            region=record.region, connect_timeout=self.config.timeout_seconds,
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

    def persist(self, path: Path, content_hash: str, ext: str, size: int, mime: str) -> None:
        """Write through to OSS. Safe to repeat after a process/DB failure."""
        if not self.enabled:
            return
        identity = self.identity(content_hash, ext)
        with self._lock(content_hash):
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

    def materialize(self, path: Path, content_hash: str, ext: str, size: int) -> Path:
        """Restore a cold working copy. Never refetch a mutable origin URL."""
        self.identity(content_hash, ext)
        with self._lock(content_hash):
            if path.is_file() and path.stat().st_size == size:
                return path
            record = self.location(content_hash, ext)
            if record is None:
                return path
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
            return path

    def stats(self) -> dict:
        with Session(self.engine) as session:
            rows = session.exec(select(ObjectBlobRecord).where(ObjectBlobRecord.namespace == self.namespace)).all()
        return {
            "storage_backend": self.config.backend(self.namespace),
            "remote_objects": len(rows),
            "remote_bytes": sum(row.size_bytes for row in rows),
        }

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
        except ObjectStorageError:
            raise
        except Exception:
            raise ObjectStorageError("object_storage_verify_failed") from None

    def durable_bytes(self, paths) -> int:
        with Session(self.engine) as session:
            rows = session.exec(select(ObjectBlobRecord).where(ObjectBlobRecord.namespace == self.namespace)).all()
        objects = {f"{row.content_hash}{row.ext}": row.size_bytes for row in rows}
        for path in paths:
            objects[path.name] = max(objects.get(path.name, 0), path.stat().st_size)
        return sum(objects.values())
