"""Admin API coverage for editable Podcast ASR duration limits."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from models.db import AdminAuditRecord, AppSettingRecord, UserRecord
from services import accounts as accounts_service
from services.credentials import ALIYUN_ISI_NAMESPACE
from services.podcast_source_media import SourceMediaTooLong
from storage.impl.db_storage import DatabaseStorage
from config_bailian import BailianSpeechConfig


def _setup(monkeypatch, tmp_path):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'asr-quota.db'}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = dt.datetime.now().isoformat()
    with Session(sink.engine) as session:
        for username, role in (("admin", "admin"), ("user", "user")):
            session.add(
                UserRecord(
                    username=username,
                    password_hash=accounts_service.hash_password(username),
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    return app_module, sink


def _login(client, username):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": username},
    )
    assert response.status_code == 200


def test_admin_can_read_and_update_independent_asr_audio_limits(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        initial = client.get("/api/admin/podcast-asr-quota")
        assert initial.status_code == 200
        assert set(initial.json()) == {
            "provider",
            "configured",
            "daily_audio_seconds_limit",
            "daily_audio_hours_limit",
            "max_audio_seconds_per_file",
            "max_audio_hours_per_file",
            "quota_scope",
            "quota_timezone",
            "source",
            "max_audio_per_file_source",
            "quota_period",
            "quota_window_start_at",
            "quota_window_end_at",
            "used_audio_seconds",
            "reserved_audio_seconds",
            "remaining_audio_seconds",
            "usage_status",
            "usage_reason",
        }
        assert initial.json()["daily_audio_seconds_limit"] == 144_000
        assert initial.json()["daily_audio_hours_limit"] == 40
        assert initial.json()["max_audio_seconds_per_file"] == 10_800
        assert initial.json()["max_audio_hours_per_file"] == 3
        if initial.json()["usage_status"] not in {"available", "frozen"}:
            assert initial.json()["used_audio_seconds"] is None
            assert initial.json()["reserved_audio_seconds"] is None
            assert initial.json()["remaining_audio_seconds"] is None

        updated = client.put(
            "/api/admin/podcast-asr-quota",
            json={
                "daily_audio_seconds_limit": 36_000,
                "max_audio_seconds_per_file": 21_600,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["daily_audio_seconds_limit"] == 36_000
        assert updated.json()["daily_audio_hours_limit"] == 10
        assert updated.json()["source"] == "runtime_kv"
        assert updated.json()["max_audio_seconds_per_file"] == 21_600
        assert updated.json()["max_audio_hours_per_file"] == 6
        assert updated.json()["max_audio_per_file_source"] == "runtime_kv"

    key = ALIYUN_ISI_NAMESPACE.field_by_name(
        "asr_daily_audio_seconds_limit"
    ).kv_key
    max_key = ALIYUN_ISI_NAMESPACE.field_by_name(
        "asr_max_audio_seconds_per_file"
    ).kv_key
    with Session(sink.engine) as session:
        assert session.get(AppSettingRecord, key).value == "36000"
        assert session.get(AppSettingRecord, max_key).value == "21600"
        rows = session.exec(select(AdminAuditRecord)).all()
        assert len(rows) == 1
        assert rows[0].username == "admin"
        assert rows[0].target == "podcast-asr-quota"
        assert rows[0].summary == "更新播客 ASR 配额：每日 40h → 10h；单集 3h → 6h"


def test_asr_daily_audio_limit_is_admin_only_positive_and_not_wall_clock_bounded(
    monkeypatch, tmp_path
):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "user")
        assert client.get("/api/admin/podcast-asr-quota").status_code == 403
        assert client.put(
            "/api/admin/podcast-asr-quota",
            json={"daily_audio_seconds_limit": 36_000},
        ).status_code == 403
        _login(client, "admin")
        assert client.put(
            "/api/admin/podcast-asr-quota",
            json={"daily_audio_seconds_limit": 0},
        ).status_code == 422
        assert client.put(
            "/api/admin/podcast-asr-quota",
            json={
                "daily_audio_seconds_limit": 3_599,
                "max_audio_seconds_per_file": 3_600,
            },
        ).status_code == 422
        # Omitting the per-episode field still validates against its effective
        # current value (3 hours by default), rather than accepting an invalid pair.
        assert client.put(
            "/api/admin/podcast-asr-quota",
            json={"daily_audio_seconds_limit": 3_600},
        ).status_code == 422
        assert client.put(
            "/api/admin/podcast-asr-quota",
            json={
                "daily_audio_seconds_limit": 36_000,
                "max_audio_seconds_per_file": 43_201,
            },
        ).status_code == 422
        large = client.put(
            "/api/admin/podcast-asr-quota",
            json={
                "daily_audio_seconds_limit": 100 * 3_600,
                "max_audio_seconds_per_file": 43_200,
            },
        )
        assert large.status_code == 200, large.text
        assert large.json()["daily_audio_hours_limit"] == 100


def test_runtime_single_file_limit_applies_to_source_media_validation_immediately(
    monkeypatch, tmp_path
):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    import api.routers.podcasts as podcasts_router

    monkeypatch.setattr(
        app_module, "podcast_artifact_store", SimpleNamespace(engine=object())
    )
    observed_limits: list[int] = []

    async def reject_overlong_source(*_args, max_audio_seconds_per_file, **_kwargs):
        observed_limits.append(max_audio_seconds_per_file)
        if max_audio_seconds_per_file < 2:
            raise SourceMediaTooLong("Podcast enclosure 超过 ASR 单任务音频时长上限")
        raise AssertionError("test expects the runtime limit to be applied")

    monkeypatch.setattr(
        podcasts_router, "validate_source_media", reject_overlong_source
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        saved = client.put(
            "/api/admin/podcast-asr-quota",
            json={
                "daily_audio_seconds_limit": 36_000,
                "max_audio_seconds_per_file": 1,
            },
        )
        assert saved.status_code == 200, saved.text
        response = client.post(
            "/api/admin/podcast-episodes/episode-runtime-limit/validate-source-media"
        )

    assert response.status_code == 422
    assert response.json()["code"] == "podcast_source_media_too_long"
    assert observed_limits == [1]


def test_quota_readback_uses_bailian_window_and_default_limits(monkeypatch, tmp_path):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    import api.routers.podcasts as podcasts_router

    config = BailianSpeechConfig(
        api_key="test-key",
        account_scope="account-a",
        asr_entitlement_ends_at="2030-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        podcasts_router.podcast_speech_config_service,
        "resolve_config",
        lambda _session: config,
    )
    monkeypatch.setattr(
        podcasts_router.podcast_speech_config_service,
        "field_sources",
        lambda _session: {
            "asr_daily_audio_seconds_limit": "default",
            "asr_max_audio_seconds_per_file": "default",
        },
    )
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        response = client.get("/api/admin/podcast-asr-quota")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provider"] == "bailian"
    assert payload["daily_audio_hours_limit"] == 40
    assert payload["max_audio_hours_per_file"] == 3
    assert payload["usage_status"] == "available"
    assert payload["quota_timezone"] == "Asia/Singapore"
    assert payload["quota_window_end_at"]
