"""scripts/release.sh 的行为守卫(tag 即发布波):临时仓库里走一遍发版,校验三处版本号、tag、
uv.lock 只进索引不动工作区,以及各前置校验的拒绝路径。"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "release.sh")

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="需要 git 与 bash")

UV_LOCK = 'version = 1\n\n[[package]]\nname = "alembic"\nversion = "1.13.0"\n\n[[package]]\nname = "doramisourcearchive"\nversion = "%s"\nsource = { virtual = "." }\n'


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write(repo, rel, content):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = str(tmp_path / "work")
    _git(tmp_path, "clone", "-q", str(origin), work)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    os.makedirs(os.path.join(work, "scripts"))
    shutil.copy(SCRIPT, os.path.join(work, "scripts", "release.sh"))
    _write(work, "src/version.py", '"""doc"""\n\n__version__ = "3.54.0"\n')
    _write(work, "pyproject.toml", '[project]\nname = "doramisourcearchive"\nversion = "3.54.0"\n')
    _write(work, "uv.lock", UV_LOCK % "3.54.0")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "tag", "-a", "v3.54.0", "-m", "v3.54.0")
    _write(work, "feature.txt", "x")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "feat: 某功能")
    _git(work, "push", "-q", "-u", "origin", "main", "--tags")
    return work


def _release(repo, *args):
    return subprocess.run(["bash", "scripts/release.sh", *args], cwd=repo, capture_output=True, text=True)


def test_release_bumps_three_files_tags_and_pushes(repo):
    # 工作区 uv.lock 带「镜像改写」:发版不得把它提交进去,只改索引里的版本行
    _write(repo, "uv.lock", (UV_LOCK % "3.54.0").replace("version = 1\n", "version = 1\n# mirror rewrite\n"))
    res = _release(repo, "3.55.0", "--yes", "-m", "功能波")
    assert res.returncode == 0, res.stderr + res.stdout
    assert "本版内容(v3.54.0..HEAD)" in res.stdout and "- feat: 某功能" in res.stdout

    assert '__version__ = "3.55.0"' in open(os.path.join(repo, "src/version.py")).read()
    assert 'version = "3.55.0"' in open(os.path.join(repo, "pyproject.toml")).read()
    committed_lock = _git(repo, "show", "HEAD:uv.lock")
    assert 'name = "doramisourcearchive"\nversion = "3.55.0"' in committed_lock
    assert "mirror rewrite" not in committed_lock
    assert "mirror rewrite" in open(os.path.join(repo, "uv.lock")).read()  # 工作区原样

    assert _git(repo, "log", "-1", "--format=%s") == "release: v3.55.0"
    assert _git(repo, "cat-file", "-t", "v3.55.0") == "tag"
    body = _git(repo, "tag", "-l", "--format=%(contents)", "v3.55.0")
    assert body.startswith("功能波") and "- feat: 某功能" in body
    assert _git(repo, "rev-parse", "origin/main") == _git(repo, "rev-parse", "HEAD")
    assert "refs/tags/v3.55.0" in _git(repo, "ls-remote", "--tags", "origin")


def test_no_push_keeps_local(repo):
    res = _release(repo, "3.54.1", "--yes", "--no-push")
    assert res.returncode == 0, res.stderr
    assert "未推送" in res.stdout
    assert "refs/tags/v3.54.1" not in _git(repo, "ls-remote", "--tags", "origin")


@pytest.mark.parametrize("version, needle", [
    ("3.54.0", "已存在"),
    ("3.53.9", "必须大于最近的 tag"),
    ("abc", "X.Y.Z"),
])
def test_bad_versions_are_refused(repo, version, needle):
    res = _release(repo, version, "--yes", "--no-push")
    assert res.returncode != 0 and needle in res.stderr


def test_refuses_off_main_dirty_or_unsynced(repo):
    _git(repo, "checkout", "-q", "-b", "feat")
    assert "必须在 main" in _release(repo, "3.55.0", "--yes", "--no-push").stderr
    _git(repo, "checkout", "-q", "main")
    _write(repo, "feature.txt", "changed")
    assert "未提交的修改" in _release(repo, "3.55.0", "--yes", "--no-push").stderr
    _git(repo, "checkout", "-q", "--", "feature.txt")
    _write(repo, "ahead.txt", "y")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ahead")
    assert "不一致" in _release(repo, "3.55.0", "--yes", "--no-push").stderr


def test_existing_remote_tag_is_refused(repo):
    _git(repo, "tag", "-a", "v3.55.0", "-m", "x", "HEAD~1")
    _git(repo, "push", "-q", "origin", "v3.55.0")
    _git(repo, "tag", "-d", "v3.55.0")
    res = _release(repo, "3.55.0", "--yes", "--no-push")
    assert res.returncode != 0 and "远端已存在" in res.stderr
