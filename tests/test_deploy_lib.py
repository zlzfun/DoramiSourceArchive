"""scripts/deploy-lib.sh 的行为守卫(tag 即发布波):在临时 git 仓库里驱动 resolve_deploy_ref。

覆盖:无参数选版本号最新的 tag(不是字母序/时间序)、指定版本、tag 与代码版本号不一致即拒绝、
入库文件有手改即拒绝、--here 不切换且标注非发布版、切换后重执行的脚本看到的是 tag 的内容。
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIB = os.path.join(ROOT, "scripts", "deploy-lib.sh")

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="需要 git 与 bash")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write_version(repo, v):
    os.makedirs(os.path.join(repo, "src"), exist_ok=True)
    with open(os.path.join(repo, "src", "version.py"), "w", encoding="utf-8") as fh:
        fh.write(f'__version__ = "{v}"\n')


# 一个最小的「部署脚本」:source 库 → 解析参数 → 打印结果。切 tag 后会被 exec 重跑,
# 所以它自身也入库(与真实 deploy.sh 同形);内容随 tag 变化用来验证「重执行看到的是新文件」。
DEPLOY = """#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
source scripts/deploy-lib.sh
resolve_deploy_ref "$@"
echo "MODE=$DORAMI_DEPLOY_MODE REF=$DORAMI_BUILD_REF SHA=$DORAMI_BUILD_SHA SCRIPT=%s VERSION=$(grep -o '"[^"]*"' src/version.py)"
"""


def _commit_release(repo, version, script_marker, tag=True):
    _write_version(repo, version)
    with open(os.path.join(repo, "deploy.sh"), "w", encoding="utf-8") as fh:
        fh.write(DEPLOY % script_marker)
    os.chmod(os.path.join(repo, "deploy.sh"), 0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"release {version}")
    if tag:
        _git(repo, "tag", "-a", f"v{version}", "-m", f"v{version}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    work = str(work)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    os.makedirs(os.path.join(work, "scripts"))
    shutil.copy(LIB, os.path.join(work, "scripts", "deploy-lib.sh"))
    return work


def _run(repo, *args, env=None):
    full_env = dict(os.environ, **(env or {}))
    for key in ("DORAMI_BUILD_REF", "DORAMI_BUILD_SHA", "DORAMI_DEPLOY_MODE", "DORAMI_DEPLOY_REEXEC"):
        full_env.pop(key, None)
    return subprocess.run(["bash", "./deploy.sh", *args], cwd=repo, capture_output=True, text=True, env=full_env)


def test_default_picks_highest_version_tag_and_reexecs(repo):
    _commit_release(repo, "3.9.0", "A")
    sha_high = _commit_release(repo, "3.10.0", "B")   # 版本号最大,但字母序排在 3.9 前
    _commit_release(repo, "3.10.1", "C", tag=False)   # main 上未发布的提交
    _git(repo, "push", "-q", "origin", "HEAD", "--tags")
    res = _run(repo)
    assert res.returncode == 0, res.stderr
    assert "MODE=tag REF=v3.10.0" in res.stdout
    assert f"SHA={sha_high}" in res.stdout
    assert "SCRIPT=B" in res.stdout and 'VERSION="3.10.0"' in res.stdout  # 重执行的是 tag 那份脚本
    assert _git(repo, "rev-parse", "HEAD") == sha_high


def test_explicit_version_with_or_without_v_prefix(repo):
    sha = _commit_release(repo, "1.0.0", "A")
    _commit_release(repo, "1.1.0", "B")
    for spec in ("v1.0.0", "1.0.0"):
        res = _run(repo, spec)
        assert res.returncode == 0, res.stderr
        assert "REF=v1.0.0" in res.stdout and f"SHA={sha}" in res.stdout


def test_unknown_tag_lists_available(repo):
    _commit_release(repo, "1.0.0", "A")
    res = _run(repo, "v7.7.7")
    assert res.returncode != 0
    assert "tag 不存在: v7.7.7" in res.stderr and "v1.0.0" in res.stderr


def test_tag_version_mismatch_is_refused(repo):
    _commit_release(repo, "1.0.0", "A")
    _git(repo, "tag", "-a", "v1.0.1", "-m", "手工打在没 bump 的提交上")
    res = _run(repo, "v1.0.1")
    assert res.returncode != 0
    assert "版本号是 1.0.0" in res.stderr


def test_dirty_tracked_file_is_refused_but_untracked_is_fine(repo):
    _commit_release(repo, "1.0.0", "A")
    _commit_release(repo, "1.1.0", "B")
    with open(os.path.join(repo, "production.ini"), "w") as fh:  # 未跟踪:允许
        fh.write("x")
    assert _run(repo, "v1.0.0").returncode == 0
    with open(os.path.join(repo, "deploy.sh"), "a") as fh:  # 入库文件手改:拒绝
        fh.write("# hand edit\n")
    res = _run(repo, "v1.1.0")
    assert res.returncode != 0
    assert "工作树有对入库文件的修改" in res.stderr


def test_here_mode_never_checks_out(repo):
    _commit_release(repo, "1.0.0", "A")
    head = _commit_release(repo, "1.0.1", "B", tag=False)
    res = _run(repo, "--here")
    assert res.returncode == 0, res.stderr
    assert "MODE=here" in res.stdout and f"SHA={head}" in res.stdout
    assert "REF=v1.0.0-1-g" in res.stdout  # describe 后缀 = 非发布版
    assert "不是发布版" in res.stdout
    assert _git(repo, "rev-parse", "HEAD") == head


def test_here_with_version_or_unknown_flag_is_usage_error(repo):
    _commit_release(repo, "1.0.0", "A")
    assert _run(repo, "--here", "v1.0.0").returncode != 0
    res = _run(repo, "--bogus")
    assert res.returncode != 0 and "未知参数" in res.stderr


def test_backup_sqlite_db_keeps_ten(tmp_path):
    import sqlite3

    db = tmp_path / "cms.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t(x)")
        conn.execute("insert into t values (1)")
    script = f'set -euo pipefail; cd "{tmp_path}"; source "{LIB}"; for i in $(seq 1 12); do backup_sqlite_db cms.db >/dev/null; sleep 0.01; done'
    # 同一秒内文件名会重复,拿 12 次循环只验证「不超过 10 份 + 内容可读」
    subprocess.run(["bash", "-c", script], check=True, env={**os.environ, "PATH": os.environ["PATH"]})
    backups = sorted((tmp_path / "backups").glob("cms.db.*"))
    assert 1 <= len(backups) <= 10
    with sqlite3.connect(backups[-1]) as conn:
        assert conn.execute("select x from t").fetchone() == (1,)


# ── issue #227:探针地址读 .env、迁移宽限公式 ──

def _lib_run(cwd, script, env):
    return subprocess.run(["bash", "-c", f'set -euo pipefail; cd "{cwd}"; source "{LIB}"; {script}'],
                          capture_output=True, text=True, env=env)


FAKE_DOCKER_CONFIG = """#!/usr/bin/env python3
import os, sys
if os.environ.get("FAKE_DOCKER_FAIL"):
    sys.exit(1)
sys.stdout.write(open(os.environ["FAKE_COMPOSE_JSON"]).read())
"""


def test_compose_helpers_read_published_port_and_start_period(tmp_path):
    """探针端口与 healthcheck 宽限都从 `docker compose config --format json` 取(codex R1 F1 / F2):host_ip 空 / 0.0.0.0 → 127.0.0.1、
    published 范围取首个、只认 target 80;Go 时长串折秒;缺字段 / docker 失败 → 空输出 + 返回 1。"""
    import json
    fakebin = tmp_path / "bin"; fakebin.mkdir()
    (fakebin / "docker").write_text(FAKE_DOCKER_CONFIG); (fakebin / "docker").chmod(0o755)
    cfg = tmp_path / "cfg.json"
    script = ('p="$(deploy_compose_published_hostport nginx 80 || echo "rc=$?")"; s="$(deploy_compose_healthcheck_start_period_seconds backend || echo "rc=$?")"; '
              'printf "[%s] [%s]\\n" "$p" "$s"')

    def run(obj, fail=False):
        cfg.write_text(json.dumps(obj))
        env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}", "FAKE_COMPOSE_JSON": str(cfg)}
        if fail:
            env["FAKE_DOCKER_FAIL"] = "1"
        out = _lib_run(tmp_path, script, env)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def compose(ports, start_period):
        return {"services": {"backend": {"healthcheck": {"start_period": start_period}}, "nginx": {"ports": ports}}}

    assert run(compose([{"target": 80, "published": "8080", "host_ip": "127.0.0.1"}], "1m0s")) == "[127.0.0.1:8080] [60]"
    assert run(compose([{"target": 443, "published": "8443"}, {"target": 80, "published": "8080-8081", "host_ip": "0.0.0.0"}], "20m0s")) == "[127.0.0.1:8080] [1200]"
    assert run(compose([{"target": 80, "published": "80"}], "1h2m3s")) == "[127.0.0.1:80] [3723]"
    assert run(compose([{"target": 80, "published": 8080}], "90s")) == "[127.0.0.1:8080] [90]"
    assert run(compose([{"target": 80, "published": "8080"}], "20m")) == "[127.0.0.1:8080] [1200]"
    assert run(compose([{"target": 443, "published": "8443"}], "")) == "[rc=1] [rc=1]"
    assert run({"services": {"backend": {}, "nginx": {}}}) == "[rc=1] [rc=1]"
    assert run(compose([{"target": 80, "published": "8080"}], "45")) == "[127.0.0.1:8080] [rc=1]", "没有单位的时长不猜"
    assert run(compose([{"target": 80, "published": "8080"}], "1m0s"), fail=True) == "[rc=1] [rc=1]"


def test_migration_grace_seconds_formula_cap_and_knobs(tmp_path):
    (tmp_path / "small.db").write_bytes(b"x" * 1024)
    with open(tmp_path / "big.db", "wb") as fh:
        fh.truncate(int(1.5 * 1024 ** 3))   # 稀疏文件,不占盘:ceil(1.5 GiB) = 2
    conf = tmp_path / "deploy.conf"; conf.write_text("DORAMI_DEPLOY_HEALTH_PER_MIGRATION_SECONDS=10\n", encoding="utf-8")
    script = ('for args in "0 small.db" "abc small.db" "6 small.db" "6 big.db" "6 missing.db" "500 small.db"; do '
              'set -- $args; printf "%s %s=%s\\n" "$1" "$2" "$(deploy_migration_grace_seconds "$1" "$2")"; done')
    clean = {k: v for k, v in os.environ.items() if not k.startswith("DORAMI_DEPLOY_")}
    out = _lib_run(tmp_path, script, {**clean, "DORAMI_DEPLOY_CONF": str(tmp_path / "absent.conf")})
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["0 small.db=0", "abc small.db=0", "6 small.db=360", "6 big.db=720", "6 missing.db=360", "500 small.db=1200"]
    out = _lib_run(tmp_path, script, {**clean, "DORAMI_DEPLOY_CONF": str(conf)})
    assert out.returncode == 0 and "6 small.db=60" in out.stdout and "6 big.db=120" in out.stdout, out.stdout + out.stderr
    out = _lib_run(tmp_path, script, {**clean, "DORAMI_DEPLOY_CONF": str(conf), "DORAMI_DEPLOY_HEALTH_PER_MIGRATION_SECONDS": "100",
                                      "DORAMI_DEPLOY_HEALTH_MIGRATION_GRACE_MAX_SECONDS": "250"})
    assert out.returncode == 0 and "6 small.db=250" in out.stdout and "6 big.db=250" in out.stdout, "环境变量 > conf;上限也可调"
