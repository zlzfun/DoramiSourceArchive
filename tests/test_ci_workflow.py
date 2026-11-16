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
   (changes 结果 × backend 取值 × 四个 job 结果,共 4096 种)只放行两条路径:`true` 且两个重型 job 与 backend-guards 都 success,
   或 `false` 且这三个都 skipped 且 backend-light success——判定出错(output 为空)与一切其它组合都拒。
   判定脚本自身与「跳过不丢覆盖」的读取审计在 tests/test_ci_scope.py。
5. 「job 成功」等于「用例通过」(Codex R1-3 / R1-4):汇总门那一步的形状恰是 name + env(五个变量各一次)+ `run: |`,
   job 层没有 env;三个后端 job 里跑 pytest 的那一步只能是 name + 单行 run,job 层只认 name / needs / if / runs-on / steps,
   ci.yml 任何位置不得设置 `PYTEST_ADDOPTS`。挂条件、continue-on-error、同名 env 覆盖、折行接 `|| true` 都在反向对照里。
6. 分片(issue #289):两个重型 job 各用矩阵拆成 N 片,命令只差 `--shard I/N`。strategy 块只认三行受控格式(多一行都拒),
   矩阵恰是 1..N、分母等于 N、分子必须是矩阵变量;端到端把矩阵值代入**真实命令的分片参数**真跑 collect-only,证明互不相交、
   并集为全集。检查分片的用例不能只待在被分片的 job 里(配置写错时它们可能正落在没人跑的那一片):`backend-guards` 不分片地
   单独跑本文件与 tests/test_ci_scope.py,汇总门的全量路径要求它成功(真值表因此是六个变量、4096 种组合)。
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
CHANGES, LIGHT, GUARDS = "changes", "backend-light", "backend-guards"
RESULTS = ("success", "failure", "cancelled", "skipped")
# changes 没成功时它的 output 是空串;大小写不同的值也不是放行
BACKEND_VALUES = ("true", "false", "", "TRUE")
_OPTS_WITH_VALUE = {"-n", "-p", "--dist", "-k", "-m", "-o", "-c", "--shard"}
# 汇总门 shell 变量 → 工作流表达式
_GATE_ENV = {
    "CHANGES": "needs.changes.result",
    "BACKEND": "needs.changes.outputs.backend",
    "UNIT": "needs.backend-unit.result",
    "DEPLOY": "needs.backend-deploy.result",
    "LIGHT": "needs.backend-light.result",
    "GUARDS": "needs.backend-guards.result",
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
# 分片的两个 job 多一个 strategy(issue #289;矩阵本身的形状由 _assert_shards 守)
_SHARDED = {"backend-unit", "backend-deploy"}
# CI 的 pytest 命令只许出现这些选项(各自的取值另有守卫:-n / --dist 见 _assert_parallel,--shard 见 _assert_shards)
_PYTEST_OPTS_WITH_VALUE = {"-p", "-n", "--dist", "--shard"}
_PYTEST_PLAIN_OPTS = re.compile(r"-q|--durations=[0-9]+|--ignore=[^-]\S*|--ignore-glob=[^-]\S*")


def _job_keys(text: str, job: str) -> dict[str, str]:
    """job 层的键,严格版(Codex #289 R1-2 的同类处):四空格缩进的有效行必须都是不带引号的 `key:`。
    `"continue-on-error": true` 这样带引号的写法 YAML 照认,而按 `^    key:` 找键的宽松解析根本看不见它。"""
    block = _code(_job_block(text, job))
    for line in block.splitlines():
        if line.strip() and line.startswith("    ") and not line.startswith("     "):
            assert re.match(r"^    [A-Za-z0-9_-]+:(\s|$)", line), f"{job} 的 job 层出现了守卫不认的写法:{line!r}"
    return _job_level(block)


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


def _gate_should_pass(changes: str, backend: str, unit: str, deploy: str, light: str, guards: str) -> bool:
    """汇总门的真值表,独立于 ci.yml 里那段 shell 重述一遍。"""
    if changes != "success":
        return False
    if backend == "true":
        return unit == "success" and deploy == "success" and guards == "success"
    if backend == "false":
        return unit == "skipped" and deploy == "skipped" and guards == "skipped" and light == "success"
    return False


def _gate_combos() -> list[tuple[str, ...]]:
    return list(itertools.product(RESULTS, BACKEND_VALUES, *([RESULTS] * (len(_GATE_ENV) - 2))))


def _gate_wrong(script: str, limit: int = 3) -> list[str]:
    """真跑汇总门那段 shell,返回判错的组合(攒够 limit 条就停——突变体通常前几百种里就露馅,不必跑完四千种)。"""

    def run(combo: tuple[str, ...]) -> str | None:
        env = {**os.environ, **dict(zip(_GATE_ENV, combo))}
        r = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True, timeout=30)
        expect_pass = _gate_should_pass(*combo)
        if (r.returncode == 0) == expect_pass:
            return None
        return f"{dict(zip(_GATE_ENV, combo))} rc={r.returncode}(应{'通过' if expect_pass else '失败'})"

    combos, wrong = _gate_combos(), []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for start in range(0, len(combos), 256):
            wrong += [w for w in pool.map(run, combos[start:start + 256]) if w]
            if len(wrong) >= limit:
                break
    return wrong


def _assert_gate(text: str) -> None:
    block = _code(_job_block(text, GATE))
    top = _job_keys(text, GATE)
    assert top.get("name") == "backend (pytest)", "汇总门 job 的显示名必须仍是 `backend (pytest)`(分支保护 required)"
    needs = re.fullmatch(r"\[(.*)\]", top.get("needs", ""))
    assert needs and {s.strip() for s in needs.group(1).split(",")} == {CHANGES, UNIT, DEPLOY, LIGHT, GUARDS}, (
        "汇总门必须在 job 层 needs 改动面判定与四个后端 job"
    )
    assert top.get("if") == "always()", "汇总门必须在 job 层 if: always(),否则上游失败时被 skip(skip 在分支保护眼里等于通过)"
    assert "    steps:" in block, "汇总门没有 steps"
    steps_text = block.split("    steps:", 1)[1]
    assert not re.search(r"^\s+(?:- )?if:", steps_text, re.M), "汇总门的 step 不得再挂 if(step 条件救不回被 skip 的 job,却能让核对步骤被跳过)"
    assert "continue-on-error" not in block, "汇总门不得 continue-on-error"
    assert set(top) == _JOB_KEYS, f"汇总门的 job 层只能有 {sorted(_JOB_KEYS)}(job 层的 env 会被 step 层同名项覆盖,守卫只认 step 层那一份),实得 {sorted(top)}"
    # 真值表跑的是下面提取出的那段 shell;要让它等于 runner 上真正执行的东西,这一步的形状必须恰好是
    # name + env(_GATE_ENV 的每个变量各一次、各接各的表达式)+ `run: |` 脚本——多一个键(if / shell / 第二份 env / 第二步)都不认。
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
        f"汇总门的 env 必须恰好把这 {n} 个表达式各传一次(写死成常量等于让门禁恒走某一条路径),实得 {env}"
    )
    assert all(line.startswith("          ") for line in step[n + 3:]), "`run: |` 之后只能是脚本正文"
    script = _run_block(block)
    wrong = _gate_wrong(script)
    assert not wrong, f"汇总门有组合判错,如:{wrong[:3]}"
    assert sum(_gate_should_pass(*c) for c in _gate_combos()) == len(RESULTS) + 1, "真值表应恰有 4(全量,light 任意)+ 1(轻量)种放行组合"
    for job in (UNIT, DEPLOY, LIGHT, GUARDS):
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
    for job in (UNIT, DEPLOY, GUARDS):
        level = _job_keys(text, job)
        assert level.get("needs") == "[changes]" and level.get("if") == "needs.changes.outputs.backend == 'true'", (
            f"{job} 必须 needs: [changes] 且只在 backend == 'true' 时运行"
        )
    level = _job_keys(text, LIGHT)
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
    # 守卫工作流自己的测试另起一个不分片的 job(issue #289,Codex R1-1):清单与 GUARD_TESTS 逐字一致,不筛、不分片
    assert GUARDS in cmds, f"{GUARDS} 里没有 pytest 命令"
    argv = cmds[GUARDS]
    pos, ignores = _split_argv(argv)
    guard_tests = _ci_scope().GUARD_TESTS
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() in guard_tests, "GUARD_TESTS 必须包含本文件"
    assert tuple(pos) == tuple(guard_tests), f"{GUARDS} 的文件清单必须与 ci_scope.GUARD_TESTS 逐字一致:{pos} vs {list(guard_tests)}"
    options = argv[argv.index("pytest") + 1:]
    assert not ignores and not any(a in ("-k", "-m", "--deselect", "--shard") or a.startswith(("--deselect=", "--shard=")) for a in options), (
        f"{GUARDS} 不得筛选用例,更不得带 --shard(它存在的意义就是不受分片配置影响)"
    )
    assert any(a == "-p" and b == "no:cacheprovider" for a, b in zip(argv, argv[1:])), f"{GUARDS} 应带 -p no:cacheprovider"
    # 两边互为对照:这几份守卫同时仍在 unit 的划分里(某一片会跑到它们)
    assert set(pos) <= all_files - _pytest_ignored(all_files, unit_ign), "GUARD_TESTS 必须同时留在 backend-unit 的划分里"


def _assert_plain_pytest_step(text: str, job: str) -> None:
    """跑 pytest 的那一步只能是 `name` + 单行 `run`(Codex R1-4):挂条件、吞失败、用环境变量改 pytest 行为、
    把命令折行接 `|| true`——每一种都让「job 成功」不再等于「用例通过」,而命令文本看起来原封不动。"""
    assert "PYTEST_ADDOPTS" not in _code(text), "ci.yml 不得设置 PYTEST_ADDOPTS(一句 --collect-only 就让所有用例只收集不执行)"
    block = _job_block(text, job)
    extra = set(_job_keys(text, job)) - _JOB_KEYS - ({"strategy"} if job in _SHARDED else set())
    assert not extra, f"{job} 的 job 层出现了守卫不认识的键 {sorted(extra)}"
    hits = [step for step in _steps(block) if any(re.search(r"python -m pytest ", line) for line in step)]
    assert len(hits) == 1, f"{job} 里应恰有一步跑 pytest(实得 {len(hits)})"
    step = hits[0]
    assert len(step) == 2 and re.fullmatch(r"      - name: .+", step[0]) and re.fullmatch(r"        run: \S*python -m pytest .+", step[1]), (
        f"{job} 跑 pytest 的那一步只能有 name 与单行 run,实得 {step}"
    )
    # 看原始命令串而不是分词结果:`…cacheprovider||true` 不带空格时 shlex 不会把 `||` 拆成独立的词(Codex R2 观察项)
    command = step[1].split("run: ", 1)[1]
    assert not re.search(r"[|&;<>`]|\$\(", command), f"{job} 的 pytest 命令里不得串接、重定向或嵌入别的命令:{command!r}"
    # 选项白名单(Codex #289 R2 观察项):`--collect-only` 只收集不执行、`-x` 见红就停、`-k` / `-m` 筛掉用例——
    # 命令照样成功,测的东西却少了。认得的才放行,不去枚举坏选项。
    argv = shlex.split(command)
    options, i = argv[argv.index("pytest") + 1:], 0
    while i < len(options):
        tok = options[i]
        if tok in _PYTEST_OPTS_WITH_VALUE:
            assert i + 1 < len(options), f"{job}:{tok} 缺取值"
            if tok == "-p":
                assert options[i + 1] == "no:cacheprovider", f"{job}:-p 只许 no:cacheprovider(实得 {options[i + 1]!r})"
            i += 2
            continue
        assert not tok.startswith("-") or _PYTEST_PLAIN_OPTS.fullmatch(tok), f"{job} 的 pytest 命令里出现了守卫不认的选项 {tok!r}"
        i += 1


def _strategy_lines(text: str, job: str) -> list[str]:
    """job 的 strategy 块里**全部**有效行(去注释、去空行):从 `strategy:` 的下一行到缩进回到 job 层为止。"""
    lines = _code(_job_block(text, job)).splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^    [\"']?strategy[\"']?\s*:", line)]
    assert len(starts) == 1 and lines[starts[0]] == "    strategy:", f"{job} 须恰有一个 job 层的 `strategy:`(不带引号、行内无值;分片靠矩阵)"
    out = []
    for line in lines[starts[0] + 1:]:
        if not line.strip():
            continue  # 空行不结束这个块(Codex R1-2:原先的正则遇空行就停,空行后面的 exclude 整个漏掉)
        if not line.startswith("     "):
            break
        out.append(line)
    return out


def _shard_matrix(text: str, job: str) -> list[int]:
    """job 的 `strategy.matrix.shard` 取值。strategy 块只认恰好这三行:`fail-fast: false` / `matrix:` / `shard: [整数行内列表]`。
    多出任何一行——`include` / `exclude`(带不带引号、前面隔不隔空行)、第二个维度、`max-parallel`、多行列表——一律拒:
    每一种都可能让实际展开的片不等于这里读到的,而受控格式之外的写法这个解析器看不懂就不该放行。"""
    lines = _strategy_lines(text, job)
    assert len(lines) == 3 and lines[0] == "      fail-fast: false" and lines[1] == "      matrix:", (
        f"{job} 的 strategy 只能是 fail-fast: false / matrix: / shard: [...] 三行,实得 {lines}"
    )
    values = re.fullmatch(r"        shard: \[([0-9]+(?:, [0-9]+)*)\]", lines[2])
    assert values, f"{job} 的矩阵只能有一行 `shard: [1, 2, ...]`(行内整数列表),实得 {lines[2]!r}"
    return [int(v) for v in values.group(1).split(", ")]


def _assert_shards(text: str, job: str) -> None:
    """issue #289:矩阵取值恰是 1..N,命令里恰有一次 `--shard ${{matrix.shard}}/N`——少一片 = 那一片的用例谁都不跑而 CI 仍绿。"""
    values = _shard_matrix(text, job)
    total = len(values)
    assert total >= 2 and values == list(range(1, total + 1)), f"{job} 的 matrix.shard 必须恰是 1..N(实得 {values})"
    argv = _pytest_commands(text)[job]
    assert argv.count("--shard") == 1 and not any(a.startswith("--shard=") for a in argv), f"{job}:--shard 须恰好出现一次(空格分隔)"
    spec = argv[argv.index("--shard") + 1]
    assert spec == "${{matrix.shard}}/" + str(total), (
        f"{job} 的 --shard 必须是 `${{{{matrix.shard}}}}/{total}`(分子取矩阵变量、分母等于片数),实得 {spec!r}"
    )


def _shard_args(text: str, job: str, value: int) -> list[str]:
    """真实命令里**全部**与分片有关的参数,按出现顺序、矩阵变量代入 value——pytest 对重复选项取最后一个,这里原样交给它。"""
    argv, out = _pytest_commands(text)[job], []
    for i, tok in enumerate(argv):
        if tok == "--shard":
            out += ["--shard", argv[i + 1].replace("${{matrix.shard}}", str(value))]
        elif tok.startswith("--shard="):
            out.append(tok.replace("${{matrix.shard}}", str(value)))
    return out


def _collected(*args: str) -> set[str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert r.returncode in (0, 5), r.stdout[-2000:] + r.stderr[-2000:]  # 5 = 这一片在样本里一条都没有
    return {line.strip() for line in r.stdout.splitlines() if "::" in line}


_SHARD_SAMPLE = ("tests/test_ci_scope.py", "tests/test_content_classification.py", "tests/test_docs_index.py", "tests/test_deploy_lib.py")


def _assert_shards_partition(text: str, job: str) -> None:
    """端到端(Codex R1-1):矩阵的每个取值代入**真实命令里的分片参数**,真跑 `--collect-only`,各片互不相交、并集等于不分片时的全集。
    只读矩阵长度再自己拼 `--shard I/N` 是不够的——命令里多一个写死的 `--shard 1/3` 把矩阵值盖掉时,那样的检查照样通过。
    样本取参数化多、类里有用例、两侧文件都有的几个测试文件(整仓收集一次 4~5 s,这里不必)。"""
    values = _shard_matrix(text, job)
    with ThreadPoolExecutor(max_workers=len(values) + 1) as pool:
        whole = pool.submit(_collected, *_SHARD_SAMPLE)
        parts = [pool.submit(_collected, *_SHARD_SAMPLE, *_shard_args(text, job, v)) for v in values]
        whole, parts = whole.result(), [p.result() for p in parts]
    assert len(whole) > 50
    assert all(parts), f"{job}:样本下每一片都应有用例"
    assert sum(len(p) for p in parts) == len(whole) and set().union(*parts) == whole, (
        f"{job}:各片必须互不相交且并集等于全集(各片 {[len(p) for p in parts]},全集 {len(whole)})"
    )
    for part in parts:  # 同一个测试函数的参数化不拆到不同片
        functions = {nodeid.split("[", 1)[0] for nodeid in part}
        assert all(nodeid.split("[", 1)[0] not in functions for other in parts if other is not part for nodeid in other)


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


@pytest.mark.parametrize("job", [UNIT, DEPLOY, LIGHT, GUARDS])
def test_pytest_step_cannot_be_skipped_or_have_its_failure_swallowed(job):
    _assert_plain_pytest_step(CI.read_text(encoding="utf-8"), job)


@pytest.mark.parametrize("job", [UNIT, DEPLOY])
def test_backend_jobs_are_sharded_by_a_complete_matrix(job):
    _assert_shards(CI.read_text(encoding="utf-8"), job)


@pytest.mark.parametrize("job", [UNIT, DEPLOY])
def test_shards_cover_every_test_exactly_once(job):
    _assert_shards_partition(CI.read_text(encoding="utf-8"), job)


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1", "1/0", "a/b", "1/3/5", "-1/3"])
def test_malformed_shard_spec_is_a_usage_error_not_a_silent_full_or_empty_run(spec):
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_docs_index.py", "--collect-only", "-q", "-p", "no:cacheprovider", f"--shard={spec}"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert r.returncode == 4 and "--shard" in r.stderr, (r.returncode, r.stdout[-500:], r.stderr[-500:])


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
_GATE_NEEDS = "    needs: [changes, backend-unit, backend-deploy, backend-light, backend-guards]\n"
_GATE_FULL = '[ "$UNIT" = "success" ] && [ "$DEPLOY" = "success" ] && [ "$GUARDS" = "success" ]'
_GATE_LIGHT = '[ "$UNIT" = "skipped" ] && [ "$DEPLOY" = "skipped" ] && [ "$GUARDS" = "skipped" ] && [ "$LIGHT" = "success" ]'
_HEAVY_IF = "    if: needs.changes.outputs.backend == 'true'\n"
_LIGHT_IF = "    if: needs.changes.outputs.backend == 'false'\n"
_LIGHT_CMD = "pytest tests/test_docs_index.py tests/test_pwa_support.py -q"
_SCOPE_RUN = "        run: python3 scripts/ci_scope.py\n"
_LIGHT_RUN = "        run: .venv/bin/python -m pytest tests/test_docs_index.py tests/test_pwa_support.py -q -p no:cacheprovider\n"
_UNIT_RUN_HEAD = "        run: .venv/bin/python -m pytest tests -q"
_GATE_UNIT_ENV = "          UNIT: ${{ needs.backend-unit.result }}\n"
_GUARDS_RUN = "        run: .venv/bin/python -m pytest tests/test_ci_workflow.py tests/test_ci_scope.py -q -p no:cacheprovider\n"


def _in_job(text: str, job: str, old: str, new: str) -> str:
    """只在某个 job 的块里做替换(两个分片 job 的片数相同时,矩阵那一行在全文里会出现两次)。"""
    block = _job_block(text, job)
    assert block.count(old) == 1, f"突变锚点在 {job} 里出现 {block.count(old)} 次:{old!r}"
    assert text.count(block) == 1
    return text.replace(block, block.replace(old, new))


def _matrix_line(values: list[int]) -> str:
    return "        shard: [" + ", ".join(str(v) for v in values) + "]\n"


def _shard_mutation(job: str, kind: str):
    """分片突变按 ci.yml **当前**的片数生成(Codex R1-4:写死 `[1, 2, 3]` / `/3` 的锚点,改片数时要跟着改一串测试)。"""

    def mutate(text: str) -> str:
        values = _shard_matrix(text, job)
        n, line = len(values), _matrix_line(values)
        spec = " --shard ${{matrix.shard}}/" + str(n)
        if kind == "misses_a_shard":
            return _in_job(text, job, line, _matrix_line(values[:-1]))
        if kind == "starts_at_zero":
            return _in_job(text, job, line, _matrix_line([v - 1 for v in values]))
        if kind == "repeats_a_shard":
            return _in_job(text, job, line, _matrix_line(values[:-1] + [values[-2]]))
        if kind == "denominator_drifts":
            return _in_job(text, job, spec, " --shard ${{matrix.shard}}/" + str(n + 1))
        if kind == "hardwired":
            return _in_job(text, job, spec, f" --shard 1/{n}")
        if kind == "option_dropped":
            return _in_job(text, job, spec, "")
        if kind == "excludes_a_shard":
            return _in_job(text, job, line, line + f"        exclude:\n          - shard: {n}\n")
        if kind == "excludes_after_blank_line":
            return _in_job(text, job, line, line + f"\n        exclude: [{{shard: {n}}}]\n")
        if kind == "excludes_with_quoted_key":
            return _in_job(text, job, line, line + f'        "exclude": [{{shard: {n}}}]\n')
        if kind == "includes_extra_leg":
            return _in_job(text, job, line, line + "        include:\n          - shard: 1\n")
        if kind == "gains_a_dimension":
            return _in_job(text, job, line, line + "        os: [a, b]\n")
        if kind == "max_parallel_added":
            return _in_job(text, job, "      matrix:\n", "      max-parallel: 1\n      matrix:\n")
        if kind == "multiline_list":
            return _in_job(text, job, line, "        shard:\n" + "".join(f"          - {v}\n" for v in values))
        if kind == "quoted_strategy_key":
            return _in_job(text, job, "    strategy:\n", '    "strategy":\n')
        if kind == "matrix_removed":
            return _in_job(text, job, "    strategy:\n      fail-fast: false\n      matrix:\n" + line, "")
        raise AssertionError(kind)

    return mutate


def _fold_or_true(text: str, job: str) -> str:
    line = next(l for l in _job_block(text, job).splitlines() if re.search(r"run: \S*python -m pytest ", l))
    return _in_job(text, job, line + "\n", line + "\n          || true\n")


_SHARD_MUTATION_KINDS = (
    "misses_a_shard", "starts_at_zero", "repeats_a_shard", "denominator_drifts", "hardwired", "option_dropped",
    "excludes_a_shard", "excludes_after_blank_line", "excludes_with_quoted_key", "includes_extra_leg", "gains_a_dimension",
    "max_parallel_added", "multiline_list", "quoted_strategy_key", "matrix_removed",
)

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
    "gate_needs_only_one_job": (lambda t: _mutate(t, _GATE_NEEDS, "    needs: [changes, backend-unit, backend-light, backend-guards]\n", count=1), _assert_gate, None),
    # ── issue #288:按改动面选 job ──
    # 判定 job 失败(output 为空)时不能放行
    "gate_ignores_changes_result": (lambda t: _mutate(t, '          [ "$CHANGES" = "success" ] || exit 1\n', "", count=1), _assert_gate, None),
    # 「不是 true」不等于「是 false」:空串 / 意外取值不能走轻量路径
    "gate_treats_unknown_backend_as_light": (lambda t: _mutate(t, 'elif [ "$BACKEND" = "false" ]; then', 'elif [ "$BACKEND" != "true" ]; then', count=1), _assert_gate, None),
    "gate_light_path_does_not_require_light_job": (lambda t: _mutate(t, _GATE_LIGHT, '[ "$UNIT" = "skipped" ] && [ "$DEPLOY" = "skipped" ] && [ "$GUARDS" = "skipped" ]', count=1), _assert_gate, None),
    "gate_light_path_accepts_any_heavy_result": (lambda t: _mutate(t, _GATE_LIGHT, '[ "$LIGHT" = "success" ]', count=1), _assert_gate, None),
    "gate_full_path_accepts_skipped": (lambda t: _mutate(t, _GATE_FULL, '[ "$UNIT" != "failure" ] && [ "$DEPLOY" != "failure" ] && [ "$GUARDS" != "failure" ]', count=1), _assert_gate, None),
    "gate_backend_hardwired": (lambda t: _mutate(t, "          BACKEND: ${{ needs.changes.outputs.backend }}\n", "          BACKEND: 'false'\n", count=1), _assert_gate, None),
    "gate_needs_drop_changes": (lambda t: _mutate(t, _GATE_NEEDS, "    needs: [backend-unit, backend-deploy, backend-light, backend-guards]\n", count=1), _assert_gate, None),
    "scope_output_hardwired": (lambda t: _mutate(t, "      backend: ${{ steps.scope.outputs.backend }}\n", "      backend: 'false'\n", count=1), _assert_scope, None),
    "scope_step_replaced_by_constant": (lambda t: _mutate(t, _SCOPE_RUN, '        run: echo "backend=false" >> "$GITHUB_OUTPUT"\n', count=1), _assert_scope, None),
    "scope_extra_step_writes_output": (lambda t: _mutate(t, _SCOPE_RUN, _SCOPE_RUN + '      - run: echo "backend=false" >> "$GITHUB_OUTPUT"\n', count=1), _assert_scope, None),
    "scope_job_made_conditional": (lambda t: _mutate(t, "    name: changes (scope)\n", "    name: changes (scope)\n    if: github.event_name == 'pull_request'\n", count=1), _assert_scope, None),
    "scope_fetch_depth_dropped": (lambda t: _mutate(t, "        with:\n          fetch-depth: 2\n", "", count=1), _assert_scope, None),
    "scope_pr_head_not_passed": (lambda t: _mutate(t, "          PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}\n", "          PR_HEAD_SHA: ''\n", count=1), _assert_scope, None),
    "heavy_jobs_run_unconditionally": (lambda t: _mutate(t, _HEAVY_IF, "", count=3), _assert_scope, None),
    "heavy_job_condition_loosened": (lambda t: _mutate(t, _HEAVY_IF, "    if: needs.changes.outputs.backend != 'false'\n", count=3), _assert_scope, None),
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
    "light_command_chained_without_spaces": (lambda t: _mutate(t, _LIGHT_RUN, _LIGHT_RUN[:-1] + "||true\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_command_backgrounded": (lambda t: _mutate(t, _LIGHT_RUN, _LIGHT_RUN[:-1] + "&\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_job_level_env": (lambda t: _mutate(t, _LIGHT_IF, _LIGHT_IF + "    env:\n      PYTEST_DISABLE_PLUGIN_AUTOLOAD: '1'\n", count=1), _assert_plain_pytest_step, LIGHT),
    "light_job_continue_on_error": (lambda t: _mutate(t, _LIGHT_IF, _LIGHT_IF + "    continue-on-error: true\n", count=1), _assert_plain_pytest_step, LIGHT),
    "unit_step_swallows_failure": (lambda t: _mutate(t, _UNIT_RUN_HEAD, "        continue-on-error: true\n" + _UNIT_RUN_HEAD, count=1), _assert_plain_pytest_step, UNIT),
    "unit_command_chained_or_true": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " || true", count=1), _assert_plain_pytest_step, UNIT),
    "deploy_command_folded_or_true": (lambda t: _fold_or_true(t, DEPLOY), _assert_plain_pytest_step, DEPLOY),
    "addopts_set_anywhere": (lambda t: _mutate(t, "concurrency:\n", "env:\n  PYTEST_ADDOPTS: --collect-only\n\nconcurrency:\n", count=1), _assert_plain_pytest_step, UNIT),
    # ── issue #289:矩阵分片(按当前片数生成,见 _shard_mutation)──
    **{f"{job}_shard_{kind}": (_shard_mutation(job, kind), _assert_shards, job) for job in (UNIT, DEPLOY) for kind in _SHARD_MUTATION_KINDS},
    "unit_shard_overridden_later": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " --shard 1/1", count=1), _assert_shards, UNIT),
    # Codex R1-1 的现场:命令末尾多一个写死的第一片——pytest 取后值,三个 job 全跑第一片。结构守卫与端到端各拦一次。
    "unit_shard_overridden_by_first_shard": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + f" --shard 1/{len(_shard_matrix(t, UNIT))}", count=1), _assert_shards, UNIT),
    "unit_shard_overridden_by_first_shard_end_to_end": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + f" --shard 1/{len(_shard_matrix(t, UNIT))}", count=1), _assert_shards_partition, UNIT),
    "unit_shard_dropped_end_to_end": (_shard_mutation(UNIT, "option_dropped"), _assert_shards_partition, UNIT),
    "light_job_gets_a_matrix": (lambda t: _mutate(t, _LIGHT_IF, _LIGHT_IF + "    strategy:\n      matrix:\n        shard: [1, 2]\n", count=1), _assert_plain_pytest_step, LIGHT),
    "unit_job_level_key_quoted": (lambda t: _in_job(t, UNIT, "    runs-on: ubuntu-latest\n", '    runs-on: ubuntu-latest\n    "continue-on-error": true\n'), _assert_plain_pytest_step, UNIT),
    # ── issue #289:不分片的 backend-guards ──
    "gate_full_path_does_not_require_guards": (lambda t: _mutate(t, _GATE_FULL, '[ "$UNIT" = "success" ] && [ "$DEPLOY" = "success" ]', count=1), _assert_gate, None),
    "gate_needs_drop_guards": (lambda t: _mutate(t, _GATE_NEEDS, "    needs: [changes, backend-unit, backend-deploy, backend-light]\n", count=1), _assert_gate, None),
    "guards_job_sharded": (lambda t: _mutate(t, _GUARDS_RUN, _GUARDS_RUN[:-1] + " --shard 1/3\n", count=1), _assert_scope, None),
    "guards_list_drops_workflow_guard": (lambda t: _mutate(t, _GUARDS_RUN, _GUARDS_RUN.replace("tests/test_ci_workflow.py ", ""), count=1), _assert_scope, None),
    "guards_filtered": (lambda t: _mutate(t, _GUARDS_RUN, _GUARDS_RUN[:-1] + " -k nothing\n", count=1), _assert_scope, None),
    "guards_run_only_in_light_mode": (lambda t: _in_job(t, GUARDS, _HEAVY_IF, _LIGHT_IF), _assert_scope, None),
    "guards_step_swallows_failure": (lambda t: _mutate(t, _GUARDS_RUN, "        continue-on-error: true\n" + _GUARDS_RUN, count=1), _assert_plain_pytest_step, GUARDS),
    # ── Codex #289 R2 观察项:命令成功但没真测 ──
    "guards_collect_only": (lambda t: _mutate(t, _GUARDS_RUN, _GUARDS_RUN[:-1] + " --collect-only\n", count=1), _assert_plain_pytest_step, GUARDS),
    "light_collect_only_short": (lambda t: _mutate(t, _LIGHT_RUN, _LIGHT_RUN[:-1] + " --co\n", count=1), _assert_plain_pytest_step, LIGHT),
    "unit_stops_at_first_failure": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " -x", count=1), _assert_plain_pytest_step, UNIT),
    "unit_maxfail": (lambda t: _mutate(t, _UNIT_CMD_TAIL, _UNIT_CMD_TAIL + " --maxfail=1", count=1), _assert_plain_pytest_step, UNIT),
    "deploy_filtered_by_keyword": (lambda t: _in_job(t, DEPLOY, " --durations=25", " --durations=25 -k migrations"), _assert_plain_pytest_step, DEPLOY),
    "unit_plugin_disabled": (lambda t: _in_job(t, UNIT, " -p no:cacheprovider", " -p no:cacheprovider -p no:xdist"), _assert_plain_pytest_step, UNIT),
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
