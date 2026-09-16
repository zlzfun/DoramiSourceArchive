"""Actual SQLite/tar restore drills plus cloud and hostile archive boundaries."""
import configparser
import datetime as dt
from dataclasses import replace
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from config_backup import BackupConfig, load_backup_config
from services.storage_backup import BackupError, BackupService, restore_backup
import services.storage_backup as backup_module


@pytest.fixture
def service(tmp_path):
    database = tmp_path / "live.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE articles (id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE bailian_tts_calls (id TEXT PRIMARY KEY, status TEXT, audio_hash TEXT);
            CREATE TABLE media_assets (content_hash TEXT, ext TEXT, size_bytes INTEGER, status TEXT);
            CREATE TABLE podcast_artifacts (content_hash TEXT, ext TEXT, size_bytes INTEGER);
            CREATE TABLE object_blobs (id TEXT PRIMARY KEY, namespace TEXT, content_hash TEXT,
                ext TEXT, size_bytes INTEGER, bucket TEXT, region TEXT, object_key TEXT);
            INSERT INTO articles VALUES ('retained', 'private article');
        """)
    return BackupService(BackupConfig(enabled=True, local_dir=str(tmp_path / "backups"), minimum_free_mb=0),
                         f"sqlite:///{database}", tmp_path / "receipts",
                         media_root=tmp_path / "media", podcast_root=tmp_path / "podcast")


def add_local(service, namespace, data, ext):
    digest = hashlib.sha256(data).hexdigest()
    path = service.roots[namespace] / digest[:2] / (digest + ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with sqlite3.connect(service._database()) as connection:
        if namespace == "media":
            connection.execute("INSERT INTO media_assets VALUES (?,?,?,'cached')", (digest, ext, len(data)))
        else:
            connection.execute("INSERT INTO podcast_artifacts VALUES (?,?,?)", (digest, ext, len(data)))
    return path, digest


def test_disabled_config_is_isolated_and_secrets_never_from_ini(monkeypatch):
    parser = configparser.ConfigParser()
    parser.read_string("[backup]\nenabled=false\ninterval_hours=bad\ndestination=nonsense\n")
    assert load_backup_config(parser) == BackupConfig()
    parser.read_string("[backup]\nenabled=true\ndestination=local\ninterval_hours=1\naccess_key_secret=DO-NOT-USE\n")
    assert load_backup_config(parser).access_key_secret == ""
    monkeypatch.setenv("DORAMI_BACKUP_ACCESS_KEY_SECRET", "env-secret")
    assert "env-secret" not in repr(load_backup_config(parser))
    config = BackupConfig(enabled=True, destination="oss", bucket="test-backup", region="ap-southeast-1",
                          endpoint="https://oss-ap-southeast-1-internal.aliyuncs.com",
                          credential_provider="ecs_role", ecs_role_name="BackupRole")
    assert config.enabled
    with pytest.raises(ValueError):
        replace(config, prefix="prod/media")


def test_round_trip_wal_database_receipts_paid_audio_local_blobs(service, tmp_path):
    # Keep a WAL-writing connection live during the online snapshot.
    database = sqlite3.connect(service._database())
    database.execute("INSERT INTO articles VALUES ('wal-only', 'latest committed row')")
    data = b"already-paid audio"
    call_id = "a" * 64
    audio_hash = hashlib.sha256(data).hexdigest()
    database.execute("INSERT INTO bailian_tts_calls VALUES (?,?,?)", (call_id, "succeeded", audio_hash))
    database.execute("INSERT INTO bailian_tts_calls VALUES (?,?,?)", ("b" * 64, "authorized", ""))
    database.commit()
    service.receipt_root.mkdir()
    (service.receipt_root / f"{call_id}.json").write_text('{"private":"receipt"}')
    (service.receipt_root / f"{call_id}.wav").write_bytes(data)
    image, image_hash = add_local(service, "media", b"original image", ".png")
    audio, audio_digest = add_local(service, "podcast", b"published audio", ".mp3")
    state = service.run()
    database.close()
    assert state["status"] == "succeeded", state
    assert state["external_objects"] == 0
    archive = service.directory / state["archive"]
    assert archive.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / "restored"
    result = restore_backup(archive, restored, expected_sha256=state["sha256"], offline=True)
    assert result == {"status": "restored", "files": 5, "external_objects": 0}
    with sqlite3.connect(restored / "database.sqlite3") as connection:
        assert connection.execute("SELECT body FROM articles WHERE id='wal-only'").fetchone()[0] == "latest committed row"
        assert connection.execute("SELECT status FROM bailian_tts_calls WHERE id=?", ("b" * 64,)).fetchone()[0] == "authorized"
    assert (restored / "receipts" / f"{call_id}.wav").read_bytes() == data
    assert (restored / "media" / image_hash[:2] / image.name).read_bytes() == image.read_bytes()
    assert (restored / "podcast" / audio_digest[:2] / audio.name).read_bytes() == audio.read_bytes()
    assert not (restored / "receipts" / ".lock").exists()


def test_runtime_receipt_root_override_and_busy_spool(service, tmp_path):
    root = tmp_path / "runtime-root"
    root.mkdir()
    with sqlite3.connect(service._database()) as connection:
        connection.execute("INSERT INTO app_settings VALUES (?,?)", ("bailian_speech_tts_receipt_root", str(root)))
    receipt = root / ("c" * 64 + ".json")
    receipt.write_text('{"paid":"response"}')
    with (root / ".lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = service.run()
        assert state["status"] == "failed"
        assert state["error"] == "backup_busy"
    state = service.run()
    assert state["status"] == "succeeded"
    with tarfile.open(service.directory / state["archive"]) as archive:
        assert f"receipts/{receipt.name}" in archive.getnames()


def test_incomplete_local_or_paid_data_never_reports_success(service):
    image, _ = add_local(service, "media", b"missing later", ".png")
    image.unlink()
    assert service.run()["status"] == "failed"
    with sqlite3.connect(service._database()) as connection:
        connection.execute("DELETE FROM media_assets")
        connection.execute("INSERT INTO bailian_tts_calls VALUES (?,?,?)", ("d" * 64, "succeeded", "e" * 64))
    state = service.run()
    assert state["status"] == "failed"
    assert state["error"] == "backup_paid_receipt_missing"
    assert not list(service.directory.glob("*.tar.gz"))


def test_remote_blobs_are_explicit_restore_dependencies(service, tmp_path):
    image, digest = add_local(service, "media", b"cold object", ".png")
    with sqlite3.connect(service._database()) as connection:
        connection.execute("INSERT INTO object_blobs VALUES (?,?,?,?,?,?,?,?)",
                           (f"media:{digest}.png", "media", digest, ".png", image.stat().st_size,
                            "private-bucket", "ap-southeast-1", "prod/media/" + digest))
    image.unlink()
    state = service.run()
    assert state["status"] == "succeeded" and state["external_objects"] == 1
    result = restore_backup(service.directory / state["archive"], tmp_path / "restore",
                            expected_sha256=state["sha256"], offline=True)
    assert result["external_objects"] == 1
    manifest = json.loads((tmp_path / "restore" / "manifest.json").read_text())
    assert manifest["external_objects"][0]["content_hash"] == digest


def test_restore_requires_offline_empty_destination_checksum_and_size(service, tmp_path):
    state = service.run()
    archive = service.directory / state["archive"]
    target = tmp_path / "restore"
    with pytest.raises(BackupError, match="requires_offline"):
        restore_backup(archive, target, expected_sha256=state["sha256"])
    with pytest.raises(BackupError, match="checksum"):
        restore_backup(archive, target, expected_sha256="0" * 64, offline=True)
    with pytest.raises(BackupError, match="size_limit"):
        restore_backup(archive, target, expected_sha256=state["sha256"], offline=True, max_bytes=1)
    assert not target.exists()
    target.mkdir()
    (target / "keep").write_text("untouched")
    with pytest.raises(BackupError, match="not_empty"):
        restore_backup(archive, target, expected_sha256=state["sha256"], offline=True)
    assert (target / "keep").read_text() == "untouched"


@pytest.mark.parametrize("attack", ["traversal", "symlink", "duplicate", "bad_hash", "missing"])
def test_hostile_archives_leave_destination_untouched(service, tmp_path, attack):
    content = service._database().read_bytes()
    manifest = {"format": "dorami-backup-v1", "files": {
        "database.sqlite3": {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}}, "external_objects": []}
    if attack == "bad_hash":
        manifest["files"]["database.sqlite3"]["sha256"] = "0" * 64
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = json.dumps(manifest).encode()
        item = tarfile.TarInfo("manifest.json")
        item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
        if attack != "missing":
            item = tarfile.TarInfo("../outside" if attack == "traversal" else "database.sqlite3")
            item.size = len(content)
            if attack == "symlink":
                item.type, item.linkname, item.size = tarfile.SYMTYPE, "../outside", 0
            tar.addfile(item, io.BytesIO(content))
            if attack == "duplicate":
                tar.addfile(item, io.BytesIO(content))
    target = tmp_path / "restored"
    with pytest.raises(BackupError):
        restore_backup(archive, target, expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), offline=True)
    assert not target.exists() and not (tmp_path / "outside").exists()


class Bucket:
    def __init__(self):
        self.items = {}
        self.fail = False

    def put_object_from_file(self, key, path, headers):
        if self.fail:
            raise RuntimeError("must-not-log-secret")
        self.items[key] = (Path(path).read_bytes(), headers)
        return SimpleNamespace(status=200)

    def head_object(self, key):
        body, headers = self.items[key]
        return SimpleNamespace(content_length=len(body), headers=headers)

    def get_object(self, key):
        return io.BytesIO(self.items[key][0])


def test_oss_round_trip_failure_redaction_and_separate_credentials(service, tmp_path):
    bucket = Bucket()
    service.config = replace(service.config, destination="oss", bucket="backup-bucket", region="ap-southeast-1",
                             endpoint="https://oss-ap-southeast-1-internal.aliyuncs.com",
                             credential_provider="ecs_role", ecs_role_name="BackupRole")
    service.bucket_factory = lambda: bucket
    state = service.run()
    assert state["status"] == "succeeded"
    assert state["object_key"].startswith("backups/")
    download = tmp_path / "downloaded.tar.gz"
    service.download(state["object_key"], download, expected_sha256=state["sha256"])
    result = restore_backup(download, tmp_path / "restored", expected_sha256=state["sha256"], offline=True)
    assert result["status"] == "restored"
    with pytest.raises(BackupError, match="exists"):
        service.download(state["object_key"], download, expected_sha256=state["sha256"])
    bucket.fail = True
    state = service.run()
    assert state["status"] == "failed" and state["error"] == "backup_upload_failed"
    assert "must-not-log-secret" not in json.dumps(state)
    assert (service.directory / state["archive"]).is_file()
    with pytest.raises(BackupError, match="requires_multipart"):
        service._upload(Path("not-read"), "0" * 64, 5_000_000_001)


def test_schedule_retention_and_disabled_no_files(service, tmp_path):
    now = [1_800_000_000]
    service.clock = lambda: now[0]
    service.config = replace(service.config, retain_local=2)
    for _ in range(3):
        assert service.run_if_due()["status"] == "succeeded"
        now[0] += 24 * 3600 + 1
    assert len(list(service.directory.glob("*.tar.gz"))) == 2
    assert len(list(service.directory.glob("*.sha256"))) == 2
    count = len(list(service.directory.glob("*.tar.gz")))
    now[0] -= 24 * 3600
    assert service.run_if_due()["status"] == "succeeded"
    assert len(list(service.directory.glob("*.tar.gz"))) == count
    service.config = BackupConfig(local_dir=str(tmp_path / "disabled"))
    service.directory = Path(service.config.local_dir)
    assert service.run()["status"] == "disabled"
    assert not service.directory.exists()


def test_cli_verify_and_restore_without_loading_application_or_cloud_config(service, tmp_path):
    state = service.run()
    archive = service.directory / state["archive"]
    script = Path(__file__).resolve().parents[1] / "scripts" / "storage_backup.py"
    common = ["--archive", str(archive), "--sha256", state["sha256"]]
    verified = subprocess.run([sys.executable, str(script), "verify", *common],
                              capture_output=True, text=True, check=True)
    assert json.loads(verified.stdout)["status"] == "verified"
    restored = subprocess.run([sys.executable, str(script), "restore", *common,
                               "--offline", "--target", str(tmp_path / "cli-restore")],
                              capture_output=True, text=True, check=True)
    assert json.loads(restored.stdout)["status"] == "restored"
    refused = subprocess.run([sys.executable, str(script), "restore", *common,
                              "--target", str(tmp_path / "not-created")], capture_output=True)
    assert refused.returncode != 0 and not (tmp_path / "not-created").exists()


def test_status_write_does_not_follow_fixed_temporary_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("must survive")
    directory = tmp_path / "backups"
    directory.mkdir()
    (directory / "status.json.tmp").symlink_to(outside)
    (directory / "status.json").symlink_to(outside)
    backup_module._write_json(directory / "status.json", {"status": "succeeded"})
    assert outside.read_text() == "must survive"
    assert not (directory / "status.json").is_symlink()
    assert json.loads((directory / "status.json").read_text()) == {"status": "succeeded"}
    assert not list(directory.glob(".status.json-*"))


def test_failed_upload_retries_preserve_daily_points_and_snapshot_age(service, monkeypatch):
    now = [1_800_000_000]
    service.clock = lambda: now[0]
    bucket = Bucket()
    service.config = replace(service.config, destination="oss", retain_local=2,
                             bucket="backup-bucket", region="ap-southeast-1",
                             endpoint="https://oss-ap-southeast-1-internal.aliyuncs.com",
                             credential_provider="ecs_role", ecs_role_name="BackupRole")
    service.bucket_factory = lambda: bucket
    originals = set()
    for _ in range(2):
        state = service.run_if_due()
        assert state["status"] == "succeeded"
        originals.add(state["archive"])
        now[0] += 24 * 3600 + 1
    bucket.fail = True
    first_failed = service.run_if_due()
    assert first_failed["pending_upload"] is True
    pending = first_failed["archive"]
    old_snapshot_time = first_failed["snapshot_at"]
    # Reconstruct the service to exercise persisted retry metadata after a restart.
    service = BackupService(service.config, service.database_url, service.receipt_root,
                            media_root=service.roots["media"], podcast_root=service.roots["podcast"],
                            clock=lambda: now[0], bucket_factory=lambda: bucket)
    snapshot = service._snapshot
    monkeypatch.setattr(service, "_snapshot", lambda _: pytest.fail("retry must reuse completed archive"))
    for _ in range(4):
        now[0] += 601
        state = service.run_if_due()
        assert state["status"] == "failed" and state["archive"] == pending
        assert {path.name for path in service.directory.glob("*.tar.gz")} == originals | {pending}
    now[0] += 2 * 24 * 3600
    bucket.fail = False
    recovered = service.run_if_due()
    assert recovered["status"] == "succeeded"
    assert recovered["last_success_at"] == old_snapshot_time
    assert recovered["pending_upload"] is False
    # The completed old snapshot must not suppress a new, current restore point.
    monkeypatch.setattr(service, "_snapshot", snapshot)
    current = service.run_if_due()
    assert current["archive"] != pending and current["status"] == "succeeded"
    assert dt.datetime.fromisoformat(current["last_success_at"]).timestamp() == now[0]
    assert len(list(service.directory.glob("*.tar.gz"))) == 2


def test_unwritable_status_keeps_ui_failure_and_retry_backoff(service, monkeypatch):
    now = [1_800_000_000]
    service.clock = lambda: now[0]
    original = service.run_if_due()
    assert original["status"] == "succeeded"
    now[0] += 24 * 3600 + 1
    real_write = backup_module._write_json
    calls = []
    def unavailable(path, value):
        calls.append(path)
        raise OSError("sensitive-path-must-not-leak")
    monkeypatch.setattr(backup_module, "_write_json", unavailable)
    state = service.run_if_due()
    assert state["status"] == "failed" and state["last_error"] == "backup_status_unavailable"
    assert state["last_success_at"] == original["last_success_at"]
    assert "sensitive-path" not in json.dumps(state)
    writes = len(calls)
    now[0] += 60
    assert service.run_if_due()["status"] == "failed"
    assert len(calls) == writes
    assert service.status()["last_error"] == "backup_status_unavailable"
    monkeypatch.setattr(backup_module, "_write_json", real_write)
    now[0] += 601
    assert service.run_if_due()["status"] == "succeeded"


def test_unavailable_backup_directory_reports_failure_without_raising(service, tmp_path):
    now = [1_800_000_000]
    service.clock = lambda: now[0]
    service.directory = tmp_path / "blocked-directory"
    service.directory.write_text("existing file")
    state = service.run_if_due()
    assert state["status"] == "failed" and state["last_error"] == "backup_storage_unavailable"
    assert service.status()["status"] == "failed"
    now[0] += 60
    assert service.run_if_due()["last_attempt_at"] == state["last_attempt_at"]
    now[0] += 601
    assert service.run_if_due()["last_attempt_at"] != state["last_attempt_at"]


def test_blob_parent_symlink_cannot_escape_configured_root(service, tmp_path):
    image, _ = add_local(service, "media", b"correct hash does not authorize another directory", ".png")
    parent = image.parent
    escaped = tmp_path / "outside-media"
    parent.rename(escaped)
    parent.symlink_to(escaped, target_is_directory=True)
    state = service.run()
    assert state["status"] == "failed"
    assert state["last_error"] == "backup_source_path_unavailable"
    assert (escaped / image.name).read_bytes() == b"correct hash does not authorize another directory"
    assert not list(service.directory.glob("*.tar.gz"))


def test_backup_sdk_disallows_proxies_and_redirects(service):
    service.config = replace(service.config, destination="oss", bucket="backup-bucket", region="ap-southeast-1",
                             endpoint="https://oss-ap-southeast-1-internal.aliyuncs.com",
                             credential_provider="ecs_role", ecs_role_name="BackupRole")
    bucket = service._bucket()
    assert bucket.session.session.trust_env is False
    assert bucket.session.session.max_redirects == 0


def test_upload_response_loss_recovers_same_remote_object(service):
    import oss2
    class LostResponseBucket(Bucket):
        def put_object_from_file(self, key, path, headers):
            if key in self.items:
                raise oss2.exceptions.ObjectAlreadyExists(409, {}, b"", {})
            super().put_object_from_file(key, path, headers)
            raise OSError("response lost after server stored object")
    bucket = LostResponseBucket()
    service.config = replace(service.config, destination="oss", bucket="backup-bucket", region="ap-southeast-1",
                             endpoint="https://oss-ap-southeast-1-internal.aliyuncs.com",
                             credential_provider="ecs_role", ecs_role_name="BackupRole")
    service.bucket_factory = lambda: bucket
    first = service.run()
    assert first["status"] == "failed" and first["pending_upload"] is True
    second = service.run()
    assert second["status"] == "succeeded" and second["archive"] == first["archive"]
    assert len(bucket.items) == 1 and second["pending_upload"] is False
