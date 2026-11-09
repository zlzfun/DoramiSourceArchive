"""自动部署脚本的进程级测试(issue #102,方案 §5 第 2 项)。

对象:仓库外 launcher(docker/dorami-deploy.example)与 worker(docker/dorami-deploy-worker.example)、
scripts/deploy-lib.sh 的协议 / 锁 / expected sha、deploy-docker.sh 的七步流程、scripts/verify-release-ref.sh。
手法:真 git(临时 bare origin + clone)、假 docker / curl / df 放临时 PATH、目标脚本用桩(worker 状态机测试)或真脚本
(deploy-docker.sh 测试)。不碰真实 docker / 网络。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "docker" / "dorami-deploy.example"
WORKER = ROOT / "docker" / "dorami-deploy-worker.example"
VERIFY_REF = ROOT / "scripts" / "verify-release-ref.sh"
# 本波之前(8996f82,v3.58.2 时代)的 deploy-docker.sh / deploy-lib.sh 原文固化为 fixture:自举测试要模拟
# 「生产机还停在旧 tag、旧脚本 checkout 新 tag 后以新脚本重执行」;不用 git show 取,CI 浅克隆拿不到那个提交。
OLD_SCRIPTS_DIR = ROOT / "tests" / "fixtures" / "deploy_scripts_pre_issue_102"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")

FAKE_DOCKER = r'''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DOCKER_DIR"]
args = sys.argv[1:]
def rd(name, default=""):
    p = os.path.join(d, name)
    return open(p).read() if os.path.exists(p) else default
def rc(name):
    return int((rd(name, "0").strip() or "0"))
extra = ""
if args[:2] == ["compose", "up"]:
    mark = os.environ.get("DORAMI_DEPLOY_SWITCH_MARK", "")
    extra = " switch_mark_present=" + ("1" if mark and os.path.exists(mark) else "0")
    extra += " start_period=" + os.environ.get("DORAMI_BACKEND_HEALTH_START_PERIOD", "-")   # issue #227:up 时导出的 healthcheck 宽限
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(" ".join(args) + extra + "\n")
if args[:2] == ["compose", "version"]:
    sys.exit(0)
if args[:1] == ["info"]:
    print(rd("docker_root", "/var/lib/docker").strip() or "/var/lib/docker"); sys.exit(0)
if args[:2] == ["compose", "config"] and "--format" in args:
    # issue #227:模拟 compose 的最终解析——shell 环境(非空)> .env(引号 / 行尾注释)> 默认;FAKE 文件 compose_json_rc 模拟解析失败
    if rc("compose_json_rc"):
        sys.stderr.write("fake compose config --format json failure\n"); sys.exit(rc("compose_json_rc"))
    def dotenv(key):
        if not os.path.exists(".env"):
            return None
        val = None
        for line in open(".env", encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() != key:
                continue
            v = v.strip()
            if v[:1] in ("'", '"'):
                end = v.find(v[0], 1); val = v[1:end] if end > 0 else v[1:]
            else:
                val = v.split(" #", 1)[0].strip()
        return val
    def resolve(key, default):
        # compose:shell 环境里**存在**的键(哪怕是空串)压过 .env;再按 ${VAR:-default} 对空值取默认——显式空值不会回落到 .env
        v = os.environ[key] if key in os.environ else (dotenv(key) or "")
        return v if v else default
    listen = resolve("DORAMI_HTTP_LISTEN", "80")
    host, _, port = listen.rpartition(":")
    ports = [{"mode": "ingress", "target": 80, "published": port, "protocol": "tcp", **({"host_ip": host} if host else {})}]
    import json as _json
    sys.stdout.write(_json.dumps({"services": {"backend": {"healthcheck": {"start_period": resolve("DORAMI_BACKEND_HEALTH_START_PERIOD", "1m0s")}},
                                               "nginx": {"ports": ports}}}))
    sys.exit(0)
if args[:2] == ["compose", "config"]:
    code = rc("config_rc")
    if code:
        sys.stderr.write("required variable DORAMI_PODCAST_AUTHORITY_ID is missing a value: set a stable id\n")
    sys.exit(code)
if args[:2] == ["compose", "build"]:
    sys.exit(rc("build_rc"))
if args[:2] == ["compose", "run"]:
    mode = args[-1]
    if mode == "--check-config":
        sys.stdout.write(rd("check.json", '{"status": "ok", "errors": [], "warnings": []}')); sys.exit(0)
    if mode == "--plan-migrations":
        sys.stdout.write(rd("plan.json", '{"status": "compatible", "pending": [], "pending_count": 0}')); sys.exit(0)
    sys.exit(1)
if args[:2] == ["compose", "up"]:
    sys.exit(rc("up_rc"))
if args[:2] == ["compose", "ps"]:
    code = rc("ps_rc")
    sys.stderr.write(rd("ps_stderr"))
    if code:
        sys.stderr.write("fake compose ps failure\n"); sys.exit(code)
    svc = args[-1] if not args[-1].startswith("-") and args[-1] != "ps" else ""
    if svc:
        if "-a" in args and os.path.exists(os.path.join(d, "ps_" + svc + "_all")):
            sys.stdout.write(rd("ps_" + svc + "_all")); sys.exit(0)
        sys.stdout.write(rd("ps_" + svc)); sys.exit(0)
    if "-a" in args:
        sys.stdout.write(rd("ps_all")); sys.exit(0)
    if "-q" in args:
        sys.exit(0)
    print("NAME STATUS"); sys.exit(0)
if args[:2] == ["compose", "logs"]:
    print("(fake logs)"); sys.exit(0)
if args[:1] == ["inspect"]:
    if rc("inspect_rc"):
        sys.stderr.write(rd("inspect_stderr", "fake inspect failure\n")); sys.exit(rc("inspect_rc"))
    cid = args[-1]
    fmt = " ".join(args)
    if "Config.Env" in fmt:
        sys.stdout.write(rd("env_of_" + cid)); sys.exit(0)
    sys.stdout.write(rd("image_of_" + cid).strip() + "\n"); sys.exit(0)
if args[:1] == ["tag"]:
    with open(os.path.join(d, "images"), "a") as f:
        f.write(args[2] + "\n")
    sys.exit(0)
if args[:2] == ["image", "ls"]:
    sys.stdout.write(rd("images")); sys.exit(0)
if args[:1] == ["rmi"]:
    lines = [l for l in rd("images").splitlines() if l and l != args[1]]
    open(os.path.join(d, "images"), "w").write("".join(l + "\n" for l in lines))
    sys.exit(0)
if args[:2] == ["image", "prune"]:
    # 「不足时回收」腾出空间:把 FAKE_RECLAIM_SETS_AVAIL_KB 写进假 df 读的文件,预检复测就能看到变化
    # (挂在 image prune 而不是 builder prune 上,因为 builder prune 每次部署开头都会无条件跑一次)
    f, v = os.environ.get("FAKE_DF_AVAIL_FILE", ""), os.environ.get("FAKE_RECLAIM_SETS_AVAIL_KB", "")
    if f and v:
        open(f, "w").write(v)
    sys.exit(0)
if args[:2] == ["builder", "prune"]:
    if os.environ.get("FAKE_PRUNE_SLEEP"):   # 模拟卡住的 docker CLI
        import time; time.sleep(float(os.environ["FAKE_PRUNE_SLEEP"]))
    if rc("prune_rc"):
        sys.stderr.write("fake prune failure\n"); sys.exit(rc("prune_rc"))
    print("Total reclaimed space: 1.5GB"); sys.exit(0)
if args[:2] == ["system", "df"]:
    print("    docker Images: 10GB(可回收 7GB)"); sys.exit(0)
sys.stderr.write("fake docker: unhandled " + " ".join(args) + "\n")
sys.exit(1)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
"""curl 桩:/api/health → FAKE_HEALTH_FILE(200 JSON)或 FAKE_HEALTH_RAW + FAKE_HEALTH_CODE(任意状态码 / 文本);缺则连不上(rc 7)。
FAKE_HEALTH_301_ON=host:port 时,打到该地址的请求一律回 TLS 边缘式的 301 页(issue #227 实况)。
支持 -o / -w '%{http_code}' / -f(4xx+ 时 rc 22、不输出体):新旧两版 deploy-lib 的调用形态都走这里。"""
import os, sys
args = sys.argv[1:]
out, fmt, url, fail_on_error = None, "", "", False
i = 0
while i < len(args):
    a = args[i]
    if a == "-o": out = args[i + 1]; i += 2; continue
    if a == "-w": fmt = args[i + 1]; i += 2; continue
    if a in ("-H", "--connect-timeout", "--max-time"): i += 2; continue
    if a.startswith("-"):
        if not a.startswith("--") and "f" in a: fail_on_error = True
        i += 1; continue
    url = a; i += 1
hostport = url.split("://", 1)[-1].split("/", 1)[0]
raw = os.environ.get("FAKE_HEALTH_RAW")
p = os.environ.get("FAKE_HEALTH_FILE", "")
if os.environ.get("FAKE_HEALTH_301_ON") and hostport == os.environ["FAKE_HEALTH_301_ON"]:
    code, body = "301", b"<html>\r\n<head><title>301 Moved Permanently</title></head>\r\n<body>\r\n<center><h1>301 Moved Permanently</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
elif raw is not None:
    code, body = os.environ.get("FAKE_HEALTH_CODE", "200"), raw.encode()
elif p and os.path.exists(p):
    code, body = "200", open(p, "rb").read()
else:
    if fmt:
        sys.stdout.write(fmt.replace("%{http_code}", "000"))
    sys.exit(7)
if fail_on_error and int(code) >= 400:
    if fmt:
        sys.stdout.write(fmt.replace("%{http_code}", code))
    sys.exit(22)
if out:
    open(out, "wb").write(body)
else:
    sys.stdout.buffer.write(body)
if fmt:
    sys.stdout.write(fmt.replace("%{http_code}", code))
sys.exit(0)
'''

FAKE_DF = r'''#!/usr/bin/env python3
import os, sys
if os.environ.get("FAKE_DF_RC"):   # 模拟 df 本身失败(挂载点读不到 / 权限)
    sys.stderr.write("fake df failure\n"); sys.exit(int(os.environ["FAKE_DF_RC"]))
avail = os.environ.get("FAKE_DF_AVAIL_KB", str(100 * 1024 * 1024))
_f = os.environ.get("FAKE_DF_AVAIL_FILE", "")
if _f and os.path.exists(_f):
    avail = open(_f).read().strip() or avail
mode = os.environ.get("FAKE_DF_MODE", "gnu")
ifree = os.environ.get("FAKE_DF_IFREE", "999000")
if "-Pi" in sys.argv:
    if mode == "macos":   # macOS 的 -Pi 是 9 列,ifree 在第 7 列
        print("Filesystem 1024-blocks Used Available Capacity iused ifree %iused Mounted on")
        print(f"fakefs 1000000000 1000 {avail} 1% 1000 {ifree} 0% /")
    elif mode == "noinode":
        print("Filesystem 1024-blocks Used Available Capacity Mounted on"); print(f"fakefs 1000000000 1000 {avail} 1% /")
    else:
        print("Filesystem Inodes IUsed IFree IUse% Mounted on"); print(f"fakefs 1000000 1000 {ifree} 1% /")
else:
    print("Filesystem 1024-blocks Used Available Capacity Mounted on"); print(f"fakefs 1000000000 1000 {avail} 1% /")
'''

STUB_DEPLOY = r'''#!/bin/bash
# 桩:记录 worker 传来的环境,按 STUB_* 决定行为
echo "STUB: origin=${DORAMI_DEPLOY_ORIGIN:-} expected=${DORAMI_EXPECTED_SHA:-} fresh_ok=${DORAMI_DEPLOY_FRESH_OK:-} lock_fd=${DORAMI_DEPLOY_LOCK_FD:-} tag=$1"
if [ -n "${STUB_ENV_OUT:-}" ]; then env | grep '^DORAMI_' | sort > "$STUB_ENV_OUT"; fi
[ -n "${DORAMI_DEPLOY_LOCK_FD:-}" ] && [ -e "/dev/fd/${DORAMI_DEPLOY_LOCK_FD}" ] && echo "STUB: lock fd inherited"
if [ -n "${STUB_DESCENDANT:-}" ]; then ( sleep "$STUB_DESCENDANT"; touch "$DORAMI_DEPLOY_SWITCH_MARK" ) & fi
[ -n "${STUB_SLEEP:-}" ] && sleep "$STUB_SLEEP"
[ "${STUB_SWITCH:-0}" = 1 ] && touch "$DORAMI_DEPLOY_SWITCH_MARK"
echo "DORAMI_DEPLOY_META stub=1"
exit "${STUB_RC:-0}"
'''


# ── git 工具 ──
def _git_env(home: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    return env


def git(cwd: Path, *args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=check, capture_output=True, text=True)


class Repo:
    """临时 bare origin + 工作克隆(模拟生产机上的 git clone)。"""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"; self.home.mkdir()
        self.env = _git_env(self.home)
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.origin), env=self.env)
        git(tmp_path, "init", "-b", "main", str(self.work), env=self.env)
        git(self.work, "remote", "add", "origin", str(self.origin), env=self.env)
        self.clone: Path | None = None

    def commit(self, files: dict[str, str], tag: str | None = None, message: str = "c", branch: str | None = None) -> str:
        for item in self.work.iterdir():
            if item.name == ".git":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        for rel, content in files.items():
            p = self.work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if rel.endswith(".sh"):
                p.chmod(0o755)
        git(self.work, "add", "-A", env=self.env)
        git(self.work, "commit", "-qm", message, env=self.env)
        sha = git(self.work, "rev-parse", "HEAD", env=self.env).stdout.strip()
        if tag:
            git(self.work, "tag", "-a", tag, "-m", tag, env=self.env)
        git(self.work, "push", "-q", "origin", branch or "main", "--tags", env=self.env)
        return sha

    def make_clone(self, tmp_path: Path, checkout: str | None = None) -> Path:
        self.clone = tmp_path / "repo"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.clone), env=self.env)
        if checkout:
            git(self.clone, "checkout", "-q", "--detach", checkout, env=self.env)
        return self.clone

    def sha_of(self, ref: str) -> str:
        return git(self.work, "rev-parse", f"{ref}^{{commit}}", env=self.env).stdout.strip()


def worker_repo_files(version: str, *, protocol: bool = True, migrations=("0001",)) -> dict[str, str]:
    files = {
        "src/version.py": f'__version__ = "{version}"\n',
        "config/production.ini": "[storage]\ndatabase_url = sqlite:///data/cms_data.db\n",
        "deploy-docker.sh": STUB_DEPLOY,
        "scripts/deploy-lib.sh": "# lib\nDORAMI_DEPLOY_PROTOCOL=1\n" if protocol else "# lib without protocol\n",
    }
    for m in migrations:
        files[f"alembic/versions/{m}_m.py"] = f'revision = "{m}"\n'
    return files


# ── 环境搭建 ──
class Env:
    def __init__(self, tmp_path: Path, repo: Repo, *, keep: int = 2):
        self.tmp = tmp_path
        self.repo = repo
        self.fakebin = tmp_path / "fakebin"; self.fakebin.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("df", FAKE_DF)):
            p = self.fakebin / name; p.write_text(body); p.chmod(0o755)
        self.fake = tmp_path / "fake"; self.fake.mkdir()
        self.state = tmp_path / "state"
        self.logs = tmp_path / "logs"
        self.lock = tmp_path / "deploy.lock"
        self.conf = tmp_path / "deploy.conf"
        path = f"{self.fakebin}:{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
        self.conf.write_text(
            f"REPO_DIR={repo.clone}\nSTATE_DIR={self.state}\nLOG_DIR={self.logs}\nLOCK_FILE={self.lock}\n"
            f"DEPLOY_WORKER={WORKER}\nDEPLOY_PATH={path}\nDORAMI_DEPLOY_MIN_FREE_GB=1\n"
            f"DORAMI_DEPLOY_BACKUP_KEEP={keep}\nDORAMI_HTTP_LISTEN=127.0.0.1:8080\n",
            encoding="utf-8",
        )
        self.path = path

    def base_env(self, **extra: str) -> dict:
        env = dict(os.environ)
        env.update({
            "DORAMI_DEPLOY_CONF": str(self.conf), "FAKE_DOCKER_DIR": str(self.fake),
            "PATH": self.path, "HOME": str(self.repo.home),
        })
        for k in list(env):
            if k.startswith("STUB_") or k.startswith("DORAMI_DEPLOY_"):
                if k not in ("DORAMI_DEPLOY_CONF",):
                    env.pop(k)
        env.update(extra)
        return env

    def fake_write(self, name: str, content: str) -> None:
        (self.fake / name).write_text(content)

    def running_container(self, base_sha: str, base_ref: str, image: str = "sha256:backendimg1") -> None:
        self.fake_write("ps_backend", "cid-backend\n")
        self.fake_write("ps_nginx", "cid-nginx\n")
        self.fake_write("ps_all", "cid-backend\ncid-nginx\n")
        self.fake_write("image_of_cid-backend", image + "\n")
        self.fake_write("image_of_cid-nginx", "sha256:nginximg1\n")
        self.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={base_sha}\nDORAMI_BUILD_REF={base_ref}\nOTHER=1\n")

    def create_db(self) -> Path:
        db = self.repo.clone / "data" / "cms_data.db"
        db.parent.mkdir(exist_ok=True)
        con = sqlite3.connect(db); con.execute("CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY)"); con.commit(); con.close()
        return db

    def cmd(self, tag: str, downgrade: int = 0, redeploy: int = 0) -> str:
        return f"{tag} {self.repo.sha_of(tag)} downgrade={downgrade} redeploy={redeploy}"

    def launch(self, cmd: str, timeout: int = 90, *, launcher: Path | None = None, **extra: str) -> subprocess.CompletedProcess:
        r = subprocess.run([str(launcher or LAUNCHER), cmd], env=self.base_env(**extra), capture_output=True,
                           text=True, errors="replace", timeout=timeout)
        if r.returncode == 0 and (self.fake / "ps_backend").exists():
            # 成功部署后容器已是目标版本(真实系统里 up 之后容器 env 的构建 sha 就是目标):假容器同步
            tag, sha = cmd.split()[0], cmd.split()[1]
            self.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={sha}\nDORAMI_BUILD_REF={tag}\nOTHER=1\n")
        return r

    def state_json(self, name: str) -> dict | None:
        p = self.state / name
        return json.loads(p.read_text()) if p.exists() else None

    def calls(self) -> list[str]:
        p = self.fake / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def worker_log(self, tag: str, sha: str | None = None) -> str:
        sha = sha or self.repo.sha_of(tag)
        p = self.logs / f"{tag}-{sha[:7]}.log"
        return p.read_text() if p.exists() else ""


@pytest.fixture
def env(tmp_path: Path) -> Env:
    repo = Repo(tmp_path)
    repo.commit(worker_repo_files("1.0.0", protocol=False), tag="v1.0.0")
    repo.commit(worker_repo_files("1.1.0"), tag="v1.1.0")
    repo.commit(worker_repo_files("1.2.0", migrations=("0001", "0002")), tag="v1.2.0")
    repo.make_clone(tmp_path)
    e = Env(tmp_path, repo)
    e.running_container(repo.sha_of("v1.0.0"), "v1.0.0")
    e.create_db()
    return e


# ══════════════ launcher / worker 状态机 ══════════════

def test_launcher_rejects_malformed_commands(env: Env):
    for bad in ("v1.1.0", "v1.1.0 deadbeef downgrade=0 redeploy=0", f"{env.cmd('v1.1.0')} extra", env.cmd("v1.1.0").replace("downgrade=0", "downgrade=2")):
        r = env.launch(bad)
        assert r.returncode == 2, bad
    assert not env.state.exists() or not (env.state / "state.json").exists()


def test_happy_path_writes_transaction_last_success_and_rc(env: Env):
    r = env.launch(env.cmd("v1.1.0"), STUB_SWITCH="1")
    assert r.returncode == 0, r.stdout + r.stderr
    state = env.state_json("state.json")
    assert state["phase"] == "complete" and state["rc"] == 0
    rc_file = env.state / f"v1.1.0-{env.repo.sha_of('v1.1.0')[:7]}.rc"
    assert rc_file.read_text().strip() == "0"
    assert env.state_json("in-progress.json") is None
    ls = env.state_json("last-success.json")
    assert ls["target"]["tag"] == "v1.1.0" and ls["target"]["sha"] == env.repo.sha_of("v1.1.0")
    assert ls["target"]["backend_image_id"] == "sha256:backendimg1", "晋升时记录目标镜像,供下次容器缺失时作 prev"
    assert ls["prev"]["sha"] == env.repo.sha_of("v1.0.0") and ls["prev"]["ref"] == "v1.0.0"
    assert ls["prev"]["backend_image_id"] == "sha256:backendimg1"
    assert len(ls["prev"]["managed_tags"]) == 2 and all(t.startswith("dorami-") and "-managed:" in t for t in ls["prev"]["managed_tags"])
    assert Path(ls["prev"]["db_backup"]).exists() and Path(ls["prev"]["db_backup"]).stat().st_size > 0
    assert ls["txn_id"] and ls["deployed_at"] and ls["switched_at"]
    log = env.worker_log("v1.1.0")
    assert "STUB: origin=pipeline expected=" + env.repo.sha_of("v1.1.0") in log
    assert "lock fd inherited" in log
    assert "方向 forward" in log
    calls = env.calls()
    assert any(c.startswith("tag sha256:backendimg1 dorami-backend-managed:v1.0.0-") for c in calls)
    assert "tag sha256:backendimg1 dorami-backend-rollback:auto" in calls
    assert "DORAMI_DEPLOY_META target_tag=v1.1.0" in r.stdout


def test_replay_then_force_redeploy(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    (env.fake / "calls.log").unlink()
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0
    assert "回放成功" in r.stdout and "STUB:" not in env.worker_log("v1.1.0").split("回放成功")[-1]
    assert not any(c.startswith("tag ") for c in env.calls()), "回放不得打 managed tag(读容器现状做交叉核对是允许的)"
    r = env.launch(env.cmd("v1.1.0", redeploy=1))
    assert r.returncode == 0
    assert "redeploy=1" in r.stdout
    assert any(c.startswith("tag ") for c in env.calls()), "强制重部署要重新走事务"


def test_launcher_killed_worker_continues_and_second_launcher_attaches(env: Env):
    first = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="6"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    time.sleep(2.5)
    second = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="6"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    time.sleep(1)
    first.kill(); first.wait()
    out, err = second.communicate(timeout=60)
    assert second.returncode == 0, out + err
    assert "attach 已有日志" in out
    assert env.state_json("state.json")["phase"] == "complete"
    assert env.state_json("last-success.json")["target"]["tag"] == "v1.1.0"
    assert (env.state / f"v1.1.0-{env.repo.sha_of('v1.1.0')[:7]}.rc").read_text().strip() == "0"


def test_other_target_while_running_is_rejected(env: Env):
    first = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="5"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    time.sleep(2.5)
    r = env.launch(env.cmd("v1.2.0"))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "另一部署正在进行: v1.1.0" in r.stderr
    out, _ = first.communicate(timeout=60)
    assert first.returncode == 0


def test_child_failure_keeps_transaction_and_retry_reuses_original_prev(env: Env):
    r = env.launch(env.cmd("v1.1.0"), STUB_RC="7", STUB_SWITCH="1")
    assert r.returncode == 7
    ip = env.state_json("in-progress.json")
    assert ip is not None and ip["target"]["tag"] == "v1.1.0" and ip["switched_at"]
    assert env.state_json("last-success.json") is None
    original_backup = ip["prev"]["db_backup"]
    # 失败后容器已是新版本:再采样会得到不同镜像;重试必须复用原 prev
    env.fake_write("image_of_cid-backend", "sha256:backendimg-AFTER-FAILURE\n")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "复用未收口事务" in env.worker_log("v1.1.0")
    ls = env.state_json("last-success.json")
    assert ls["txn_id"] == ip["txn_id"]
    assert ls["prev"]["backend_image_id"] == "sha256:backendimg1"
    assert ls["prev"]["db_backup"] == original_backup
    assert env.state_json("in-progress.json") is None


def test_unswitched_transaction_is_auto_closed_but_switched_one_blocks_other_target(env: Env):
    # 未切换(up 前失败)→ 别的目标自动关闭它
    assert env.launch(env.cmd("v1.1.0"), STUB_RC="9").returncode == 9
    txn1 = env.state_json("in-progress.json")["txn_id"]
    r = env.launch(env.cmd("v1.2.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (env.state / f"closed-{txn1}.json").exists()
    assert "自动关闭事务" in env.worker_log("v1.2.0")
    # 已切换 → 别的目标 fail closed(20),--close-in-progress 后放行
    (env.state / "last-success.json").unlink()
    assert env.launch(env.cmd("v1.1.0", downgrade=1), STUB_RC="9", STUB_SWITCH="1").returncode == 9
    r = env.launch(env.cmd("v1.2.0"))
    assert r.returncode == 20, r.stdout + r.stderr
    close = subprocess.run([str(WORKER), "--close-in-progress"], env=env.base_env(), capture_output=True, text=True)
    assert close.returncode == 0, close.stdout + close.stderr
    assert env.state_json("in-progress.json") is None
    assert env.launch(env.cmd("v1.2.0")).returncode == 0


def test_monotonic_guard_blocks_downgrade_unless_flagged_and_allows_deleted_migrations(env: Env):
    assert env.launch(env.cmd("v1.2.0")).returncode == 0
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 21, r.stdout + r.stderr
    assert "方向 downgrade" in env.worker_log("v1.1.0")
    r = env.launch(env.cmd("v1.1.0", downgrade=1))
    assert r.returncode == 0, r.stdout + r.stderr
    log = env.worker_log("v1.1.0")
    assert "downgrade=1:人为确认放行" in log and "D=1 为预期" in log


def test_forward_deploy_with_deleted_migration_file_fails_closed(env: Env):
    env.repo.commit(worker_repo_files("1.3.0", migrations=("0001",)), tag="v1.3.0")  # 删掉了 0002
    assert env.launch(env.cmd("v1.2.0")).returncode == 0
    r = env.launch(env.cmd("v1.3.0"))
    assert r.returncode == 22, r.stdout + r.stderr
    assert "迁移历史被篡改" in env.worker_log("v1.3.0")


def test_target_without_protocol_is_rejected(env: Env):
    r = env.launch(env.cmd("v1.0.0", downgrade=1))
    assert r.returncode == 11, r.stdout + r.stderr
    assert "未宣告 DORAMI_DEPLOY_PROTOCOL" in env.worker_log("v1.0.0")


def test_ref_verification_fails_closed(env: Env, tmp_path: Path):
    wrong = env.cmd("v1.1.0").replace(env.repo.sha_of("v1.1.0"), env.repo.sha_of("v1.2.0"))
    r = env.launch(wrong)
    assert r.returncode == 10, r.stdout + r.stderr
    assert "不一致" in env.worker_log("v1.1.0", env.repo.sha_of("v1.2.0"))  # 日志按命令里的 sha 命名
    # origin 消失 → fetch 失败即停,不回落到本地 tag
    shutil.rmtree(env.repo.origin)
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 10
    assert "git fetch --tags origin 失败" in env.worker_log("v1.1.0")


def test_first_install_gate_token_flow(tmp_path: Path):
    repo = Repo(tmp_path)
    repo.commit(worker_repo_files("1.1.0"), tag="v1.1.0")
    repo.make_clone(tmp_path)
    e = Env(tmp_path, repo)  # 无容器、无备份、无库、无 last-success
    e.state.mkdir()
    out = tmp_path / "stub-env.txt"
    # 无令牌、无容器、无 last-success:既不是首装也没有回滚点 → fail closed(24),不起子进程
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out))
    assert r.returncode == 24, r.stdout + r.stderr
    assert "无法确定回滚点" in e.worker_log("v1.1.0")
    assert not out.exists()
    # 有令牌:FRESH_OK=1,令牌被消费;失败后重试仍沿用事务内授权
    token = e.state / "first-install.token"; token.write_text("first\n")
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out), STUB_RC="5")
    assert r.returncode == 5
    assert "DORAMI_DEPLOY_FRESH_OK=1" in out.read_text()
    assert not token.exists(), "令牌应在事务落盘后立即消费"
    assert e.state_json("in-progress.json")["fresh_authorized"] is True
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DORAMI_DEPLOY_FRESH_OK=1" in out.read_text()
    assert "沿用事务内 fresh_authorized=1" in e.worker_log("v1.1.0")


def test_evidence_blocks_first_install_even_with_token(env: Env):
    token = env.state / "first-install.token"; env.state.mkdir(exist_ok=True); token.write_text("x")
    out = env.tmp / "stub-env.txt"
    r = env.launch(env.cmd("v1.1.0"), STUB_ENV_OUT=str(out))
    assert r.returncode == 0
    assert "DORAMI_DEPLOY_FRESH_OK=0" in out.read_text()
    assert token.exists(), "有证据时令牌不消费"
    assert "有部署证据" in env.worker_log("v1.1.0")


def test_crash_window_same_txn_is_reconciled_idempotently(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    ls = env.state_json("last-success.json")
    stale = {k: v for k, v in ls.items() if k != "deployed_at"}
    (env.state / "in-progress.json").write_text(json.dumps(stale))
    r = env.launch(env.cmd("v1.2.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "崩溃窗口" in env.worker_log("v1.2.0")
    assert env.state_json("in-progress.json") is None


def test_cleanup_keeps_referenced_backup_and_managed_tags(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    ls1 = env.state_json("last-success.json")
    # 模拟部署后容器换成了新镜像
    env.fake_write("image_of_cid-backend", "sha256:backendimg2\n"); env.fake_write("image_of_cid-nginx", "sha256:nginximg2\n")
    assert env.launch(env.cmd("v1.2.0")).returncode == 0
    ls2 = env.state_json("last-success.json")
    images = (env.fake / "images").read_text().splitlines()
    for t in ls2["prev"]["managed_tags"]:
        assert t in images
    for t in ls1["prev"]["managed_tags"]:
        assert t not in images, "不被 last-success 引用的旧 managed tag 应被清理"
    backups = sorted((env.repo.clone / "backups").glob("cms_data.db.*"))
    assert Path(ls2["prev"]["db_backup"]) in backups
    assert len(backups) <= 2 + 1  # keep=2 + 被引用的那份不计数


# ══════════════ deploy-docker.sh(真脚本 + 假 docker / curl / df)══════════════

def _real_repo_files(version: str, *, old: bool = False, worktree: Path = ROOT) -> dict[str, str]:
    if old:
        lib = (OLD_SCRIPTS_DIR / "deploy-lib.sh").read_text(encoding="utf-8")
        dd = (OLD_SCRIPTS_DIR / "deploy-docker.sh").read_text(encoding="utf-8")
    else:
        lib = (worktree / "scripts" / "deploy-lib.sh").read_text(encoding="utf-8")
        dd = (worktree / "deploy-docker.sh").read_text(encoding="utf-8")
    return {
        "src/version.py": f'__version__ = "{version}"\n',
        "config/production.ini": "[storage]\ndatabase_url = sqlite:///data/cms_data.db\n",
        "docker-compose.yml": "services: {}\n",
        "deploy-docker.sh": dd,
        "scripts/deploy-lib.sh": lib,
        "alembic/versions/0001_m.py": 'revision = "0001"\n',
    }


@pytest.fixture
def real(tmp_path: Path) -> Env:
    repo = Repo(tmp_path)
    repo.commit(_real_repo_files("9.9.8", old=True), tag="v9.9.8")
    repo.commit(_real_repo_files("9.9.9"), tag="v9.9.9")
    repo.make_clone(tmp_path)
    e = Env(tmp_path, repo)
    e.create_db()
    e.running_container(repo.sha_of("v9.9.8"), "v9.9.8")
    return e


def _health(e: Env, tag: str) -> Path:
    body = {"status": "ok", "version": tag[1:], "build": {"ref": tag, "sha": e.repo.sha_of(tag), "source": "env"}}
    p = e.fake / "health.json"; p.write_text(json.dumps(body)); return p


def _run_deploy(e: Env, *args: str, timeout: int = 120, **extra: str) -> subprocess.CompletedProcess:
    defaults = dict(DORAMI_DEPLOY_LOCK_FILE=str(e.lock), DORAMI_DEPLOY_HEALTH_ATTEMPTS="2",
                    FAKE_HEALTH_FILE=str(e.fake / "health.json"), DORAMI_HTTP_LISTEN="127.0.0.1:8080")
    defaults.update(extra)
    env = e.base_env(**{k: v for k, v in defaults.items() if v is not None})
    for k, v in defaults.items():     # 值为 None = 真正从环境里拿掉(模拟手工 shell 没导出)
        if v is None:
            env.pop(k, None)
    return subprocess.run(["./deploy-docker.sh", *args], cwd=str(e.repo.clone), env=env, capture_output=True,
                          text=True, errors="replace", timeout=timeout)


def test_deploy_docker_manual_happy_path_runs_steps_in_order(real: Env):
    _health(real, "v9.9.9")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = real.calls()
    order = [c for c in calls if c.startswith(("compose config", "compose build", "compose run", "compose up"))]
    assert order == [
        "compose config -q", "compose build",
        "compose run --rm --no-deps -T backend python docker/entrypoint.py --check-config",
        "compose run --rm --no-deps -T backend python docker/entrypoint.py --plan-migrations",
        "compose config --format json",   # 读 compose 解析出的 backend start_period(显式与否)
        "compose up -d --remove-orphans switch_mark_present=0 start_period=60s",
        "compose config --format json",   # 读 nginx 发布端口定探针地址
    ]
    backups = list((real.repo.clone / "backups").glob("cms_data.db.*"))
    assert len(backups) == 1, "手工来源做备份"
    assert "DORAMI_DEPLOY_META origin=manual" in r.stdout and "DORAMI_DEPLOY_META health=ok" in r.stdout
    assert "Deploy complete. 发布版 v9.9.9" in r.stdout


# 像 worker 那样由 bash 打开 FD 9 持锁,再 exec 目标脚本(不在 pytest 进程里动 fd 9——那会撞上 pytest 自己的描述符)
_AS_WORKER = (
    "exec 9>>\"$1\"; python3 -c 'import fcntl,sys; fcntl.flock(9, fcntl.LOCK_EX|fcntl.LOCK_NB)' "
    "|| { echo lock-busy >&2; exit 99; }; export DORAMI_DEPLOY_LOCK_FD=9; exec ./deploy-docker.sh \"$2\""
)


def _run_deploy_as_worker(e: Env, tag: str, **extra: str) -> subprocess.CompletedProcess:
    env = e.base_env(DORAMI_DEPLOY_LOCK_FILE=str(e.lock), DORAMI_DEPLOY_HEALTH_ATTEMPTS="2",
                     FAKE_HEALTH_FILE=str(e.fake / "health.json"), DORAMI_DEPLOY_ORIGIN="pipeline", **extra)
    return subprocess.run(["bash", "-c", _AS_WORKER, "_", str(e.lock), tag], cwd=str(e.repo.clone), env=env,
                          capture_output=True, text=True, timeout=120)


def test_deploy_docker_pipeline_origin_skips_backup_touches_switch_mark_and_checks_expected_sha(real: Env):
    _health(real, "v9.9.9")
    mark = real.tmp / "txn.switch"
    r = _run_deploy_as_worker(real, "v9.9.9", DORAMI_EXPECTED_SHA=real.repo.sha_of("v9.9.9"), DORAMI_DEPLOY_SWITCH_MARK=str(mark))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not list((real.repo.clone / "backups").glob("cms_data.db.*")), "流水线来源不自做备份"
    assert "DORAMI_DEPLOY_META db_backup=worker" in r.stdout
    assert any("switch_mark_present=1" in c for c in real.calls())
    # expected sha 不一致 → 构建之前失败
    (real.fake / "calls.log").unlink()
    r = _run_deploy_as_worker(real, "v9.9.9", DORAMI_EXPECTED_SHA=real.repo.sha_of("v9.9.8"), DORAMI_DEPLOY_SWITCH_MARK=str(mark))
    assert r.returncode != 0 and "不一致" in r.stderr
    assert not any(c.startswith("compose build") for c in real.calls())
    # 流水线来源缺 expected sha 也拒绝
    r = _run_deploy_as_worker(real, "v9.9.9", DORAMI_DEPLOY_SWITCH_MARK=str(mark))
    assert r.returncode != 0 and "缺 DORAMI_EXPECTED_SHA" in r.stderr


def test_deploy_docker_preflight_failures_stop_before_build(real: Env):
    _health(real, "v9.9.9")
    r = _run_deploy(real, "v9.9.9", FAKE_DF_AVAIL_KB=str(100 * 1024))  # 100MB
    assert r.returncode != 0 and "磁盘不足" in r.stderr and "回收后仍不足" in r.stderr
    assert "可回收" in r.stdout, "仍不足时打印 docker system df 去向"
    calls = real.calls()
    assert "image prune -f" in calls and "builder prune -f --keep-storage=2G" in calls, "fail 之前先做过安全回收"
    assert not any(c.startswith("compose build") for c in calls)
    assert "空闲 inode 999000" in r.stdout and "999000999000" not in r.stdout, "inode 数只打印一遍"
    real.fake_write("config_rc", "1")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0 and "docker compose config 校验失败" in r.stderr
    assert "required variable DORAMI_PODCAST_AUTHORITY_ID is missing" in r.stdout
    assert not any(c.startswith("compose build") for c in real.calls())


def _aged_backups(bdir: Path, n: int) -> list[Path]:
    bdir.mkdir(exist_ok=True)
    out = []
    for i in range(1, n + 1):   # 序号越大越新(mtime 递增)
        p = bdir / f"cms_data.db.202601{i:02d}-000000"
        p.write_bytes(b"x")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
        out.append(p)
    return out


def test_deploy_docker_preflight_reclaims_then_proceeds(real: Env):
    # issue #232:预检不足 → 回收(悬空镜像 / 构建缓存上限 / 备份计数)→ 复测够了 → 正常部署,回收在 build 之前
    _health(real, "v9.9.9")
    avail = real.tmp / "df_avail"; avail.write_text(str(100 * 1024))
    r = _run_deploy(real, "v9.9.9", FAKE_DF_AVAIL_FILE=str(avail), FAKE_RECLAIM_SETS_AVAIL_KB=str(50 * 1024 * 1024))
    assert r.returncode == 0, r.stdout + r.stderr
    calls = real.calls()
    assert calls.index("image prune -f") < calls.index("compose build")
    assert "回收后复测" in r.stdout and "Deploy complete" in r.stdout
    # 预检阈值也读 conf:conf 写 1 GB、df 报 1.5 GB → 不回收直接过
    (real.fake / "calls.log").unlink()
    avail.write_text(str(1536 * 1024))
    r = _run_deploy(real, "v9.9.9", FAKE_DF_AVAIL_FILE=str(avail))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "回收后复测" not in r.stdout


def test_deploy_docker_preflight_df_failure_fails_closed(real: Env):
    # codex R1 P2:disk_free_ok 在 `if !` 里跑、errexit 不生效,df 失败不能被当成「够」
    _health(real, "v9.9.9")
    r = _run_deploy(real, "v9.9.9", FAKE_DF_RC="1")
    assert r.returncode != 0 and "无法判断磁盘余量" in r.stderr
    assert not any(c.startswith(("compose build", "compose up")) for c in real.calls())


def test_success_path_prune_timeout_and_failure_do_not_fail_deploy(real: Env):
    # codex R1 P2:成功后的 builder prune 卡住 / 失败都只记一行,部署仍成功收口且进程组不留后代
    _health(real, "v9.9.9")
    t0 = time.monotonic()
    r = _run_deploy(real, "v9.9.9", FAKE_PRUNE_SLEEP="30", DORAMI_DEPLOY_PRUNE_TIMEOUT_SECONDS="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert time.monotonic() - t0 < 25, "超时应终止卡住的 prune,而不是等它 30 秒"
    assert "超时 1s" in r.stdout and "Deploy complete" in r.stdout
    real.fake_write("prune_rc", "7")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0 and "退出码 7" in r.stdout


def test_preflight_backup_trim_honors_conf_keep_and_manifest_pins(real: Env):
    _health(real, "v9.9.9")
    bdir = real.repo.clone / "backups"
    old = _aged_backups(bdir, 5)
    pinned = old[0]   # 最老的一份被 in-progress 事务引用,计数清理永不碰
    real.state.mkdir(parents=True, exist_ok=True)
    (real.state / "in-progress.json").write_text(json.dumps({"prev": {"db_backup": str(pinned)}}))
    avail = real.tmp / "df_avail"; avail.write_text(str(100 * 1024))
    r = _run_deploy(real, "v9.9.9", FAKE_DF_AVAIL_FILE=str(avail), FAKE_RECLAIM_SETS_AVAIL_KB=str(50 * 1024 * 1024))
    assert r.returncode == 0, r.stdout + r.stderr
    remaining = sorted(p.name for p in bdir.glob("cms_data.db.*"))
    # conf keep=2(环境未给):预检留最新 2 份 + 被引用的;随后手工备份新做 1 份再按 2 计数 → 被引用 + 最新 1 旧 + 新做
    assert pinned.name in remaining and old[4].name in remaining and len(remaining) == 3, remaining
    assert f"rm backups/{old[1].name}" in r.stdout and f"rm backups/{old[2].name}" in r.stdout


def test_manual_backup_count_reads_conf_keep_when_env_absent(real: Env):
    _health(real, "v9.9.9")
    bdir = real.repo.clone / "backups"
    _aged_backups(bdir, 4)
    r = _run_deploy(real, "v9.9.9")   # 环境未给 DORAMI_DEPLOY_BACKUP_KEEP → 读 conf 的 2,而不是旧默认 10
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list(bdir.glob("cms_data.db.*"))) == 2
    # 环境变量优先于 conf
    _aged_backups(bdir, 4)
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_BACKUP_KEEP="3")
    assert r.returncode == 0 and len(list(bdir.glob("cms_data.db.*"))) == 3


def test_success_path_prunes_build_cache_with_conf_limit(real: Env):
    _health(real, "v9.9.9")
    real.conf.write_text(real.conf.read_text() + "DORAMI_DEPLOY_BUILD_CACHE_KEEP_GB=3\n")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = real.calls()
    prunes = [i for i, c in enumerate(calls) if c == "builder prune -f --keep-storage=3G"]
    # 每次部署开头无条件压一次(在 build 之前),成功后再压一次(在 up 之后)
    assert len(prunes) == 2 and prunes[0] < calls.index("compose build") < calls.index("compose up -d --remove-orphans switch_mark_present=0 start_period=60s") < prunes[1]
    assert "构建缓存回收(上限 3 GB)" in r.stdout
    # 健康验证失败时不动构建缓存(失败路径只保留现场)
    (real.fake / "calls.log").unlink()
    (real.fake / "health.json").unlink()
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS="2")
    assert r.returncode != 0
    calls = real.calls()
    prunes = [i for i, c in enumerate(calls) if c.startswith("builder prune")]
    assert len(prunes) == 1 and prunes[0] < calls.index("compose build"), "失败路径只有开头那一次,up 之后不动现场"


def test_deploy_docker_check_config_and_plan_gate_before_up(real: Env):
    _health(real, "v9.9.9")
    real.fake_write("check.json", '{"status": "error", "errors": ["security: [auth] secret 未设置"], "warnings": []}')
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0 and "配置自检未通过" in r.stderr and "secret 未设置" in r.stdout
    assert not any(c.startswith("compose up") for c in real.calls())
    real.fake_write("check.json", '{"status": "ok", "errors": [], "warnings": ["w1"]}')
    real.fake_write("plan.json", '{"status": "fresh", "pending": ["a"], "pending_count": 1}')
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0 and "首装门未放行" in r.stderr
    assert not any(c.startswith("compose up") for c in real.calls())
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_FRESH_OK="1")
    assert r.returncode == 0, r.stdout + r.stderr
    real.fake_write("plan.json", '{"status": "incompatible", "pending": [], "pending_count": 0, "detail": "DB 领先"}')
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0 and "不兼容" in r.stderr


def test_deploy_docker_health_mismatch_fails_without_rollback(real: Env):
    body = {"status": "ok", "version": "9.9.9", "build": {"ref": "v9.9.9", "sha": "0" * 40, "source": "env"}}
    (real.fake / "health.json").write_text(json.dumps(body))
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0
    assert "mismatch sha=" in r.stderr and "DORAMI_DEPLOY_META health=failed" in r.stdout
    assert "不自动回滚" in r.stderr


def test_deploy_docker_manual_lock_conflict(real: Env):
    _health(real, "v9.9.9")
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl, sys, time; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); time.sleep(30)",
        str(real.lock)])
    try:
        time.sleep(0.5)
        r = _run_deploy(real, "v9.9.9")
        assert r.returncode != 0 and "另一个部署正在进行" in r.stderr
        assert not real.calls()
    finally:
        holder.kill(); holder.wait()


def test_old_tag_bootstraps_to_new_tag_through_worker(tmp_path: Path):
    """生产站在旧 tag(本波之前的脚本)上,worker 部署新 tag:旧 deploy-lib checkout 后 exec 新脚本,新脚本核对 ORIGIN / expected sha,锁 FD 仍在。"""
    repo = Repo(tmp_path)
    repo.commit(_real_repo_files("9.9.8", old=True), tag="v9.9.8")
    repo.commit(_real_repo_files("9.9.9"), tag="v9.9.9")
    repo.make_clone(tmp_path, checkout="v9.9.8")
    e = Env(tmp_path, repo)
    e.create_db()
    e.running_container(repo.sha_of("v9.9.8"), "v9.9.8")
    health = _health(e, "v9.9.9")
    r = e.launch(e.cmd("v9.9.9"), FAKE_HEALTH_FILE=str(health), DORAMI_DEPLOY_HEALTH_ATTEMPTS="2")
    assert r.returncode == 0, r.stdout + r.stderr
    log = e.worker_log("v9.9.9")
    assert "切换到发布版 v9.9.9" in log, "旧 deploy-lib 应先 checkout 目标 tag"
    assert "来源 pipeline" in log and "Deploy complete. 发布版 v9.9.9" in log
    assert "流水线来源:跳过" in log
    assert git(repo.clone, "rev-parse", "HEAD", env=repo.env).stdout.strip() == repo.sha_of("v9.9.9")
    ls = e.state_json("last-success.json")
    assert ls["target"]["tag"] == "v9.9.9" and ls["prev"]["ref"] == "v9.9.8"


# ══════════════ verify-release-ref.sh ══════════════

def test_verify_release_ref(tmp_path: Path):
    repo = Repo(tmp_path)
    repo.commit(worker_repo_files("1.1.0"), tag="v1.1.0")
    good_sha = repo.sha_of("v1.1.0")
    # 版本号不匹配的 tag(打在 main 上但代码版本是 1.1.0)
    git(repo.work, "tag", "-a", "v1.9.9", "-m", "bad", env=repo.env)
    # 不在 main 线上的 tag
    git(repo.work, "checkout", "-qb", "side", env=repo.env)
    repo.commit(worker_repo_files("2.0.0"), tag="v2.0.0", branch="side")
    git(repo.work, "checkout", "-q", "main", env=repo.env)
    git(repo.work, "push", "-q", "origin", "--tags", env=repo.env)
    clone = repo.make_clone(tmp_path)
    out = tmp_path / "gh_output"

    def run(tag: str) -> subprocess.CompletedProcess:
        env = dict(repo.env); env["GITHUB_OUTPUT"] = str(out)
        return subprocess.run([str(VERIFY_REF), tag], cwd=str(clone), env=env, capture_output=True, text=True)

    r = run("v1.1.0")
    assert r.returncode == 0 and r.stdout.strip() == good_sha
    assert f"target_sha={good_sha}" in out.read_text()
    assert run("v1.9.9").returncode == 1 and "版本号" in run("v1.9.9").stderr
    assert run("v2.0.0").returncode == 1 and "不在 main 线上" in run("v2.0.0").stderr
    assert run("nope").returncode == 1
    # actions/checkout 对 tag 事件会把本地 tag 引用改写成指向提交的轻量 tag(fetch +<sha>:refs/tags/<tag>);
    # 核验不得因此失败(2026-09-17 v3.60.0:--tags 撞「would clobber existing tag」静默退出 1),
    # 也不得把它误报成轻量 tag(轻量与否以 origin 为准)
    git(clone, "fetch", "--no-tags", "origin", f"+{good_sha}:refs/tags/v1.1.0", env=repo.env)
    assert git(clone, "cat-file", "-t", "refs/tags/v1.1.0", env=repo.env).stdout.strip() == "commit"
    r = run("v1.1.0")
    assert r.returncode == 0 and r.stdout.strip() == good_sha, r.stderr
    assert "轻量" not in r.stderr
    # 本地没有 tag 时单独取回;origin 删了 tag 则拒绝
    git(clone, "tag", "-d", "v1.1.0", env=repo.env)
    r = run("v1.1.0")
    assert r.returncode == 0 and r.stdout.strip() == good_sha, r.stderr
    git(repo.work, "push", "-q", "--delete", "origin", "v1.1.0", env=repo.env)
    r = run("v1.1.0")
    assert r.returncode == 1 and "不存在于 origin" in r.stderr


# ══════════════ 脚本层检视 R1 返修(codex 14 条)对应的失败路径 / 竞争窗口 ══════════════

def _worker_pid(e: Env, phase: str = "running") -> int:
    """等 worker 进入指定 phase(默认 running:事务已落盘、子进程已起)后返回其 pid。"""
    for _ in range(150):
        st = e.state_json("state.json")
        if st and st.get("phase") == phase and st.get("worker_pid"):
            return int(st["worker_pid"])
        time.sleep(0.2)
    raise AssertionError(f"worker 未进入 {phase}")


def test_launcher_ignores_stale_complete_and_waits_for_its_own_run(env: Env, tmp_path: Path):
    """上次同目标已 complete 且 rc=0;这次 worker 延迟 3 秒才起——launcher 不得读旧 complete 提前返回成功。"""
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    slow = tmp_path / "slow-worker"
    slow.write_text(f'#!/bin/bash\nsleep 3\nexec "{WORKER}" "$@"\n'); slow.chmod(0o755)
    env.conf.write_text(env.conf.read_text().replace(f"DEPLOY_WORKER={WORKER}", f"DEPLOY_WORKER={slow}"))
    (env.fake / "calls.log").unlink()
    t0 = time.time()
    r = env.launch(env.cmd("v1.1.0", redeploy=1))
    assert r.returncode == 0, r.stdout + r.stderr
    assert time.time() - t0 >= 3, "launcher 提前返回了旧结果"
    assert any(c.startswith("tag ") for c in env.calls()), "本次 worker 应真的跑了(redeploy=1)"
    st = env.state_json("state.json")
    assert st["run_id"] in r.stdout


def test_sigterm_finalizes_nonzero_and_stops_child(env: Env):
    first = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="8", STUB_SWITCH="1"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    pid = _worker_pid(env)
    time.sleep(0.5)
    os.kill(pid, 15)
    out, err = first.communicate(timeout=60)
    assert first.returncode == 143, out + err
    st = env.state_json("state.json")
    assert st["phase"] == "complete" and st["rc"] == 143
    assert env.state_json("in-progress.json") is not None, "信号中断后事务保留"
    txn = env.state_json("in-progress.json")["txn_id"]
    time.sleep(1)
    assert not (env.state / f"{txn}.switch").exists(), "子进程应被终止,不得继续切换"
    assert env.state_json("last-success.json") is None


def test_compose_ps_failure_fails_closed_before_transaction(env: Env):
    env.fake_write("ps_rc", "1")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 24, r.stdout + r.stderr
    assert env.state_json("in-progress.json") is None
    assert "docker compose ps 失败" in env.worker_log("v1.1.0")


def test_promote_failure_keeps_transaction_and_resources(env: Env):
    env.state.mkdir(exist_ok=True)
    (env.state / "last-success.json").mkdir()  # rename 到目录上会失败 → 晋升失败
    r = env.launch(env.cmd("v1.1.0"), STUB_SWITCH="1")
    assert r.returncode == 25, r.stdout + r.stderr
    ip = env.state_json("in-progress.json")
    assert ip is not None and ip["target"]["tag"] == "v1.1.0"
    images = (env.fake / "images").read_text().splitlines()
    for t in ip["prev"]["managed_tags"]:
        assert t in images, "晋升失败不得清理 managed 镜像"
    assert Path(ip["prev"]["db_backup"]).exists()
    assert not any(c.startswith(("rmi ", "image prune")) for c in env.calls())


def test_manual_deploy_invalidates_last_success_via_container_and_marker(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    # ① 旧脚本手工部署了 v1.2.0:容器里的构建 sha 变了,但没有 marker
    env.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={env.repo.sha_of('v1.2.0')}\nDORAMI_BUILD_REF=v1.2.0\n")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 21, r.stdout + r.stderr  # 不回放;基线取容器 v1.2.0 → 降级被护栏拦
    assert "已被手工部署越过" in env.worker_log("v1.1.0")
    r = env.launch(env.cmd("v1.1.0", downgrade=1))
    assert r.returncode == 0 and "STUB:" in env.worker_log("v1.1.0")
    # ② 新脚本手工部署留下 marker(容器 sha 恰好等于 last-success 也不回放)
    (env.state / "manual-switch.json").write_text(json.dumps({"ref": "v1.1.0", "sha": env.repo.sha_of("v1.1.0"), "at": "x"}))
    env.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={env.repo.sha_of('v1.1.0')}\nDORAMI_BUILD_REF=v1.1.0\n")
    (env.fake / "calls.log").unlink()
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0 and "replay=1" not in r.stdout
    assert any(c.startswith("tag ") for c in env.calls())
    assert not (env.state / "manual-switch.json").exists(), "成功晋升后清除标记"


def test_pinned_backup_survives_manual_count_cleanup(tmp_path: Path):
    repo = Repo(tmp_path)
    repo.commit(_real_repo_files("9.9.8", old=True), tag="v9.9.8")
    repo.commit(_real_repo_files("9.9.9"), tag="v9.9.9")
    repo.make_clone(tmp_path, checkout="v9.9.8")
    e = Env(tmp_path, repo, keep=1)
    e.create_db()
    e.running_container(repo.sha_of("v9.9.8"), "v9.9.8")
    health = _health(e, "v9.9.9")
    e.fake_write("up_rc", "1")   # 流水线在 up 失败:已切换事务 + 事务备份留下
    r = e.launch(e.cmd("v9.9.9"), FAKE_HEALTH_FILE=str(health), DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS="3")
    assert r.returncode != 0
    ip = e.state_json("in-progress.json"); pinned = Path(ip["prev"]["db_backup"])
    assert pinned.exists() and ip["switched_at"]
    os.utime(pinned, (time.time() - 100, time.time() - 100))
    e.fake_write("up_rc", "0")
    r = _run_deploy(e, "v9.9.9", DORAMI_DEPLOY_BACKUP_KEEP="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert pinned.exists(), "手工路径的计数清理不得删掉事务引用的备份"
    assert (e.state / "manual-switch.json").exists()


def test_renamed_and_modified_migration_file_is_rejected(env: Env):
    files = worker_repo_files("1.3.0", migrations=())
    files["alembic/versions/0001_renamed.py"] = 'revision = "0001"\nsql = "changed"\n'
    env.repo.commit(files, tag="v1.3.0")
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    r = env.launch(env.cmd("v1.3.0"))
    assert r.returncode == 22, r.stdout + r.stderr
    assert "改名" in env.worker_log("v1.3.0")


def test_protocol_declaration_parsing_is_strict(env: Env):
    files = worker_repo_files("1.3.0", migrations=("0001", "0002"))
    files["scripts/deploy-lib.sh"] = "# lib\nDORAMI_DEPLOY_PROTOCOL=2 # next protocol\n"
    env.repo.commit(files, tag="v1.3.0")
    assert env.launch(env.cmd("v1.2.0")).returncode == 0
    r = env.launch(env.cmd("v1.3.0"))
    assert r.returncode == 11, r.stdout + r.stderr
    files["src/version.py"] = '__version__ = "1.4.0"\n'
    files["scripts/deploy-lib.sh"] = "# lib\nDORAMI_DEPLOY_PROTOCOL=1 # ok\n"
    env.repo.commit(files, tag="v1.4.0")
    assert env.launch(env.cmd("v1.4.0")).returncode == 0
    files["src/version.py"] = '__version__ = "1.5.0"\n'
    files["scripts/deploy-lib.sh"] = "# lib\nDORAMI_DEPLOY_PROTOCOL=$((1))\n"
    env.repo.commit(files, tag="v1.5.0")
    assert env.launch(env.cmd("v1.5.0")).returncode == 11


def test_fixed_db_path_counts_as_first_install_evidence(tmp_path: Path):
    repo = Repo(tmp_path)
    files = worker_repo_files("1.1.0")
    files["config/production.ini"] = "[storage]\ndatabase_url = sqlite:///data/wrong.db\n"
    repo.commit(files, tag="v1.1.0")
    repo.make_clone(tmp_path)
    e = Env(tmp_path, repo)
    e.create_db()  # 真正的旧库在 data/cms_data.db,ini 指错了
    e.state.mkdir(); (e.state / "first-install.token").write_text("x")
    out = tmp_path / "stub-env.txt"
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out))
    assert r.returncode == 24, r.stdout + r.stderr  # 有证据 → 不是首装;又没有容器 / 记录可作回滚点 → fail closed
    assert (e.state / "first-install.token").exists(), "有证据时令牌不消费"
    assert "database(data/cms_data.db)" in e.worker_log("v1.1.0")
    assert "无法确定回滚点" in e.worker_log("v1.1.0")


@pytest.mark.parametrize("kind", ["containers", "backups", "images", "database"])
def test_each_evidence_kind_blocks_first_install(tmp_path: Path, kind: str):
    repo = Repo(tmp_path); repo.commit(worker_repo_files("1.1.0"), tag="v1.1.0"); repo.make_clone(tmp_path)
    e = Env(tmp_path, repo)
    e.state.mkdir(); (e.state / "first-install.token").write_text("x")
    if kind == "containers":
        e.fake_write("ps_all", "cid-stopped\n")   # 只有 stopped 容器
    elif kind == "backups":
        (repo.clone / "backups").mkdir(); (repo.clone / "backups" / "cms_data.db.20260101-000000").write_text("x")
    elif kind == "images":
        e.fake_write("images", "dorami-backend-rollback:v3.57.0\n")
    else:
        e.create_db()
    out = tmp_path / "stub-env.txt"
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out))
    assert r.returncode == 24, kind + ": " + r.stdout + r.stderr  # 有证据 → 非首装;无回滚点 → fail closed
    log = e.worker_log("v1.1.0")
    assert f"有部署证据( {kind}" in log or f" {kind}" in log.split("有部署证据(")[1].split(")")[0], kind
    assert "无法确定回滚点" in log
    assert (e.state / "first-install.token").exists(), kind + ": 有证据时令牌不消费"
    assert not out.exists(), kind + ": 不得起子进程"


def test_inherited_lock_fd_must_refer_to_lock_file_and_hold_it(real: Env):
    _health(real, "v9.9.9")
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl, sys, time; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); time.sleep(30)",
        str(real.lock)])
    try:
        time.sleep(0.5)
        wrong = ("exec 9>>/dev/null; export DORAMI_DEPLOY_LOCK_FD=9; exec ./deploy-docker.sh \"$2\"")
        env = real.base_env(DORAMI_DEPLOY_LOCK_FILE=str(real.lock), DORAMI_DEPLOY_ORIGIN="pipeline",
                            DORAMI_EXPECTED_SHA=real.repo.sha_of("v9.9.9"), FAKE_HEALTH_FILE=str(real.fake / "health.json"))
        r = subprocess.run(["bash", "-c", wrong, "_", str(real.lock), "v9.9.9"], cwd=str(real.repo.clone), env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode != 0 and "锁 FD" in r.stderr and "校验失败" in r.stderr
        assert not real.calls(), "checkout 前就应拒绝,不碰 docker"
    finally:
        holder.kill(); holder.wait()


def test_remote_tag_deletion_is_treated_as_unpublished(env: Env, tmp_path: Path):
    git(env.repo.work, "push", "-q", "origin", ":refs/tags/v1.1.0", env=env.repo.env)
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 10, r.stdout + r.stderr
    assert "origin 上不存在 tag v1.1.0" in env.worker_log("v1.1.0")
    clone = env.repo.clone
    e = dict(env.repo.env)
    r = subprocess.run([str(VERIFY_REF), "v1.1.0"], cwd=str(clone), env=e, capture_output=True, text=True)
    assert r.returncode == 1 and "不存在于 origin" in r.stderr


def test_launcher_reads_ssh_original_command(env: Env):
    r = subprocess.run([str(LAUNCHER)], env=env.base_env(SSH_ORIGINAL_COMMAND=env.cmd("v1.1.0")), capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([str(LAUNCHER)], env=env.base_env(SSH_ORIGINAL_COMMAND="v1.1.0 && id"), capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


def test_health_check_respects_time_budget(real: Env):
    (real.fake / "health.json").unlink(missing_ok=True)  # curl 一直失败
    t0 = time.time()
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS="3", DORAMI_DEPLOY_HEALTH_ATTEMPTS="90")
    assert r.returncode != 0 and "预算 3s" in r.stderr
    assert time.time() - t0 < 20


def test_switched_in_progress_conflict_wins_over_replay(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    assert env.launch(env.cmd("v1.2.0"), STUB_RC="9", STUB_SWITCH="1").returncode == 9
    # last-success 仍是 v1.1.0;再请求 v1.1.0 不得回放成功——v1.2.0 的已切换事务优先拒绝
    r = env.launch(env.cmd("v1.1.0", downgrade=1))
    assert r.returncode == 20, r.stdout + r.stderr


# ══════════════ 复检剩余项(R3 / R4 / R5 / R13 / #14)与新引入(N1 / N2) ══════════════

def test_stale_pid_or_wrong_start_id_is_not_treated_as_alive(env: Env):
    env.state.mkdir(exist_ok=True)
    # ① pid 活着(测试进程自己)但 start_id 不对 → 不算存活;未 complete → 状态不一致(6)
    (env.state / "state.json").write_text(json.dumps({"run_id": "deadbeefdeadbeef", "tag": "v1.1.0", "target_sha": env.repo.sha_of("v1.1.0"),
        "worker_pid": os.getpid(), "worker_start_id": "bogus start", "phase": "running", "log": str(env.logs / "x.log")}))
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 6 and "状态不一致" in r.stderr
    # ② pid 已死 → 同样 6;phase 已 complete 的旧 state 则允许新起
    dead = subprocess.Popen(["true"]); dead.wait()
    (env.state / "state.json").write_text(json.dumps({"run_id": "deadbeefdeadbeef", "tag": "v1.1.0", "target_sha": env.repo.sha_of("v1.1.0"),
        "worker_pid": dead.pid, "worker_start_id": "x", "phase": "running", "log": str(env.logs / "x.log")}))
    assert env.launch(env.cmd("v1.1.0")).returncode == 6
    (env.state / "state.json").write_text(json.dumps({"run_id": "deadbeefdeadbeef", "phase": "complete", "rc": 7}))
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_close_in_progress_refuses_while_worker_runs(env: Env):
    first = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="5", STUB_RC="9", STUB_SWITCH="1"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    _worker_pid(env); time.sleep(0.5)
    close = subprocess.run([str(WORKER), "--close-in-progress"], env=env.base_env(), capture_output=True, text=True)
    assert close.returncode == 4 and "锁被占" in close.stderr
    first.communicate(timeout=60); assert first.returncode == 9
    close = subprocess.run([str(WORKER), "--close-in-progress"], env=env.base_env(), capture_output=True, text=True)
    assert close.returncode == 0 and env.state_json("in-progress.json") is None


def test_direction_matrix_with_modified_migration(env: Env):
    files = worker_repo_files("1.3.0", migrations=("0001", "0002"))
    files["alembic/versions/0001_m.py"] = 'revision = "0001"\nsql = "rewritten"\n'
    env.repo.commit(files, tag="v1.3.0")
    assert env.launch(env.cmd("v1.2.0")).returncode == 0
    # forward + M(共有文件被改写)→ 22
    r = env.launch(env.cmd("v1.3.0"))
    assert r.returncode == 22 and "改写" in env.worker_log("v1.3.0")
    # downgrade + M:基线 v1.3.0(容器 sha),目标 v1.2.0 → 共有的 0001 不同 → 22
    (env.state / "last-success.json").unlink()
    env.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={env.repo.sha_of('v1.3.0')}\nDORAMI_BUILD_REF=v1.3.0\n")
    r = env.launch(env.cmd("v1.2.0", downgrade=1))
    assert r.returncode == 22 and "降级时两边共有的迁移文件被改写" in env.worker_log("v1.2.0")
    # unrelated + M:基线在从 v1.1.0 分出的旁支(与 v1.2.0 互不为祖先;对象经 tag 可达)
    git(env.repo.work, "checkout", "-qb", "side", "v1.1.0", env=env.repo.env)
    side = worker_repo_files("9.0.0"); side["alembic/versions/0001_m.py"] = 'revision = "0001"\nsql = "side"\n'
    side_sha = env.repo.commit(side, tag="v9.0.0", branch="side")
    git(env.repo.work, "checkout", "-q", "main", env=env.repo.env)
    env.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={side_sha}\nDORAMI_BUILD_REF=v9.0.0\n")
    r = env.launch(env.cmd("v1.2.0", downgrade=1))
    assert r.returncode == 22 and "方向 unrelated" in env.worker_log("v1.2.0")


def test_repeated_pipeline_failures_reuse_transaction_and_keep_backup(env: Env):
    env.conf.write_text(env.conf.read_text().replace("DORAMI_DEPLOY_BACKUP_KEEP=2", "DORAMI_DEPLOY_BACKUP_KEEP=1"))
    for _ in range(3):
        assert env.launch(env.cmd("v1.1.0"), STUB_RC="9", STUB_SWITCH="1").returncode == 9
    ip = env.state_json("in-progress.json")
    backups = list((env.repo.clone / "backups").glob("cms_data.db.*"))
    assert len(backups) == 1 and Path(ip["prev"]["db_backup"]) in backups, "重试复用事务:不多做备份,原备份仍在"
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    assert Path(env.state_json("last-success.json")["prev"]["db_backup"]).exists()


def test_token_crash_window_consumed_and_not_inherited_by_other_target(tmp_path: Path):
    repo = Repo(tmp_path)
    repo.commit(worker_repo_files("1.1.0"), tag="v1.1.0"); repo.commit(worker_repo_files("1.2.0"), tag="v1.2.0")
    repo.make_clone(tmp_path)
    e = Env(tmp_path, repo); e.state.mkdir()
    token = e.state / "first-install.token"; token.write_text("tok")
    import hashlib
    digest = hashlib.sha256(b"tok").hexdigest()
    # 事务已落盘(带令牌摘要)但令牌未删 —— 崩溃窗口;同 target 重试要幂等消费并沿用授权
    (e.state / "in-progress.json").write_text(json.dumps({"txn_id": "t1", "target": {"tag": "v1.1.0", "sha": repo.sha_of("v1.1.0")},
        "prev": {"ref": "", "sha": "", "backend_image_id": "", "nginx_image_id": "", "managed_tags": [], "db_backup": ""},
        "opened_at": "x", "switched_at": None, "fresh_authorized": True, "token_digest": digest}))
    out = tmp_path / "stub-env.txt"
    r = e.launch(e.cmd("v1.1.0", downgrade=1), STUB_ENV_OUT=str(out), STUB_RC="5")
    assert r.returncode == 5, r.stdout + r.stderr
    assert not token.exists() and "幂等删除" in e.worker_log("v1.1.0")
    assert "DORAMI_DEPLOY_FRESH_OK=1" in out.read_text()
    # 换目标:未切换事务被自动关闭,授权不继承;无证据无令牌 → 无回滚点 fail closed,且不起子进程
    out.unlink()
    r = e.launch(e.cmd("v1.2.0", downgrade=1), STUB_ENV_OUT=str(out))
    assert r.returncode == 24, r.stdout + r.stderr
    log = e.worker_log("v1.2.0")
    assert "自动关闭事务 t1" in log and "fresh_ok=0" in log and not out.exists()


def test_inspect_failure_fails_closed(env: Env):
    env.fake_write("inspect_rc", "1")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 24 and "docker inspect" in env.worker_log("v1.1.0")
    assert env.state_json("in-progress.json") is None


def test_compose_stderr_warning_does_not_corrupt_container_id(env: Env):
    env.fake_write("ps_stderr", 'time="2026-09-16" level=warning msg="The \\"MISSING\\" variable is not set. Defaulting to a blank string."\n')
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.state_json("last-success.json")["prev"]["backend_image_id"] == "sha256:backendimg1"


def test_compose_error_output_is_not_echoed_into_logs(env: Env):
    secret = "FAKE_REVIEW_SECRET_123"
    env.fake_write("ps_stderr", f'unterminated quoted value: DORAMI_X_BEARER_TOKEN="{secret}\n')
    env.fake_write("ps_rc", "1")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 24
    log = env.worker_log("v1.1.0")
    assert secret not in log and secret not in r.stdout + r.stderr
    assert "unterminated quoted value" in log and "原文不回显" in log


# ── issue #203:「最终退出码只能来自同一份快照」改为读取触发的确定性夹具 ──
# 旧用例让假 worker 写出本轮 complete/rc=7 后固定 0.3 s 就覆写成另一轮;launcher 要在这 0.3 s 内完成进程起动、「等 run_id」
# 轮询与两次 python3 冷启动,CI runner 上常错过——错过在前是 rc=5(等不到本轮 run_id),错过在后是 rc=6(快照已是另一轮),
# 都不是用例想验的阶段。现在 DEPLOY_PATH 前置一个 python3 垫片:launcher 的 state_snapshot(`python3 - <state.json>`,一次读
# 三字段)**返回之后**才由垫片覆写 state——「覆写发生在取快照后」成为确定事件,不靠 sleep 对齐;launcher 本身一字不改。

OTHER_RUN = "ffffffffffffffff"


def _install_snapshot_race(env: Env, tmp_path: Path) -> dict[str, Path]:
    """装配假 worker(写本轮 complete/rc=7 后只等覆写标记)与 python3 垫片(快照读取返回后覆写、落标记);返回标记与垫片日志路径。"""
    state = env.state / "state.json"
    marker = tmp_path / "overwritten.mark"
    shim_log = tmp_path / "python3-shim.log"
    fake_log = env.logs / "fake.log"
    fake_worker = tmp_path / "fake-worker"
    fake_worker.write_text(f'''#!/bin/bash
# $1=run $2=tag $3=sha $4=dg $5=rd $6=run_id —— 一上来就写本轮 complete/rc=7;之后只等垫片覆写(标记出现)再退出,自己不按时间覆写
mkdir -p "{env.state}" "{env.logs}"
: > "{fake_log}"
python3 - "$6" "$$" "$(ps -o lstart= -p $$ | tr -s ' ' | sed 's/^ *//;s/ *$//')" <<'PY'
import json, sys
json.dump({{"run_id": sys.argv[1], "tag": "v1.1.0", "target_sha": "x", "worker_pid": int(sys.argv[2]), "worker_start_id": sys.argv[3],
           "phase": "complete", "rc": 7, "log": "{fake_log}"}}, open("{state}", "w"))
PY
for _ in $(seq 1 300); do [ -f "{marker}" ] && exit 0; sleep 0.1; done
exit 0
''')
    fake_worker.chmod(0o755)
    shimbin = tmp_path / "shimbin"; shimbin.mkdir()
    shim = shimbin / "python3"
    shim.write_text(f'''#!/bin/bash
# python3 垫片(仅本夹具):原样转发给真解释器;当调用形如 `python3 - <state.json>`(launcher 的 state_snapshot 一次性读取)
# 且读到的是本轮 complete,在该次读取**返回之后**把 state 覆写成另一轮的 complete/rc=0 并落标记。只触发一次。
printf '%s\\n' "$*" >> "{shim_log}"
"{sys.executable}" "$@"; rc=$?
if [ $# -eq 2 ] && [ "$1" = "-" ] && [ "$2" = "{state}" ] && [ ! -f "{marker}" ]; then
    "{sys.executable}" - "{state}" "{marker}" "{fake_log}" <<'PY'
import json, sys
state, marker, log = sys.argv[1:4]
d = json.load(open(state, encoding="utf-8"))
if d.get("phase") == "complete" and d.get("run_id") != "{OTHER_RUN}":
    json.dump({{"run_id": "{OTHER_RUN}", "phase": "complete", "rc": 0, "worker_pid": 1, "worker_start_id": "x", "log": log}},
              open(state, "w", encoding="utf-8"))
    open(marker, "w", encoding="utf-8").write(d["run_id"])
PY
fi
exit $rc
''')
    shim.chmod(0o755)
    conf = env.conf.read_text()
    assert f"DEPLOY_WORKER={WORKER}" in conf and f"DEPLOY_PATH={env.path}" in conf
    env.conf.write_text(conf.replace(f"DEPLOY_WORKER={WORKER}", f"DEPLOY_WORKER={fake_worker}")
                            .replace(f"DEPLOY_PATH={env.path}", f"DEPLOY_PATH={shimbin}:{env.path}"))
    return {"marker": marker, "shim_log": shim_log}


def _snapshot_diag(r: subprocess.CompletedProcess, race: dict[str, Path]) -> str:
    """失败诊断:launcher 的 stdout / stderr + 垫片收到的每次 python3 调用(一眼看出 launcher 读 state 的调用形是否变了)。"""
    shim_log = race["shim_log"].read_text() if race["shim_log"].exists() else "(空)"
    return f"--- stdout ---\n{r.stdout}--- stderr ---\n{r.stderr}--- python3 垫片日志 ---\n{shim_log}"


def test_launcher_final_rc_comes_from_the_same_snapshot(env: Env, tmp_path: Path):
    """假 worker 写本轮 complete/rc=7;launcher 取到快照**之后**state 才被覆写成另一轮的 complete/rc=0(读取触发,见上);
    launcher 必须报 7——run_id / phase / rc 只能来自同一份快照,之后不得再读全局 state。"""
    race = _install_snapshot_race(env, tmp_path)
    r = env.launch(env.cmd("v1.1.0"))
    diag = _snapshot_diag(r, race)
    assert r.returncode == 7, diag
    assert race["marker"].exists(), "前置:垫片没有触发(launcher 读快照的方式变了?)\n" + diag
    assert env.state_json("state.json")["run_id"] == OTHER_RUN, "前置:state 确实已被另一轮覆盖\n" + diag
    assert f"worker 退出码 7(run {race['marker'].read_text()})" in r.stdout, diag


# 反向对照:把 launcher 改回「二次读取 rc」的旧错误(首版脚本检视前的写法),同一夹具必须让它报出另一轮的 rc=0 而非 7
_SNAPSHOT_MUTATIONS = {
    "reread_rc_in_loop": ('\n        FINAL_RC="$snap_rc"; break\n', '\n        FINAL_RC="$(json_get "$STATE" rc "")"; break\n'),
    "reread_rc_at_exit": ('\n    exit "$FINAL_RC"\n', '\n    exit "$(json_get "$STATE" rc 6)"\n'),
}


@pytest.mark.parametrize("name", sorted(_SNAPSHOT_MUTATIONS))
def test_snapshot_race_harness_catches_second_read(env: Env, tmp_path: Path, name: str):
    """反向对照:改坏的 launcher 副本在同一夹具下必须报出另一轮的 rc=0——证明夹具会咬人,而不是碰巧放行。"""
    old, new = _SNAPSHOT_MUTATIONS[name]
    text = LAUNCHER.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"突变锚点 {old!r} 出现 {text.count(old)} 次"
    mutated = tmp_path / "mutated-launcher"
    mutated.write_text(text.replace(old, new), encoding="utf-8"); mutated.chmod(0o755)
    race = _install_snapshot_race(env, tmp_path)
    r = env.launch(env.cmd("v1.1.0"), launcher=mutated)
    diag = _snapshot_diag(r, race)
    assert race["marker"].exists(), "前置:垫片没有触发\n" + diag
    assert r.returncode == 0, f"突变 {name} 本应读到另一轮的 rc=0:\n" + diag


def test_normal_exit_with_lingering_descendants_is_not_success(env: Env):
    r = env.launch(env.cmd("v1.1.0"), STUB_DESCENDANT="5")   # 桩本身立即退出 0,但留了个 5 秒后写 switch 的后代
    assert r.returncode == 24, r.stdout + r.stderr
    assert env.state_json("in-progress.json") is not None and env.state_json("last-success.json") is None
    txn = env.state_json("in-progress.json")["txn_id"]
    time.sleep(6)
    assert not (env.state / f"{txn}.switch").exists(), "后代应已被整组终止"
    assert "视为未干净收口" in env.worker_log("v1.1.0")


def test_unrelated_baseline_with_extra_migration_is_rejected(env: Env):
    git(env.repo.work, "checkout", "-qb", "side2", "v1.1.0", env=env.repo.env)
    side = worker_repo_files("9.1.0", migrations=("0001", "9999"))
    side_sha = env.repo.commit(side, tag="v9.1.0", branch="side2")
    git(env.repo.work, "checkout", "-q", "main", env=env.repo.env)
    env.fake_write("env_of_cid-backend", f"DORAMI_BUILD_SHA={side_sha}\nDORAMI_BUILD_REF=v9.1.0\n")
    r = env.launch(env.cmd("v1.2.0", downgrade=1))
    assert r.returncode == 22, r.stdout + r.stderr
    log = env.worker_log("v1.2.0")
    assert "方向 unrelated" in log and "D=1" in log


def test_multiple_container_ids_use_the_first(env: Env):
    env.fake_write("ps_backend", "cid-backend\ncid-backend-old\n")
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.state_json("last-success.json")["prev"]["backend_image_id"] == "sha256:backendimg1"


def test_descendant_processes_are_terminated_with_the_group(env: Env):
    """目标脚本再起一个后台后代(延迟 6s 写 switch);SIGTERM worker 后后代也必须消失,不得在 complete 之后切换。"""
    first = subprocess.Popen([str(LAUNCHER), env.cmd("v1.1.0")], env=env.base_env(STUB_SLEEP="8", STUB_DESCENDANT="6"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    pid = _worker_pid(env); time.sleep(1)
    os.kill(pid, 15)
    first.communicate(timeout=60)
    assert first.returncode == 143
    txn = env.state_json("in-progress.json")["txn_id"]
    time.sleep(6)
    assert not (env.state / f"{txn}.switch").exists(), "后代仍写出了切换标记:进程组未被整组终止"
    assert "进程组" in env.worker_log("v1.1.0")


def test_deploy_docker_inode_check_handles_macos_and_missing_column(real: Env):
    _health(real, "v9.9.9")
    r = _run_deploy(real, "v9.9.9", FAKE_DF_MODE="macos", FAKE_DF_IFREE="123")
    assert r.returncode != 0 and "inode 不足" in r.stderr
    r = _run_deploy(real, "v9.9.9", FAKE_DF_MODE="macos", FAKE_DF_IFREE="50000")
    assert r.returncode == 0 and "空闲 inode 50000" in r.stdout
    r = _run_deploy(real, "v9.9.9", FAKE_DF_MODE="noinode")
    assert r.returncode == 0 and "inode 列未识别" in r.stdout


def test_container_without_build_identity_makes_last_success_unverifiable(env: Env):
    assert env.launch(env.cmd("v1.1.0")).returncode == 0
    env.fake_write("env_of_cid-backend", "OTHER=1\n")   # 旧脚本构建的镜像:没有 DORAMI_BUILD_SHA
    r = env.launch(env.cmd("v1.1.0"))
    assert r.returncode == 21, r.stdout + r.stderr   # 不回放;基线未知 → 护栏
    assert "没有构建身份" in env.worker_log("v1.1.0")


# ══════════════ issue #126 裸机回滚波的 Docker 等价守卫(docs/archive/baremetal-rollback-plan.md §4.14 / §6.6)══════════════

def test_deploy_docker_default_health_budget_unchanged():
    """健康轮询抽到 deploy-lib 共用后,Docker 路径的默认预算仍是 180 s,次数默认预算 / 2(= 90,issue #227 起随预算走),
    都由本脚本传入(不在库里定默认)。"""
    text = (ROOT / "deploy-docker.sh").read_text(encoding="utf-8")
    assert 'BUDGET="${DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS:-180}"' in text
    assert 'ATTEMPTS="${DORAMI_DEPLOY_HEALTH_ATTEMPTS:-$(( BUDGET / 2 > 0 ? BUDGET / 2 : 1 ))}"' in text
    assert "deploy_wait_healthy" in text
    lib = (ROOT / "scripts" / "deploy-lib.sh").read_text(encoding="utf-8")
    assert "HEALTH_BUDGET_SECONDS:-" not in lib, "预算默认值只能在调用方"


@pytest.mark.parametrize("arg", ["--rollback", "--status", "--code", "--adopt", "--discard-txn", "--restore-db"])
def test_deploy_docker_rejects_baremetal_only_arguments(real: Env, arg: str):
    """裸机专属参数只在 deploy.sh 装配;deploy-docker.sh 对它们仍按未知参数拒绝(不改 Docker 路径行为)。"""
    _health(real, "v9.9.9")
    r = _run_deploy(real, arg)
    assert r.returncode != 0 and "未知参数" in r.stderr
    assert not real.calls(), "参数错误须在任何 docker 调用之前"


# ══════════════ issue #227:健康验证的探针地址、失败诊断与迁移宽限 ══════════════

def test_health_probe_follows_compose_resolution(real: Env):
    """v3.67.0 手工部署的实况(推测):shell 没导出 DORAMI_HTTP_LISTEN,探针退到 80 撞上 TLS 边缘的 301 页(87 次 `bad-json`)。
    探针地址以 compose 自己的解析为准:.env 里写了 127.0.0.1:8080 就探 8080(含引号 + 行尾注释);显式空的环境变量按 compose 的
    ${VAR:-80} 语义仍是 80;compose 解析不到才回退环境变量 / 80 并提醒(codex R1 F1)。"""
    _health(real, "v9.9.9")
    edge = dict(FAKE_HEALTH_301_ON="127.0.0.1:80", DORAMI_HTTP_LISTEN=None)
    r = _run_deploy(real, "v9.9.9", **edge)
    assert r.returncode != 0
    assert "探针 http://127.0.0.1:80/api/health(来源:compose 解析)" in r.stdout
    assert "http-301" in r.stderr and "HTTP 301" in r.stderr and "301 Moved Permanently" in r.stderr and "TLS 边缘" in r.stderr, r.stderr
    assert "bad-json" not in r.stderr, "3xx 不再被当成 JSON 去解析"
    assert "与本次实际发布端口 127.0.0.1:80 不一致" in r.stderr, "conf 里的 8080 与实际发布端口不一致要提醒"
    dotenv = real.repo.clone / ".env"
    dotenv.write_text('# compose 读这个\nDORAMI_PODCAST_INSTALLATION=external\nDORAMI_HTTP_LISTEN="127.0.0.1:8080" # 收进环回\n', encoding="utf-8")
    r = _run_deploy(real, "v9.9.9", **edge)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "探针 http://127.0.0.1:8080/api/health(来源:compose 解析)" in r.stdout and "DORAMI_DEPLOY_META health=ok" in r.stdout
    assert "不一致" not in r.stderr
    # 显式空值:compose 的 ${DORAMI_HTTP_LISTEN:-80} 取默认 80,脚本跟它一致(不能自己回读 .env 的 8080)
    r = _run_deploy(real, "v9.9.9", FAKE_HEALTH_301_ON="127.0.0.1:80", DORAMI_HTTP_LISTEN="")
    assert r.returncode != 0 and "探针 http://127.0.0.1:80/api/health(来源:compose 解析)" in r.stdout and "http-301" in r.stderr, r.stdout + r.stderr
    # compose 解析失败:回退环境变量 / 80 并提醒
    real.fake_write("compose_json_rc", "1")
    r = _run_deploy(real, "v9.9.9", FAKE_HEALTH_301_ON="127.0.0.1:80", DORAMI_HTTP_LISTEN="127.0.0.1:8080")
    assert r.returncode == 0 and "来源:回退" in r.stdout and "解析不到 nginx 的发布端口" in r.stderr, r.stdout + r.stderr
    real.fake_write("compose_json_rc", "0")
    r = _run_deploy(real, "v9.9.9", FAKE_HEALTH_301_ON="127.0.0.1:80", DORAMI_HTTP_LISTEN=None, DORAMI_DEPLOY_CONF=str(real.tmp / "no-such.conf"))
    assert r.returncode == 0 and "不一致" not in r.stderr, "conf 不存在就没有一致性提醒"


def test_health_failure_reports_status_and_body_excerpt(real: Env):
    r = _run_deploy(real, "v9.9.9", FAKE_HEALTH_RAW="<!doctype html><title>维护中</title>\n" + "x" * 500, FAKE_HEALTH_CODE="200")
    assert r.returncode != 0 and "DORAMI_DEPLOY_META health=failed" in r.stdout
    assert "bad-json" in r.stderr and "最后一次响应 HTTP 200,前 200 字节:<!doctype html><title>维护中</title> xxx" in r.stderr, r.stderr
    assert "x" * 200 not in r.stderr and "…" in r.stderr, "只打前 200 字节,截断有标记"
    r = _run_deploy(real, "v9.9.9", FAKE_HEALTH_RAW="<html>502 Bad Gateway</html>", FAKE_HEALTH_CODE="502")
    assert r.returncode != 0 and "http-502" in r.stderr and "HTTP 502" in r.stderr and "连不上 backend" in r.stderr, r.stderr
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS="3")   # 从未连上:仍是「无响应」,不打响应行
    assert r.returncode != 0 and "无响应" in r.stderr and "最后一次响应" not in r.stderr


def test_backend_health_start_period_scales_with_pending_migrations(real: Env):
    _health(real, "v9.9.9")
    real.fake_write("plan.json", json.dumps({"status": "compatible", "pending": ["a"] * 6, "pending_count": 6}))
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "backend 健康检查宽限 420s(基础 60s + 迁移宽限 360s,待执行迁移 6 个)" in r.stdout
    ups = [c for c in real.calls() if c.startswith("compose up")]
    assert ups and ups[-1].endswith("start_period=420s"), ups
    # conf 调每迁移秒数;显式 start_period 照旧;上限 1200
    with open(real.conf, "a", encoding="utf-8") as fh:
        fh.write("DORAMI_DEPLOY_HEALTH_PER_MIGRATION_SECONDS=10\n")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0 and "宽限 120s(基础 60s + 迁移宽限 60s" in r.stdout, r.stdout + r.stderr
    r = _run_deploy(real, "v9.9.9", DORAMI_BACKEND_HEALTH_START_PERIOD="999s")
    assert r.returncode == 0 and "宽限 999s(compose 配置里显式设置" in r.stdout, r.stdout + r.stderr
    assert [c for c in real.calls() if c.startswith("compose up")][-1].endswith("start_period=999s")
    # .env 里显式设的也算显式(compose 解析得到 20m → 1200 s):脚本不导出、不覆盖(codex R1 F2)
    (real.repo.clone / ".env").write_text("DORAMI_BACKEND_HEALTH_START_PERIOD=20m\n", encoding="utf-8")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode == 0 and "宽限 1200s(compose 配置里显式设置" in r.stdout, r.stdout + r.stderr
    assert [c for c in real.calls() if c.startswith("compose up")][-1].endswith("start_period=-"), "不导出,交给 compose 读 .env"
    (real.repo.clone / ".env").unlink()
    real.fake_write("plan.json", json.dumps({"status": "compatible", "pending": ["a"] * 500, "pending_count": 500}))
    r = _run_deploy(real, "v9.9.9", DORAMI_DEPLOY_HEALTH_PER_MIGRATION_SECONDS="60")
    assert r.returncode == 0 and "宽限 1260s(基础 60s + 迁移宽限 1200s" in r.stdout, r.stdout + r.stderr


def test_compose_up_failure_explains_grace_and_dumps_state(real: Env):
    _health(real, "v9.9.9")
    real.fake_write("up_rc", "1")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0
    assert "docker compose up 失败(backend 健康检查宽限 60s)" in r.stderr and "不自动回滚" in r.stderr, r.stderr
    assert "DORAMI_DEPLOY_META health=" not in r.stdout, "没走到健康验证,不写 health 元数据"
    calls = real.calls()
    assert "compose ps" in calls and "compose logs --tail 50 backend" in calls


def test_health_report_survives_mktemp_failure(real: Env):
    """codex R1 F4:临时目录不可写时 deploy_wait_healthy 的结果变量已初始化,调用方仍打出完整的健康失败报告,不撞 set -u。"""
    fake = real.fakebin / "mktemp"
    fake.write_text('#!/bin/bash\ncase "$*" in *dorami-health*) echo "fake mktemp: No space left" >&2; exit 1 ;; esac\nexec /usr/bin/mktemp "$@"\n')
    fake.chmod(0o755)
    _health(real, "v9.9.9")
    r = _run_deploy(real, "v9.9.9")
    assert r.returncode != 0 and "unbound variable" not in r.stderr, r.stderr
    assert "健康检查未通过(预算 180s,尝试 0 次):tmpfile-error" in r.stderr and "DORAMI_DEPLOY_META health=failed" in r.stdout, r.stderr
