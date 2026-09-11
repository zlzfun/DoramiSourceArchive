"""Admin API coverage for editable Podcast ASR duration limits."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session

from models.db import AppSettingRecord, UserRecord
from services import accounts as accounts_service
from services.credentials import ALIYUN_ISI_NAMESPACE
from services.podcast_source_media import SourceMediaTooLong
from storage.impl.db_storage import DatabaseStorage


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
            "daily_audio_seconds_limit",
            "daily_audio_hours_limit",
            "max_audio_seconds_per_file",
            "max_audio_hours_per_file",
            "quota_scope",
            "quota_timezone",
            "source",
            "max_audio_per_file_source",
        }
        assert initial.json()["max_audio_seconds_per_file"] == 43_200
        assert initial.json()["max_audio_hours_per_file"] == 12

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
