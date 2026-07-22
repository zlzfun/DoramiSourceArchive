#!/bin/bash
# Docker 版一键部署:构建镜像 → 起容器 → 全链路健康验证(nginx → backend)。
# 裸机 PM2 路径的 deploy.sh 保留作过渡备用;二者别同时占 80 端口。
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker 未安装"
docker compose version >/dev/null 2>&1 || fail "docker compose 插件未安装"

CONFIG_FILE="config/production.ini"
[ -f "$CONFIG_FILE" ] || fail "$CONFIG_FILE 不存在,先从 config/production.example.ini 创建"

echo "=================================================="
echo "  Dorami production deploy (docker)"
echo "=================================================="

echo "[1/3] 构建镜像..."
docker compose build

echo "[2/3] 启动容器..."
docker compose up -d --remove-orphans

echo "[3/3] 健康验证..."
PORT="${DORAMI_HTTP_PORT:-80}"
# /api/auth/session 免鉴权,未登录也 200——作全链路(nginx→backend)探针
for _ in $(seq 1 45); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/auth/session" >/dev/null 2>&1; then
        VERSION="$(grep -o '"[0-9][^"]*"' src/version.py | head -1 | tr -d '"')"
        echo ""
        echo "Deploy complete. 后端版本: ${VERSION:-unknown}"
        exit 0
    fi
    sleep 2
done

echo "健康检查超时,当前容器状态:" >&2
docker compose ps >&2
docker compose logs --tail 50 backend >&2
fail "部署未通过健康验证"
