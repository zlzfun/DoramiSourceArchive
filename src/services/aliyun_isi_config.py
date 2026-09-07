"""Resolve Aliyun ISI settings through the shared credential registry."""

from __future__ import annotations

from sqlmodel import Session

import config
from models.db import AppSettingRecord
from services import credentials


NAMESPACE = credentials.ALIYUN_ISI_NAMESPACE


def resolve_config(session: Session) -> config.AliyunIsiConfig:
    """Return the effective local config without logging or serializing secrets."""

    values = credentials.resolve_values(session, NAMESPACE, config.settings.aliyun_isi)
    return config.AliyunIsiConfig(**values)


def field_sources(session: Session) -> dict[str, str]:
    """Expose provenance only; callers must not return resolved secret values."""

    return credentials.field_sources(session, NAMESPACE)


def persist_refreshed_token(
    session: Session,
    *,
    access_token: str,
    expires_at: int,
) -> None:
    """Atomically persist a refreshed token/expiry pair in local runtime KV."""

    value = str(access_token or "").strip()
    expiry = int(expires_at)
    if not value or expiry <= 0:
        raise ValueError("refreshed Aliyun ISI token and expiry are required")
    updates = {
        NAMESPACE.field_by_name("access_token").kv_key: value,
        NAMESPACE.field_by_name("token_expires_at").kv_key: str(expiry),
    }
    try:
        for key, text in updates.items():
            record = session.get(AppSettingRecord, key)
            if record is None:
                record = AppSettingRecord(key=key, value=text)
            else:
                record.value = text
            session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        raise
