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


SENTINEL = "FAKE_LLM_SECRET_SENTINEL"


def test_config_parse_error_never_echoes_the_offending_line(tmp_path):
    """漏个等号的 `api_key …` 行:configparser 的 ParsingError 自带整行原文,不得进报告。"""
    ini = tmp_path / "leak.ini"
    ini.write_text(
        "[storage]\n"
        f"database_url = sqlite:///{tmp_path / 'leak.db'}\n"
        "[llm]\n"
        f"api_key {SENTINEL}\n",
        encoding="utf-8",
    )
    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    dumped = json.dumps(report, ensure_ascii=False)
    assert SENTINEL not in dumped
    assert any("ParsingError" in m and "第 4 行" in m for m in report["errors"]), report["errors"]

    for mode in ("--check-config", "--plan-migrations"):
        result = _run_entrypoint(tmp_path, ini, mode)
        assert result.returncode in (1, 2), result
        assert SENTINEL not in result.stdout + result.stderr, mode
        assert json.loads(result.stdout)["status"] == "error", mode


def test_database_url_errors_never_echo_the_connection_string(tmp_path):
    """带 % 与密码的 URL:alembic/sqlalchemy 的异常常含完整连接串,不得进报告。"""
    ini = tmp_path / "url.ini"
    ini.write_text(
        "[storage]\n"
        f"database_url = postgresql://user:{SENTINEL}%40@localhost/db\n",
        encoding="utf-8",
    )
    report = check_config(config_path=str(ini))
    assert report["status"] == "error", report
    dumped = json.dumps(report, ensure_ascii=False)
    assert SENTINEL not in dumped
    assert report["checks"]["database"]["url"].count("***") >= 1

    for mode in ("--check-config", "--plan-migrations"):
        result = _run_entrypoint(tmp_path, ini, mode)
        assert result.returncode in (1, 2), result
        assert SENTINEL not in result.stdout + result.stderr, mode
        assert json.loads(result.stdout)["status"] == "error", mode


def test_config_value_errors_never_echo_the_raw_value(tmp_path, monkeypatch):
    """config 自己抛的 ValueError 也可能整段带原值:误缩进让 configparser 把 secret 行并进 role 值。"""
    ini = tmp_path / "indent.ini"
    ini.write_text(
        "[storage]\n"
        f"database_url = sqlite:///{tmp_path / 'indent.db'}\n"
        "[runtime]\n"
        "role = all\n"
        " [auth]\n"
        f" secret = {SENTINEL}\n",
        encoding="utf-8",
    )
    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    dumped = json.dumps(report, ensure_ascii=False)
    assert SENTINEL not in dumped
    assert any("ValueError" in m and "runtime role" in m for m in report["errors"]), report["errors"]
    for mode in ("--check-config", "--plan-migrations"):
        result = _run_entrypoint(tmp_path, ini, mode)
        assert result.returncode in (1, 2), result
        assert SENTINEL not in result.stdout + result.stderr, mode
        assert json.loads(result.stdout)["status"] == "error", mode

    # 环境变量转换失败(int())同样只回前缀
    clean = _write_ini(tmp_path, db_name="envval.db")
    env_sentinel = "FAKE_ENV_SECRET_SENTINEL"
    monkeypatch.setenv("DORAMI_PODCAST_FEED_MAX_BYTES", env_sentinel)
    report = check_config(config_path=str(clean))
    assert report["status"] == "error", report
    assert env_sentinel not in json.dumps(report, ensure_ascii=False)


def test_redact_url_masks_password_and_secret_query_params():
    from services.deploy_checks import _redact_url

    masked = _redact_url("postgresql://user:hunter2@localhost/db?password=hunter2&sslmode=require&token=abc")
    assert "hunter2" not in masked
    assert "abc" not in masked
    assert "sslmode=require" in masked


def test_authority_database_query_failure_still_yields_json_report(tmp_path):
    """只有 alembic_version 表(schema 漂移 / 缺表):状态校验的 OperationalError 也要收成结构化报告。"""
    ini = _write_ini(tmp_path, db_name="drift.db")
    ini.write_text(ini.read_text(encoding="utf-8").replace("deployment = manual", "deployment = authority"), encoding="utf-8")
    db_path = tmp_path / "drift.db"
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(make_alembic_config()).get_current_head()
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE articles (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})
    finally:
        engine.dispose()

    report = check_config(config_path=str(ini))
    assert report["status"] == "error"
    assert report["checks"]["taxonomy"]["database_state"]["status"] == "error"
    assert any(m.startswith("taxonomy: 数据库状态校验失败") for m in report["errors"]), report["errors"]

    result = _run_entrypoint(tmp_path, ini, "--check-config")
    assert result.returncode == 1, result
    assert json.loads(result.stdout)["status"] == "error"
    assert "Traceback" not in result.stderr


def test_tilde_config_path_is_expanded_like_the_loader(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ini = _write_ini(home, db_name="tilde.db")
    report = check_config(config_path="~/check.ini")
    assert report["status"] == "ok", report
    missing = check_config(config_path="~/nope.ini")
    assert missing["status"] == "error"
    assert any("不存在" in m for m in missing["errors"])


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
