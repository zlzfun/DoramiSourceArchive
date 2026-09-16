"""One cache namespace's filesystem failure must not disable other maintenance."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.object_storage import ObjectStorage
from services.storage_runtime import maintain_storage
from tests.test_object_storage import oss_config


@pytest.mark.parametrize("failure", ["root", "lock"])
def test_cache_filesystem_failure_still_runs_other_namespace_and_backup(monkeypatch, tmp_path, failure):
    media = ObjectStorage(None, tmp_path / "media", "media", oss_config())
    podcast = ObjectStorage(None, tmp_path / "podcast", "podcast", oss_config())
    calls = []
    monkeypatch.setattr(media, "evict_cache", lambda: pytest.fail("unusable namespace must not evict"))
    monkeypatch.setattr(podcast, "evict_cache", lambda: calls.append("podcast"))
    backup = SimpleNamespace(config=SimpleNamespace(enabled=True), run_if_due=lambda: calls.append("backup"))

    if failure == "root":
        media.root.write_bytes(b"occupied by a regular file")
    else:
        original_open = Path.open

        def open_path(path, *args, **kwargs):
            if path == media.root / ".oss-maintenance.lock":
                raise PermissionError("private filesystem diagnostic")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_path)

    maintain_storage([media, podcast], backup)

    assert calls == ["podcast", "backup"]
    if failure == "lock":
        assert media._state()["cache"]["last_error"] == "object_storage_cache_failed"
        assert "private filesystem diagnostic" not in (media.root / ".oss-status.json").read_text()
