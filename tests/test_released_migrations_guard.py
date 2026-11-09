"""`scripts/check_released_migrations.py` 的行为守卫(issue #227:已发布迁移文件逐字节不变)。

在临时 bare origin + 工作克隆里驱动脚本:基线 = 远端版本号最新的 vX.Y.Z(不是字母序 / 时间序;`v1.10.0` > `v1.9.0`);
已发布文件改写 / 删除 / 改名 → rc 1 并逐条点名,新增 → rc 0;远端没有正式 tag、基线下没有迁移文件、本地 tag 与远端
不一致 → rc 2(CI 里一样是红,不会被当成通过);CI 形态的 `--depth 1 --no-tags` 克隆能自己 fetch 那一个 tag 且保持浅;
`--offline` 只看本地 tag;`--baseline` 显式。另对真实仓跑一遍 `--offline`(本地没有 v* tag 时跳过,如 CI 的浅检出)。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_released_migrations.py"
VERSIONS = "alembic/versions"


def _git_env(home: Path) -> dict:
    env = dict(os.environ)
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
    return env


class Repo:
    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"; self.home.mkdir()
        self.env = _git_env(self.home)
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(tmp_path, "init", "-q", "-b", "main", str(self.work))
        self.git(self.work, "remote", "add", "origin", str(self.origin))

    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), env=self.env, check=check, capture_output=True, text=True)

    def commit(self, files: dict[str, str | None], tag: str | None = None, message: str = "c") -> str:
        """files 里值为 None 表示删除该路径;其余写入(覆盖)。"""
        for rel, content in files.items():
            p = self.work / rel
            if content is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-qm", message)
        if tag:
            self.git(self.work, "tag", "-a", tag, "-m", tag)
        self.git(self.work, "push", "-q", "origin", "main", "--tags")
        return self.git(self.work, "rev-parse", "HEAD").stdout.strip()

    def clone(self, dest: Path, *extra: str, file_url: bool = False) -> Path:
        # 本地路径克隆会忽略 --depth(git 自己会警告「use file:// instead」),CI 形态的浅克隆用 file:// 走真实传输
        src = f"file://{self.origin}" if file_url else str(self.origin)
        self.git(dest.parent, "clone", "-q", *extra, src, str(dest))
        return dest


def _mig(rev: str, body: str = "") -> str:
    return f'"""rev {rev}"""\nrevision = "{rev}"\n{body}'


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path)
    r.commit({f"{VERSIONS}/0001_base.py": _mig("0001"), f"{VERSIONS}/0002_next.py": _mig("0002"), "src/version.py": '__version__ = "1.0.0"\n'},
             tag="v1.0.0")
    return r


def run(cwd: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)


# ── 正向:一致 / 新增 ──

def test_identical_tree_passes_and_reports_counts(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    r = run(clone, repo.env)
    assert r.returncode == 0, r.stderr
    assert "基线 v1.0.0" in r.stdout and "2 个已发布迁移文件逐字节一致" in r.stdout and "新增" not in r.stdout


def test_new_migration_file_is_allowed_even_uncommitted(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    (clone / VERSIONS / "0003_new.py").write_text(_mig("0003"), encoding="utf-8")
    r = run(clone, repo.env)
    assert r.returncode == 0, r.stderr
    repo.git(clone, "add", "-A")   # 进了索引(CI 里就是提交后的树)同样放行并计数
    r = run(clone, repo.env)
    assert r.returncode == 0 and "新增 1 个" in r.stdout, r.stdout + r.stderr


# ── 反向:改写 / 删除 / 改名 / 类型变化 ──

def test_modified_released_file_fails_and_names_it(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    p = clone / VERSIONS / "0001_base.py"
    p.write_text(p.read_text(encoding="utf-8") + "# docstring 路径更新也不行\n", encoding="utf-8")
    r = run(clone, repo.env)
    assert r.returncode == 1
    assert f"M  {VERSIONS}/0001_base.py" in r.stderr and "改写" in r.stderr and "0002_next.py" not in r.stderr
    assert "一字不改" in r.stderr and "docs/dev/migrations.md" in r.stderr


def test_mode_only_change_counts_as_modification(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    repo.git(clone, "config", "core.fileMode", "true")
    (clone / VERSIONS / "0002_next.py").chmod(0o755)
    r = run(clone, repo.env)
    assert r.returncode == 1 and f"M  {VERSIONS}/0002_next.py" in r.stderr, r.stderr


def test_deleted_released_file_fails(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    (clone / VERSIONS / "0002_next.py").unlink()
    r = run(clone, repo.env)
    assert r.returncode == 1 and f"D  {VERSIONS}/0002_next.py" in r.stderr and "删除" in r.stderr


def test_renamed_released_file_fails_as_deletion(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    repo.git(clone, "mv", f"{VERSIONS}/0002_next.py", f"{VERSIONS}/0002_renamed.py")
    r = run(clone, repo.env)
    assert r.returncode == 1
    assert f"D  {VERSIONS}/0002_next.py" in r.stderr and "0002_renamed.py" not in r.stderr, "改名 = 旧路径删除 + 新路径新增,只点名旧路径"


def test_type_change_fails(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    target = clone / VERSIONS / "0002_next.py"
    target.unlink(); target.symlink_to("0001_base.py")
    r = run(clone, repo.env)
    assert r.returncode == 1 and f"T  {VERSIONS}/0002_next.py" in r.stderr, r.stderr


# ── 基线选择 ──

def test_baseline_is_highest_version_not_lexicographic_or_newest(repo: Repo, tmp_path: Path):
    repo.commit({f"{VERSIONS}/0003_v110.py": _mig("0003")}, tag="v1.10.0")
    repo.commit({f"{VERSIONS}/0004_v19.py": _mig("0004")}, tag="v1.9.0")   # 更晚打的、字母序更大,但版本号更小
    repo.commit({f"{VERSIONS}/0005_rc.py": _mig("0005")}, tag="v2.0.0-rc1")   # 非正式 tag 不算基线
    clone = repo.clone(tmp_path / "clone")
    r = run(clone, repo.env)
    assert r.returncode == 0 and "基线 v1.10.0" in r.stdout, r.stdout + r.stderr
    # v1.10.0 里的文件被改必须被抓住;v1.9.0 / rc 之后才加的文件改了不算(它们尚未在最新正式版里)
    (clone / VERSIONS / "0003_v110.py").write_text("changed", encoding="utf-8")
    (clone / VERSIONS / "0004_v19.py").write_text("changed", encoding="utf-8")
    r = run(clone, repo.env)
    assert r.returncode == 1 and "0003_v110.py" in r.stderr and "0004_v19.py" not in r.stderr, r.stderr


def test_explicit_baseline_overrides_remote_lookup(repo: Repo, tmp_path: Path):
    repo.commit({f"{VERSIONS}/0001_base.py": _mig("0001", "# changed in v1.1.0\n")}, tag="v1.1.0")
    clone = repo.clone(tmp_path / "clone")
    assert run(clone, repo.env).returncode == 0, "对最新 tag 一致"
    r = run(clone, repo.env, "--baseline", "v1.0.0")
    assert r.returncode == 1 and "基线 v1.0.0" in r.stderr and "0001_base.py" in r.stderr
    r = run(clone, repo.env, "--baseline", "nope")
    assert r.returncode == 2 and "解析不到提交" in r.stderr


def test_offline_uses_local_tags_only(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    repo.commit({f"{VERSIONS}/0001_base.py": _mig("0001", "# v1.1.0\n")}, tag="v1.1.0")
    repo.git(clone, "pull", "-q", "--no-tags", "--ff-only", "origin", "main")   # 代码到了 v1.1.0,tag 没拉
    assert repo.git(clone, "tag", "--list").stdout.split() == ["v1.0.0"]
    r = run(clone, repo.env, "--offline")
    assert r.returncode == 1 and "基线 v1.0.0" in r.stderr and "0001_base.py" in r.stderr, "离线只认本地 tag:树已是 v1.1.0,对旧基线当然不一致"
    r = run(clone, repo.env)
    assert r.returncode == 0 and "基线 v1.1.0" in r.stdout, "在线模式按远端选基线并自己 fetch 那个 tag:" + r.stdout + r.stderr
    assert repo.git(clone, "tag", "--list").stdout.split() == ["v1.0.0", "v1.1.0"]
    (tmp_path / "bare2").mkdir(); bare = Repo(tmp_path / "bare2")
    bare.commit({f"{VERSIONS}/0001_base.py": _mig("0001")})   # 没有任何 tag
    clone2 = bare.clone(tmp_path / "clone2")
    r = run(clone2, bare.env, "--offline")
    assert r.returncode == 2 and "本地没有任何正式 tag" in r.stderr


# ── rc 2:不能静默放行的情形 ──

def test_remote_without_formal_tag_is_an_error_not_a_pass(tmp_path: Path):
    r0 = Repo(tmp_path)
    r0.commit({f"{VERSIONS}/0001_base.py": _mig("0001")}, tag="v1.0.0-rc1")
    r0.commit({f"{VERSIONS}/0001_base.py": _mig("0001", "# x\n")}, tag="release-2026")
    clone = r0.clone(tmp_path / "clone")
    r = run(clone, r0.env)
    assert r.returncode == 2 and "没有任何正式 tag" in r.stderr


def test_baseline_without_migration_files_is_refused(tmp_path: Path):
    r0 = Repo(tmp_path)
    r0.commit({"src/version.py": '__version__ = "1.0.0"\n'}, tag="v1.0.0")
    clone = r0.clone(tmp_path / "clone")
    (clone / VERSIONS).mkdir(parents=True); (clone / VERSIONS / "0001.py").write_text("x", encoding="utf-8")
    r = run(clone, r0.env)
    assert r.returncode == 2 and "没有任何文件" in r.stderr
    r = run(clone, r0.env, "--versions-dir", "nowhere")
    assert r.returncode == 2


def test_stale_local_tag_differing_from_remote_is_an_error(repo: Repo, tmp_path: Path):
    clone = repo.clone(tmp_path / "clone")
    repo.git(clone, "tag", "-f", "-a", "v1.0.0", "-m", "moved", "HEAD~0")   # 本地把 tag 重打成另一个对象
    r = run(clone, repo.env)
    assert r.returncode == 2 and "本地 tag v1.0.0" in r.stderr and "--force" in r.stderr


# ── CI 形态:浅克隆、无 tag ──

def test_shallow_no_tags_clone_fetches_only_the_baseline_tag_and_stays_shallow(repo: Repo, tmp_path: Path):
    repo.commit({f"{VERSIONS}/0003_new.py": _mig("0003")}, tag="v1.1.0")
    repo.commit({"README.md": "head\n"})
    clone = repo.clone(tmp_path / "ci", "--depth", "1", "--no-tags", file_url=True)
    assert repo.git(clone, "tag", "--list").stdout.strip() == ""
    r = run(clone, repo.env)
    assert r.returncode == 0 and "基线 v1.1.0" in r.stdout and "3 个已发布迁移文件" in r.stdout, r.stdout + r.stderr
    assert repo.git(clone, "tag", "--list").stdout.split() == ["v1.1.0"], "只取基线那一个 tag"
    assert repo.git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    (clone / VERSIONS / "0003_new.py").write_text("tampered", encoding="utf-8")
    r = run(clone, repo.env)
    assert r.returncode == 1 and "0003_new.py" in r.stderr


# ── 真实仓冒烟 ──

def test_real_repo_released_migrations_are_untouched():
    tags = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(ROOT), capture_output=True, text=True).stdout.split()
    if not any(t.count(".") == 2 and t[1:].replace(".", "").isdigit() for t in tags):
        pytest.skip("本地没有 v* tag(CI 浅检出):守卫由 ci.yml 的独立步骤在线执行")
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--offline"], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
