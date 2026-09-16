#!/usr/bin/env python3
"""Create/verify backups or restore into an EMPTY offline destination.

Does not load api.app, initialize databases, start workers, or invoke paid APIs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "status", "verify", "restore", "download"])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256", help="known SHA-256 from create report or protected sidecar")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--object-key")
    parser.add_argument("--offline", action="store_true", help="confirm ALL application processes are stopped")
    parser.add_argument("--max-unpacked-mb", type=int, default=102400)
    args = parser.parse_args(argv)
    if args.max_unpacked_mb <= 0:
        parser.error("positive --max-unpacked-mb required")
    if args.action in {"verify", "restore"} and (not args.archive or not args.sha256):
        parser.error("--archive and --sha256 required")
    if args.action == "restore" and (not args.target or not args.offline):
        parser.error("--target and --offline required; stop all API/workers first")
    if args.action == "download" and (not args.object_key or not args.target or not args.sha256):
        parser.error("--object-key, --target and --sha256 required")
    from services.storage_backup import BackupError, BackupService, restore_backup
    try:
        if args.action == "restore":
            result = restore_backup(args.archive, args.target, expected_sha256=args.sha256,
                                    offline=True, max_bytes=args.max_unpacked_mb * 1024**2)
        elif args.action == "verify":
            with tempfile.TemporaryDirectory(prefix="dorami-verify-") as directory:
                result = restore_backup(args.archive, Path(directory) / "restored", expected_sha256=args.sha256,
                                        offline=True, max_bytes=args.max_unpacked_mb * 1024**2)
            result["status"] = "verified"
        else:
            from config import load_config
            config = load_config()
            service = BackupService(config.backup, config.storage.database_url, config.bailian_speech.tts_receipt_root,
                                    media_root=config.media.media_dir, podcast_root=config.podcast_artifacts.root_dir)
            if args.action == "create":
                # Pin local objects during copies, including across online cache workers.
                from sqlalchemy import create_engine
                from services.object_storage import ObjectStorage
                engine = create_engine(config.storage.database_url)
                try:
                    service.object_stores = {name: ObjectStorage(engine, root, name, config.oss)
                                             for name, root in service.roots.items()}
                    result = service.run()
                finally:
                    engine.dispose()
            elif args.action == "download":
                result = service.download(args.object_key, args.target, expected_sha256=args.sha256,
                                          max_bytes=args.max_unpacked_mb * 1024**2)
            else:
                result = service.status()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] not in {"failed", "busy", "disabled"} or args.action == "status" else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc) if isinstance(exc, BackupError) else "backup_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
