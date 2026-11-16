"""CI 工作流守卫(issue #150:后端测试拆 backend-unit / backend-deploy 两个 job + pytest-xdist 并行)。

守三件事,任何一件退化都会让 CI 悄悄变慢或漏跑用例:
1. 两个 job 对 tests/ 的划分**同源且按 pytest 语义互斥、覆盖全部文件**——backend-deploy 的位置参数(runner 的 bash 展开)
   与 backend-unit 的 `--ignore` / `--ignore-glob`(pytest 9 `pytest_ignore_collect`:前者绝对路径相等、后者对绝对路径 fnmatch)
   分别算出文件集合,交集为空、并集等于全部 `tests/test_*.py`;两边的模式字符串还要逐字相同,新增 `tests/test_deploy_*.py` 自动落 deploy 侧;
2. 两个 job 都带恰好一次 `-n <workers>` 与 `--dist worksteal`(pytest 对重复选项取后值,所以重复即拒绝)——默认 load 调度会把
   连成一片的部署用例整块塞给单 worker(实测 13 min vs 5:50),worksteal 不是可选项;
3. 汇总门 job `backend (pytest)`(分支保护 required 的名字)在 **job 层** `needs` 全部上游且 `if: always()`,step 层不得再挂条件,
   `env` 把上游结果与改动面判定传进 shell,并且真跑那段 shell——否则上游失败时它被 skip 或被吞成功,而 skip 在分支保护眼里等于通过;
4. 按改动面选 job(issue #288):`changes` job 无条件运行、只由 `scripts/ci_scope.py` 给出 `backend`,两个重型 job 只在
   `backend == 'true'` 时跑,`backend-light` 只在 `== 'false'` 时跑且命令与 `ci_scope.LIGHT_TESTS` 逐字一致;汇总门的真值表
   (changes 结果 × backend 取值 × 三个 job 结果,共 1024 种)只放行两条路径:`true` 且两个重型 job 都 success,
   或 `false` 且两个重型 job 都 skipped 且 backend-light success——判定出错(output 为空)与一切其它组合都拒。
   判定脚本自身与「跳过不丢覆盖」的读取审计在 tests/test_ci_scope.py。
5. 「job 成功」等于「用例通过」(Codex R1-3 / R1-4):汇总门那一步的形状恰是 name + env(五个变量各一次)+ `run: |`,
   job 层没有 env;三个后端 job 里跑 pytest 的那一步只能是 name + 单行 run,job 层只认 name / needs / if / runs-on / steps,
   ci.yml 任何位置不得设置 `PYTEST_ADDOPTS`。挂条件、continue-on-error、同名 env 覆盖、折行接 `|| true` 都在反向对照里。
每条守卫都以工作流文本为输入,配一组**反向对照**(对 ci.yml 做突变,必须被拒);另守 conftest 的按 worker 沙箱规则(子进程 `runpy` 执行
conftest 顶层,四种环境组合)与双 worker 端到端。不引入 PyYAML(钉版清单里没有):pytest 命令在 ci.yml 里刻意写成一行,这里按行解析。
(codex R1 检视:守卫首版对这三种退化只挡住了最直接的形态——`always()` 挪到 step / 成功判定换 `echo` / glob 改 `--ignore=` / 末尾追加 `-n 0`
都能通过,故改为语义计算 + 真跑门禁 shell + 反向对照。)
"""
import fnmatch
import glob
import importlib.util
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
UNIT, DEPLOY, GATE = "backend-unit", "backend-deploy", "backend"
CHANGES, LIGHT = "changes", "backend-light"
RESULTS = ("success", "failure", "cancelled", "skipped")
# changes 没成功时它的 output 是空串;大小写不同的值也不是放行
BACKEND_VALUES = ("true", "false", "", "TRUE")
_OPTS_WITH_VALUE = {"-n", "-p", "--dist", "-k", "-m", "-o", "-c"}
# 汇总门 shell 变量 → 工作流表达式
_GATE_ENV = {
    "CHANGES": "needs.changes.result",
    "BACKEND": "needs.changes.outputs.backend",
    "UNIT": "needs.backend-unit.result",
    "DEPLOY": "needs.backend-deploy.result",
    "LIGHT": "needs.backend-light.result",
}


def _ci_scope():
    spec = importlib.util.spec_from_file_location("ci_scope_for_workflow_guard", ROOT / "scripts" / "ci_scope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _code(block: str) -> str:
    """去掉整行注释(job 之间的说明注释按文本位置归在上一个 job 的块里,不该参与结构判断)。"""
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#")) + "\n"


# 后端各 job 在 job 层只认这几个键:env / continue-on-error / defaults 之类都会改变「job 成功」的含义
_JOB_KEYS = {"name", "needs", "if", "runs-on", "steps"}


def _steps(block: str) -> list[list[str]]:
    """job 块里的各个 step:每个是去注释、去空行后的行列表(受控格式:step 以六空格缩进的 `- ` 开头,其余行缩进更深)。"""
    code = _code(block)
    assert code.count("    steps:\n") == 1, "job 应恰有一个 steps"
    steps: list[list[str]] = []
    for line in code.split("    steps:\n", 1)[1].splitlines():
        if not line.strip():
            continue
        if line.startswith("      - "):
            steps.append([line])
        else:
            assert steps and line.startswith("        "), f"steps 里出现了归不到任何 step 的行:{line!r}"
            steps[-1].append(line)
    return steps


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


def _gate_should_pass(changes: str, backend: str, unit: str, deploy: str, light: str) -> bool:
    """汇总门的真值表,独立于 ci.yml 里那段 shell 重述一遍。"""
    if changes != "success":
        return False
    if backend == "true":
        return unit == "success" and deploy == "success"
    if backend == "false":
        return unit == "skipped" and deploy == "skipped" and light == "success"
    return False


def _assert_gate(text: str) -> None:
    block = _code(_job_block(text, GATE))
    top = _job_level(block)
    assert top.get("name") == "backend (pytest)", "汇总门 job 的显示名必须仍是 `backend (pytest)`(分支保护 required)"
    needs = re.fullmatch(r"\[(.*)\]", top.get("needs", ""))
    assert needs and {s.strip() for s in needs.group(1).split(",")} == {CHANGES, UNIT, DEPLOY, LIGHT}, (
        "汇总门必须在 job 层 needs 改动面判定与三个后端 job"
    )
    assert top.get("if") == "always()", "汇总门必须在 job 层 if: always(),否则上游失败时被 skip(skip 在分支保护眼里等于通过)"
    assert "    steps:" in block, "汇总门没有 steps"
    steps_text = block.split("    steps:", 1)[1]
    assert not re.search(r"^\s+(?:- )?if:", steps_text, re.M), "汇总门的 step 不得再挂 if(step 条件救不回被 skip 的 job,却能让核对步骤被跳过)"
    assert "continue-on-error" not in block, "汇总门不得 continue-on-error"
    assert set(top) == _JOB_KEYS, f"汇总门的 job 层只能有 {sorted(_JOB_KEYS)}(job 层的 env 会被 step 层同名项覆盖,守卫只认 step 层那一份),实得 {sorted(top)}"
    # 真值表跑的是下面提取出的那段 shell;要让它等于 runner 上真正执行的东西,这一步的形状必须恰好是
    # name + env(五个变量各一次、各接各的表达式)+ `run: |` 脚本——多一个键(if / shell / 第二份 env / 第二步)都不认。
    steps = _steps(_job_block(text, GATE))
    assert len(steps) == 1, f"汇总门只能有一步(实得 {len(steps)} 步)"
    step = steps[0]
    n = len(_GATE_ENV)
    assert len(step) > n + 3 and re.fullmatch(r"      - name: .+", step[0]) and step[1] == "        env:" and step[n + 2] == "        run: |", (
        f"汇总门那一步的形状必须是 name / env / run: |,实得 {step[:n + 3]}"
    )
    env = dict(re.fullmatch(r"          ([A-Z]+): (.+)", line).groups() if re.fullmatch(r"          ([A-Z]+): (.+)", line) else ("?", line)
               for line in step[2:n + 2])
    assert env == {var: "${{ " + expr + " }}" for var, expr in _GATE_ENV.items()}, (
        f"汇总门的 env 必须恰好把这五个表达式各传一次(写死成常量等于让门禁恒走某一条路径),实得 {env}"
    )
    assert all(line.startswith("          ") for line in step[n + 3:]), "`run: |` 之后只能是脚本正文"
    script = _run_block(block)
    combos = list(itertools.product(RESULTS, BACKEND_VALUES, RESULTS, RESULTS, RESULTS))

    def run(combo: tuple[str, ...]) -> str | None:
        env = {**os.environ, **dict(zip(_GATE_ENV, combo))}
        r = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True, timeout=30)
        expect_pass = _gate_should_pass(*combo)
        if (r.returncode == 0) == expect_pass:
            return None
        return f"{dict(zip(_GATE_ENV, combo))} rc={r.returncode}(应{'通过' if expect_pass else '失败'})"

    with ThreadPoolExecutor(max_workers=8) as pool:
        wrong = [w for w in pool.map(run, combos) if w]
    assert not wrong, f"汇总门有 {len(wrong)} 种组合判错,如:{wrong[:3]}"
    assert sum(_gate_should_pass(*c) for c in combos) == len(RESULTS) + 1, "真值表应恰有 4(全量,light 任意)+ 1(轻量)种放行组合"
    for job in (UNIT, DEPLOY, LIGHT):
        assert _job_level(_job_block(text, job)).get("name") == f"{job} (pytest)"


def _assert_scope(text: str) -> None:
    """改动面判定的接线:判定只来自脚本,重型 / 轻量 job 各按它的一个取值运行,轻量命令与 LIGHT_TESTS 同源。"""
    block = _code(_job_block(text, CHANGES))
    top = _job_level(block)
    assert "if" not in top and "needs" not in top, "changes job 必须无条件运行(被跳过时 output 为空,所有 PR 都会卡在汇总门)"
    assert "continue-on-error" not in block, "changes job 不得 continue-on-error"
    assert re.search(r"^      backend:\s*\$\{\{\s*steps\.scope\.outputs\.backend\s*\}\}\s*$", block, re.M), (
        "changes 的 outputs.backend 必须来自 steps.scope(写死等于关掉判定)"
    )
    assert re.search(r"^\s+id:\s*scope\s*$", block, re.M), "判定步骤的 id 必须是 scope"
    runs = [m.strip() for m in re.findall(r"^\s+(?:- )?run:\s*(.*)$", block, re.M)]
    assert runs == ["python3 scripts/ci_scope.py"], f"changes job 只能有一条命令:运行判定脚本(实得 {runs})"
    assert "GITHUB_OUTPUT" not in block, "判定只由脚本写 $GITHUB_OUTPUT,工作流里不另写"
    assert re.search(r"^\s+fetch-depth:\s*2\s*$", block, re.M), (
        "changes 的检出必须 fetch-depth: 2(合并提交 + 两个父提交);退回默认深度时脚本一律判全量,裁剪悄悄失效"
    )
    assert re.search(r"^\s+PR_HEAD_SHA:\s*\$\{\{\s*github\.event\.pull_request\.head\.sha\s*\}\}\s*$", block, re.M), (
        "判定步骤必须把 PR head 传给脚本(脚本靠它认出合并提交)"
    )
    for job in (UNIT, DEPLOY):
        level = _job_level(_code(_job_block(text, job)))
        assert level.get("needs") == "[changes]" and level.get("if") == "needs.changes.outputs.backend == 'true'", (
            f"{job} 必须 needs: [changes] 且只在 backend == 'true' 时运行"
        )
    level = _job_level(_code(_job_block(text, LIGHT)))
    assert level.get("needs") == "[changes]" and level.get("if") == "needs.changes.outputs.backend == 'false'", (
        f"{LIGHT} 必须 needs: [changes] 且只在 backend == 'false' 时运行"
    )
    cmds = _pytest_commands(text)
    assert LIGHT in cmds, f"{LIGHT} 里没有 pytest 命令"
    argv = cmds[LIGHT]
    pos, ignores = _split_argv(argv)
    light_tests = _ci_scope().LIGHT_TESTS
    assert tuple(pos) == tuple(light_tests), f"{LIGHT} 的文件清单必须与 ci_scope.LIGHT_TESTS 逐字一致:{pos} vs {list(light_tests)}"
    options = argv[argv.index("pytest") + 1:]  # `python -m pytest` 里的 -m 不是筛选项
    assert not ignores and not any(a in ("-k", "-m", "--deselect") or a.startswith("--deselect=") for a in options), (
        f"{LIGHT} 不得再排除或筛选用例"
    )
    assert any(a == "-p" and b == "no:cacheprovider" for a, b in zip(argv, argv[1:])), f"{LIGHT} 应带 -p no:cacheprovider"
    # 全量时这几条由 backend-unit 跑(轻量 job 只是它被跳过时的替身),所以必须都在 unit 侧
    _, unit_ign = _split_argv(cmds[UNIT])
    all_files = _all_test_files()
    assert set(pos) <= all_files - _pytest_ignored(all_files, unit_ign), "LIGHT_TESTS 必须都落在 backend-unit 的划分里"


def _assert_plain_pytest_step(text: str, job: str) -> None:
    """跑 pytest 的那一步只能是 `name` + 单行 `run`(Codex R1-4):挂条件、吞失败、用环境变量改 pytest 行为、
    把命令折行接 `|| true`——每一种都让「job 成功」不再等于「用例通过」,而命令文本看起来原封不动。"""
    assert "PYTEST_ADDOPTS" not in _code(text), "ci.yml 不得设置 PYTEST_ADDOPTS(一句 --collect-only 就让所有用例只收集不执行)"
    block = _job_block(text, job)
    extra = set(_job_level(_code(block))) - _JOB_KEYS
    assert not extra, f"{job} 的 job 层出现了守卫不认识的键 {sorted(extra)}"
    hits = [step for step in _steps(block) if any(re.search(r"python -m pytest ", line) for line in step)]
    assert len(hits) == 1, f"{job} 里应恰有一步跑 pytest(实得 {len(hits)})"
    step = hits[0]
    assert len(step) == 2 and re.fullmatch(r"      - name: .+", step[0]) and re.fullmatch(r"        run: \S*python -m pytest .+", step[1]), (
        f"{job} 跑 pytest 的那一步只能有 name 与单行 run,实得 {step}"
    )
    argv = _pytest_commands(text)[job]
    assert not any(tok in ("||", "&&", "|", "&", ";") or tok.endswith(";") for tok in argv), f"{job} 的 pytest 命令里不得串接别的命令"


def _assert_released_migrations_guard(text: str) -> None:
    """issue #227:`scripts/check_released_migrations.py` 必须作为 backend-deploy 的一个无条件 step 出现(汇总门 needs 它),
    不能挂 if / continue-on-error——否则已发布迁移文件被改写只会在发版那天由生产机 worker 的第 7 道门发现。
    (issue #288 起 backend-deploy 这个 job 在 PR 只动前端 / 文档时整体跳过:那种 PR 改不到 `alembic/versions`,
    `alembic/` 不在 `ci_scope.SKIP_SAFE_DIRS` 里——由 tests/test_ci_scope.py 的判定表钉住。)"""
    block = _job_block(text, DEPLOY)
    lines = block.splitlines()
    hits = [i for i, line in enumerate(lines) if re.match(r"^\s+run:\s*python3 scripts/check_released_migrations\.py\s*$", line)]
    assert len(hits) == 1, "backend-deploy 里必须恰好有一步 `run: python3 scripts/check_released_migrations.py`"
    # 该 step 的块:从上一个 `- ` 开头的行到下一个 `- ` 开头的行
    start = max(i for i in range(hits[0] + 1) if lines[i].lstrip().startswith("- "))
    end = next((i for i in range(hits[0] + 1, len(lines)) if lines[i].lstrip().startswith("- ")), len(lines))
    step = "\n".join(lines[start:end])
    assert not re.search(r"^\s+if:", step, re.M), "迁移文件守卫 step 不得挂 if"
    assert "continue-on-error" not in step, "迁移文件守卫 step 不得 continue-on-error"


def test_released_migrations_guard_runs_unconditionally_in_backend_deploy():
    _assert_released_migrations_guard(CI.read_text(encoding="utf-8"))


def test_partition_is_shared_and_covers_every_test_file():
    _assert_partition(CI.read_text(encoding="utf-8"))


@pytest.mark.parametrize("job", [UNIT, DEPLOY])
def test_backend_jobs_run_xdist_with_worksteal(job):
    _assert_parallel(CI.read_text(encoding="utf-8"), job)


def test_gate_job_keeps_required_check_name_and_cannot_be_skipped():
    _assert_gate(CI.read_text(encoding="utf-8"))


def test_scope_job_decides_and_backend_jobs_follow_it():
    _assert_scope(CI.read_text(encoding="utf-8"))


@pytest.mark.parametrize("job", [UNIT, DEPLOY, LIGHT])
def test_pytest_step_cannot_be_skipped_or_have_its_failure_swallowed(job):
    _assert_plain_pytest_step(CI.read_text(encoding="utf-8"), job)


# ── 反向对照:对 ci.yml 做突变,对应守卫必须拒绝(每种突变都要真的改到了文本)──

def _mutate(text: str, old: str, new: str, count: int | None = None) -> str:
    n = text.count(old)
    assert n >= 1, f"突变锚点没找到:{old!r}"
    if count is not None:
        assert n == count, f"突变锚点 {old!r} 出现 {n} 次,预期 {count}"
    return text.replace(old, new)


_UNIT_CMD_TAIL = "--ignore-glob='tests/test_deploy_*.py' --ignore=tests/test_migrations.py"
_DEPLOY_CMD_HEAD = "pytest tests/test_deploy_*.py tests/test_migrations.py"
_GATE_STEP = "      - name: Require the backend jobs this change needs\n"
_GATE_NEEDS = "    needs: [changes, backend-unit, backend-deploy, backend-light]\n"
_GATE_FULL = '[ "$UNIT" = "success" ] && [ "$DEPLOY" = "success" ]'
_GATE_LIGHT = '[ "$UNIT" = "skipped" ] && [ "$DEPLOY" = "skipped" ] && [ "$LIGHT" = "success" ]'
_HEAVY_IF = "    if: needs.changes.outputs.backend == 'true'\n"
_LIGHT_IF = "    if: needs.changes.outputs.backend == 'false'\n"
_LIGHT_CMD = "pytest tests/test_docs_index.py tests/test_pwa_support.py -q"
_SCOPE_RUN = "        run: python3 scripts/ci_scope.py\n"
_LIGHT_RUN = "        run: .venv/bin/python -m pytest tests/test_docs_index.py tests/test_pwa_support.py -q -p no:cacheprovider\n"
_UNIT_RUN_HEAD = "        run: .venv/bin/python -m pytest tests -q"
_GATE_UNIT_ENV = "          UNIT: ${{ needs.backend-unit.result }}\n"

MUTATIONS = {
    "drop_worksteal": (lambda t: _mutate(t, " --dist worksteal", "", count=2), _assert_parallel, UNIT),
    "append_n0_override": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " -n 0", count=1), _assert_parallel, UNIT),
    "append_dist_load_override": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " --dist load", count=1), _assert_parallel, UNIT),
    "glob_via_ignore_instead_of_ignore_glob": (lambda t: _mutate(t, "--ignore-glob='tests/test_deploy_*.py'", "--ignore='tests/test_deploy_*.py'", count=1), _assert_partition, None),
    "drop_migrations_exclusion": (lambda t: _mutate(t, " --ignore=tests/test_migrations.py", "", count=1), _assert_partition, None),
    "deploy_list_diverges": (lambda t: _mutate(t, _DEPLOY_CMD_HEAD, "pytest tests/test_deploy_*.py", count=1), _assert_partition, None),
    "gate_always_moved_to_step": (lambda t: _mutate(_mutate(t, "    if: always()\n", "", count=1),
                                                   _GATE_STEP, _GATE_STEP + "        if: always()\n", count=1), _assert_gate, None),
    "gate_step_gets_its_own_condition": (lambda t: _mutate(t, _GATE_STEP, _GATE_STEP + "        if: success()\n", count=1), _assert_gate, None),
    "gate_success_check_replaced_by_echo": (lambda t: _mutate(t, _GATE_FULL, 'echo "done"', count=1), _assert_gate, None),
    "gate_always_removed": (lambda t: _mutate(t, "    if: always()\n", "", count=1), _assert_gate, None),
    "gate_needs_only_one_job": (lambda t: _mutate(t, _GATE_NEEDS, "    needs: [changes, backend-unit, backend-light]\n", count=1), _assert_gate, None),
    # ── issue #288:按改动面选 job ──
    # 判定 job 失败(output 为空)时不能放行
    "gate_ignores_changes_result": (lambda t: _mutate(t, '          [ "$CHANGES" = "success" ] || exit 1\n', "", count=1), _assert_gate, None),
    # 「不是 true」不等于「是 false」:空串 / 意外取值不能走轻量路径
    "gate_treats_unknown_backend_as_light": (lambda t: _mutate(t, 'elif [ "$BACKEND" = "false" ]; then', 'elif [ "$BACKEND" != "true" ]; then', count=1), _assert_gate, None),
    "gate_light_path_does_not_require_light_job": (lambda t: _mutate(t, _GATE_LIGHT, '[ "$UNIT" = "skipped" ] && [ "$DEPLOY" = "skipped" ]', count=1), _assert_gate, None),
    "gate_light_path_accepts_any_heavy_result": (lambda t: _mutate(t, _GATE_LIGHT, '[ "$LIGHT" = "success" ]', count=1), _assert_gate, None),
    "gate_full_path_accepts_skipped": (lambda t: _mutate(t, _GATE_FULL, '[ "$UNIT" != "failure" ] && [ "$DEPLOY" != "failure" ]', count=1), _assert_gate, None),
    "gate_backend_hardwired": (lambda t: _mutate(t, "          BACKEND: ${{ needs.changes.outputs.backend }}\n", "          BACKEND: 'false'\n", count=1), _assert_gate, None),
    "gate_needs_drop_changes": (lambda t: _mutate(t, _GATE_NEEDS, "    needs: [backend-unit, backend-deploy, backend-light]\n", count=1), _assert_gate, None),
    "scope_output_hardwired": (lambda t: _mutate(t, "      backend: ${{ steps.scope.outputs.backend }}\n", "      backend: 'false'\n", count=1), _assert_scope, None),
    "scope_step_replaced_by_constant": (lambda t: _mutate(t, _SCOPE_RUN, '        run: echo "backend=false" >> "$GITHUB_OUTPUT"\n', count=1), _assert_scope, None),
    "scope_extra_step_writes_output": (lambda t: _mutate(t, _SCOPE_RUN, _SCOPE_RUN + '      - run: echo "backend=false" >> "$GITHUB_OUTPUT"\n', count=1), _assert_scope, None),
    "scope_job_made_conditional": (lambda t: _mutate(t, "    name: changes (scope)\n", "    name: changes (scope)\n    if: github.event_name == 'pull_request'\n", count=1), _assert_scope, None),
    "scope_fetch_depth_dropped": (lambda t: _mutate(t, "        with:\n          fetch-depth: 2\n", "", count=1), _assert_scope, None),
    "scope_pr_head_not_passed": (lambda t: _mutate(t, "          PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}\n", "          PR_HEAD_SHA: ''\n", count=1), _assert_scope, None),
    "heavy_jobs_run_unconditionally": (lambda t: _mutate(t, _HEAVY_IF, "", count=2), _assert_scope, None),
    "heavy_job_condition_loosened": (lambda t: _mutate(t, _HEAVY_IF, "    if: needs.changes.outputs.backend != 'false'\n", count=2), _assert_scope, None),
    "light_condition_inverted": (lambda t: _mutate(t, _LIGHT_IF, _HEAVY_IF, count=1), _assert_scope, None),
    "light_list_drops_a_guard": (lambda t: _mutate(t, _LIGHT_CMD, "pytest tests/test_docs_index.py -q", count=1), _assert_scope, None),
    "light_list_adds_unlisted_file": (lambda t: _mutate(t, _LIGHT_CMD, "pytest tests/test_docs_index.py tests/test_pwa_support.py tests/test_admin_ops.py -q", count=1), _assert_scope, None),
    "light_deselects_everything": (lambda t: _mutate(t, _LIGHT_CMD, "pytest tests/test_docs_index.py tests/test_pwa_support.py -k nothing -q", count=1), _assert_scope, None),
    # ── Codex R1-3:汇总门那一步真的会执行、拿到的真的是上游结果 ──
    "gate_step_disabled_by_leading_if": (lambda t: _mutate(t, _GATE_STEP, "      - if: false\n        name: Require the backend jobs this change needs\n", count=1), _assert_gate, None),
    "gate_env_shadowed_by_step_constant": (lambda t: _mutate(_mutate(t, _GATE_UNIT_ENV, "          UNIT: success\n", count=1),
                                                             _GATE_NEEDS, _GATE_NEEDS + "    env:\n      UNIT: ${{ needs.backend-unit.result }}\n", count=1), _assert_gate, None),
    "gate_env_defined_twice": (lambda t: _mutate(t, _GATE_UNIT_ENV, _GATE_UNIT_ENV + "          UNIT: success\n", count=1), _assert_gate, None),
    "gate_gets_a_second_step": (lambda t: _mutate(t, "          fi\n", "          fi\n      - run: echo ok\n", count=1), _assert_gate, None),
    "gate_shell_overridden": (lambda t: _mutate(t, _GATE_STEP, _GATE_STEP + "        shell: bash {0}\n", count=1), _assert_gate, None),
    # ── Codex R1-4:「job 成功」必须等于「用例通过」 ──
    "light_step_conditional": (lambda t: _mutate(t, _LIGHT_RUN, "        if: github.event_name == 'push'\n" + _LIGHT_RUN, count=1), _assert_plain_pytest_step, LIGHT),
    "light_step_swallows_failure": (lambda t: _mutate(t, _LIGHT_RUN, "        continue-on-error: true\n" + _LIGHT_RUN, count=1), _assert_plain_pytest_step, LIGHT),
    "light_step_collect_only_via_env": (lambda t: _mutate(t, _LIGHT_RUN, "        env:\n          PYTEST_ADDOPTS: --collect-only\n" + _LIGHT_RUN, count=1), _assert_plain_pytest_step, LIGHT),
    "light_command_folded_or_true": (lambda t: _mutate(t, _LIGHT_RUN, _LIGHT_RUN + "          || true\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_command_chained_or_true": (lambda t: _mutate(t, _LIGHT_RUN, _LIGHT_RUN[:-1] + " || true\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_job_level_env": (lambda t: _mutate(t, _LIGHT_IF, _LIGHT_IF + "    env:\n      PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1'\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_job_continue_on_error": (lambda t: _mutate(t, _LIGHT_IF, _LIGHT_IF + "    continue-on-error: true\n", count=1), _assert_plain_pytest_step, LIGHT),
    "unit_step_swallows_failure": (lambda t: _mutate(t, _UNIT_RUN_HEAD, "        continue-on-error: true\n" + _UNIT_RUN_HEAD, count=1), _assert_plain_pytest_step, UNIT),
    "unit_command_chained_or_true": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " || true", count=1), _assert_plain_pytest_step, UNIT),
    "deploy_command_folded_or_true": (lambda t: _mutate(t, "--durations=25\n", "--durations=25\n          || true\n", count=1), _assert_plain_pytest_step, DEPLOY),
    "addopts_set_anywhere": (lambda t: _mutate(t, "concurrency:\n", "env:\n  PYTEST_ADDOPTS: --collect-only\n\nconcurrency:\n", count=1), _assert_plain_pytest_step, UNIT),
    "released_migrations_guard_dropped": (lambda t: _mutate(t, "        run: python3 scripts/check_released_migrations.py\n", "        run: echo skip\n", count=1), _assert_released_migrations_guard, None),
    "released_migrations_guard_soft_failed": (lambda t: _mutate(t, "        run: python3 scripts/check_released_migrations.py\n",
                                                             "        continue-on-error: true\n        run: python3 scripts/check_released_migrations.py\n", count=1), _assert_released_migrations_guard, None),
    "released_migrations_guard_conditional": (lambda t: _mutate(t, "        run: python3 scripts/check_released_migrations.py\n",
                                                             "        if: github.event_name == 'push'\n        run: python3 scripts/check_released_migrations.py\n", count=1), _assert_released_migrations_guard, None),
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
