#!/bin/bash
# 一键部署(推荐路径;装不了 Docker 时走 ./deploy.sh 裸机路径):
# 站到发布版 tag → 预检 → 构建镜像 → 目标镜像只读自检 → 迁移计划 → (手工来源)备份 DB → 起容器 → 健康 + 版本核对。
#
# 用法:./deploy-docker.sh            部署版本号最新的 v* tag(一键)
#      ./deploy-docker.sh v3.55.0    部署指定版本
#      ./deploy-docker.sh --here     部署当前工作树(非发布版,联调/应急;输出会标注)
# 发布流程、回滚与「tag 即发布」的来龙去脉见 docs/release-process.md;自动部署流水线见 docs/auto-deploy-plan.md。
#
# 本脚本只做**它自己这一版**的构建与验证;跨版本的状态与门禁(锁的 owner、in-progress / last-success 事务、
# 方向判定与单调护栏、首装门)由仓库外的 dorami-deploy-worker 承担(DORAMI_DEPLOY_ORIGIN=pipeline 时)。
# 与 worker 的契约版本见 scripts/deploy-lib.sh 的 DORAMI_DEPLOY_PROTOCOL。
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "ERROR: $*" >&2; exit 1; }

# shellcheck source=scripts/deploy-lib.sh
source scripts/deploy-lib.sh
# 唯一部署锁(手工来源自己抢;流水线来源由 worker 持有并经 FD 继承)——先于 checkout,两条路径互斥
acquire_deploy_lock
# 选版本并切换(切换时会以切换后的脚本重执行,见库文件头);导出 DORAMI_BUILD_REF/SHA;流水线来源核 expected sha
resolve_deploy_ref "$@"
ORIGIN="$(deploy_origin)"

command -v docker >/dev/null 2>&1 || fail "docker 未安装"
docker compose version >/dev/null 2>&1 || fail "docker compose 插件未安装"

CONFIG_FILE="config/production.ini"
[ -f "$CONFIG_FILE" ] || fail "$CONFIG_FILE 不存在,先从 config/production.example.ini 创建"

echo "=================================================="
echo "  Dorami production deploy (docker) — ${DORAMI_BUILD_REF}(来源 ${ORIGIN})"
echo "=================================================="
deploy_meta build_ref "$DORAMI_BUILD_REF"; deploy_meta build_sha "$DORAMI_BUILD_SHA"; deploy_meta origin "$ORIGIN"

# ── 预检助手 ──
preflight_disk() {
    local min_gb="${DORAMI_DEPLOY_MIN_FREE_GB:-5}" min_kb docker_root checked="" p fs avail iavail
    min_kb=$((min_gb * 1024 * 1024))
    docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
    for p in "$docker_root" "$PWD" "$PWD/data" "$PWD/backups"; do
        [ -e "$p" ] || continue
        fs="$(df -Pk "$p" 2>/dev/null | awk 'NR==2{print $1}')"
        [ -n "$fs" ] || continue
        case " $checked " in *" $fs "*) continue ;; esac
        checked="$checked $fs"
        avail="$(df -Pk "$p" | awk 'NR==2{print $4}')"
        # inode 空闲列按表头名定位(GNU 是第 4 列 IFree,macOS 是第 7 列 ifree);找不到就跳过 inode 检查
        iavail="$(df -Pi "$p" 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if(tolower($i)=="ifree") c=i} NR==2 && c {print $c}')"
        [[ "$iavail" =~ ^[0-9]+$ ]] || iavail=""
        echo "    $fs($p): 可用 $((avail / 1024 / 1024)) GB${iavail:+, 空闲 inode $iavail}"
        [ "$avail" -ge "$min_kb" ] \
            || fail "磁盘不足:$p 所在文件系统可用 $((avail / 1024 / 1024)) GB < ${min_gb} GB(DORAMI_DEPLOY_MIN_FREE_GB);先清理(docker image prune / 旧备份)再部署"
        if [ -n "$iavail" ] && [ "$iavail" -lt 10000 ]; then
            fail "inode 不足:$p 所在文件系统只剩 $iavail 个空闲 inode"
        fi
    done
    if command -v free >/dev/null 2>&1; then
        echo "    swap: $(free -h 2>/dev/null | awk '/[Ss]wap/{print $2" 总 / "$3" 用"}')"
    fi
}

preflight_compose_config() {
    # 固定带 -q:只校验、不打印渲染后的配置(compose 含大量密钥环境变量,会进 Actions 日志);只记退出码与脱敏摘要
    local err
    if ! err="$(docker compose config -q 2>&1 >/dev/null)"; then
        printf '%s\n' "$err" | grep -oE 'required variable [A-Za-z_][A-Za-z0-9_]* is missing[^:]*' | head -5 | sed 's/^/    /' || true
        fail "docker compose config 校验失败(.env 里 :? 必填变量缺失或 compose 文件错误);原始输出不回显"
    fi
}

json_field() {  # stdin JSON, $1 key(dot path) → 值(缺则空;列表输出长度)
    python3 -c '
import json, sys
try:
    cur = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for part in sys.argv[1].split("."):
    cur = cur.get(part) if isinstance(cur, dict) else None
if cur is None:
    pass
elif isinstance(cur, bool):
    print("true" if cur else "false")
elif isinstance(cur, list):
    print(len(cur))
else:
    print(cur)
' "$1"
}

print_report_messages() {  # stdin JSON → 逐行打印 errors / warnings
    python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("    (目标镜像未输出可解析的 JSON)"); sys.exit(0)
for m in d.get("errors", []): print("    ✗", m)
for m in d.get("warnings", []): print("    ⚠", m)
' || true
}

# ── [1/7] 预检 ──
echo "[1/7] 预检(磁盘 / compose 配置)..."
preflight_disk
preflight_compose_config
echo "    OK"

# ── [2/7] 构建 ──
echo "[2/7] 构建镜像..."
# 构建来源经 build args 烤进镜像(镜像里没有 .git),运行时 /api/runtime 与 /api/health 透出,
# 设置 → 关于 可核对生产到底跑的是哪一版。
docker compose build

# ── [3/7] 目标镜像只读自检 ──
echo "[3/7] 配置与当前状态预检(目标镜像 --check-config)..."
check_out="$(docker compose run --rm --no-deps -T backend python docker/entrypoint.py --check-config 2>/dev/null || true)"
check_status="$(printf '%s' "$check_out" | json_field status)"
printf '%s' "$check_out" | print_report_messages
if [ "$check_status" != "ok" ]; then
    fail "目标版本配置自检未通过(status=${check_status:-none});修正 production.ini / .env 后重试,容器未被切换"
fi
echo "    OK"

# ── [4/7] 迁移计划 ──
echo "[4/7] 迁移计划(目标镜像 --plan-migrations,只读)..."
plan_out="$(docker compose run --rm --no-deps -T backend python docker/entrypoint.py --plan-migrations 2>/dev/null || true)"
plan_status="$(printf '%s' "$plan_out" | json_field status)"
plan_pending="$(printf '%s' "$plan_out" | json_field pending_count)"
deploy_meta migrations_status "${plan_status:-none}"; deploy_meta migrations_pending "${plan_pending:-unknown}"
case "$plan_status" in
    compatible)
        echo "    OK: compatible,待执行迁移 ${plan_pending:-0} 个" ;;
    legacy_adoption_required)
        echo "    有业务表无 alembic_version:容器启动时 ensure_migrated 会对齐基线并收养(待执行 ${plan_pending:-?})" ;;
    fresh)
        if [ "${DORAMI_DEPLOY_FRESH_OK:-0}" = "1" ]; then
            echo "    库不存在 / 无业务表,首装门已放行:目标链将从头建立(${plan_pending:-?} 个迁移)"
        else
            fail "迁移计划报 fresh(库不存在或无业务表)但首装门未放行:有部署证据的节点绝不起空站——检查 DB 路径 / 卷挂载 / 备份;真正首装请 touch first-install 令牌(见 docker/dorami-deploy.conf.example)"
        fi ;;
    incompatible)
        printf '%s' "$plan_out" | json_field detail | sed 's/^/    /'
        fail "数据库与目标代码的迁移图不兼容(典型:DB 领先于旧 tag,降级撞迁移);先按 docs/release-process.md 恢复对应备份再重跑" ;;
    *)
        printf '%s' "$plan_out" | print_report_messages
        fail "迁移计划失败(status=${plan_status:-none})" ;;
esac

# ── [5/7] 备份 ──
echo "[5/7] 备份数据库..."
if [ "$ORIGIN" = "pipeline" ]; then
    # worker 已在切换前做了事务备份并按 manifest pin 清理;本脚本的纯计数清理不认识 manifest,
    # 若在流水线下再跑,同 target 多次重试后会把仍被 in-progress 引用的原始备份删掉。
    echo "    流水线来源:跳过(worker 已做事务备份)"
    deploy_meta db_backup worker
else
    # 迁移在容器启动时执行(entrypoint 的 ensure_migrated)且不一定可逆:
    # 回滚 = ./deploy-docker.sh <上一 tag> + 用这份备份覆盖 data/ 里的库文件。
    DB_PATH="$(sqlite_path_from_ini "$CONFIG_FILE")"
    if [ -n "$DB_PATH" ] && [ -f "$DB_PATH" ]; then
        backup_sqlite_db "$DB_PATH"
    else
        echo "    未找到 SQLite 库文件(${DB_PATH:-非 sqlite}),跳过备份(全新部署或外部数据库)"
    fi
fi

# ── [6/7] 切换 ──
echo "[6/7] 启动容器..."
if [ -n "${DORAMI_DEPLOY_SWITCH_MARK:-}" ]; then
    # 向 worker 表明「系统即将被改动」:此后失败的事务不再被别的目标自动关闭
    touch "$DORAMI_DEPLOY_SWITCH_MARK"
fi
if [ "$ORIGIN" != "pipeline" ]; then
    # 手工越过切换点:同一锁内先让流水线的 last-success 失效(worker 下次从容器读基线、不回放)
    note_manual_switch "$DORAMI_BUILD_REF" "$DORAMI_BUILD_SHA"
fi
docker compose up -d --remove-orphans

# ── [7/7] 健康 + 版本核对 ──
echo "[7/7] 健康验证(/api/health 五项核对)..."
# DORAMI_HTTP_LISTEN 可以是纯端口(80)或绑定形式(127.0.0.1:8080),探针地址随之推导
LISTEN="${DORAMI_HTTP_LISTEN:-80}"
case "$LISTEN" in
    *:*) PROBE="http://${LISTEN}" ;;
    *)   PROBE="http://127.0.0.1:${LISTEN}" ;;
esac
EXPECT_VERSION="$(_deploy_lib_source_version)"
# 时间预算(默认 180s:1.6GB 机冷启动带 Chromium 的容器不一定 90 秒就绪);每次 curl 都带连接 / 总超时,
# 连接建立后后端不答也不能吃掉整个预算(容器 nginx 的 upstream 读超时是 300s)。次数只作额外上限。
BUDGET="${DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS:-180}"
ATTEMPTS="${DORAMI_DEPLOY_HEALTH_ATTEMPTS:-90}"
deadline=$(( $(date +%s) + BUDGET ))
health_ok=""
last_verdict=""
attempt=0
while [ "$attempt" -lt "$ATTEMPTS" ]; do
    attempt=$((attempt + 1))
    remaining=$(( deadline - $(date +%s) ))
    [ "$remaining" -gt 0 ] || break
    max_time=$(( remaining < 15 ? remaining : 15 ))
    body="$(curl -fsS --connect-timeout 5 --max-time "$max_time" -H 'Cache-Control: no-cache' "${PROBE}/api/health?_=$(date +%s)" 2>/dev/null || true)"
    if [ -n "$body" ]; then
        verdict="$(printf '%s' "$body" | python3 -c '
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
' "$EXPECT_VERSION" "$DORAMI_BUILD_REF" "$DORAMI_BUILD_SHA")"
        if [ "$verdict" = "ok" ]; then health_ok=1; break; fi
        last_verdict="$verdict"
    fi
    [ $(( deadline - $(date +%s) )) -gt 2 ] || break
    sleep 2
done

if [ -n "$health_ok" ]; then
    deploy_meta health ok
    echo ""
    if [ "${DORAMI_DEPLOY_MODE}" = "tag" ]; then
        echo "Deploy complete. 发布版 ${DORAMI_BUILD_REF}(${DORAMI_BUILD_SHA:0:7})"
    else
        echo "Deploy complete. ⚠️  非发布版:${DORAMI_BUILD_REF}(${DORAMI_BUILD_SHA:0:7})"
    fi
    exit 0
fi

deploy_meta health failed
echo "健康检查未通过(预算 ${BUDGET}s,尝试 ${attempt} 次):${last_verdict:-无响应};当前容器状态:" >&2
docker compose ps >&2
docker compose logs --tail 50 backend >&2
fail "部署未通过健康验证(不自动回滚:流水线来源下 in-progress 事务保留,可重试或按 docs/release-process.md 恢复)"
