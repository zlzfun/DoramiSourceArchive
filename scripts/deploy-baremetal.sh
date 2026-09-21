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
    [ -n "$(ls -A "$BM_REPO/data/media" 2>/dev/null || true)" ] && ev="$ev media"
    [ -n "$(ls -A "$BM_REPO/data/podcast-artifacts" 2>/dev/null || true)" ] && ev="$ev podcast-artifacts"
    [ -n "$(ls -A "$BM_REPO/backups" 2>/dev/null || true)" ] && ev="$ev backups"
    # 只有晋升过的 release(有 manifest.json)算证据;失败事务留下的材料目录不算
    [ -n "$(ls "$BM_RELEASES_DIR"/*/manifest.json 2>/dev/null || true)" ] && ev="$ev releases"
    echo "${ev# }"
}
# 证据快照:部署脚本自己会建目录 / 开事务,首装门必须用动作之前采样的证据
bm_snapshot_evidence() {
    BM_EVIDENCE_SNAPSHOT="$(bm_evidence)"
    BM_EVIDENCE_TAKEN=1
    export BM_EVIDENCE_SNAPSHOT BM_EVIDENCE_TAKEN
}
bm_evidence_for_gate() {
    if [ "${BM_EVIDENCE_TAKEN:-0}" = 1 ]; then printf '%s' "$BM_EVIDENCE_SNAPSHOT"; else bm_evidence; fi
}

# 首装门(§4.1):迁移计划报 fresh 时——有既有部署证据一律拒绝(数据目录配错,不提供覆盖);无证据的真首装需
# DORAMI_DEPLOY_FRESH_OK=1 显式授权。
bm_fresh_gate() {  # plan_status
    [ "$1" = "fresh" ] || return 0
    local ev; ev="$(bm_evidence_for_gate)"
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
    local ev; ev="$(bm_evidence_for_gate)"
    [ -n "$ev" ] || return 0
    if declare -F bm_adopt_main >/dev/null; then
        echo "    无 last-success 但有既有部署证据(${ev}):先收养旧形态安装(一次 PM2 重启的维护窗)"
        bm_adopt_main
    else
        echo "    ⚠️  无 last-success 但有既有部署证据(${ev}):收养尚未装配,按旧形态继续"
    fi
}
bm_adopt_resume() { bm_fail "$BM_RC_IDENTITY" "收养续做尚未装配"; }

# ══════════════════════ release 形态(§3 / §4.4–§4.9;第 3 层)══════════════════════

# ── --code <sha|tag>(§3.3):由当前编排器部署任意代码对象,不 checkout 工作树 ──
bm_resolve_code() {
    local want="$1" sha ref
    sha="$(git rev-parse -q --verify "${want}^{commit}" 2>/dev/null)" \
        || bm_fail "$BM_RC_USAGE" "--code $want 不是本地仓库里的提交 / tag(先 git fetch 取到它)"
    if git rev-parse -q --verify "refs/tags/${want}^{commit}" >/dev/null 2>&1; then
        ref="$want"
    else
        ref="$(git describe --tags --always "$sha" 2>/dev/null || echo "${sha:0:7}")"
    fi
    export DORAMI_DEPLOY_MODE="code" DORAMI_BUILD_SHA="$sha" DORAMI_BUILD_REF="$ref"
    echo "--code:由当前编排器($(git rev-parse --short HEAD))部署 ${ref}(${sha:0:7}),不切换工作树"
}

# ── dirty 固化(§4.4):临时 index add -A(独立排除集合)→ write-tree → 与 HEAD 树比对 → commit-tree → pin ref ──
# 输出:BM_CODE_SHA(要归档的 commit)、BM_DIRTY(true/false)、BM_PIN_REF(有则随 release 清理一起删)
bm_freeze_worktree() {  # txn_id
    local txn="$1" head_sha tree head_tree snap
    head_sha="$(git rev-parse HEAD)"
    # tag / --code 模式的代码身份 = resolve 出来的目标对象(--code 时 ≠ HEAD);只有 --here 才看工作树
    BM_PIN_REF=""; BM_DIRTY=false; BM_CODE_SHA="${DORAMI_BUILD_SHA:-$head_sha}"
    [ "${DORAMI_DEPLOY_MODE}" = "here" ] || return 0
    # 排除集合 = 固定项 + 由配置 / 环境 / 挂点映射生成的项(相对仓库根;不依赖目标 .gitignore)
    local excludes="venv venvs releases deploy-state logs data backups .venv frontend/node_modules frontend/dist current config/production.ini config/backend.ini .env"
    local extra p
    for p in "${VENV_DIR:-venv}" "$BM_RELEASES_DIR" "$BM_VENVS_DIR" "$BM_STATE_DIR" "${NGINX_HTML_DIR:-}" "${BM_EXTRA_EXCLUDES:-}"; do
        [ -n "$p" ] || continue
        case "$p" in
            "$BM_REPO"/*) extra="${p#"$BM_REPO"/}" ;;
            /*) continue ;;
            *) extra="$p" ;;
        esac
        excludes="$excludes $extra"
    done
    # 排除集合经临时 excludes 文件生效(显式 pathspec 点名已 ignore 的路径会被 git 拒绝);已跟踪却落在排除集合里的
    # 文件再从临时 index 里移除——两步合起来才是「独立于目标 .gitignore」的排除
    local tmp_index="$BM_STATE_DIR/.tmp-index-$$" excl_file="$BM_STATE_DIR/.tmp-excludes-$$"
    mkdir -p "$BM_STATE_DIR"
    { for p in $excludes; do printf '/%s\n' "$p"; done; printf '*.db\n*.sqlite\n*.db-wal\n*.db-shm\n'; } >"$excl_file"
    tree="$( (export GIT_INDEX_FILE="$tmp_index"
              git read-tree HEAD \
              && git -c core.excludesFile="$excl_file" add -A . >/dev/null \
              && git rm -r -q --cached --ignore-unmatch -- $excludes >/dev/null \
              && git write-tree) )" \
        || { rm -f "$tmp_index" "$excl_file"; bm_fail "$BM_RC_STEP" "固化工作树失败(git add -A / write-tree)"; }
    rm -f "$tmp_index" "$excl_file"
    head_tree="$(git rev-parse "HEAD^{tree}")"
    if [ "$tree" = "$head_tree" ]; then
        echo "    工作树与 HEAD 一致(clean),代码身份 = HEAD ${head_sha:0:7}"
        return 0
    fi
    # 快照 blob 总量阈值(默认 64 MiB):误把大文件当源码固化会把 release 撑爆
    local limit_mb="${DORAMI_DEPLOY_SNAPSHOT_MAX_MB:-64}" size_mb
    size_mb="$(git ls-tree -r -l "$tree" | awk '{s += $4} END {printf "%d", s / 1024 / 1024}')"
    [ "$size_mb" -le "$limit_mb" ] \
        || bm_fail "$BM_RC_STEP" "dirty 工作树快照 ${size_mb} MiB 超过阈值 ${limit_mb} MiB(DORAMI_DEPLOY_SNAPSHOT_MAX_MB):有大文件混进工作树?git status 查看后清理或加入排除"
    snap="$(echo "dorami-deploy: dirty worktree snapshot for ${txn}" | git commit-tree "$tree" -p "$head_sha")" \
        || bm_fail "$BM_RC_STEP" "commit-tree 失败"
    BM_PIN_REF="refs/dorami-deploy/${txn}"
    git update-ref "$BM_PIN_REF" "$snap" || bm_fail "$BM_RC_STEP" "写 pin ref $BM_PIN_REF 失败"
    BM_DIRTY=true; BM_CODE_SHA="$snap"
    export DORAMI_BUILD_SHA="$snap"
    case "$DORAMI_BUILD_REF" in *-dirty) ;; *) export DORAMI_BUILD_REF="${DORAMI_BUILD_REF}-dirty" ;; esac
    echo "    工作树 dirty(含未跟踪源码):固化为快照 ${snap:0:7}(pin $BM_PIN_REF),代码身份 = 快照而非 HEAD ${head_sha:0:7}"
}

# ── 代码副本(§4.4):git archive <code_sha> → <release>/app + app.sha256 ──
bm_archive_code() {  # code_sha release_dir
    local sha="$1" app="$2/app"
    mkdir -p "$app"
    git archive --format=tar "$sha" | tar -x -C "$app" || bm_fail "$BM_RC_STEP" "git archive ${sha:0:7} 失败"
    # 挂点冲突检查:归档里不得已有挂点路径(否则 symlink 建不上 / 语义歧义)
    local m
    for m in venv data logs config/production.ini; do
        [ -e "$app/$m" ] && bm_fail "$BM_RC_STEP" "代码归档里已存在挂点路径 app/$m,与共享挂点冲突;从源码树移除后重试"
    done
    bm_tree_sha256 "$app" >"$2/app.sha256" || bm_fail "$BM_RC_STEP" "写 app.sha256 失败"
    echo "    代码副本:$app($(wc -l <"$2/app.sha256" | tr -d ' ') 个文件,sha ${sha:0:7})"
}
# 目录内全部普通文件的 sha256 清单(跳过 symlink;相对路径排序)
bm_tree_sha256() {  # dir
    python3 - "$1" <<'PY'
import hashlib, os, sys
root = sys.argv[1]
rows = []
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if not os.path.islink(os.path.join(dp, d))]
    for fn in fns:
        p = os.path.join(dp, fn)
        if os.path.islink(p):
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        rows.append((os.path.relpath(p, root), h.hexdigest()))
for rel, digest in sorted(rows):
    print(f"{digest}  {rel}")
PY
}
# 核对清单:清单里每个文件存在且摘要一致(不检查多出的文件——挂点与运行期生成物不在清单)
bm_verify_sha256() {  # dir manifest
    python3 - "$1" "$2" <<'PY'
import hashlib, os, sys
root, manifest = sys.argv[1], sys.argv[2]
bad = []
for line in open(manifest, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line:
        continue
    digest, rel = line.split("  ", 1)
    p = os.path.join(root, rel)
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            bad.append(rel)
    except OSError:
        bad.append(rel)
    if len(bad) > 5:
        break
if bad:
    print("摘要不符 / 缺失: " + ", ".join(bad[:5]), file=sys.stderr); sys.exit(1)
PY
}

# ── venv 版本化(§4.5):指纹 = sha256(requirements.txt)+ extras 钉版清单 sha + 解释器身份 ──
# 输出 BM_VENV_DIR(绝对)、BM_VENV_FINGERPRINT;复用前逐项比对 inputs.json + .dorami-complete;半成品在锁内删除重建。
bm_interpreter_identity() {  # python
    "$1" -c 'import os, sys; print(sys.version.split()[0] + " " + os.path.realpath(sys.executable))' 2>/dev/null
}
bm_prepare_venv() {  # release_dir
    local release="$1" app="$1/app" req="$1/app/docker/requirements.txt"
    [ -f "$req" ] || bm_fail "$BM_RC_STEP" "目标代码没有 docker/requirements.txt(钉版清单):release 形态不做现解安装;该版本请用旧形态脚本部署"
    local extras="${DORAMI_DEPLOY_EXTRAS:-}" extra extra_files=() f
    for extra in ${extras//,/ }; do
        f="$app/docker/requirements-${extra}.txt"
        [ -f "$f" ] || bm_fail "$BM_RC_STEP" "DORAMI_DEPLOY_EXTRAS=$extras 但目标代码没有 docker/requirements-${extra}.txt(extras 须有钉版清单,不现解)"
        extra_files+=("$f")
    done
    # uv 将要使用的解释器(uv python find);建好后再核对实际身份
    local base_py identity
    base_py="$(uv python find 2>/dev/null | head -1 || true)"
    [ -n "$base_py" ] || base_py="$(command -v python3)"
    identity="$(bm_interpreter_identity "$base_py")"
    [ -n "$identity" ] || bm_fail "$BM_RC_STEP" "无法取得解释器身份($base_py)"
    local inputs_json fp
    inputs_json="$(python3 - "$req" "$identity" "$extras" ${extra_files[@]+"${extra_files[@]}"} <<'PY'
import hashlib, json, sys
req, identity, extras = sys.argv[1:4]
files = sys.argv[4:]
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
inputs = {"requirements_sha256": sha(req), "extras": {}, "interpreter": identity}
for name, path in zip([e for e in extras.replace(",", " ").split() if e], files):
    inputs["extras"][name] = sha(path)
fp = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
inputs["fingerprint"] = fp
print(json.dumps(inputs, sort_keys=True))
PY
)"
    fp="$(printf '%s' "$inputs_json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["fingerprint"])')"
    BM_VENV_FINGERPRINT="$fp"
    BM_VENV_DIR="$BM_VENVS_DIR/$fp"
    mkdir -p "$BM_VENVS_DIR"
    if [ -f "$BM_VENV_DIR/.dorami-complete" ] && [ -f "$BM_VENV_DIR/inputs.json" ] \
        && [ "$(python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); d.pop("playwright", None); d.pop("built_at", None); print(json.dumps(d, sort_keys=True))' "$BM_VENV_DIR/inputs.json")" = "$(printf '%s' "$inputs_json" | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin), sort_keys=True))')" ]; then
        echo "    venv:复用 $BM_VENV_DIR(指纹 $fp,inputs 一致)"
        return 0
    fi
    if [ -d "$BM_VENV_DIR" ]; then
        echo "    venv:$BM_VENV_DIR 是半成品或 inputs 不一致,删除重建"
        rm -rf "$BM_VENV_DIR"
    fi
    echo "    venv:新建 $BM_VENV_DIR(指纹 $fp)"
    if ! uv venv --python "$base_py" "$BM_VENV_DIR"; then
        rm -rf "$BM_VENV_DIR"; bm_fail "$BM_RC_STEP" "uv venv 失败"
    fi
    local got
    got="$(bm_interpreter_identity "$BM_VENV_DIR/bin/python")" \
        || { rm -rf "$BM_VENV_DIR"; bm_fail "$BM_RC_STEP" "新建 venv 的解释器无法运行($BM_VENV_DIR/bin/python)"; }
    # 解释器身份核对:venv 里的 python 是 base 的 symlink / 复制,版本应等于 base 的版本
    if [ "${got%% *}" != "${identity%% *}" ]; then
        rm -rf "$BM_VENV_DIR"; bm_fail "$BM_RC_STEP" "venv 解释器版本 ${got%% *} ≠ 预期 ${identity%% *}"
    fi
    # 只按钉版清单装,不做 editable 安装(运行时靠 PYTHONPATH=app/src;§4.5)
    if ! uv pip install --python "$BM_VENV_DIR/bin/python" -r "$req" ${extra_files[@]+$(printf ' -r %q' "${extra_files[@]}")}; then
        rm -rf "$BM_VENV_DIR"; bm_fail "$BM_RC_STEP" "uv pip install 失败(清单 $req)"
    fi
    local pw_version=""
    if [ -x "$BM_VENV_DIR/bin/playwright" ]; then
        pw_version="$("$BM_VENV_DIR/bin/python" -c 'import playwright, importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null || true)"
    fi
    printf '%s' "$inputs_json" | python3 -c 'import json, sys, time; d = json.load(sys.stdin); d["playwright"] = sys.argv[1]; d["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); print(json.dumps(d, indent=2, sort_keys=True))' "$pw_version" >"$BM_VENV_DIR/inputs.json"
    echo "kind=fingerprint" >"$BM_VENV_DIR/.dorami-complete"
    echo "    venv:完成($BM_VENV_DIR)"
    bm_provision_playwright "$BM_VENV_DIR"
}

# Playwright 浏览器(§4.5):PLAYWRIGHT_SKIP_BROWSER_GC=1 保留旧版浏览器(回滚到旧 release 仍可用),三层兜底与旧脚本相同,任何失败不阻断
bm_provision_playwright() {  # venv_dir
    local venv="$1"
    echo "    Provisioning Playwright Chromium (for the OpenAI News render node)..."
    if [ -n "${PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" ]; then
        echo "    使用预设的系统 Chromium: $PLAYWRIGHT_CHROMIUM_EXECUTABLE"
    elif [ -x "$venv/bin/playwright" ] && PLAYWRIGHT_SKIP_BROWSER_GC=1 "$venv/bin/playwright" install chromium; then
        ${SUDO:-} "$venv/bin/playwright" install-deps chromium \
            || echo "    ⚠️  playwright install-deps 失败或不适用;若 OpenAI News 渲染异常请手动装 Chromium 系统依赖。"
    else
        local sys_chromium
        sys_chromium="$(command -v chromium || command -v chromium-browser || command -v google-chrome || command -v google-chrome-stable || true)"
        if [ -n "$sys_chromium" ]; then
            export PLAYWRIGHT_CHROMIUM_EXECUTABLE="$sys_chromium"
            echo "    ⚠️  Playwright 自带浏览器装不上(OS 不受支持或网络不通),已自动改用系统 Chromium: $sys_chromium"
        else
            echo "    ⚠️  Playwright 自带浏览器装不上,且未发现系统 Chromium → OpenAI News 将降级为 RSS 摘要。"
            echo "        修复:装一个 Chromium 后重跑 ./deploy.sh,例如  sudo apt-get install -y chromium  (或 snap install chromium / 装 Google Chrome)"
        fi
    fi
}

# requires-python 独立检查(§4.5;不做 editable 安装后没人替我们检查):目标 pyproject 的声明 vs venv 解释器版本
bm_check_requires_python() {  # app venv
    python3 - "$1/pyproject.toml" "$("$2/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" <<'PY' || bm_fail "$BM_RC_STEP" "requires-python 不满足(见上)"
import re, sys
path, have = sys.argv[1], sys.argv[2]
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    sys.exit(0)
m = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.M)
if not m:
    sys.exit(0)
spec = m.group(1)
def vt(s):
    return tuple(int(x) for x in s.split(".")[:3]) + (0,) * (3 - len(s.split(".")[:3]))
hv = vt(have)
for clause in [c.strip() for c in spec.split(",") if c.strip()]:
    mm = re.match(r"(>=|<=|==|!=|>|<|~=)\s*([0-9.]+)", clause)
    if not mm:
        continue
    op, v = mm.group(1), vt(mm.group(2))
    ok = {">=": hv >= v, "<=": hv <= v, "==": hv[:len(mm.group(2).split("."))] == v[:len(mm.group(2).split("."))],
          "!=": hv != v, ">": hv > v, "<": hv < v, "~=": hv >= v and hv[:2] == v[:2]}[op]
    if not ok:
        print(f"目标代码 requires-python = \"{spec}\",venv 解释器是 {have}", file=sys.stderr); sys.exit(1)
PY
}

# ── 挂点(§4.7):release 内的 venv / data / logs / config/production.ini 指向共享位置 ──
bm_mount_points() {  # release_dir venv_dir config_file
    local app="$1/app"
    ln -sfn "$2" "$app/venv"
    mkdir -p "$BM_REPO/data" "$BM_REPO/logs"
    ln -sfn "$BM_REPO/data" "$app/data"
    ln -sfn "$BM_REPO/logs" "$app/logs"
    mkdir -p "$app/config"
    ln -sfn "$3" "$app/config/production.ini"
}

# 在目标上下文执行 python:目标 venv、cwd=app、PYTHONPATH=app/src、DORAMI_CONFIG_FILE 绝对路径;stdin 为脚本
bm_target_python() {  # app venv config_file [args…]
    local app="$1" venv="$2" cfg="$3"; shift 3
    (cd "$app" && DORAMI_CONFIG_FILE="$cfg" PYTHONPATH="$app/src" NODE_ENV=production "$venv/bin/python" - "$@")
}

# 路径探针(§4.7):在目标上下文导出所有路径型配置的最终绝对值(realpath),分「可变存储」与「随代码发布」两组
bm_path_probe() {  # app venv config_file → JSON
    bm_target_python "$1" "$2" "$3" <<'PY'
import json, os, sys
import config as c
cfg = c.load_config()
root = str(c.PROJECT_ROOT)
def rp(p):
    if not p:
        return ""
    p = os.path.expanduser(str(p))
    if not os.path.isabs(p):
        p = os.path.join(root, p)
    return os.path.realpath(p)
def g(obj, *names, default=""):
    for n in names:
        obj = getattr(obj, n, None)
        if obj is None:
            return default
    return obj
url = g(cfg, "storage", "database_url")
is_sqlite, db_path = False, ""
try:
    from sqlalchemy.engine import make_url
    u = make_url(url)
    is_sqlite = u.get_backend_name() == "sqlite"
    if is_sqlite and u.database and u.database != ":memory:" and not u.database.startswith("file:"):
        db_path = rp(u.database)
except Exception:
    if url.startswith("sqlite:///"):
        is_sqlite, db_path = True, rp(url[len("sqlite:///"):])
mutable = {
    "database": db_path,
    "media_dir": rp(g(cfg, "media", "media_dir")),
    "podcast_root": rp(g(cfg, "podcast_artifacts", "root_dir")),
    "backup_local_dir": rp(g(cfg, "backup", "local_dir")),
}
code_bound = {"catalog_path": rp(g(cfg, "taxonomy", "catalog_path"))}
print(json.dumps({"project_root": os.path.realpath(root), "is_sqlite": is_sqlite, "mutable": mutable, "code_bound": code_bound}, sort_keys=True))
PY
}

# 目标上下文检查(§4.7):可变存储必须解析到 release 之外(靠挂点承接),缺挂点则按探针结果动态补建(仅当归档里无同名路径),
# 有基准时逐项相等才放行(exit 33)。输出 BM_PROBE_JSON、BM_DB_TARGET、BM_DB_IS_SQLITE。
bm_check_paths() {  # release app venv config_file baseline_json(空=无基准) [baseline_release]
    local release="$1" app="$2" venv="$3" cfg="$4" baseline="$5" base_release="${6:-}" probe rel_real pass=0 fix
    rel_real="$(bm_realpath "$release")"
    [ -n "$base_release" ] && base_release="$(bm_realpath "$base_release")"
    while [ "$pass" -lt 3 ]; do
        pass=$((pass + 1))
        probe="$(bm_path_probe "$app" "$venv" "$cfg")" || bm_fail "$BM_RC_PATH_PROBE" "目标上下文路径探针失败(目标代码的 config 无法加载?)"
        fix="$(printf '%s' "$probe" | python3 -c '
import json, os, sys
d = json.load(sys.stdin); rel = sys.argv[1]; app = sys.argv[2]
for key, p in d["mutable"].items():
    if p and (p == rel or p.startswith(rel + os.sep)):
        inner = os.path.relpath(p, app)
        print(inner.split(os.sep)[0]); break
' "$rel_real" "$(bm_realpath "$app")")"
        [ -n "$fix" ] || break
        case "$fix" in
            ..*|.) bm_fail "$BM_RC_PATH_PROBE" "路径探针:可变存储解析到 release 内但不在 app/ 之下($fix),无法挂载" ;;
        esac
        if [ -e "$app/$fix" ] && [ ! -L "$app/$fix" ]; then
            bm_fail "$BM_RC_PATH_PROBE" "路径探针:配置把可变存储放在 app/$fix 之下,但代码归档里已有同名路径,挂点冲突;改配置指向仓库外 / data/ 之下"
        fi
        echo "    路径探针:app/$fix 是可变存储根,补建挂点 app/$fix -> $BM_REPO/$fix"
        mkdir -p "$BM_REPO/$fix"
        ln -sfn "$BM_REPO/$fix" "$app/$fix"
    done
    [ -z "$fix" ] || bm_fail "$BM_RC_PATH_PROBE" "路径探针:补建挂点后可变存储仍解析到 release 内($fix)"
    if [ -n "$baseline" ]; then
        python3 - "$probe" "$baseline" "$base_release" <<'PY' || bm_fail "$BM_RC_PATH_PROBE" "路径探针与基准不一致(见上):目标代码在 release 上下文里会读写另一份存储;核对配置后重试"
import json, os, sys
probe, base, base_release = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
bad = []
for key, p in probe["mutable"].items():
    b = base.get("mutable", {}).get(key)
    if b is None or b == "":
        continue  # 基准上下文的代码不认识该配置(更老的版本):不比较
    if base_release and (b == base_release or b.startswith(base_release + os.sep)):
        continue  # 基准 release 自己没有这个挂点(配置新增的存储根),它的值落在自己的 release 内,不是有效基准
    if p != b:
        bad.append(f"{key}: 目标={p} 基准={b}")
if probe.get("is_sqlite") != base.get("is_sqlite"):
    bad.append(f"数据库后端: 目标 sqlite={probe.get('is_sqlite')} 基准 sqlite={base.get('is_sqlite')}")
if bad:
    print("\n".join("    ✗ " + b for b in bad), file=sys.stderr); sys.exit(1)
PY
        echo "    路径探针:与基准一致"
    else
        echo "    路径探针:无基准(首装),可变存储均在 release 之外"
    fi
    BM_PROBE_JSON="$probe"
    BM_DB_TARGET="$(printf '%s' "$probe" | python3 -c 'import json, sys; print(json.load(sys.stdin)["mutable"]["database"])')"
    BM_DB_IS_SQLITE="$(printf '%s' "$probe" | python3 -c 'import json, sys; print("1" if json.load(sys.stdin)["is_sqlite"] else "0")')"
    export BM_PROBE_JSON BM_DB_TARGET BM_DB_IS_SQLITE
}

# ── 迁移计划(§4.8):算法自持,在目标上下文运行(目标 venv + PYTHONPATH + script_location=app/alembic)──
# 输出 JSON:status(fresh|legacy_adoption_required|compatible|incompatible|error)、pending_count、current_heads、target_heads、
# detail、error_kind(target_graph|db_unreadable)。库以 mode=ro&uri=true 打开,不存在即 fresh 不建。
bm_db_plan() {  # app venv config_file db_path → JSON
    bm_target_python "$1" "$2" "$3" "$1/alembic" "$4" <<'PY'
import json, os, sys
from urllib.parse import quote
script_location, db_path = sys.argv[1], sys.argv[2]
out = {"status": "", "detail": "", "current_heads": [], "target_heads": [], "pending": [], "pending_count": 0, "extra": [], "error_kind": ""}
def finish(status, detail, pending=(), extra=(), error_kind=""):
    out.update(status=status, detail=detail, pending=list(pending), pending_count=len(pending), extra=sorted(extra), error_kind=error_kind)
    print(json.dumps(out, ensure_ascii=False)); sys.exit(0)
try:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    from alembic.script.revision import ResolutionError
    from sqlalchemy import create_engine, event, inspect
    from sqlalchemy.engine import URL
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    script = ScriptDirectory.from_config(cfg)
    target_heads = sorted(script.get_heads())
    def closure(heads):
        s = set()
        for h in heads:
            for rev in script.walk_revisions(base="base", head=h):
                s.add(rev.revision)
        return s
    def ordered(wanted):
        desc = list(script.walk_revisions(base="base", head="heads"))
        return [r.revision for r in reversed(desc) if r.revision in wanted]
    required = closure(target_heads)
    out["target_heads"] = target_heads
except Exception as exc:
    finish("error", f"目标迁移图读取失败: {type(exc).__name__}: {exc}", error_kind="target_graph")
if not db_path:
    finish("error", "数据库不是 SQLite 文件(非 sqlite 后端由 --no-rollback-guarantee 显式处理)", error_kind="not_sqlite")
if not os.path.exists(db_path):
    finish("fresh", "数据库不存在:目标链将从头建立(是否放行由首装门决定)", pending=ordered(required))
try:
    url = URL.create("sqlite", database=f"file:{quote(db_path, safe='/')}", query={"mode": "ro", "uri": "true"})
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def _ro(conn, _rec):
        cur = conn.cursor(); cur.execute("PRAGMA query_only=ON"); cur.close()
    with engine.connect() as conn:
        current_heads = sorted(MigrationContext.configure(conn).get_current_heads())
        has_tables = "articles" in inspect(conn).get_table_names()
        integrity = conn.exec_driver_sql("PRAGMA integrity_check").scalar()
    engine.dispose()
except Exception as exc:
    finish("error", f"当前库无法读取: {type(exc).__name__}: {exc}", error_kind="db_unreadable")
if integrity != "ok":
    finish("error", f"当前库 integrity_check 未通过: {integrity}", error_kind="db_unreadable")
out["current_heads"] = current_heads
if not current_heads:
    if has_tables:
        finish("legacy_adoption_required", "有业务表但无 alembic_version:启动时 ensure_migrated 会对齐基线并收养后升级", pending=ordered(required))
    finish("fresh", "库文件存在但无业务表:目标链将从头建立", pending=ordered(required))
unknown = []
for h in current_heads:
    try:
        script.revision_map.get_revision(h)
    except ResolutionError:
        unknown.append(h)
if unknown:
    finish("incompatible", f"数据库当前 revision 不在目标代码的迁移图里: {unknown}(典型:库领先于目标代码)", extra=unknown)
applied = closure(current_heads)
pending = ordered(required - applied)
finish("compatible", "已在目标 revision 集合" if not pending else f"待执行 {len(pending)} 个迁移", pending=pending)
PY
}

# 在目标上下文执行迁移与 taxonomy reconcile(与旧脚本同两条命令,只是换了上下文)
bm_db_migrate() {  # app venv config_file
    echo "    Applying database migrations (alembic upgrade head)..."
    bm_target_python "$1" "$2" "$3" <<'PY' || bm_fail "$BM_RC_STEP" "数据库迁移失败"
from config import settings
from storage.migrations import ensure_migrated
ensure_migrated(settings.storage.database_url)
PY
    echo "    Reconciling configured Taxonomy deployment posture..."
    bm_target_python "$1" "$2" "$3" <<'PY' || bm_fail "$BM_RC_STEP" "Taxonomy reconcile 失败"
from config import settings
from services.taxonomy_deployment import run_taxonomy_deployment
print(run_taxonomy_deployment(settings.storage.database_url, settings.taxonomy))
PY
}

# DB 快照(§4.8):backups/baremetal/<txn>/<name>.sqlite,在线 .backup + integrity_check,记录真实时刻进 manifest
bm_db_snapshot() {  # txn db_path
    local dst="$BM_SNAPSHOT_DIR/$1/$(basename "$2" | sed 's/\.[^.]*$//').sqlite"
    if [ ! -f "$2" ]; then
        echo "    DB 快照:库文件不存在($2),跳过"
        deploy_json_set "$BM_IN_PROGRESS" db.snapshot "" || true
        return 0
    fi
    sqlite_snapshot "$2" "$dst" || bm_fail "$BM_RC_STEP" "DB 快照失败: $dst"
    deploy_json_set "$BM_IN_PROGRESS" db.snapshot "$dst" && deploy_json_set "$BM_IN_PROGRESS" db.snapshot_at "$(bm_now)" \
        || bm_fail "$BM_RC_STEP" "记录快照路径失败"
    echo "    DB 快照:$dst"
}

# ── 前端构建(§4.4 / §4.6):在 release 的临时 build/ 目录构建快照里的 frontend,产物移入 dist/,不从工作树构建 ──
bm_build_dist() {  # release_dir txn_id  → BM_DIST_DIR
    local release="$1" build="$1/build"
    rm -rf "$build"; mkdir -p "$build"
    cp -R "$release/app/frontend" "$build/frontend" || bm_fail "$BM_RC_STEP" "复制 frontend 源码到构建目录失败"
    (cd "$build/frontend" \
        && npm install --no-audit --no-fund --replace-registry-host=always ${NPM_REGISTRY:+--registry=${NPM_REGISTRY}} \
        && npm run build) || bm_fail "$BM_RC_STEP" "前端构建失败"
    [ -f "$build/frontend/dist/index.html" ] || bm_fail "$BM_RC_STEP" "前端构建没有产出 dist/index.html"
    rm -rf "$release/dist"
    mv "$build/frontend/dist" "$release/dist" || bm_fail "$BM_RC_STEP" "移动 dist 失败"
    rm -rf "$build"
    chmod -R o+rX "$release/dist" 2>/dev/null || true
    bm_tree_sha256 "$release/dist" >"$release/dist.sha256" || bm_fail "$BM_RC_STEP" "写 dist.sha256 失败"
    BM_DIST_DIR="$release/dist"
    # 可选宿主目录(NGINX_RELEASES_DIR / [nginx] releases_dir):仓库在 /root 之类 nginx worker 穿不过的位置时,
    # dist 复制到 sudo 归属的宿主目录,html_dir 指过去;清理时随 release 一起删
    if [ -n "${NGINX_RELEASES_DIR:-}" ]; then
        local host_dist="$NGINX_RELEASES_DIR/$2"
        ${SUDO:-} mkdir -p "$host_dist" || bm_fail "$BM_RC_STEP" "建 $host_dist 失败"
        ${SUDO:-} cp -R "$release/dist/." "$host_dist/" || bm_fail "$BM_RC_STEP" "复制 dist 到 $host_dist 失败"
        ${SUDO:-} chmod -R o+rX "$host_dist" || true
        bm_verify_sha256 "$host_dist" "$release/dist.sha256" || bm_fail "$BM_RC_STEP" "宿主目录 dist 与 dist.sha256 不符"
        BM_DIST_DIR="$host_dist"
    fi
    echo "    dist:$BM_DIST_DIR($(wc -l <"$release/dist.sha256" | tr -d ' ') 个文件)"
}

# ── nginx 变更集(§4.6)──
# 受影响集合(站点文件 / enabled 链接 / default 站点 / 主配置)的原状记录在 <release>/nginx/changes.json,**先记后写**;
# 落盘后的最终状态快照进 <release>/nginx/snapshot.json(回滚恢复源)。所有文件读写经 $SUDO python 做,GNU / BSD 通用。
bm_nginx_record_state() {  # out_json path…  —— 记录各路径当前状态(absent | file(content_b64) | symlink(target))
    local out="$1"; shift
    ${SUDO:-} python3 - "$out" "$@" <<'PY'
import base64, json, os, sys
out = sys.argv[1]
rows = []
for p in sys.argv[2:]:
    if os.path.islink(p):
        rows.append({"path": p, "kind": "symlink", "link_target": os.readlink(p)})
    elif os.path.isfile(p):
        rows.append({"path": p, "kind": "file", "content_b64": base64.b64encode(open(p, "rb").read()).decode(), "mode": oct(os.stat(p).st_mode & 0o777)})
    elif os.path.exists(p):
        print(f"{p} 既不是文件也不是 symlink,拒绝纳入变更集", file=sys.stderr); sys.exit(1)
    else:
        rows.append({"path": p, "kind": "absent"})
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"changes": rows}, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
os.replace(tmp, out)
PY
}
bm_nginx_apply_state() {  # json  —— 把记录里的每个路径恢复成记录的状态(absent 即删除)
    ${SUDO:-} python3 - "$1" <<'PY'
import base64, json, os, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for row in d["changes"]:
    p = row["path"]
    if os.path.islink(p) or os.path.isfile(p):
        os.unlink(p)
    elif os.path.isdir(p):
        print(f"{p} 是目录,拒绝覆盖", file=sys.stderr); sys.exit(1)
    if row["kind"] == "absent":
        continue
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    if row["kind"] == "symlink":
        os.symlink(row["link_target"], p)
    else:
        with open(p, "wb") as f:
            f.write(base64.b64decode(row["content_b64"]))
        try:
            os.chmod(p, int(row.get("mode", "0o644"), 8))
        except Exception:
            pass
PY
}
# nginx 站点写入(替代旧 write_nginx_site_config 的直接写):候选先落 <release>/nginx/site.conf,记变更集,再落在线路径,
# 校验失败按记录整体恢复。调用方须已 resolve_nginx_site_file / check_nginx_ssl_inputs / 设 NGINX_BIN。
bm_nginx_prepare() {  # release_dir backend_host backend_port
    local release="$1" ndir="$1/nginx" main_conf="" need_include=0
    mkdir -p "$ndir"
    render_nginx_site_config "$2" "$3" >"$ndir/site.conf"
    # 主配置是否需要插 include(源码装 nginx):以现状判断,记进受影响集合
    if ! ${SUDO:-} "$NGINX_BIN" -T 2>/dev/null | grep -qF "configuration file ${NGINX_SITE_FILE}"; then
        resolve_nginx_main_conf; main_conf="$NGINX_MAIN_CONF"; need_include=1
    fi
    local affected=("$NGINX_SITE_FILE")
    [ "$NGINX_SITE_ENABLED_FILE" != "$NGINX_SITE_FILE" ] && affected+=("$NGINX_SITE_ENABLED_FILE")
    if [ "$NGINX_SITE_ENABLED_FILE" != "$NGINX_SITE_FILE" ] && truthy "$NGINX_DISABLE_DEFAULT_SITE" && [ -e "$NGINX_DEFAULT_SITE_FILE" ]; then
        affected+=("$NGINX_DEFAULT_SITE_FILE")
    fi
    [ -n "$main_conf" ] && affected+=("$main_conf")
    # 先记后写(首次宿主写入 intent 已由调用方落盘)
    bm_nginx_record_state "$ndir/changes.json" "${affected[@]}" || bm_fail "$BM_RC_STEP" "记录 nginx 变更集失败"
    printf '%s\n' "${affected[@]}" >"$ndir/managed-paths"
    if ! bm_nginx_write_online "$release" "$2" "$3"; then
        echo "    nginx 落盘 / 校验失败:按变更集整体恢复..." >&2
        bm_nginx_apply_state "$ndir/changes.json" || echo "    ⚠️  按变更集恢复失败,需人工检查 $ndir/changes.json" >&2
        bm_fail "$BM_RC_STEP" "nginx 站点配置未通过校验(已恢复原状)"
    fi
    bm_nginx_record_state "$ndir/snapshot.json" "${affected[@]}" || bm_fail "$BM_RC_STEP" "记录 nginx 快照失败"
}
bm_nginx_write_online() {  # release backend_host backend_port(返回非零 = 失败,由调用方恢复)
    local release="$1"
    ${SUDO:-} mkdir -p "$(dirname "$NGINX_SITE_FILE")" || return 1
    echo "Writing Nginx site config: $NGINX_SITE_FILE"
    ${SUDO:-} tee "$NGINX_SITE_FILE" <"$release/nginx/site.conf" >/dev/null || return 1
    if [ "$NGINX_SITE_ENABLED_FILE" != "$NGINX_SITE_FILE" ]; then
        ${SUDO:-} ln -sfn "$NGINX_SITE_FILE" "$NGINX_SITE_ENABLED_FILE" || return 1
        if truthy "$NGINX_DISABLE_DEFAULT_SITE" && [ -e "$NGINX_DEFAULT_SITE_FILE" ]; then
            echo "Disabling default Nginx site: $NGINX_DEFAULT_SITE_FILE"
            ${SUDO:-} rm -f "$NGINX_DEFAULT_SITE_FILE" || return 1
        fi
    fi
    ( ensure_site_included ) || return 1
    ( validate_nginx_config "$2" "$3" ) || return 1
}
# 回滚用(§4.6 / §4.10):先撤销 recover_from 的变更集,再恢复目标快照,再 nginx -t
bm_nginx_restore_for_rollback() {  # recover_from_release(可空) target_release
    local from="$1" target="$2"
    if [ -n "$from" ] && [ -f "$from/nginx/changes.json" ]; then
        echo "    nginx:撤销 $from 的变更集"
        bm_nginx_apply_state "$from/nginx/changes.json" || bm_fail "$BM_RC_STEP" "撤销 nginx 变更集失败"
    fi
    [ -f "$target/nginx/snapshot.json" ] || bm_fail "$BM_RC_NO_TARGET" "目标 release 没有 nginx 快照($target/nginx/snapshot.json)"
    echo "    nginx:恢复 $target 的配置集合快照"
    bm_nginx_apply_state "$target/nginx/snapshot.json" || bm_fail "$BM_RC_STEP" "恢复 nginx 快照失败"
    ${SUDO:-} "$NGINX_BIN" -t || bm_fail "$BM_RC_STEP" "恢复后 nginx -t 未通过"
}

# ── 切换序(§4.9)──
bm_pm2_stop() {
    if pm2 describe "$BM_APP_NAME" >/dev/null 2>&1; then
        pm2 delete "$BM_APP_NAME" >/dev/null 2>&1 || pm2 delete "$BM_APP_NAME" || bm_fail "$BM_RC_STEP" "pm2 delete $BM_APP_NAME 失败"
    fi
    local i
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
        pm2 describe "$BM_APP_NAME" >/dev/null 2>&1 || return 0
        sleep 1
    done
    bm_fail "$BM_RC_STEP" "pm2 进程 $BM_APP_NAME 30 秒内未退出"
}
bm_switch_links() {  # app_dir dist_dir(html_dir 与 current 两个 symlink)
    local app="$1" dist="$2"
    if [ -n "${NGINX_HTML_DIR:-}" ]; then
        if [ -d "$NGINX_HTML_DIR" ] && [ ! -L "$NGINX_HTML_DIR" ]; then
            bm_fail "$BM_RC_IDENTITY" "html_dir $NGINX_HTML_DIR 是真实目录(旧形态发布物):先 ./deploy.sh --adopt 收养,或人工把它挪走"
        fi
        ${SUDO:-} mkdir -p "$(dirname "$NGINX_HTML_DIR")"
        deploy_atomic_symlink "$dist" "$NGINX_HTML_DIR" ${SUDO:-} || bm_fail "$BM_RC_STEP" "切换 html_dir symlink 失败"
        ensure_traversal_bits "$(bm_realpath "$dist")"
        ensure_traversal_bits "$(dirname "$NGINX_HTML_DIR")"
    fi
    deploy_atomic_symlink "$app" "$BM_CURRENT_LINK" || bm_fail "$BM_RC_STEP" "切换 current symlink 失败"
}
bm_pm2_start() {  # release_dir ref sha config_file
    local app="$1/app"
    (cd "$app" && DORAMI_BUILD_REF="$2" DORAMI_BUILD_SHA="$3" DORAMI_CONFIG_FILE="$4" NODE_ENV=production \
        PLAYWRIGHT_CHROMIUM_EXECUTABLE="${PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" \
        pm2 start "$app/ecosystem.config.js" --update-env) || bm_fail "$BM_RC_STEP" "pm2 start 失败"
    pm2 save || bm_fail "$BM_RC_STEP" "pm2 save 失败(开机 resurrect 必须指向已确认的 release)"
}

# ── 两级健康门(§4.9)──
# ① 后端身份门(直连 backend_proxy);② 站点链路门(经真实 nginx 入口:index + 主 JS / CSS 内容摘要 + /api/health);
# ③ 五项一致后连续观察 DORAMI_DEPLOY_STABLE_SECONDS(默认 10)秒且 PID 不变。任一失败返回非零(不自动回滚)。
bm_health_gates() {  # dist_dir version ref sha  → BM_GATE_REASON
    local dist="$1" version="$2" ref="$3" sha="$4"
    local budget="${DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS:-180}" attempts="${DORAMI_DEPLOY_HEALTH_ATTEMPTS:-90}"
    BM_GATE_REASON=""
    echo "    健康门 ①:后端身份(http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health 五项)..."
    if ! deploy_wait_healthy "http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health" "$version" "$ref" "$sha" "$budget" "$attempts"; then
        BM_GATE_REASON="后端身份门:${DEPLOY_HEALTH_LAST_VERDICT:-无响应}(预算 ${budget}s,尝试 ${DEPLOY_HEALTH_ATTEMPT} 次)"
        return 1
    fi
    echo "    健康门 ②:站点链路(经 nginx:index + 主资产摘要 + /api/health)..."
    local out
    if ! out="$(bm_site_probe "$dist" "$version" "$ref" "$sha" 2>&1)"; then
        BM_GATE_REASON="站点链路门:${out}"
        return 1
    fi
    local stable="${DORAMI_DEPLOY_STABLE_SECONDS:-10}" pid0 pid1 t0 body verdict
    if [ "$stable" -gt 0 ]; then
        echo "    健康门 ③:稳定窗 ${stable}s(PID 不变、/api/health 持续一致)..."
        pid0="$(bm_pm2_pid)"
        t0=$(date +%s)
        while [ $(( $(date +%s) - t0 )) -lt "$stable" ]; do
            sleep 2
            body="$(curl -fsS --connect-timeout 3 --max-time 8 -H 'Cache-Control: no-cache' "http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health?_=$(date +%s)" 2>/dev/null || true)"
            verdict="$(printf '%s' "$body" | deploy_health_verdict "$version" "$ref" "$sha")"
            [ "$verdict" = "ok" ] || { BM_GATE_REASON="稳定窗内 /api/health 变化:${verdict}"; return 1; }
            pid1="$(bm_pm2_pid)"
            [ "$pid1" = "$pid0" ] || { BM_GATE_REASON="稳定窗内进程重启(pid ${pid0:-?} → ${pid1:-?})"; return 1; }
        done
    fi
    return 0
}
bm_pm2_pid() {
    pm2 jlist 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read(); i = raw.find("[")
try:
    procs = json.loads(raw[i:]) if i >= 0 else []
except Exception:
    procs = []
for p in procs:
    if p.get("name") == sys.argv[1]:
        print(p.get("pid") or ""); break
' "$BM_APP_NAME"
}
# 站点链路探针(§4.9 ②):curl 经真实入口;server_name 首个非 _ 名字作 Host(TLS 用 --resolve),`_` 则 127.0.0.1;
# 不提供 -k,证书用系统 CA 或 DORAMI_DEPLOY_PROBE_CACERT。输出 ok 或失败原因(stdout),退出码 0/1。
bm_site_probe() {  # dist version ref sha
    python3 - "$1" "$2" "$3" "$4" "${NGINX_SERVER_NAME:-_}" "${NGINX_LISTEN_PORT:-80}" "${NGINX_ENABLE_SSL:-false}" \
        "${NGINX_SSL_LISTEN_PORT:-443}" "${NGINX_SSL_REDIRECT:-true}" "${DORAMI_DEPLOY_PROBE_CACERT:-}" <<'PY'
import hashlib, json, os, re, subprocess, sys, tempfile
dist, version, ref, sha, server_name, http_port, ssl, ssl_port, ssl_redirect, cacert = sys.argv[1:11]
truthy = lambda s: str(s).strip().lower() in ("1", "true", "yes", "on")
name = next((n for n in server_name.split() if n and n != "_"), "")
resolve, host_header = [], []
if truthy(ssl):
    base = f"https://{name}:{ssl_port}" if name else f"https://127.0.0.1:{ssl_port}"
    if name:
        resolve = ["--resolve", f"{name}:{ssl_port}:127.0.0.1"]
    if cacert:
        resolve += ["--cacert", cacert]
    http_base = f"http://127.0.0.1:{http_port}"
    if name:
        host_header = ["-H", f"Host: {name}"]
else:
    base = f"http://127.0.0.1:{http_port}"
    if name:
        host_header = ["-H", f"Host: {name}"]
def fetch(url, extra=()):
    fd, tmp = tempfile.mkstemp(prefix="dorami-probe-"); os.close(fd)
    try:
        r = subprocess.run(["curl", "-sS", "--connect-timeout", "5", "--max-time", "20", "-o", tmp, "-w", "%{http_code}\t%{content_type}\t%{redirect_url}",
                            *resolve, *host_header, *extra, url], capture_output=True, text=True)
        if r.returncode != 0:
            return None, "", "", f"curl 失败 rc={r.returncode}: {r.stderr.strip()[:200]}"
        parts = (r.stdout.split("\t") + ["", "", ""])[:3]
        return open(tmp, "rb").read(), parts[0], parts[1], parts[2]
    finally:
        os.unlink(tmp)
def fail(msg):
    print(msg); sys.exit(1)
def sha256(b):
    return hashlib.sha256(b).hexdigest()
body, code, ctype, _ = fetch(base + "/index.html")
if body is None:
    fail(f"index.html 不可达: {_}")
if code != "200":
    fail(f"index.html HTTP {code}")
want = open(os.path.join(dist, "index.html"), "rb").read()
if body != want:
    fail("index.html 内容 ≠ 本 release 的 dist/index.html(nginx root 未切换或缓存)")
html = body.decode("utf-8", "replace")
scripts = re.findall(r'<script[^>]+type=["\']module["\'][^>]*src=["\']([^"\']+)["\']', html) or re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
styles = re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']', html)
assets = [(scripts[0], "script") if scripts else None, (styles[0], "style") if styles else None]
for item in assets:
    if not item:
        continue
    path, kind = item
    if path.startswith("http://") or path.startswith("https://") or path.startswith("//"):
        continue
    rel = path.split("?")[0].lstrip("/")
    local = os.path.join(dist, rel)
    if not os.path.isfile(local):
        fail(f"index 引用的资产 {path} 不在 dist 里")
    b, code, ctype, _ = fetch(base + "/" + rel)
    if b is None or code != "200":
        fail(f"资产 {path} HTTP {code or '不可达'}")
    ct = (ctype or "").lower()
    if kind == "script" and "javascript" not in ct and "ecmascript" not in ct:
        fail(f"资产 {path} 的 Content-Type 是 {ctype!r},不是脚本(被 SPA 回退成 HTML?)")
    if kind == "style" and "css" not in ct:
        fail(f"资产 {path} 的 Content-Type 是 {ctype!r},不是样式")
    if sha256(b) != sha256(open(local, "rb").read()):
        fail(f"资产 {path} 经站点取回的内容 ≠ dist 内文件")
b, code, ctype, _ = fetch(base + "/api/health", ["-H", "Cache-Control: no-cache"])
if b is None or code != "200":
    fail(f"经站点的 /api/health HTTP {code or '不可达'}")
try:
    d = json.loads(b)
except Exception:
    fail("经站点的 /api/health 不是 JSON")
bld = d.get("build") or {}
got = {"status": d.get("status"), "version": d.get("version"), "ref": bld.get("ref"), "sha": bld.get("sha"), "source": bld.get("source")}
exp = {"status": "ok", "version": version, "ref": ref, "sha": sha, "source": "env"}
bad = [k for k in exp if got.get(k) != exp[k]]
if bad:
    fail("经站点的 /api/health 不一致: " + ",".join(f"{k}={got.get(k)}" for k in bad))
if truthy(ssl) and truthy(ssl_redirect):
    b, code, ctype, loc = fetch(http_base + "/")
    if b is None or code not in ("301", "308"):
        fail(f"HTTP 入口未 301 到 https(得到 {code or '不可达'})")
    if not loc.startswith("https://"):
        fail(f"HTTP 入口的 Location 不是 https({loc!r})")
print("ok")
PY
}

# ── 清理与引用集合(§4.12)──
bm_referenced_releases() {  # → 一行一个 release 目录(realpath)
    python3 - "$BM_STATE_DIR" "$BM_CLOSED_DIR" "$BM_CURRENT_LINK" "${NGINX_HTML_DIR:-}" "${BM_PM2_CWD:-}" "$HOME/.pm2/dump.pm2" "${DORAMI_DEPLOY_CLOSED_KEEP_DAYS:-7}" <<'PY'
import glob, json, os, sys, time
state, closed, current, html, pm2_cwd, dump, keep_days = sys.argv[1:8]
refs = set()
def add_release(p):
    if p:
        refs.add(os.path.realpath(p))
def add_app(p):
    if p:
        rp = os.path.realpath(p)
        refs.add(os.path.dirname(rp) if os.path.basename(rp) == "app" else rp)
def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
def scan(m):
    if not m:
        return
    for key in ("target", "prev"):
        v = m.get(key) or {}
        add_release(v.get("release"))
    rf = m.get("recover_from")
    if rf:
        add_release(os.path.join(os.path.dirname(os.path.dirname(m.get("controller") or "")), rf) if False else None)
    c = m.get("controller")
    if c:
        add_release(os.path.dirname(c))
for name in ("in-progress.json", "last-success.json"):
    scan(load(os.path.join(state, name)))
now = time.time()
for p in glob.glob(os.path.join(closed, "*.json")):
    if now - os.path.getmtime(p) <= float(keep_days) * 86400:
        scan(load(p))
if os.path.islink(current):
    add_app(current)
if html and os.path.islink(html):
    rp = os.path.realpath(html)
    refs.add(os.path.dirname(rp) if os.path.basename(rp) == "dist" else rp)
if pm2_cwd:
    add_app(pm2_cwd)
d = load(dump)
if isinstance(d, list):
    for proc in d:
        add_app((proc or {}).get("pm_cwd"))
for r in sorted(refs):
    print(r)
PY
}
bm_cleanup() {  # 按数量清理引用集合之外的 release(3)/ venv(2)/ 快照(10);失败非致命
    local keep_rel="${DORAMI_DEPLOY_KEEP_RELEASES:-3}" keep_venv="${DORAMI_DEPLOY_KEEP_VENVS:-2}" keep_snap="${DORAMI_DEPLOY_KEEP_SNAPSHOTS:-10}"
    local referenced out line txn
    referenced="$(bm_referenced_releases)"
    out="$(python3 - "$BM_RELEASES_DIR" "$BM_VENVS_DIR" "$BM_SNAPSHOT_DIR" "$keep_rel" "$keep_venv" "$keep_snap" "$referenced" "$BM_REPO" <<'PY' || echo "    ⚠️  清理未完成(maintenance_failed),下次重试"
import json, os, shutil, subprocess, sys
releases, venvs, snaps, keep_rel, keep_venv, keep_snap, referenced, repo = sys.argv[1:9]
refs = set(l for l in referenced.split("\n") if l)
def opened_at(path):
    for name in ("manifest.json",):
        try:
            return json.load(open(os.path.join(path, name), encoding="utf-8")).get("opened_at") or ""
        except Exception:
            pass
    return os.path.basename(path)
# release
kept_venvs = set()
if os.path.isdir(releases):
    dirs = [os.path.join(releases, d) for d in os.listdir(releases) if os.path.isdir(os.path.join(releases, d))]
    for d in dirs:
        if os.path.realpath(d) in refs:
            v = os.path.join(d, "app", "venv")
            if os.path.islink(v):
                kept_venvs.add(os.path.realpath(v))
    others = sorted([d for d in dirs if os.path.realpath(d) not in refs], key=opened_at, reverse=True)
    for d in others[:int(keep_rel)]:
        v = os.path.join(d, "app", "venv")
        if os.path.islink(v):
            kept_venvs.add(os.path.realpath(v))
    for d in others[int(keep_rel):]:
        txn = os.path.basename(d)
        subprocess.run(["git", "-C", repo, "update-ref", "-d", f"refs/dorami-deploy/{txn}"], capture_output=True)
        shutil.rmtree(d, ignore_errors=True)
        print(f"RELEASE_REMOVED {txn}")
# venv:只删不被任何保留 release 引用的
if os.path.isdir(venvs):
    vd = [os.path.join(venvs, d) for d in os.listdir(venvs) if os.path.isdir(os.path.join(venvs, d))]
    others = sorted([d for d in vd if os.path.realpath(d) not in kept_venvs], key=os.path.getmtime, reverse=True)
    for d in others[int(keep_venv):]:
        shutil.rmtree(d, ignore_errors=True)
        print(f"    清理 venv {os.path.basename(d)}")
# 快照目录(按 txn):引用集合里的事务快照不删
ref_txns = set(os.path.basename(r) for r in refs)
if os.path.isdir(snaps):
    sd = [os.path.join(snaps, d) for d in os.listdir(snaps) if os.path.isdir(os.path.join(snaps, d))]
    others = sorted([d for d in sd if os.path.basename(d) not in ref_txns], key=os.path.getmtime, reverse=True)
    for d in others[int(keep_snap):]:
        shutil.rmtree(d, ignore_errors=True)
        print(f"    清理快照 {os.path.basename(d)}")
PY
)"
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        case "$line" in
            "RELEASE_REMOVED "*)
                txn="${line#RELEASE_REMOVED }"
                echo "    清理 release $txn"
                if [ -n "${NGINX_RELEASES_DIR:-}" ] && [ -d "$NGINX_RELEASES_DIR/$txn" ]; then
                    ${SUDO:-} rm -rf "$NGINX_RELEASES_DIR/$txn" || echo "    ⚠️  删除 $NGINX_RELEASES_DIR/$txn 失败"
                fi ;;
            *) echo "$line" ;;
        esac
    done <<<"$out"
}

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
