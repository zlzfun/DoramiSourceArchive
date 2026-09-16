#!/usr/bin/env python3
"""Temporarily expose a production build + synthetic reader via a Cloudflare Quick Tunnel.

Owns its build, database and process groups. Never connects to deployment data.
Logs/session details remain in tmp/pwa-preview; the sandbox is removed on exit.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from e2e.reader_fixture import USERNAME  # noqa: E402
from scripts.check_mobile_reader_e2e import (  # noqa: E402
    configure, free_port, isolated_environment, run_command, start_process, wait_ready,
)


def run(minutes: int) -> None:
    if not 1 <= minutes <= 240:
        raise ValueError("preview lifetime must be 1–240 minutes")
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError("cloudflared is required")
    output = ROOT / "tmp/pwa-preview"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="session-", dir=output))
    print(f"Artifacts: {artifacts}", flush=True)
    # Keep credentials/configuration out of world-readable directories and process arguments.
    artifacts.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix="dorami-pwa-preview-") as temp, ExitStack() as stack:
        sandbox = Path(temp).resolve()
        backend_port, frontend_port = free_port(), free_port()
        while frontend_port == backend_port:
            frontend_port = free_port()
        configure(sandbox, backend_port)
        env = isolated_environment(sandbox)
        password = secrets.token_urlsafe(15)
        run_command([sys.executable, "e2e/reader_fixture.py", str(sandbox)],
                    {**env, "DORAMI_E2E_PASSWORD": password}, ROOT, artifacts / "seed.log", 60)
        run_command(["npm", "run", "build", "--", "--outDir", str(sandbox / "site")],
                    env, ROOT / "frontend", artifacts / "build.log", 90)
        backend = start_process(stack, [sys.executable, "src/main.py"], env, ROOT, artifacts / "backend.log")
        wait_ready(f"http://127.0.0.1:{backend_port}/api/auth/session", backend,
                   artifacts / "backend.log", f"Uvicorn running on http://127.0.0.1:{backend_port}")
        local = f"http://127.0.0.1:{frontend_port}"
        tunnel_log = artifacts / "tunnel.log"
        # Explicit empty config ignores existing named tunnels/credentials in ~/.cloudflared.
        tunnel_config = sandbox / "cloudflared.yaml"
        tunnel_config.write_text("{}\n")
        tunnel = start_process(stack, [cloudflared, "--no-autoupdate", "tunnel", "--config", str(tunnel_config),
                                      "--url", local], env, ROOT, tunnel_log)
        deadline = time.monotonic() + 60
        match = None
        while time.monotonic() < deadline and tunnel.poll() is None:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", tunnel_log.read_text())
            if match:
                break
            time.sleep(.2)
        if not match:
            raise RuntimeError(f"tunnel URL unavailable; see {tunnel_log}")
        url = match.group()
        env.update(VITE_PROXY_TARGET=f"http://127.0.0.1:{backend_port}",
                   DORAMI_VITE_ALLOWED_HOSTS=urlsplit(url).hostname)
        frontend = start_process(stack, ["npm", "run", "preview", "--", "--host", "127.0.0.1",
                                        "--port", str(frontend_port), "--strictPort", "--outDir", str(sandbox / "site")],
                                 env, ROOT / "frontend", artifacts / "frontend.log")
        wait_ready(local, frontend, artifacts / "frontend.log", local)
        hashes = {name: hashlib.sha256((sandbox / "site" / name).read_bytes()).hexdigest()
                  for name in ("index.html", "sw.js", "manifest.webmanifest")}
        with httpx.Client(trust_env=False, timeout=15) as client:
            # Verify this public origin serves our build, not an unrelated or stale deployment.
            deadline = time.monotonic() + 60
            while True:
                try:
                    for name, expected in hashes.items():
                        response = client.get(url + "/" + name)
                        response.raise_for_status()
                        if hashlib.sha256(response.content).hexdigest() != expected:
                            raise RuntimeError("public preview build hash mismatch")
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            # A hostile Host must not reach the preview; only localhost and this exact tunnel are allowed.
            assert client.get(local, headers={"Host": "untrusted.example"}).status_code == 403
        expiry = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        session = {"url": url, "local_url": local, "username": USERNAME, "password": password,
                   "expires_at": expiry.isoformat(), "build_sha256": hashes,
                   "scope": "synthetic data only; user role, no admin; reader runtime; media/AI disabled",
                   "processes": {"backend": backend.pid, "frontend": frontend.pid, "tunnel": tunnel.pid},
                   "device_pending": "MatePad mini / HarmonyOS 7 / Huawei Browser 6.1.7.303 (user reported)"}
        session_file = artifacts / "session.json"
        session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n")
        session_file.chmod(0o600)
        print(f"Ready: {url}\nCredentials: {session_file}\nExpires: {expiry.isoformat()}", flush=True)
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in (backend, frontend, tunnel)):
                raise RuntimeError("a preview process exited; retiring this session")
            time.sleep(1)
    print("Preview stopped; sandbox removed.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=120, help="Auto-stop after 1–240 minutes (default 120)")
    args = parser.parse_args()
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        run(args.minutes)
    except KeyboardInterrupt:
        print("Preview stopped.", flush=True)
