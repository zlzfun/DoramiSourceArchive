"""构建来源透出(tag 即发布波):version.build_info 的来源优先级与 /api/runtime 载荷形状。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import version


def _reset():
    version._BUILD_INFO = None


def test_env_takes_precedence_over_git(monkeypatch):
    _reset()
    monkeypatch.setenv("DORAMI_BUILD_REF", "v9.9.9")
    monkeypatch.setenv("DORAMI_BUILD_SHA", "abc123def")
    info = version.build_info()
    assert info == {"ref": "v9.9.9", "sha": "abc123def", "source": "env"}
    _reset()


def test_git_fallback_or_unknown_never_raises(monkeypatch):
    _reset()
    monkeypatch.delenv("DORAMI_BUILD_REF", raising=False)
    monkeypatch.delenv("DORAMI_BUILD_SHA", raising=False)
    info = version.build_info()
    assert info["source"] in {"git", "unknown"}
    assert set(info) == {"ref", "sha", "source"}
    if info["source"] == "git":
        assert info["sha"]
    else:
        assert info == {"ref": "", "sha": "", "source": "unknown"}
    _reset()


def test_build_info_is_cached_and_copied(monkeypatch):
    _reset()
    monkeypatch.setenv("DORAMI_BUILD_REF", "v1.0.0")
    monkeypatch.setenv("DORAMI_BUILD_SHA", "s")
    first = version.build_info()
    first["ref"] = "mutated"
    monkeypatch.setenv("DORAMI_BUILD_REF", "v2.0.0")
    assert version.build_info()["ref"] == "v1.0.0"  # 缓存且返回副本
    _reset()


def test_runtime_capabilities_carry_build(monkeypatch):
    _reset()
    monkeypatch.setenv("DORAMI_BUILD_REF", "v3.55.0")
    monkeypatch.setenv("DORAMI_BUILD_SHA", "deadbeef")
    from api import app as app_module

    caps = app_module.runtime_capabilities(None)
    assert caps["build"] == {"ref": "v3.55.0", "sha": "deadbeef", "source": "env"}
    assert caps["version"] == version.__version__
    _reset()
