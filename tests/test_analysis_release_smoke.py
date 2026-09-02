"""Deterministic guard for the isolated WP-7 release smoke."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import smoke_analysis_release as release_smoke  # noqa: E402


def test_release_smoke_exercises_recovery_concurrency_and_deadline(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'release-smoke.db'}"
    report = asyncio.run(
        release_smoke.run_release_smoke(
            argparse.Namespace(
                database_url=database_url,
                rss_source="rss_simonwillison",
                skip_live_rss=True,
                live_llm=False,
                writers=3,
            )
        )
    )

    assert report["violations"] == []
    assert report["restart_recovery"]["attempt_statuses"] == [
        "timeout",
        "succeeded",
    ]
    assert report["sqlite_concurrency"]["locked_errors"] == 0
    assert report["sqlite_concurrency"]["persisted_rows"] == 4
    assert report["first_open_deadline"]["after_status"] == "degraded"
    assert report["first_open_deadline"]["outside_scope_items"] == 0


def test_release_smoke_refuses_configured_application_database():
    with pytest.raises(ValueError, match="configured application database"):
        release_smoke._assert_isolated_database(  # noqa: SLF001 - safety guard contract
            release_smoke.settings.storage.database_url
        )
