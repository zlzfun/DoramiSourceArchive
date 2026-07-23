#!/bin/bash
# 低版本 Docker 兼容一键部署(内网 Docker 18.x + docker-compose v1 环境)。
# 流程与 deploy-docker.sh 一致(构建 → 起容器 → 全链路健康验证),差异:
#   1) compose 命令自动探测:优先 docker-compose(v1 独立二进制),无则退回 docker compose 插件;
#   2) 使用钉 version "2.4" 的 docker-compose.legacy.yml(旧 docker-compose 不识别
#      无 version 键的 compose-spec 文件);
#   3) 旧版无 profiles,RAG 服务组改为 --rag 参数叠加 docker-compose.legacy-rag.yml。
# 新版环境(Docker 20.10+ 且带 compose 插件)请用 ./deploy-docker.sh。
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker 未安装"

if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
else
    fail "docker-compose 与 docker compose 插件均未安装"
fi

COMPOSE_FILES=(-f docker-compose.legacy.yml)
WITH_RAG=0
if [ "${1:-}" = "--rag" ]; then
    WITH_RAG=1
    COMPOSE_FILES+=(-f docker-compose.legacy-rag.yml)
elif [ -n "${1:-}" ]; then
    fail "未知参数: $1(仅支持 --rag)"
fi

CONFIG_FILE="config/production.ini"
[ -f "$CONFIG_FILE" ] || fail "$CONFIG_FILE 不存在,先从 config/production.example.ini 创建"

# 先让当前安装的 compose 解析一遍配置,版本过旧(< 1.21,不识别 file format 2.4)在这里
# 就报清楚,而不是 build 中途报晦涩的 schema 错
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" config >/dev/null 2>&1 \
    || fail "compose 配置校验失败:docker-compose 版本可能过旧(file format 2.4 需 docker-compose 1.21+ / Engine 17.12+),可运行 ${COMPOSE[*]} ${COMPOSE_FILES[*]} config 查看具体报错"

echo "=================================================="
echo "  Dorami production deploy (docker, legacy compose)"
echo "  compose 命令: ${COMPOSE[*]}  RAG: $([ "$WITH_RAG" = 1 ] && echo on || echo off)"
echo "=================================================="

echo "[1/3] 构建镜像..."
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" build

echo "[2/3] 启动容器..."
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" up -d --remove-orphans

echo "[3/3] 健康验证..."
# DORAMI_HTTP_LISTEN 可以是纯端口(80)或绑定形式(127.0.0.1:8080),探针地址随之推导
LISTEN="${DORAMI_HTTP_LISTEN:-80}"
case "$LISTEN" in
    *:*) PROBE="http://${LISTEN}" ;;
    *)   PROBE="http://127.0.0.1:${LISTEN}" ;;
esac
# /api/auth/session 免鉴权,未登录也 200——作全链路(nginx→backend)探针
for _ in $(seq 1 45); do
    if curl -fsS "${PROBE}/api/auth/session" >/dev/null 2>&1; then
        VERSION="$(grep -o '"[0-9][^"]*"' src/version.py | head -1 | tr -d '"')"
        echo ""
        echo "Deploy complete. 后端版本: ${VERSION:-unknown}"
        exit 0
    fi
    sleep 2
done

echo "健康检查超时,当前容器状态:" >&2
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" ps >&2
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" logs --tail=50 backend >&2
fail "部署未通过健康验证"
