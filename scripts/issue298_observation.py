#!/usr/bin/env python3
"""Prepare, supervise and report one isolated, bounded #298 observation.

No command guesses credentials or points at an existing database. ``prepare``
creates a new owned run with paid work disabled. ``run`` requires hashed readiness
evidence and explicit private configuration before starting the application's
real scheduler. Never delete this directory to retry a paid/unknown request.
"""
from __future__ import annotations

import argparse
import configparser
import contextlib
import csv
import datetime as dt
import hashlib
import html
import json
import os
import plistlib
from pathlib import Path
import secrets
import signal
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
UTC = dt.timezone.utc
EXPIRED = "2020-01-01T00:00:00+00:00"
FLAGS = {
    "article_analysis_enabled": True,
    "taxonomy_candidate_enabled": True,
    "personal_digest_enabled": True,
    "custom_interest_matching_enabled": True,
    "taxonomy_auto_activation_enabled": True,
    "ai_beta_global_enabled": True,
    "daily_brief_enabled": False,
    "auto_title_translation_enabled": False,
    "paper_fulltext_enabled": False,
    "user_sources_enabled": False,
    "remote_sync_enabled": False,
}
CHECKS = ("implementation", "migration", "ui", "cross_review", "speech_smoke", "paid_replay")
CONDITIONS = (
    ("AI for Science", [["AI", "人工智能"], ["science", "科学研究", "科学发现"]], []),
    ("企业内网 Agent", [["agent", "智能体"], ["enterprise", "企业"], ["intranet", "内网", "on-premise", "私有化"]], []),
    ("个人智能体长期记忆", [["agent", "智能体"], ["personal", "个人"], ["memory", "记忆"]], ["GPU memory", "显存"]),
    ("端侧小模型优化", [["small model", "小模型", "SLM"], ["on-device", "端侧", "edge"], ["optimization", "优化", "quantization", "量化"]], []),
    ("浏览器 Agent 权限与确认", [["browser", "浏览器"], ["agent", "智能体"], ["permission", "权限", "confirmation", "确认"]], []),
    ("Apple 人工智能（歧义）", [["Apple", "苹果公司"], ["AI", "人工智能", "machine learning"]], ["apple pie", "苹果派"]),
    ("播客生产部署评测与失败", [["deployment", "部署", "production", "生产"], ["evaluation", "评测", "failure", "失败"]], []),
    ("无结果负例", [["量子独角兽缝纫机"], ["海底火星水稻收割"]], []),
)


def now():
    return dt.datetime.now(UTC)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


@contextlib.contextmanager
def lock(root):
    from services.file_lock import LOCK_EX, flock
    with (root / ".runner.lock").open("a+b") as handle:
        flock(handle, LOCK_EX)
        yield


def owned(root):
    root = Path(root).resolve()
    state = json.loads((root / "manifest.json").read_text())
    if state.get("issue") != 298 or state.get("root") != str(root):
        raise ValueError("Not this runner's isolated experiment directory")
    expected = root / "private" / "observation.db"
    if state.get("database") != str(expected) or expected.is_symlink():
        raise ValueError("Experiment database ownership mismatch")
    return state


def config_path(root):
    return root / "private" / "runtime.ini"


def read_config(root):
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path(root))
    state = owned(root)
    if config.get("storage", "database_url") != "sqlite:///" + state["database"]:
        raise ValueError("Runtime database differs from the frozen owned database")
    if Path(config.get("bailian_speech", "tts_receipt_root")).resolve() != root / "private" / "receipts":
        raise ValueError("Do not detach the speech receipts from this run's ledger")
    return config


def save_config(root, config):
    path = config_path(root)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        config.write(handle)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def clean_environment(root):
    # An inherited deployment override must never redirect this runner's writes.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DORAMI_", "UVICORN_"))}
    env.update(DORAMI_CONFIG_FILE=str(config_path(root)), PYTHONPATH=str(REPO / "src"))
    return env


def flags(root, enabled):
    state = owned(root)
    with sqlite3.connect(state["database"]) as connection:
        for key, on in FLAGS.items():
            connection.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (key, "true" if enabled and on else "false"))
        connection.execute("INSERT INTO app_settings(key,value) VALUES ('auto_title_translation_daily_tokens','0') ON CONFLICT(key) DO UPDATE SET value='0'")
        connection.execute("UPDATE collection_jobs SET is_active=0")
        if enabled:
            for job_id in state["collection_job_ids"]:
                connection.execute("UPDATE collection_jobs SET is_active=1 WHERE id=?", (job_id,))
        connection.commit()


def prepare(root, source_manifest, *, port=8099, frontend_port=5199):
    root = Path(root).resolve()
    source_manifest = Path(source_manifest).resolve()
    source_hash = digest(source_manifest)
    if not 1 <= port <= 65535 or not 0 <= frontend_port <= 65535 or frontend_port == port:
        raise ValueError("Choose distinct valid loopback backend/frontend ports")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root):
        if (root / "manifest.json").exists():
            state = owned(root)
            if state["source_manifest_sha256"] != source_hash:
                raise ValueError("Source manifest changed; explicitly reconcile instead of replacing a run")
            if state["phase"] == "preparing":
                raise ValueError("Interrupted preparation needs explicit inspection; no state was replaced")
            return state
        if any(p.name != ".runner.lock" for p in root.iterdir()):
            raise ValueError("Prepare requires an empty directory; never adopts a prior database")
        private = root / "private"
        private.mkdir(mode=0o700)
        (private / "receipts").mkdir(mode=0o700)
        (private / "artifacts").mkdir(mode=0o700)
        (root / "logs").mkdir(mode=0o700)
        data = json.loads(source_manifest.read_text())
        sources = [{k: s.get(k) for k in ("source_id", "name", "category", "url", "format", "checked_at", "status")}
                   for s in data["sources"]]
        if not sources or len({s["source_id"] for s in sources}) != len(sources):
            raise ValueError("Source identities must be nonempty and unique")
        write_json(root / "sources.json", sources)
        stamp = now().isoformat()
        run_id = "issue-298-" + now().strftime("%Y%m%d") + "-" + secrets.token_hex(3)
        state = {"schema_version": 1, "issue": 298, "run_id": run_id, "root": str(root),
                 "database": str(private / "observation.db"), "source_manifest_sha256": source_hash,
                 "source_ids": [s["source_id"] for s in sources], "source_snapshot_sha256": digest(root / "sources.json"),
                 "created_at": stamp, "phase": "preparing",
                 "started_at": None, "ends_at": None, "paid_authorized_until": None,
                 "tts_daily_budget_minor": 2000, "frontend_port": frontend_port,
                 "review_url": f"http://127.0.0.1:{frontend_port}/review/taxonomy" if frontend_port else None,
                 "condition_version": 1, "collection_job_ids": [], "scenario_accounts": {},
                 "conditions": [{"name": n, "all_of": [{"any_of": g} for g in groups], "exclude": exclusions}
                                for n, groups, exclusions in CONDITIONS], "stop_verification": [],
                 "criteria": {"hours": 72, "scheduled_briefs_per_positive_account": 3, "successful_sources": 24,
                              "historical_candidates": 300, "exact_merge_reviewed": 30, "exact_merge_errors": 0,
                              "interest_review_pairs": 100, "negative_pairs": 40, "composite_negative_pairs": 20,
                              "composite_negative_core_errors": 0, "core_precision": 0.9,
                              "same_snapshot_improved_conditions": 3, "semantic_relation_precision": 0.9,
                              "notification_window_delay_seconds": 300,
                              "semantic_relation_pairs": 50, "official_transcripts": 3, "real_asr_episodes": 3,
                              "tts_guides": 6}}
        write_json(root / "manifest.json", state)
        config = configparser.ConfigParser(interpolation=None)
        config.read_dict({
            "server": {"host": "127.0.0.1", "port": str(port), "reload": "false"},
            "runtime": {"role": "all"}, "storage": {"database_url": "sqlite:///" + state["database"]},
            "auth": {"cookie_name": run_id, "cookie_secure": "false", "secret": secrets.token_hex(32)},
            "taxonomy": {"deployment": "manual", "governance_enabled": "false", "exact_merge_enabled": "false", "review_enabled": "false", "feishu_config_path": ""},
            "llm": {"base_url": "", "api_key": "", "model": "", "paid_authorized_until": EXPIRED},
            "media": {"enabled": "false", "media_dir": str(private / "media")},
            "podcast_artifacts": {"root_dir": str(private / "artifacts")},
            "podcast": {"installation": "external", "authority_id": run_id, "processing_enabled": "false",
                        "allowed_stages": "fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish",
                        "provider_ready_targets": "transcript,full_analysis,digest_blog,digest_audio", "monthly_budget_cny_minor": "12000",
                        "per_run_budget_cny_minor": "1500", "budget_scope": run_id, "voice_profiles": "narrator_zh", "default_voice_profile": "narrator_zh"},
            "bailian_speech": {"enabled": "false", "api_key": "", "account_scope": "", "asr_model": "fun-asr", "tts_model": "qwen3-tts-flash",
                               "asr_entitlement_ends_at": EXPIRED, "tts_entitlement_ends_at": EXPIRED,
                               "asr_daily_audio_seconds_limit": "21600", "tts_daily_budget_minor": "2000",
                               "tts_monthly_budget_minor": "8000", "tts_per_run_budget_minor": "1000",
                               "tts_receipt_root": str(private / "receipts")},
        })
        save_config(root, config)
        # Import runtime modules only after selecting the private, disabled config.
        os.environ["DORAMI_CONFIG_FILE"] = str(config_path(root))
        from storage.migrations import ensure_migrated
        from storage.impl.db_storage import DatabaseStorage
        from sqlmodel import Session, select
        from models.db import CollectionJobRecord, SourceConfigRecord, CmsTagRecord, ReaderSubscriptionRecord, UserInterestTagRecord
        from services import accounts, podcast_catalog, source_rows, taxonomy_deployment
        from services.custom_interests import ConditionInput, save_condition, bump_revision
        ensure_migrated("sqlite:///" + state["database"])
        sink = DatabaseStorage("sqlite:///" + state["database"])
        taxonomy_deployment.reconcile_approved_taxonomy_v1(sink.engine)
        podcast_catalog.ensure_default_podcast_sources(sink.engine)
        source_rows.run_startup_reconcile(sink.engine)
        passwords = {}
        with Session(sink.engine) as session:
            for scenario in ("admin", "formal", "custom_only", "mixed", "permission", "negative"):
                username = "i298-" + scenario.replace("_", "-")
                password = secrets.token_urlsafe(24)
                user = accounts.create_user(session, username, password, "admin" if scenario == "admin" else "user")
                user.ai_beta_enabled = scenario != "permission"
                session.add(user)
                accounts.complete_interest_onboarding(session, username)
                passwords[username] = password
                state["scenario_accounts"][scenario] = username
            tags = session.exec(select(CmsTagRecord).where(CmsTagRecord.status == "active", CmsTagRecord.user_selectable.is_(True))).all()
            formal = [t for t in tags if any(term in (t.name_zh + t.name_en + t.code).lower() for term in ("agent", "智能体", "machine learning", "大语言"))][:3]
            if not formal:
                raise ValueError("Approved catalogue has no suitable baseline interests")
            state["formal_tag_ids"] = [t.id for t in formal]
            for scenario in ("formal", "mixed"):
                username = state["scenario_accounts"][scenario]
                for tag in formal:
                    session.add(UserInterestTagRecord(owner_username=username, tag_id=tag.id, created_at=stamp, updated_at=stamp))
                bump_revision(session, username)
                for source in sources:
                    sid = source["source_id"]
                    if session.get(SourceConfigRecord, sid) is None:
                        raise ValueError("Source is not in installed curated catalogue: " + sid)
                    session.add(ReaderSubscriptionRecord(owner_username=username, name=source["name"],
                        filters_json=json.dumps({"source_id": sid}), token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                        created_at=stamp, updated_at=stamp))
            session.commit()
            for scenario in ("custom_only", "mixed", "permission", "negative"):
                for item in state["conditions"][-1:] if scenario == "negative" else state["conditions"]:
                    save_condition(session, state["scenario_accounts"][scenario], ConditionInput(
                        name=item["name"], condition={"all_of": item["all_of"], "exclude": item["exclude"]}))
            for source in sources:
                sid = source["source_id"]
                row = session.get(SourceConfigRecord, sid)
                row.ai_analysis_enabled = True
                session.add(row)
                job = CollectionJobRecord(name=run_id + ":" + sid, description="Isolated #298 observation",
                    fetcher_ids_json=json.dumps([sid]), params_json=json.dumps({"limit": 20}),
                    cron_expr="17 */6 * * *" if source["category"] == "podcast" else "7 */2 * * *",
                    is_active=False, created_at=stamp, updated_at=stamp)
                session.add(job); session.flush(); state["collection_job_ids"].append(job.id)
            session.commit()
        sink.engine.dispose()
        write_json(private / "accounts.json", passwords)
        state["phase"] = "prepared"
        write_json(root / "manifest.json", state)
        flags(root, False)
        return state


def publish_catalog(root):
    """Publish only the already reviewed repository catalog in the owned DB."""
    with lock(root):
        state = owned(root)
        config = read_config(root)
        if state["phase"] != "prepared" or state["started_at"]:
            raise ValueError("Catalogue publication must precede observation")
        if config.get("taxonomy", "deployment") != "manual":
            raise ValueError("The isolated experiment must remain local/manual")
        if config.getboolean("podcast", "processing_enabled") or config.getboolean("bailian_speech", "enabled"):
            raise ValueError("Finish speech smoke and disable paid providers first")
        for section, key in (("llm", "paid_authorized_until"), ("bailian_speech", "asr_entitlement_ends_at"), ("bailian_speech", "tts_entitlement_ends_at")):
            if now() < dt.datetime.fromisoformat(config.get(section, key)):
                raise ValueError("Paid authorization must be expired before catalogue preparation")
        os.environ["DORAMI_CONFIG_FILE"] = str(config_path(root))
        from sqlmodel import Session, create_engine
        from services import taxonomy, taxonomy_deployment, taxonomy_bootstrap
        from services.sync_consumer_policy import taxonomy_governed_remotely
        engine = create_engine("sqlite:///" + state["database"])
        try:
            with Session(engine) as session:
                if taxonomy_governed_remotely(session):
                    raise ValueError("Remote taxonomy cannot be published by this experiment")
                # Re-check the existing receipt against the exact reviewed catalog.
                taxonomy_deployment.validate_catalog_session(session, taxonomy_deployment.load_catalog())
                active = taxonomy.current_taxonomy_version(session)
                if active not in {0, 1}:
                    raise ValueError("Unexpected taxonomy baseline version")
                if not active:
                    readiness_state = taxonomy.taxonomy_governance_state(session)
                    if not readiness_state["publish_ready"]:
                        raise ValueError("Reviewed catalogue is not ready for publication")
                result = taxonomy_bootstrap.publish_taxonomy_v1(session, actor_id="issue-298-approved-catalog",
                    now=now(), change_summary="Isolated #298 baseline: existing reviewed repository v1 catalogue")
                session.commit()
        finally:
            engine.dispose()
        state["taxonomy_baseline_version"] = result["taxonomy_version"]
        write_json(root / "manifest.json", state)
        write_json(root / "catalog-publication.json", {**result, "at": now().isoformat(), "new_concepts_approved": 0})
        return {"taxonomy_version": result["taxonomy_version"], "new_concepts_approved": 0, "phase": state["phase"]}


def verify_local_baseline(root, config):
    if config.get("taxonomy", "deployment") != "manual":
        raise ValueError("This isolated run requires its frozen local/manual taxonomy")
    with sqlite3.connect("file:" + owned(root)["database"] + "?mode=ro", uri=True) as db:
        version = db.execute("SELECT version FROM taxonomy_versions WHERE status='active'").fetchone()
        remote = db.execute("SELECT value FROM app_settings WHERE key='taxonomy:authority_id'").fetchone()
    if not version or (remote and remote[0].strip()):
        raise ValueError("Publish the reviewed local catalogue before enabling exact governance")
    return int(version[0])


def metrics(root):
    state = owned(root)
    with sqlite3.connect("file:" + state["database"] + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        def rows(sql):
            return [dict(row) for row in db.execute(sql)]
        data = {"at": now().isoformat(), "articles": rows("SELECT source_id,count(*) AS count FROM articles GROUP BY source_id"),
                "analysis": rows("SELECT status,count(*) AS count FROM article_analyses GROUP BY status"),
                "matches": rows("SELECT outcome,count(*) AS count FROM interest_article_matches GROUP BY outcome"),
                "semantic_scans": rows("SELECT status,count(*) AS count,sum(page_count) AS reviewed_pages FROM interest_semantic_scans GROUP BY status"),
                "semantic_requests": rows("SELECT status,count(*) AS count,sum(input_bytes) AS input_bytes FROM interest_semantic_requests GROUP BY status"),
                "briefs": rows("SELECT owner_username,report_date,generation_reason,status FROM personal_digest_editions"),
                "candidates": rows("SELECT status,count(*) AS count FROM cms_tag_candidates GROUP BY status"),
                "proposals": rows("SELECT status,count(*) AS count FROM review_proposals GROUP BY status"),
                "notifications": rows("SELECT status,count(*) AS count FROM review_notification_batches GROUP BY status"),
                "tts": rows("SELECT budget_day,status,count(*) AS calls,sum(cost_minor) AS cost_minor FROM bailian_tts_calls GROUP BY budget_day,status"),
                "podcast_cost": rows("SELECT stage,sum(actual_cost_minor) AS cost_minor FROM podcast_cost_ledger GROUP BY stage"),
                "ai_usage": rows("SELECT day,purpose,sum(calls) AS calls,sum(total_tokens) AS tokens FROM ai_usage GROUP BY day,purpose"),
                "provider_attempts": rows("SELECT stage,submission_state,count(*) AS count FROM podcast_stage_attempts WHERE execution_kind='provider' GROUP BY stage,submission_state")}
    data["source_count"] = sum(r["source_id"] in state["source_ids"] and r["count"] > 0 for r in data["articles"])
    data["submission_counter"] = {"tts": sum(r["calls"] for r in data["tts"] if r["status"] != "reserved"),
                                  "provider": sum(r["count"] for r in data["provider_attempts"]),
                                  "semantic_authorizations": sum(r["count"] for r in data["semantic_requests"]),
                                  "llm_metered_calls": sum(r["calls"] for r in data["ai_usage"])}
    return data


def status(root):
    state = owned(root)
    current = metrics(root)
    started = dt.datetime.fromisoformat(state["started_at"]) if state["started_at"] else None
    end = dt.datetime.fromisoformat(state.get("stopped_at") or now().isoformat())
    return {"run_id": state["run_id"], "phase": state["phase"], "started_at": state["started_at"],
            "ends_at": state["ends_at"], "elapsed_hours": (end - started).total_seconds() / 3600 if started else 0,
            "metrics": current, "stop_verification": state["stop_verification"],
            "observation_gaps": state.get("observation_gaps", [])}


def readiness(path):
    evidence = json.loads(Path(path).read_text())
    for name in CHECKS:
        check = evidence.get("checks", {}).get(name, {})
        target = Path(check.get("evidence", ""))
        if check.get("passed") is not True or not target.is_file() or digest(target) != check.get("sha256"):
            raise ValueError("Missing or changed readiness evidence: " + name)
    return evidence


def code_fingerprint():
    """Freeze executable inputs, including uncommitted implementation changes."""
    result = hashlib.sha256()
    paths = [Path(__file__).resolve()]
    for directory in ("src", "alembic", "frontend/src", "frontend/dist", "frontend/pwa"):
        paths.extend(p for p in (REPO / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".map"})
    paths.extend(REPO / "frontend" / name for name in ("package.json", "package-lock.json", "vite.config.js", "app.config.json") if (REPO / "frontend" / name).is_file())
    for path in sorted(paths):
        result.update(str(path.relative_to(REPO)).encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def validate_runtime(root, config):
    """Parse proposed config in a clean process without booting any scheduler."""
    path = root / "private" / "validate-runtime.ini"
    try:
        with path.open("w", encoding="utf-8") as handle:
            config.write(handle)
        path.chmod(0o600)
        env = clean_environment(root)
        env["DORAMI_CONFIG_FILE"] = str(path)
        result = subprocess.run([sys.executable, "-c", "import config"], cwd=REPO,
                                env=env, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError("Proposed runtime config failed validation; no tasks were enabled")
    finally:
        path.unlink(missing_ok=True)


def start(root, readiness_path):
    with lock(root):
        state = owned(root)
        if state["phase"] not in {"prepared", "running"}:
            raise ValueError("A stopped run cannot be silently restarted or extended")
        readiness(readiness_path)
        config = read_config(root)
        baseline_version = verify_local_baseline(root, config)
        if digest(root / "sources.json") != state["source_snapshot_sha256"]:
            raise ValueError("Frozen source snapshot changed")
        if state["phase"] == "running" and digest(config_path(root)) != state.get("runtime_config_sha256"):
            raise ValueError("Runtime configuration changed after T0; investigate before resume")
        if (root / "stop-request.json").exists():
            raise ValueError("A stop request cannot be cleared by starting another worker")
        if len(state["source_ids"]) < 24:
            raise ValueError("Real observation requires at least 24 frozen sources")
        for section, keys in {"llm": ("base_url", "api_key", "model"), "bailian_speech": ("api_key", "account_scope")}.items():
            if any(not config.get(section, key, fallback="").strip() for key in keys):
                raise ValueError("Complete private credentials before live start: " + section)
        if state["phase"] == "prepared":
            beginning = now()
            state.update(phase="running", started_at=beginning.isoformat(),
                         ends_at=(beginning + dt.timedelta(hours=72)).isoformat())
            state["paid_authorized_until"] = state["ends_at"]
            state["readiness_sha256"] = digest(readiness_path)
        elif state.get("readiness_sha256") != digest(readiness_path):
            raise ValueError("Restart must use the original frozen readiness evidence")
        if now() >= dt.datetime.fromisoformat(state["ends_at"]):
            raise ValueError("Run authorization has expired; stop/report instead")
        for section, key in (("llm", "paid_authorized_until"), ("bailian_speech", "asr_entitlement_ends_at"), ("bailian_speech", "tts_entitlement_ends_at")):
            config.set(section, key, state["ends_at"])
        config.set("bailian_speech", "tts_daily_budget_minor", "2000")
        for key in ("governance_enabled", "exact_merge_enabled", "review_enabled"):
            config.set("taxonomy", key, "true")
        config.set("podcast", "processing_enabled", "true")
        config.set("bailian_speech", "enabled", "true")
        fingerprint = code_fingerprint()
        if state.get("code_sha256") and state["code_sha256"] != fingerprint:
            raise ValueError("Executable inputs changed after T0; resume requires investigation")
        validate_runtime(root, config)
        if state["phase"] == "running" and not owned(root).get("started_at"):
            for port in [config.getint("server", "port"), state.get("frontend_port", 0)]:
                if port:
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", port))
            if state.get("frontend_port"):
                source_dist = REPO / "frontend" / "dist"
                if not (source_dist / "index.html").is_file():
                    raise ValueError("Build and verify frontend/dist before live start")
                frozen_dist = root / "private" / "frontend-dist"
                if frozen_dist.exists():
                    shutil.rmtree(frozen_dist)
                shutil.copytree(source_dist, frozen_dist)
        state["code_sha256"] = fingerprint
        state["taxonomy_baseline_version"] = baseline_version
        state["enabled_feature_flags"] = dict(FLAGS)
        save_config(root, config)
        state["runtime_config_sha256"] = digest(config_path(root))
        write_json(root / "manifest.json", state)
        flags(root, True)
        return state


def stop(root):
    with lock(root):
        state = owned(root)
        config = read_config(root)
        for section, key in (("llm", "paid_authorized_until"), ("bailian_speech", "asr_entitlement_ends_at"), ("bailian_speech", "tts_entitlement_ends_at")):
            config.set(section, key, EXPIRED)
        config.set("podcast", "processing_enabled", "false")
        config.set("bailian_speech", "enabled", "false")
        for key in ("governance_enabled", "exact_merge_enabled", "review_enabled"):
            config.set("taxonomy", key, "false")
        save_config(root, config)
        flags(root, False)
        if state["phase"] != "stopped":
            state["phase"] = "stopping"
            state.setdefault("stop_requested_at", now().isoformat())
        write_json(root / "stop-request.json", {"requested_at": state["stop_requested_at"]})
        write_json(root / "manifest.json", state)
        return state


def verify_stopped(root, *, worker_stopped=False, minimum_seconds=65):
    """Two spaced counter samples are evidence; writing stop flags alone is not."""
    with lock(root):
        state = owned(root)
        if state["phase"] not in {"stopping", "stopped"}:
            raise ValueError("Stop must be requested first")
        sample = metrics(root)
        prior = state["stop_verification"]
        if prior and (now() - dt.datetime.fromisoformat(prior[-1]["at"])).total_seconds() < minimum_seconds:
            return state
        point = {"at": sample["at"], "counters": sample["submission_counter"], "owned_worker_stopped": worker_stopped}
        prior.append(point)
        if len(prior) >= 2 and all(p["owned_worker_stopped"] for p in prior[-2:]) and prior[-1]["counters"] == prior[-2]["counters"]:
            state.update(phase="stopped", stopped_at=point["at"])
        write_json(root / "manifest.json", state)
        return state


def report(root):
    snapshot = status(root)
    state = owned(root)
    write_json(root / "metrics.json", snapshot)
    with (root / "source-counts.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "count"])
        counts = {row["source_id"]: row["count"] for row in snapshot["metrics"]["articles"]}
        writer.writeheader()
        writer.writerows({"source_id": sid, "count": counts.get(sid, 0)} for sid in state["source_ids"])
    criteria = [
        ("自然观察时长", snapshot["elapsed_hours"], 72),
        ("成功入库来源", snapshot["metrics"]["source_count"], 24),
        ("停止后零提交核验", 2 if state["phase"] == "stopped" else 0, 2),
    ]
    rows = "".join("<tr><td>" + html.escape(str(k)) + "</td><td>" + html.escape(str(v)) + "</td><td>" + str(t) + "</td><td>" + ("达到数量门槛" if v >= t else "证据不足") + "</td></tr>" for k, v, t in criteria)
    document = "<!doctype html><meta charset=utf-8><title>#298 观察报告</title><style>body{font:16px/1.7 system-ui;max-width:980px;margin:3em auto;padding:0 1em}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #bbb;text-align:left;padding:.5em}pre{white-space:pre-wrap}</style>"
    document += "<h1>#298 自动观察报告</h1><p>运行 " + html.escape(state["run_id"]) + "；状态 " + html.escape(state["phase"]) + "。</p>"
    document += "<table><tr><th>项目</th><th>观测值</th><th>门槛</th><th>结论</th></tr>" + rows + "</table>"
    document += "<details><summary>冻结验收门槛（未提供独立证据的项目仍为待验证）</summary><pre>" + html.escape(json.dumps(state["criteria"], ensure_ascii=False, indent=2)) + "</pre></details>"
    document += "<p>计数只说明运行规模。核心相关正确率、归并正确性、语义关系质量及人工审核闭环须附独立样本审阅；尚无审阅证据时均为待验证，不自动宣称方案有效。</p>"
    document += "<p>TTS、ASR/播客费用和 LLM token 分列。这里是本地记账与保守预占，不能冒充供应商账单；未知请求保留且不重提。</p><pre>" + html.escape(json.dumps(snapshot, ensure_ascii=False, indent=2)) + "</pre>"
    (root / "report.html").write_text(document, encoding="utf-8")
    return {"report": str(root / "report.html"), "metrics": str(root / "metrics.json"), "phase": state["phase"]}


def live_recorded_workers(state):
    """Conservatively detect a recorded PID; never kill a possibly reused PID."""
    alive = []
    for name, pid in state.get("owned_worker_pids", {}).items():
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        alive.append(name)
    return alive


def arm(root, ready_path):
    """Explicit final handoff; an installed agent alone cannot start T0."""
    with lock(root):
        state = owned(root)
        if state["phase"] != "prepared" or (root / "stop-request.json").exists():
            raise ValueError("Only an untouched prepared run may be armed")
        readiness(ready_path)
        config = read_config(root)
        validate_runtime(root, config)
        verify_local_baseline(root, config)
        for section, keys in {"llm": ("base_url", "api_key", "model"), "bailian_speech": ("api_key", "account_scope")}.items():
            if any(not config.get(section, key, fallback="").strip() for key in keys):
                raise ValueError("Complete credentials before arming")
        authorization = {"run_id": state["run_id"], "armed_at": now().isoformat(),
                         "readiness_path": str(Path(ready_path).resolve()), "readiness_sha256": digest(ready_path),
                         "config_sha256": digest(config_path(root)), "code_sha256": code_fingerprint()}
        write_json(root / "armed.json", authorization)
        return {"phase": "armed", "note": "The next agent tick may start the fixed 72h window."}


def agent_tick(root, ready_path):
    """Idempotent launchd entry; sleeping/restarting never moves the deadline."""
    state = owned(root)
    result = {"run_id": state["run_id"], "at": now().isoformat(), "phase": state["phase"]}
    if state["phase"] == "prepared" and not (root / "armed.json").exists():
        result["action"] = "waiting_for_explicit_arm"
    elif state["phase"] == "stopped":
        result["action"] = "already_stopped"
    elif state["phase"] == "stopping" or (root / "stop-request.json").exists() or (
            state.get("ends_at") and now() >= dt.datetime.fromisoformat(state["ends_at"])):
        stop(root)
        alive = live_recorded_workers(state)
        if alive:
            result.update(action="waiting_for_owned_workers", worker_names=alive)
        else:
            verified = verify_stopped(root, worker_stopped=True)
            report(root)
            result.update(action="stop_verification", phase=verified["phase"])
    elif live_recorded_workers(state):
        result["action"] = "owned_workers_already_running"
    else:
        try:
            if state["phase"] == "prepared":
                authorization = json.loads((root / "armed.json").read_text())
                expected = {"run_id": state["run_id"], "readiness_path": str(Path(ready_path).resolve()),
                            "readiness_sha256": digest(ready_path), "config_sha256": digest(config_path(root)),
                            "code_sha256": code_fingerprint()}
                if any(authorization.get(key) != value for key, value in expected.items()):
                    raise ValueError("Armed inputs changed before T0")
            supervise(root, ready_path)
            result.update(action="supervision_finished", phase=owned(root)["phase"])
        except BlockingIOError:
            result["action"] = "supervisor_already_running"
        except Exception as exc:
            # Fail closed for an interrupted live run; preserve paid receipts.
            # A never-started run stays prepared and cannot bypass the same gate.
            if owned(root)["started_at"]:
                stop(root)
            result.update(action="blocked_prerequisite", error=type(exc).__name__)
    write_json(root / "agent-status.json", result)
    return result


def launchagent_spec(root, ready_path):
    state = owned(root)
    node_directory = str(Path(shutil.which("node") or "/usr/bin/node").parent)
    return {"Label": "org.dorami." + state["run_id"],
            "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
                                 "agent-tick", "--readiness", str(Path(ready_path).resolve())],
            "WorkingDirectory": str(REPO), "StartInterval": 75, "RunAtLoad": False,
            "KeepAlive": False, "AbandonProcessGroup": False, "ExitTimeOut": 120,
            "ProcessType": "Background", "Umask": 0o077,
            "EnvironmentVariables": {"PATH": node_directory + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(root / "logs/launchagent.log"),
            "StandardErrorPath": str(root / "logs/launchagent-error.log")}


def install_agent(root, ready_path):
    """Install only this run's agent. Prepared/unarmed remains a no-op."""
    if sys.platform != "darwin":
        raise ValueError("LaunchAgent installation requires macOS")
    spec = launchagent_spec(root, ready_path)
    directory = Path.home() / "Library/LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (spec["Label"] + ".plist")
    if path.exists() and plistlib.loads(path.read_bytes()) != spec:
        raise ValueError("Existing agent differs; inspect it before replacing")
    path.write_bytes(plistlib.dumps(spec)); path.chmod(0o600)
    domain = "gui/" + str(os.getuid())
    present = subprocess.run(["launchctl", "print", domain + "/" + spec["Label"]], capture_output=True).returncode == 0
    if not present:
        loaded = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        if loaded.returncode:
            raise ValueError("LaunchAgent bootstrap failed")
    with lock(root):
        state = owned(root)
        state["launchagent"] = {"label": spec["Label"], "plist": str(path), "readiness": str(Path(ready_path).resolve())}
        write_json(root / "manifest.json", state)
    return {"installed": True, "label": spec["Label"], "phase": state["phase"], "armed": (root / "armed.json").exists()}


def supervise(root, ready_path, *, tick_seconds=30):
    """Own one app process; never daemonize duplicate workers or extend T0."""
    from services.file_lock import LOCK_EX, LOCK_NB, flock
    with (root / ".supervisor.lock").open("a+b") as lease:
        flock(lease, LOCK_EX | LOCK_NB)
        state = start(root, ready_path)
        config = read_config(root)
        stop_signal = [False]
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop_signal.__setitem__(0, True))
        with (root / "logs" / "runtime.log").open("a") as log:
            children = []
            try:
                child = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", str(REPO / "src"), "api.app:app",
                                          "--host", "127.0.0.1", "--port", config.get("server", "port")],
                                         cwd=REPO, env=clean_environment(root), stdout=log, stderr=subprocess.STDOUT)
                children.append(("backend", child))
                if state.get("frontend_port"):
                    frontend_env = clean_environment(root)
                    frontend_env["VITE_PROXY_TARGET"] = "http://127.0.0.1:" + config.get("server", "port")
                    ui = subprocess.Popen(["node", str(REPO / "frontend/node_modules/vite/bin/vite.js"), "preview",
                                           "--outDir", str(root / "private/frontend-dist"), "--host", "127.0.0.1",
                                           "--port", str(state["frontend_port"]), "--strictPort"],
                                          cwd=REPO / "frontend", env=frontend_env, stdout=log, stderr=subprocess.STDOUT)
                    children.append(("frontend", ui))
                with lock(root):
                    state = owned(root)
                    state["owned_worker_pids"] = {name: process.pid for name, process in children}
                    state["worker_started_at"] = now().isoformat()
                    write_json(root / "manifest.json", state)
                while all(process.poll() is None for _, process in children) and not stop_signal[0] and not (root / "stop-request.json").exists():
                    if now() >= dt.datetime.fromisoformat(state["ends_at"]):
                        break
                    with lock(root):
                        latest = owned(root)
                        current = now()
                        previous = latest.get("heartbeat_at")
                        if previous and (current - dt.datetime.fromisoformat(previous)).total_seconds() > tick_seconds * 3:
                            latest.setdefault("observation_gaps", []).append({"from": previous, "to": current.isoformat(),
                                "reason": "heartbeat_gap_sleep_or_process_restart", "deadline_extended": False})
                        latest["heartbeat_at"] = current.isoformat()
                        write_json(root / "manifest.json", latest)
                    snapshot = status(root)
                    write_json(root / "latest-status.json", snapshot)
                    with (root / "observations.jsonl").open("a") as handle:
                        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                    time.sleep(tick_seconds)
            finally:
                stop(root)
                for _, process in children:
                    if process.poll() is None:
                        process.terminate()
                for _, process in children:
                    try: process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=10)
                verify_stopped(root, worker_stopped=True)
                time.sleep(65)
                verify_stopped(root, worker_stopped=True)
                report(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--sources", type=Path, required=True); p.add_argument("--port", type=int, default=8099); p.add_argument("--frontend-port", type=int, default=5199, help="Loopback preview port; 0 disables frontend")
    for action in ("run", "arm", "agent-tick", "install-agent"):
        p = sub.add_parser(action); p.add_argument("--readiness", type=Path, required=True)
    for name in ("status", "stop", "report", "publish-catalog"):
        sub.add_parser(name)
    args = parser.parse_args(); root = args.root.resolve()
    try:
        if args.command == "prepare":
            result = prepare(root, args.sources, port=args.port, frontend_port=args.frontend_port)
            result = {"run_id": result["run_id"], "phase": result["phase"], "source_count": len(result["source_ids"]), "config": str(config_path(root))}
        elif args.command == "run":
            supervise(root, args.readiness); result = report(root)
        elif args.command == "publish-catalog": result = publish_catalog(root)
        elif args.command == "arm": result = arm(root, args.readiness)
        elif args.command == "agent-tick": result = agent_tick(root, args.readiness)
        elif args.command == "install-agent": result = install_agent(root, args.readiness)
        elif args.command == "status": result = status(root)
        elif args.command == "stop":
            state = stop(root); result = {"phase": state["phase"], "note": "Supervisor must stop its owned worker and collect two spaced samples before stopped is proven."}
        else: result = report(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Never print configparser/provider exception payloads or secret values.
        print(json.dumps({"error": type(exc).__name__, "message": "Observation command failed; inspect sanitized local state and prerequisites."}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
