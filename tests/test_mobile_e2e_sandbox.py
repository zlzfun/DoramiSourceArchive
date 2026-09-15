"""E2E safety guards run in ordinary pytest; no browser or application DB needed."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from e2e.reader_fixture import validate_sandbox  # noqa: E402
from scripts.check_mobile_reader_e2e import configure, isolated_environment, stop_process  # noqa: E402


def test_child_environment_does_not_inherit_deployment_configuration(monkeypatch, tmp_path):
    for key in ("DORAMI_CONFIG_FILE", "DORAMI_RUNTIME_ROLE", "DORAMI_LLM_API_KEY",
                "DORAMI_PODCAST_ARTIFACT_ROOT_DIR", "ALIYUN_AK_SECRET", "HTTP_PROXY", "VITE_PROXY_TARGET"):
        monkeypatch.setenv(key, "must-not-reach-the-test-child")
    env = isolated_environment(tmp_path)
    assert env["DORAMI_CONFIG_FILE"] == str(tmp_path / "backend.ini")
    assert "HOME" in env and "PATH" in env
    assert not any(key.startswith(("ALIYUN_", "VITE_")) for key in env)
    assert {key for key in env if key.startswith("DORAMI_")} == {"DORAMI_CONFIG_FILE"}
    assert "HTTP_PROXY" not in env
    assert os.environ["DORAMI_RUNTIME_ROLE"] == "must-not-reach-the-test-child"


@pytest.mark.parametrize("broken", ["marker", "config_selection", "database_selection", "existing_database"])
def test_seed_guard_refuses_unsafe_inputs(monkeypatch, tmp_path, broken):
    configure(tmp_path, 19001)
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "backend.ini"))
    database = tmp_path / "reader.db"
    if broken == "marker":
        (tmp_path / ".dorami-e2e").unlink()
    elif broken == "config_selection":
        monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "other.ini"))
    elif broken == "database_selection":
        (tmp_path / "backend.ini").write_text("[storage]\ndatabase_url = sqlite:///data/cms_data.db\n")
    else:
        database.write_bytes(b"existing data must not be touched")
    with pytest.raises(ValueError):
        validate_sandbox(tmp_path)
    if broken == "existing_database":
        assert database.read_bytes() == b"existing data must not be touched"
    else:
        assert not database.exists()


def test_seed_guard_accepts_owned_empty_sandbox(monkeypatch, tmp_path):
    configure(tmp_path, 19001)
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(tmp_path / "backend.ini"))
    assert validate_sandbox(tmp_path) == tmp_path / "reader.db"
    assert not (tmp_path / "reader.db").exists()


@pytest.mark.skipif(os.name != "posix", reason="E2E process groups currently target macOS/Linux")
def test_process_cleanup_also_stops_child_ignoring_sigterm(tmp_path):
    ready = tmp_path / "port"
    child = (
        "import signal,socket,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen();"
        f"pathlib.Path({str(ready)!r}).write_text(str(s.getsockname()[1])); time.sleep(60)"
    )
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-c", parent], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        port = int(ready.read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        stop_process(process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
            except OSError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("owned child still listens after cleanup")
        assert process.poll() is not None
    finally:
        stop_process(process)
