"""Guards that signed Podcast ASR fetch queries never reach proxy/app logs."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

from services.request_log_redaction import (
    PODCAST_ASR_SOURCE_AUDIO_PATH,
    SensitiveRequestQueryFilter,
    redact_sensitive_request_queries,
)
from services.podcast_asr_fetch_signing import ASR_FETCH_PATH


ROOT = Path(__file__).resolve().parents[1]
SIGNED_TARGET = f"{PODCAST_ASR_SOURCE_AUDIO_PATH}?payload=claim&signature=top-secret"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _exact_locations(source: str) -> list[str]:
    return re.findall(
        rf"location = {re.escape(PODCAST_ASR_SOURCE_AUDIO_PATH)} \{{(.*?)\n\s*\}}",
        source,
        re.DOTALL,
    )


def test_redactor_removes_only_the_signed_asr_route_query():
    assert PODCAST_ASR_SOURCE_AUDIO_PATH == ASR_FETCH_PATH
    assert redact_sensitive_request_queries(
        f'GET {SIGNED_TARGET} HTTP/1.1'
    ) == f"GET {PODCAST_ASR_SOURCE_AUDIO_PATH} HTTP/1.1"
    assert redact_sensitive_request_queries(
        "GET /api/health?verbose=1 HTTP/1.1"
    ) == "GET /api/health?verbose=1 HTTP/1.1"
    assert redact_sensitive_request_queries(
        f"https://archive.example{PODCAST_ASR_SOURCE_AUDIO_PATH}?signature=secret"
    ) == f"https://archive.example{PODCAST_ASR_SOURCE_AUDIO_PATH}"


def test_filter_redacts_formatted_messages_and_exception_text():
    import io
    import logging

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SensitiveRequestQueryFilter())
    logger = logging.getLogger("tests.podcast-asr-redaction")
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("request %s", SIGNED_TARGET)
        try:
            raise RuntimeError(f"upstream failed for {SIGNED_TARGET}")
        except RuntimeError:
            logger.exception("ASR route failed")
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    output = stream.getvalue()
    assert PODCAST_ASR_SOURCE_AUDIO_PATH in output
    assert "payload=claim" not in output
    assert "top-secret" not in output


def test_real_uvicorn_configuration_then_app_import_keeps_queries_out_of_logs():
    """Exercise Uvicorn's actual dictConfig-before-app-import startup order."""

    script = f"""
import logging
import uvicorn

config = uvicorn.Config("api.app:app", lifespan="off")
config.load()

access = logging.getLogger("uvicorn.access")
access.info(
    '%s - "%s %s HTTP/%s" %d',
    '127.0.0.1:1234', 'GET', {SIGNED_TARGET!r}, '1.1', 200,
)
access.info(
    '%s - "%s %s HTTP/%s" %d',
    '127.0.0.1:1234', 'GET', '/api/health?verbose=1', '1.1', 200,
)
try:
    raise RuntimeError('failure at ' + {SIGNED_TARGET!r})
except RuntimeError:
    logging.getLogger("uvicorn.error").exception("ASR request failed")
logging.getLogger("dorami.podcasts").error("worker failed: %s", {SIGNED_TARGET!r})
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = completed.stdout + completed.stderr
    assert PODCAST_ASR_SOURCE_AUDIO_PATH in output
    assert f'GET {PODCAST_ASR_SOURCE_AUDIO_PATH} HTTP/1.1" 200' in output
    assert "/api/health?verbose=1" in output
    assert "payload=claim" not in output
    assert "top-secret" not in output
    assert "Logging error" not in output


def test_all_nginx_deployment_paths_protect_only_the_exact_signed_route():
    docker_inner = _read("docker/nginx.conf")
    edge = _read("docker/edge-nginx.conf.example")
    baremetal = _read("deploy.sh")

    inner_locations = _exact_locations(docker_inner)
    edge_locations = _exact_locations(edge)
    baremetal_locations = _exact_locations(baremetal)

    assert len(inner_locations) == 1
    assert len(edge_locations) == 2
    assert len(baremetal_locations) == 4

    for block in inner_locations + edge_locations + baremetal_locations:
        assert "access_log off;" in block
        assert "error_log /dev/null crit;" in block

    for block in (
        inner_locations + edge_locations[1:] + baremetal_locations[1:]
    ):
        assert "proxy_buffering off;" in block
        assert "proxy_pass " in block

    # The general API locations remain present and retain their own logging.
    assert docker_inner.count("location /api/ {") == 1
    assert baremetal.count("location /api/ {") == 3
