"""CI 工作流守卫(issue #150:后端测试拆 backend-unit / backend-deploy 两个 job + pytest-xdist 并行)。

守三件事,任何一件退化都会让 CI 悄悄变慢或漏跑用例:
1. 两个 job 对 tests/ 的划分**同源且按 pytest 语义互斥、覆盖全部文件**——backend-deploy 的位置参数(runner 的 bash 展开)
   与 backend-unit 的 `--ignore` / `--ignore-glob`(pytest 9 `pytest_ignore_collect`:前者绝对路径相等、后者对绝对路径 fnmatch)
   分别算出文件集合,交集为空、并集等于全部 `tests/test_*.py`;两边的模式字符串还要逐字相同,新增 `tests/test_deploy_*.py` 自动落 deploy 侧;
2. 两个 job 都带恰好一次 `-n <workers>` 与 `--dist worksteal`(pytest 对重复选项取后值,所以重复即拒绝)——默认 load 调度会把
   连成一片的部署用例整块塞给单 worker(实测 13 min vs 5:50),worksteal 不是可选项;
3. 汇总门 job `backend (pytest)`(分支保护 required 的名字)在 **job 层** `needs` 两个真 job 且 `if: always()`,step 层不得再挂条件,
   `env` 把两个上游结果传进 shell,并且真跑那段 shell:success / failure / cancelled / skipped 的 16 种组合只有 success/success 通过——
   否则上游失败时它被 skip 或被吞成功,而 skip 在分支保护眼里等于通过。
每条守卫都以工作流文本为输入,配一组**反向对照**(对 ci.yml 做突变,必须被拒);另守 conftest 的按 worker 沙箱规则(子进程 `runpy` 执行
conftest 顶层,四种环境组合)与双 worker 端到端。不引入 PyYAML(钉版清单里没有):pytest 命令在 ci.yml 里刻意写成一行,这里按行解析。
(codex R1 检视:守卫首版对这三种退化只挡住了最直接的形态——`always()` 挪到 step / 成功判定换 `echo` / glob 改 `--ignore=` / 末尾追加 `-n 0`
都能通过,故改为语义计算 + 真跑门禁 shell + 反向对照。)
"""
import fnmatch
import glob
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
UNIT, DEPLOY, GATE = "backend-unit", "backend-deploy", "backend"
RESULTS = ("success", "failure", "cancelled", "skipped")
_OPTS_WITH_VALUE = {"-n", "-p", "--dist", "-k", "-m", "-o", "-c"}


# ── 工作流文本解析(受控格式:job 头两空格缩进、job 层键四空格、pytest 命令单行)──

def _jobs_region(text: str) -> str:
    m = re.search(r"^jobs:\s*$", text, re.M)
    assert m, "ci.yml 里没有 jobs:"
    return text[m.end():]


def _pytest_commands(text: str) -> dict[str, list[str]]:
    """job id → 该 job 里 `python -m pytest …` 命令的 argv。"""
    job, out = None, {}
    for line in _jobs_region(text).splitlines():
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            job = m.group(1)
            continue
        m = re.match(r"^\s+run:\s*(\S*python -m pytest .*)$", line)
        if m and job:
            assert job not in out, f"job {job} 里出现了两条 pytest 命令,守卫只按一条解析"
            out[job] = shlex.split(m.group(1))
    return out


def _job_block(text: str, job: str) -> str:
    m = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)", _jobs_region(text), re.S | re.M)
    assert m, f"ci.yml 里没有 job {job}"
    return m.group(1)


def _job_level(block: str) -> dict[str, str]:
    """job 自己那一层(四空格缩进)的标量键;steps 里同名的键缩进更深,不算。"""
    out = {}
    for line in block.splitlines():
        m = re.match(r"^    ([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _run_block(block: str) -> str:
    """job 里第一个 `run: |` 的 shell 正文(去公共缩进)。"""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s+)run:\s*\|\s*$", line)
        if not m:
            continue
        indent, body = len(m.group(1)), []
        for follow in lines[i + 1:]:
            if follow.strip() == "":
                body.append("")
                continue
            if len(follow) - len(follow.lstrip(" ")) <= indent:
                break
            body.append(follow)
        common = min(len(b) - len(b.lstrip(" ")) for b in body if b.strip())
        return "\n".join(b[common:] for b in body) + "\n"
    raise AssertionError("job 里没有 `run: |` 块")


def _split_argv(argv: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """pytest 之后的位置参数,以及保留种类的排除项 [(\"path\"|\"glob\", value)];跳过带独立取值的选项。"""
    rest, pos, ignores = argv[argv.index("pytest") + 1:], [], []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in _OPTS_WITH_VALUE:
            i += 2
            continue
        if a.startswith("--ignore-glob="):
            ignores.append(("glob", a[len("--ignore-glob="):]))
        elif a.startswith("--ignore="):
            ignores.append(("path", a[len("--ignore="):]))
        elif a in ("--ignore", "--ignore-glob"):
            raise AssertionError(f"{a} 请用 = 连写,守卫只按该形态解析")
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    return pos, ignores


def _all_test_files() -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}


def _shell_expanded(patterns: list[str]) -> set[str]:
    """deploy 侧:位置参数由 runner 的 bash 展开(nullglob 关:无匹配原样传给 pytest,pytest 报错而非静默)。"""
    files: set[str] = set()
    for pattern in patterns:
        hits = {Path(h).as_posix() for h in glob.glob(pattern, root_dir=ROOT)}
        assert hits, f"deploy 模式 {pattern!r} 一个文件都没匹配到(死模式)"
        files |= hits
    return files


def _pytest_ignored(all_files: set[str], ignores: list[tuple[str, str]]) -> set[str]:
    """unit 侧:镜像 pytest 9 `_pytest/main.py::pytest_ignore_collect`——`--ignore` 是 absolutepath 相等,
    `--ignore-glob` 是 fnmatch(str(collection_path), absolutepath(pattern)),祖先目录命中同样排除。"""
    excluded: set[str] = set()
    for kind, value in ignores:
        target = os.path.abspath(os.path.join(ROOT, value))
        if kind == "path":
            assert not any(ch in value for ch in "*?["), (
                f"--ignore={value!r} 含通配符,但 --ignore 只做路径相等(通配须用 --ignore-glob),部署用例会在两个 job 各跑一遍"
            )
        for f in all_files:
            candidates = [os.path.abspath(os.path.join(ROOT, f))]
            parent = Path(f).parent
            while parent != Path("."):
                candidates.append(os.path.abspath(os.path.join(ROOT, parent)))
                parent = parent.parent
            if kind == "path":
                hit = target in candidates
            else:
                hit = any(fnmatch.fnmatch(c, target) for c in candidates)
            if hit:
                excluded.add(f)
    return excluded


# ── 三条守卫(以工作流文本为输入,便于反向对照)──

def _assert_partition(text: str) -> None:
    cmds = _pytest_commands(text)
    assert {UNIT, DEPLOY} <= set(cmds), f"ci.yml 里应有 {UNIT} 与 {DEPLOY} 两条 pytest 命令,实得 {sorted(cmds)}"
    unit_pos, unit_ign = _split_argv(cmds[UNIT])
    deploy_pos, deploy_ign = _split_argv(cmds[DEPLOY])
    assert unit_pos == ["tests"], "backend-unit 必须从整个 tests/ 减去 deploy 模式,而不是另列名单"
    assert not deploy_ign, "backend-deploy 不该再有排除项"
    assert set(deploy_pos) == {v for _, v in unit_ign}, (
        f"两个 job 的划分必须逐字同源:deploy 正向 {sorted(deploy_pos)} vs unit 排除 {sorted(v for _, v in unit_ign)}"
    )
    all_files = _all_test_files()
    deploy_files = _shell_expanded(deploy_pos)
    assert deploy_files <= all_files, f"deploy 模式匹配到 tests/test_*.py 之外的路径 {sorted(deploy_files - all_files)}"
    unit_files = all_files - _pytest_ignored(all_files, unit_ign)
    overlap = unit_files & deploy_files
    assert not overlap, f"这些文件会在两个 job 各跑一遍:{sorted(overlap)}"
    missing = all_files - (unit_files | deploy_files)
    assert not missing, f"这些文件哪个 job 都不跑:{sorted(missing)}"
    assert unit_files and deploy_files, "两个 job 都必须有用例"
    # 划分意图的锚点:部署脚本桩测试与迁移测试在 deploy 侧,普通用例在 unit 侧
    assert {"tests/test_deploy_baremetal.py", "tests/test_deploy_scripts.py", "tests/test_migrations.py"} <= deploy_files
    assert {"tests/test_admin_ops.py", "tests/test_ci_workflow.py"} <= unit_files


def _assert_parallel(text: str, job: str) -> None:
    argv = _pytest_commands(text)[job]
    assert argv.count("-n") == 1 and not any(re.match(r"^(-n\S|--numprocesses)", a) for a in argv), (
        f"{job}:-n 须恰好出现一次且没有 -nN / --numprocesses 形态(pytest 对重复选项取后值,末尾一个 -n 0 就退回串行)"
    )
    workers = argv[argv.index("-n") + 1]
    assert workers == "auto" or (workers.isdigit() and int(workers) >= 2), f"{job} 的 -n 值 {workers!r} 不是并行"
    assert argv.count("--dist") == 1 and not any(a.startswith("--dist=") for a in argv), f"{job}:--dist 须恰好出现一次"
    assert argv[argv.index("--dist") + 1] == "worksteal", f"{job} 必须 --dist worksteal(见模块注释)"
    assert any(a == "-p" and b == "no:cacheprovider" for a, b in zip(argv, argv[1:])), f"{job} 应带 -p no:cacheprovider"


def _assert_gate(text: str) -> None:
    block = _job_block(text, GATE)
    top = _job_level(block)
    assert top.get("name") == "backend (pytest)", "汇总门 job 的显示名必须仍是 `backend (pytest)`(分支保护 required)"
    needs = re.fullmatch(r"\[(.*)\]", top.get("needs", ""))
    assert needs and {s.strip() for s in needs.group(1).split(",")} == {UNIT, DEPLOY}, "汇总门必须在 job 层 needs 两个真 job"
    assert top.get("if") == "always()", "汇总门必须在 job 层 if: always(),否则上游失败时被 skip(skip 在分支保护眼里等于通过)"
    assert "    steps:" in block, "汇总门没有 steps"
    steps = block.split("    steps:", 1)[1]
    assert not re.search(r"^\s+if:", steps, re.M), "汇总门的 step 不得再挂 if(step 条件救不回被 skip 的 job,却能让核对步骤被跳过)"
    assert "continue-on-error" not in block, "汇总门不得 continue-on-error"
    for job, var in ((UNIT, "UNIT"), (DEPLOY, "DEPLOY")):
        assert re.search(rf"^\s+{var}:\s*\$\{{\{{\s*needs\.{re.escape(job)}\.result\s*\}}\}}\s*$", block, re.M), (
            f"汇总门的 env 必须把 needs.{job}.result 传进 shell 的 ${var}"
        )
    script = _run_block(block)
    for unit_result in RESULTS:
        for deploy_result in RESULTS:
            r = subprocess.run(["bash", "-e", "-c", script], env={**os.environ, "UNIT": unit_result, "DEPLOY": deploy_result},
                               capture_output=True, text=True, timeout=30)
            expect_pass = (unit_result, deploy_result) == ("success", "success")
            assert (r.returncode == 0) == expect_pass, (
                f"UNIT={unit_result} DEPLOY={deploy_result} rc={r.returncode}(应{'通过' if expect_pass else '失败'}):{r.stdout}{r.stderr}"
            )
    for job in (UNIT, DEPLOY):
        assert _job_level(_job_block(text, job)).get("name") == f"{job} (pytest)"


def test_partition_is_shared_and_covers_every_test_file():
    _assert_partition(CI.read_text(encoding="utf-8"))


@pytest.mark.parametrize("job", [UNIT, DEPLOY])
def test_backend_jobs_run_xdist_with_worksteal(job):
    _assert_parallel(CI.read_text(encoding="utf-8"), job)


def test_gate_job_keeps_required_check_name_and_cannot_be_skipped():
    _assert_gate(CI.read_text(encoding="utf-8"))


# ── 反向对照:对 ci.yml 做突变,对应守卫必须拒绝(每种突变都要真的改到了文本)──

def _mutate(text: str, old: str, new: str, count: int | None = None) -> str:
    n = text.count(old)
    assert n >= 1, f"突变锚点没找到:{old!r}"
    if count is not None:
        assert n == count, f"突变锚点 {old!r} 出现 {n} 次,预期 {count}"
    return text.replace(old, new)


_UNIT_CMD_TAIL = "--ignore-glob='tests/test_deploy_*.py' --ignore=tests/test_migrations.py"
_DEPLOY_CMD_HEAD = "pytest tests/test_deploy_*.py tests/test_migrations.py"
_GATE_CHECK = '[ "$UNIT" = "success" ] && [ "$DEPLOY" = "success" ]'

MUTATIONS = {
    "drop_worksteal": (lambda t: _mutate(t, " --dist worksteal", "", count=2), _assert_parallel, UNIT),
    "append_n0_override": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " -n 0", count=1), _assert_parallel, UNIT),
    "append_dist_load_override": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " --dist load", count=1), _assert_parallel, UNIT),
    "glob_via_ignore_instead_of_ignore_glob": (lambda t: _mutate(t, "--ignore-glob='tests/test_deploy_*.py'", "--ignore='tests/test_deploy_*.py'", count=1), _assert_partition, None),
    "drop_migrations_exclusion": (lambda t: _mutate(t, " --ignore=tests/test_migrations.py", "", count=1), _assert_partition, None),
    "deploy_list_diverges": (lambda t: _mutate(t, _DEPLOY_CMD_HEAD, "pytest tests/test_deploy_*.py", count=1), _assert_partition, None),
    "gate_always_moved_to_step": (lambda t: _mutate(_mutate(t, "    if: always()\n", "", count=1),
                                                   "      - name: Require both backend jobs to succeed\n",
                                                   "      - name: Require both backend jobs to succeed\n        if: always()\n", count=1), _assert_gate, None),
    "gate_step_gets_its_own_condition": (lambda t: _mutate(t, "      - name: Require both backend jobs to succeed\n",
                                                       "      - name: Require both backend jobs to succeed\n        if: success()\n", count=1), _assert_gate, None),
    "gate_success_check_replaced_by_echo": (lambda t: _mutate(t, _GATE_CHECK, 'echo "done"', count=1), _assert_gate, None),
    "gate_always_removed": (lambda t: _mutate(t, "    if: always()\n", "", count=1), _assert_gate, None),
    "gate_needs_only_one_job": (lambda t: _mutate(t, "    needs: [backend-unit, backend-deploy]\n", "    needs: [backend-unit]\n", count=1), _assert_gate, None),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutated_workflow_is_rejected(name):
    mutate, check, job = MUTATIONS[name]
    original = CI.read_text(encoding="utf-8")
    mutated = mutate(original)
    assert mutated != original, f"突变 {name} 没有改到文本"
    with pytest.raises(AssertionError):
        if job is None:
            check(mutated)
        else:
            check(mutated, job)


# ── conftest 沙箱规则(子进程 + 受控环境,不起 worker)──

_SANDBOX_KEYS = ("DORAMI_CONFIG_FILE", "DORAMI_TEST_SANDBOX")


def _run_conftest(**env_extra: str) -> dict[str, str | None]:
    """在干净子进程里执行 tests/conftest.py 顶层,返回执行后的沙箱相关环境变量。"""
    env = {k: v for k, v in os.environ.items() if k not in _SANDBOX_KEYS and k != "PYTEST_XDIST_WORKER"}
    env.update(env_extra)
    code = (
        "import json, os, runpy, sys; runpy.run_path(sys.argv[1]); "
        f"print(json.dumps({{k: os.environ.get(k) for k in {_SANDBOX_KEYS!r}}}))"
    )
    r = subprocess.run([sys.executable, "-c", code, str(ROOT / "tests" / "conftest.py")],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_sandbox_minted_when_nothing_is_set():
    got = _run_conftest()
    ini = got["DORAMI_CONFIG_FILE"]
    assert ini and got["DORAMI_TEST_SANDBOX"] == "1"
    assert "dorami-test-sandbox-main-" in ini and Path(ini).read_text(encoding="utf-8").startswith("[storage]")


def test_worker_remints_sandbox_inherited_from_controller():
    got = _run_conftest(PYTEST_XDIST_WORKER="gw3", DORAMI_CONFIG_FILE="/inherited/test.ini", DORAMI_TEST_SANDBOX="1")
    ini = got["DORAMI_CONFIG_FILE"]
    assert ini and ini != "/inherited/test.ini" and got["DORAMI_TEST_SANDBOX"] == "1"
    assert "dorami-test-sandbox-gw3-" in ini, "worker 的沙箱要带 worker id,四个 worker 互不共用"


@pytest.mark.parametrize("worker", ["", "gw0"])
def test_explicit_external_config_is_respected_in_controller_and_worker(worker):
    extra = {"DORAMI_CONFIG_FILE": "/ci/injected.ini"}
    if worker:
        extra["PYTEST_XDIST_WORKER"] = worker
    got = _run_conftest(**extra)
    assert got == {"DORAMI_CONFIG_FILE": "/ci/injected.ini", "DORAMI_TEST_SANDBOX": None}


def test_two_workers_import_app_concurrently_without_sharing_a_database(tmp_path):
    """端到端:两个 worker 同时 import api.app(各自 create_all + 播种)不再互撞 `database is locked`。"""
    pytest.importorskip("xdist")
    target = ROOT / "tests" / "test_admin_ops.py"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "-q", "-p", "no:cacheprovider", "-n", "2", "--dist", "worksteal"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "database is locked" not in r.stdout + r.stderr
