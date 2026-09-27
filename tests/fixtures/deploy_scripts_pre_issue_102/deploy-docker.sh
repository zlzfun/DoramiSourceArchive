#!/bin/bash
# 一键部署(推荐路径;装不了 Docker 时走 ./deploy.sh 裸机路径):
# 站到发布版 tag → 备份 DB → 构建镜像 → 起容器 → 全链路健康验证(nginx → backend)。
#
# 用法:./deploy-docker.sh            部署版本号最新的 v* tag(一键)
#      ./deploy-docker.sh v3.55.0    部署指定版本
#      ./deploy-docker.sh --here     部署当前工作树(非发布版,联调/应急;输出会标注)
# 发布流程、回滚与「tag 即发布」的来龙去脉见 docs/release-process.md。
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "ERROR: $*" >&2; exit 1; }

# shellcheck source=scripts/deploy-lib.sh
source scripts/deploy-lib.sh
# 选版本并切换(切换时会以切换后的脚本重执行,见库文件头);导出 DORAMI_BUILD_REF/SHA
resolve_deploy_ref "$@"

command -v docker >/dev/null 2>&1 || fail "docker 未安装"
docker compose version >/dev/null 2>&1 || fail "docker compose 插件未安装"

CONFIG_FILE="config/production.ini"
[ -f "$CONFIG_FILE" ] || fail "$CONFIG_FILE 不存在,先从 config/production.example.ini 创建"

echo "=================================================="
echo "  Dorami production deploy (docker) — ${DORAMI_BUILD_REF}"
echo "=================================================="

echo "[1/4] 备份数据库..."
# 迁移在容器启动时执行(entrypoint 的 ensure_migrated)且不一定可逆:
# 回滚 = ./deploy-docker.sh <上一 tag> + 用这份备份覆盖 data/ 里的库文件。
DB_PATH="$(sqlite_path_from_ini "$CONFIG_FILE")"
if [ -n "$DB_PATH" ] && [ -f "$DB_PATH" ]; then
    backup_sqlite_db "$DB_PATH"
else
    echo "    未找到 SQLite 库文件(${DB_PATH:-非 sqlite}),跳过备份(全新部署或外部数据库)"
fi

echo "[2/4] 构建镜像..."
# 构建来源经 build args 烤进镜像(镜像里没有 .git),运行时 /api/runtime 透出,
# 设置 → 关于 可核对生产到底跑的是哪一版。
docker compose build

echo "[3/4] 启动容器..."
docker compose up -d --remove-orphans

echo "[4/4] 健康验证..."
# DORAMI_HTTP_LISTEN 可以是纯端口(80)或绑定形式(127.0.0.1:8080),探针地址随之推导
LISTEN="${DORAMI_HTTP_LISTEN:-80}"
case "$LISTEN" in
    *:*) PROBE="http://${LISTEN}" ;;
    *)   PROBE="http://127.0.0.1:${LISTEN}" ;;
esac
# /api/auth/session 免鉴权,未登录也 200——作全链路(nginx→backend)探针
for _ in $(seq 1 45); do
    if curl -fsS "${PROBE}/api/auth/session" >/dev/null 2>&1; then
        echo ""
        if [ "${DORAMI_DEPLOY_MODE}" = "tag" ]; then
            echo "Deploy complete. 发布版 ${DORAMI_BUILD_REF}(${DORAMI_BUILD_SHA:0:7})"
        else
            echo "Deploy complete. ⚠️  非发布版:${DORAMI_BUILD_REF}(${DORAMI_BUILD_SHA:0:7})"
        fi
        exit 0
    fi
    sleep 2
done

echo "健康检查超时,当前容器状态:" >&2
docker compose ps >&2
docker compose logs --tail 50 backend >&2
fail "部署未通过健康验证"
