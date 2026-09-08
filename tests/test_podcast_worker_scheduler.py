"""Focused fail-closed contracts for Podcast ASR scheduler wiring."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace

import pytest

import api.app as app_module
from config import PodcastConfig, PodcastWorkerConfig, RuntimeConfig, load_config
from services.podcast_asr_worker import AsrWorkerStep
from services.podcast_processing_admin import PodcastProcessingProviderRegistry
from services.podcast_stage_policy import PodcastStagePolicy


class _Scheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[object, str, dict]] = {}
        self.removed: list[str] = []

    def add_job(self, callback, trigger, **kwargs):
        self.jobs[kwargs["id"]] = (callback, trigger, kwargs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)


def _external_config() -> PodcastConfig:
    return PodcastConfig(
        installation="external",
        authority_id="stable-external-test",
        allowed_stages=("fetch", "asr"),
        processing_enabled=True,
        provider_ready_targets=("transcript",),
        monthly_budget_cny_minor=10_000,
        per_run_budget_cny_minor=1_000,
    )


def _ready_registry(worker) -> PodcastProcessingProviderRegistry:
    registry = PodcastProcessingProviderRegistry()
    registry.register_target(
        "transcript",
        stage_executors={"asr": lambda _context: None},
        estimator=lambda _metadata: 0,
    )
    registry.register_stage_worker("asr", worker, readiness=lambda _config: True)
    return registry


def _install(monkeypatch, *, registry, runtime_role="all", max_steps=1):
    configured = replace(
        app_module.settings,
        runtime=RuntimeConfig(role=runtime_role),
        podcast=_external_config(),
        podcast_worker=PodcastWorkerConfig(
            tick_seconds=17,
            lease_seconds=83,
            fallback_retry_seconds=29,
            max_steps_per_tick=max_steps,
        ),
    )
    fake_scheduler = _Scheduler()
    monkeypatch.setattr(app_module, "settings", configured)
    monkeypatch.setattr(app_module, "podcast_processing_providers", registry)
    monkeypatch.setattr(app_module, "scheduler", fake_scheduler)
    return fake_scheduler


def test_empty_registry_neither_registers_nor_opens_a_session(monkeypatch):
    scheduler = _install(
        monkeypatch,
        registry=PodcastProcessingProviderRegistry(),
    )
    monkeypatch.setattr(
        app_module,
        "Session",
        lambda *_args, **_kwargs: pytest.fail("empty registry must not open DB"),
    )

    app_module.reload_podcast_asr_worker_schedule()
    result = asyncio.run(app_module.execute_podcast_asr_worker_job())

    assert scheduler.jobs == {}
    assert result == ()


def test_scheduler_depends_on_stage_worker_not_admin_target_readiness(monkeypatch):
    target_only = PodcastProcessingProviderRegistry()
    target_only.register_target(
        "transcript",
        stage_executors={"asr": lambda _context: None},
        estimator=lambda _metadata: 0,
    )
    worker_only = PodcastProcessingProviderRegistry()
    worker_only.register_stage_worker(
        "asr",
        lambda *_args, **_kwargs: AsrWorkerStep("idle"),
        readiness=lambda _config: True,
    )

    scheduler = _install(monkeypatch, registry=target_only)
    app_module.reload_podcast_asr_worker_schedule()
    assert scheduler.jobs == {}

    scheduler = _install(monkeypatch, registry=worker_only)
    app_module.reload_podcast_asr_worker_schedule()
    assert app_module.PODCAST_ASR_WORKER_JOB_ID in scheduler.jobs


@pytest.mark.parametrize("runtime_role", ["all", "reader", "collector"])
def test_registration_uses_stage_policy_not_runtime_role(monkeypatch, runtime_role):
    calls: list[object] = []

    def worker(_session, *, config, policy):
        calls.append((config, policy))
        return AsrWorkerStep("idle")

    scheduler = _install(
        monkeypatch,
        registry=_ready_registry(worker),
        runtime_role=runtime_role,
    )
    before = dt.datetime.now(dt.timezone.utc)

    app_module.reload_podcast_asr_worker_schedule()

    _callback, trigger, kwargs = scheduler.jobs[app_module.PODCAST_ASR_WORKER_JOB_ID]
    assert trigger == "interval"
    assert kwargs["seconds"] == 17
    assert kwargs["next_run_time"] >= before + dt.timedelta(seconds=16)
    assert kwargs["max_instances"] == 1
    assert kwargs["coalesce"] is True
    assert calls == []


def test_disabled_processing_removes_an_existing_job(monkeypatch):
    scheduler = _install(
        monkeypatch,
        registry=_ready_registry(lambda *_args, **_kwargs: AsrWorkerStep("idle")),
    )
    scheduler.jobs[app_module.PODCAST_ASR_WORKER_JOB_ID] = (object(), "interval", {})
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(
            app_module.settings,
            podcast=replace(app_module.settings.podcast, processing_enabled=False),
        ),
    )

    app_module.reload_podcast_asr_worker_schedule()

    assert app_module.PODCAST_ASR_WORKER_JOB_ID in scheduler.removed
    assert scheduler.jobs == {}


def test_tick_passes_config_and_policy_and_stops_when_idle(monkeypatch):
    observed: list[tuple[object, object, object]] = []
    resolved_configs: list[object] = []
    steps = iter((AsrWorkerStep("retry_wait"), AsrWorkerStep("idle")))

    def worker(session, *, config, policy):
        observed.append((session, config, policy))
        return next(steps)

    _install(
        monkeypatch,
        registry=_ready_registry(worker),
        max_steps=5,
    )
    effective_aliyun = app_module.settings.aliyun_isi

    def resolve_effective(_session):
        resolved_configs.append(effective_aliyun)
        return effective_aliyun

    monkeypatch.setattr(
        app_module.aliyun_isi_config_service,
        "resolve_config",
        resolve_effective,
    )

    result = asyncio.run(app_module.execute_podcast_asr_worker_job())

    assert result == ("retry_wait", "idle")
    assert len(observed) == 2
    assert resolved_configs == [effective_aliyun, effective_aliyun]
    worker_config = observed[0][1]
    assert worker_config.worker_id == "podcast-asr:stable-external-test"
    assert worker_config.lease_seconds == 83
    assert worker_config.fallback_retry_seconds == 29
    assert worker_config.next_stage_by_target == {
        "transcript": None,
        "digest_blog": "translate",
    }
    assert isinstance(observed[0][2], PodcastStagePolicy)
    assert observed[0][2].aliyun_isi is effective_aliyun


def test_tick_preserves_completed_actions_and_stops_after_one_step_error(
    monkeypatch,
):
    calls = 0

    def worker(_session, *, config, policy):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AsrWorkerStep("poll_scheduled")
        raise RuntimeError("signed-url-and-provider-body-must-not-be-logged")

    _install(
        monkeypatch,
        registry=_ready_registry(worker),
        max_steps=5,
    )

    result = asyncio.run(app_module.execute_podcast_asr_worker_job())

    assert result == ("poll_scheduled",)
    assert calls == 2


def test_runtime_unready_worker_does_not_claim(monkeypatch):
    worker_calls = 0

    def worker(_session, *, config, policy):
        nonlocal worker_calls
        worker_calls += 1
        return AsrWorkerStep("idle")

    registry = PodcastProcessingProviderRegistry()
    registry.register_stage_worker(
        "asr", worker, readiness=lambda _config: False
    )
    _install(monkeypatch, registry=registry)

    result = asyncio.run(app_module.execute_podcast_asr_worker_job())

    assert result == ()
    assert worker_calls == 0


def test_worker_config_loads_environment_overrides(monkeypatch):
    monkeypatch.setenv("DORAMI_PODCAST_WORKER_TICK_SECONDS", "19")
    monkeypatch.setenv("DORAMI_PODCAST_WORKER_LEASE_SECONDS", "91")
    monkeypatch.setenv("DORAMI_PODCAST_WORKER_HEARTBEAT_SECONDS", "23")
    monkeypatch.setenv("DORAMI_PODCAST_WORKER_FALLBACK_RETRY_SECONDS", "37")
    monkeypatch.setenv("DORAMI_PODCAST_WORKER_MAX_STEPS_PER_TICK", "4")

    worker = load_config().podcast_worker

    assert worker == PodcastWorkerConfig(
        tick_seconds=19,
        lease_seconds=91,
        heartbeat_seconds=23,
        fallback_retry_seconds=37,
        max_steps_per_tick=4,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tick_seconds": 0},
        {"lease_seconds": 0},
        {"heartbeat_seconds": 0},
        {"lease_seconds": 30, "heartbeat_seconds": 30},
        {"fallback_retry_seconds": 0},
        {"max_steps_per_tick": 0},
        {"max_steps_per_tick": 101},
    ],
)
def test_worker_config_rejects_unsafe_bounds(kwargs):
    with pytest.raises(ValueError):
        PodcastWorkerConfig(**kwargs)
