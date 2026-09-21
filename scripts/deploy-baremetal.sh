#!/bin/bash
# 裸机部署的事务 / release / 回滚库(issue #126,docs/baremetal-rollback-plan.md)。
# 只被 ./deploy.sh 与固化在 releases/<txn>/controller/ 里的 rollback.sh source,不直接执行。
# 前置:调用方已 `set -euo pipefail`、已 source scripts/deploy-lib.sh;deploy.sh 已 cd 到仓库根,
# controller 由 env.sh 给出 BM_REPO。裸机专属的参数、能力钩子与事务函数只在这里装配,Docker 路径不 source 本文件。
#
# 形状(§3.1「运行副本版本化」):每次部署生成不可变的 release(代码副本 + 私有 venv 指针 + dist + nginx 配置集合
# + 事务材料 + 固化的回滚执行体),PM2 从 release 的实路径启动;工作树只用于编排与取代码。
# 回滚 = 用目标 release 重起 PM2 + 切 html_dir symlink + 恢复 nginx 配置集合 + (视迁移计划)恢复 DB 快照——
# 不 checkout、不出网、不构建。
#
# 退出码(沿 Docker worker 编号,不重号;§4.13):
#   0 成功  1 步骤失败或健康门未通过  2 用法  4 锁被占  11 目标脚本无裸机事务能力  20 已改宿主的未收口事务阻断
#   23 首装门  24 身份证据冲突或收养未完成  30 无回滚点或材料缺失  32 需 --restore-db  33 路径探针不一致
BM_RC_STEP=1; BM_RC_USAGE=2; BM_RC_LOCK=4; BM_RC_NO_TXN_CAP=11; BM_RC_UNCLOSED=20; BM_RC_FRESH_GATE=23
BM_RC_IDENTITY=24; BM_RC_NO_TARGET=30; BM_RC_NEED_RESTORE_DB=32; BM_RC_PATH_PROBE=33
export BM_RC_STEP BM_RC_USAGE BM_RC_LOCK BM_RC_NO_TXN_CAP BM_RC_UNCLOSED BM_RC_FRESH_GATE BM_RC_IDENTITY \
    BM_RC_NO_TARGET BM_RC_NEED_RESTORE_DB BM_RC_PATH_PROBE

# 阶段序列(§4.3):每步先写 intent 再做、做完写 completed;信号 / 失败只补写 error;SIGKILL / 断电靠 intent ≠ completed 被发现。
BM_STAGES_DEPLOY="opened code_archived venv_ready dist_built nginx_prepared db_snapshotted db_migrated process_stopped links_switched process_started health_ok promoted"
BM_STAGES_ROLLBACK="opened nginx_reverted nginx_restored process_stopped db_rescued db_restored links_switched process_started health_ok promoted"
BM_STAGES_ADOPT="opened code_archived venv_ready dist_copied nginx_snapshotted process_stopped links_switched process_started health_ok promoted"
# deploy 事务的「首次宿主写入」intent:在此之前失败的事务可被证明未改现场(§4.3)
BM_FIRST_HOST_WRITE_DEPLOY="nginx_prepared"
BM_FIRST_HOST_WRITE_ADOPT="dist_copied"

BM_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BM_LAST_ERROR=""
BM_TXN_OPEN=""

# ── 路径(§3.2)──
bm_init_paths() {  # [repo]
    BM_REPO="$(cd "${1:-$PWD}" && pwd -P)"
    BM_STATE_DIR="${DORAMI_DEPLOY_STATE_DIR:-$BM_REPO/deploy-state}"
    BM_RELEASES_DIR="${DORAMI_DEPLOY_RELEASES_DIR:-$BM_REPO/releases}"
    BM_VENVS_DIR="${DORAMI_DEPLOY_VENVS_DIR:-$BM_REPO/venvs}"
    BM_SNAPSHOT_DIR="${DORAMI_DEPLOY_SNAPSHOT_DIR:-$BM_REPO/backups/baremetal}"
    BM_CURRENT_LINK="$BM_REPO/current"
    BM_IN_PROGRESS="$BM_STATE_DIR/in-progress.json"
    BM_LAST_SUCCESS="$BM_STATE_DIR/last-success.json"
    BM_CLOSED_DIR="$BM_STATE_DIR/closed"
    BM_ENTRY="$BM_STATE_DIR/rollback"
    BM_APP_NAME="${PM2_APP_NAME:-dorami-backend-v2}"
    export DORAMI_DEPLOY_STATE_DIR="$BM_STATE_DIR"
}

bm_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
bm_realpath() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
bm_link_target() {  # 存在的 symlink → 其 realpath;真实目录 → "dir:<realpath>";不存在 → 空
    python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]
if os.path.islink(p):
    print(os.path.realpath(p))
elif os.path.isdir(p):
    print("dir:" + os.path.realpath(p))
PY
}
bm_fail() {  # rc msg…
    local rc="$1"; shift
    BM_LAST_ERROR="$*"
    echo "ERROR: $*" >&2
    exit "$rc"
}
bm_json_str() { python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"; }
bm_manifest_get() { deploy_json_get "$BM_IN_PROGRESS" "$1" "${2-}"; }

# ── 阶段(§4.3)──
bm_stage_index() {  # seq name → 下标(不在序列里 → -1)
    local i=0 s
    for s in $1; do
        [ "$s" = "$2" ] && { echo "$i"; return 0; }
        i=$((i + 1))
    done
    echo -1
}
bm_stage_intent() { deploy_json_set "$BM_IN_PROGRESS" stage.intent "$1" || bm_fail "$BM_RC_STEP" "写阶段 intent=$1 失败"; }
bm_stage_done() { deploy_json_set "$BM_IN_PROGRESS" stage.completed "$1" || bm_fail "$BM_RC_STEP" "写阶段 completed=$1 失败"; }
bm_stage_error() { deploy_json_set "$BM_IN_PROGRESS" stage.error "$1" >/dev/null 2>&1 || true; }
# bm_stage_needed <seq> <name>:该阶段尚未 completed(须做 / 续做)则返回 0;已完成返回 1(重入时跳过)
bm_stage_needed() {
    local completed idx_done idx_want
    completed="$(bm_manifest_get stage.completed "")"
    idx_done="$(bm_stage_index "$1" "$completed")"
    idx_want="$(bm_stage_index "$1" "$2")"
    [ "$idx_want" -gt "$idx_done" ]
}
# 失败 / 信号时把错误补进 manifest(不改 completed / intent,恢复靠它们)
bm_on_exit() {
    local rc=$?
    [ -n "$BM_TXN_OPEN" ] && [ -f "$BM_IN_PROGRESS" ] || return 0
    if [ "$rc" -ne 0 ]; then
        bm_stage_error "${BM_LAST_ERROR:-exit rc=$rc}(intent=$(bm_manifest_get stage.intent ""), $(bm_now))"
    fi
}
bm_on_signal() {
    BM_LAST_ERROR="收到 SIG$1"
    exit 129
}
bm_install_traps() {
    trap bm_on_exit EXIT
    trap 'bm_on_signal TERM' TERM
    trap 'bm_on_signal INT' INT
    trap 'bm_on_signal HUP' HUP
}

# ── 现场采样(§4.1,只读)──
# 导出:BM_HEALTH_JSON / BM_HEALTH_SHA / BM_HEALTH_REF / BM_HEALTH_VERSION;BM_PM2_PRESENT(1/0)/ BM_PM2_STATUS /
# BM_PM2_CWD / BM_PM2_SHA / BM_PM2_REF / BM_PM2_PID;BM_CURRENT_TARGET(current → realpath)/ BM_HTML_TARGET
# (html_dir 是 symlink → realpath;真实目录 → dir:<realpath>;不存在 → 空);BM_RUN_SHA / BM_RUN_REF / BM_RUN_SRC(health|pm2|none)。
bm_sample_running() {
    local host="${BACKEND_PROXY_HOST:-127.0.0.1}" port="${BACKEND_PROXY_PORT:-8088}" health jlist
    health="$(curl -fsS --connect-timeout 3 --max-time 8 -H 'Cache-Control: no-cache' "http://${host}:${port}/api/health?_=$(date +%s)" 2>/dev/null || true)"
    jlist="$(pm2 jlist 2>/dev/null || true)"
    eval "$(python3 - "$BM_APP_NAME" "$health" "$jlist" <<'PY'
import json, shlex, sys
app, health, jlist = sys.argv[1], sys.argv[2], sys.argv[3]
out = {"BM_HEALTH_JSON": "", "BM_HEALTH_SHA": "", "BM_HEALTH_REF": "", "BM_HEALTH_VERSION": "",
       "BM_PM2_PRESENT": "0", "BM_PM2_STATUS": "", "BM_PM2_CWD": "", "BM_PM2_SHA": "", "BM_PM2_REF": "", "BM_PM2_PID": ""}
try:
    h = json.loads(health)
    b = h.get("build") or {}
    out["BM_HEALTH_JSON"] = json.dumps(h, ensure_ascii=False, sort_keys=True)
    out["BM_HEALTH_SHA"] = str(b.get("sha") or "")
    out["BM_HEALTH_REF"] = str(b.get("ref") or "")
    out["BM_HEALTH_VERSION"] = str(h.get("version") or "")
except Exception:
    pass
try:
    # pm2 jlist 偶尔在 JSON 前打印一行升级提示:从第一个 '[' 起解析
    start = jlist.find("[")
    procs = json.loads(jlist[start:]) if start >= 0 else []
except Exception:
    procs = []
for p in procs:
    if p.get("name") != app:
        continue
    env = p.get("pm2_env") or {}
    inner = env.get("env") or {}
    out["BM_PM2_PRESENT"] = "1"
    out["BM_PM2_STATUS"] = str(env.get("status") or "")
    out["BM_PM2_CWD"] = str(env.get("pm_cwd") or "")
    out["BM_PM2_SHA"] = str(env.get("DORAMI_BUILD_SHA") or inner.get("DORAMI_BUILD_SHA") or "")
    out["BM_PM2_REF"] = str(env.get("DORAMI_BUILD_REF") or inner.get("DORAMI_BUILD_REF") or "")
    out["BM_PM2_PID"] = str(p.get("pid") or "")
    break
for k, v in out.items():
    print(f"{k}={shlex.quote(v)}")
PY
)"
    BM_CURRENT_TARGET="$(bm_link_target "$BM_CURRENT_LINK")"
    BM_HTML_TARGET=""
    [ -n "${NGINX_HTML_DIR:-}" ] && BM_HTML_TARGET="$(bm_link_target "$NGINX_HTML_DIR")"
    if [ -n "$BM_HEALTH_SHA" ]; then
        BM_RUN_SHA="$BM_HEALTH_SHA"; BM_RUN_REF="$BM_HEALTH_REF"; BM_RUN_SRC="health"
    elif [ -n "$BM_PM2_SHA" ]; then
        BM_RUN_SHA="$BM_PM2_SHA"; BM_RUN_REF="$BM_PM2_REF"; BM_RUN_SRC="pm2"
    else
        BM_RUN_SHA=""; BM_RUN_REF=""; BM_RUN_SRC="none"
    fi
    export BM_HEALTH_JSON BM_HEALTH_SHA BM_HEALTH_REF BM_HEALTH_VERSION BM_PM2_PRESENT BM_PM2_STATUS BM_PM2_CWD \
        BM_PM2_SHA BM_PM2_REF BM_PM2_PID BM_CURRENT_TARGET BM_HTML_TARGET BM_RUN_SHA BM_RUN_REF BM_RUN_SRC
}

# 既有部署证据(§4.1 首装门):输出空格分隔的证据名;空 = 真首装候选
bm_evidence() {
    local ev="" db="${BM_DB_PATH:-}"
    [ -f "$BM_LAST_SUCCESS" ] && ev="$ev last-success"
    [ -f "$BM_IN_PROGRESS" ] && ev="$ev in-progress"
    [ "${BM_PM2_PRESENT:-0}" = 1 ] && ev="$ev pm2-app"
    if [ -n "${NGINX_HTML_DIR:-}" ] && [ -e "$NGINX_HTML_DIR" ] && [ -n "$(ls -A "$NGINX_HTML_DIR" 2>/dev/null || true)" ]; then
        ev="$ev html_dir"
    fi
    [ -d "$BM_REPO/${VENV_DIR:-venv}" ] && ev="$ev venv"
    [ -n "$db" ] && [ -f "$db" ] && ev="$ev database"
    [ -d "$BM_REPO/data/media" ] && ev="$ev media"
    [ -d "$BM_REPO/data/podcast-artifacts" ] && ev="$ev podcast-artifacts"
    [ -n "$(ls -A "$BM_REPO/backups" 2>/dev/null || true)" ] && ev="$ev backups"
    [ -n "$(ls -A "$BM_RELEASES_DIR" 2>/dev/null || true)" ] && ev="$ev releases"
    echo "${ev# }"
}

# 首装门(§4.1):迁移计划报 fresh 时——有既有部署证据一律拒绝(数据目录配错,不提供覆盖);无证据的真首装需
# DORAMI_DEPLOY_FRESH_OK=1 显式授权。
bm_fresh_gate() {  # plan_status
    [ "$1" = "fresh" ] || return 0
    local ev; ev="$(bm_evidence)"
    if [ -n "$ev" ]; then
        bm_fail "$BM_RC_FRESH_GATE" "迁移计划报 fresh(库不存在 / 无业务表)但本机有既有部署证据(${ev}):这是数据目录 / 库路径配错,绝不起空站;核对 [storage] database_url 与 data/ 后重试(不提供覆盖开关)"
    fi
    if [ "${DORAMI_DEPLOY_FRESH_OK:-0}" != 1 ]; then
        bm_fail "$BM_RC_FRESH_GATE" "迁移计划报 fresh 且本机无任何部署证据:真正首装请 DORAMI_DEPLOY_FRESH_OK=1 ./deploy.sh … 显式授权(首装没有回滚点)"
    fi
    echo "    首装门:无部署证据 + DORAMI_DEPLOY_FRESH_OK=1,放行(本次部署没有回滚点)"
    BM_CAP_ROLLBACK=false
}

# ── prev 采样(§4.1)──
# 输出 BM_PREV_JSON(JSON 对象或 null)与 BM_CAP_ROLLBACK(true/false)。前置:bm_sample_running 已跑;收养已完成
# (无 last-success 且有证据的情况由 bm_pre_deploy_checks 先转收养,不会走到这里)。
bm_determine_prev() {
    BM_PREV_JSON="null"; BM_CAP_ROLLBACK=true
    if [ ! -f "$BM_LAST_SUCCESS" ]; then
        # 真首装(证据为空时才可能到这里;fresh 门在目标上下文迁移计划出来后再判)
        BM_CAP_ROLLBACK=false
        echo "    prev:无 last-success(首装候选),本次部署没有回滚点"
        return 0
    fi
    local ls_release ls_sha ls_ref ls_app cur_ok=1 run_ok=1 why=""
    ls_release="$(deploy_json_get "$BM_LAST_SUCCESS" target.release "")"
    ls_sha="$(deploy_json_get "$BM_LAST_SUCCESS" target.code_sha "")"
    ls_ref="$(deploy_json_get "$BM_LAST_SUCCESS" target.ref "")"
    ls_app="$(bm_realpath "$ls_release/app")"
    [ -d "$ls_release" ] || why="last-success 的 release 目录不存在($ls_release)"
    if [ -z "$why" ] && [ "$BM_CURRENT_TARGET" != "$ls_app" ]; then
        cur_ok=0; why="current 指向 ${BM_CURRENT_TARGET:-<无>},不是 last-success 的 $ls_app"
    fi
    if [ -z "$why" ]; then
        if [ -n "$BM_RUN_SHA" ]; then
            [ "$BM_RUN_SHA" = "$ls_sha" ] || { run_ok=0; why="运行中的构建 sha ${BM_RUN_SHA:0:7}(来源 $BM_RUN_SRC)≠ last-success ${ls_sha:0:7}"; }
        elif [ "${BM_PM2_PRESENT:-0}" = 1 ]; then
            [ "$(bm_realpath "$BM_PM2_CWD")" = "$ls_app" ] || { run_ok=0; why="pm2 进程 cwd $BM_PM2_CWD 不是 last-success 的 $ls_app 且无构建 sha 可核"; }
        else
            echo "    ⚠️  后端未运行(无 /api/health、无 pm2 进程):按 last-success 记录采样 prev"
        fi
    fi
    if [ -n "$why" ]; then
        if [ "${BM_NO_ROLLBACK_GUARANTEE:-0}" = 1 ]; then
            echo "    ⚠️  身份证据冲突($why);--no-rollback-guarantee 显式继续:prev=null,本次部署没有回滚点"
            BM_CAP_ROLLBACK=false
            return 0
        fi
        bm_fail "$BM_RC_IDENTITY" "既有部署的身份证据冲突:$why。默认停止(last-success 与材料保留);人工核对 ./deploy.sh --status 后,确认放弃回滚保证可加 --no-rollback-guarantee 继续"
    fi
    BM_PREV_JSON="$(python3 - "$BM_LAST_SUCCESS" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
t = m.get("target") or {}
print(json.dumps({"txn_id": m.get("txn_id"), "kind": m.get("kind"), "ref": t.get("ref"), "code_sha": t.get("code_sha"),
                  "release": t.get("release"), "venv": t.get("venv"), "dist": t.get("dist")}, ensure_ascii=False))
PY
)"
    echo "    prev:${ls_ref}(${ls_sha:0:7},txn $(deploy_json_get "$BM_LAST_SUCCESS" txn_id ?))——current / 运行身份一致"
}

# ── 事务(§4.3)──
bm_txn_id() {  # code_sha7
    echo "$(date -u +%Y%m%dT%H%M%SZ)-$1-$(python3 -c 'import secrets; print(secrets.token_hex(2))')"
}

# 固化执行体:把当前 deploy-lib.sh / deploy-baremetal.sh 与入口 rollback.sh、环境 env.sh 复制进 <release>/controller/
bm_controller_install() {  # release_dir
    local ctl="$1/controller"
    mkdir -p "$ctl"
    cp "$BM_LIB_DIR/deploy-lib.sh" "$ctl/deploy-lib.sh"
    cp "$BM_LIB_DIR/deploy-baremetal.sh" "$ctl/deploy-baremetal.sh"
    cat >"$ctl/rollback.sh" <<'EOF'
#!/bin/bash
# 固化的回滚执行体(docs/baremetal-rollback-plan.md §4.3):开事务时的 deploy-lib.sh / deploy-baremetal.sh 副本 + 本入口,
# 永远跑树外副本——旧版 deploy.sh 不认识参数也无妨,bash 边读边执行的坑也由此消失。由 deploy-state/rollback 分派进来。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
# shellcheck disable=SC1091
source "$HERE/deploy-lib.sh"
# shellcheck disable=SC1091
source "$HERE/deploy-baremetal.sh"
bm_controller_main "$@"
EOF
    chmod 755 "$ctl/rollback.sh"
    {
        echo "# 开事务时的环境(自动生成):controller 运行时据此定位仓库 / 状态目录 / 锁,不读工作树"
        printf 'export BM_REPO=%q\n' "$BM_REPO"
        printf 'export DORAMI_DEPLOY_STATE_DIR=%q\n' "$BM_STATE_DIR"
        printf 'export DORAMI_DEPLOY_RELEASES_DIR=%q\n' "$BM_RELEASES_DIR"
        printf 'export DORAMI_DEPLOY_VENVS_DIR=%q\n' "$BM_VENVS_DIR"
        printf 'export DORAMI_DEPLOY_SNAPSHOT_DIR=%q\n' "$BM_SNAPSHOT_DIR"
        printf 'export PM2_APP_NAME=%q\n' "$BM_APP_NAME"
        [ -n "${DORAMI_DEPLOY_LOCK_FILE:-}" ] && printf 'export DORAMI_DEPLOY_LOCK_FILE=%q\n' "$DORAMI_DEPLOY_LOCK_FILE"
        [ -n "${DORAMI_NGINX_ETC_DIR:-}" ] && printf 'export DORAMI_NGINX_ETC_DIR=%q\n' "$DORAMI_NGINX_ETC_DIR"
        [ -n "${NGINX_BIN:-}" ] && printf 'export NGINX_BIN=%q\n' "$NGINX_BIN"
        printf 'export PATH=%q\n' "$PATH"
        true
    } >"$ctl/env.sh"
}

# 稳定恢复入口 deploy-state/rollback(§4.2):固定的分派脚本,内嵌绝对路径;有 in-progress → 该事务自己的 controller,
# 否则 last-success 的 controller;都无 → 报告并拒绝。开事务时(controller 落盘之后、首次宿主写入之前)发布;幂等。
bm_publish_entry() {
    mkdir -p "$BM_STATE_DIR"
    local tmp="$BM_STATE_DIR/.tmp-rollback-$$"
    cat >"$tmp" <<EOF
#!/bin/bash
# dorami 裸机部署的稳定恢复入口(自动生成,勿手改;docs/baremetal-rollback-plan.md §4.2)。
# ./deploy.sh --rollback 只是转发到这里;分派规则:有未收口事务 → 进入该事务自己的固化执行体(adopt / rollback 续做,
# deploy 则回滚);否则进入 last-success 的执行体;两者都没有(真首装 / 收养未开始)→ 拒绝。
set -euo pipefail
STATE_DIR=$(printf '%q' "$BM_STATE_DIR")
ctl="\$(python3 - "\$STATE_DIR" <<'PY'
import json, os, sys
d = sys.argv[1]
for name in ("in-progress.json", "last-success.json"):
    try:
        m = json.load(open(os.path.join(d, name), encoding="utf-8"))
    except Exception:
        continue
    c = m.get("controller")
    if c and os.path.isfile(os.path.join(c, "rollback.sh")):
        print(c); break
PY
)"
if [ -z "\$ctl" ]; then
    echo "ERROR: 本机没有可用的回滚执行体(无未收口事务、无 last-success,或其 controller 已不存在):真首装或收养未开始。" >&2
    echo "       先 ./deploy.sh --status 查看;旧形态安装请 ./deploy.sh --adopt 收养。" >&2
    exit 30
fi
exec bash "\$ctl/rollback.sh" "\$@"
EOF
    chmod 755 "$tmp"
    if [ -f "$BM_ENTRY" ] && cmp -s "$tmp" "$BM_ENTRY"; then
        rm -f "$tmp"
    else
        mv -f "$tmp" "$BM_ENTRY"
    fi
}

# 开事务:BM_TXN_KIND / BM_TXN_MODE / BM_TXN_TARGET_JSON / BM_TXN_PREV_JSON / BM_TXN_CAPS_JSON / BM_TXN_SITE_JSON /
# BM_TXN_RECOVER_FROM / BM_TXN_DB_JSON 由调用方设好;deploy / adopt 事务先 mkdir releases/<txn>(排他),rollback 事务无新 release。
# 落盘顺序:release 目录 → controller → in-progress(stage=opened)→ 分派入口。此后任何宿主改动都有发现入口。
bm_txn_open() {  # txn_id [release_dir]
    local txn="$1" release="${2-}" controller
    if [ -n "$release" ]; then
        mkdir -p "$BM_RELEASES_DIR"
        mkdir "$release" 2>/dev/null || bm_fail "$BM_RC_STEP" "release 目录已存在,拒绝复用: $release"
        bm_controller_install "$release"
        controller="$release/controller"
    else
        controller="${BM_CONTROLLER_DIR:-}"
        [ -n "$controller" ] || bm_fail "$BM_RC_STEP" "rollback 事务需要 BM_CONTROLLER_DIR(当前执行体所在目录)"
    fi
    mkdir -p "$BM_STATE_DIR" "$BM_CLOSED_DIR"
    [ -f "$BM_IN_PROGRESS" ] && bm_fail "$BM_RC_UNCLOSED" "开事务时发现 in-progress 仍在($(bm_manifest_get txn_id ?)),拒绝覆盖"
    python3 - "$txn" "$BM_TXN_KIND" "$BM_TXN_MODE" "${BM_ORCHESTRATOR_SHA:-}" "$controller" \
        "${BM_TXN_TARGET_JSON:-null}" "${BM_TXN_PREV_JSON:-null}" "${BM_TXN_CAPS_JSON:-{\}}" "${BM_TXN_SITE_JSON:-{\}}" \
        "${BM_TXN_RECOVER_FROM:-}" "${BM_TXN_DB_JSON:-{\}}" "$(bm_now)" <<'PY' | deploy_json_write "$BM_IN_PROGRESS" \
        || bm_fail "$BM_RC_STEP" "写 in-progress.json 失败(磁盘 / 权限?),事务未落盘"
import json, sys
(txn, kind, mode, orch, controller, target, prev, caps, site, recover_from, db, opened) = sys.argv[1:13]
caps_d = {"rollback": True, "db_restore": True, "reproducible": True}
caps_d.update(json.loads(caps or "{}"))
print(json.dumps({
    "txn_id": txn, "kind": kind, "mode": mode, "orchestrator_sha": orch, "controller": controller,
    "target": json.loads(target or "null"), "prev": json.loads(prev or "null"),
    "db": json.loads(db or "{}"), "site": json.loads(site or "{}"), "capabilities": caps_d,
    "stage": {"completed": "opened", "intent": "opened", "error": None},
    "recover_from": recover_from or None, "opened_at": opened, "deployed_at": None,
}, ensure_ascii=False))
PY
    BM_TXN_OPEN=1
    bm_publish_entry
    echo "    事务 $txn(kind=$BM_TXN_KIND)已落盘:$BM_IN_PROGRESS;恢复入口 $BM_ENTRY"
}

# 晋升(§4.9 ⑤):in-progress + deployed_at → last-success;manifest 副本进 release;先核对入口再删 in-progress
#(覆盖「last-success 已写、入口尚未更新」的崩溃窗口)。晋升失败保留全部材料。
bm_txn_promote() {
    local release txn
    txn="$(bm_manifest_get txn_id ?)"
    release="$(bm_manifest_get target.release "")"
    deploy_json_set "$BM_IN_PROGRESS" deployed_at "$(bm_now)" || bm_fail "$BM_RC_STEP" "写 deployed_at 失败,事务保留"
    bm_stage_done promoted
    if [ -n "$release" ] && [ -d "$release" ]; then
        cp "$BM_IN_PROGRESS" "$release/manifest.json.tmp" && mv -f "$release/manifest.json.tmp" "$release/manifest.json" \
            || bm_fail "$BM_RC_STEP" "写 $release/manifest.json 失败,事务保留"
    fi
    python3 - "$BM_IN_PROGRESS" "$BM_LAST_SUCCESS" <<'PY' || bm_fail "$BM_RC_STEP" "写 last-success.json 失败(磁盘 / 权限?):in-progress 事务与全部材料保留,不清理"
import json, os, shutil, sys, tempfile
src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
d = os.path.dirname(dst) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
os.replace(tmp, dst)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
    bm_publish_entry
    rm -f "$BM_IN_PROGRESS" || echo "    ⚠️  删除 in-progress 失败(下次按 txn_id 幂等收口)"
    BM_TXN_OPEN=""
    echo "    last-success = $txn"
}

# 归档未收口事务到 closed/(材料不删)
bm_txn_archive() {  # reason
    local txn; txn="$(bm_manifest_get txn_id unknown)"
    mkdir -p "$BM_CLOSED_DIR"
    deploy_json_set "$BM_IN_PROGRESS" closed "{\"at\": \"$(bm_now)\", \"reason\": $(bm_json_str "$1")}" json || true
    mv -f "$BM_IN_PROGRESS" "$BM_CLOSED_DIR/${txn}.json" || bm_fail "$BM_RC_STEP" "归档事务 $txn 失败"
    echo "    事务 $txn 已归档 → $BM_CLOSED_DIR/${txn}.json(材料保留)"
}

# 「last-success 已写、in-progress 未删」的崩溃窗口:先核对 / 修复入口,再幂等删除
bm_reconcile_crash_window() {
    [ -f "$BM_IN_PROGRESS" ] && [ -f "$BM_LAST_SUCCESS" ] || return 0
    [ "$(bm_manifest_get txn_id a)" = "$(deploy_json_get "$BM_LAST_SUCCESS" txn_id b)" ] || return 0
    echo "    上次已晋升但事务未删(崩溃窗口):修复入口后幂等收口 $(bm_manifest_get txn_id)"
    bm_publish_entry
    rm -f "$BM_IN_PROGRESS" || bm_fail "$BM_RC_STEP" "删除已晋升的 in-progress 失败"
}

# deploy 事务能否证明「未改宿主」(§4.3):completed 与 intent 都早于首次宿主写入 intent,且现场等于 prev 记录
bm_txn_host_untouched() {
    local kind seq first idx_done idx_intent completed intent release changes prev_app prev_dist
    kind="$(bm_manifest_get kind "")"
    case "$kind" in
        deploy) seq="$BM_STAGES_DEPLOY"; first="$BM_FIRST_HOST_WRITE_DEPLOY" ;;
        *) return 1 ;;
    esac
    completed="$(bm_manifest_get stage.completed "")"; intent="$(bm_manifest_get stage.intent "")"
    idx_done="$(bm_stage_index "$seq" "$completed")"; idx_intent="$(bm_stage_index "$seq" "$intent")"
    local idx_first; idx_first="$(bm_stage_index "$seq" "$first")"
    [ "$idx_done" -lt "$idx_first" ] && [ "$idx_intent" -lt "$idx_first" ] || return 1
    release="$(bm_manifest_get target.release "")"
    changes="$release/nginx/changes.json"
    if [ -f "$changes" ] && [ "$(deploy_json_get "$changes" changes "[]")" != "[]" ]; then
        return 1
    fi
    # 现场证明:current / html_dir / pm2 cwd 等于 prev 记录(prev=null 时须都为空)
    prev_app="$(bm_manifest_get prev.release "")"; prev_dist="$(bm_manifest_get prev.dist "")"
    [ -n "$prev_app" ] && prev_app="$(bm_realpath "$prev_app/app")"
    [ -n "$prev_dist" ] && prev_dist="$(bm_realpath "$prev_dist")"
    [ "${BM_CURRENT_TARGET:-}" = "$prev_app" ] || return 1
    if [ -n "${NGINX_HTML_DIR:-}" ]; then
        case "${BM_HTML_TARGET:-}" in
            "$prev_dist"|"") ;;
            dir:*) [ -z "$prev_dist" ] || return 1 ;;
            *) return 1 ;;
        esac
    fi
    if [ "${BM_PM2_PRESENT:-0}" = 1 ] && [ -n "$prev_app" ]; then
        [ "$(bm_realpath "$BM_PM2_CWD")" = "$prev_app" ] || return 1
    fi
    return 0
}

# 未收口事务纪律(§4.3):按 kind 分派——adopt / rollback 一律续做(不归档);deploy 只在可证明未改宿主时自动归档,
# 否则拒绝(exit 20)并提示 --rollback / --discard-txn。
bm_dispatch_unclosed() {
    bm_reconcile_crash_window
    [ -f "$BM_IN_PROGRESS" ] || return 0
    local kind txn
    kind="$(bm_manifest_get kind "")"; txn="$(bm_manifest_get txn_id ?)"
    case "$kind" in
        adopt)
            echo "    发现未完成的收养事务 $txn(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)):先续做"
            bm_adopt_resume ;;
        rollback)
            bm_fail "$BM_RC_UNCLOSED" "存在未完成的回滚事务 $txn(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)):先 ./deploy.sh --rollback 续做同一目标,或 ./deploy.sh --status 查看" ;;
        deploy)
            if bm_txn_host_untouched; then
                echo "    上次部署 $txn 在改动宿主之前就失败(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)),现场等于 prev:自动归档"
                bm_txn_archive "auto-closed: host untouched"
            else
                bm_fail "$BM_RC_UNCLOSED" "存在已改动宿主的未收口部署事务 $txn(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?) error=$(bm_manifest_get stage.error 无)):先 ./deploy.sh --rollback 回到上一版,或人工处理后 ./deploy.sh --discard-txn 归档"
            fi ;;
        *)
            bm_fail "$BM_RC_UNCLOSED" "in-progress.json 的 kind 不可识别(${kind:-空}),拒绝继续;人工检查 $BM_IN_PROGRESS" ;;
    esac
}

# --discard-txn(§4.13):人已手工处理,归档未收口事务;已持久化首次宿主写入 intent 或现场无法证明未写入时要求 --yes
bm_discard_txn() {
    bm_reconcile_crash_window
    [ -f "$BM_IN_PROGRESS" ] || { echo "没有未收口的事务"; return 0; }
    local txn; txn="$(bm_manifest_get txn_id ?)"
    if ! bm_txn_host_untouched && [ "${BM_YES:-0}" != 1 ]; then
        bm_fail "$BM_RC_USAGE" "事务 $txn(kind=$(bm_manifest_get kind ?))已改动宿主或无法证明未改动:确认已人工恢复现场后加 --yes 再归档,或先 ./deploy.sh --rollback"
    fi
    bm_txn_archive "discarded by operator"
}

# ── 能力检查与 checkout 前检查(§3.3 / §4.2)──
# 目标 tag 的 scripts/deploy-lib.sh 必须宣告 DORAMI_BAREMETAL_TXN,否则以 tag 模式切换会换掉本脚本并失去回滚入口
bm_pre_exec_check() {  # tag tag_sha(由 resolve_deploy_ref 在 checkout 前调用)
    local tag="$1" sha="$2"
    if ! git show "${sha}:scripts/deploy-lib.sh" 2>/dev/null | grep -qE '^DORAMI_BAREMETAL_TXN=[0-9]+'; then
        bm_fail "$BM_RC_NO_TXN_CAP" "目标 $tag(${sha:0:7})的部署脚本没有裸机事务能力(scripts/deploy-lib.sh 未宣告 DORAMI_BAREMETAL_TXN):以 tag 模式切换会换掉本脚本并失去回滚入口。改用当前编排器部署那份代码:  ./deploy.sh --code $tag"
    fi
    bm_pre_deploy_checks
}
# 任何正向部署(tag / --here / --code)进入实际步骤前:崩溃窗口 → 未收口事务分派 → 收养检测(§4.11 在 checkout 之前)
bm_pre_deploy_checks() {
    [ "${BM_PRE_CHECKS_DONE:-0}" = 1 ] && return 0
    bm_sample_running
    bm_dispatch_unclosed
    bm_ensure_adopted
    export BM_PRE_CHECKS_DONE=1
}
# 收养检测:无 last-success 且有既有部署证据 → 自动进入收养(第 4 层实现);此处先占位
bm_ensure_adopted() {
    [ -f "$BM_LAST_SUCCESS" ] && return 0
    local ev; ev="$(bm_evidence)"
    [ -n "$ev" ] || return 0
    if declare -F bm_adopt_main >/dev/null; then
        echo "    无 last-success 但有既有部署证据(${ev}):先收养旧形态安装(一次 PM2 重启的维护窗)"
        bm_adopt_main
    else
        echo "    ⚠️  无 last-success 但有既有部署证据(${ev}):收养尚未装配,按旧形态继续"
    fi
}
bm_adopt_resume() { bm_fail "$BM_RC_IDENTITY" "收养续做尚未装配"; }

# ── 告警(§4.9 ④):健康门不通过 = 部署失败;红字横幅 + pm2 现状 + 日志尾 + 精确的回滚命令;不自动回滚 ──
bm_alert_health_failed() {  # ref sha reason
    local ref="$1" sha="$2" reason="$3"
    BM_LAST_ERROR="健康核对未通过:$reason"
    {
        printf '\033[1;31m'
        echo "=================================================================="
        echo "  部署 ${ref}(${sha:0:7})健康核对未通过:${reason}"
        echo "  系统已切换到该版本且未自动回滚。"
        if [ -x "${BM_ENTRY:-}" ]; then
            echo "  回滚:  ./deploy.sh --rollback        (查看:./deploy.sh --status)"
        else
            echo "  本机尚无回滚入口(首装 / 未收养);排查:pm2 logs ${BM_APP_NAME} --lines 100"
        fi
        echo "=================================================================="
        printf '\033[0m'
        pm2 describe "$BM_APP_NAME" 2>/dev/null || true
        pm2 logs "$BM_APP_NAME" --nostream --lines 50 2>/dev/null || true
    } >&2
}

# ── --status(§4.13,只读、不抢锁)──
bm_status() {
    local head_sha head_ref dirty
    head_sha="$(git -C "$BM_REPO" rev-parse HEAD 2>/dev/null || echo "")"
    head_ref="$(git -C "$BM_REPO" describe --tags --always --dirty 2>/dev/null || echo "${head_sha:0:7}")"
    dirty="$( [ -n "$(git -C "$BM_REPO" status --porcelain 2>/dev/null)" ] && echo yes || echo no )"
    bm_sample_running
    echo "== 工作树(编排,不是已部署身份)=="
    echo "   HEAD: ${head_ref} (${head_sha:0:7}) dirty=${dirty}   编排器脚本: ${BM_LIB_DIR}"
    echo "== 现场 =="
    echo "   current -> ${BM_CURRENT_TARGET:-<无>}"
    [ -n "${NGINX_HTML_DIR:-}" ] && echo "   html_dir ${NGINX_HTML_DIR} -> ${BM_HTML_TARGET:-<无>}"
    if [ "${BM_PM2_PRESENT:-0}" = 1 ]; then
        echo "   pm2 ${BM_APP_NAME}: status=${BM_PM2_STATUS} pid=${BM_PM2_PID:-?} cwd=${BM_PM2_CWD} sha=${BM_PM2_SHA:0:7}"
        if [ -n "$BM_CURRENT_TARGET" ] && [ "$(bm_realpath "$BM_PM2_CWD")" != "$BM_CURRENT_TARGET" ]; then
            echo "   ⚠️  pm2 进程 cwd ≠ current"
        fi
    else
        echo "   pm2 ${BM_APP_NAME}: 无进程"
    fi
    if [ -n "$BM_HEALTH_SHA" ] || [ -n "$BM_HEALTH_VERSION" ]; then
        echo "   /api/health: version=${BM_HEALTH_VERSION:-?} build=${BM_HEALTH_REF:-?} (${BM_HEALTH_SHA:0:7})"
    else
        echo "   /api/health: 不可达"
    fi
    echo "== last-success =="
    if [ -f "$BM_LAST_SUCCESS" ]; then
        echo "   txn=$(deploy_json_get "$BM_LAST_SUCCESS" txn_id ?) kind=$(deploy_json_get "$BM_LAST_SUCCESS" kind ?) deployed_at=$(deploy_json_get "$BM_LAST_SUCCESS" deployed_at ?)"
        echo "   target: $(deploy_json_get "$BM_LAST_SUCCESS" target.ref ?) ($(deploy_json_get "$BM_LAST_SUCCESS" target.code_sha ? | cut -c1-7)) release=$(deploy_json_get "$BM_LAST_SUCCESS" target.release ?)"
        echo "   prev:   $(deploy_json_get "$BM_LAST_SUCCESS" prev.ref 无) ($(deploy_json_get "$BM_LAST_SUCCESS" prev.code_sha - | cut -c1-7)) txn=$(deploy_json_get "$BM_LAST_SUCCESS" prev.txn_id -)"
        local ls_sha; ls_sha="$(deploy_json_get "$BM_LAST_SUCCESS" target.code_sha "")"
        if [ -n "$BM_RUN_SHA" ]; then
            [ "$BM_RUN_SHA" = "$ls_sha" ] && echo "   运行身份与 last-success 一致(来源 $BM_RUN_SRC)" || echo "   ⚠️  运行身份 ${BM_RUN_SHA:0:7}(来源 $BM_RUN_SRC)≠ last-success ${ls_sha:0:7}"
        fi
        echo "   能力位: rollback=$(deploy_json_get "$BM_LAST_SUCCESS" capabilities.rollback ?) db_restore=$(deploy_json_get "$BM_LAST_SUCCESS" capabilities.db_restore ?) reproducible=$(deploy_json_get "$BM_LAST_SUCCESS" capabilities.reproducible ?)"
    else
        echo "   (无)——真首装或尚未收养(./deploy.sh --adopt)"
    fi
    echo "== in-progress =="
    if [ -f "$BM_IN_PROGRESS" ]; then
        printf '\033[31m'
        echo "   txn=$(bm_manifest_get txn_id ?) kind=$(bm_manifest_get kind ?) target=$(bm_manifest_get target.ref ?) ($(bm_manifest_get target.code_sha ? | cut -c1-7))"
        echo "   中断于 completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?) error=$(bm_manifest_get stage.error 无)"
        [ -n "$(bm_manifest_get recover_from "")" ] && echo "   recover_from=$(bm_manifest_get recover_from)"
        printf '\033[0m'
    else
        echo "   (无)"
    fi
    if declare -F bm_status_rollback_section >/dev/null; then
        bm_status_rollback_section
    fi
    echo "== 入口 =="
    [ -x "$BM_ENTRY" ] && echo "   $BM_ENTRY(已发布)" || echo "   $BM_ENTRY(未发布)"
}

# controller/rollback.sh 的入口(第 5 层装配 --rollback;此处先提供 --status)
bm_controller_main() {
    BM_CONTROLLER_DIR="$BM_LIB_DIR"
    cd "$BM_REPO" || bm_fail "$BM_RC_STEP" "仓库目录不存在: $BM_REPO"
    bm_init_paths "$BM_REPO"
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
    case "${1:-}" in
        --status) bm_status; exit 0 ;;
        --rollback)
            shift
            declare -F bm_rollback_main >/dev/null || bm_fail "$BM_RC_USAGE" "本执行体没有 --rollback(第 5 层装配)"
            bm_rollback_main "$@" ;;
        *) bm_fail "$BM_RC_USAGE" "用法: $0 --rollback [--restore-db] [--yes] [--to <txn>] [--no-rescue-snapshot] | --status" ;;
    esac
}
