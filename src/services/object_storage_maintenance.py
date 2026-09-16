"""Explicit maintenance of registered immutable objects; never scans/deletes a whole bucket."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from pathlib import Path

from sqlmodel import Session, select

from models.db import MediaAssetRecord, ObjectBlobRecord, PodcastArtifactRecord
from services.object_storage import ObjectStorage, ObjectStorageError, hash_file


@dataclass
class Blob:
    namespace: str
    content_hash: str
    ext: str
    size: int
    mime: str
    references: int = 0
    remote: ObjectBlobRecord | None = None

    @property
    def identity(self):
        return f"{self.namespace}:{self.content_hash}{self.ext}"


def inventory(engine) -> list[Blob]:
    """Include withdrawn audio and every referenced hash; only byte copies are deduplicated."""
    blobs = {}
    with Session(engine) as session:
        for namespace, model in (("media", MediaAssetRecord), ("podcast", PodcastArtifactRecord)):
            for row in session.exec(select(model)).all():
                if not row.content_hash or not row.ext or not row.size_bytes:
                    continue
                item = Blob(namespace, row.content_hash, row.ext, row.size_bytes, row.mime)
                previous = blobs.setdefault(item.identity, item)
                if (previous.size, previous.mime) != (item.size, item.mime):
                    raise ObjectStorageError("object_storage_reference_conflict")
                previous.references += 1
        for row in session.exec(select(ObjectBlobRecord)).all():
            item = Blob(row.namespace, row.content_hash, row.ext, row.size_bytes, row.mime)
            previous = blobs.setdefault(item.identity, item)
            if (previous.size, previous.mime) != (item.size, item.mime):
                raise ObjectStorageError("object_storage_registry_conflict")
            previous.remote = row
    return sorted(blobs.values(), key=lambda item: item.identity)


def local_path(store: ObjectStorage, item: Blob) -> Path:
    store.identity(item.content_hash, item.ext)
    path = store.root / item.content_hash[:2] / f"{item.content_hash}{item.ext}"
    if not path.resolve().is_relative_to(store.root) or path.is_symlink():
        raise ObjectStorageError("object_storage_unsafe_path")
    return path


def execute(engine, stores, *, action="upload", apply=False, offline=False,
            cache_target_bytes=0, prune_before=None, emit=lambda row: None):
    if action not in {"upload", "verify", "restore", "evict", "gc"}:
        raise ValueError("unknown action")
    if apply and not offline:
        raise ValueError("stop all API/workers and pass --offline before --apply")
    if cache_target_bytes < 0:
        raise ValueError("cache target must be nonnegative")
    if action == "gc" and apply and prune_before is None:
        raise ValueError("GC requires --prune-before after checking retained backups")
    # Snapshot-only, no transaction survives cloud I/O. Operator must stop writers.
    items = [item for item in inventory(engine) if item.namespace in stores]
    paths = {item.identity: local_path(stores[item.namespace], item) for item in items}
    totals = {ns: sum(p.stat().st_size for p in store.root.glob("[0-9a-f][0-9a-f]/*") if p.is_file())
              for ns, store in stores.items()}
    if action == "evict":
        items.sort(key=lambda item: paths[item.identity].stat().st_mtime if paths[item.identity].is_file() else 0)
    result = {"objects": len(items), "completed": 0, "skipped": 0, "errors": 0, "bytes": 0, "dry_run": not apply}
    for item in items:
        path, store = paths[item.identity], stores[item.namespace]
        report = {"id": item.identity, "references": item.references, "size_bytes": item.size,
                  "registered": item.remote is not None, "local": path.is_file(), "action": action}
        try:
            if action == "upload":
                if item.references == 0:
                    report["result"] = "unreferenced"
                elif path.is_file():
                    if hash_file(path) != (item.content_hash, item.size):
                        raise ObjectStorageError("object_storage_local_checksum_mismatch")
                    if apply:
                        if not store.enabled:
                            raise ObjectStorageError("object_storage_backend_not_enabled")
                        store.persist(path, item.content_hash, item.ext, item.size, item.mime)
                    report["result"] = "uploaded" if apply else "would_upload_or_verify"
                elif item.remote:
                    if apply:
                        store.verify_remote(item.remote)
                    report["result"] = "already_remote" if apply else "remote_only_unverified"
                else:
                    raise ObjectStorageError("object_storage_local_file_missing")
            elif action == "verify":
                if item.remote is None:
                    raise ObjectStorageError("object_storage_not_registered")
                if apply:
                    store.verify_remote(item.remote)
                report["result"] = "verified" if apply else "would_verify"
            elif action == "restore":
                if item.references == 0:
                    report["result"] = "unreferenced"
                elif path.is_file() and hash_file(path) == (item.content_hash, item.size):
                    report["result"] = "already_local"
                elif item.remote is None:
                    raise ObjectStorageError("object_storage_not_registered")
                else:
                    if apply:
                        # materialize only checks warm file size. Preserve a corrupt
                        # file for diagnosis, forcing a verified remote download.
                        if path.exists():
                            path.rename(path.with_name(path.name + ".corrupt-" + dt.datetime.now().strftime("%Y%m%d%H%M%S%f")))
                        store.materialize(path, item.content_hash, item.ext, item.size)
                    report["result"] = "restored" if apply else "would_restore"
            elif action == "evict":
                if not path.is_file() or item.remote is None or totals[item.namespace] <= cache_target_bytes:
                    report["result"] = "skipped"
                else:
                    if apply:
                        store.verify_remote(item.remote)
                        # Recheck local identity before deleting; keep damaged bytes for diagnosis.
                        if hash_file(path) != (item.content_hash, item.size):
                            raise ObjectStorageError("object_storage_local_checksum_mismatch")
                        path.unlink()
                    totals[item.namespace] -= item.size
                    report["result"] = "evicted" if apply else "would_evict"
            elif action == "gc":
                if item.references or item.remote is None:
                    report["result"] = "skipped"
                else:
                    created = dt.datetime.fromisoformat(item.remote.created_at.replace("Z", "+00:00"))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=dt.timezone.utc)
                    if prune_before is None or created >= prune_before:
                        report["result"] = "orphan_retained"
                    else:
                        if apply:
                            # Requires a separate maintenance credential with DeleteObject.
                            try:
                                store._bucket(item.remote).delete_object(item.remote.object_key)
                            except Exception:
                                raise ObjectStorageError("object_storage_delete_failed") from None
                            with Session(engine) as session:
                                row = session.get(ObjectBlobRecord, item.identity)
                                if row is not None:
                                    session.delete(row)
                                    session.commit()
                        report["result"] = "remote_deleted" if apply else "would_delete_remote"
            if report["result"] in {"skipped", "unreferenced", "already_local", "orphan_retained"}:
                result["skipped"] += 1
            else:
                result["completed"] += 1
                result["bytes"] += item.size
        except (ObjectStorageError, OSError, ValueError) as exc:
            result["errors"] += 1
            report["result"] = "error"
            report["error"] = str(exc) if isinstance(exc, ObjectStorageError) else type(exc).__name__
        emit(report)
    return result
