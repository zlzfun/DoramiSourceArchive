"""`docker/entrypoint.py --check-config` / `--plan-migrations` 的只读自检(issue #102,PR-1)。

锁定:配置缺失 / 非法姿态报成 JSON 错误而非 traceback;安全检查按 posture 分级;authority 姿态下
DB 已在目标 revision 集合时跑只读 taxonomy 校验(冲突报错、无冲突不写一行);DB 落后时跳过;
以及「不 import api.app」这条纪律(子进程验证)。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import CmsTagRecord  # noqa: E402
from services import taxonomy  # noqa: E402
from services.deploy_checks import check_config, run_cli  # noqa: E402
from storage.migrations import BASELINE_REVISION, make_alembic_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _write_ini(tmp_path: Path, *, db_name: str = "check.db", extra: str = "") -> Path:
    ini = tmp_path / "check.ini"
    ini.write_text(
        "[storage]\n"
        f"database_url = sqlite:///{tmp_path / db_name}\n"
        "[auth]\n"
        "cookie_secure = false\n"
        "secret = unit-test-secret-long-enough-0123456789\n"
        "[taxonomy]\n"
        "deployment = manual\n"
        + extra,
        encoding="utf-8",
    )
    return ini


def _upgrade(db_url: str, target: str = "head") -> None:
    command.upgrade(make_alembic_config(db_url), target)


def test_dev_posture_manual_taxonomy_is_ok_and_reports_fresh_plan(tmp_path):
    ini = _write_ini(tmp_path)
    report = check_config(config_path=str(ini))
    assert report["status"] == "ok", report
    assert report["errors"] == []
    assert report["checks"]["security"]["posture"] == "dev"
    assert report["checks"]["taxonomy"] == {"mode": "manual", "catalog": None, "database_state": None}
    assert report["checks"]["migrations"]["status"] == "fresh"
    assert not (tmp_path / "check.db").exists(), "自检不得把库建出来"


def test_missing_config_file_is_an_error_not_a_silent_default(tmp_path):
    report = check_config(config_path=str(tmp_path / "nope.ini"))
    assert report["status"] == "error"
    assert any(m.startswith("config:") and "不存在" in m for m in report["errors"])


def test_invalid_taxonomy_mode_is_reported_as_config_error(tmp_path):
    ini = tmp_path / "bad.ini"
    ini.write_text("[taxonomy]\ndeployment = automatic\n", encoding="utf-8")
    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    assert any(m.startswith("config:") and "taxonomy" in m.lower() for m in report["errors"])


def test_production_posture_with_placeholder_secret_is_an_error(tmp_path):
    ini = tmp_path / "prod.ini"
    ini.write_text(
        "[storage]\n"
        f"database_url = sqlite:///{tmp_path / 'prod.db'}\n"
        "[auth]\n"
        "cookie_secure = true\n"
        "secret = change-me\n",
        encoding="utf-8",
    )
    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    assert report["checks"]["security"]["posture"] == "production"
    assert any(m.startswith("security:") for m in report["errors"])


def test_authority_with_database_at_head_validates_readonly_without_writing(tmp_path):
    ini = _write_ini(tmp_path, db_name="auth.db", extra="")
    ini.write_text(ini.read_text(encoding="utf-8").replace("deployment = manual", "deployment = authority"), encoding="utf-8")
    db_url = f"sqlite:///{tmp_path / 'auth.db'}"
    _upgrade(db_url)

    report = check_config(config_path=str(ini))
    assert report["status"] == "ok", report
    assert report["checks"]["taxonomy"]["catalog"]["entries"] == 96
    assert report["checks"]["migrations"]["status"] == "compatible"
    assert report["checks"]["taxonomy"]["database_state"]["status"] == "install_required"
    assert report["checks"]["taxonomy"]["database_state"]["missing"] == 96

    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            assert session.exec(select(CmsTagRecord)).first() is None, "只读校验不得安装任何 tag"
    finally:
        engine.dispose()


def test_authority_conflicting_tag_is_reported_without_touching_database(tmp_path):
    ini = _write_ini(tmp_path, db_name="conflict.db")
    ini.write_text(ini.read_text(encoding="utf-8").replace("deployment = manual", "deployment = authority"), encoding="utf-8")
    db_url = f"sqlite:///{tmp_path / 'conflict.db'}"
    _upgrade(db_url)
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            taxonomy.create_tag(
                session,
                code="topic.not-in-catalog",
                kind="topic",
                name_zh="目录外标签",
                name_en="outside",
                description="",
                prompt_description="",
                status="active",
                user_selectable=True,
                filterable=True,
                recommendable=True,
                activation_mode="manual",
                entity_type="",
                external_key="",
            )
            session.commit()
    finally:
        engine.dispose()

    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    assert report["checks"]["taxonomy"]["database_state"] == {"status": "conflict"}
    assert any("outside approved v1" in m for m in report["errors"])


def test_authority_with_database_behind_head_skips_taxonomy_state(tmp_path):
    ini = _write_ini(tmp_path, db_name="behind.db")
    ini.write_text(ini.read_text(encoding="utf-8").replace("deployment = manual", "deployment = authority"), encoding="utf-8")
    db_url = f"sqlite:///{tmp_path / 'behind.db'}"
    _upgrade(db_url, BASELINE_REVISION)

    report = check_config(config_path=str(ini))
    assert report["status"] == "ok", report
    assert report["checks"]["migrations"]["status"] == "compatible"
    assert report["checks"]["migrations"]["pending_count"] > 0
    assert report["checks"]["taxonomy"]["database_state"]["status"] == "skipped"


def test_run_cli_exit_codes(tmp_path, capsys, monkeypatch):
    ini = _write_ini(tmp_path, db_name="cli.db")
    monkeypatch.setenv("DORAMI_CONFIG_FILE", str(ini))

    assert run_cli(["--check-config"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"

    assert run_cli(["--plan-migrations"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "fresh"

    assert run_cli(["--bogus"]) == 2


def _run_entrypoint(tmp_path: Path, ini: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DORAMI_CONFIG_FILE"] = str(ini)
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, str(ROOT / "docker" / "entrypoint.py"), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_entrypoint_check_config_mode_prints_json_and_exits_zero(tmp_path):
    ini = _write_ini(tmp_path, db_name="entry.db")
    result = _run_entrypoint(tmp_path, ini, "--check-config")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert not (tmp_path / "entry.db").exists()


def test_entrypoint_plan_migrations_mode_reports_incompatible_with_exit_one(tmp_path):
    ini = _write_ini(tmp_path, db_name="plan.db")
    db_url = f"sqlite:///{tmp_path / 'plan.db'}"
    _upgrade(db_url)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("UPDATE alembic_version SET version_num = 'deadbeefcafe'"))
    finally:
        engine.dispose()
    result = _run_entrypoint(tmp_path, ini, "--plan-migrations")
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "incompatible"


def test_check_config_never_imports_the_api_app(tmp_path):
    """纪律:自检只 import 无副作用模块——api.app 装配阶段会建 storage、种账号。"""
    ini = _write_ini(tmp_path, db_name="noapp.db")
    env = dict(os.environ)
    env["DORAMI_CONFIG_FILE"] = str(ini)
    env["PYTHONPATH"] = str(SRC)
    probe = (
        "import sys\n"
        "from services.deploy_checks import check_config\n"
        "report = check_config()\n"
        "assert report['status'] == 'ok', report\n"
        "print('api.app' in sys.modules, 'api' in sys.modules and hasattr(sys.modules['api'], 'app'))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False False"
