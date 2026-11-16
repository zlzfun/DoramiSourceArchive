"""CI 改动面判定守卫(issue #288:PR 只动前端 / 文档时跳过两个重型后端 job)。

守三层,任何一层退化都会让后端回归带着绿灯合入,或让裁剪悄悄失效:
1. `scripts/ci_scope.py` 的判定表——只有全部路径都在 SKIP_SAFE 内才是轻量;任何在外的路径、形状可疑的路径、空改动都是全量;
2. 脚本在真实 git 仓里的行为——按 CI 的形态(`refs/pull/N/merge` 的合并提交、`--depth 2` 浅克隆)造临时仓:
   改名拆成删除 + 新增、base 另有改动不算进 PR、HEAD 不是这个 PR 的合并提交 / 深度不够 / 不是 PR 事件一律全量;
3. `tests/conftest.py` 的读取审计——不在 LIGHT_TESTS 里的测试读了 SKIP_SAFE 路径(用例里、夹具里、模块顶层、列目录)即失败,
   读别处与读临时目录里的同名路径不受影响。这一层保证「重型 job 被跳过不丢覆盖」。
工作流文本侧(job 条件、汇总门真值表、轻量命令与 LIGHT_TESTS 一致)在 tests/test_ci_workflow.py。
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_scope.py"


def _load():
    spec = importlib.util.spec_from_file_location("ci_scope_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_scope = _load()


# ── 1. 判定表 ──

@pytest.mark.parametrize("path", [
    "frontend/src/App.jsx", "frontend/package-lock.json", "docs/README.md", "docs/plans/issue-288-ci-scope.md",
    "docs/design/样页 一.html", "docs-site/guide/index.md", "README.md", "AGENTS.md", "CLAUDE.md",
])
def test_frontend_docs_and_root_markdown_are_skip_safe(path):
    assert ci_scope.is_skip_safe(path)


@pytest.mark.parametrize("path", [
    "src/main.py", "tests/test_ci_scope.py", "tests/fixtures/sample.md", "scripts/ci_scope.py", "scripts/README.md",
    "alembic/versions/x.py", "docker/nginx.conf", "deploy.sh", "pyproject.toml", "uv.lock", ".github/workflows/ci.yml",
    ".claude/rules/tests.md", "e2e/flows.py", "config/config.example.ini",
    # 前缀必须落在目录边界上
    "frontendx/a.js", "docs2/a.md", "docs-site2/a.md", "docs", "frontend",
    # 形状可疑的一律不算
    "", "/docs/a.md", "docs/../src/main.py", "./docs/a.md", "docs//a.md", "docs\\a.md", "README.md/", "notes.MD",
])
def test_everything_else_needs_the_backend_jobs(path):
    assert not ci_scope.is_skip_safe(path)


def test_light_tests_exist_and_are_plain_test_files():
    assert ci_scope.LIGHT_TESTS, "LIGHT_TESTS 不能为空:重型 job 被跳过时至少要有文档守卫"
    for rel in ci_scope.LIGHT_TESTS:
        assert (ROOT / rel).is_file(), f"LIGHT_TESTS 里的 {rel} 不存在"
        assert Path(rel).parent.as_posix() == "tests" and Path(rel).name.startswith("test_")
    assert all(d.endswith("/") and "/" not in d[:-1] for d in ci_scope.SKIP_SAFE_DIRS), "SKIP_SAFE_DIRS 只按首段目录匹配"
    # issue #289:不分片单独跑的工作流守卫——本文件是其中之一
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() in ci_scope.GUARD_TESTS
    for rel in ci_scope.GUARD_TESTS:
        assert (ROOT / rel).is_file(), f"GUARD_TESTS 里的 {rel} 不存在"


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "merge_group", "pull_request_target", ""])
def test_only_pull_request_events_are_narrowed(event, tmp_path):
    needed, reason = ci_scope.decide(event, str(tmp_path), "0" * 40)
    assert needed and "pull_request" in reason


# ── 2. 真实 git 仓(CI 形态)──

class Repo:
    """bare origin + 工作仓;`pull()` 造出与 GitHub `refs/pull/N/merge` 同形的合并提交(第一父 = base 尖端,第二父 = PR head)。"""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        home = tmp_path / "home"
        home.mkdir()
        self.env = {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(tmp_path, "init", "-q", "-b", "main", str(self.work))
        self.git(self.work, "remote", "add", "origin", str(self.origin))
        self.commit({"src/app.py": "print(1)\n", "docs/README.md": "# docs\n", "frontend/src/App.jsx": "export default 1\n",
                     "README.md": "# r\n"})

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=str(cwd), env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict[str, str | None], *, allow_empty: bool = False) -> str:
        for rel, content in files.items():
            path = self.work / rel
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-qm", "c", *(["--allow-empty"] if allow_empty else []))
        return self.git(self.work, "rev-parse", "HEAD")

    def pull(self, pr_files: dict[str, str | None], *, base_files: dict[str, str] | None = None, empty: bool = False) -> tuple[str, str]:
        """在 PR 分支上提交 `pr_files`(可选:此后 main 再前进 `base_files`),返回 (合并提交, PR head)。"""
        self.git(self.work, "checkout", "-q", "-b", "pr")
        head = self.commit(pr_files, allow_empty=empty)
        self.git(self.work, "checkout", "-q", "main")
        if base_files:
            self.commit(base_files)
        self.git(self.work, "merge", "-q", "--no-ff", "-m", "Merge pr into main", "pr")
        merge = self.git(self.work, "rev-parse", "HEAD")
        self.git(self.work, "push", "-q", "origin", "main")
        return merge, head

    def shallow(self, depth: int) -> Path:
        """`actions/checkout` 的形态:走真实传输的浅克隆(本地路径克隆会忽略 --depth)。"""
        dest = self.tmp / f"checkout-{depth}"
        self.git(self.tmp, "clone", "-q", "--depth", str(depth), f"file://{self.origin}", str(dest))
        return dest

    def decide(self, cwd: Path, *, event: str = "pull_request", head: str = "", output: bool = True) -> tuple[str, str]:
        """跑脚本,返回 (写进 GITHUB_OUTPUT 的内容, stdout)。"""
        out_file = self.tmp / f"github-output-{len(list(self.tmp.glob('github-output-*')))}"
        env = {**self.env, "GITHUB_EVENT_NAME": event, "PR_HEAD_SHA": head}
        env.pop("GITHUB_OUTPUT", None)
        if output:
            out_file.write_text("", encoding="utf-8")
            env["GITHUB_OUTPUT"] = str(out_file)
        r = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        return (out_file.read_text(encoding="utf-8") if output else ""), r.stdout


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def test_frontend_and_docs_only_pull_request_skips_the_heavy_jobs(repo):
    merge, head = repo.pull({"frontend/src/App.jsx": "export default 2\n", "docs/plans/新 方案.md": "# p\n", "README.md": "# r2\n"})
    written, stdout = repo.decide(repo.shallow(2), head=head)
    assert written == "backend=false\n"
    assert "3 个改动路径" in stdout


@pytest.mark.parametrize("pr_files", [
    {"src/app.py": "print(2)\n"},
    {"frontend/src/App.jsx": "export default 2\n", "tests/test_x.py": "def test_x(): pass\n"},
    {"src/app.py": None},  # 只删后端文件
    {".github/workflows/ci.yml": "name: CI\n", "docs/README.md": "# d2\n"},
])
def test_any_path_outside_frontend_and_docs_keeps_the_full_suite(repo, pr_files):
    merge, head = repo.pull(pr_files)
    written, _ = repo.decide(repo.shallow(2), head=head)
    assert written == "backend=true\n"


def test_rename_out_of_backend_is_seen_as_a_backend_deletion(repo):
    """`src/app.py → docs/app.md`:带改名检测的 diff 只列出 docs/app.md,整条 PR 会被误判成文档改动。"""
    repo.git(repo.work, "checkout", "-q", "-b", "pr")
    repo.git(repo.work, "mv", "src/app.py", "docs/app.md")
    repo.git(repo.work, "commit", "-qm", "rename")
    head = repo.git(repo.work, "rev-parse", "HEAD")
    repo.git(repo.work, "checkout", "-q", "main")
    repo.git(repo.work, "merge", "-q", "--no-ff", "-m", "Merge pr into main", "pr")
    repo.git(repo.work, "push", "-q", "origin", "main")
    # 对照:改名检测开着时 git 确实只报新路径——所以 --no-renames 不是摆设
    assert repo.git(repo.work, "diff", "--name-only", "-M", "HEAD^1", "HEAD") == "docs/app.md"
    written, stdout = repo.decide(repo.shallow(2), head=head)
    assert written == "backend=true\n" and "src/app.py" in stdout


def test_base_branch_moving_on_does_not_count_as_this_pull_requests_change(repo):
    """PR 只改文档,而 main 在分叉后另有后端提交:合并提交相对 base 尖端的改动仍只有文档。"""
    merge, head = repo.pull({"docs/README.md": "# d2\n"}, base_files={"src/app.py": "print(3)\n"})
    written, _ = repo.decide(repo.shallow(2), head=head)
    assert written == "backend=false\n"


def test_empty_pull_request_keeps_the_full_suite(repo):
    merge, head = repo.pull({}, empty=True)
    written, stdout = repo.decide(repo.shallow(2), head=head)
    assert written == "backend=true\n" and "没有改动路径" in stdout


def test_head_that_is_not_this_pull_requests_merge_commit_keeps_the_full_suite(repo):
    """三种看不准:深度 1(看不到父提交)、HEAD 是普通提交(只会 diff 出最后一个提交)、第二父提交不是事件里的 PR head。"""
    merge, head = repo.pull({"docs/README.md": "# d2\n"})
    checkout = repo.shallow(2)
    assert repo.decide(checkout, head=head)[0] == "backend=false\n", "对照:同一份改动在正常形态下是轻量"

    written, stdout = repo.decide(repo.shallow(1), head=head)
    assert written == "backend=true\n" and "不是 PR 的合并提交" in stdout

    repo.git(repo.work, "checkout", "-q", "pr")  # 直接检出 PR head:一个父提交
    written, stdout = repo.decide(repo.work, head=head)
    assert written == "backend=true\n" and "不是 PR 的合并提交" in stdout
    repo.git(repo.work, "checkout", "-q", "main")

    for wrong in ("", "0" * 40, merge):
        written, stdout = repo.decide(checkout, head=wrong)
        assert written == "backend=true\n" and "第二父提交" in stdout, wrong


def test_non_pull_request_event_keeps_the_full_suite_without_looking_at_git(repo, tmp_path):
    merge, head = repo.pull({"docs/README.md": "# d2\n"})
    assert repo.decide(repo.shallow(2), event="push", head=head)[0] == "backend=true\n"
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert repo.decide(not_a_repo, event="push")[0] == "backend=true\n"
    written, stdout = repo.decide(not_a_repo, event="pull_request", head=head)  # git 失败也是全量,不是报错
    assert written == "backend=true\n" and "看不准" in stdout


def test_without_github_output_the_decision_is_only_printed(repo):
    merge, head = repo.pull({"docs/README.md": "# d2\n"})
    _, stdout = repo.decide(repo.shallow(2), head=head, output=False)
    assert stdout.startswith("backend=false")


# ── 3. 读取审计(tests/conftest.py)──

_PROBE = '''
import os
from pathlib import Path

import pytest

ROOT = Path({root!r})


def test_reads_docs_in_the_test_body():
    assert (ROOT / "docs" / "README.md").read_text(encoding="utf-8")


@pytest.fixture
def frontend_package():
    return (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")


def test_reads_frontend_in_a_fixture(frontend_package):
    assert frontend_package


def test_lists_the_docs_directory():
    assert os.listdir(ROOT / "docs")


def test_reads_root_markdown_through_a_relative_path(monkeypatch):
    monkeypatch.chdir(ROOT)
    with open("AGENTS.md", encoding="utf-8") as f:
        assert f.read()


def test_reads_elsewhere_and_lookalike_paths_in_tmp(tmp_path, monkeypatch):
    assert (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    assert (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    fake = tmp_path / "docs"
    fake.mkdir()
    (fake / "README.md").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert open("docs/README.md", encoding="utf-8").read() == "x"
    assert os.listdir("docs") == ["README.md"]
'''

_PROBE_MODULE_LEVEL = '''
from pathlib import Path

TEXT = (Path({root!r}) / "docs-site" / "package.json").read_text(encoding="utf-8")


def test_nothing():
    assert TEXT
'''


# 读了之后不让结果是「通过」的几种写法——都得判失败(Codex R1-2);已经失败的用例保留它自己的失败原因
_PROBE_NOT_PASSING = '''
from pathlib import Path

import pytest

ROOT = Path({root!r})


def _title():
    return (ROOT / "docs" / "README.md").read_text(encoding="utf-8").splitlines()[0]


def test_skips_depending_on_what_it_read():
    if _title():
        pytest.skip("文档长这样就不测")


def test_xfails_imperatively_after_reading():
    if _title():
        pytest.xfail("文档长这样就算预期失败")


@pytest.mark.xfail(reason="预期失败")
def test_marked_xfail_and_fails_after_reading():
    assert not _title()


@pytest.mark.xfail(reason="预期失败却通过")
def test_marked_xfail_but_passes_after_reading():
    assert _title()


@pytest.fixture
def skipping_fixture():
    if _title():
        pytest.skip("夹具里读完就跳过")


def test_skipped_from_a_fixture_that_read(skipping_fixture):
    pass


def test_fails_on_its_own_after_reading():
    assert _title() == "boom-this-is-the-tests-own-failure"
'''

# 类里的参数生成在收集「类」时才跑,不在收集模块时(Codex R1-1)
_PROBE_CLASS_PARAMS = '''
from pathlib import Path

ROOT = Path({root!r})


def pytest_generate_tests(metafunc):
    if "title" in metafunc.fixturenames:
        metafunc.parametrize("title", [(ROOT / "docs" / "README.md").read_text(encoding="utf-8").splitlines()[0]])


class TestTitle:
    def test_title(self, title):
        assert title
'''

_PROBE_MODULE_SKIP = '''
from pathlib import Path

import pytest

if (Path({root!r}) / "frontend" / "package.json").read_text(encoding="utf-8"):
    pytest.skip("读完前端配置就整个模块跳过", allow_module_level=True)


def test_nothing():
    pass
'''


def _run_probe(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    """在仓外的临时目录里跑一个探针测试文件,把 tests/conftest.py 当插件载入(`-p conftest`)——探针不在 LIGHT_TESTS 里。"""
    probe = tmp_path / "probe" / "test_scope_probe.py"
    probe.parent.mkdir()
    probe.write_text(source.format(root=str(ROOT)), encoding="utf-8")
    # 不继承本进程的 xdist 身份与沙箱:探针进程按「什么都没设置」自己铸一份
    dropped = ("PYTEST_XDIST_WORKER", "PYTEST_CURRENT_TEST", "DORAMI_CONFIG_FILE", "DORAMI_TEST_SANDBOX")
    env = {k: v for k, v in os.environ.items() if k not in dropped}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "tests"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider", "-p", "conftest",
         "--rootdir", str(probe.parent), "-c", os.devnull, "-rfE"],
        cwd=str(probe.parent), env=env, capture_output=True, text=True, timeout=300,
    )


def test_audit_fails_unlisted_tests_that_read_skip_safe_paths(tmp_path):
    r = _run_probe(tmp_path, _PROBE)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-3000:]
    for fragment in ("3 failed", "1 passed", "1 error"):  # 夹具里读的那条在 setup 阶段判失败,pytest 记为 error
        assert fragment in out, (fragment, out[-3000:])
    for name, path in (("test_reads_docs_in_the_test_body", "docs/README.md"),
                       ("test_reads_frontend_in_a_fixture", "frontend/package.json"),
                       ("test_lists_the_docs_directory", "docs"),
                       ("test_reads_root_markdown_through_a_relative_path", "AGENTS.md")):
        assert f"{name} 读了 {path}" in out, (name, out[-3000:])
    assert "LIGHT_TESTS" in out
    assert "test_reads_elsewhere_and_lookalike_paths_in_tmp 读了" not in out


def test_audit_fails_collection_when_a_module_reads_skip_safe_paths_at_import(tmp_path):
    r = _run_probe(tmp_path, _PROBE_MODULE_LEVEL)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out[-3000:]
    assert "读了 docs-site/package.json" in out and "error" in out, out[-3000:]


def test_audit_fails_tests_that_read_and_then_skip_or_xfail(tmp_path):
    r = _run_probe(tmp_path, _PROBE_NOT_PASSING)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-3000:]
    for fragment in ("5 failed", "1 error"):  # 夹具里跳过的那条在 setup 阶段判失败,pytest 记为 error
        assert fragment in out, (fragment, out[-3000:])
    for word in ("skipped", "xfailed", "xpassed"):
        assert word not in out.splitlines()[-1], (word, out[-3000:])
    for name in ("test_skips_depending_on_what_it_read", "test_xfails_imperatively_after_reading",
                 "test_marked_xfail_and_fails_after_reading", "test_marked_xfail_but_passes_after_reading",
                 "test_skipped_from_a_fixture_that_read"):
        assert f"{name} 读了 docs/README.md" in out, (name, out[-3000:])
    # 本来就失败的那条:保留它自己的断言失败,不被审计信息盖掉
    assert "boom-this-is-the-tests-own-failure" in out
    assert "test_fails_on_its_own_after_reading 读了" not in out


@pytest.mark.parametrize("source, path", [(_PROBE_CLASS_PARAMS, "docs/README.md"), (_PROBE_MODULE_SKIP, "frontend/package.json")],
                         ids=["class-level-parametrize", "module-level-skip"])
def test_audit_fails_collection_for_reads_outside_plain_module_import(tmp_path, source, path):
    r = _run_probe(tmp_path, source)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out[-3000:]
    assert f"读了 {path}" in out and "error" in out and "passed" not in out.splitlines()[-1], out[-3000:]


def test_conftest_executed_twice_in_one_process_shares_one_audit():
    """conftest 在同一进程里被执行两遍(换模块名再导入时会这样):读取记录必须是同一个列表,钩子只装一次——
    两遍之后读一次 docs/README.md,列表里只多一条而不是两条。放在子进程里做:再执行一遍 conftest 会重铸沙箱环境变量。"""
    code = (
        "import runpy, sys\n"
        "first = runpy.run_path(sys.argv[1])\n"
        "second = runpy.run_path(sys.argv[1])\n"
        "assert first['_scope_reads'] is second['_scope_reads'] is sys._dorami_scope_reads\n"
        "del sys._dorami_scope_reads[:]\n"
        "open(sys.argv[2], encoding='utf-8').close()\n"
        "print(len(sys._dorami_scope_reads), sys._dorami_scope_reads)\n"
    )
    dropped = ("PYTEST_XDIST_WORKER", "PYTEST_CURRENT_TEST", "DORAMI_CONFIG_FILE", "DORAMI_TEST_SANDBOX")
    env = {k: v for k, v in os.environ.items() if k not in dropped}
    r = subprocess.run([sys.executable, "-c", code, str(ROOT / "tests" / "conftest.py"), str(ROOT / "docs" / "README.md")],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "1 ['docs/README.md']", r.stdout


def test_listed_light_tests_are_allowed_to_read_them(pytestconfig):
    """正向对照:LIGHT_TESTS 自己读这些路径不被拦(它们本就在全量里通过,这里钉住判定函数)。"""
    target = ROOT / "tests" / "conftest.py"
    loaded = [plugin for plugin in pytestconfig.pluginmanager.get_plugins()
              if getattr(plugin, "__file__", None) and Path(plugin.__file__).resolve() == target]
    assert len(loaded) == 1, "tests/conftest.py 应恰好载入一次"
    conftest = loaded[0]

    assert conftest.scope_guarded_path(ROOT / "docs" / "README.md") == "docs/README.md"
    assert conftest.scope_guarded_path(str(ROOT / "frontend" / "vite.config.js")) == "frontend/vite.config.js"
    assert conftest.scope_guarded_path(ROOT / "src" / "version.py") is None
    assert conftest.scope_guarded_path(3) is None
    for rel in ci_scope.LIGHT_TESTS:
        assert conftest._scope_listed(ROOT / rel)
    assert not conftest._scope_listed(Path(__file__))
