"""Consistent SQLite, paid-speech and local-blob backups with safe offline restore.

No cloud deletion and no credentials in reports. OSS media already registered in
the snapshot is an explicit external dependency, not silently omitted content.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
import uuid

from sqlalchemy.engine import make_url

from config_backup import BackupConfig


class BackupError(OSError):
    """Only fixed, non-secret error codes may leave this module."""


def _epoch(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


def _hash(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _write_json(path, value):
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _open_source(source, root):
    """Walk beneath the configured root via directory FDs; never follow child links."""
    try:
        relative = Path(source).absolute().relative_to(Path(root).absolute())
    except ValueError:
        raise BackupError("backup_source_outside_root") from None
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise BackupError("backup_source_outside_root")
    directory = os.open(Path(root).resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError:
        raise BackupError("backup_source_path_unavailable") from None
    finally:
        os.close(directory)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("backup_busy") from None
        yield


def _connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _has_table(connection, table):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok":
        raise BackupError("backup_database_corrupt")


def _safe_name(name):
    if name == "database.sqlite3":
        return True
    if re.fullmatch(r"receipts/[0-9a-f]{64}\.(?:json|wav)", name):
        return True
    match = re.fullmatch(r"(?:media|podcast)/([0-9a-f]{2})/([0-9a-f]{64})\.[a-z0-9]{1,8}", name)
    return bool(match and match[1] == match[2][:2])


def _inspect_restore(archive, target, *, expected_sha256, max_bytes):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or "") or _hash(archive)[0] != expected_sha256:
        raise BackupError("backup_archive_checksum_mismatch")
    with tarfile.open(archive, "r:gz") as tar:
        first = tar.next()
        if not first or first.name != "manifest.json" or not first.isfile() or first.size > 8 * 1024 * 1024:
            raise BackupError("backup_manifest_invalid")
        manifest = json.load(tar.extractfile(first))
        if manifest.get("format") != "dorami-backup-v1" or not isinstance(manifest.get("files"), dict):
            raise BackupError("backup_manifest_invalid")
        files = manifest["files"]
        if "database.sqlite3" not in files or any(not _safe_name(name) for name in files):
            raise BackupError("backup_manifest_invalid")
        total = 0
        for value in files.values():
            if (not isinstance(value, dict) or type(value.get("size")) is not int or value["size"] < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", value.get("sha256", ""))):
                raise BackupError("backup_manifest_invalid")
            total += value["size"]
        if total > max_bytes or shutil.disk_usage(target).free < total:
            raise BackupError("backup_restore_size_limit")
        seen = set()
        while member := tar.next():
            if (not member.isfile() or member.name not in files or member.name in seen
                    or member.size != files[member.name]["size"]):
                raise BackupError("backup_archive_entry_invalid")
            seen.add(member.name)
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256()
            with tar.extractfile(member) as source, path.open("xb") as output:
                os.chmod(path, 0o600)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    output.write(block)
            if digest.hexdigest() != files[member.name]["sha256"] or path.stat().st_size != member.size:
                raise BackupError("backup_file_checksum_mismatch")
        if seen != set(files):
            raise BackupError("backup_archive_incomplete")
    with _connect(target / "database.sqlite3") as database:
        _integrity(database)
    _write_json(target / "manifest.json", manifest)
    return manifest


def restore_backup(archive, target, *, expected_sha256, offline=False, max_bytes=100 * 1024**3):
    """Restore into a new/empty directory; never overwrite a live data directory."""
    if not offline:
        raise BackupError("backup_restore_requires_offline")
    target = Path(target).absolute()
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise BackupError("backup_restore_target_not_empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dorami-restore-", dir=target.parent) as staging:
        manifest = _inspect_restore(Path(archive), Path(staging), expected_sha256=expected_sha256, max_bytes=max_bytes)
        # rename into an empty directory is atomic; a concurrently populated target fails.
        os.replace(staging, target)
    return {"status": "restored", "files": len(manifest["files"]),
            "external_objects": len(manifest.get("external_objects", []))}


class BackupService:
    def __init__(self, config: BackupConfig, database_url: str, receipt_root, *,
                 media_root, podcast_root, object_stores=None, bucket_factory=None, clock=time.time):
        self.config = config
        self.database_url = database_url
        self.receipt_root = Path(receipt_root)
        self.roots = {"media": Path(media_root), "podcast": Path(podcast_root)}
        self.object_stores = object_stores or {}
        self.bucket_factory = bucket_factory
        self.clock = clock
        self.directory = Path(config.local_dir)
        self._memory_state = None

    def status(self):
        result = {"enabled": self.config.enabled, "destination": self.config.destination,
                  "interval_hours": self.config.interval_hours, "status": "disabled" if not self.config.enabled else "pending"}
        if self.config.enabled:
            allowed = {"status", "last_attempt_at", "last_success_at", "error", "archive", "sha256",
                       "size_bytes", "files", "external_objects", "object_key", "pending_upload", "snapshot_at"}
            try:
                saved = json.loads((self.directory / "status.json").read_text())
                result.update({key: value for key, value in saved.items() if key in allowed})
            except (OSError, ValueError, AttributeError):
                pass
            if self._memory_state and _epoch(self._memory_state.get("last_attempt_at")) >= _epoch(result.get("last_attempt_at")):
                result.update({key: value for key, value in self._memory_state.items() if key in allowed})
        for field in ("last_success_at", "last_attempt_at", "snapshot_at"):
            value = _epoch(result.get(field))
            result[field] = dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat() if value else None
        result.update(last_error=result.get("error", ""), running=result["status"] == "running",
                      last_size_bytes=result.get("size_bytes", 0))
        return result

    def run_if_due(self):
        if not self.config.enabled:
            return self.status()
        state = self.status()
        now = self.clock()
        # Retry failures/busy spools after ten minutes; successes respect the schedule.
        if now - _epoch(state.get("last_success_at")) < self.config.interval_hours * 3600:
            return state
        if state["status"] in {"failed", "busy", "running"} and now - _epoch(state.get("last_attempt_at")) < 600:
            return state
        return self.run(due_only=True)

    def _database(self):
        url = make_url(self.database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise BackupError("backup_requires_file_sqlite")
        path = Path(url.database).resolve()
        if not path.is_file():
            raise BackupError("backup_database_missing")
        return path

    def _receipt_directory(self, connection):
        root = self.receipt_root
        if _has_table(connection, "app_settings"):
            row = connection.execute("SELECT value FROM app_settings WHERE key=?", ("bailian_speech_tts_receipt_root",)).fetchone()
            if row and str(row[0]).strip():
                root = Path(str(row[0]).strip())
        return root.resolve()

    def _copy(self, source, target, files, name, *, root, digest=None, size=None):
        fd = _open_source(source, root)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BackupError("backup_source_not_regular")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            expected_size = os.fstat(stream.fileno()).st_size
            if shutil.disk_usage(target.parent).free - expected_size < self.config.minimum_free_mb * 1024**2:
                raise BackupError("backup_insufficient_space")
            with target.open("xb") as output:
                os.chmod(target, 0o600)
                shutil.copyfileobj(stream, output, 1024 * 1024)
        actual_digest, actual_size = _hash(target)
        if ((digest is not None and digest != actual_digest) or (size is not None and size != actual_size)):
            raise BackupError("backup_source_checksum_mismatch")
        files[name] = {"sha256": actual_digest, "size": actual_size}

    def _snapshot(self, staging):
        source_path = self._database()
        files = {}
        with _connect(source_path) as source:
            receipt_root = self._receipt_directory(source)
        receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _lock(receipt_root / ".lock"):
            with _connect(source_path) as source, sqlite3.connect(staging / "database.sqlite3") as target:
                if shutil.disk_usage(staging).free - source_path.stat().st_size * 2 < self.config.minimum_free_mb * 1024**2:
                    raise BackupError("backup_insufficient_space")
                deadline = time.monotonic() + self.config.timeout_seconds
                def progress(_status, _remaining, _total):
                    if time.monotonic() > deadline:
                        raise BackupError("backup_snapshot_timeout")
                source.backup(target, pages=256, sleep=0.01, progress=progress)
            os.chmod(staging / "database.sqlite3", 0o600)
            with _connect(staging / "database.sqlite3") as snapshot:
                _integrity(snapshot)
                if self._receipt_directory(snapshot) != receipt_root:
                    raise BackupError("backup_receipt_root_changed")
                for path in sorted(receipt_root.iterdir()):
                    if re.fullmatch(r"[0-9a-f]{64}\.(json|wav)", path.name):
                        name = "receipts/" + path.name
                        self._copy(path, staging / name, files, name, root=receipt_root)
                if _has_table(snapshot, "bailian_tts_calls"):
                    for row in snapshot.execute("SELECT id,status,audio_hash FROM bailian_tts_calls"):
                        if row["status"] in {"generated", "succeeded"} and f"receipts/{row['id']}.json" not in files:
                            raise BackupError("backup_paid_receipt_missing")
                        if row["status"] == "succeeded":
                            audio = files.get(f"receipts/{row['id']}.wav")
                            if not audio or audio["sha256"] != row["audio_hash"]:
                                raise BackupError("backup_paid_audio_missing_or_corrupt")
        checksum, size = _hash(staging / "database.sqlite3")
        files["database.sqlite3"] = {"sha256": checksum, "size": size}
        external = []
        with _connect(staging / "database.sqlite3") as snapshot:
            for namespace, table in (("media", "media_assets"), ("podcast", "podcast_artifacts")):
                if not _has_table(snapshot, table):
                    continue
                query = f"SELECT DISTINCT content_hash,ext,size_bytes FROM {table}"
                if namespace == "media":
                    query += " WHERE status='cached'"
                for row in snapshot.execute(query):
                    digest, ext, size = row
                    name = f"{namespace}/{str(digest)[:2]}/{digest}{ext}"
                    if not _safe_name(name) or type(size) is not int or size < 0:
                        raise BackupError("backup_blob_identity_invalid")
                    remote = None
                    if _has_table(snapshot, "object_blobs"):
                        remote = snapshot.execute("SELECT * FROM object_blobs WHERE id=?", (f"{namespace}:{digest}{ext}",)).fetchone()
                    if remote:
                        if (remote["content_hash"] != digest or remote["size_bytes"] != size
                                or remote["namespace"] != namespace or remote["ext"] != ext):
                            raise BackupError("backup_blob_registry_conflict")
                        external.append(dict(remote))
                    else:
                        store = self.object_stores.get(namespace)
                        with store.pin(digest) if store is not None else nullcontext():
                            self._copy(self.roots[namespace] / name.split("/", 1)[1], staging / name,
                                       files, name, root=self.roots[namespace], digest=digest, size=size)
        return {"format": "dorami-backup-v1", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "files": files, "external_objects": external}

    def _bucket(self):
        if self.bucket_factory:
            return self.bucket_factory()
        import oss2
        if self.config.credential_provider == "ecs_role":
            from services.oss_credentials import EcsRoleCredentialsProvider
            provider = EcsRoleCredentialsProvider(self.config.ecs_role_name)
        else:
            provider = oss2.credentials.StaticCredentialsProvider(
                self.config.access_key_id, self.config.access_key_secret, self.config.security_token)
        session = oss2.Session()
        session.session.trust_env = False
        session.session.max_redirects = 0
        return oss2.Bucket(oss2.ProviderAuthV4(provider), self.config.endpoint, self.config.bucket,
                           region=self.config.region, session=session, connect_timeout=self.config.timeout_seconds)

    def _upload(self, path, checksum, size):
        if size > 5_000_000_000:
            raise BackupError("backup_requires_multipart_upload")
        key = self.config.prefix + "/" + path.name
        try:
            bucket = self._bucket()
            import oss2
            try:
                result = bucket.put_object_from_file(key, str(path), headers={
                    "Content-Type": "application/gzip", "x-oss-meta-sha256": checksum,
                    "x-oss-forbid-overwrite": "true"})
                if not 200 <= result.status < 300:
                    raise BackupError("backup_upload_failed")
            except oss2.exceptions.ObjectAlreadyExists:
                # A prior PUT may have succeeded even if its response was lost.
                pass
            head = bucket.head_object(key)
            headers = {key.lower(): value for key, value in head.headers.items()}
            if int(head.content_length) != size or headers.get("x-oss-meta-sha256") != checksum:
                raise BackupError("backup_upload_verification_failed")
        except BackupError:
            raise
        except Exception:
            raise BackupError("backup_upload_failed") from None
        return key

    def download(self, object_key, target, *, expected_sha256, max_bytes=100 * 1024**3):
        """Download a known backup key; no bucket listing, overwrite or cloud delete."""
        if not self.config.enabled or self.config.destination != "oss":
            raise BackupError("backup_oss_not_enabled")
        if not re.fullmatch(re.escape(self.config.prefix) + r"/dorami-\d{8}T\d{6}Z-[0-9a-f]{32}\.tar\.gz", object_key):
            raise BackupError("backup_object_key_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise BackupError("backup_checksum_required")
        target = Path(target).absolute()
        if target.exists() or target.is_symlink():
            raise BackupError("backup_download_target_exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            bucket = self._bucket()
            head = bucket.head_object(object_key)
            size = int(head.content_length)
            headers = {key.lower(): value for key, value in head.headers.items()}
            if headers.get("x-oss-meta-sha256") != expected_sha256:
                raise BackupError("backup_archive_checksum_mismatch")
            if size < 0 or size > max_bytes or shutil.disk_usage(target.parent).free - size < self.config.minimum_free_mb * 1024**2:
                raise BackupError("backup_restore_size_limit")
            fd, name = tempfile.mkstemp(prefix=".backup-download-", dir=target.parent)
            temporary = Path(name)
            response = bucket.get_object(object_key)
            received = 0
            try:
                with os.fdopen(fd, "wb") as output:
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        if received > size:
                            raise BackupError("backup_archive_checksum_mismatch")
                        output.write(block)
            finally:
                response.close()
            if _hash(temporary) != (expected_sha256, size):
                raise BackupError("backup_archive_checksum_mismatch")
            # Atomic hardlink creates a new name only; never overwrites a racing writer.
            os.link(temporary, target)
            return {"status": "downloaded", "sha256": expected_sha256, "size_bytes": size}
        except BackupError:
            raise
        except Exception:
            raise BackupError("backup_download_failed") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prune(self):
        # Only archives produced by this service; never delete arbitrary files or OSS.
        candidates = sorted((p for p in self.directory.glob("dorami-*.tar.gz")
                             if re.fullmatch(r"dorami-\d{8}T\d{6}Z-[0-9a-f]{32}\.tar\.gz", p.name)),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates[self.config.retain_local:]:
            path.unlink()
            path.with_name(path.name + ".sha256").unlink(missing_ok=True)

    def run(self, *, due_only=False):
        if not self.config.enabled:
            return self.status()
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with _lock(self.directory / ".backup.lock"):
                if due_only:
                    # Another process may have completed since run_if_due read state.
                    state = self.status()
                    if (self.clock() - _epoch(state.get("last_success_at")) < self.config.interval_hours * 3600
                            or (state["status"] in {"failed", "busy", "running"}
                                and self.clock() - _epoch(state.get("last_attempt_at")) < 600)):
                        return state
                return self._run_locked()
        except Exception as exc:
            return self._failure(exc)

    def _save_state(self, state):
        self._memory_state = dict(state)
        try:
            _write_json(self.directory / "status.json", state)
        except Exception:
            raise BackupError("backup_status_unavailable") from None

    def _failure(self, error):
        state = self.status()
        code = str(error) if isinstance(error, BackupError) else "backup_storage_unavailable"
        state.update(status="failed", error=code, last_attempt_at=self.clock())
        self._memory_state = state
        # State persistence itself may be the failing resource. Keep an in-memory
        # attempt timestamp and safe error so UI and retry backoff remain truthful.
        try:
            if code != "backup_busy":
                _write_json(self.directory / "status.json", state)
        except Exception:
            pass
        return self.status()

    def _run_locked(self):
        state = self.status()
        retry_upload = bool(state.get("pending_upload") and self.config.destination == "oss")
        state.update(status="running", last_attempt_at=self.clock(), error="")
        self._save_state(state)
        try:
            if retry_upload:
                name = state.get("archive", "")
                if not re.fullmatch(r"dorami-\d{8}T\d{6}Z-[0-9a-f]{32}\.tar\.gz", name):
                    raise BackupError("backup_pending_archive_invalid")
                archive = self.directory / name
                checksum, size = state.get("sha256"), state.get("size_bytes")
                if archive.is_symlink() or _hash(archive) != (checksum, size):
                    raise BackupError("backup_pending_archive_invalid")
            else:
                state["snapshot_at"] = self.clock()
                archive, checksum, size, manifest = self._create_archive()
                state.update(archive=archive.name, sha256=checksum, size_bytes=size,
                             files=len(manifest["files"]), external_objects=len(manifest["external_objects"]),
                             pending_upload=self.config.destination == "oss")
                # Record a complete pending archive before cloud I/O. Retries (and
                # restarts) reuse it, preserving the older successful restore points.
                self._save_state(state)
            if self.config.destination == "oss":
                state["object_key"] = self._upload(archive, checksum, size)
            state.update(status="succeeded", last_success_at=state["snapshot_at"], pending_upload=False)
            self._save_state(state)
            self._prune()
        except Exception as exc:
            self._memory_state = state
            return self._failure(exc)
        return self.status()

    def _create_archive(self):
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.directory / f"dorami-{stamp}-{uuid.uuid4().hex}.tar.gz"
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=self.directory) as staging:
            staging = Path(staging)
            manifest = self._snapshot(staging)
            total = sum(row["size"] for row in manifest["files"].values())
            if shutil.disk_usage(staging).free - total < self.config.minimum_free_mb * 1024**2:
                raise BackupError("backup_insufficient_space")
            partial = staging / "archive.part"
            with tarfile.open(partial, "w:gz") as tar:
                body = json.dumps(manifest, sort_keys=True).encode()
                if len(body) > 8 * 1024 * 1024:
                    raise BackupError("backup_manifest_too_large")
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mode = len(body), 0o600
                tar.addfile(info, io.BytesIO(body))
                for name in sorted(manifest["files"]):
                    tar.add(staging / name, arcname=name, recursive=False)
            os.chmod(partial, 0o600)
            with partial.open("rb") as durable:
                os.fsync(durable.fileno())
            checksum, size = _hash(partial)
            # Read every compressed byte before marking the local snapshot complete.
            with tarfile.open(partial, "r:gz") as tar:
                for member in tar:
                    if member.isfile():
                        digest = hashlib.sha256()
                        with tar.extractfile(member) as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if member.name != "manifest.json" and digest.hexdigest() != manifest["files"][member.name]["sha256"]:
                            raise BackupError("backup_archive_checksum_mismatch")
            os.replace(partial, archive)
            _sync_directory(self.directory)
        digest_path = archive.with_name(archive.name + ".sha256")
        with digest_path.open("x") as stream:
            os.chmod(digest_path, 0o600)
            stream.write(checksum + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return archive, checksum, size, manifest
