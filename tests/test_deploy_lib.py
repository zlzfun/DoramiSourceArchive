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
