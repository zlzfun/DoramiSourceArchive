"""Opt-in cloud configuration must not affect ordinary local installations."""
import configparser
import hashlib
from dataclasses import replace
import os

import pytest

from config_oss import OssConfig, load_oss_config


@pytest.fixture(autouse=True)
def isolated_oss_environment(monkeypatch):
    for key in os.environ:
        if key.startswith("DORAMI_OSS_"):
            monkeypatch.delenv(key)


def configured(text=""):
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return load_oss_config(parser)


VALID = """[oss]
media_backend = oss
bucket = example-media
region = ap-southeast-1
endpoint = https://oss-ap-southeast-1-internal.aliyuncs.com
prefix = prod
credential_provider = ecs_role
ecs_role_name = DoramiMediaOssRole
"""


def test_no_section_is_local_without_connection_parameters():
    config = configured()
    assert config == OssConfig()
    assert not config.enabled
    assert not config.bucket and not config.ecs_role_name and not config.endpoint


def test_local_store_never_contacts_oss_or_metadata(tmp_path, monkeypatch):
    from services.object_storage import ObjectStorage
    from services.oss_credentials import EcsRoleCredentialsProvider
    from storage.impl.db_storage import DatabaseStorage
    import oss2

    def forbidden(*args, **kwargs):
        raise AssertionError("local installation contacted cloud credentials or OSS")

    monkeypatch.setattr(EcsRoleCredentialsProvider, "get_credentials", forbidden)
    monkeypatch.setattr(oss2, "Bucket", forbidden)
    sink = DatabaseStorage(f"sqlite:///{tmp_path / 'local.db'}")
    store = ObjectStorage(sink.engine, tmp_path / "media", "media", configured())
    body = b"local-only-media"
    digest = hashlib.sha256(body).hexdigest()
    path = tmp_path / "media" / digest[:2] / f"{digest}.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    store.persist(path, digest, ".png", len(body), "image/png")
    assert store.materialize(path, digest, ".png", len(body)).read_bytes() == body
    path.unlink()
    assert store.materialize(path, digest, ".png", len(body)) == path
    assert store.stats()["remote_objects"] == 0


def test_unused_bad_cloud_settings_do_not_block_local(monkeypatch):
    monkeypatch.setenv("DORAMI_OSS_CACHE_INTERVAL_SECONDS", "not-an-integer")
    monkeypatch.setenv("DORAMI_OSS_ACCESS_KEY_SECRET", "retained-secret")
    config = configured("""[oss]
credential_provider = misspelled
timeout_seconds =
minimum_free_mb = negative-not-number
cache_enabled = typo
media_cache_max_mb = -100
endpoint = %(missing_variable)s
prefix = ../bad
""")
    assert not config.enabled
    assert config.timeout_seconds == 30 and config.media_cache_max_mb == 2048
    assert config.access_key_secret == "retained-secret"
    assert "retained-secret" not in repr(config)


@pytest.mark.parametrize("backend", ["typo", "", "s3"])
def test_backend_typo_is_never_silently_local(backend):
    with pytest.raises(ValueError, match="backend"):
        configured(f"[oss]\nmedia_backend={backend}\n")


@pytest.mark.parametrize("field, message", [
    ("bucket", "bucket"), ("region", "region"), ("endpoint", "endpoint"),
    ("prefix", "prefix"), ("ecs_role_name", "role name"),
])
def test_enabled_cloud_missing_field_fails_clearly(field, message):
    body = "\n".join(line for line in VALID.splitlines() if not line.startswith(field + " ="))
    with pytest.raises(ValueError, match=message):
        configured(body)


def test_enabled_static_requires_env_credentials(monkeypatch):
    body = VALID.replace("credential_provider = ecs_role", "credential_provider = static")
    with pytest.raises(ValueError, match="credentials"):
        configured(body + "access_key_id = ignored\naccess_key_secret = ignored\n")
    monkeypatch.setenv("DORAMI_OSS_ACCESS_KEY_ID", "environment-id")
    monkeypatch.setenv("DORAMI_OSS_ACCESS_KEY_SECRET", "environment-secret")
    assert configured(body).access_key_id == "environment-id"


def test_cache_defaults_and_environment_override(monkeypatch):
    config = configured(VALID)
    assert config.cache_enabled is True
    assert (config.media_cache_max_mb, config.podcast_cache_max_mb) == (2048, 4096)
    assert (config.cache_interval_seconds, config.cache_min_age_seconds) == (300, 300)
    monkeypatch.setenv("DORAMI_OSS_CACHE_ENABLED", "false")
    monkeypatch.setenv("DORAMI_OSS_PODCAST_CACHE_MAX_MB", "8192")
    config = configured(VALID)
    assert config.cache_enabled is False
    assert config.podcast_cache_max_mb == 8192
    assert replace(config, media_cache_max_mb=0, cache_min_age_seconds=0).enabled


@pytest.mark.parametrize("field,value,message", [
    ("cache_enabled", "maybe", "cache_enabled"),
    ("cache_interval_seconds", "bad", "cache_interval_seconds"),
    ("cache_interval_seconds", "0", "cache interval"),
    ("cache_min_age_seconds", "-1", "minimum age"),
    ("media_cache_max_mb", "-1", "cache size"),
    ("podcast_cache_max_mb", "-1", "cache size"),
])
def test_enabled_invalid_cache_setting_is_rejected(field, value, message):
    with pytest.raises(ValueError, match=message):
        configured(VALID + f"{field} = {value}\n")


def test_empty_compose_overrides_keep_ini_configuration(monkeypatch):
    monkeypatch.setenv("DORAMI_OSS_MEDIA_BACKEND", "")
    monkeypatch.setenv("DORAMI_OSS_CACHE_ENABLED", "")
    assert configured(VALID).media_backend == "oss"
    assert configured(VALID).cache_enabled is True
