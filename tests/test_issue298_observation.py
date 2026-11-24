"""Observation orchestration stays isolated, idempotent and honest about time."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "issue298_observation.py"
spec = importlib.util.spec_from_file_location("issue298_observation", MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("DORAMI_CONFIG_FILE", os.environ.get("DORAMI_CONFIG_FILE", ""))
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"sources": [{"source_id": "rss_openai_news", "name": "OpenAI", "category": "official", "url": "https://openai.com/news/rss.xml", "format": "rss", "status": "ok"}]}))
    root = tmp_path / "run"
    # Do not collide with a developer's preview on the default backend port.
    # start() still performs its real exclusive bind check on this port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    state = runner.prepare(root, sources, port=port, frontend_port=0)
    runner.publish_catalog(root)
    return root, sources, state


def test_prepare_idempotent_keeps_ledger_and_scenarios(prepared):
    root, sources, first = prepared
    again = runner.prepare(root, sources)
    assert first["run_id"] == again["run_id"]
    assert len(first["scenario_accounts"]) == 6
    assert len(first["conditions"]) == 8
    config = runner.read_config(root)
    assert config.get("llm", "api_key") == ""
    assert config.get("bailian_speech", "api_key") == ""
    assert config.getboolean("podcast", "processing_enabled") is False
    assert config.getint("bailian_speech", "tts_daily_budget_minor") == 2000
    with sqlite3.connect(first["database"]) as db:
        assert db.execute("SELECT count(*) FROM collection_jobs WHERE is_active=1").fetchone()[0] == 0
        assert db.execute("SELECT value FROM app_settings WHERE key='ai_beta_global_enabled'").fetchone()[0] == "false"
        assert db.execute("SELECT count(*) FROM reader_subscriptions WHERE owner_username=?", (first["scenario_accounts"]["custom_only"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM user_interest_conditions WHERE owner_username=?", (first["scenario_accounts"]["custom_only"],)).fetchone()[0] == 8
    assert runner.metrics(root)["submission_counter"] == {"tts": 0, "provider": 0, "semantic_authorizations": 0, "llm_metered_calls": 0}


def test_prepare_rejects_unowned_or_changed_input(prepared, tmp_path):
    root, sources, _ = prepared
    sources.write_text('{"sources":[]}')
    with pytest.raises(ValueError, match="Source manifest changed"):
        runner.prepare(root, sources)
    foreign = tmp_path / "foreign"
    foreign.mkdir(); (foreign / "data.db").write_text("preserve")
    with pytest.raises(ValueError, match="empty directory"):
        runner.prepare(foreign, sources)
    assert (foreign / "data.db").read_text() == "preserve"


def test_stop_requires_spaced_verified_samples_and_preserves_db(prepared):
    root, _, state = prepared
    runner.stop(root)
    assert runner.verify_stopped(root, worker_stopped=True)["phase"] == "stopping"
    assert runner.verify_stopped(root, worker_stopped=True)["phase"] == "stopping"
    done = runner.verify_stopped(root, worker_stopped=True, minimum_seconds=0)
    assert done["phase"] == "stopped"
    assert Path(state["database"]).exists()
    assert runner.read_config(root).get("llm", "paid_authorized_until") == runner.EXPIRED
    result = runner.report(root)
    content = Path(result["report"]).read_text()
    assert "证据不足" in content and "待验证" in content
    assert runner.status(root)["elapsed_hours"] == 0


def test_readiness_hash_and_pass_are_mandatory(tmp_path):
    proof = tmp_path / "proof.txt"; proof.write_text("verified")
    ready = tmp_path / "ready.json"
    data = {"checks": {name: {"passed": True, "evidence": str(proof), "sha256": runner.digest(proof)} for name in runner.CHECKS}}
    ready.write_text(json.dumps(data))
    assert runner.readiness(ready)
    proof.write_text("changed")
    with pytest.raises(ValueError, match="readiness evidence"):
        runner.readiness(ready)


def test_config_cannot_redirect_owned_ledger(prepared, tmp_path):
    root, _, _ = prepared
    config = runner.read_config(root)
    config.set("storage", "database_url", "sqlite:///" + str(tmp_path / "foreign.db"))
    runner.save_config(root, config)
    with pytest.raises(ValueError, match="Runtime database differs"):
        runner.stop(root)


def test_start_validates_before_enabling_and_preserves_deadline(prepared, tmp_path, monkeypatch):
    root, _, state = prepared
    # No runtime process is booted. Fake credentials are parsed, never submitted.
    state["source_ids"] = [f"fixture-{n}" for n in range(24)]
    runner.write_json(root / "manifest.json", state)
    proof = tmp_path / "proof.txt"; proof.write_text("fixture validation")
    ready = tmp_path / "ready.json"
    ready.write_text(json.dumps({"checks": {name: {"passed": True, "evidence": str(proof),
        "sha256": runner.digest(proof)} for name in runner.CHECKS}}))
    config = runner.read_config(root)
    for section, items in {"llm": {"base_url": "https://example.invalid/v1", "api_key": "fake", "model": "fake"},
                           "bailian_speech": {"api_key": "fake", "account_scope": "fixture", "tts_chunk_chars": "99999"}}.items():
        for key, value in items.items(): config.set(section, key, value)
    runner.save_config(root, config)
    monkeypatch.setattr(runner, "code_fingerprint", lambda: "fixture-source-revision")
    with pytest.raises(ValueError, match="config failed validation"):
        runner.start(root, ready)
    assert runner.owned(root)["started_at"] is None
    assert not runner.read_config(root).getboolean("podcast", "processing_enabled")
    with sqlite3.connect(state["database"]) as db:
        assert db.execute("SELECT count(*) FROM collection_jobs WHERE is_active=1").fetchone()[0] == 0
    config.set("bailian_speech", "tts_chunk_chars", "500")
    runner.save_config(root, config)
    started = runner.start(root, ready)
    resumed = runner.start(root, ready)
    assert resumed["started_at"] == started["started_at"]
    assert resumed["ends_at"] == started["ends_at"]
    assert (runner.dt.datetime.fromisoformat(started["ends_at"]) - runner.dt.datetime.fromisoformat(started["started_at"])).total_seconds() == 72 * 3600
    assert runner.read_config(root).getboolean("bailian_speech", "enabled")
    assert started["taxonomy_baseline_version"] == 1
    with sqlite3.connect(state["database"]) as db:
        for key in ("ai_beta_global_enabled", "taxonomy_auto_activation_enabled", "taxonomy_candidate_enabled"):
            assert db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()[0] == "true"
    assert runner.metrics(root)["submission_counter"] == {"tts": 0, "provider": 0, "semantic_authorizations": 0, "llm_metered_calls": 0}
    monkeypatch.setattr(runner, "code_fingerprint", lambda: "changed-source-revision")
    with pytest.raises(ValueError, match="Executable inputs changed"):
        runner.start(root, ready)
    runner.stop(root)
    with pytest.raises(ValueError, match="stopped run"):
        runner.start(root, ready)


def test_agent_installed_but_unarmed_never_starts(prepared, tmp_path, monkeypatch):
    root, _, _ = prepared
    monkeypatch.setattr(runner, "supervise", lambda *a: pytest.fail("Unarmed agent started runtime"))
    result = runner.agent_tick(root, tmp_path / "not-ready.json")
    assert result["action"] == "waiting_for_explicit_arm"
    assert runner.owned(root)["started_at"] is None
    spec = runner.launchagent_spec(root, tmp_path / "not-ready.json")
    assert spec["StartInterval"] == 75 and not spec["KeepAlive"]
    assert not spec["AbandonProcessGroup"]
    assert spec["ProgramArguments"][-3] == "agent-tick"
    assert set(spec["EnvironmentVariables"]) == {"PATH"}


def test_agent_resume_after_deadline_only_stops_and_verifies(prepared, tmp_path, monkeypatch):
    root, _, state = prepared
    current = runner.now()
    state.update(phase="running", started_at=(current-runner.dt.timedelta(hours=73)).isoformat(),
                 ends_at=(current-runner.dt.timedelta(hours=1)).isoformat())
    runner.write_json(root / "manifest.json", state)
    monkeypatch.setattr(runner, "supervise", lambda *a: pytest.fail("Expired run restarted paid runtime"))
    monkeypatch.setattr(runner, "now", lambda: current)
    first = runner.agent_tick(root, tmp_path / "not-ready.json")
    assert first["phase"] == "stopping"
    current += runner.dt.timedelta(seconds=76)
    second = runner.agent_tick(root, tmp_path / "not-ready.json")
    assert second["phase"] == "stopped"
    assert runner.owned(root)["ends_at"] == state["ends_at"]
    assert runner.read_config(root).get("llm", "paid_authorized_until") == runner.EXPIRED
    assert runner.metrics(root)["submission_counter"] == {"tts": 0, "provider": 0, "semantic_authorizations": 0, "llm_metered_calls": 0}


def test_agent_does_not_bypass_changed_armed_inputs(prepared, tmp_path, monkeypatch):
    root, _, state = prepared
    ready = tmp_path / "ready.json"; ready.write_text('{}')
    runner.write_json(root / "armed.json", {"run_id": state["run_id"], "readiness_path": str(ready),
        "readiness_sha256": runner.digest(ready), "config_sha256": "stale", "code_sha256": "fixture"})
    monkeypatch.setattr(runner, "code_fingerprint", lambda: "fixture")
    monkeypatch.setattr(runner, "supervise", lambda *a: pytest.fail("Changed inputs started runtime"))
    assert runner.agent_tick(root, ready)["action"] == "blocked_prerequisite"
    assert runner.owned(root)["started_at"] is None


def test_catalogue_publish_is_idempotent_and_start_requires_local_version(prepared):
    root, _, state = prepared
    assert runner.publish_catalog(root)["taxonomy_version"] == 1
    assert runner.publish_catalog(root)["new_concepts_approved"] == 0
    with sqlite3.connect(state["database"]) as db:
        assert db.execute("SELECT count(*) FROM taxonomy_versions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM tag_retag_jobs").fetchone()[0] == 1
        db.execute("INSERT INTO app_settings(key,value) VALUES ('taxonomy:authority_id','remote-fixture')")
    with pytest.raises(ValueError, match="Publish the reviewed local catalogue"):
        runner.verify_local_baseline(root, runner.read_config(root))
