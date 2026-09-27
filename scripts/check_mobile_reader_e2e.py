#!/usr/bin/env python3
"""Build and test the mobile reader against an owned FastAPI/SQLite sandbox.

No externally supplied server or database URL is accepted. Each invocation owns
its processes, configuration, browser context and disposable database.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from e2e.mobile_reader import run_flows  # noqa: E402
from e2e.focus_ring import run_focus_flows  # noqa: E402
from e2e.pwa import run_pwa_flows  # noqa: E402

FLOWS = ("mobile", "pwa", "focus")


def isolated_environment(sandbox: Path) -> dict[str, str]:
    # An allowlist also excludes deployment credentials and DORAMI_* overrides.
    keep = {"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "LANG"}
    env = {key: value for key, value in os.environ.items() if key in keep or key.startswith("LC_")}
    env["DORAMI_CONFIG_FILE"] = str(sandbox / "backend.ini")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def configure(sandbox: Path, port: int) -> None:
    (sandbox / ".dorami-e2e").touch()
    (sandbox / "backend.ini").write_text(f"""[server]
host = 127.0.0.1
port = {port}
reload = false
[runtime]
role = reader
[taxonomy]
deployment = manual
[storage]
database_url = sqlite:///{sandbox / 'reader.db'}
[auth]
secret = {secrets.token_urlsafe(32)}
[cors]
allow_origins = http://127.0.0.1
[network]
disable_ca_bundle = false
[media]
enabled = false
media_dir = {sandbox / 'media'}
[podcast]
installation = development
processing_enabled = false
[podcast_artifacts]
root_dir = {sandbox / 'podcast'}
[bailian_speech]
enabled = false
tts_receipt_root = {sandbox / 'speech'}
""")


def stop_process(process: subprocess.Popen) -> None:
    # npm starts a child Node process; stop only the process group we created.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The parent may exit before a child; finish retiring the owned group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def start_process(stack, command, env, cwd, log_path):
    log = stack.enter_context(log_path.open("w"))
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    stack.callback(stop_process, process)
    return process


def run_command(command, env, cwd, log_path, timeout):
    with ExitStack() as stack:
        process = start_process(stack, command, env, cwd, log_path)
        code = process.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, command)


def wait_ready(url: str, process: subprocess.Popen, log_path: Path, marker: str, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"server exited ({process.returncode}); see server log")
            try:
                # A free port can be claimed between selection and startup. Require our
                # child's successful bind message before trusting any HTTP response.
                if marker in log_path.read_text() and client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"server did not become ready: {url}")


def run(args) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="reader-", dir=args.output)).resolve()
    flows = [flow.strip() for flow in args.flows.split(",") if flow.strip()]
    unknown = sorted(set(flows) - set(FLOWS))
    if unknown or not flows:
        raise SystemExit(f"--flows accepts a comma-separated subset of {','.join(FLOWS)}; got {args.flows!r}")
    result = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(),
              "browser": args.channel or "chromium", "issues": [85, 86, 90, 108], "flows": flows,
              "scope": "Built frontend + real FastAPI + disposable SQLite; no API response mocks.",
              "artifacts": str(artifacts)}
    started = time.monotonic()
    sandbox = None
    try:
        result["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        result["tracked_changes"] = subprocess.check_output(["git", "status", "--short", "--untracked-files=no"], cwd=ROOT, text=True).splitlines()
        with tempfile.TemporaryDirectory(prefix="dorami-reader-e2e-") as temp, ExitStack() as stack:
            sandbox = Path(temp).resolve()
            backend_port = free_port()
            configure(sandbox, backend_port)
            env = isolated_environment(sandbox)
            run_command([sys.executable, "e2e/reader_fixture.py", str(sandbox)], env, ROOT,
                        artifacts / "seed.log", timeout=60)
            run_command(["npm", "run", "build", "--", "--outDir", str(sandbox / "site")],
                        env, ROOT / "frontend", artifacts / "build.log", timeout=90)
            # Fresh build, isolated output: never replace the user's running dist preview.
            result["build_index_sha256"] = hashlib.sha256((sandbox / "site/index.html").read_bytes()).hexdigest()
            backend = start_process(stack, [sys.executable, "src/main.py"], env, ROOT, artifacts / "backend.log")
            wait_ready(f"http://127.0.0.1:{backend_port}/api/auth/session", backend,
                       artifacts / "backend.log", f"Uvicorn running on http://127.0.0.1:{backend_port}")
            frontend_port = free_port()
            env["VITE_PROXY_TARGET"] = f"http://127.0.0.1:{backend_port}"
            frontend = start_process(stack, ["npm", "run", "preview", "--", "--host", "127.0.0.1",
                                     "--port", str(frontend_port), "--strictPort", "--outDir", str(sandbox / "site")],
                                     env, ROOT / "frontend", artifacts / "frontend.log")
            base_url = f"http://127.0.0.1:{frontend_port}"
            wait_ready(base_url, frontend, artifacts / "frontend.log", base_url)
            with httpx.Client(trust_env=False, timeout=5) as client:
                served_hash = hashlib.sha256(client.get(base_url).content).hexdigest()
            if served_hash != result["build_index_sha256"]:
                raise RuntimeError("preview is not serving this run's build")
            result["base_url"] = base_url
            result["processes"] = {"backend": backend.pid, "frontend": frontend.pid}
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.channel, headless=not args.headed)
                try:
                    if "mobile" in flows:
                        run_flows(browser, base_url, sandbox / "reader.db", artifacts, result)
                    if "pwa" in flows:
                        run_pwa_flows(browser, base_url, sandbox / "site", artifacts, result)
                    if "focus" in flows:
                        run_focus_flows(browser, base_url, artifacts, result)
                finally:
                    browser.close()
        result["status"] = "passed"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        (artifacts / "error.txt").write_text(traceback.format_exc())
        print(result["error"], file=sys.stderr)
    finally:
        if sandbox is not None:
            result["sandbox_removed"] = not sandbox.exists()
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        (artifacts / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Mobile reader E2E: {result['status']} ({result['duration_seconds']}s)\nArtifacts: {artifacts}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/e2e")
    parser.add_argument("--channel", default=None, help="Optional installed Chromium channel, e.g. chrome")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--flows", default=",".join(FLOWS),
                        help=f"comma-separated subset of {','.join(FLOWS)} (default: all)")
    raise SystemExit(run(parser.parse_args()))
