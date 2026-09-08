"""Safety boundary tests for the destructive-capable Podcast E2E harness."""

from pathlib import Path

import pytest

from scripts.verify_podcast_all_all_e2e import (
    PROJECT_ROOT,
    _CHILD_ENV_PASSTHROUGH,
    _child_environment,
    assert_isolated_e2e_paths,
)


def test_podcast_e2e_refuses_repository_or_production_paths(tmp_path):
    with pytest.raises(RuntimeError, match="outside the repository"):
        assert_isolated_e2e_paths(
            PROJECT_ROOT,
            PROJECT_ROOT / "data" / "cms_data.db",
        )

    isolated_root = tmp_path / "isolated"
    isolated_root.mkdir()
    with pytest.raises(RuntimeError, match="escapes its temporary root"):
        assert_isolated_e2e_paths(
            isolated_root,
            PROJECT_ROOT / "data" / "cms_data.db",
        )


def test_podcast_e2e_accepts_only_children_of_isolated_root(tmp_path):
    isolated_root = tmp_path / "isolated"
    isolated_root.mkdir()
    assert_isolated_e2e_paths(
        isolated_root,
        isolated_root / "external.db",
        isolated_root / "internal.db",
        isolated_root / "internal-podcast-artifacts",
    )


def test_podcast_e2e_refuses_symlinked_storage_target(tmp_path):
    isolated_root = tmp_path / "isolated"
    isolated_root.mkdir()
    real_target = isolated_root / "real.db"
    real_target.touch()
    linked_target = isolated_root / "linked.db"
    linked_target.symlink_to(real_target)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        assert_isolated_e2e_paths(isolated_root, linked_target)


def test_podcast_e2e_child_environment_never_inherits_provider_secrets(
    monkeypatch, tmp_path
):
    secret_names = (
        "ALIYUN_AK_ID",
        "ALIYUN_AK_SECRET",
        "ALIYUN_SECURITY_TOKEN",
        "NLS_APP_KEY",
        "NLS_ACCESS_TOKEN",
        "NLS_TOKEN_EXPIRES_AT",
        "DORAMI_ALIYUN_ISI_ACCESS_KEY_ID",
        "DORAMI_ALIYUN_ISI_ACCESS_KEY_SECRET",
        "DORAMI_ALIYUN_ISI_APP_KEY",
        "DORAMI_ALIYUN_ISI_ACCESS_TOKEN",
        "DORAMI_LLM_API_KEY",
        "DORAMI_E2E_UNEXPECTED_SECRET",
    )
    for name in secret_names:
        monkeypatch.setenv(name, f"parent-secret-{name}")

    env = _child_environment(
        config=tmp_path / "isolated.ini",
        archive_authority="test-authority",
        installation="external",
        stages=("asr",),
        artifact_root=tmp_path / "artifacts",
    )

    assert all(name not in env for name in secret_names)
    explicit = {
        "DORAMI_CONFIG_FILE",
        "DORAMI_ARCHIVE_AUTHORITY_ID",
        "DORAMI_RUNTIME_ROLE",
        "DORAMI_MEDIA_ENABLED",
        "DORAMI_PODCAST_INSTALLATION",
        "DORAMI_PODCAST_AUTHORITY_ID",
        "DORAMI_PODCAST_ALLOWED_STAGES",
        "DORAMI_PODCAST_ARTIFACT_ROOT_DIR",
        "DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_BYTES",
        "DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_BYTES",
        "PYTHONPATH",
    }
    assert set(env) <= set(_CHILD_ENV_PASSTHROUGH) | explicit
    assert env["DORAMI_PODCAST_ALLOWED_STAGES"] == "asr"
