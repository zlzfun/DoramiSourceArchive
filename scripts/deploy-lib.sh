#!/bin/bash
# 两条官方部署路径(./deploy-docker.sh 与 ./deploy.sh)共用的库:「tag 即发布」的版本选择
# 与 SQLite 备份。本文件只被 source,不直接执行;调用方须已 `set -euo pipefail` 并 cd 到仓库根。
#
# ── 为什么部署要站到 tag 上(2026-09-14 拍板) ──
# 此前两条脚本都部署「当前工作树」,生产跑的是哪个版本只能靠人记;两人并行开发后
# main 上「已合入未发布」的提交会越攒越多,发不发全凭感觉。自此:
#   · annotated tag `v{__version__}` 是唯一的发布单元(见 docs/release-process.md);
#   · 部署脚本先把工作树站到某个 tag 上再做后面的事——生产上跑的永远是一个有名字的版本;
#   · 想部署未打 tag 的内容(开发机联调 / 应急热修)必须显式 `--here`,脚本会大声标注。
#
# 用法(由部署脚本调用,把自己的全部参数透传进来):
#   resolve_deploy_ref "$@"
# 参数:
#   (无)         拉取 tag,切到版本号最新的 v* tag——一键部署的默认语义
#   vX.Y.Z       切到指定 tag(不带 v 也接受)
#   --here       不切换,部署当前工作树(打印警告;DORAMI_DEPLOY_MODE=here)
#   -h|--help    打印用法
# 导出(供后续步骤把「构建来源」烤进镜像 / 传给 PM2 进程,运行时 /api/runtime 透出):
#   DORAMI_BUILD_REF    tag 名(tag 模式)或 `git describe --tags --always --dirty`(here 模式)
#   DORAMI_BUILD_SHA    HEAD 完整 sha
#   DORAMI_DEPLOY_MODE  tag | here(版本选择方式;与 DORAMI_DEPLOY_ORIGIN=pipeline|manual 的「来源」正交)
#
# 切换实现细节:bash 是边读边执行脚本文件的,checkout 换掉正在执行的 deploy 脚本本身会让
# 后半段读到另一个版本的字节——所以切换后立即 `exec` 重新执行「切换后的那份脚本」
# (带 --here 与 DORAMI_DEPLOY_REEXEC=1),由新进程从头跑完整流程;HEAD 已在目标 tag 上时
# 文件没变,无需重执行。

# ── 部署协议版本(issue #102 自动部署,docs/auto-deploy-plan.md §4.4 / §4.5)──
# 仓库外 worker(docker/dorami-deploy-worker.example)用 `git show <sha>:scripts/deploy-lib.sh | grep '^DORAMI_DEPLOY_PROTOCOL='`
# 读取目标 tag 的协议号,没有宣告或高于它支持的版本一律拒绝经流水线部署(那些 tag 走手工路径)。
# 协议 = worker ↔ 本脚本之间的契约:
#   环境变量  DORAMI_DEPLOY_ORIGIN=pipeline|manual  DORAMI_EXPECTED_SHA  DORAMI_DEPLOY_LOCK_FD  DORAMI_DEPLOY_SWITCH_MARK
#             DORAMI_DEPLOY_FRESH_OK  DORAMI_DEPLOY_TAG  DORAMI_DEPLOY_LOCK_FILE  DORAMI_DEPLOY_MIN_FREE_GB  DORAMI_DEPLOY_BACKUP_KEEP
#   输出行    `DORAMI_DEPLOY_META key=value`(值限 [A-Za-z0-9._:/=-])
#   步骤      流水线来源下本脚本不自做 DB 备份(worker 已做事务备份)、`up` 前 touch 切换标记、健康核对五项
# 改任一契约即 bump 本号,并同步 worker 的 WORKER_MAX_PROTOCOL。
DORAMI_DEPLOY_PROTOCOL=1

# ── 裸机事务能力宣告(issue #126,docs/baremetal-rollback-plan.md §4.2)──
# 裸机 `./deploy.sh <tag>` 在 checkout **之前** `git show <tag>:scripts/deploy-lib.sh | grep '^DORAMI_BAREMETAL_TXN='`:
# 目标 tag 的脚本没有这一行 = 它不认识 release 事务 / 回滚入口,一律拒绝以 tag 模式部署(exit 11),
# 改用 `./deploy.sh --code <tag>` 由当前编排器部署那份代码。与 DORAMI_DEPLOY_PROTOCOL 正交(那是 Docker worker 的契约)。
DORAMI_BAREMETAL_TXN=1

# 部署来源:pipeline(仓库外 worker 起的)| manual(人手工跑)。手工来源保留离线语义(fetch 失败可用本地 tag);
# 流水线来源 fail closed。由 DORAMI_DEPLOY_ORIGIN 显式决定,不由别的变量缺席隐式推断。
deploy_origin() { echo "${DORAMI_DEPLOY_ORIGIN:-manual}"; }

# 元数据行:worker / runner 按 allowlist 解析进 Job Summary;值字符集受限,别的字符替换成 _。
deploy_meta() { printf 'DORAMI_DEPLOY_META %s=%s\n' "$1" "$(printf '%s' "$2" | tr -c 'A-Za-z0-9._:/=-' '_')"; }

# flock 的可移植实现(macOS 没有 flock(1)):锁挂在 FD 的 open file description 上,python 子进程退出后
# 本 shell 仍持有该 FD,锁保持——与 flock(1) 的机制相同。
_deploy_lib_flock() { python3 -c 'import fcntl, sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)' "$1" 2>/dev/null; }

# 唯一部署锁:手工路径与流水线共用同一把(默认 /run/lock/dorami-deploy.lock,DORAMI_DEPLOY_LOCK_FILE 可覆盖)。
# 由 worker 起的子进程带 DORAMI_DEPLOY_LOCK_FD(FD 随 exec 重执行继承)→ 只校验描述符仍打开,不二次抢;
# 手工来源自己打开 FD 9 并 flock -n,抢不到即报「另一部署进行中」。
# 继承的锁 FD 不能只看「打开了」:它必须指向配置的锁文件(dev/inode 相同)且本进程经它持锁——同一 open file
# description 上再 flock 是无操作,别的 OFD 未持锁则会失败(codex 脚本层检视 P2-10)。
_deploy_lib_verify_lock_fd() {  # fd lock_file
    python3 - "$1" "$2" <<'PY'
import fcntl, os, sys
fd, lock = int(sys.argv[1]), sys.argv[2]
try:
    a, b = os.fstat(fd), os.stat(lock)
except OSError as exc:
    print(f"fd/lock stat failed: {exc}"); sys.exit(1)
if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
    print("fd does not refer to the configured lock file"); sys.exit(1)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("lock is not held on this fd"); sys.exit(1)
PY
}

acquire_deploy_lock() {
    local lock_file="${DORAMI_DEPLOY_LOCK_FILE:-/run/lock/dorami-deploy.lock}"
    if [ -n "${DORAMI_DEPLOY_LOCK_FD:-}" ]; then
        [[ "$DORAMI_DEPLOY_LOCK_FD" =~ ^[0-9]+$ ]] || _deploy_lib_fail "DORAMI_DEPLOY_LOCK_FD 须为数字: ${DORAMI_DEPLOY_LOCK_FD}"
        [ -e "/dev/fd/${DORAMI_DEPLOY_LOCK_FD}" ] \
            || _deploy_lib_fail "DORAMI_DEPLOY_LOCK_FD=${DORAMI_DEPLOY_LOCK_FD} 不是打开的描述符(锁应由 worker 持有并继承)"
        local why
        why="$(_deploy_lib_verify_lock_fd "$DORAMI_DEPLOY_LOCK_FD" "$lock_file")" \
            || _deploy_lib_fail "继承的锁 FD ${DORAMI_DEPLOY_LOCK_FD} 校验失败(${why});拒绝继续"
        return 0
    fi
    mkdir -p "$(dirname "$lock_file")" 2>/dev/null || true
    # 锁目录不存在 / 不可写即失败并要求显式配置,不静默回退到别的路径:两条部署路径同机时必须是同一把锁,
    # 各自回退会造出两把互不相知的锁(docs/baremetal-rollback-plan.md §4.2)。
    exec 9>>"$lock_file" \
        || _deploy_lib_fail "打不开锁文件 $lock_file(目录不存在或不可写);请显式设置 DORAMI_DEPLOY_LOCK_FILE=<可写路径>,两条部署路径须指向同一个文件"
    _deploy_lib_flock 9 || _deploy_lib_fail_rc "${DORAMI_DEPLOY_LOCK_BUSY_RC:-1}" \
        "另一个部署正在进行(锁 $lock_file 被占);等它结束或检查 dorami-deploy-worker status / ./deploy.sh --status"
    export DORAMI_DEPLOY_LOCK_FD=9
}

_deploy_lib_usage() {
    local self
    self="$(basename "${0:-deploy.sh}")"
    cat <<EOF
用法: ./${self} [vX.Y.Z | --here]

  (无参数)   拉取 tag,部署版本号最新的发布版(一键部署)
  vX.Y.Z     部署指定版本,例如 ./${self} v3.55.0
  --here     部署当前工作树(不切换 tag;开发机联调 / 应急热修用,输出会标注非发布版)

发布流程与回滚见 docs/release-process.md。
EOF
}

_deploy_lib_fail() { echo "ERROR: $*" >&2; exit 1; }
_deploy_lib_fail_rc() { local rc="$1"; shift; echo "ERROR: $*" >&2; exit "$rc"; }

# ── /api/health 五项核对(两条路径共用;docs/baremetal-rollback-plan.md §4.9 / §4.14)──
# deploy_health_verdict <version> <ref> <sha>:stdin 为响应体,输出 `ok` / `mismatch k=v,…` / `bad-json`。
deploy_health_verdict() {
    python3 -c '
import json, sys
want_version, want_ref, want_sha = sys.argv[1:4]
try:
    d = json.load(sys.stdin)
except Exception:
    print("bad-json"); sys.exit(0)
b = d.get("build") or {}
got = {"status": d.get("status"), "version": d.get("version"), "ref": b.get("ref"), "sha": b.get("sha"), "source": b.get("source")}
expect = {"status": "ok", "version": want_version, "ref": want_ref, "sha": want_sha, "source": "env"}
bad = [k for k in expect if got.get(k) != expect[k]]
print("ok" if not bad else "mismatch " + ",".join(f"{k}={got.get(k)}" for k in bad))
' "$1" "$2" "$3"
}

# deploy_wait_healthy <url> <version> <ref> <sha> <budget_seconds> <max_attempts> [curl 额外参数…]
# 在时间预算内轮询 <url>,五项全对即返回 0;否则返回 1。预算由调用方传入(Docker 路径 180 s / 90 次不变)。
# 每次 curl 都带连接 / 总超时:连接建立后后端不答也不能吃掉整个预算;次数只作额外上限。
# 结果变量:DEPLOY_HEALTH_ATTEMPT(已尝试次数)、DEPLOY_HEALTH_LAST_VERDICT(最后一次非 ok 的判定,无响应时为空)、
# DEPLOY_HEALTH_BODY(最后一次响应体)。
deploy_wait_healthy() {
    local url="$1" want_version="$2" want_ref="$3" want_sha="$4" budget="$5" attempts="$6"
    shift 6
    local deadline remaining max_time body verdict
    deadline=$(( $(date +%s) + budget ))
    DEPLOY_HEALTH_ATTEMPT=0; DEPLOY_HEALTH_LAST_VERDICT=""; DEPLOY_HEALTH_BODY=""
    while [ "$DEPLOY_HEALTH_ATTEMPT" -lt "$attempts" ]; do
        DEPLOY_HEALTH_ATTEMPT=$((DEPLOY_HEALTH_ATTEMPT + 1))
        remaining=$(( deadline - $(date +%s) ))
        [ "$remaining" -gt 0 ] || break
        max_time=$(( remaining < 15 ? remaining : 15 ))
        body="$(curl -fsS --connect-timeout 5 --max-time "$max_time" -H 'Cache-Control: no-cache' "$@" "${url}?_=$(date +%s)" 2>/dev/null || true)"
        if [ -n "$body" ]; then
            DEPLOY_HEALTH_BODY="$body"
            verdict="$(printf '%s' "$body" | deploy_health_verdict "$want_version" "$want_ref" "$want_sha")"
            [ "$verdict" = "ok" ] && return 0
            DEPLOY_HEALTH_LAST_VERDICT="$verdict"
        fi
        [ $(( deadline - $(date +%s) )) -gt 2 ] || break
        sleep 2
    done
    return 1
}

# ── JSON / 原子写助手(与 docker/dorami-deploy-worker.example 同法:同目录 tmp → fsync → rename → 父目录 fsync)──
deploy_json_get() {  # file key [default]  —— 点路径;bool 输出 true/false;dict/list 输出 JSON;缺则 default
    python3 - "$1" "$2" "${3-}" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cur = json.load(open(path, encoding="utf-8"))
except Exception:
    print(default); sys.exit(0)
for part in key.split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        print(default); sys.exit(0)
if cur is None:
    print(default)
elif isinstance(cur, bool):
    print("true" if cur else "false")
elif isinstance(cur, (dict, list)):
    print(json.dumps(cur, ensure_ascii=False, sort_keys=True))
else:
    print(cur)
PY
}
deploy_json_set() {  # file key value [json]  —— 第 4 参为 json 时 value 按 JSON 字面量解析;失败返回非零
    python3 - "$1" "$2" "$3" "${4-}" <<'PY'
import json, os, sys, tempfile
path, key, raw, kind = sys.argv[1:5]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    data = {}
value = json.loads(raw) if kind == "json" else raw
cur = data
parts = key.split(".")
for part in parts[:-1]:
    cur = cur.setdefault(part, {})
cur[parts[-1]] = value
d = os.path.dirname(path) or "."
os.makedirs(d, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
}
deploy_json_write() {  # file  ← stdin 为完整 JSON 文本;原子落盘(脚本用 -c 传入,stdin 留给数据)
    python3 -c '
import json, os, sys, tempfile
path = sys.argv[1]
data = json.load(sys.stdin)
d = os.path.dirname(path) or "."
os.makedirs(d, exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
' "$1"
}

# deploy_atomic_symlink <target> <link> [sudo 前缀…]:同目录临时 symlink + rename 原子替换(link 已是真实目录时拒绝),
# 父目录 fsync。sudo 前缀用于 nginx html_dir 这类 root 属主的位置。
deploy_atomic_symlink() {
    local target="$1" link="$2"; shift 2
    "$@" python3 - "$target" "$link" <<'PY'
import os, sys, tempfile
target, link = sys.argv[1], sys.argv[2]
d = os.path.dirname(link) or "."
if os.path.isdir(link) and not os.path.islink(link):
    print(f"{link} 是真实目录而不是 symlink,拒绝替换", file=sys.stderr); sys.exit(1)
os.makedirs(d, exist_ok=True)
tmp = tempfile.mktemp(dir=d, prefix=".tmp-link-")
os.symlink(target, tmp)
try:
    os.rename(tmp, link)
except Exception:
    os.unlink(tmp); raise
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
}

# sqlite_snapshot <src> <dst>:在线一致快照(优先 sqlite3 .backup,退回 python backup API)+ 快照文件 integrity_check;
# 任一步失败返回非零(快照文件留下供人看)。裸机事务快照与两条路径的部署前备份共用同一实现。
sqlite_snapshot() {
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")" || return 1
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$src" ".backup '${dst}'" || return 1
    else
        python3 - "$src" "$dst" <<'PYEOF' || return 1
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
    a.backup(b)
PYEOF
    fi
    [ -s "$dst" ] || { echo "快照为空: $dst" >&2; return 1; }
    python3 - "$dst" <<'PYEOF' || return 1
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
try:
    row = con.execute("PRAGMA integrity_check").fetchone()
finally:
    con.close()
if not row or row[0] != "ok":
    print(f"integrity_check 未通过: {row}", file=sys.stderr); sys.exit(1)
PYEOF
}

# 当前版本号(src/version.py 的 __version__),部署前用它与 tag 名核对。
_deploy_lib_source_version() {
    grep -o '__version__ = "[^"]*"' src/version.py | head -1 | sed 's/.*"\(.*\)"/\1/'
}

resolve_deploy_ref() {
    local want="" mode="tag"
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help) _deploy_lib_usage; exit 0 ;;
            --here) mode="here" ;;
            --*) _deploy_lib_usage >&2; _deploy_lib_fail "未知参数: $1" ;;
            *)
                [ -z "$want" ] || { _deploy_lib_usage >&2; _deploy_lib_fail "只能指定一个版本: $want 与 $1"; }
                want="$1"
                ;;
        esac
        shift
    done
    [ "$mode" = "here" ] && [ -n "$want" ] && _deploy_lib_fail "--here 与指定版本不能同时使用"

    git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || _deploy_lib_fail "当前目录不是 git 仓库,tag 即发布需要仓库元数据(请用 git clone 取代码)"

    local head_sha
    head_sha="$(git rev-parse HEAD)"

    if [ "$mode" = "here" ]; then
        if [ "${DORAMI_DEPLOY_REEXEC:-}" = "1" ] && [ "${DORAMI_DEPLOY_MODE:-}" = "tag" ]; then
            # 由 tag 模式切换后重执行:REF/SHA/MODE 已随环境继承,只需核对并播报。
            _deploy_lib_verify_tag "$DORAMI_BUILD_REF" "$DORAMI_BUILD_SHA"
            return 0
        fi
        export DORAMI_DEPLOY_MODE="here"
        export DORAMI_BUILD_SHA="$head_sha"
        export DORAMI_BUILD_REF="$(git describe --tags --always --dirty 2>/dev/null || echo "${head_sha:0:7}")"
        echo "⚠️  --here:部署当前工作树 ${DORAMI_BUILD_REF}(${head_sha:0:7}),不是发布版。"
        echo "    生产机请改用 ./$(basename "$0") [vX.Y.Z] 按 tag 部署。"
        return 0
    fi

    # 拉最新 tag。离线/受限网络拉不到时只警告——本地已有的 tag 照样能部署
    # (内网机常见:代码由人工搬入,tag 随 clone 一起带过来)。
    if ! git fetch --tags --quiet origin 2>/dev/null; then
        [ "$(deploy_origin)" != "pipeline" ] \
            || _deploy_lib_fail "git fetch --tags 失败:流水线来源不回落到本地 tag(fail closed)"
        echo "⚠️  git fetch --tags 失败(离线或无 origin),按本地已有 tag 选择。"
    fi

    local tag
    if [ -n "$want" ]; then
        case "$want" in v*) tag="$want" ;; *) tag="v$want" ;; esac
        git rev-parse -q --verify "refs/tags/${tag}^{commit}" >/dev/null \
            || _deploy_lib_fail "tag 不存在: ${tag}(可用版本: $(git tag --list 'v*' --sort=-v:refname | head -5 | tr '\n' ' '))"
    else
        tag="$(git tag --list 'v*' --sort=-v:refname | head -1)"
        [ -n "$tag" ] || _deploy_lib_fail "仓库里没有任何 v* tag——先按 docs/release-process.md 发一个版本,或用 --here 部署当前工作树"
    fi

    # 入库文件不允许有手改:生产机上改过 deploy.sh / nginx 模板之类再切 tag 会撞冲突,
    # 而且「生产上跑的是 tag」这句话就不再成立。未跟踪文件(production.ini/.env/logs)不算。
    local dirty
    dirty="$(git status --porcelain --untracked-files=no)"
    if [ -n "$dirty" ]; then
        echo "$dirty" >&2
        _deploy_lib_fail "工作树有对入库文件的修改,拒绝切到 ${tag}。收起改动(git stash)后重试,或用 --here 部署当前手改内容。"
    fi

    local tag_sha
    tag_sha="$(git rev-parse "refs/tags/${tag}^{commit}")"
    export DORAMI_DEPLOY_MODE="tag"
    export DORAMI_BUILD_REF="$tag"
    export DORAMI_BUILD_SHA="$tag_sha"

    # checkout 前钩子(裸机路径注入,Docker 路径不设):目标已确定、工作树尚未改动时做能力检查 / 未收口事务分派 /
    # 收养等跨版本的事——它们必须在换掉脚本自身之前完成(docs/baremetal-rollback-plan.md §3.3 / §4.2)。
    if [ -n "${DORAMI_DEPLOY_PRE_EXEC_CHECK:-}" ]; then
        declare -F "$DORAMI_DEPLOY_PRE_EXEC_CHECK" >/dev/null \
            || _deploy_lib_fail "DORAMI_DEPLOY_PRE_EXEC_CHECK=$DORAMI_DEPLOY_PRE_EXEC_CHECK 不是已定义的函数"
        "$DORAMI_DEPLOY_PRE_EXEC_CHECK" "$tag" "$tag_sha"
    fi

    if [ "$head_sha" != "$tag_sha" ]; then
        echo "切换到发布版 ${tag}(${tag_sha:0:7};当前 ${head_sha:0:7})..."
        git checkout --quiet --detach "refs/tags/${tag}"
        # 脚本文件可能已被换掉:以切换后的那份从头重跑(见文件头说明)。
        DORAMI_DEPLOY_REEXEC=1 exec "./$(basename "$0")" --here
    fi

    _deploy_lib_verify_tag "$tag" "$tag_sha"
}

# tag 名必须与代码里的版本号一致(tag 是用 scripts/release.sh 打的才会一致),核对后播报。
_deploy_lib_verify_tag() {
    local tag="$1" tag_sha="$2" src_version head_sha
    src_version="$(_deploy_lib_source_version)"
    [ "v${src_version}" = "$tag" ] \
        || _deploy_lib_fail "tag ${tag} 指向的代码版本号是 ${src_version},二者不一致——tag 不是用 scripts/release.sh 打的?"
    # 最终执行的目标与核验值闭环(issue #102):checkout + exec 重执行之后再核一次 HEAD;
    # 流水线来源还要等于 Actions / worker 核验过的 DORAMI_EXPECTED_SHA。
    head_sha="$(git rev-parse HEAD)"
    [ "$head_sha" = "$tag_sha" ] || _deploy_lib_fail "HEAD ${head_sha:0:7} 不等于 tag ${tag} 的提交 ${tag_sha:0:7}"
    if [ "$(deploy_origin)" = "pipeline" ]; then
        [ -n "${DORAMI_EXPECTED_SHA:-}" ] || _deploy_lib_fail "流水线来源缺 DORAMI_EXPECTED_SHA"
        [ "$head_sha" = "$DORAMI_EXPECTED_SHA" ] \
            || _deploy_lib_fail "HEAD ${head_sha:0:7} 与流水线核验的 ${DORAMI_EXPECTED_SHA:0:7} 不一致,拒绝继续"
    fi
    echo "部署版本: ${tag}(${tag_sha:0:7},打标 $(git tag -l --format='%(taggerdate:short)' "$tag" 2>/dev/null || echo '?'))"
}

# 部署前备份 SQLite 库文件到 backups/(保留最近 10 份)。迁移不一定可逆(已有不可逆的
# Podcast 迁移),回滚 = 切回上一 tag + 恢复这份备份,只回代码不够。
# 库可能正被旧进程写着(WAL 模式):优先 sqlite3 在线 .backup,退而求其次 python 的
# backup API,都没有才裸 cp(连同 -wal 一起,避免丢最近写入)。
backup_sqlite_db() {
    local db_path="$1"
    [ -f "$db_path" ] || return 0
    mkdir -p backups
    local backup_file
    backup_file="backups/$(basename "$db_path").$(date +%Y%m%d-%H%M%S)"
    if command -v sqlite3 >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
        sqlite_snapshot "$db_path" "$backup_file" || _deploy_lib_fail "DB 备份失败: $backup_file"
    else
        cp "$db_path" "$backup_file"
        [ -f "${db_path}-wal" ] && cp "${db_path}-wal" "${backup_file}-wal"
    fi
    echo "    DB backup: $backup_file"
    # 保留数可配(DORAMI_DEPLOY_BACKUP_KEEP,默认 10)。流水线来源下本函数不被调用(worker 做事务备份);
    # 手工来源下计数清理必须**排除 manifest 引用的备份**——流水线失败留下的 in-progress 事务备份是恢复材料,
    # 手工多跑几次不能把它删掉(codex 脚本层检视 P1-6)。
    local keep="${DORAMI_DEPLOY_BACKUP_KEEP:-10}" referenced n=0 f
    referenced="$(_deploy_lib_referenced_backups)"
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        if grep -qxF "$f" <<<"$referenced" || grep -qxF "$PWD/$f" <<<"$referenced"; then   # here-string:无管道无 SIGPIPE
            continue
        fi
        n=$((n + 1))
        [ "$n" -le "$keep" ] && continue
        rm -f "$f"
    done < <(ls -1t backups/"$(basename "$db_path")".* 2>/dev/null | grep -v -- '-wal$')
}

# 仓库外状态目录(/etc/dorami-deploy.conf 的 STATE_DIR);没有 conf 的开发机返回空串。
deploy_state_dir() {
    local conf="${DORAMI_DEPLOY_CONF:-/etc/dorami-deploy.conf}"
    [ -r "$conf" ] || return 0
    ( set +u; STATE_DIR=""; source "$conf" >/dev/null 2>&1; printf '%s' "${STATE_DIR:-}" )
}

# in-progress / last-success 引用的备份文件(绝对路径,一行一个)。分别读 Docker worker 的状态目录
# (/etc/dorami-deploy.conf 的 STATE_DIR,`prev.db_backup`)与裸机事务的状态目录(`deploy-state/`,`db.snapshot` /
# `db.rescue_snapshot`);缺哪个跳哪个,不早退(docs/baremetal-rollback-plan.md §4.14)。
_deploy_lib_referenced_backups() {
    local worker_dir baremetal_dir
    worker_dir="$(deploy_state_dir)"
    baremetal_dir="${DORAMI_DEPLOY_STATE_DIR:-$PWD/deploy-state}"
    python3 - "$worker_dir" "$baremetal_dir" <<'PY'
import glob, json, os, sys
worker_dir, baremetal_dir = sys.argv[1], sys.argv[2]
def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
if worker_dir:
    for name in ("in-progress.json", "last-success.json"):
        v = (load(os.path.join(worker_dir, name)).get("prev") or {}).get("db_backup")
        if v:
            print(v)
if baremetal_dir and os.path.isdir(baremetal_dir):
    files = [os.path.join(baremetal_dir, n) for n in ("in-progress.json", "last-success.json")]
    files += glob.glob(os.path.join(baremetal_dir, "closed", "*.json"))
    for path in files:
        db = load(path).get("db") or {}
        for key in ("snapshot", "rescue_snapshot"):
            if db.get(key):
                print(db[key])
PY
}

# 手工来源在切换(up)之前、同一锁内留下标记:仓库外 worker 据此知道 last-success 已被手工越过,
# 不再回放、基线改读容器(codex 脚本层检视 P1-5)。没有 conf 的开发机是空操作。
note_manual_switch() {  # ref sha
    local state_dir; state_dir="$(deploy_state_dir)"
    [ -n "$state_dir" ] && [ -d "$state_dir" ] || return 0
    python3 - "$state_dir/manual-switch.json" "$1" "$2" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" <<'PY' || _deploy_lib_fail "写 manual-switch.json 失败"
import json, os, sys, tempfile
path, ref, sha, at = sys.argv[1:5]
d = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump({"ref": ref, "sha": sha, "at": at}, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
    echo "    已记录手工切换(manual-switch.json):流水线的 last-success 自此失效,下次从容器读基线"
}

# 从 ini 读 [storage] database_url 里的 sqlite 文件路径;非 sqlite 或读不到时输出空串。
sqlite_path_from_ini() {
    local ini="$1"
    [ -f "$ini" ] || return 0
    local url
    url="$(awk -F '=' '
        /^[[:space:]]*[#;]/ { next }
        /^[[:space:]]*\[/ { in_section = ($0 ~ /^[[:space:]]*\[storage\][[:space:]]*$/); next }
        in_section && $1 ~ /^[[:space:]]*database_url[[:space:]]*$/ {
            sub(/^[^=]*=/, ""); sub(/[[:space:]]*[#;].*$/, ""); gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit
        }' "$ini")"
    case "$url" in
        sqlite:///*) echo "${url#sqlite:///}" ;;
        "") echo "data/cms_data.db" ;;
        *) echo "" ;;
    esac
}
