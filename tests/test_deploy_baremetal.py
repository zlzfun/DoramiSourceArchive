"""裸机部署 release 形态 / 事务 / 回滚的进程级测试(issue #126,docs/baremetal-rollback-plan.md §6)。

对象:真 deploy.sh + scripts/deploy-lib.sh + scripts/deploy-baremetal.sh(取自本工作树),跑在一个**迷你项目**上——
它只提供部署脚本真正依赖的目标代码表面(src/config.py 的路径解析、src/storage/migrations.py 的 ensure_migrated、
src/services/taxonomy_deployment.py、alembic 图、frontend/、docker/requirements.txt、pyproject requires-python、
ecosystem.config.js 用真实文件)。真 git / symlink / rename / SQLite / Alembic 不桩;PATH 桩:uv / npm / node / pm2 / nginx /
sudo / curl / pgrep / ffmpeg / ffprobe。sudo 桩只是原样执行(隔离靠把 html / nginx 配置根 / 锁 / 状态目录全部指到临时目录),
不会碰真实系统路径。pm2 桩是有状态的:start 读 release 里的 src/version.py 与环境里的构建身份写出 /api/health 读数,
src/main.py 含 BREAK_HEALTH 即「起不来」——健康门的期望值永远来自实际选中的目标。
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
OLD_SCRIPTS_DIR = ROOT / "tests" / "fixtures" / "deploy_scripts_pre_issue_126"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="需要 git 与 bash")

APP = "dorami-backend-v2"

# ── 迷你项目 ──
MINI_CONFIG = '''"""迷你 config:与真 src/config.py 同样把相对路径按 PROJECT_ROOT(代码根)解析。"""
import configparser, os
from dataclasses import dataclass
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
def _path(raw):
    p = Path(raw).expanduser()
    return str(p) if p.is_absolute() else str((PROJECT_ROOT / p).resolve())
def _database_url(raw):
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        return "sqlite:///" + _path(raw[len("sqlite:///"):])
    return raw
@dataclass(frozen=True)
class StorageConfig:
    database_url: str
@dataclass(frozen=True)
class MediaConfig:
    media_dir: str
@dataclass(frozen=True)
class PodcastArtifactStorageConfig:
    root_dir: str
@dataclass(frozen=True)
class TaxonomyDeploymentConfig:
    mode: str
    catalog_path: str
@dataclass(frozen=True)
class AppConfig:
    storage: StorageConfig
    media: MediaConfig
    podcast_artifacts: PodcastArtifactStorageConfig
    taxonomy: TaxonomyDeploymentConfig
def load_config():
    parser = configparser.ConfigParser()
    ini = os.environ.get("DORAMI_CONFIG_FILE") or str(PROJECT_ROOT / "config" / "backend.ini")
    parser.read(ini, encoding="utf-8")
    return AppConfig(
        storage=StorageConfig(_database_url(parser.get("storage", "database_url", fallback="sqlite:///data/cms_data.db"))),
        media=MediaConfig(_path(parser.get("media", "media_dir", fallback="data/media"))),
        podcast_artifacts=PodcastArtifactStorageConfig(_path(parser.get("podcast_artifacts", "root_dir", fallback="data/podcast-artifacts"))),
        taxonomy=TaxonomyDeploymentConfig(parser.get("taxonomy", "deployment", fallback="manual"),
                                          _path(parser.get("taxonomy", "catalog", fallback="config/catalog.json"))),
    )
settings = load_config()
'''

MINI_MIGRATIONS = '''"""迷你 storage.migrations:与真实模块同名接口(部署脚本只用 ensure_migrated / make_alembic_config / _current_revision)。"""
from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
def make_alembic_config(db_url=None):
    cfg = Config()
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg
def _current_revision(db_url):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            heads = MigrationContext.configure(conn).get_current_heads()
    finally:
        engine.dispose()
    return ",".join(heads) if heads else None
def ensure_migrated(db_url):
    if ":memory:" in db_url:
        return
    cfg = make_alembic_config(db_url)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    command.upgrade(cfg, "heads" if len(heads) > 1 else "head")
'''

MINI_ENV_PY = '''from alembic import context
from sqlalchemy import create_engine
config = context.config
engine = create_engine(config.get_main_option("sqlalchemy.url"))
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
'''

MIGRATIONS = {
    "0001": ('revision = "0001"\ndown_revision = None\nimport sqlalchemy as sa\nfrom alembic import op\n'
             'def upgrade():\n    op.create_table("articles", sa.Column("id", sa.Integer, primary_key=True), sa.Column("body", sa.String()))\n'
             'def downgrade():\n    pass\n'),
    "0002": ('revision = "0002"\ndown_revision = "0001"\nimport sqlalchemy as sa\nfrom alembic import op\n'
             'def upgrade():\n    op.add_column("articles", sa.Column("title", sa.String()))\n'
             'def downgrade():\n    raise RuntimeError("irreversible")\n'),
    "0003": ('revision = "0003"\ndown_revision = "0002"\nimport sqlalchemy as sa\nfrom alembic import op\n'
             'def upgrade():\n    op.add_column("articles", sa.Column("score", sa.Float()))\n'
             'def downgrade():\n    pass\n'),
}

MINI_TAXONOMY = 'def run_taxonomy_deployment(db_url, cfg):\n    return {"status": getattr(cfg, "mode", "manual")}\n'


def mini_project(version: str, *, migrations=("0001",), broken: bool = False, requires_python: str = ">=3.10",
                 frontend_marker: str = "", requirements: str = "alembic==1.13.0\nsqlalchemy==2.0.30\n",
                 old_scripts: bool = False, worktree: Path = ROOT, extra_files: dict | None = None) -> dict[str, str]:
    if old_scripts:
        scripts = {"deploy.sh": (OLD_SCRIPTS_DIR / "deploy.sh").read_text(encoding="utf-8"),
                   "scripts/deploy-lib.sh": (OLD_SCRIPTS_DIR / "deploy-lib.sh").read_text(encoding="utf-8")}
    else:
        scripts = {"deploy.sh": (worktree / "deploy.sh").read_text(encoding="utf-8"),
                   "scripts/deploy-lib.sh": (worktree / "scripts" / "deploy-lib.sh").read_text(encoding="utf-8"),
                   "scripts/deploy-baremetal.sh": (worktree / "scripts" / "deploy-baremetal.sh").read_text(encoding="utf-8")}
    files = {
        "src/version.py": f'__version__ = "{version}"\n',
        "src/config.py": MINI_CONFIG,
        "src/storage/__init__.py": "",
        "src/storage/migrations.py": MINI_MIGRATIONS,
        "src/services/__init__.py": "",
        "src/services/taxonomy_deployment.py": MINI_TAXONOMY,
        "src/main.py": "# mini backend entry\n" + ("BREAK_HEALTH = True\n" if broken else ""),
        "alembic/env.py": MINI_ENV_PY,
        "pyproject.toml": f'[project]\nname = "mini"\nversion = "{version}"\nrequires-python = "{requires_python}"\n',
        "docker/requirements.txt": requirements,
        "frontend/package.json": '{"name": "frontend", "private": true, "scripts": {"build": "vite build"}}\n',
        "frontend/src/app.js": f"// app {version} {frontend_marker}\n",
        "ecosystem.config.js": (ROOT / "ecosystem.config.js").read_text(encoding="utf-8"),
        "config/production.example.ini": "[storage]\ndatabase_url = sqlite:///data/cms_data.db\n",
        "config/catalog.json": "{}\n",
        ".gitignore": "data/\nbackups/\n*.log\n",
        **scripts,
    }
    for m in migrations:
        files[f"alembic/versions/{m}_m.py"] = MIGRATIONS[m]
    if extra_files:
        files.update(extra_files)
    return files


# ── PATH 桩 ──
FAKE_UV = r'''#!/usr/bin/env python3
import os, sys
log = os.environ.get("FAKE_UV_LOG")
args = sys.argv[1:]
if log:
    open(log, "a").write(" ".join(args) + "\n")
if args[:2] == ["python", "find"]:
    print(sys.executable); sys.exit(0)
if args[:1] == ["venv"]:
    target = [a for a in args[1:] if not a.startswith("--") and a != sys.executable and not a.endswith("python3") and not a.endswith("python")]
    d = target[-1]
    if os.environ.get("FAKE_UV_VENV_FAIL"):
        sys.exit(1)
    # 桩 venv 的 python 是 exec 到测试解释器的包装脚本(symlink 会让 python 按 symlink 所在目录找 pyvenv.cfg,
    # 从而丢掉测试 venv 的 site-packages——alembic / sqlalchemy 都在那里)
    os.makedirs(os.path.join(d, "bin"), exist_ok=True)
    py = os.path.join(d, "bin", "python")
    open(py, "w").write('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
    os.chmod(py, 0o755)
    sys.exit(0)
if args[:2] == ["pip", "install"]:
    if os.environ.get("FAKE_UV_PIP_FAIL"):
        sys.exit(1)
    sys.exit(0)
sys.stderr.write("fake uv: unhandled " + " ".join(args) + "\n"); sys.exit(1)
'''

FAKE_NPM = r'''#!/usr/bin/env python3
import hashlib, os, sys
log = os.environ.get("FAKE_NPM_LOG")
args = sys.argv[1:]
if log:
    open(log, "a").write(os.getcwd() + " :: " + " ".join(args) + "\n")
if args[:1] == ["install"]:
    sys.exit(0)
if args[:2] == ["run", "build"]:
    if os.environ.get("FAKE_NPM_BUILD_FAIL"):
        sys.stderr.write("build failed\n"); sys.exit(1)
    src = open("src/app.js").read() if os.path.exists("src/app.js") else ""
    h = hashlib.sha256(src.encode()).hexdigest()[:8]
    os.makedirs("dist/assets", exist_ok=True)
    open(f"dist/assets/index-{h}.js", "w").write("console.log(" + repr(src) + ");\n")
    open(f"dist/assets/index-{h}.css", "w").write("body{}\n/* " + h + " */\n")
    open("dist/index.html", "w").write(
        '<!doctype html><html><head><link rel="stylesheet" href="/assets/index-%s.css"></head>'
        '<body><script type="module" src="/assets/index-%s.js"></script></body></html>\n' % (h, h))
    sys.exit(0)
sys.exit(0)
'''

FAKE_PM2 = r'''#!/usr/bin/env python3
"""有状态的 pm2 桩:state.json 里记 {name: {cwd, pid, status, env}};start 后按 release 内容写 /api/health 读数。"""
import json, os, random, sys
d = os.environ["FAKE_PM2_DIR"]
state_path = os.path.join(d, "state.json")
health = os.environ.get("FAKE_HEALTH_FILE", "")
app = os.environ.get("PM2_APP_NAME", "dorami-backend-v2")
args = sys.argv[1:]
open(os.path.join(d, "calls.log"), "a").write(" ".join(args) + "\n")
def load():
    try:
        return json.load(open(state_path))
    except Exception:
        return {}
def save(s):
    json.dump(s, open(state_path, "w"))
def version_of(cwd):
    try:
        txt = open(os.path.join(cwd, "src", "version.py")).read()
        return txt.split('"')[1]
    except Exception:
        return ""
def boot(cwd, env):
    main = ""
    try:
        main = open(os.path.join(cwd, "src", "main.py")).read()
    except Exception:
        pass
    ok = "BREAK_HEALTH" not in main and os.path.exists(os.path.join(cwd, "venv", "bin", "python"))
    if ok and health:
        json.dump({"status": "ok", "version": version_of(cwd),
                   "build": {"ref": env.get("DORAMI_BUILD_REF", ""), "sha": env.get("DORAMI_BUILD_SHA", ""), "source": "env" if env.get("DORAMI_BUILD_SHA") else "unknown"}},
                  open(health, "w"))
    elif health and os.path.exists(health):
        os.unlink(health)
    return "online" if ok else "errored"
s = load()
cmd = args[0] if args else ""
if cmd == "describe":
    sys.exit(0 if args[1] in s else 1)
if cmd == "jlist":
    print(json.dumps([{"name": n, "pid": v["pid"], "pm2_env": {"status": v["status"], "pm_cwd": v["cwd"], "env": v["env"],
                       "DORAMI_BUILD_SHA": v["env"].get("DORAMI_BUILD_SHA", ""), "DORAMI_BUILD_REF": v["env"].get("DORAMI_BUILD_REF", "")}}
                      for n, v in s.items()]))
    sys.exit(0)
if cmd == "start":
    eco = [a for a in args[1:] if not a.startswith("--")][0]
    cwd = os.path.dirname(os.path.abspath(eco))
    env = {k: os.environ.get(k, "") for k in ("DORAMI_BUILD_REF", "DORAMI_BUILD_SHA", "DORAMI_CONFIG_FILE")}
    status = boot(cwd, env)
    s[app] = {"cwd": cwd, "pid": random.randint(1000, 60000), "status": status, "env": env}
    save(s); sys.exit(0)
if cmd == "reload":
    if app in s:
        s[app]["env"].update({k: os.environ.get(k, s[app]["env"].get(k, "")) for k in ("DORAMI_BUILD_REF", "DORAMI_BUILD_SHA")})
        s[app]["status"] = boot(s[app]["cwd"], s[app]["env"]); s[app]["pid"] = random.randint(1000, 60000)
        save(s)
    sys.exit(0)
if cmd == "delete":
    s.pop(args[1], None); save(s)
    if health and os.path.exists(health):
        os.unlink(health)
    sys.exit(0)
if cmd == "save":
    if os.environ.get("FAKE_PM2_SAVE_FAIL"):
        sys.exit(1)
    home = os.path.expanduser("~")
    os.makedirs(os.path.join(home, ".pm2"), exist_ok=True)
    json.dump([{"name": n, "pm_cwd": v["cwd"]} for n, v in s.items()], open(os.path.join(home, ".pm2", "dump.pm2"), "w"))
    sys.exit(0)
sys.exit(0)
'''

FAKE_NGINX = r'''#!/usr/bin/env python3
import glob, os, re, sys
etc = os.environ["FAKE_NGINX_ETC"]
args = sys.argv[1:]
open(os.path.join(etc, "calls.log"), "a").write(" ".join(args) + "\n")
def included():
    files = []
    main = os.path.join(etc, "nginx.conf")
    try:
        for m in re.finditer(r"^\s*include\s+([^;]+);", open(main).read(), re.M):
            files += sorted(glob.glob(m.group(1).strip()))
    except OSError:
        pass
    files += sorted(glob.glob(os.path.join(etc, "conf.d", "*.conf"))) + sorted(glob.glob(os.path.join(etc, "sites-enabled", "*")))
    out, seen = [], set()
    for f in files:
        if f not in seen:
            seen.add(f); out.append(f)
    return out
if "-V" in args:
    sys.stderr.write(f"nginx version: fake\nconfigure arguments: --conf-path={etc}/nginx.conf\n"); sys.exit(0)
if "-T" in args or "-t" in args:
    if os.path.exists(os.path.join(etc, ".t_fail")):
        sys.stderr.write("nginx: [emerg] fake failure\n"); sys.exit(1)
    if "-T" in args:
        for f in included():
            try:
                body = open(f).read()
            except OSError:
                continue
            print(f"# configuration file {f}:")
            print(body)
    sys.exit(0)
sys.exit(0)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
"""按站点根目录服务文件的 curl 桩:/api/health → FAKE_HEALTH_FILE;其它路径 → FAKE_SITE_ROOT 下的文件(缺则 SPA 回退 index.html)。"""
import mimetypes, os, sys
args = sys.argv[1:]
out, fmt, url, fail_on_error = None, "", "", False
i = 0
while i < len(args):
    a = args[i]
    if a == "-o": out = args[i + 1]; i += 2; continue
    if a == "-w": fmt = args[i + 1]; i += 2; continue
    if a in ("-H", "--resolve", "--cacert", "--connect-timeout", "--max-time"): i += 2; continue
    if a.startswith("-"):
        if "f" in a and not a.startswith("--"): fail_on_error = True
        i += 1; continue
    url = a; i += 1
path = url.split("://", 1)[-1].split("/", 1)[1] if "/" in url.split("://", 1)[-1] else ""
path = "/" + path.split("?")[0]
code, ctype, body, redirect = "404", "", b"", ""
if path == "/api/health":
    p = os.environ.get("FAKE_HEALTH_FILE", "")
    if p and os.path.exists(p):
        code, ctype, body = "200", "application/json", open(p, "rb").read()
    else:
        sys.exit(7)
else:
    root = os.environ.get("FAKE_SITE_ROOT", "")
    if path == "/" and os.environ.get("FAKE_SSL_REDIRECT"):
        code, redirect = "301", "https://example.test/"
    else:
        f = os.path.join(root, path.lstrip("/")) if root else ""
        if f and os.path.isfile(f):
            code, ctype, body = "200", mimetypes.guess_type(f)[0] or "application/octet-stream", open(f, "rb").read()
            if f.endswith(".js"): ctype = "text/javascript"
        elif root and os.path.isfile(os.path.join(root, "index.html")):
            code, ctype, body = "200", "text/html", open(os.path.join(root, "index.html"), "rb").read()
if code.startswith("4") and fail_on_error:
    sys.exit(22)
if out:
    open(out, "wb").write(body)
else:
    sys.stdout.buffer.write(body)
if fmt:
    sys.stdout.write(fmt.replace("%{http_code}", code).replace("%{content_type}", ctype).replace("%{redirect_url}", redirect))
sys.exit(0)
'''

FAKE_SUDO = '#!/bin/bash\nexec "$@"\n'
FAKE_TRUE = '#!/bin/bash\nexit 0\n'


def _git_env(home: Path) -> dict:
    env = dict(os.environ)
    env.update({"HOME": str(home), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_NOSYSTEM": "1"})
    return env


def git(cwd: Path, *args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=check, capture_output=True, text=True)


class Repo:
    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"; self.home.mkdir()
        self.env = _git_env(self.home)
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.origin), env=self.env)
        git(tmp_path, "init", "-b", "main", str(self.work), env=self.env)
        git(self.work, "remote", "add", "origin", str(self.origin), env=self.env)
        self.clone: Path | None = None

    def commit(self, files: dict[str, str], tag: str | None = None, message: str = "c") -> str:
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
        git(self.work, "push", "-q", "origin", "main", "--tags", env=self.env)
        return sha

    def make_clone(self, tmp_path: Path, checkout: str | None = None) -> Path:
        self.clone = tmp_path / "repo"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.clone), env=self.env)
        if checkout:
            git(self.clone, "checkout", "-q", "--detach", checkout, env=self.env)
        return self.clone

    def sha_of(self, ref: str) -> str:
        return git(self.work, "rev-parse", f"{ref}^{{commit}}", env=self.env).stdout.strip()


class BM:
    """一台「裸机」:临时 nginx 配置根 / html 目录 / pm2 状态 / 锁 / HOME,全部隔离。"""

    def __init__(self, tmp_path: Path, repo: Repo):
        self.tmp = tmp_path; self.repo = repo; self.clone = repo.clone
        self.fakebin = tmp_path / "fakebin"; self.fakebin.mkdir()
        for name, body in (("uv", FAKE_UV), ("npm", FAKE_NPM), ("node", FAKE_TRUE), ("pm2", FAKE_PM2), ("nginx", FAKE_NGINX),
                           ("curl", FAKE_CURL), ("sudo", FAKE_SUDO), ("pgrep", FAKE_TRUE), ("ffmpeg", FAKE_TRUE), ("ffprobe", FAKE_TRUE)):
            p = self.fakebin / name; p.write_text(body); p.chmod(0o755)
        self.etc = tmp_path / "etc-nginx"; (self.etc / "conf.d").mkdir(parents=True)
        (self.etc / "nginx.conf").write_text("events {}\nhttp {\n    include " + str(self.etc) + "/conf.d/*.conf;\n}\n")
        self.www = tmp_path / "www"; self.www.mkdir()
        self.html_dir = self.www / "site"
        self.pm2dir = tmp_path / "pm2"; self.pm2dir.mkdir()
        self.fake = tmp_path / "fake"; self.fake.mkdir()
        self.health = self.fake / "health.json"
        self.lock = tmp_path / "deploy.lock"
        self.write_ini()

    def write_ini(self, **overrides: str) -> None:
        """按节写 production.ini;overrides 里给出的节整节替换(键值行文本)。"""
        sections = {
            "server": "port = 8088",
            "storage": "database_url = sqlite:///data/cms_data.db",
            "taxonomy": "deployment = manual",
            "nginx": (f"html_dir = {self.html_dir}\nsite_name = dorami\nserver_name = _\nlisten_port = 8080\n"
                      "backend_proxy_host = 127.0.0.1\nbackend_proxy_port = 8088"),
        }
        sections.update(overrides)
        ini = "".join(f"[{name}]\n{body}\n" for name, body in sections.items())
        (self.clone / "config").mkdir(exist_ok=True)
        (self.clone / "config" / "production.ini").write_text(ini, encoding="utf-8")

    def env(self, **extra: str) -> dict:
        env = dict(os.environ)
        for k in list(env):
            if k.startswith(("DORAMI_DEPLOY_", "DORAMI_BUILD_", "FAKE_", "BM_", "NGINX_", "PM2_")) or k in ("DORAMI_CONFIG_FILE", "VENV_DIR"):
                env.pop(k)
        env.update({
            "PATH": f"{self.fakebin}:{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.repo.home),
            "DORAMI_DEPLOY_LOCK_FILE": str(self.lock),
            "DORAMI_NGINX_ETC_DIR": str(self.etc),
            "FAKE_NGINX_ETC": str(self.etc), "FAKE_PM2_DIR": str(self.pm2dir), "FAKE_HEALTH_FILE": str(self.health),
            "FAKE_SITE_ROOT": str(self.html_dir), "FAKE_UV_LOG": str(self.fake / "uv.log"), "FAKE_NPM_LOG": str(self.fake / "npm.log"),
            "DORAMI_DEPLOY_STABLE_SECONDS": "0", "DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS": "12", "DORAMI_DEPLOY_HEALTH_ATTEMPTS": "4",
            "DORAMI_DEPLOY_MIN_FREE_GB": "0",
        })
        env.update(extra)
        return env

    def run(self, *args: str, timeout: int = 300, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["./deploy.sh", *args], cwd=str(self.clone), env=self.env(**extra), capture_output=True,
                              text=True, errors="replace", timeout=timeout)

    def state(self, name: str) -> dict | None:
        p = self.clone / "deploy-state" / name
        return json.loads(p.read_text()) if p.exists() else None

    def closed(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.clone / "deploy-state" / "closed").glob("*.json"))]

    def pm2(self) -> dict:
        p = self.pm2dir / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def pm2_calls(self) -> list[str]:
        p = self.pm2dir / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def health_json(self) -> dict | None:
        return json.loads(self.health.read_text()) if self.health.exists() else None

    def db_heads(self) -> list[str]:
        db = self.clone / "data" / "cms_data.db"
        if not db.exists():
            return []
        con = sqlite3.connect(db)
        try:
            return sorted(r[0] for r in con.execute("select version_num from alembic_version"))
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()

    def db_rows(self) -> int:
        con = sqlite3.connect(self.clone / "data" / "cms_data.db")
        try:
            return con.execute("select count(*) from articles").fetchone()[0]
        finally:
            con.close()

    def db_insert(self, n: int = 1) -> None:
        con = sqlite3.connect(self.clone / "data" / "cms_data.db")
        for _ in range(n):
            con.execute("insert into articles (body) values ('x')")
        con.commit(); con.close()

    def releases(self) -> list[Path]:
        d = self.clone / "releases"
        return sorted(p for p in d.iterdir() if p.is_dir()) if d.exists() else []

    def head(self) -> str:
        return git(self.clone, "rev-parse", "HEAD", env=self.repo.env).stdout.strip()


@pytest.fixture
def bm(tmp_path: Path) -> BM:
    repo = Repo(tmp_path)
    repo.commit(mini_project("1.0.0"), tag="v1.0.0")
    repo.make_clone(tmp_path)
    return BM(tmp_path, repo)


def _deploy_v1(bm: BM) -> subprocess.CompletedProcess:
    r = bm.run("--here", DORAMI_DEPLOY_FRESH_OK="1")
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _commit_and_pull(bm: BM, files: dict[str, str], tag: str | None = None) -> str:
    sha = bm.repo.commit(files, tag=tag)
    git(bm.clone, "fetch", "-q", "origin", "--tags", env=bm.repo.env)
    git(bm.clone, "checkout", "-q", "--detach", sha, env=bm.repo.env)
    return sha


# ══════════════ 用法 / 锁 / 首装门 ══════════════

def test_usage_errors_exit_2_before_touching_anything(bm: BM):
    for args in (["--bogus"], ["--restore-db"], ["--code", "v1.0.0", "--here"], ["--status", "v1.0.0"], ["--adopt-sha", "abc"]):
        r = bm.run(*args)
        assert r.returncode == 2, (args, r.stderr)
    assert not (bm.clone / "deploy-state").exists() and not (bm.clone / "releases").exists()


def test_lock_conflict_exits_4(bm: BM):
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl, sys, time; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); time.sleep(30)", str(bm.lock)])
    try:
        time.sleep(0.5)
        r = bm.run("--here", DORAMI_DEPLOY_FRESH_OK="1")
        assert r.returncode == 4 and "另一个部署正在进行" in r.stderr
    finally:
        holder.kill(); holder.wait()


def test_first_install_needs_explicit_fresh_ok(bm: BM):
    r = bm.run("--here")
    assert r.returncode == 23 and "DORAMI_DEPLOY_FRESH_OK=1" in r.stderr
    ip = bm.state("in-progress.json")
    assert ip and ip["stage"]["completed"] == "venv_ready", "首装门在目标上下文迁移计划之后、任何宿主写入之前"
    assert not bm.html_dir.exists() and not (bm.clone / "data" / "cms_data.db").exists()
    # 再跑:事务未改宿主 → 自动归档;授权后成功
    r = bm.run("--here", DORAMI_DEPLOY_FRESH_OK="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "自动归档" in r.stdout and len(bm.closed()) == 1


def test_fresh_plan_with_evidence_is_refused_without_override(bm: BM):
    _deploy_v1(bm)
    # 库文件「消失」但 last-success / html_dir / pm2 等证据都在:fresh 一律拒绝,没有覆盖开关
    (bm.clone / "data" / "cms_data.db").unlink()
    r = bm.run("--here", DORAMI_DEPLOY_FRESH_OK="1")
    assert r.returncode == 23 and "既有部署证据" in r.stderr and "不提供覆盖开关" in r.stderr


# ══════════════ release 形态:首次部署的布局 ══════════════

def test_first_deploy_builds_release_layout(bm: BM):
    r = _deploy_v1(bm)
    assert "Deploy complete" in r.stdout
    ls = bm.state("last-success.json")
    assert ls and ls["kind"] == "deploy" and ls["mode"] == "here" and ls["prev"] is None
    assert ls["target"]["code_sha"] == bm.head() and ls["target"]["dirty"] is False
    assert ls["capabilities"]["rollback"] is False, "首装无回滚点"
    release = Path(ls["target"]["release"])
    assert (release / "app" / "src" / "version.py").is_file() and (release / "app.sha256").is_file()
    assert (release / "dist" / "index.html").is_file() and (release / "dist.sha256").is_file()
    assert (release / "nginx" / "site.conf").is_file() and (release / "nginx" / "changes.json").is_file() and (release / "nginx" / "snapshot.json").is_file()
    assert (release / "controller" / "rollback.sh").is_file() and (release / "controller" / "deploy-baremetal.sh").is_file()
    assert (release / "manifest.json").is_file()
    assert not (release / "build").exists(), "构建目录用完即删"
    # 挂点
    app = release / "app"
    assert (app / "venv").is_symlink() and Path(os.path.realpath(app / "venv")).parent == (bm.clone / "venvs").resolve()
    assert (app / "data").is_symlink() and os.path.realpath(app / "data") == os.path.realpath(bm.clone / "data")
    assert (app / "logs").is_symlink() and (app / "config" / "production.ini").is_symlink()
    venv = Path(os.path.realpath(app / "venv"))
    assert (venv / ".dorami-complete").read_text().strip() == "kind=fingerprint" and (venv / "inputs.json").is_file()
    assert ls["target"]["venv"] == str(venv)
    # 现场
    assert (bm.clone / "current").is_symlink() and os.path.realpath(bm.clone / "current") == os.path.realpath(app)
    assert bm.html_dir.is_symlink() and os.path.realpath(bm.html_dir) == os.path.realpath(release / "dist")
    assert os.path.realpath(bm.pm2()[APP]["cwd"]) == os.path.realpath(app)
    assert bm.pm2()[APP]["env"]["DORAMI_BUILD_SHA"] == bm.head()
    assert bm.health_json()["build"]["sha"] == bm.head() and bm.health_json()["version"] == "1.0.0"
    assert bm.db_heads() == ["0001"]
    assert (bm.clone / "deploy-state" / "rollback").exists() and os.access(bm.clone / "deploy-state" / "rollback", os.X_OK)
    assert not (bm.clone / "deploy-state" / "in-progress.json").exists()
    assert (bm.etc / "conf.d" / "dorami.conf").is_file()
    # pm2 序:delete(无进程时不调)→ start → save
    calls = [c.split()[0] for c in bm.pm2_calls() if c.split()[0] in ("delete", "start", "save")]
    assert calls == ["start", "save"]
    # 快照:首装没有库,跳过
    assert ls["db"]["snapshot"] in ("", None)


def test_second_deploy_records_prev_reuses_venv_and_snapshots_db(bm: BM):
    _deploy_v1(bm)
    first = bm.state("last-success.json")
    bm.db_insert(3)
    sha2 = _commit_and_pull(bm, mini_project("1.1.0", migrations=("0001", "0002")), tag="v1.1.0")
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    ls = bm.state("last-success.json")
    assert ls["target"]["code_sha"] == sha2 and ls["prev"]["txn_id"] == first["txn_id"] and ls["prev"]["code_sha"] == first["target"]["code_sha"]
    assert ls["capabilities"]["rollback"] is True
    assert bm.db_heads() == ["0002"] and bm.db_rows() == 3
    snap = Path(ls["db"]["snapshot"])
    assert snap.is_file() and snap.parent.name == ls["txn_id"] and snap.parent.parent == (bm.clone / "backups" / "baremetal")
    con = sqlite3.connect(snap); assert con.execute("select count(*) from articles").fetchone()[0] == 3; con.close()
    assert ls["db"]["plan"]["status"] == "compatible" and ls["db"]["plan"]["pending_count"] == 1
    assert ls["db"]["heads_before"] == ["0001"]
    assert len(bm.releases()) == 2 and Path(first["target"]["release"]).is_dir(), "旧 release 保留(回滚材料)"
    assert len(list((bm.clone / "venvs").iterdir())) == 1, "requirements 未变 → venv 按指纹复用"
    assert "复用" in r.stdout
    assert os.path.realpath(bm.html_dir) == os.path.realpath(Path(ls["target"]["release"]) / "dist")
    assert bm.health_json()["version"] == "1.1.0"
    calls = [c.split()[0] for c in bm.pm2_calls() if c.split()[0] in ("delete", "start", "save")]
    assert calls == ["start", "save", "delete", "start", "save"]


def test_changed_requirements_build_second_venv(bm: BM):
    _deploy_v1(bm)
    _commit_and_pull(bm, mini_project("1.1.0", requirements="alembic==1.13.0\nsqlalchemy==2.0.31\n"))
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list((bm.clone / "venvs").iterdir())) == 2 and "新建" in r.stdout


def test_extras_require_pinned_list_and_enter_fingerprint(bm: BM):
    _deploy_v1(bm)
    r = bm.run("--here", DORAMI_DEPLOY_EXTRAS="crawl4ai")
    assert r.returncode != 0 and "requirements-crawl4ai.txt" in r.stderr
    _commit_and_pull(bm, mini_project("1.1.0", extra_files={"docker/requirements-crawl4ai.txt": "crawl4ai==0.9.0\n"}))
    r = bm.run("--here", DORAMI_DEPLOY_EXTRAS="crawl4ai")
    assert r.returncode == 0, r.stdout + r.stderr
    uv_log = (bm.fake / "uv.log").read_text()
    install_lines = [l for l in uv_log.splitlines() if l.startswith("pip install")]
    assert install_lines and "requirements-crawl4ai.txt" in install_lines[-1]
    assert not any(tok == "-e" for tok in install_lines[-1].split()), "按钉版清单装,不做 editable 安装"
    assert len(list((bm.clone / "venvs").iterdir())) == 2, "extras 进指纹 → 另一个 venv"
    ls = bm.state("last-success.json")
    inputs = json.loads((Path(ls["target"]["venv"]) / "inputs.json").read_text())
    assert "crawl4ai" in inputs["extras"]


# ══════════════ 健康门与未收口事务 ══════════════

def test_health_failure_alerts_keeps_transaction_and_blocks_next_deploy(bm: BM):
    _deploy_v1(bm)
    first = bm.state("last-success.json")
    sha2 = _commit_and_pull(bm, mini_project("1.1.0", broken=True))
    r = bm.run("--here")
    assert r.returncode == 1
    assert "健康核对未通过" in r.stderr and "./deploy.sh --rollback" in r.stderr and "未自动回滚" in r.stderr
    ip = bm.state("in-progress.json")
    assert ip and ip["target"]["code_sha"] == sha2 and ip["stage"]["completed"] == "process_started" and ip["stage"]["intent"] == "health_ok"
    assert ip["stage"]["error"] and "健康核对未通过" in ip["stage"]["error"]
    assert bm.state("last-success.json") == first, "last-success 不变"
    assert os.path.realpath(bm.html_dir) == os.path.realpath(Path(ip["target"]["release"]) / "dist"), "已切换且未自动回滚"
    assert bm.pm2()[APP]["status"] == "errored"
    # --status 描述实际阶段;再部署被已改宿主的事务阻断
    s = bm.run("--status")
    assert s.returncode == 0 and "中断于 completed=process_started intent=health_ok" in s.stdout
    r = bm.run("--here")
    assert r.returncode == 20 and "--rollback" in r.stderr and "--discard-txn" in r.stderr


def test_failure_before_host_write_is_auto_archived_next_time(bm: BM):
    _deploy_v1(bm)
    _commit_and_pull(bm, mini_project("1.1.0"))
    r = bm.run("--here", FAKE_NPM_BUILD_FAIL="1")
    assert r.returncode == 1 and "前端构建失败" in r.stderr
    ip = bm.state("in-progress.json")
    assert ip["stage"]["completed"] == "venv_ready" and ip["stage"]["intent"] == "dist_built"
    assert bm.health_json()["version"] == "1.0.0", "构建期失败,在线服务零改动"
    assert not (bm.etc / "conf.d" / "dorami.conf").read_text() == "", "nginx 未被碰"
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "自动归档" in r.stdout
    closed = bm.closed()
    assert len(closed) == 1 and closed[0]["txn_id"] == ip["txn_id"] and closed[0]["closed"]["reason"].startswith("auto-closed")


def test_nginx_validation_failure_restores_files_and_counts_as_host_write(bm: BM):
    _deploy_v1(bm)
    site = bm.etc / "conf.d" / "dorami.conf"
    before = site.read_text()
    _commit_and_pull(bm, mini_project("1.1.0"))
    (bm.etc / ".t_fail").write_text("")
    r = bm.run("--here")
    assert r.returncode == 1 and "nginx" in r.stderr
    assert site.read_text() == before, "按变更集恢复原状"
    ip = bm.state("in-progress.json")
    assert ip["stage"]["intent"] == "nginx_prepared" and ip["stage"]["completed"] == "dist_built"
    (bm.etc / ".t_fail").unlink()
    r = bm.run("--here")
    assert r.returncode == 20, "首次宿主写入 intent 已持久化:不自动归档,要求 --rollback / --discard-txn"
    r = bm.run("--discard-txn")
    assert r.returncode == 2 and "--yes" in r.stderr
    r = bm.run("--discard-txn", "--yes")
    assert r.returncode == 0 and len(bm.closed()) == 1
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr


def test_discard_txn_without_transaction_is_noop(bm: BM):
    _deploy_v1(bm)
    r = bm.run("--discard-txn")
    assert r.returncode == 0 and "没有未收口的事务" in r.stdout


# ══════════════ 身份:dirty 固化 / --code / 能力检查 ══════════════

def test_dirty_worktree_is_frozen_into_snapshot_commit(bm: BM):
    _deploy_v1(bm)
    (bm.clone / "src" / "version.py").write_text('__version__ = "1.0.1"\n')
    (bm.clone / "src" / "hotfix.py").write_text("HOTFIX = True\n")     # 未跟踪源码:也固化
    (bm.clone / "data").mkdir(exist_ok=True); (bm.clone / "data" / "junk.bin").write_bytes(b"\0" * 10)
    head = bm.head()
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    ls = bm.state("last-success.json")
    assert ls["target"]["dirty"] is True and ls["target"]["code_sha"] != head and ls["target"]["head_sha"] == head
    assert ls["target"]["ref"].endswith("-dirty") and ls["target"]["pin_ref"] == f"refs/dorami-deploy/{ls['txn_id']}"
    assert bm.head() == head, "HEAD 不动"
    assert git(bm.clone, "rev-parse", ls["target"]["pin_ref"], env=bm.repo.env).stdout.strip() == ls["target"]["code_sha"]
    app = Path(ls["target"]["release"]) / "app"
    assert (app / "src" / "hotfix.py").is_file() and '"1.0.1"' in (app / "src" / "version.py").read_text()
    tree = git(bm.clone, "ls-tree", "-r", "--name-only", ls["target"]["code_sha"], env=bm.repo.env).stdout
    assert "data/" not in tree and "venvs/" not in tree and "releases/" not in tree and "deploy-state/" not in tree
    assert bm.health_json()["version"] == "1.0.1" and bm.health_json()["build"]["sha"] == ls["target"]["code_sha"]


def test_clean_worktree_identity_is_head_and_snapshot_size_limit(bm: BM):
    _deploy_v1(bm)
    (bm.clone / "src" / "big.bin").write_bytes(b"\1" * (2 * 1024 * 1024))
    r = bm.run("--here", DORAMI_DEPLOY_SNAPSHOT_MAX_MB="1")
    assert r.returncode != 0 and "超过阈值" in r.stderr
    (bm.clone / "src" / "big.bin").unlink()
    assert not bm.state("in-progress.json"), "固化在开事务之前失败,不留事务"


def test_tag_mode_refuses_target_without_txn_capability_and_code_mode_deploys_it(bm: BM):
    """目标 tag 的脚本没有 DORAMI_BAREMETAL_TXN → 一律 exit 11 且 HEAD 不动;--code 由当前编排器部署那份代码。"""
    old_sha = _commit_and_pull(bm, mini_project("0.9.0", old_scripts=True), tag="v0.9.0")
    new_sha = _commit_and_pull(bm, mini_project("1.0.0"), tag="v1.0.0b")
    _deploy_v1(bm)
    r = bm.run("v0.9.0")
    assert r.returncode == 11 and "--code v0.9.0" in r.stderr
    assert bm.head() == new_sha and not bm.state("in-progress.json")
    r = bm.run("--code", "v0.9.0")
    assert r.returncode == 0, r.stdout + r.stderr
    ls = bm.state("last-success.json")
    assert ls["mode"] == "code" and ls["target"]["ref"] == "v0.9.0" and ls["target"]["code_sha"] == old_sha
    assert ls["orchestrator_sha"] == new_sha and bm.head() == new_sha
    assert bm.health_json()["version"] == "0.9.0"


def test_tag_mode_checks_out_and_reexecs_target_script(bm: BM):
    _deploy_v1(bm)
    sha2 = bm.repo.commit(mini_project("1.1.0", migrations=("0001", "0002")), tag="v1.1.0")
    r = bm.run("v1.1.0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "切换到发布版 v1.1.0" in r.stdout and bm.head() == sha2
    ls = bm.state("last-success.json")
    assert ls["mode"] == "tag" and ls["target"]["ref"] == "v1.1.0" and ls["target"]["code_sha"] == sha2 and ls["target"]["dirty"] is False


def test_old_script_bootstraps_to_new_tag(tmp_path: Path):
    """生产还停在本波之前的 deploy.sh 上:旧脚本 checkout 新 tag 后 exec 新脚本,新脚本走 release 形态(§6.7)。"""
    repo = Repo(tmp_path)
    repo.commit(mini_project("0.9.0", old_scripts=True), tag="v0.9.0")
    sha2 = repo.commit(mini_project("1.0.0"), tag="v1.0.0")
    repo.make_clone(tmp_path, checkout="v0.9.0")
    bm = BM(tmp_path, repo)
    r = bm.run("v1.0.0", DORAMI_DEPLOY_FRESH_OK="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "切换到发布版 v1.0.0" in r.stdout and bm.head() == sha2
    ls = bm.state("last-success.json")
    assert ls and ls["target"]["ref"] == "v1.0.0" and ls["mode"] == "tag"


# ══════════════ 目标上下文检查 ══════════════

def test_path_probe_mounts_extra_storage_root_and_rejects_conflicts(bm: BM):
    _deploy_v1(bm)
    bm.write_ini(media="media_dir = state/media")
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "补建挂点 app/state" in r.stdout
    app = Path(bm.state("last-success.json")["target"]["release"]) / "app"
    assert (app / "state").is_symlink() and os.path.realpath(app / "state") == os.path.realpath(bm.clone / "state")
    bm.write_ini(media="media_dir = src/media")
    r = bm.run("--here")
    assert r.returncode == 33 and "挂点冲突" in r.stderr


def test_path_probe_rejects_baseline_drift_between_code_versions(bm: BM):
    """目标代码把媒体目录的默认值改到别处(代码级差异):基准(当前 release 上下文)与目标不等 → exit 33,切换前拒绝。"""
    _deploy_v1(bm)
    drifted = MINI_CONFIG.replace('fallback="data/media"', 'fallback="state/media"')
    _commit_and_pull(bm, mini_project("1.1.0", extra_files={"src/config.py": drifted}))
    r = bm.run("--here")
    assert r.returncode == 33 and "与基准不一致" in r.stderr and "media_dir" in r.stderr
    assert bm.health_json()["version"] == "1.0.0", "切换前拒绝,现场不变"


def test_database_moved_in_config_is_caught_by_fresh_gate(bm: BM):
    """运维把 [storage] database_url 改到一个不存在的库:迁移计划报 fresh 而本机有部署证据 → 首装门拒绝,不起空站。"""
    _deploy_v1(bm)
    other = bm.tmp / "elsewhere"; other.mkdir()
    bm.write_ini(storage=f"database_url = sqlite:///{other}/cms.db")
    r = bm.run("--here")
    assert r.returncode == 23 and "既有部署证据" in r.stderr
    assert not (other / "cms.db").exists()


def test_requires_python_is_checked_independently(bm: BM):
    _deploy_v1(bm)
    _commit_and_pull(bm, mini_project("1.1.0", requires_python=">=99.0"))
    r = bm.run("--here")
    assert r.returncode != 0 and "requires-python" in r.stderr


def test_incompatible_database_blocks_forward_deploy(bm: BM):
    _deploy_v1(bm)
    v1 = bm.head()
    _commit_and_pull(bm, mini_project("1.1.0", migrations=("0001", "0002")))
    assert bm.run("--here").returncode == 0
    r = bm.run("--code", v1)
    assert r.returncode == 1 and "不兼容" in r.stderr
    assert bm.health_json()["version"] == "1.1.0", "切换前拒绝,现场不变"


def test_non_sqlite_database_needs_explicit_no_rollback_guarantee(bm: BM):
    _deploy_v1(bm)
    bm.write_ini(storage="database_url = postgresql://u:p@localhost/db")
    r = bm.run("--here")
    assert r.returncode == 24 and "--no-rollback-guarantee" in r.stderr


# ══════════════ 收养(§4.11)══════════════

def _setup_old_form(bm: BM, sha: str, version: str = "1.0.0", *, running: bool = True) -> None:
    """把「裸机」摆成本波之前的形态:仓库内 venv/(带 editable finder)、html_dir 真实目录、conf.d 站点、库文件、PM2 进程从仓库根起。"""
    venv = bm.clone / "venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin" / "python"; py.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable); py.chmod(0o755)
    sp = venv / "lib" / "python3.12" / "site-packages"; sp.mkdir(parents=True)
    (sp / "__editable___doramisourcearchive_3_0_0_finder.py").write_text("MAPPING = {}\n")
    (sp / "__editable__.doramisourcearchive-3.0.0.pth").write_text(f"{bm.clone}/src\n")
    (sp / "_virtualenv.pth").write_text("import _virtualenv\n")
    di = sp / "doramisourcearchive-3.0.0.dist-info"; di.mkdir()
    (di / "direct_url.json").write_text('{"url": "file:///x", "dir_info": {"editable": true}}')
    bm.html_dir.mkdir(parents=True)
    (bm.html_dir / "assets").mkdir()
    (bm.html_dir / "index.html").write_text('<!doctype html><html><head><link rel="stylesheet" href="/assets/index-old.css"></head>'
                                           '<body><script type="module" src="/assets/index-old.js"></script></body></html>\n')
    (bm.html_dir / "assets" / "index-old.js").write_text("console.log('old');\n")
    (bm.html_dir / "assets" / "index-old.css").write_text("body{}\n")
    (bm.etc / "conf.d" / "dorami.conf").write_text(f"server {{\n    listen 8080;\n    root {bm.html_dir};\n    location /api/ {{ proxy_pass http://127.0.0.1:8088; }}\n}}\n")
    (bm.clone / "data").mkdir(exist_ok=True)
    con = sqlite3.connect(bm.clone / "data" / "cms_data.db")
    con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, body VARCHAR)")
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    con.execute("INSERT INTO alembic_version VALUES ('0001')")
    con.execute("INSERT INTO articles (body) VALUES ('legacy-row')")
    con.commit(); con.close()
    if running:
        env = {"DORAMI_BUILD_REF": f"v{version}", "DORAMI_BUILD_SHA": sha, "DORAMI_CONFIG_FILE": str(bm.clone / "config" / "production.ini")}
        (bm.pm2dir / "state.json").write_text(json.dumps({APP: {"cwd": str(bm.clone), "pid": 4242, "status": "online", "env": env}}))
        bm.health.write_text(json.dumps({"status": "ok", "version": version, "build": {"ref": f"v{version}", "sha": sha, "source": "env"}}))


def test_first_run_of_new_script_adopts_old_form_then_deploys(bm: BM):
    sha = bm.head()
    _setup_old_form(bm, sha)
    old_index = (bm.html_dir / "index.html").read_text()
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "先收养旧形态安装" in r.stdout and "收养完成" in r.stdout and "Deploy complete" in r.stdout
    rels = bm.releases()
    legacy = [p for p in rels if p.name.startswith("legacy-")]
    assert len(legacy) == 1 and len(rels) == 2
    legacy = legacy[0]
    ls = bm.state("last-success.json")
    assert ls["kind"] == "deploy" and ls["prev"]["txn_id"] == legacy.name and ls["prev"]["release"] == str(legacy)
    assert ls["prev"]["kind"] == "adopt" and ls["capabilities"]["rollback"] is True
    lm = json.loads((legacy / "manifest.json").read_text())
    assert lm["kind"] == "adopt" and lm["prev"] is None and lm["target"]["code_sha"] == sha and lm["capabilities"]["reproducible"] is True
    assert lm["target"]["adopt_sha_source"] == "health"
    # venv 不移动、editable 痕迹移除、legacy 完成凭据
    app = legacy / "app"
    assert os.path.realpath(app / "venv") == os.path.realpath(bm.clone / "venv")
    sp = bm.clone / "venv" / "lib" / "python3.12" / "site-packages"
    assert not list(sp.glob("__editable__*")) and (sp / "_virtualenv.pth").exists() and not (sp / "doramisourcearchive-3.0.0.dist-info").exists()
    assert (bm.clone / "venv" / ".dorami-complete").read_text().strip() == "kind=legacy"
    assert (app / "src" / "version.py").is_file() and (app / "data").is_symlink()
    # dist 复制、旧目录挪走、nginx 快照
    assert (legacy / "dist" / "index.html").read_text() == old_index and (legacy / "dist.sha256").is_file()
    moved = Path(lm["target"]["html_dir_moved_to"])
    assert moved.is_dir() and (moved / "index.html").read_text() == old_index
    assert bm.html_dir.is_symlink() and os.path.realpath(bm.html_dir) == os.path.realpath(Path(ls["target"]["release"]) / "dist")
    assert (legacy / "nginx" / "snapshot.json").is_file() and json.loads((legacy / "nginx" / "changes.json").read_text()) == {"changes": []}
    # 一次收养重启 + 一次部署重启
    calls = [c.split()[0] for c in bm.pm2_calls() if c.split()[0] in ("delete", "start", "save")]
    assert calls == ["delete", "start", "save", "delete", "start", "save"]
    assert bm.db_rows() == 1 and bm.db_heads() == ["0001"]


def test_explicit_adopt_requires_sha_when_identity_unknown(bm: BM):
    sha = bm.head()
    _setup_old_form(bm, sha, running=False)
    r = bm.run("--adopt")
    assert r.returncode == 24 and "--adopt-sha" in r.stderr
    assert not bm.state("in-progress.json")
    r = bm.run("--adopt", "--adopt-sha", sha)
    assert r.returncode == 0, r.stdout + r.stderr
    ls = bm.state("last-success.json")
    assert ls["kind"] == "adopt" and ls["target"]["adopt_sha_source"] == "operator" and ls["capabilities"]["reproducible"] is False
    assert bm.health_json()["build"]["sha"] == sha
    r = bm.run("--adopt", "--adopt-sha", sha)
    assert r.returncode == 2 and "不需要收养" in r.stderr


def test_interrupted_adoption_is_resumed_not_archived(bm: BM):
    sha = bm.head()
    _setup_old_form(bm, sha)
    r = bm.run("--adopt", FAKE_PM2_SAVE_FAIL="1")
    assert r.returncode == 1 and "pm2 save 失败" in r.stderr
    ip = bm.state("in-progress.json")
    assert ip["kind"] == "adopt" and ip["stage"]["intent"] == "process_started" and ip["stage"]["completed"] == "links_switched"
    assert bm.html_dir.is_symlink(), "已挪走旧目录、建了链接"
    r = bm.run("--here")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "续做收养事务" in r.stdout and "收养完成" in r.stdout and "Deploy complete" in r.stdout
    assert not bm.closed(), "收养事务只续做不归档"
    ls = bm.state("last-success.json")
    assert ls["kind"] == "deploy" and ls["prev"]["kind"] == "adopt"
    # 续做没有重复复制 dist / 重新挪目录
    assert len([p for p in bm.www.iterdir() if p.name.startswith("site.adopt-")]) == 1


def test_status_before_adoption_points_to_adopt(bm: BM):
    _setup_old_form(bm, bm.head())
    r = bm.run("--status")
    assert r.returncode == 0 and "尚未收养" in r.stdout and "未发布" in r.stdout
