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
BM_FIRST_HOST_WRITE_ADOPT="process_stopped"

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
    local host="${BACKEND_PROXY_HOST:-127.0.0.1}" port="${BACKEND_PROXY_PORT:-8088}" health jlist jlist_rc=0 health_tmp health_code
    # 「端口有没有响应」与「响应是不是有效身份」分开记(codex R1 P1-07 复检):不带 -f,HTTP 4xx/5xx / 非 JSON / 空响应都算有响应
    health_tmp="$(mktemp "${TMPDIR:-/tmp}/dorami-health.XXXXXX")"
    health_code="$(curl -sS -o "$health_tmp" -w '%{http_code}' --connect-timeout 3 --max-time 8 -H 'Cache-Control: no-cache' "http://${host}:${port}/api/health?_=$(date +%s)" 2>/dev/null || true)"
    health="$(cat "$health_tmp" 2>/dev/null || true)"; rm -f "$health_tmp"
    case "$health_code" in ""|000) BM_HEALTH_HTTP="none" ;; *) BM_HEALTH_HTTP="$health_code" ;; esac
    if command -v pm2 >/dev/null 2>&1; then
        jlist="$(pm2 jlist 2>/dev/null)" || jlist_rc=$?
    else
        jlist=""; jlist_rc=127
    fi
    eval "$(python3 - "$BM_APP_NAME" "$health" "$jlist" "$jlist_rc" <<'PY'
import json, shlex, sys
app, health, jlist, jlist_rc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
out = {"BM_HEALTH_JSON": "", "BM_HEALTH_SHA": "", "BM_HEALTH_REF": "", "BM_HEALTH_VERSION": "",
       "BM_PM2_PRESENT": "0", "BM_PM2_STATUS": "", "BM_PM2_CWD": "", "BM_PM2_SHA": "", "BM_PM2_REF": "", "BM_PM2_PID": "",
       "BM_PM2_QUERY": "failed"}
try:
    h = json.loads(health)
    b = h.get("build") or {}
    out["BM_HEALTH_JSON"] = json.dumps(h, ensure_ascii=False, sort_keys=True)
    out["BM_HEALTH_SHA"] = str(b.get("sha") or "")
    out["BM_HEALTH_REF"] = str(b.get("ref") or "")
    out["BM_HEALTH_VERSION"] = str(h.get("version") or "")
except Exception:
    pass
# pm2 查询结果分三态:ok(拿到有效列表)/ failed(命令失败、坏 JSON、非列表)——「查询失败」不等于「确认没在跑」(§4.1 停机例外)
procs = []
if jlist_rc == "0":
    try:
        start = jlist.find("[")  # pm2 jlist 偶尔在 JSON 前打印一行升级提示:从第一个 '[' 起解析
        parsed = json.loads(jlist[start:]) if start >= 0 else None
        if isinstance(parsed, list):
            procs = parsed
            out["BM_PM2_QUERY"] = "ok"
    except Exception:
        pass
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
    export BM_HEALTH_JSON BM_HEALTH_SHA BM_HEALTH_REF BM_HEALTH_VERSION BM_HEALTH_HTTP BM_PM2_PRESENT BM_PM2_STATUS BM_PM2_CWD \
        BM_PM2_SHA BM_PM2_REF BM_PM2_PID BM_PM2_QUERY BM_CURRENT_TARGET BM_HTML_TARGET BM_RUN_SHA BM_RUN_REF BM_RUN_SRC
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
    local ls_release ls_sha ls_ref ls_app ls_dist why="" verdict=""
    ls_release="$(deploy_json_get "$BM_LAST_SUCCESS" target.release "")"
    ls_sha="$(deploy_json_get "$BM_LAST_SUCCESS" target.code_sha "")"
    ls_ref="$(deploy_json_get "$BM_LAST_SUCCESS" target.ref "")"
    ls_dist="$(deploy_json_get "$BM_LAST_SUCCESS" target.dist "$ls_release/dist")"
    ls_app="$(bm_realpath "$ls_release/app")"
    [ -d "$ls_release" ] || why="last-success 的 release 目录不存在($ls_release)"
    if [ -z "$why" ] && [ "$BM_CURRENT_TARGET" != "$ls_app" ]; then
        why="current 指向 ${BM_CURRENT_TARGET:-<无>},不是 last-success 的 $ls_app"
    fi
    if [ -z "$why" ] && [ -n "${NGINX_HTML_DIR:-}" ] && [ "$BM_HTML_TARGET" != "$(bm_realpath "$ls_dist")" ]; then
        why="html_dir 指向 ${BM_HTML_TARGET:-<无>},不是 last-success 的 $ls_dist"
    fi
    # 运行身份(§4.1):pm2 进程存在时无论身份来源都核 cwd;pm2 来源还核 sha;health 有响应但身份缺失 / 冲突也是冲突;
    # 两者都没有 = 「pm2 查询成功且有效列表里没有受管 app」且 health 不可达 → 停机例外:材料门代替运行身份(codex R1 P1-07)
    if [ -z "$why" ]; then
        if [ "${BM_PM2_PRESENT:-0}" = 1 ]; then
            if [ "$(bm_realpath "$BM_PM2_CWD")" != "$ls_app" ]; then
                why="pm2 进程 cwd $BM_PM2_CWD 不是 last-success 的 $ls_app"
            elif [ -n "$BM_HEALTH_SHA" ] && [ "$BM_HEALTH_SHA" != "$ls_sha" ]; then
                why="运行中的构建 sha ${BM_HEALTH_SHA:0:7}(来源 health)≠ last-success ${ls_sha:0:7}"
            elif [ "${BM_HEALTH_HTTP:-none}" != "none" ] && [ -z "$BM_HEALTH_SHA" ]; then
                why="/api/health 有响应(HTTP ${BM_HEALTH_HTTP})但没有有效的构建身份(非 JSON / build.sha 缺失)"
            elif [ -z "$BM_HEALTH_SHA" ] && [ "$BM_PM2_SHA" != "$ls_sha" ]; then
                why="pm2 进程的构建 sha ${BM_PM2_SHA:0:7}(来源 pm2)≠ last-success ${ls_sha:0:7}"
            else
                verdict="运行身份已核对(pm2 cwd + sha,来源 ${BM_RUN_SRC})"
            fi
        elif [ "${BM_HEALTH_HTTP:-none}" != "none" ]; then
            why="/api/health 有响应(HTTP ${BM_HEALTH_HTTP})但 pm2 没有受管进程 ${BM_APP_NAME}(查询 ${BM_PM2_QUERY}):端口上有别的进程或响应无有效身份,不能视为停机"
        elif [ "${BM_PM2_QUERY:-failed}" != "ok" ]; then
            why="pm2 查询失败(命令失败 / 输出不是有效列表),无法确认服务是否在运行"
        else
            # 停机例外:pm2 有效列表里没有 app、health 不可达 → 复用回滚材料门证明 last-success 仍可恢复
            if bm_rollback_material_check "$ls_release" "$ls_release/manifest.json"; then
                verdict="受管服务未运行(pm2 有效列表无 ${BM_APP_NAME}、/api/health 不可达),依据 last-success 及材料门确认回滚点"
            else
                why="受管服务未运行且 last-success 的材料不完整($BM_RB_REASON)"
            fi
        fi
    fi
    if [ -n "$why" ]; then
        if [ "${BM_NO_ROLLBACK_GUARANTEE:-0}" = 1 ]; then
            echo "    ⚠️  身份证据冲突($why);--no-rollback-guarantee 显式继续:prev=null,本次部署没有回滚点"
            BM_CAP_ROLLBACK=false
            return 0
        fi
        bm_fail "$BM_RC_IDENTITY" "既有部署的身份证据冲突:${why}。默认停止(last-success 与材料保留);人工核对 ./deploy.sh --status 后,确认放弃回滚保证可加 --no-rollback-guarantee 继续"
    fi
    BM_PREV_JSON="$(python3 - "$BM_LAST_SUCCESS" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
t = m.get("target") or {}
print(json.dumps({"txn_id": m.get("txn_id"), "kind": m.get("kind"), "ref": t.get("ref"), "code_sha": t.get("code_sha"),
                  "release": t.get("release"), "venv": t.get("venv"), "dist": t.get("dist")}, ensure_ascii=False))
PY
)"
    echo "    prev:${ls_ref}(${ls_sha:0:7},txn $(deploy_json_get "$BM_LAST_SUCCESS" txn_id ?))——current / html_dir 一致;${verdict}"
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
bm_txn_open() {  # txn_id [release_dir] [replace]  —— replace:允许原子覆盖已存在的 in-progress(只给失败部署 → 回滚事务的交接用)
    local txn="$1" release="${2-}" mode="${3-}" controller
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
    if [ -f "$BM_IN_PROGRESS" ] && [ "$mode" != "replace" ]; then
        bm_fail "$BM_RC_UNCLOSED" "开事务时发现 in-progress 仍在($(bm_manifest_get txn_id ?)),拒绝覆盖"
    fi
    python3 - "$txn" "$BM_TXN_KIND" "$BM_TXN_MODE" "${BM_ORCHESTRATOR_SHA:-}" "$controller" \
        "${BM_TXN_TARGET_JSON:-null}" "${BM_TXN_PREV_JSON:-null}" "${BM_TXN_CAPS_JSON:-{\}}" "${BM_TXN_SITE_JSON:-{\}}" \
        "${BM_TXN_RECOVER_FROM:-}" "${BM_TXN_DB_JSON:-{\}}" "$(bm_now)" \
        "${BM_CURRENT_TARGET:-}" "${BM_HTML_TARGET:-}" "${BM_PM2_CWD:-}" <<'PY' | deploy_json_write "$BM_IN_PROGRESS" \
        || bm_fail "$BM_RC_STEP" "写 in-progress.json 失败(磁盘 / 权限?),事务未落盘"
import json, os, sys
(txn, kind, mode, orch, controller, target, prev, caps, site, recover_from, db, opened, cur, html, pm2_cwd) = sys.argv[1:16]
caps_d = {"rollback": True, "db_restore": True, "reproducible": True}
caps_d.update(json.loads(caps or "{}"))
print(json.dumps({
    "txn_id": txn, "kind": kind, "mode": mode, "orchestrator_sha": orch, "controller": controller,
    "target": json.loads(target or "null"), "prev": json.loads(prev or "null"),
    "db": json.loads(db or "{}"), "site": json.loads(site or "{}"), "capabilities": caps_d,
    "stage": {"completed": "opened", "intent": "opened", "error": None},
    # 开事务时的现场(§4.3「现场证明」的对照值;prev=null 的首装 / 放弃保证也能据此证明未改宿主)
    "scene": {"current": cur, "html_dir": html, "pm2_cwd": os.path.realpath(pm2_cwd) if pm2_cwd else ""},
    "recover_from": recover_from or None, "opened_at": opened, "deployed_at": None,
}, ensure_ascii=False))
PY
    BM_TXN_OPEN=1
    bm_publish_entry
    echo "    事务 ${txn}(kind=$BM_TXN_KIND)已落盘:$BM_IN_PROGRESS;恢复入口 $BM_ENTRY"
}

# 晋升(§4.9 ⑤):in-progress + deployed_at → last-success;manifest 副本进 release;先核对入口再删 in-progress
#(覆盖「last-success 已写、入口尚未更新」的崩溃窗口)。晋升失败保留全部材料。
bm_txn_promote() {
    local release txn
    txn="$(bm_manifest_get txn_id ?)"
    release="$(bm_manifest_get target.release "")"
    deploy_json_set "$BM_IN_PROGRESS" deployed_at "$(bm_now)" || bm_fail "$BM_RC_STEP" "写 deployed_at 失败,事务保留"
    bm_stage_done promoted
    # release 事务的 manifest 副本进 release 目录;rollback 事务没有自己的 release,副本进材料目录(不覆盖目标 release 的原 manifest)
    if [ "$(bm_manifest_get kind "")" = "rollback" ]; then
        release="$(bm_manifest_get materials "$BM_STATE_DIR/txns/$txn")"
        mkdir -p "$release"
    fi
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
    bm_txn_archive_copy "$1"
    rm -f "$BM_IN_PROGRESS" || bm_fail "$BM_RC_STEP" "归档事务 $txn 失败"
    echo "    事务 $txn 已归档 → $BM_CLOSED_DIR/${txn}.json(材料保留)"
}
# 只写 closed/ 副本、不动 in-progress(原子交接的第一步,幂等;codex R1 P1-03):失败部署 → 回滚事务之间任一中断点,
# in-progress 仍是可被重选的失败部署
bm_txn_archive_copy() {  # reason
    local txn; txn="$(bm_manifest_get txn_id unknown)"
    mkdir -p "$BM_CLOSED_DIR"
    python3 - "$BM_IN_PROGRESS" "$BM_CLOSED_DIR/${txn}.json" "$(bm_now)" "$1" <<'PY' || bm_fail "$BM_RC_STEP" "写 closed/${txn}.json 失败"
import json, os, sys, tempfile
src, dst, at, reason = sys.argv[1:5]
data = json.load(open(src, encoding="utf-8"))
data.setdefault("closed", {"at": at, "reason": reason})
d = os.path.dirname(dst) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True); f.flush(); os.fsync(f.fileno())
os.replace(tmp, dst)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
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
        adopt) seq="$BM_STAGES_ADOPT"; first="$BM_FIRST_HOST_WRITE_ADOPT" ;;
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
    # 现场证明:current / html_dir / pm2 cwd 等于开事务时记录的 scene(旧 manifest 无 scene 时退回 prev 记录)
    if [ "$(bm_manifest_get scene "")" != "" ]; then
        [ "${BM_CURRENT_TARGET:-}" = "$(bm_manifest_get scene.current "")" ] || return 1
        [ -z "${NGINX_HTML_DIR:-}" ] || [ "${BM_HTML_TARGET:-}" = "$(bm_manifest_get scene.html_dir "")" ] || return 1
        local scene_pm2; scene_pm2="$(bm_manifest_get scene.pm2_cwd "")"
        if [ "${BM_PM2_PRESENT:-0}" = 1 ]; then
            [ "$(bm_realpath "$BM_PM2_CWD")" = "$scene_pm2" ] || return 1
        else
            [ -z "$scene_pm2" ] || return 1
        fi
        return 0
    fi
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
            echo "    发现未完成的收养事务 ${txn}(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)):先续做"
            bm_adopt_resume ;;
        rollback)
            # §4.3:rollback 一律续做——在同一把锁内(FD 9 继承)经稳定入口跑固化执行体,回滚开始时已由人确认过 yes;
            # 续做永不翻转目标;成功后重新采样现场再进入正向流程,失败则事务保留、本次退出
            echo "    发现未完成的回滚事务 ${txn}(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)):先经 $BM_ENTRY 续做同一目标"
            [ -x "$BM_ENTRY" ] || bm_fail "$BM_RC_UNCLOSED" "未完成的回滚事务 $txn 但恢复入口 $BM_ENTRY 不存在,人工检查"
            "$BM_ENTRY" --rollback --yes || bm_fail "$BM_RC_UNCLOSED" "续做回滚事务 $txn 未完成(事务保留;排查后 ./deploy.sh --rollback 再续做)"
            bm_sample_running ;;
        deploy)
            if bm_txn_host_untouched; then
                echo "    上次部署 $txn 在改动宿主之前就失败(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)),现场等于 prev:自动归档"
                bm_txn_archive "auto-closed: host untouched"
            else
                bm_fail "$BM_RC_UNCLOSED" "存在已改动宿主的未收口部署事务 ${txn}(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?) error=$(bm_manifest_get stage.error 无)):先 ./deploy.sh --rollback 回到上一版,或人工处理后 ./deploy.sh --discard-txn 归档"
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
        bm_fail "$BM_RC_USAGE" "事务 ${txn}(kind=$(bm_manifest_get kind ?))已改动宿主或无法证明未改动:确认已人工恢复现场后加 --yes 再归档,或先 ./deploy.sh --rollback"
    fi
    bm_txn_archive "discarded by operator"
}

# ── 能力检查与 checkout 前检查(§3.3 / §4.2)──
# 目标 tag 的 scripts/deploy-lib.sh 必须宣告 DORAMI_BAREMETAL_TXN,否则以 tag 模式切换会换掉本脚本并失去回滚入口
bm_pre_exec_check() {  # tag tag_sha(由 resolve_deploy_ref 在 checkout 前调用)
    local tag="$1" sha="$2"
    if ! git show "${sha}:scripts/deploy-lib.sh" 2>/dev/null | grep -qE '^DORAMI_BAREMETAL_TXN=[0-9]+'; then
        bm_fail "$BM_RC_NO_TXN_CAP" "目标 ${tag}(${sha:0:7})的部署脚本没有裸机事务能力(scripts/deploy-lib.sh 未宣告 DORAMI_BAREMETAL_TXN):以 tag 模式切换会换掉本脚本并失去回滚入口。改用当前编排器部署那份代码:  ./deploy.sh --code $tag"
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
# 收养检测(§4.11):无 last-success 且有既有部署证据 → 先收养旧形态安装,完成之前不进入任何正向部署
bm_ensure_adopted() {
    [ -f "$BM_LAST_SUCCESS" ] && return 0
    local ev; ev="$(bm_evidence_for_gate)"
    [ -n "$ev" ] || return 0
    # 证据来自 release 形态自己(首装事务失败后被 --discard-txn:current / html_dir 已是 symlink)而不是旧形态安装(仓库内 venv)时,
    # 没有可收养的对象:按首装候选继续(prev=null),不进收养
    if [ ! -x "$BM_REPO/${VENV_DIR:-venv}/bin/python" ] && { [ -L "$BM_CURRENT_LINK" ] || { [ -n "${NGINX_HTML_DIR:-}" ] && [ -L "$NGINX_HTML_DIR" ]; }; }; then
        echo "    无 last-success,证据(${ev})来自 release 形态的未晋升事务而非旧形态安装:不收养,按首装候选继续(本次没有回滚点)"
        return 0
    fi
    bm_adopt_refuse_dangling
    echo "    无 last-success 但有既有部署证据(${ev}):先收养旧形态安装(一次 PM2 重启的维护窗)"
    bm_adopt_main
    # 收养重启了服务、建了 release:证据快照与现场采样都要刷新
    bm_sample_running
    bm_snapshot_evidence
}

# 现场有悬空 symlink(上一次收养被人工清理过 releases/ 之类):旧形态现场已不完整,不能拿悬空目录当 dist 去收养(内网实测 R1;
# 自动收养与显式 --adopt 两个入口都在开事务前检查——codex 复检 P2)
bm_adopt_refuse_dangling() {
    local dangling=""
    [ -L "$BM_CURRENT_LINK" ] && [ ! -e "$BM_CURRENT_LINK" ] && dangling="${dangling} current"
    [ -n "${NGINX_HTML_DIR:-}" ] && [ -L "$NGINX_HTML_DIR" ] && [ ! -e "$NGINX_HTML_DIR" ] && dangling="${dangling} html_dir"
    [ -n "$dangling" ] || return 0
    bm_fail "$BM_RC_IDENTITY" "现场有悬空 symlink(${dangling# }):指向的 release 已不存在,旧形态现场不完整。恢复:rm 悬空的 html_dir 链接;把真实 dist 目录放回 html_dir(${NGINX_HTML_DIR:-<html_dir>}.adopt-* 是旧 dist 备份,或 frontend/dist);rm ${BM_CURRENT_LINK};./deploy.sh --status 核对后重跑"
}

# ══════════════════════ 收养(§4.11;第 4 层)══════════════════════
# kind=adopt 事务,锁内、可重入、每步阶段落盘;完成判据 = 旧服务已从 legacy release 启动并过两级健康门。
# venv 不移动(app/venv -> <repo>/venv,并移除 editable finder);dist 复制;nginx 当前生效集合快照作日后恢复源。
bm_adopt_prepare_env() {
    NGINX_BIN="${NGINX_BIN:-$(command -v nginx || true)}"
    [ -n "$NGINX_BIN" ] || bm_fail "$BM_RC_STEP" "收养需要 nginx 可执行文件在 PATH 里"
    command -v pm2 >/dev/null 2>&1 || { declare -F install_pm2 >/dev/null && install_pm2; }
    command -v pm2 >/dev/null 2>&1 || bm_fail "$BM_RC_STEP" "收养需要 pm2"
    [ -n "${NGINX_SITE_FILE:-}" ] || { declare -F resolve_nginx_site_file >/dev/null && resolve_nginx_site_file; }
    declare -F check_nginx_ssl_inputs >/dev/null && check_nginx_ssl_inputs
    [ -n "${CONFIG_FILE:-}" ] || bm_fail "$BM_RC_STEP" "收养需要 CONFIG_FILE"
    return 0
}
bm_adopt_main() {
    bm_adopt_prepare_env
    bm_sample_running
    if [ -f "$BM_IN_PROGRESS" ]; then
        [ "$(bm_manifest_get kind "")" = "adopt" ] || bm_fail "$BM_RC_UNCLOSED" "存在非收养的未收口事务 $(bm_manifest_get txn_id ?),先处理它"
        bm_adopt_resume
        return 0
    fi
    [ -f "$BM_LAST_SUCCESS" ] && bm_fail "$BM_RC_USAGE" "本机已是 release 形态(last-success 存在),不需要收养"
    bm_adopt_refuse_dangling
    # 前提(内网实测 R1):健康门只认 /api/health(v3.60.0 起才有)。服务在响应却给不出它(404 / 401 / HTML),
    # 说明运行的是更旧的代码,收养重启后必然过不了门——在开事务、动 venv 之前拒绝;完全无响应(已停机)交给 --adopt-sha 与健康门裁决
    if [ "${BM_HEALTH_HTTP:-none}" != none ] && [ -z "${BM_HEALTH_VERSION:-}" ]; then
        bm_fail "$BM_RC_IDENTITY" "运行中的服务对 /api/health 回了 HTTP ${BM_HEALTH_HTTP} 而不是构建身份:该端点 v3.60.0 起才有,新脚本的健康门依赖它。先按旧方式(alembic upgrade + 构建 + pm2 restart)把运行版本升到 ≥ v3.60.0,再跑本脚本收养"
    fi
    # ① 身份:运行中 sha(/api/health 或 pm2 env);取不到 → 要求 --adopt-sha
    local sha="" ref="" src="" reproducible=true
    if [ -n "$BM_RUN_SHA" ]; then
        sha="$BM_RUN_SHA"; ref="$BM_RUN_REF"; src="$BM_RUN_SRC"
        if [ -n "${BM_ADOPT_SHA:-}" ] && [ "$BM_ADOPT_SHA" != "$sha" ]; then
            bm_fail "$BM_RC_IDENTITY" "--adopt-sha ${BM_ADOPT_SHA:0:7} 与运行中的构建 sha ${sha:0:7}(来源 $src)不一致,拒绝"
        fi
    elif [ -n "${BM_ADOPT_SHA:-}" ]; then
        sha="$BM_ADOPT_SHA"; src="operator"; reproducible=false
    else
        bm_fail "$BM_RC_IDENTITY" "无法确定运行中代码的 sha(/api/health 无 build.sha、pm2 进程无 DORAMI_BUILD_SHA):请人工核对后 ./deploy.sh --adopt --adopt-sha <sha>"
    fi
    sha="$(git rev-parse -q --verify "${sha}^{commit}" 2>/dev/null)" || bm_fail "$BM_RC_IDENTITY" "运行中的 sha ${sha:0:7} 不在本地仓库(先 git fetch)"
    [ -n "$ref" ] || ref="$(git describe --tags --always "$sha" 2>/dev/null || echo "${sha:0:7}")"
    case "$ref" in *-dirty) reproducible=false ;; esac
    local venv_real; venv_real="$(bm_realpath "$BM_REPO/${VENV_DIR:-venv}")"
    [ -x "$venv_real/bin/python" ] || bm_fail "$BM_RC_IDENTITY" "旧形态 venv 不存在或无 python($venv_real):没有可收养的运行环境"
    echo "    收养身份:${ref}(${sha:0:7},来源 ${src});venv $venv_real;reproducible=$reproducible"
    # ② 开事务
    local txn release
    txn="legacy-$(bm_txn_id "${sha:0:7}")"
    release="$BM_RELEASES_DIR/$txn"
    BM_TXN_KIND="adopt"; BM_TXN_MODE="adopt"
    BM_TXN_TARGET_JSON="$(python3 -c 'import json, sys; print(json.dumps({"ref": sys.argv[1], "code_sha": sys.argv[2], "head_sha": sys.argv[3], "dirty": False, "release": sys.argv[4], "venv": sys.argv[5], "dist": sys.argv[4] + "/dist", "adopt_sha_source": sys.argv[6], "html_dir_moved_to": sys.argv[7]}))' \
        "$ref" "$sha" "$(git rev-parse HEAD)" "$release" "$venv_real" "$src" "${NGINX_HTML_DIR}.adopt-${txn}")"
    BM_TXN_PREV_JSON="null"
    BM_TXN_CAPS_JSON="$(python3 -c 'import json, sys; print(json.dumps({"rollback": True, "db_restore": True, "reproducible": sys.argv[1] == "true"}))' "$reproducible")"
    BM_TXN_SITE_JSON="$(python3 - "${NGINX_HTML_DIR:-}" "${NGINX_SITE_FILE:-}" "${NGINX_SITE_ENABLED_FILE:-}" "${NGINX_DEFAULT_SITE_FILE:-}" "${NGINX_SERVER_NAME:-_}" \
        "${NGINX_LISTEN_PORT:-80}" "${NGINX_ENABLE_SSL:-false}" "${NGINX_SSL_LISTEN_PORT:-443}" "${NGINX_SSL_REDIRECT:-true}" "${BACKEND_PROXY_HOST:-127.0.0.1}" "${BACKEND_PROXY_PORT:-8088}" "$BM_APP_NAME" "$NGINX_BIN" "$CONFIG_FILE" "${NGINX_RELEASES_DIR:-}" <<'PY'
import json, sys
k = ["html_dir", "site_file", "enabled_file", "default_site_file", "server_name", "listen_port", "enable_ssl", "ssl_listen_port",
     "ssl_redirect", "backend_host", "backend_port", "app_name", "nginx_bin", "config_file", "releases_dir"]
print(json.dumps(dict(zip(k, sys.argv[1:]))))
PY
)"
    BM_TXN_DB_JSON="{\"target\": $(bm_json_str "${BM_DB_PATH:-}"), \"snapshot\": null, \"snapshot_at\": null, \"rescue_snapshot\": null, \"heads_before\": [], \"plan\": null}"
    bm_txn_open "$txn" "$release"
    bm_adopt_run
}
bm_adopt_resume() {
    bm_adopt_prepare_env
    bm_sample_running   # 固化入口(--rollback / deploy-state/rollback)不经正向预检,这里必须自己采样(codex 复检 P1)
    # 运行中的代码已被人换掉(手工从仓库根起了新版本)时不能续做:续做会用事务记录的旧代码起服务 = 降级(内网实测 R1)
    local want; want="$(bm_manifest_get target.code_sha "")"
    if [ -n "${BM_RUN_SHA:-}" ]; then
        case "$want" in
            "$BM_RUN_SHA"*) ;;
            *) case "$BM_RUN_SHA" in
                   "$want"*) ;;
                   *) bm_fail "$BM_RC_IDENTITY" "运行中的代码 ${BM_RUN_SHA:0:7}(来源 ${BM_RUN_SRC})已不是收养事务记录的 ${want:0:7}:续做会把服务切回旧代码。先 ./deploy.sh --discard-txn 归档该事务(事务已进入停机阶段时加 --yes 确认现场已人工恢复),再 ./deploy.sh --here 重新收养当前运行的版本" ;;
               esac ;;
        esac
    fi
    echo "    续做收养事务 $(bm_manifest_get txn_id ?)(completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?))"
    BM_TXN_OPEN=1
    bm_adopt_run
}
# 收养的阶段主体(首次与续做共用;每步按 completed 判定是否需要做)
bm_adopt_run() {
    local seq="$BM_STAGES_ADOPT" release app sha ref venv_real dist moved
    release="$(bm_manifest_get target.release "")"; app="$release/app"
    sha="$(bm_manifest_get target.code_sha "")"; ref="$(bm_manifest_get target.ref "")"
    venv_real="$(bm_manifest_get target.venv "")"; dist="$release/dist"
    moved="$(bm_manifest_get target.html_dir_moved_to "")"
    [ -n "$release" ] && [ -n "$sha" ] || bm_fail "$BM_RC_STEP" "收养事务 manifest 不完整"

    if bm_stage_needed "$seq" code_archived; then
        bm_stage_intent code_archived
        rm -rf "$app"; bm_archive_code "$sha" "$release"
        bm_stage_done code_archived
    fi
    if bm_stage_needed "$seq" venv_ready; then
        bm_stage_intent venv_ready
        ln -sfn "$venv_real" "$app/venv"
        bm_mount_points "$release" "$venv_real" "$CONFIG_FILE"
        # 路径基准 = 原安装上下文(cwd=<repo>、PYTHONPATH=<repo>/src、<repo>/venv)的探针(§4.7 收养基准;codex R1 P1-01):
        # 工作树可能已是更新的代码,所以只比对两边都启用的存储根(见 bm_check_paths);持久化的是 legacy 上下文的探针。
        # 先核对、再动 venv(内网实测 R1:核对失败时不该已经改过运行中的 venv);收养不接受 DORAMI_DEPLOY_ACCEPT_PATH_CHANGE 重设
        local baseline
        baseline="$(bm_path_probe "$BM_REPO" "$venv_real" "$CONFIG_FILE")" || bm_fail "$BM_RC_PATH_PROBE" "原安装上下文的路径探针失败(旧代码的 config 无法加载?)"
        BM_PATHS_NO_REBASE=1
        bm_check_paths "$release" "$app" "$venv_real" "$CONFIG_FILE" "$baseline"
        unset BM_PATHS_NO_REBASE
        bm_persist_paths "$BM_PROBE_JSON"
        deploy_json_set "$BM_IN_PROGRESS" db.target "$BM_DB_TARGET" && deploy_json_set "$BM_IN_PROGRESS" db.backend "${BM_DB_BACKEND:-sqlite}" \
            || bm_fail "$BM_RC_STEP" "记录 DB 目标失败"
        bm_legacy_venv_detach "$venv_real" "$app"
        bm_stage_done venv_ready
    fi
    if bm_stage_needed "$seq" dist_copied; then
        bm_stage_intent dist_copied
        bm_adopt_copy_dist "$dist" "$release" "$moved"
        bm_stage_done dist_copied
    fi
    if bm_stage_needed "$seq" nginx_snapshotted; then
        bm_stage_intent nginx_snapshotted
        mkdir -p "$release/nginx"
        local affected=("$NGINX_SITE_FILE")
        [ "${NGINX_SITE_ENABLED_FILE:-}" != "$NGINX_SITE_FILE" ] && [ -n "${NGINX_SITE_ENABLED_FILE:-}" ] && affected+=("$NGINX_SITE_ENABLED_FILE")
        [ -n "${NGINX_DEFAULT_SITE_FILE:-}" ] && affected+=("$NGINX_DEFAULT_SITE_FILE")
        bm_nginx_record_state "$release/nginx/snapshot.json" "${affected[@]}" || bm_fail "$BM_RC_STEP" "记录 nginx 快照失败"
        printf '{"changes": []}\n' >"$release/nginx/changes.json"
        printf '%s\n' "${affected[@]}" >"$release/nginx/managed-paths"
        ${SUDO:-} "$NGINX_BIN" -T 2>/dev/null >"$release/nginx/effective.conf" || true
        bm_stage_done nginx_snapshotted
    fi
    if bm_stage_needed "$seq" process_stopped; then
        bm_stage_intent process_stopped        # 首次宿主改动(维护窗开始)
        bm_pm2_stop
        bm_stage_done process_stopped
    fi
    if bm_stage_needed "$seq" links_switched; then
        bm_stage_intent links_switched
        bm_adopt_switch_html "$dist" "$moved"
        deploy_atomic_symlink "$app" "$BM_CURRENT_LINK" || bm_fail "$BM_RC_STEP" "切换 current symlink 失败"
        bm_stage_done links_switched
    fi
    if bm_stage_needed "$seq" process_started; then
        bm_stage_intent process_started
        bm_pm2_start "$release" "$ref" "$sha" "$CONFIG_FILE"
        bm_stage_done process_started
    fi
    if bm_stage_needed "$seq" health_ok; then
        bm_stage_intent health_ok
        local version
        version="$(grep -o '__version__ = "[^"]*"' "$app/src/version.py" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')"
        if ! bm_health_gates "$dist" "$version" "$ref" "$sha"; then
            bm_alert_health_failed "$ref" "$sha" "收养后从 legacy release 启动未通过:$BM_GATE_REASON"
            bm_fail "$BM_RC_STEP" "收养未完成(事务保留,下次运行会续做;排查后可 ./deploy.sh --adopt 重试)"
        fi
        bm_stage_done health_ok
    fi
    # 完成序:核对 pm2 进程 cwd = legacy app → 发布入口 → last-success(kind=adopt, prev=null)
    bm_sample_running
    if [ "${BM_PM2_PRESENT:-0}" = 1 ] && [ "$(bm_realpath "$BM_PM2_CWD")" != "$(bm_realpath "$app")" ]; then
        bm_fail "$BM_RC_IDENTITY" "收养后 pm2 进程 cwd($BM_PM2_CWD)不是 legacy release($app),拒绝晋升"
    fi
    bm_stage_intent promoted
    bm_txn_promote
    echo "    收养完成:${ref}(${sha:0:7})现从 $app 运行;旧 html_dir 内容留在 ${moved:-<无>}"
}
# 从旧 venv 移除 editable finder(§4.11 ②):__editable__* / 指向 <repo>/src 或本项目的 .pth / 对应 dist-info;
# 之后在 legacy 上下文核对 sys.path 不含 <repo>/src
bm_legacy_venv_detach() {  # venv_real app
    local venv="$1" app="$2"
    python3 - "$venv" "$BM_REPO" <<'PY' || bm_fail "$BM_RC_STEP" "移除旧 venv 的 editable finder 失败"
import glob, os, re, shutil, sys
venv, repo = sys.argv[1], os.path.realpath(sys.argv[2])
removed = []
for sp in glob.glob(os.path.join(venv, "lib", "python*", "site-packages")):
    for p in glob.glob(os.path.join(sp, "__editable__*")):
        (shutil.rmtree if os.path.isdir(p) else os.unlink)(p); removed.append(p)
    for p in glob.glob(os.path.join(sp, "*.pth")):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "__editable__" in text or repo + "/src" in text or repo in text.replace("\\", "/") or "dorami" in os.path.basename(p).lower():
            os.unlink(p); removed.append(p)
    for p in glob.glob(os.path.join(sp, "*.dist-info")):
        name = os.path.basename(p).lower()
        if name.startswith("doramisourcearchive-") or name.startswith("mini-"):
            direct = os.path.join(p, "direct_url.json")
            if os.path.exists(direct) and "editable" in open(direct, encoding="utf-8", errors="replace").read():
                shutil.rmtree(p); removed.append(p)
for p in removed:
    print(f"    移除 editable 痕迹: {p}")
PY
    # 核对:legacy 上下文的 sys.path 不含工作树的 src(realpath 比较)
    (cd "$app" && PYTHONPATH="$app/src" "$venv/bin/python" - "$BM_REPO" <<'PY') || bm_fail "$BM_RC_IDENTITY" "legacy 上下文的 sys.path 仍指向工作树 src,收养不能保证运行副本独立"
import os, sys
bad = [p for p in sys.path if p and os.path.realpath(p) == os.path.realpath(os.path.join(sys.argv[1], "src"))]
if bad:
    print("sys.path 含工作树 src: " + ", ".join(bad), file=sys.stderr); sys.exit(1)
PY
    printf '{"kind": "legacy", "venv": %s, "interpreter": %s}\n' "$(bm_json_str "$venv")" "$(bm_json_str "$(bm_interpreter_identity "$venv/bin/python")")" >"$venv/inputs.json"
    echo "kind=legacy" >"$venv/.dorami-complete"
}
# dist:复制 html_dir/* → legacy dist(校验清单);html_dir 已是 symlink(上次中断后)则从其目标复制
bm_adopt_copy_dist() {  # dist release moved
    local dist="$1" release="$2" moved="$3" src="$NGINX_HTML_DIR"
    if [ -L "$NGINX_HTML_DIR" ]; then
        [ -f "$release/dist.sha256" ] && [ -d "$dist" ] && bm_verify_sha256 "$dist" "$release/dist.sha256" && return 0
        src="$(bm_realpath "$NGINX_HTML_DIR")"
    elif [ ! -d "$NGINX_HTML_DIR" ] && [ -d "$moved" ]; then
        src="$moved"
    fi
    [ -d "$src" ] || bm_fail "$BM_RC_IDENTITY" "旧形态发布目录不存在($NGINX_HTML_DIR):没有可收养的前端产物"
    rm -rf "$dist"; mkdir -p "$dist"
    ${SUDO:-} cp -R "$src/." "$dist/" || bm_fail "$BM_RC_STEP" "复制 $src 到 $dist 失败"
    ${SUDO:-} chown -R "$(id -u):$(id -g)" "$dist" 2>/dev/null || true
    chmod -R o+rX "$dist" 2>/dev/null || true
    local want got
    want="$(${SUDO:-} python3 -c 'import os, sys
n = b = 0
for dp, _, fns in os.walk(sys.argv[1]):
    for f in fns:
        p = os.path.join(dp, f)
        if not os.path.islink(p): n += 1; b += os.path.getsize(p)
print(n, b)' "$src")"
    got="$(python3 -c 'import os, sys
n = b = 0
for dp, _, fns in os.walk(sys.argv[1]):
    for f in fns:
        p = os.path.join(dp, f)
        if not os.path.islink(p): n += 1; b += os.path.getsize(p)
print(n, b)' "$dist")"
    [ "$want" = "$got" ] || bm_fail "$BM_RC_STEP" "复制后的 dist 文件数 / 字节数不符(源 $want,副本 $got)"
    bm_tree_sha256 "$dist" >"$release/dist.sha256"
    echo "    dist:复制 $src → ${dist}($got)"
}
# html_dir 真实目录 → mv 到 <html_dir>.adopt-<txn> + 建 symlink(两步;断电后按现场补建);跨文件系统只复制不 mv
bm_adopt_switch_html() {  # dist moved
    local dist="$1" moved="$2"
    if [ -L "$NGINX_HTML_DIR" ]; then
        [ "$(bm_realpath "$NGINX_HTML_DIR")" = "$(bm_realpath "$dist")" ] && return 0
    elif [ -d "$NGINX_HTML_DIR" ]; then
        if [ -e "$moved" ]; then
            bm_fail "$BM_RC_STEP" "$moved 已存在,无法挪走旧 html_dir;人工检查后重试"
        fi
        ${SUDO:-} mv "$NGINX_HTML_DIR" "$moved" || bm_fail "$BM_RC_STEP" "挪走旧 html_dir 失败(跨文件系统?人工 mv 后重跑)"
    fi
    ${SUDO:-} mkdir -p "$(dirname "$NGINX_HTML_DIR")"
    deploy_atomic_symlink "$dist" "$NGINX_HTML_DIR" ${SUDO:-} || bm_fail "$BM_RC_STEP" "建 html_dir symlink 失败"
    ensure_traversal_bits "$(bm_realpath "$dist")"
}

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
    # 排除集合 = 固定项 + 由有效配置(含环境覆盖)/ 挂点映射生成的项(BM_EXTRA_EXCLUDES 由 deploy.sh 按 ini + 环境算出;
    # 相对仓库根;不依赖目标 .gitignore,codex R1 P2-01)
    local excludes="venv venvs releases deploy-state logs data backups .venv frontend/node_modules frontend/dist current config/production.ini config/backend.ini .env"
    local extra p
    for p in "${VENV_DIR:-venv}" "$BM_RELEASES_DIR" "$BM_VENVS_DIR" "$BM_STATE_DIR" "$BM_SNAPSHOT_DIR" "${NGINX_HTML_DIR:-}" "${NGINX_RELEASES_DIR:-}" ${BM_EXTRA_EXCLUDES:-}; do
        [ -n "$p" ] || continue
        case "$p" in
            "$BM_REPO"/*) extra="${p#"$BM_REPO"/}" ;;
            /*) continue ;;
            .|./) continue ;;
            *) extra="${p#./}" ;;
        esac
        extra="${extra%%/}"
        [ -n "$extra" ] || continue
        # 配置把可变存储指进了已入库的源码路径(如 media_dir = src/media):不能把源码排除出快照——留给挂点冲突检查拒绝(exit 33)
        if git cat-file -e "HEAD:$extra" 2>/dev/null; then
            echo "    ⚠️  排除集合里的 $extra 是已入库的源码路径,不排除(挂点冲突会在目标上下文检查里拒绝)"
            continue
        fi
        excludes="$excludes $extra"
    done
    # 三步:① 临时 excludes 文件挡未跟踪文件(显式 pathspec 点名已 ignore 的路径会被 git 拒绝);② 目录项从临时 index 显式移除
    # (已跟踪的也一并移除);③ 数据库通配项按 ls-files 逐个显式移除——不依赖 ignore 优先级,`!` 否定规则也挡不住
    local tmp_index="$BM_STATE_DIR/.tmp-index-$$" excl_file="$BM_STATE_DIR/.tmp-excludes-$$"
    mkdir -p "$BM_STATE_DIR"
    { for p in $excludes; do printf '/%s\n' "$p"; done; printf '*.db\n*.sqlite\n*.sqlite3\n*-wal\n*-shm\n*-journal\n'; } >"$excl_file"
    tree="$( (export GIT_INDEX_FILE="$tmp_index"
              git read-tree HEAD \
              && git -c core.excludesFile="$excl_file" add -A . >/dev/null \
              && git rm -r -q --cached --ignore-unmatch -- $excludes >/dev/null \
              && git ls-files -z --cached | python3 -c '
import subprocess, sys
paths = [p for p in sys.stdin.buffer.read().split(b"\0") if p]
hits = [p for p in paths if p.lower().endswith((b".db", b".sqlite", b".sqlite3", b"-wal", b"-shm", b"-journal"))]
for i in range(0, len(hits), 200):
    subprocess.run([b"git", b"rm", b"-q", b"--cached", b"--"] + hits[i:i + 200], check=True)
' \
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
    # 快照 commit 是机器生成的:固定身份,不依赖生产机 / CI 的 git user 配置(否则 commit-tree 报 empty ident)
    snap="$(echo "dorami-deploy: dirty worktree snapshot for ${txn}" \
        | GIT_AUTHOR_NAME="dorami-deploy" GIT_AUTHOR_EMAIL="dorami-deploy@localhost" \
          GIT_COMMITTER_NAME="dorami-deploy" GIT_COMMITTER_EMAIL="dorami-deploy@localhost" \
          git commit-tree "$tree" -p "$head_sha")" \
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
    echo "    代码副本:${app}($(wc -l <"$2/app.sha256" | tr -d ' ') 个文件,sha ${sha:0:7})"
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
        echo "    venv:复用 ${BM_VENV_DIR}(指纹 $fp,inputs 一致)"
        return 0
    fi
    if [ -d "$BM_VENV_DIR" ]; then
        echo "    venv:$BM_VENV_DIR 是半成品或 inputs 不一致,删除重建"
        rm -rf "$BM_VENV_DIR"
    fi
    echo "    venv:新建 ${BM_VENV_DIR}(指纹 $fp)"
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
is_sqlite, db_path, backend, url_summary = False, "", "", ""
try:
    from sqlalchemy.engine import make_url
    u = make_url(url)
    backend = u.get_backend_name()
    is_sqlite = backend == "sqlite"
    if is_sqlite and u.database and u.database != ":memory:" and not u.database.startswith("file:"):
        db_path = rp(u.database)
    url_summary = u.render_as_string(hide_password=True) if not is_sqlite else ""
except Exception:
    if url.startswith("sqlite:///"):
        is_sqlite, db_path, backend = True, rp(url[len("sqlite:///"):]), "sqlite"
    else:
        backend = url.split(":", 1)[0] if ":" in url else "unknown"
        url_summary = backend + "://<unparsed>"
mutable = {
    "database": db_path,
    "media_dir": rp(g(cfg, "media", "media_dir")),
    "podcast_root": rp(g(cfg, "podcast_artifacts", "root_dir")),
    "backup_local_dir": rp(g(cfg, "backup", "local_dir")),
}
code_bound = {"catalog_path": rp(g(cfg, "taxonomy", "catalog_path"))}
print(json.dumps({"project_root": os.path.realpath(root), "is_sqlite": is_sqlite, "backend": backend, "url_summary": url_summary,
                  "mutable": mutable, "code_bound": code_bound}, sort_keys=True))
PY
}

# 目标上下文检查(§4.7):可变存储必须解析到 release 之外(靠挂点承接),缺挂点则按探针结果动态补建(仅当归档里无同名路径),
# 有基准时逐项相等才放行(exit 33)。输出 BM_PROBE_JSON、BM_DB_TARGET、BM_DB_IS_SQLITE。
bm_check_paths() {  # release app venv config_file baseline_json(空=无基准;{"mutable":…})
    local release="$1" app="$2" venv="$3" cfg="$4" baseline="$5" probe rel_real pass=0 fix
    rel_real="$(bm_realpath "$release")"
    BM_PATHS_REBASED=0
    # 有进展就继续补挂点,每轮之后必须重新探测;上限只防死循环(codex R1 P2-02)
    while :; do
        pass=$((pass + 1))
        [ "$pass" -le 8 ] || bm_fail "$BM_RC_PATH_PROBE" "路径探针:补建挂点超过 8 轮仍有可变存储解析到 release 内($fix)"
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
    if [ -n "$baseline" ]; then
        # 基准 = 上次成功部署 / 收养时**持久化**的探针结果(§4.7;codex R1 P1-02:不用当前 ini 重算,不跳过任何已知键);
        # 两边都有的键不等 → exit 33。运维确要迁移存储(换盘)的显式出口:DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 且
        # --no-rollback-guarantee 同用——本次记 prev=null / rollback=false(跨存储布局没有回滚保证),新布局成为之后的基准。
        local diff
        if ! diff="$(python3 - "$probe" "$baseline" <<'PY'
import json, os, sys
probe, base = json.loads(sys.argv[1]), json.loads(sys.argv[2])
db_target = os.environ.get("BM_BASELINE_DB_TARGET") or ""
bad = []
base_m = base.get("mutable")
if not isinstance(base_m, dict) or not base_m:
    bad.append("历史基准缺 mutable(无法确认历史存储布局)")
    base_m = {}
base_backend = base.get("backend") or "sqlite"
notes = []
for key, p in probe["mutable"].items():
    b = base_m.get(key)
    if key == "database":
        # 库路径永远严格:基准缺字段 / 为空都无法确认(codex R1 P1-02 复检)
        if b is None:
            bad.append(f"database: 历史基准无此字段,无法确认(目标={p})")
        elif b == "" and base_backend == "sqlite":
            bad.append("database: 历史基准的 SQLite 路径为空,无法确认")
        elif b and p != b:
            bad.append(f"database: 目标={p} 基准={b}")
        continue
    # 基准永远是某次探针的完整输出:缺键 = 产生基准的探针 / 代码不认识这个存储根(更旧),空串 = 该代码 / 配置未启用
    # (旧 config 常见的是固定键给空串,而不是缺键)。
    # 两边都启用才比对(内网实测 R1:收养 v3.58 安装、再升到带 backup 根的版本时,严格「缺即拒」会把正常升级挡死)
    if b is None:
        notes.append(f"{key}: 历史基准(更旧的代码)无此存储根,视为新增:{p or '<未启用>'}")
    elif b == "" and p:
        notes.append(f"{key}: 历史基准未启用,本次启用:{p}")
    elif p == "" and b:
        notes.append(f"{key}: 历史基准 {b},目标代码 / 配置未启用(退役或停用)")
    elif p != b:
        bad.append(f"{key}: 目标={p} 基准={b}")
for key, b in base_m.items():
    if key not in probe["mutable"]:
        notes.append(f"{key}: 目标代码不再报告该存储根(基准 {b})")
if db_target and probe["mutable"].get("database", "") != db_target:
    bad.append(f"database: 目标={probe['mutable'].get('database')} last-success.db.target={db_target}")
if probe.get("backend") != base_backend:
    bad.append(f"数据库后端: 目标={probe.get('backend')} 基准={base_backend}")
if bad:
    print("\n".join("    ✗ " + b for b in bad)); sys.exit(1)
print("\n".join("    · " + n for n in notes))
PY
)"; then
            if [ "${BM_PATHS_NO_REBASE:-0}" = 1 ]; then
                echo "$diff" >&2
                bm_fail "$BM_RC_PATH_PROBE" "收养:legacy release 上下文与原安装上下文的存储路径不一致(见上),收养不接受重设基准;核对配置与挂点后重跑"
            elif [ "${DORAMI_DEPLOY_ACCEPT_PATH_CHANGE:-0}" = 1 ] && [ "${BM_NO_ROLLBACK_GUARANTEE:-0}" = 1 ]; then
                echo "$diff"
                echo "    ⚠️  路径基准变化已由 DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 + --no-rollback-guarantee 显式接受:此次重设存储基准,没有跨存储布局的回滚保证(prev=null);搬数据是运维自己的事"
                BM_PATHS_REBASED=1
                BM_PATHS_REBASED_FROM="$baseline"
            elif [ "${DORAMI_DEPLOY_ACCEPT_PATH_CHANGE:-0}" = 1 ]; then
                echo "$diff" >&2
                bm_fail "$BM_RC_PATH_PROBE" "路径基准变化:DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 必须与 --no-rollback-guarantee 同用(跨存储布局的回滚点不成立)"
            else
                echo "$diff" >&2
                bm_fail "$BM_RC_PATH_PROBE" "路径探针与基准不一致(见上):目标代码在 release 上下文里会读写另一份存储。核对配置;确要迁移存储位置请先自行搬数据,再 DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 ./deploy.sh … --no-rollback-guarantee 显式重设基准"
            fi
        else
            echo "    路径探针:与基准一致"
            [ -n "$diff" ] && echo "$diff"
        fi
    else
        echo "    路径探针:无基准(首装),可变存储均在 release 之外"
    fi
    BM_PROBE_JSON="$probe"
    BM_DB_TARGET="$(printf '%s' "$probe" | python3 -c 'import json, sys; print(json.load(sys.stdin)["mutable"]["database"])')"
    BM_DB_IS_SQLITE="$(printf '%s' "$probe" | python3 -c 'import json, sys; print("1" if json.load(sys.stdin)["is_sqlite"] else "0")')"
    BM_DB_BACKEND="$(printf '%s' "$probe" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("backend") or "")')"
    BM_DB_URL_SUMMARY="$(printf '%s' "$probe" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("url_summary") or "")')"
    export BM_PROBE_JSON BM_DB_TARGET BM_DB_IS_SQLITE BM_DB_BACKEND BM_DB_URL_SUMMARY BM_PATHS_REBASED
}
# 把探针结果持久化进事务 manifest(paths.mutable 是之后部署的基准;codex R1 P1-02)
bm_persist_paths() {  # probe_json [rebased_from_json]
    python3 - "$1" "${2:-}" <<'PY' | deploy_json_write "$BM_STATE_DIR/.tmp-paths-$$.json" || bm_fail "$BM_RC_STEP" "序列化 paths 失败"
import json, sys
probe = json.loads(sys.argv[1])
out = {"mutable": probe.get("mutable") or {}, "code_bound": probe.get("code_bound") or {}, "project_root": probe.get("project_root"),
       "backend": probe.get("backend"), "url_summary": probe.get("url_summary")}
if sys.argv[2]:
    out["rebased_from"] = json.loads(sys.argv[2]).get("mutable")
print(json.dumps(out, ensure_ascii=False))
PY
    deploy_json_set "$BM_IN_PROGRESS" paths "$(cat "$BM_STATE_DIR/.tmp-paths-$$.json")" json || bm_fail "$BM_RC_STEP" "记录 paths 失败"
    rm -f "$BM_STATE_DIR/.tmp-paths-$$.json"
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
# 错误分类(§4.8 error 行;codex R1 P1-04):只有「损坏」才是契约例外——SQLITE_CORRUPT(11)/ SQLITE_NOTADB(26)或
# integrity_check 明确非 ok 记 db_corrupt;权限 / 磁盘 / I/O / 无法归类一律 db_access,不放行跳过救援
def classify(exc):
    import sqlite3
    orig = getattr(exc, "orig", exc)
    code = getattr(orig, "sqlite_errorcode", None)
    msg = str(orig).lower()
    if isinstance(orig, sqlite3.DatabaseError) and not isinstance(orig, sqlite3.OperationalError):
        if code in (11, 26) or "not a database" in msg or "malformed" in msg:
            return "db_corrupt"
    if code in (11, 26) or "file is not a database" in msg or "database disk image is malformed" in msg:
        return "db_corrupt"
    return "db_access"
try:
    url = URL.create("sqlite", database=f"file:{quote(db_path, safe='/')}", query={"mode": "ro", "uri": "true"})
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def _ro(conn, _rec):
        cur = conn.cursor(); cur.execute("PRAGMA query_only=ON"); cur.close()
    with engine.connect() as conn:
        integrity = conn.exec_driver_sql("PRAGMA integrity_check").scalar()
        current_heads = sorted(MigrationContext.configure(conn).get_current_heads())
        has_tables = "articles" in inspect(conn).get_table_names()
    engine.dispose()
except Exception as exc:
    finish("error", f"当前库无法读取: {type(getattr(exc, 'orig', exc)).__name__}: {getattr(exc, 'orig', exc)}", error_kind=classify(exc))
if integrity != "ok":
    finish("error", f"当前库 integrity_check 未通过: {integrity}", error_kind="db_corrupt")
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
    echo "    dist:${BM_DIST_DIR}($(wc -l <"$release/dist.sha256" | tr -d ' ') 个文件)"
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
    local deadline remaining; deadline=$(( $(date +%s) + budget ))   # 三道门共用一个 deadline(codex R1 P2-06)
    BM_GATE_REASON=""
    echo "    健康门 ①:后端身份(http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health 五项)..."
    if ! deploy_wait_healthy "http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health" "$version" "$ref" "$sha" "$budget" "$attempts"; then
        BM_GATE_REASON="后端身份门:${DEPLOY_HEALTH_LAST_VERDICT:-无响应}(预算 ${budget}s,尝试 ${DEPLOY_HEALTH_ATTEMPT} 次)"
        return 1
    fi
    remaining=$(( deadline - $(date +%s) ))
    [ "$remaining" -gt 0 ] || { BM_GATE_REASON="后端身份门通过时预算(${budget}s)已耗尽,站点链路门未执行"; return 1; }
    echo "    健康门 ②:站点链路(经 nginx:index + 主资产摘要 + /api/health;剩余预算 ${remaining}s)..."
    local out
    if ! out="$(DORAMI_DEPLOY_PROBE_MAX_TIME="$remaining" bm_site_probe "$dist" "$version" "$ref" "$sha" 2>&1)"; then
        BM_GATE_REASON="站点链路门:${out}"
        return 1
    fi
    local stable="${DORAMI_DEPLOY_STABLE_SECONDS:-10}" pid0 pid1 t0 body verdict
    if [ "$stable" -gt 0 ]; then
        remaining=$(( deadline - $(date +%s) ))
        [ "$remaining" -ge "$stable" ] || { BM_GATE_REASON="预算剩余 ${remaining}s 不足以完成 ${stable}s 稳定窗(DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS=${budget})"; return 1; }
        echo "    健康门 ③:稳定窗 ${stable}s(PID 不变、/api/health 持续一致)..."
        pid0="$(bm_pm2_pid)"
        t0=$(date +%s)
        local nap max_time
        while [ $(( $(date +%s) - t0 )) -lt "$stable" ]; do
            remaining=$(( deadline - $(date +%s) ))
            [ "$remaining" -gt 0 ] || { BM_GATE_REASON="稳定窗未能在预算内完成(DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS=${budget})"; return 1; }
            nap=$(( remaining < 2 ? remaining : 2 )); sleep "$nap"
            remaining=$(( deadline - $(date +%s) ))
            [ "$remaining" -gt 0 ] || { BM_GATE_REASON="稳定窗未能在预算内完成(DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS=${budget})"; return 1; }
            max_time=$(( remaining < 8 ? remaining : 8 ))
            body="$(curl -fsS --connect-timeout 3 --max-time "$max_time" -H 'Cache-Control: no-cache' "http://${BACKEND_PROXY_HOST}:${BACKEND_PROXY_PORT}/api/health?_=$(date +%s)" 2>/dev/null || true)"
            verdict="$(printf '%s' "$body" | deploy_health_verdict "$version" "$ref" "$sha")"
            [ "$verdict" = "ok" ] || { BM_GATE_REASON="稳定窗内 /api/health 变化:${verdict}"; return 1; }
            pid1="$(bm_pm2_pid)"
            [ "$pid1" = "$pid0" ] || { BM_GATE_REASON="稳定窗内进程重启(pid ${pid0:-?} → ${pid1:-?})"; return 1; }
        done
        [ $(date +%s) -le "$deadline" ] || { BM_GATE_REASON="稳定窗未能在预算内完成(DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS=${budget})"; return 1; }
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
import time
deadline = time.time() + float(os.environ.get("DORAMI_DEPLOY_PROBE_MAX_TIME") or 60)
def fetch(url, extra=()):
    fd, tmp = tempfile.mkstemp(prefix="dorami-probe-"); os.close(fd)
    try:
        left = deadline - time.time()
        if left <= 0:
            return None, "", "", "预算耗尽"
        max_time = str(max(1, int(min(20, left))))
        r = subprocess.run(["curl", "-sS", "--connect-timeout", "5", "--max-time", max_time, "-o", tmp, "-w", "%{http_code}\t%{content_type}\t%{redirect_url}",
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
    # 跳转必须落到前面验证过的 HTTPS 入口(origin + 同一路径),不是「随便一个 https」(codex R1 P2-05)
    from urllib.parse import urlsplit
    b, code, ctype, loc = fetch(http_base + "/")
    if b is None or code not in ("301", "308"):
        fail(f"HTTP 入口未 301 到 https(得到 {code or '不可达'})")
    want_host = name or "127.0.0.1"
    want_port = int(ssl_port) if str(ssl_port).isdigit() else 443
    parts = urlsplit(loc)
    got_port = parts.port or (443 if parts.scheme == "https" else None)
    if parts.scheme != "https" or (parts.hostname or "").lower() != want_host.lower() or got_port != want_port or (parts.path or "/") != "/":
        fail(f"HTTP 入口的 Location {loc!r} 不是本站 HTTPS 入口 https://{want_host}{'' if want_port == 443 else ':' + str(want_port)}/")
print("ok")
PY
}

# ── 清理与引用集合(§4.12)──
bm_referenced_releases() {  # → 一行一个 release 目录(realpath)
    python3 - "$BM_STATE_DIR" "$BM_CLOSED_DIR" "$BM_CURRENT_LINK" "${NGINX_HTML_DIR:-}" "${BM_PM2_CWD:-}" "$HOME/.pm2/dump.pm2" "${DORAMI_DEPLOY_CLOSED_KEEP_DAYS:-7}" "$BM_RELEASES_DIR" <<'PY'
import glob, json, os, sys, time
state, closed, current, html, pm2_cwd, dump, keep_days, releases_dir = sys.argv[1:9]
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
    if rf and os.path.isdir(os.path.join(releases_dir, rf)):
        add_release(os.path.join(releases_dir, rf))
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
# 快照引用集合(codex R1 P1-05):in-progress / last-success / 保留期内 closed 与 txns / 引用 release 的 manifest 里
# db.snapshot / db.rescue_snapshot / db.restore_source 所在目录一律保留;数量策略只作用于集合之外
bm_referenced_snapshot_dirs() {  # referenced_releases(一行一个) → 一行一个目录
    python3 - "$BM_STATE_DIR" "$BM_CLOSED_DIR" "$BM_STATE_DIR/txns" "${DORAMI_DEPLOY_CLOSED_KEEP_DAYS:-7}" "$1" <<'PY'
import glob, json, os, sys, time
state, closed, txns, keep_days, referenced = sys.argv[1:6]
files = [os.path.join(state, n) for n in ("in-progress.json", "last-success.json")]
now = time.time()
for pattern in (os.path.join(closed, "*.json"), os.path.join(txns, "*", "manifest.json")):
    for p in glob.glob(pattern):
        if now - os.path.getmtime(p) <= float(keep_days) * 86400:
            files.append(p)
for rel in referenced.split("\n"):
    if rel:
        files.append(os.path.join(rel, "manifest.json"))
dirs = set()
for p in files:
    try:
        db = json.load(open(p, encoding="utf-8")).get("db") or {}
    except Exception:
        continue
    for key in ("snapshot", "rescue_snapshot", "restore_source"):
        v = db.get(key)
        if v:
            dirs.add(os.path.dirname(os.path.realpath(v)))
for d in sorted(dirs):
    print(d)
PY
}
bm_cleanup() {  # 按数量清理引用集合之外的 release(3)/ venv(2)/ 快照(10);失败非致命
    local keep_rel="${DORAMI_DEPLOY_KEEP_RELEASES:-3}" keep_venv="${DORAMI_DEPLOY_KEEP_VENVS:-2}" keep_snap="${DORAMI_DEPLOY_KEEP_SNAPSHOTS:-10}"
    local referenced snap_refs out line txn
    referenced="$(bm_referenced_releases)"
    snap_refs="$(bm_referenced_snapshot_dirs "$referenced")"
    out="$(python3 - "$BM_RELEASES_DIR" "$BM_VENVS_DIR" "$BM_SNAPSHOT_DIR" "$keep_rel" "$keep_venv" "$keep_snap" "$referenced" "$BM_REPO" "$snap_refs" <<'PY' || echo "    ⚠️  清理未完成(maintenance_failed),下次重试"
import json, os, shutil, subprocess, sys
releases, venvs, snaps, keep_rel, keep_venv, keep_snap, referenced, repo, snap_refs = sys.argv[1:10]
refs = set(l for l in referenced.split("\n") if l)
snap_keep = set(l for l in snap_refs.split("\n") if l)
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
# 快照目录(按 txn):被任何事务 manifest 的 db 字段引用的目录不删(独立于 release 引用集合)
ref_txns = set(os.path.basename(r) for r in refs)
if os.path.isdir(snaps):
    sd = [os.path.join(snaps, d) for d in os.listdir(snaps) if os.path.isdir(os.path.join(snaps, d))]
    others = sorted([d for d in sd if os.path.basename(d) not in ref_txns and os.path.realpath(d) not in snap_keep],
                    key=os.path.getmtime, reverse=True)
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

# ══════════════════════ 回滚(§4.8 / §4.10;第 5 层)══════════════════════
# 回滚只在固化执行体里跑(controller/rollback.sh → bm_controller_main → bm_rollback_main):不 checkout、不出网、不构建。
# 站点参数一律取自目标 release 的 manifest.site(部署当时的值),不读工作树 ini。

# 事务 id 对应的材料目录:release 事务 = releases/<txn>;rollback 事务 = deploy-state/txns/<txn>
bm_txn_dir() {  # txn_id
    if [ -d "$BM_RELEASES_DIR/$1" ]; then echo "$BM_RELEASES_DIR/$1"; else echo "$BM_STATE_DIR/txns/$1"; fi
}
# 按 manifest.site 装配站点变量(回滚 / --status 的目标上下文)
bm_apply_site_from_manifest() {  # manifest_file
    local m="$1"
    NGINX_HTML_DIR="$(deploy_json_get "$m" site.html_dir "${NGINX_HTML_DIR:-}")"
    NGINX_SITE_FILE="$(deploy_json_get "$m" site.site_file "${NGINX_SITE_FILE:-}")"
    NGINX_SITE_ENABLED_FILE="$(deploy_json_get "$m" site.enabled_file "${NGINX_SITE_ENABLED_FILE:-}")"
    NGINX_DEFAULT_SITE_FILE="$(deploy_json_get "$m" site.default_site_file "${NGINX_DEFAULT_SITE_FILE:-}")"
    NGINX_SERVER_NAME="$(deploy_json_get "$m" site.server_name "${NGINX_SERVER_NAME:-_}")"
    NGINX_LISTEN_PORT="$(deploy_json_get "$m" site.listen_port "${NGINX_LISTEN_PORT:-80}")"
    NGINX_ENABLE_SSL="$(deploy_json_get "$m" site.enable_ssl "${NGINX_ENABLE_SSL:-false}")"
    NGINX_SSL_LISTEN_PORT="$(deploy_json_get "$m" site.ssl_listen_port "${NGINX_SSL_LISTEN_PORT:-443}")"
    NGINX_SSL_REDIRECT="$(deploy_json_get "$m" site.ssl_redirect "${NGINX_SSL_REDIRECT:-true}")"
    BACKEND_PROXY_HOST="$(deploy_json_get "$m" site.backend_host "${BACKEND_PROXY_HOST:-127.0.0.1}")"
    BACKEND_PROXY_PORT="$(deploy_json_get "$m" site.backend_port "${BACKEND_PROXY_PORT:-8088}")"
    NGINX_BIN="$(deploy_json_get "$m" site.nginx_bin "${NGINX_BIN:-}")"
    [ -x "$NGINX_BIN" ] || NGINX_BIN="$(command -v nginx || echo "${NGINX_BIN:-nginx}")"
    NGINX_RELEASES_DIR="$(deploy_json_get "$m" site.releases_dir "${NGINX_RELEASES_DIR:-}")"
    BM_RB_CONFIG_FILE="$(deploy_json_get "$m" site.config_file "${CONFIG_FILE:-$BM_REPO/config/production.ini}")"
    export NGINX_HTML_DIR NGINX_SITE_FILE NGINX_SITE_ENABLED_FILE NGINX_DEFAULT_SITE_FILE NGINX_SERVER_NAME NGINX_LISTEN_PORT \
        NGINX_ENABLE_SSL NGINX_SSL_LISTEN_PORT NGINX_SSL_REDIRECT BACKEND_PROXY_HOST BACKEND_PROXY_PORT NGINX_BIN NGINX_RELEASES_DIR
}

# 目标选择(§4.10,与 --status 共用;只读):
#   ① 未完成的 kind=rollback 事务 → 续做同一 target;② 已改宿主的 in-progress deploy → 目标 = 其 prev,recover_from = 该事务;
#   ③ 否则 last-success.prev,recover_from = last-success;④ last-success 本身是 rollback 的结果 → 默认拒绝,--to 只接受相邻的那个。
# 输出 BM_RB_MODE(resume|failed-deploy|last-success|forward)、BM_RB_TARGET_MANIFEST、BM_RB_TARGET_RELEASE、BM_RB_RECOVER_FROM、
# BM_RB_RECOVER_DIR、BM_RB_ROLLED_BACK_JSON、BM_RB_RESTORE_SOURCE、BM_RB_RESTORE_AT;失败返回非零并设 BM_RB_REASON。
bm_select_rollback_target() {  # [--to txn]
    local to="${1:-}" src kind
    BM_RB_MODE=""; BM_RB_TARGET_MANIFEST=""; BM_RB_TARGET_RELEASE=""; BM_RB_RECOVER_FROM=""; BM_RB_RECOVER_DIR=""
    BM_RB_ROLLED_BACK_JSON="null"; BM_RB_RESTORE_SOURCE=""; BM_RB_RESTORE_AT=""; BM_RB_REASON=""
    if [ -f "$BM_IN_PROGRESS" ]; then
        kind="$(bm_manifest_get kind "")"
        case "$kind" in
            rollback)
                BM_RB_MODE="resume"
                BM_RB_TARGET_RELEASE="$(bm_manifest_get target.release "")"
                BM_RB_TARGET_MANIFEST="$BM_RB_TARGET_RELEASE/manifest.json"
                BM_RB_RECOVER_FROM="$(bm_manifest_get recover_from "")"
                BM_RB_RECOVER_DIR="$(bm_txn_dir "$BM_RB_RECOVER_FROM")"
                BM_RB_ROLLED_BACK_JSON="$(bm_manifest_get prev null)"
                BM_RB_RESTORE_SOURCE="$(bm_manifest_get db.restore_source "")"
                return 0 ;;
            adopt)
                BM_RB_REASON="收养事务 $(bm_manifest_get txn_id ?) 未完成:./deploy.sh --rollback 或恢复入口会先续做收养(完成前没有回滚点)"; return 1 ;;
            deploy)
                if bm_txn_host_untouched; then
                    BM_RB_REASON="untouched"; return 2
                fi
                src="$BM_IN_PROGRESS"; BM_RB_MODE="failed-deploy" ;;
            *) BM_RB_REASON="in-progress.json 的 kind 不可识别"; return 1 ;;
        esac
    else
        [ -f "$BM_LAST_SUCCESS" ] || { BM_RB_REASON="没有 last-success:真首装或尚未收养,无回滚点"; return 1; }
        src="$BM_LAST_SUCCESS"; BM_RB_MODE="last-success"
        if [ "$(deploy_json_get "$src" kind "")" = "rollback" ]; then
            local prev_txn prev_rel; prev_txn="$(deploy_json_get "$src" prev.txn_id "")"; prev_rel="$(deploy_json_get "$src" prev.release "")"
            if [ -z "$to" ]; then
                # 只有晋升过的相邻事务(release 里有 manifest.json)才可 --to;失败过的部署只给 --code(codex R1 P2-04)
                if [ -n "$prev_rel" ] && [ -f "$prev_rel/manifest.json" ]; then
                    BM_RB_REASON="上一版是刚被回滚掉的 $(deploy_json_get "$src" prev.ref ?)($(deploy_json_get "$src" prev.code_sha - | cut -c1-7));要回去请  ./deploy.sh --code $(deploy_json_get "$src" prev.code_sha ?)  或  ./deploy.sh --rollback --to ${prev_txn:-?}"
                else
                    BM_RB_REASON="上一版是刚被回滚掉的 $(deploy_json_get "$src" prev.ref ?)($(deploy_json_get "$src" prev.code_sha - | cut -c1-7),该事务未曾晋升);要回去请  ./deploy.sh --code $(deploy_json_get "$src" prev.code_sha ?)"
                fi
                return 1
            fi
            [ "$to" = "$prev_txn" ] || { BM_RB_REASON="--to 只接受相邻的那个事务(${prev_txn:-无});更早的版本用 ./deploy.sh --code <sha>"; return 1; }
            BM_RB_MODE="forward"
        elif [ -n "$to" ]; then
            [ "$to" = "$(deploy_json_get "$src" prev.txn_id "")" ] || { BM_RB_REASON="--to 只接受相邻的那个事务($(deploy_json_get "$src" prev.txn_id 无))"; return 1; }
        fi
    fi
    # 目标 = src.prev;recover_from = src 自己
    local prev_release; prev_release="$(deploy_json_get "$src" prev.release "")"
    if [ -z "$prev_release" ] || [ "$(deploy_json_get "$src" prev "null")" = "null" ]; then
        BM_RB_REASON="事务 $(deploy_json_get "$src" txn_id ?) 没有回滚点(prev=null:首装或放弃了回滚保证);更早的版本用 ./deploy.sh --code <sha>"
        return 1
    fi
    BM_RB_TARGET_RELEASE="$prev_release"
    BM_RB_TARGET_MANIFEST="$prev_release/manifest.json"
    BM_RB_RECOVER_FROM="$(deploy_json_get "$src" txn_id "")"
    BM_RB_RECOVER_DIR="$(bm_txn_dir "$BM_RB_RECOVER_FROM")"
    BM_RB_ROLLED_BACK_JSON="$(python3 - "$src" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
t = m.get("target") or {}
print(json.dumps({"txn_id": m.get("txn_id"), "kind": m.get("kind"), "ref": t.get("ref"), "code_sha": t.get("code_sha"),
                  "release": t.get("release"), "venv": t.get("venv"), "dist": t.get("dist")}, ensure_ascii=False))
PY
)"
    BM_RB_RESTORE_SOURCE="$(deploy_json_get "$src" db.snapshot "")"
    BM_RB_RESTORE_AT="$(deploy_json_get "$src" db.snapshot_at "")"
    return 0
}

# 材料门(§4.10):目标 release 的代码 / dist / venv 凭据 / nginx 快照全部在
bm_rollback_material_check() {  # target_release target_manifest
    local rel="$1" m="$2" dist venv
    [ -f "$m" ] || { BM_RB_REASON="目标 release 没有 manifest.json($m):该事务未曾晋升,不能作回滚目标(用 ./deploy.sh --code <sha> 正向部署)"; return 1; }
    [ -f "$rel/controller/rollback.sh" ] || { BM_RB_REASON="目标 release 缺固化执行体($rel/controller/rollback.sh)"; return 1; }
    [ -f "$rel/app.sha256" ] && bm_verify_sha256 "$rel/app" "$rel/app.sha256" || { BM_RB_REASON="目标代码副本与 app.sha256 不符或缺失($rel)"; return 1; }
    dist="$(deploy_json_get "$m" target.dist "$rel/dist")"
    [ -d "$dist" ] && [ -f "$rel/dist.sha256" ] && bm_verify_sha256 "$dist" "$rel/dist.sha256" || { BM_RB_REASON="目标 dist 与 dist.sha256 不符或缺失($dist)"; return 1; }
    venv="$(deploy_json_get "$m" target.venv "")"
    [ -n "$venv" ] && [ -f "$venv/.dorami-complete" ] && [ -x "$venv/bin/python" ] || { BM_RB_REASON="目标 venv 缺完成凭据或不可用($venv)"; return 1; }
    [ -f "$rel/nginx/snapshot.json" ] || { BM_RB_REASON="目标 release 没有 nginx 配置集合快照($rel/nginx/snapshot.json)"; return 1; }
    return 0
}

# DB 处置(§4.8 分流表):输出 BM_RB_DB_ACTION(none|migrate|restore)与 BM_RB_DB_PLAN_JSON、BM_RB_SKIP_RESCUE_REASON;
# 返回非零 = 拒绝(BM_RB_REASON,BM_RB_RC 为退出码)。跳过救援快照只允许「--restore-db 且当前库属损坏类」(codex R1 P1-04)。
bm_rollback_db_decide() {  # target_manifest db_path restore_db(0/1) no_rescue(0/1) [backend]
    local m="$1" db="$2" restore="$3" no_rescue="$4" backend="${5:-sqlite}" rel app venv plan status kind pending
    rel="$(deploy_json_get "$m" target.release "")"; app="$rel/app"; venv="$(deploy_json_get "$m" target.venv "")"
    BM_RB_DB_ACTION="none"; BM_RB_RC=1; BM_RB_SKIP_RESCUE_REASON=""
    if [ "$backend" != "sqlite" ] || [ -z "$db" ]; then
        BM_RB_DB_PLAN_JSON="{\"status\": \"n/a\", \"detail\": \"非 SQLite 库(${backend:-unknown}):回滚不处置数据库\", \"pending_count\": 0}"
        if [ "$restore" = 1 ] || [ "$no_rescue" = 1 ]; then
            BM_RB_REASON="数据库不是 SQLite(${backend:-unknown}):--restore-db / --no-rescue-snapshot 不适用,本形态不处置外部库"; BM_RB_RC="$BM_RC_USAGE"; return 1
        fi
        return 0
    fi
    if [ "$no_rescue" = 1 ] && [ "$restore" != 1 ]; then
        BM_RB_REASON="--no-rescue-snapshot 只能与 --restore-db 同用"; BM_RB_RC="$BM_RC_USAGE"; return 1
    fi
    plan="$(bm_db_plan "$app" "$venv" "$BM_RB_CONFIG_FILE" "$db")" || { BM_RB_REASON="迁移计划执行失败"; return 1; }
    BM_RB_DB_PLAN_JSON="$plan"
    status="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')"
    pending="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["pending_count"])')"
    kind="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("error_kind", ""))')"
    if [ "$no_rescue" = 1 ] && [ "$status" != "error" ]; then
        BM_RB_REASON="当前库可读(${status}):健康库必须做救援快照,--no-rescue-snapshot 只给 integrity_check 失败 / 文件不是数据库的损坏库"; BM_RB_RC="$BM_RC_USAGE"; return 1
    fi
    case "$status" in
        compatible)
            if [ "$pending" -gt 0 ]; then BM_RB_DB_ACTION="migrate"; else BM_RB_DB_ACTION="none"; fi
            if [ "$restore" = 1 ]; then
                BM_RB_REASON="目标代码认识当前库(compatible),不需要 --restore-db;去掉该参数重试"; BM_RB_RC="$BM_RC_USAGE"; return 1
            fi
            return 0 ;;
        incompatible)
            if [ "$restore" = 1 ]; then
                [ -n "$BM_RB_RESTORE_SOURCE" ] && [ -f "$BM_RB_RESTORE_SOURCE" ] \
                    || { BM_RB_REASON="需要恢复库,但记录的快照不存在($BM_RB_RESTORE_SOURCE)"; BM_RB_RC="$BM_RC_NO_TARGET"; return 1; }
                BM_RB_DB_ACTION="restore"; return 0
            fi
            BM_RB_REASON="当前库领先于目标代码的迁移图($(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["detail"])'));回滚需要恢复快照:
    快照文件:${BM_RB_RESTORE_SOURCE:-<无>}
    快照时刻:${BM_RB_RESTORE_AT:-?}    现在:$(bm_now)
    丢失窗口:快照时刻之后写入的数据在恢复后不存在
  确认后加 --restore-db 执行(默认拒绝,确认前不改现场)"
            BM_RB_RC="$BM_RC_NEED_RESTORE_DB"; return 1 ;;
        fresh|legacy_adoption_required)
            BM_RB_REASON="迁移计划报 ${status}(库缺失 / 老库形态):现场异常,请人工核对 $db"; BM_RB_RC="$BM_RC_NO_TARGET"; return 1 ;;
        error)
            local detail; detail="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["detail"])')"
            case "$kind" in
                db_corrupt)
                    if [ "$restore" = 1 ] && [ "$no_rescue" = 1 ]; then
                        [ -n "$BM_RB_RESTORE_SOURCE" ] && [ -f "$BM_RB_RESTORE_SOURCE" ] \
                            || { BM_RB_REASON="当前库已损坏且记录的快照不存在($BM_RB_RESTORE_SOURCE)"; BM_RB_RC="$BM_RC_NO_TARGET"; return 1; }
                        BM_RB_DB_ACTION="restore"; BM_RB_SKIP_RESCUE_REASON="db_corrupt"; return 0
                    fi
                    BM_RB_REASON="当前库已损坏(${detail}):受控路径 = --restore-db --no-rescue-snapshot(跳过救援快照,用记录的快照 ${BM_RB_RESTORE_SOURCE:-<无>} 恢复)"
                    BM_RB_RC="$BM_RC_NEED_RESTORE_DB"; return 1 ;;
                db_access)
                    BM_RB_REASON="当前库无法访问(${detail}):权限 / 磁盘 / I/O 错误不属契约例外,先修复环境再回滚(不提供跳过救援的路径)"
                    BM_RB_RC="$BM_RC_NO_TARGET"; return 1 ;;
                *)
                    BM_RB_REASON="目标迁移图读取失败:${detail}"; BM_RB_RC="$BM_RC_NO_TARGET"; return 1 ;;
            esac ;;
        *) BM_RB_REASON="迁移计划状态不可识别: $status"; return 1 ;;
    esac
}

# --rollback 主流程(controller 内,已 cd BM_REPO、已 init paths)
bm_rollback_main() {  # [--restore-db] [--yes] [--no-rescue-snapshot] [--to txn]
    local restore=0 yes=0 no_rescue=0 to=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --restore-db) restore=1 ;;
            --yes) yes=1 ;;
            --no-rescue-snapshot) no_rescue=1 ;;
            --to) [ $# -ge 2 ] || bm_fail "$BM_RC_USAGE" "--to 需要 <txn>"; to="$2"; shift ;;
            *) bm_fail "$BM_RC_USAGE" "未知参数: $1" ;;
        esac
        shift
    done
    export DORAMI_DEPLOY_LOCK_BUSY_RC="$BM_RC_LOCK"
    acquire_deploy_lock
    bm_install_traps
    bm_reconcile_crash_window
    # 现场采样需要站点参数:先用 last-success / in-progress 的 site 块装配
    local site_src=""
    [ -f "$BM_IN_PROGRESS" ] && site_src="$BM_IN_PROGRESS"
    [ -z "$site_src" ] && [ -f "$BM_LAST_SUCCESS" ] && site_src="$BM_LAST_SUCCESS"
    [ -n "$site_src" ] && bm_apply_site_from_manifest "$site_src"
    bm_sample_running
    local rc=0
    bm_select_rollback_target "$to" || rc=$?
    if [ "$rc" = 2 ]; then
        echo "    上次部署 $(bm_manifest_get txn_id ?) 在改动宿主之前就失败,现场等于 last-success:归档,无需回滚"
        bm_txn_archive "auto-closed by --rollback: host untouched"
        echo "当前运行的仍是 last-success $(deploy_json_get "$BM_LAST_SUCCESS" target.ref ?);要回到更早一版请再次 ./deploy.sh --rollback"
        exit 0
    fi
    [ "$rc" = 0 ] || bm_fail "$BM_RC_NO_TARGET" "$BM_RB_REASON"
    bm_apply_site_from_manifest "$BM_RB_TARGET_MANIFEST"
    bm_rollback_material_check "$BM_RB_TARGET_RELEASE" "$BM_RB_TARGET_MANIFEST" || bm_fail "$BM_RC_NO_TARGET" "回滚材料门未通过:$BM_RB_REASON"
    local t_ref t_sha db_path
    t_ref="$(deploy_json_get "$BM_RB_TARGET_MANIFEST" target.ref ?)"; t_sha="$(deploy_json_get "$BM_RB_TARGET_MANIFEST" target.code_sha "")"
    if [ "$BM_RB_MODE" = "resume" ]; then
        db_path="$(bm_manifest_get db.target "")"
        BM_RB_DB_ACTION="$(bm_manifest_get db.action none)"
        echo "    续做回滚事务 $(bm_manifest_get txn_id ?) → ${t_ref}(${t_sha:0:7});completed=$(bm_manifest_get stage.completed ?) intent=$(bm_manifest_get stage.intent ?)"
        # 续做时去掉 --no-rescue-snapshot = 改为做救援快照(只能收紧,不能事后加上跳过);
        # 只在救援阶段尚未完成时改写决策——阶段已结束的,记录必须如实反映当时发生的动作(复检 2 新 P2),不回退阶段补造救援
        if [ "$(bm_manifest_get db.no_rescue false)" = "true" ] && [ "$no_rescue" != 1 ]; then
            if bm_stage_needed "$BM_STAGES_ROLLBACK" db_rescued; then
                deploy_json_set "$BM_IN_PROGRESS" db.no_rescue false json && deploy_json_set "$BM_IN_PROGRESS" db.skip_rescue_reason null json \
                    || bm_fail "$BM_RC_STEP" "更新救援选项失败"
                echo "    本次未传 --no-rescue-snapshot:续做改为先做救援快照"
            else
                echo "    本次未传 --no-rescue-snapshot,但救援阶段已按当时的跳过决策结束(理由:$(bm_manifest_get db.skip_rescue_reason ?)):保留记录,不补做救援"
            fi
        elif [ "$no_rescue" = 1 ] && [ "$(bm_manifest_get db.no_rescue false)" != "true" ]; then
            bm_fail "$BM_RC_USAGE" "该回滚事务开始时没有跳过救援,续做不能事后加上 --no-rescue-snapshot"
        fi
        BM_TXN_OPEN=1
        bm_rollback_run
        return 0
    fi
    # DB 目标与后端:被回滚事务记录的 db.target / db.backend(与快照 / 计划共用同一个值,§4.7)
    local src_manifest; src_manifest="$([ -f "$BM_IN_PROGRESS" ] && echo "$BM_IN_PROGRESS" || echo "$BM_LAST_SUCCESS")"
    local db_backend; db_backend="$(deploy_json_get "$src_manifest" db.backend "$(deploy_json_get "$BM_RB_TARGET_MANIFEST" db.backend sqlite)")"
    db_path="$(deploy_json_get "$src_manifest" db.target "")"
    [ -n "$db_path" ] || db_path="$(deploy_json_get "$BM_RB_TARGET_MANIFEST" db.target "")"
    if ! bm_rollback_db_decide "$BM_RB_TARGET_MANIFEST" "$db_path" "$restore" "$no_rescue" "$db_backend"; then
        bm_fail "${BM_RB_RC:-1}" "$BM_RB_REASON"
    fi
    local rb_ref rb_sha
    rb_ref="$(printf '%s' "$BM_RB_ROLLED_BACK_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("ref") or "?")')"
    rb_sha="$(printf '%s' "$BM_RB_ROLLED_BACK_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("code_sha") or "")')"
    echo "=================================================="
    echo "  回滚:${rb_ref}(${rb_sha:0:7},事务 ${BM_RB_RECOVER_FROM})  →  ${t_ref}(${t_sha:0:7})"
    echo "  模式:${BM_RB_MODE};目标 release:${BM_RB_TARGET_RELEASE}"
    case "$BM_RB_DB_ACTION" in
        none) echo "  数据库:目标代码认识当前库,不覆盖(先做救援快照)" ;;
        migrate) echo "  数据库:目标代码认识当前库,回滚将向前补 $(printf '%s' "$BM_RB_DB_PLAN_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["pending_count"])') 个迁移(先做救援快照)" ;;
        restore) echo "  数据库:--restore-db,用快照 ${BM_RB_RESTORE_SOURCE}(${BM_RB_RESTORE_AT:-?})覆盖当前库$( [ "$no_rescue" = 1 ] && echo '(不做救援快照)' || echo '(先做救援快照)')" ;;
    esac
    echo "  动作:撤销 ${BM_RB_RECOVER_FROM} 的 nginx 变更集 → 恢复目标 nginx 集合 → pm2 delete → 救援快照 → DB 处置 → 切链接 → pm2 start → pm2 save → nginx reload → 两级健康门"
    echo "=================================================="
    if [ "$yes" != 1 ]; then
        if [ -t 0 ]; then
            printf '输入 yes 确认回滚: '
            local answer; read -r answer
            [ "$answer" = "yes" ] || bm_fail "$BM_RC_USAGE" "未确认,回滚取消(现场未改动)"
        else
            bm_fail "$BM_RC_USAGE" "非交互环境需要 --yes 确认回滚(现场未改动)"
        fi
    fi
    # 开回滚事务(kind=rollback;无新 release,材料目录 deploy-state/txns/<txn>);失败部署的 release 材料与 nginx 变更集
    # 留在 releases/<txn>,回滚事务以 recover_from 引用它
    local txn; txn="rb-$(bm_txn_id "${t_sha:0:7}")"
    mkdir -p "$BM_STATE_DIR/txns/$txn/nginx"
    BM_TXN_KIND="rollback"; BM_TXN_MODE="rollback"
    BM_TXN_TARGET_JSON="$(python3 - "$BM_RB_TARGET_MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
t = dict(m.get("target") or {})
t["txn_id"] = m.get("txn_id")
print(json.dumps(t, ensure_ascii=False))
PY
)"
    BM_TXN_PREV_JSON="$BM_RB_ROLLED_BACK_JSON"
    BM_TXN_CAPS_JSON="$(deploy_json_get "$BM_RB_TARGET_MANIFEST" capabilities '{}')"
    BM_TXN_SITE_JSON="$(deploy_json_get "$BM_RB_TARGET_MANIFEST" site '{}')"
    BM_TXN_RECOVER_FROM="$BM_RB_RECOVER_FROM"
    BM_TXN_DB_JSON="$(python3 -c 'import json, sys; print(json.dumps({"target": sys.argv[1] or None, "backend": sys.argv[7] or "sqlite", "snapshot": None, "snapshot_at": None, "rescue_snapshot": None, "restore_source": sys.argv[2] or None, "restore_source_at": sys.argv[3] or None, "action": sys.argv[4], "no_rescue": sys.argv[5] == "1", "skip_rescue_reason": sys.argv[8] or None, "plan": json.loads(sys.argv[6]), "heads_before": []}))' \
        "$db_path" "$BM_RB_RESTORE_SOURCE" "$BM_RB_RESTORE_AT" "$BM_RB_DB_ACTION" "$no_rescue" "$BM_RB_DB_PLAN_JSON" "$db_backend" "${BM_RB_SKIP_RESCUE_REASON:-}")"
    BM_CONTROLLER_DIR="${BM_CONTROLLER_DIR:-$BM_LIB_DIR}"
    # 原子交接(codex R1 P1-03):失败部署的 manifest 先复制到 closed/(幂等),回滚事务再原子覆盖 in-progress.json——
    # 任一中断点 in-progress 都还能被重选(B 未收口 → 目标仍是 A)或已是回滚事务
    if [ "$BM_RB_MODE" = "failed-deploy" ]; then
        bm_txn_archive_copy "superseded by rollback $txn"
        bm_txn_open "$txn" "" replace
    else
        bm_txn_open "$txn"
    fi
    deploy_json_set "$BM_IN_PROGRESS" materials "$BM_STATE_DIR/txns/$txn" || true
    # 回滚事务晋升后是之后部署的基准:把目标 release 持久化的 paths 一并带上
    deploy_json_set "$BM_IN_PROGRESS" paths "$(deploy_json_get "$BM_RB_TARGET_MANIFEST" paths 'null')" json || true
    bm_rollback_run
}

# 回滚阶段主体(首次与续做共用)
bm_rollback_run() {
    local seq="$BM_STAGES_ROLLBACK" target_rel app venv dist t_ref t_sha recover_dir db action no_rescue txn materials
    txn="$(bm_manifest_get txn_id ?)"; materials="$(bm_manifest_get materials "$BM_STATE_DIR/txns/$txn")"
    target_rel="$(bm_manifest_get target.release "")"; app="$target_rel/app"; venv="$(bm_manifest_get target.venv "")"
    dist="$(bm_manifest_get target.dist "$target_rel/dist")"
    t_ref="$(bm_manifest_get target.ref "")"; t_sha="$(bm_manifest_get target.code_sha "")"
    recover_dir="$(bm_txn_dir "$(bm_manifest_get recover_from "")")"
    db="$(bm_manifest_get db.target "")"; action="$(bm_manifest_get db.action none)"; no_rescue="$(bm_manifest_get db.no_rescue false)"
    bm_apply_site_from_manifest "$BM_IN_PROGRESS"

    if bm_stage_needed "$seq" nginx_reverted; then
        bm_stage_intent nginx_reverted
        # 回滚自身的 nginx 动作也记变更集(受影响集合 = 目标快照与 recover_from 变更集的并集)
        python3 - "$recover_dir/nginx/changes.json" "$target_rel/nginx/snapshot.json" <<'PY' | sort -u >"$materials/nginx/affected"
import json, sys
for p in sys.argv[1:]:
    try:
        for row in json.load(open(p, encoding="utf-8")).get("changes", []):
            print(row["path"])
    except Exception:
        pass
PY
        local affected=()
        while IFS= read -r line; do [ -n "$line" ] && affected+=("$line"); done <"$materials/nginx/affected"
        [ -f "$materials/nginx/changes.json" ] || bm_nginx_record_state "$materials/nginx/changes.json" ${affected[@]+"${affected[@]}"} \
            || bm_fail "$BM_RC_STEP" "记录回滚的 nginx 变更集失败"
        if [ -f "$recover_dir/nginx/changes.json" ]; then
            echo "    nginx:撤销 $(basename "$recover_dir") 的变更集"
            bm_nginx_apply_state "$recover_dir/nginx/changes.json" || bm_fail "$BM_RC_STEP" "撤销 nginx 变更集失败"
        fi
        bm_stage_done nginx_reverted
    fi
    if bm_stage_needed "$seq" nginx_restored; then
        bm_stage_intent nginx_restored
        echo "    nginx:恢复目标 release 的配置集合快照"
        bm_nginx_apply_state "$target_rel/nginx/snapshot.json" || bm_fail "$BM_RC_STEP" "恢复 nginx 快照失败"
        ${SUDO:-} "$NGINX_BIN" -t || bm_fail "$BM_RC_STEP" "恢复后 nginx -t 未通过"
        bm_stage_done nginx_restored
    fi
    if bm_stage_needed "$seq" process_stopped; then
        bm_stage_intent process_stopped
        bm_pm2_stop
        bm_stage_done process_stopped
    fi
    if bm_stage_needed "$seq" db_rescued; then
        bm_stage_intent db_rescued
        local backend; backend="$(bm_manifest_get db.backend sqlite)"
        if [ "$backend" != "sqlite" ] || [ -z "$db" ]; then
            echo "    救援快照:非 SQLite 库(${backend}),不处置"
        elif [ "$no_rescue" = "true" ]; then
            # 跳过救援只在「落盘的允许理由仍成立」时执行(codex R1 P1-04):此刻再算一次计划,必须仍是 db_corrupt
            local recheck rk
            recheck="$(bm_db_plan "$app" "$venv" "$BM_RB_CONFIG_FILE" "$db")" || bm_fail "$BM_RC_STEP" "重核当前库失败"
            rk="$(printf '%s' "$recheck" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d.get("error_kind") if d["status"] == "error" else d["status"])')"
            [ "$rk" = "db_corrupt" ] && [ "$(bm_manifest_get db.skip_rescue_reason "")" = "db_corrupt" ] \
                || bm_fail "$BM_RC_STEP" "跳过救援快照的前提不再成立(当前库此刻状态:${rk}):拒绝覆盖;不带 --no-rescue-snapshot 再次 ./deploy.sh --rollback --yes --restore-db 续做,会先做救援快照"
            echo "    救援快照:--no-rescue-snapshot 显式跳过(当前库损坏,重核仍为 db_corrupt)"
        elif [ -f "$db" ] && [ -z "$(bm_manifest_get db.rescue_snapshot "")" ]; then
            local rescue="$BM_SNAPSHOT_DIR/$txn/$(basename "$db" | sed 's/\.[^.]*$//').rescue.sqlite"
            sqlite_snapshot "$db" "$rescue" || bm_fail "$BM_RC_STEP" "救援快照失败: $rescue"
            deploy_json_set "$BM_IN_PROGRESS" db.rescue_snapshot "$rescue" && deploy_json_set "$BM_IN_PROGRESS" db.snapshot "$rescue" \
                && deploy_json_set "$BM_IN_PROGRESS" db.snapshot_at "$(bm_now)" || bm_fail "$BM_RC_STEP" "记录救援快照失败"
            echo "    救援快照:${rescue}(只创建一次)"
        elif [ -n "$(bm_manifest_get db.rescue_snapshot "")" ]; then
            echo "    救援快照:已存在 $(bm_manifest_get db.rescue_snapshot),不重复创建"
        fi
        bm_stage_done db_rescued
    fi
    if bm_stage_needed "$seq" db_restored; then
        bm_stage_intent db_restored
        case "$action" in
            restore) bm_db_restore "$(bm_manifest_get db.restore_source "")" "$db" "$app" "$venv" ;;
            migrate)
                echo "    数据库:向前补迁移(目标上下文)"
                bm_db_migrate "$app" "$venv" "$BM_RB_CONFIG_FILE" ;;
            *) echo "    数据库:不覆盖" ;;
        esac
        bm_stage_done db_restored
    fi
    if bm_stage_needed "$seq" links_switched; then
        bm_stage_intent links_switched
        bm_switch_links "$app" "$dist"
        bm_stage_done links_switched
    fi
    if bm_stage_needed "$seq" process_started; then
        bm_stage_intent process_started
        bm_pm2_start "$target_rel" "$t_ref" "$t_sha" "$BM_RB_CONFIG_FILE"
        bm_stage_done process_started
    fi
    ensure_nginx_running_or_reload
    if bm_stage_needed "$seq" health_ok; then
        bm_stage_intent health_ok
        local version
        version="$(grep -o '__version__ = "[^"]*"' "$app/src/version.py" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')"
        if ! bm_health_gates "$dist" "$version" "$t_ref" "$t_sha"; then
            bm_alert_health_failed "$t_ref" "$t_sha" "回滚后健康门未通过:$BM_GATE_REASON"
            bm_fail "$BM_RC_STEP" "回滚未完成(事务保留;再次 ./deploy.sh --rollback 续做同一目标,或 pm2 logs $BM_APP_NAME 排查)"
        fi
        bm_stage_done health_ok
    fi
    bm_stage_intent promoted
    bm_txn_promote
    bm_cleanup
    echo ""
    echo "Rollback complete. 现在运行 ${t_ref}(${t_sha:0:7});被回滚掉的版本可用 ./deploy.sh --code <sha> 重新部署"
}

# 恢复协议(§4.8 --restore-db):校验恢复源(可打开、integrity_check、alembic_version 在目标图内)→ 同目录临时文件写入 + fsync →
# 记录「即将替换」→ 删 -wal/-shm → rename → 核对库身份。写进程已在 process_stopped 阶段退出。
bm_db_restore() {  # source db app venv
    local src="$1" db="$2" app="$3" venv="$4" plan status
    [ -f "$src" ] || bm_fail "$BM_RC_NO_TARGET" "恢复源不存在: $src"
    python3 - "$src" <<'PY' || bm_fail "$BM_RC_STEP" "恢复源 integrity_check 未通过: $src"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
row = con.execute("PRAGMA integrity_check").fetchone(); con.close()
sys.exit(0 if row and row[0] == "ok" else 1)
PY
    plan="$(bm_db_plan "$app" "$venv" "$BM_RB_CONFIG_FILE" "$src")" || bm_fail "$BM_RC_STEP" "恢复源的迁移计划失败"
    status="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')"
    case "$status" in
        compatible|legacy_adoption_required) ;;
        *) bm_fail "$BM_RC_NO_TARGET" "恢复源 $src 的 alembic_version 不在目标图内(${status}),拒绝用它覆盖" ;;
    esac
    if pm2 describe "$BM_APP_NAME" >/dev/null 2>&1; then
        bm_fail "$BM_RC_STEP" "写进程 $BM_APP_NAME 仍在,拒绝替换库文件"
    fi
    echo "    数据库:用快照 $src 覆盖 $db"
    deploy_json_set "$BM_IN_PROGRESS" db.replacing true json || bm_fail "$BM_RC_STEP" "记录「即将替换」失败"
    python3 - "$src" "$db" <<'PY' || bm_fail "$BM_RC_STEP" "替换库文件失败"
import os, shutil, sys
src, db = sys.argv[1], sys.argv[2]
d = os.path.dirname(db) or "."
os.makedirs(d, exist_ok=True)
tmp = os.path.join(d, ".restore-" + os.path.basename(db) + ".tmp")
with open(src, "rb") as fi, open(tmp, "wb") as fo:
    shutil.copyfileobj(fi, fo, 1 << 20); fo.flush(); os.fsync(fo.fileno())
for suffix in ("-wal", "-shm"):
    try:
        os.unlink(db + suffix)
    except FileNotFoundError:
        pass
os.replace(tmp, db)
dfd = os.open(d, os.O_RDONLY); os.fsync(dfd); os.close(dfd)
PY
    deploy_json_set "$BM_IN_PROGRESS" db.replacing false json || true
    deploy_json_set "$BM_IN_PROGRESS" db.restored_at "$(bm_now)" || true
    plan="$(bm_db_plan "$app" "$venv" "$BM_RB_CONFIG_FILE" "$db")" || bm_fail "$BM_RC_STEP" "替换后迁移计划失败"
    status="$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')"
    case "$status" in compatible|legacy_adoption_required) ;; *) bm_fail "$BM_RC_STEP" "替换后库身份核对失败(${status})" ;; esac
    if [ "$(printf '%s' "$plan" | python3 -c 'import json, sys; print(json.load(sys.stdin)["pending_count"])')" -gt 0 ]; then
        echo "    恢复源落后于目标代码,补迁移(目标上下文)"
        bm_db_migrate "$app" "$venv" "$BM_RB_CONFIG_FILE"
    fi
}

# --status 的回滚段(与 --rollback 同一目标选择与计划段,只读)
bm_status_rollback_section() {
    echo "== 回滚预判 =="
    local site_src="" rc=0
    [ -f "$BM_IN_PROGRESS" ] && site_src="$BM_IN_PROGRESS"
    [ -z "$site_src" ] && [ -f "$BM_LAST_SUCCESS" ] && site_src="$BM_LAST_SUCCESS"
    [ -n "$site_src" ] && bm_apply_site_from_manifest "$site_src"
    bm_select_rollback_target "" || rc=$?
    if [ "$rc" = 2 ]; then
        echo "   未收口的部署未改动宿主:--rollback 会归档它,无需回滚"; return 0
    fi
    if [ "$rc" != 0 ]; then
        echo "   无回滚目标:$BM_RB_REASON"; return 0
    fi
    echo "   模式 ${BM_RB_MODE};目标 $(deploy_json_get "$BM_RB_TARGET_MANIFEST" target.ref ?) ($(deploy_json_get "$BM_RB_TARGET_MANIFEST" target.code_sha - | cut -c1-7)) release=$BM_RB_TARGET_RELEASE;recover_from=$BM_RB_RECOVER_FROM"
    if ! bm_rollback_material_check "$BM_RB_TARGET_RELEASE" "$BM_RB_TARGET_MANIFEST"; then
        echo "   ⚠️  材料门:$BM_RB_REASON"; return 0
    fi
    bm_apply_site_from_manifest "$BM_RB_TARGET_MANIFEST"
    local db backend src_m
    src_m="$([ -f "$BM_IN_PROGRESS" ] && echo "$BM_IN_PROGRESS" || echo "$BM_LAST_SUCCESS")"
    db="$(deploy_json_get "$src_m" db.target "$(deploy_json_get "$BM_RB_TARGET_MANIFEST" db.target "")")"
    backend="$(deploy_json_get "$src_m" db.backend "$(deploy_json_get "$BM_RB_TARGET_MANIFEST" db.backend sqlite)")"
    if bm_rollback_db_decide "$BM_RB_TARGET_MANIFEST" "$db" 0 0 "$backend"; then
        case "$BM_RB_DB_ACTION" in
            none) [ "$backend" = "sqlite" ] && echo "   DB:目标认识当前库,不覆盖" || echo "   DB:非 SQLite 库(${backend}),回滚不处置" ;;
            migrate) echo "   DB:目标认识当前库,回滚将向前补迁移" ;;
        esac
    else
        echo "   DB:$(printf '%s' "$BM_RB_REASON" | head -1)"
        [ "${BM_RB_RC:-}" = "$BM_RC_NEED_RESTORE_DB" ] && echo "       (需要 --restore-db;快照 ${BM_RB_RESTORE_SOURCE:-<无>} @ ${BM_RB_RESTORE_AT:-?})"
    fi
}

# ── 共用小助手(controller 里没有 deploy.sh,这些在库里)──
truthy() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}
ensure_nginx_running_or_reload() {
    if pgrep -x nginx >/dev/null 2>&1; then
        ${SUDO:-} "$NGINX_BIN" -s reload
        return
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^nginx\.service'; then
        ${SUDO:-} systemctl start nginx
    elif command -v service >/dev/null 2>&1 && service nginx status >/dev/null 2>&1; then
        ${SUDO:-} service nginx start
    else
        ${SUDO:-} "$NGINX_BIN"
    fi
}
ensure_traversal_bits() {  # dir:逐级补 others 的 x 位(只补穿越位);补不上只告警
    local dir="$1" perms
    while [ "$dir" != "/" ] && [ -n "$dir" ]; do
        perms="$(${SUDO:-} python3 -c 'import os, stat, sys; print(stat.filemode(os.stat(sys.argv[1]).st_mode))' "$dir" 2>/dev/null || echo "")"
        case "$perms" in
            "") ;;
            *x|*t) ;;
            *)
                echo "Adding o+x to $dir (nginx worker needs directory traversal)"
                ${SUDO:-} chmod o+x "$dir" \
                    || echo "    ⚠️  无法给 $dir 加穿越位;若 nginx 读不到站点文件,把 dist 放到宿主目录:[nginx] releases_dir = /var/www/dorami-releases"
                ;;
        esac
        dir="$(dirname "$dir")"
    done
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
    [ -L "$BM_CURRENT_LINK" ] && [ ! -e "$BM_CURRENT_LINK" ] && echo "   ⚠️  current 悬空:指向的目录不存在"
    [ -n "${NGINX_HTML_DIR:-}" ] && echo "   html_dir ${NGINX_HTML_DIR} -> ${BM_HTML_TARGET:-<无>}"
    [ -n "${NGINX_HTML_DIR:-}" ] && [ -L "$NGINX_HTML_DIR" ] && [ ! -e "$NGINX_HTML_DIR" ] && echo "   ⚠️  html_dir 悬空:指向的目录不存在,站点此刻没有前端"
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
        echo "   数据库: backend=$(deploy_json_get "$BM_LAST_SUCCESS" db.backend ?) target=$(deploy_json_get "$BM_LAST_SUCCESS" db.target -)"
        [ "$(deploy_json_get "$BM_LAST_SUCCESS" paths.rebased_from "")" != "" ] && echo "   ⚠️  该次部署显式重设了存储基准(paths.rebased_from),跨存储布局没有回滚保证"
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
    [ -x "$BM_ENTRY" ] && echo "   ${BM_ENTRY}(已发布)" || echo "   ${BM_ENTRY}(未发布)"
}

# controller/rollback.sh 的入口:--status / --rollback;in-progress 是 kind=adopt 时按 §4.2 续做收养(codex R1 P1-06)
bm_controller_main() {
    BM_CONTROLLER_DIR="$BM_LIB_DIR"
    cd "$BM_REPO" || bm_fail "$BM_RC_STEP" "仓库目录不存在: $BM_REPO"
    bm_init_paths "$BM_REPO"
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
    case "${1:-}" in
        --status) bm_status; exit 0 ;;
        --rollback)
            shift
            if [ -f "$BM_IN_PROGRESS" ] && [ "$(bm_manifest_get kind "")" = "adopt" ]; then
                echo "    未完成的收养事务 $(bm_manifest_get txn_id ?):先由固化执行体续做(完成前没有回滚点)"
                bm_controller_adopt_resume
                exit 0
            fi
            bm_rollback_main "$@" ;;
        *) bm_fail "$BM_RC_USAGE" "用法: $0 --rollback [--restore-db] [--yes] [--to <txn>] [--no-rescue-snapshot] | --status" ;;
    esac
}
# 在 controller 上下文续做收养:站点参数 / CONFIG_FILE / venv 都取自 manifest,不读工作树 ini
bm_controller_adopt_resume() {
    export DORAMI_DEPLOY_LOCK_BUSY_RC="$BM_RC_LOCK"
    acquire_deploy_lock
    bm_install_traps
    bm_apply_site_from_manifest "$BM_IN_PROGRESS"
    CONFIG_FILE="$BM_RB_CONFIG_FILE"; export CONFIG_FILE
    VENV_DIR="$(bm_manifest_get target.venv "${VENV_DIR:-venv}")"
    bm_adopt_resume
}
