#!/usr/bin/env python3
"""Exercise real React listen-guide refresh using explicitly simulated generation.

Owns a disposable SQLite/FastAPI sandbox and a fresh Vite build, using the existing
reader E2E fixture and process lifecycle helpers. Only runtime capability flags,
two articles' listen_guide projections, on-demand POSTs and a synthetic WAV are
browser stubs. No LLM/TTS request is forwarded or billed. This is UI regression
coverage, not evidence of real provider generation or backend authorization.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit
import wave

from playwright.sync_api import expect, sync_playwright

from check_mobile_reader_e2e import (
    ROOT, configure, free_port, isolated_environment, run_command, start_process, wait_ready,
)
from e2e.reader_fixture import PASSWORD, SOURCE_A, USERNAME

ARTICLE_A = f"{SOURCE_A}-00"
ARTICLE_B = f"{SOURCE_A}-01"
AUDIO_PATH = "/__listen-guide-e2e/audio.wav"


def sample_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000 * 8)
    return buffer.getvalue()


def ready_guide() -> dict:
    return {"status": "ready", "audio_ready": True, "audio_url": AUDIO_PATH, "duration_seconds": 8}


def wait_for_state(page, condition, description: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(description)
        page.wait_for_timeout(50)


def exercise(browser, base_url: str, artifacts: Path, width: int, scenario: str, result: dict) -> None:
    context = browser.new_context(viewport={"width": width, "height": 844 if width == 390 else 1000},
                                  is_mobile=width == 390, has_touch=width == 390,
                                  service_workers="block")
    context.set_default_timeout(10000)
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    name = f"{width}-{scenario}"
    row = {"viewport": width, "scenario": scenario, "status": "failed", "events": [], "page_errors": []}
    result["checks"].append(row)
    state = {"post_count": 0, "accepted": False, "detail_gets": 0, "injected_503": False,
             "hold_post": None, "hold_detail": None, "released": False, "external_requests": [],
             "blocked_generation_requests": []}
    page = context.new_page()
    page.on("pageerror", lambda error: row["page_errors"].append(str(error)))
    try:
        login = context.request.post(base_url + "/api/auth/login",
                                     data={"username": USERNAME, "password": PASSWORD})
        assert login.status == 200, "sandbox login failed"
        initial = {}
        for aid in (ARTICLE_A, ARTICLE_B):
            response = context.request.get(base_url + "/api/articles/" + aid)
            assert response.status == 200
            initial[aid] = response.json()
            initial[aid]["listen_guide"] = {}

        def fulfill_detail(route, aid, guide):
            payload = deepcopy(initial[aid])
            payload["listen_guide"] = guide
            route.fulfill(status=200, json=payload)
            row["events"].append({"method": "GET", "article": aid, "status": 200,
                                  "guide_status": guide.get("status", "not_requested")})

        def route_request(route):
            request = route.request
            path = urlsplit(request.url).path
            if urlsplit(request.url).netloc != urlsplit(base_url).netloc:
                state["external_requests"].append(urlsplit(request.url).hostname)
                route.abort()
                return
            if path == "/api/runtime":
                response = route.fetch()
                payload = response.json()
                payload.update(ai_beta_enabled=True, llm_configured=True)
                payload["ondemand"] = {"article": True, "podcast": False}
                route.fulfill(response=response, json=payload)
                return
            if path == f"/api/reader/ai/articles/{ARTICLE_A}/ondemand" and request.method == "POST":
                state["post_count"] += 1
                row["events"].append({"method": "POST", "article": ARTICLE_A, "simulated": True})
                if state["post_count"] > 1:
                    route.fulfill(status=409, json={"detail": "Regression: duplicate on-demand request"})
                elif scenario == "switch-old-response":
                    state["hold_post"] = route
                else:
                    state["accepted"] = True
                    route.fulfill(status=200, json={"outcome": "queued"})
                return
            # Never allow a browser click to escape to paid generation, even on failure.
            if request.method == "POST" and ("/ai/" in path or path.endswith("/ondemand")):
                state["blocked_generation_requests"].append(path)
                route.fulfill(status=403, json={"detail": "Only the explicit local generation stub is allowed"})
                return
            if path in (f"/api/articles/{ARTICLE_A}", f"/api/articles/{ARTICLE_B}") and request.method == "GET":
                aid = path.rsplit("/", 1)[-1]
                if aid == ARTICLE_B or not state["accepted"]:
                    fulfill_detail(route, aid, {})
                elif scenario == "switch-old-response":
                    if not state["released"]:
                        state["hold_detail"] = route
                    else:
                        fulfill_detail(route, aid, ready_guide())
                else:
                    state["detail_gets"] += 1
                    if not state["injected_503"]:
                        state["injected_503"] = True
                        row["events"].append({"method": "GET", "article": aid, "status": 503})
                        route.fulfill(status=503, json={"detail": "Intentional first detail-read failure"})
                    else:
                        guide = {"status": "queued", "audio_ready": False} if state["detail_gets"] == 2 else ready_guide()
                        fulfill_detail(route, aid, guide)
                return
            if path == AUDIO_PATH:
                route.fulfill(status=200, content_type="audio/wav", body=sample_wav())
                return
            route.continue_()

        context.route("**/*", route_request)
        page.goto(base_url + "/#/reader/a/" + ARTICLE_A)
        entry = page.locator(".listen-entries.is-article")
        expect(entry).to_be_visible()
        expect(entry.get_by_role("button", name="听导读", exact=True)).to_be_visible()
        entry.get_by_role("button", name="听导读", exact=True).click()

        if scenario == "switch-old-response":
            wait_for_state(page, lambda: state["hold_post"] is not None, "POST was not held")
            # A real hash-route change, without document reload or React state injection.
            page.evaluate("id => { location.hash = '#/reader/a/' + id; }", ARTICLE_B)
            expect(page.locator(".reader-pane-title")).to_have_text(initial[ARTICLE_B]["title"])
            expect(entry.get_by_role("button", name="听导读", exact=True)).to_be_visible()
            state["accepted"] = True
            state["hold_post"].fulfill(status=200, json={"outcome": "queued"})
            state["hold_post"] = None
            wait_for_state(page, lambda: state["hold_detail"] is not None,
                           "late POST must refresh its original article A, not current article B")
            state["released"] = True
            fulfill_detail(state["hold_detail"], ARTICLE_A, ready_guide())
            state["hold_detail"] = None
            # Let the old promise and React commit settle while B remains selected.
            page.wait_for_timeout(300)
            expect(page.locator(".reader-pane-title")).to_have_text(initial[ARTICLE_B]["title"])
            expect(entry.get_by_role("button", name="听导读", exact=True)).to_be_visible()
            expect(entry.locator("[data-listen-key]")).to_have_count(0)
            row["old_response_did_not_change_article_b"] = True
            page.screenshot(path=str(artifacts / f"{name}-article-b.png"))
            page.evaluate("id => { location.hash = '#/reader/a/' + id; }", ARTICLE_A)
            expect(page.locator(".reader-pane-title")).to_have_text(initial[ARTICLE_A]["title"])
        else:
            expect(entry.get_by_role("button", name="导读排队中…", exact=True)).to_be_visible()
            assert state["injected_503"], "first post-accept detail GET was not exercised"

        play = entry.locator("button[data-listen-key]")
        expect(play).to_have_count(1, timeout=30000)
        expect(play).to_have_attribute("data-listen-key", re.compile(ARTICLE_A))
        assert state["post_count"] == 1, state["post_count"]
        play.click()
        page.wait_for_function("""() => {
            const audio = document.querySelector('audio');
            return audio && audio.currentTime > 0.1 && !audio.error;
        }""")
        row["media_time_first"] = page.locator("audio").evaluate("a => a.currentTime")
        page.wait_for_timeout(500)
        row["media_time_last"] = page.locator("audio").evaluate("a => a.currentTime")
        assert row["media_time_last"] > row["media_time_first"]
        assert state["post_count"] == 1, "play must not issue a second generation POST"
        assert not row["page_errors"], row["page_errors"]
        assert not state["external_requests"], state["external_requests"]
        assert not state["blocked_generation_requests"], state["blocked_generation_requests"]
        row["status"] = "passed"
        page.screenshot(path=str(artifacts / f"{name}-playing.png"))
    finally:
        row.update({k: v for k, v in state.items() if not k.startswith("hold_")})
        if row["status"] != "passed":
            page.screenshot(path=str(artifacts / f"{name}-failure.png"))
        context.tracing.stop(path=str(artifacts / f"{name}-trace.zip"))
        context.close()


def run(args) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="listen-guide-", dir=args.output)).resolve()
    result = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Real React + isolated backend; browser-stub generation/status/WAV, no paid API calls",
              "checks": [], "artifacts": str(artifacts)}
    started = time.monotonic()
    sandbox = None
    try:
        with tempfile.TemporaryDirectory(prefix="dorami-listen-e2e-") as temp, ExitStack() as stack:
            sandbox = Path(temp).resolve()
            port = free_port()
            configure(sandbox, port)
            env = isolated_environment(sandbox)
            run_command([sys.executable, "e2e/reader_fixture.py", str(sandbox)], env, ROOT,
                        artifacts / "seed.log", timeout=60)
            run_command(["npm", "run", "build", "--", "--outDir", str(sandbox / "site")],
                        env, ROOT / "frontend", artifacts / "build.log", timeout=120)
            result["build_sha256"] = hashlib.sha256((sandbox / "site/index.html").read_bytes()).hexdigest()
            backend = start_process(stack, [sys.executable, "src/main.py"], env, ROOT, artifacts / "backend.log")
            wait_ready(f"http://127.0.0.1:{port}/api/auth/session", backend, artifacts / "backend.log",
                       f"Uvicorn running on http://127.0.0.1:{port}")
            frontend_port = free_port()
            env["VITE_PROXY_TARGET"] = f"http://127.0.0.1:{port}"
            frontend = start_process(stack, ["npm", "run", "preview", "--", "--host", "127.0.0.1",
                                           "--port", str(frontend_port), "--strictPort", "--outDir", str(sandbox / "site")],
                                     env, ROOT / "frontend", artifacts / "frontend.log")
            base_url = f"http://127.0.0.1:{frontend_port}"
            wait_ready(base_url, frontend, artifacts / "frontend.log", base_url)
            result["owned_processes"] = {"backend": backend.pid, "frontend": frontend.pid}
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.channel, headless=not args.headed)
                try:
                    for width in (1440, 390):
                        for scenario in ("first-get-503", "switch-old-response"):
                            exercise(browser, base_url, artifacts, width, scenario, result)
                finally:
                    browser.close()
        result["status"] = "passed"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        (artifacts / "error.txt").write_text(traceback.format_exc())
        print(result["error"], file=sys.stderr)
    finally:
        result["sandbox_removed"] = sandbox is not None and not sandbox.exists()
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        (artifacts / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Listen-guide E2E: {result['status']}\nArtifacts: {artifacts}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/e2e")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--headed", action="store_true")
    raise SystemExit(run(parser.parse_args()))
