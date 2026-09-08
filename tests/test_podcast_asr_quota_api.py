"""Admin API coverage for the editable Podcast ASR daily limit."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlmodel import Session

from models.db import AppSettingRecord, UserRecord
from services import accounts as accounts_service
from services.credentials import ALIYUN_ISI_NAMESPACE
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


def test_admin_can_read_and_update_asr_daily_audio_limit(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client, "admin")
        initial = client.get("/api/admin/podcast-asr-quota")
        assert initial.status_code == 200
        assert set(initial.json()) == {
            "daily_audio_seconds_limit",
            "daily_audio_hours_limit",
            "quota_scope",
            "quota_timezone",
            "source",
        }

        updated = client.put(
            "/api/admin/podcast-asr-quota",
            json={"daily_audio_seconds_limit": 36_000},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["daily_audio_seconds_limit"] == 36_000
        assert updated.json()["daily_audio_hours_limit"] == 10
        assert updated.json()["source"] == "runtime_kv"

    key = ALIYUN_ISI_NAMESPACE.field_by_name(
        "asr_daily_audio_seconds_limit"
    ).kv_key
    with Session(sink.engine) as session:
        assert session.get(AppSettingRecord, key).value == "36000"


def test_asr_daily_audio_limit_is_admin_only_and_bounded(monkeypatch, tmp_path):
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
            json={"daily_audio_seconds_limit": 86_401},
        ).status_code == 422
