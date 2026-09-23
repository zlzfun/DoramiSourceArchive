"""CI 工作流守卫(issue #150:后端测试拆 backend-unit / backend-deploy 两个 job + pytest-xdist 并行)。

守三件事,任何一件退化都会让 CI 悄悄变慢或漏跑用例:
1. 两个 job 对 tests/ 的划分**同源**——backend-deploy 的正向文件模式与 backend-unit 的 --ignore* 逐字相同,
   于是「unit = 全部 − deploy」恒成立,新增 tests/test_*.py 必落在且只落在一个 job 里;
2. 两个 job 都带 `-n <workers> --dist worksteal`——默认 load 调度会把连成一片的部署用例整块塞给单 worker
   (实测 13 min vs 5:50),worksteal 不是可选项;
3. 汇总门 job `backend (pytest)` 仍在(分支保护 required 的是这个名字),`if: always()` 且 needs 两个真 job——
   否则上游失败时它被 skip,而 skip 在分支保护眼里等于通过。
另守 conftest 的按 worker 沙箱规则:在子进程里以受控环境 `runpy.run_path` 执行 conftest(连同它顶层那一次调用),
覆盖四种环境组合,不必真起 worker。
不引入 PyYAML(钉版清单里没有):pytest 命令在 ci.yml 里刻意写成一行,这里按行解析。
"""
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


def _pytest_commands() -> dict[str, list[str]]:
    """job id → 该 job 里 `python -m pytest …` 命令的 argv。"""
    job, out = None, {}
    for line in CI.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            job = m.group(1)
            continue
        m = re.match(r"^\s+run:\s*(\S*python -m pytest .*)$", line)
        if m and job:
            assert job not in out, f"job {job} 里出现了两条 pytest 命令,守卫只按一条解析"
            out[job] = shlex.split(m.group(1))
    return out


def _job_block(job: str) -> str:
    text = CI.read_text(encoding="utf-8")
    m = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)", text, re.S | re.M)
    assert m, f"ci.yml 里没有 job {job}"
    return m.group(1)


_OPTS_WITH_VALUE = {"-n", "-p", "--dist", "-k", "-m", "-o", "-c"}


def _positional_patterns(argv: list[str]) -> set[str]:
    """pytest 之后的位置参数(文件 / 目录 / glob),跳过带独立取值的选项。"""
    out, rest = set(), argv[argv.index("pytest") + 1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in _OPTS_WITH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            out.add(a)
        i += 1
    return out


def _ignore_patterns(argv: list[str]) -> set[str]:
    out = set()
    for a in argv:
        for opt in ("--ignore-glob=", "--ignore="):
            if a.startswith(opt):
                out.add(a[len(opt):])
    return out


def test_partition_is_shared_and_covers_every_test_file():
    cmds = _pytest_commands()
    assert {UNIT, DEPLOY} <= set(cmds), f"ci.yml 里应有 {UNIT} 与 {DEPLOY} 两条 pytest 命令,实得 {sorted(cmds)}"
    deploy_patterns = _positional_patterns(cmds[DEPLOY])
    unit_patterns = _ignore_patterns(cmds[UNIT])
    assert _positional_patterns(cmds[UNIT]) == {"tests"}, "backend-unit 必须从整个 tests/ 减去 deploy 模式,而不是另列名单"
    assert deploy_patterns == unit_patterns, (
        f"两个 job 的划分必须逐字相同:deploy 正向 {sorted(deploy_patterns)} vs unit 排除 {sorted(unit_patterns)}"
    )
    all_files = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
    deploy_files: set[str] = set()
    for pattern in sorted(deploy_patterns):
        hits = {Path(p).as_posix() for p in glob.glob(pattern, root_dir=ROOT)}
        assert hits, f"deploy 模式 {pattern!r} 一个文件都没匹配到(死模式)"
        assert hits <= all_files, f"deploy 模式 {pattern!r} 匹配到 tests/test_*.py 之外的路径 {sorted(hits - all_files)}"
        deploy_files |= hits
    unit_files = all_files - deploy_files
    assert unit_files and deploy_files, "两个 job 都必须有用例"
    # 划分意图的锚点:部署脚本桩测试与迁移测试在 deploy 侧,普通用例在 unit 侧
    assert {"tests/test_deploy_baremetal.py", "tests/test_deploy_scripts.py", "tests/test_migrations.py"} <= deploy_files
    assert "tests/test_admin_ops.py" in unit_files and "tests/test_ci_workflow.py" in unit_files


@pytest.mark.parametrize("job", [UNIT, DEPLOY])
def test_backend_jobs_run_xdist_with_worksteal(job):
    argv = _pytest_commands()[job]
    assert "-n" in argv, f"{job} 未开 pytest-xdist(-n)"
    workers = argv[argv.index("-n") + 1]
    assert workers == "auto" or workers.isdigit() and int(workers) >= 2, f"{job} 的 -n 值 {workers!r} 不是并行"
    assert "--dist" in argv and argv[argv.index("--dist") + 1] == "worksteal", f"{job} 必须 --dist worksteal(见模块注释)"
    assert "-p" in argv and argv[argv.index("-p") + 1] == "no:cacheprovider"


def test_gate_job_keeps_required_check_name_and_cannot_be_skipped():
    block = _job_block(GATE)
    assert re.search(r"^\s+name: backend \(pytest\)\s*$", block, re.M), "汇总门 job 的显示名必须仍是 `backend (pytest)`(分支保护 required)"
    needs = re.search(r"^\s+needs:\s*\[(.*?)\]\s*$", block, re.M)
    assert needs and {s.strip() for s in needs.group(1).split(",")} == {UNIT, DEPLOY}
    assert re.search(r"^\s+if:\s*always\(\)\s*$", block, re.M), "汇总门必须 if: always(),否则上游失败时被 skip"
    for job in (UNIT, DEPLOY):
        assert f"needs.{job}.result" in block, f"汇总门必须核对 needs.{job}.result"
    for job in (UNIT, DEPLOY):
        assert re.search(rf"^\s+name: {job} \(pytest\)\s*$", _job_block(job), re.M)


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
