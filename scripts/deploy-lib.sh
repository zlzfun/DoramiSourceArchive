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
acquire_deploy_lock() {
    local lock_file="${DORAMI_DEPLOY_LOCK_FILE:-/run/lock/dorami-deploy.lock}"
    if [ -n "${DORAMI_DEPLOY_LOCK_FD:-}" ]; then
        [ -e "/dev/fd/${DORAMI_DEPLOY_LOCK_FD}" ] \
            || _deploy_lib_fail "DORAMI_DEPLOY_LOCK_FD=${DORAMI_DEPLOY_LOCK_FD} 不是打开的描述符(锁应由 worker 持有并继承)"
        return 0
    fi
    mkdir -p "$(dirname "$lock_file")" 2>/dev/null || true
    exec 9>>"$lock_file" || _deploy_lib_fail "打不开锁文件 $lock_file"
    _deploy_lib_flock 9 || _deploy_lib_fail "另一个部署正在进行(锁 $lock_file 被占);等它结束或检查 dorami-deploy-worker status"
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
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$db_path" ".backup '${backup_file}'"
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$db_path" "$backup_file" <<'PYEOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
    a.backup(b)
PYEOF
    else
        cp "$db_path" "$backup_file"
        [ -f "${db_path}-wal" ] && cp "${db_path}-wal" "${backup_file}-wal"
    fi
    echo "    DB backup: $backup_file"
    # 保留数可配(DORAMI_DEPLOY_BACKUP_KEEP,默认 10)。流水线来源下本函数不被调用:worker 做事务备份并按
    # manifest pin 清理,这里的纯计数清理不认识 manifest,若在流水线下跑会把仍被引用的原始备份删掉。
    local keep="${DORAMI_DEPLOY_BACKUP_KEEP:-10}"
    ls -1t backups/"$(basename "$db_path")".* 2>/dev/null | grep -v -- '-wal$' | tail -n +"$((keep + 1))" | xargs -r rm -f
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
