#!/bin/bash
# 裸机一键部署(两条官方部署路径之一):uv 装依赖 + PM2 托管后端 + 现场构建前端
# + 生成并校验宿主 Nginx 站点配置(含 HTTPS 与 /mcp)。文档见 docs/deploy-baremetal.md。
#
# 与 Docker 路径(./deploy-docker.sh)的分工:能装 Docker 就走 Docker(依赖锁定、
# 环境固化、发布原子化);装不了 Docker 的机器走本脚本(宿主装 Python/Node/Nginx)。
# 沿革:本路径曾于 v3.15.1 随生产切 Docker 退役删除,v3.39.0 因「公网机不便装
# Docker」的真实场景扶正回归,并清理了当年的 RAG 形态判定(向量子系统已于 v3.31 退役)。
#
# issue #126(docs/baremetal-rollback-plan.md)「运行副本版本化」:每次部署生成不可变的 release
# (代码副本 + 私有 venv 指针 + dist + nginx 配置集合 + 固化的回滚执行体),PM2 从 release 实路径启动;
# 部署是**事务**(锁 → 只读预检 → 开事务 → 阶段落盘 → 两级健康门 → 晋升),健康门不通过只**告警**、不自动回滚;
# `--rollback` 一键回到上次成功部署(不 checkout、不出网、不构建);`--status` 只读查看。
# 裸机专属参数与事务函数在 scripts/deploy-baremetal.sh 装配,Docker 路径不受影响。
#
# 受限网络/镜像加速:uv 走环境变量 UV_DEFAULT_INDEX=<PyPI 镜像>;
# npm 走 NPM_REGISTRY=<npm 镜像>(离线内网源与国内加速源同一开关)。
set -euo pipefail

# Always run from the project root, no matter where the command is invoked.
cd "$(dirname "$0")"

# shellcheck source=scripts/deploy-lib.sh
source scripts/deploy-lib.sh
# shellcheck source=scripts/deploy-baremetal.sh
source scripts/deploy-baremetal.sh
bm_init_paths "$PWD"

# 手动安装的 nginx/node 常落在非默认 PATH:源码装的 nginx 在 /usr/local/nginx/sbin,
# nvm 装的 node 只写进 ~/.bashrc(仅交互 shell 生效)。本脚本以非交互 shell 运行,
# 先把常见位置并进 PATH,再探测 nvm 的最新版本 node。
export PATH="$PATH:/usr/sbin:/usr/local/sbin:/usr/local/bin:/usr/local/nginx/sbin"
if ! command -v node >/dev/null 2>&1 && [ -d "${NVM_DIR:-$HOME/.nvm}/versions/node" ]; then
    NVM_NODE_BIN="$(ls -d "${NVM_DIR:-$HOME/.nvm}/versions/node"/*/bin 2>/dev/null | sort -V | tail -1)"
    if [ -n "$NVM_NODE_BIN" ]; then
        export PATH="$PATH:$NVM_NODE_BIN"
        echo "Detected nvm node: $NVM_NODE_BIN"
    fi
fi

APP_NAME="$BM_APP_NAME"
VENV_DIR="${VENV_DIR:-venv}"
CONFIG_FILE="${DORAMI_CONFIG_FILE:-$(pwd)/config/production.ini}"
case "$CONFIG_FILE" in /*) ;; *) CONFIG_FILE="$(pwd)/$CONFIG_FILE" ;; esac

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

fail() {
    BM_LAST_ERROR="$*"
    echo "ERROR: $*" >&2
    exit 1
}

need_command() {
    local command_name="$1"
    local hint="$2"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        fail "$command_name is required. $hint"
    fi
}

ini_get() {
    local section="$1"
    local key="$2"
    local fallback="$3"
    awk -F '=' -v section="[$section]" -v key="$key" -v fallback="$fallback" '
        BEGIN { in_section = 0; value = fallback }
        /^[[:space:]]*[#;]/ { next }
        /^[[:space:]]*\[/ {
            line = $0
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
            in_section = (line == section)
            next
        }
        in_section {
            line = $0
            sub(/[[:space:]]*[#;].*$/, "", line)
            split(line, parts, "=")
            candidate_key = parts[1]
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate_key)
            if (candidate_key == key) {
                sub(/^[^=]*=/, "", line)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
                value = line
            }
        }
        END { print value }
    ' "$CONFIG_FILE"
}

usage() {
    cat <<EOF
用法: ./deploy.sh [vX.Y.Z | --here | --code <sha|tag>]
      ./deploy.sh --rollback [--restore-db] [--yes] [--to <txn>] [--no-rescue-snapshot]
      ./deploy.sh --status | --discard-txn [--yes] | --adopt [--adopt-sha <sha>]

  (无参数)          拉取 tag,部署版本号最新的发布版(一键部署;目标 tag 须有裸机事务能力,否则提示 --code)
  vX.Y.Z            部署指定版本
  --here            部署当前工作树(dirty 也固化成快照;内网适配分支用这个;输出会标注非发布版)
  --code <sha|tag>  由当前编排器部署任意代码对象,不 checkout 工作树(回到更早版本 / 部署无事务能力的旧 tag)
  --rollback        回到上次成功部署(不 checkout、不出网、不构建;有迁移差异时默认拒绝,--restore-db 显式恢复库快照)
  --status          只读:当前跑什么、上次成功是什么、未收口事务、回滚目标与 DB 预判、能力位
  --discard-txn     人已手工处理,归档未收口事务(材料保留;已改宿主时要求 --yes)
  --adopt           收养旧形态安装为第一个 release(首次运行本脚本时也会自动触发)
  --no-rollback-guarantee   身份证据冲突 / 非 SQLite 库时显式放弃回滚保证继续部署

环境变量:DORAMI_DEPLOY_EXTRAS=crawl4ai(按 docker/requirements-<extra>.txt 钉版装 extras)、
  DORAMI_DEPLOY_HEALTH_BUDGET_SECONDS(默认 180)、DORAMI_DEPLOY_STABLE_SECONDS(默认 10)、
  DORAMI_DEPLOY_LOCK_FILE(默认 /run/lock/dorami-deploy.lock,与 Docker 路径同一把)、DORAMI_DEPLOY_FRESH_OK=1(真首装授权)。
方案 docs/baremetal-rollback-plan.md;发布流程 docs/release-process.md。
EOF
}

# ── 参数(裸机专属参数只在这里装配;版本 / --here 透传给 resolve_deploy_ref)──
BM_ACTION="deploy"; BM_DEPLOY_ARGS=(); BM_PASS_ARGS=(); BM_CODE=""
BM_YES=0; BM_NO_ROLLBACK_GUARANTEE=0; BM_ADOPT_SHA=""; BM_RESTORE_DB=0; BM_TO=""; BM_NO_RESCUE=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --status) BM_ACTION="status" ;;
        --rollback) BM_ACTION="rollback" ;;
        --discard-txn) BM_ACTION="discard" ;;
        --adopt) BM_ACTION="adopt" ;;
        --adopt-sha) [ $# -ge 2 ] || { usage >&2; bm_fail "$BM_RC_USAGE" "--adopt-sha 需要一个 sha"; }; BM_ADOPT_SHA="$2"; shift ;;
        --code) [ $# -ge 2 ] || { usage >&2; bm_fail "$BM_RC_USAGE" "--code 需要 <sha|tag>"; }; BM_CODE="$2"; shift ;;
        --restore-db) BM_RESTORE_DB=1; BM_PASS_ARGS+=(--restore-db) ;;
        --no-rescue-snapshot) BM_NO_RESCUE=1; BM_PASS_ARGS+=(--no-rescue-snapshot) ;;
        --to) [ $# -ge 2 ] || { usage >&2; bm_fail "$BM_RC_USAGE" "--to 需要 <txn>"; }; BM_TO="$2"; BM_PASS_ARGS+=(--to "$2"); shift ;;
        --yes) BM_YES=1; BM_PASS_ARGS+=(--yes) ;;
        --no-rollback-guarantee) BM_NO_ROLLBACK_GUARANTEE=1 ;;
        --here) BM_DEPLOY_ARGS+=(--here) ;;
        --*) usage >&2; bm_fail "$BM_RC_USAGE" "未知参数: $1" ;;
        *) BM_DEPLOY_ARGS+=("$1") ;;
    esac
    shift
done
export BM_YES BM_NO_ROLLBACK_GUARANTEE BM_ADOPT_SHA BM_RESTORE_DB BM_TO BM_NO_RESCUE
if [ "$BM_ACTION" != "rollback" ] && { [ "$BM_RESTORE_DB" = 1 ] || [ -n "$BM_TO" ] || [ "$BM_NO_RESCUE" = 1 ]; }; then
    usage >&2; bm_fail "$BM_RC_USAGE" "--restore-db / --to / --no-rescue-snapshot 只能与 --rollback 同用"
fi
if [ "$BM_ACTION" != "adopt" ] && [ -n "$BM_ADOPT_SHA" ]; then
    usage >&2; bm_fail "$BM_RC_USAGE" "--adopt-sha 只能与 --adopt 同用"
fi
if [ "$BM_NO_RESCUE" = 1 ] && [ "$BM_RESTORE_DB" != 1 ]; then
    usage >&2; bm_fail "$BM_RC_USAGE" "--no-rescue-snapshot 只能与 --restore-db 同用(且只对已损坏的库生效)"
fi
if [ -n "$BM_CODE" ] && { [ "$BM_ACTION" != "deploy" ] || [ ${#BM_DEPLOY_ARGS[@]} -gt 0 ]; }; then
    usage >&2; bm_fail "$BM_RC_USAGE" "--code 不能与版本号 / --here / 其它动作同用"
fi
if [ "$BM_ACTION" != "deploy" ] && [ ${#BM_DEPLOY_ARGS[@]} -gt 0 ]; then
    usage >&2; bm_fail "$BM_RC_USAGE" "--$BM_ACTION 不接受版本号 / --here"
fi

install_system_packages() {
    # 逐个探测,只装缺失的;包管理器装不上(受限源没有该包)不再直接打断——
    # 允许手动安装的二进制通过后面的复核。
    local missing=()
    command -v nginx >/dev/null 2>&1 || missing+=(nginx)
    command -v npm >/dev/null 2>&1 || missing+=(npm)
    { command -v node >/dev/null 2>&1 || command -v nodejs >/dev/null 2>&1; } || missing+=(nodejs)
    { command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; } || missing+=(ffmpeg)

    if [ ${#missing[@]} -eq 0 ]; then
        echo "System packages already installed."
        return
    fi

    echo "Installing missing packages: ${missing[*]} ..."
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update || true
        $SUDO apt-get install -y "${missing[@]}" || echo "WARNING: apt-get install failed; will re-check for manually installed binaries."
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y "${missing[@]}" || echo "WARNING: dnf install failed (受限源可能没有这些包); will re-check for manually installed binaries."
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y "${missing[@]}" || echo "WARNING: yum install failed (受限源可能没有这些包); will re-check for manually installed binaries."
    else
        echo "WARNING: no supported package manager found; expecting manually installed binaries."
    fi

    # 安装尝试后逐一复核。找不到时的常见原因:手动安装的命令只在交互 shell 的
    # PATH 里(nvm 写在 ~/.bashrc / 源码装在自定义目录)。脚本已自动追加
    # /usr/sbin:/usr/local/sbin:/usr/local/bin:/usr/local/nginx/sbin 与 nvm 目录,
    # 仍找不到就 `export PATH="$PATH:<安装目录>"` 后重跑本脚本。
    local hint="源里没有该包时请手动安装,并确保命令对非交互 shell 可见(见脚本头部 PATH 注释)。"
    command -v nginx >/dev/null 2>&1 || fail "nginx not found in script PATH. $hint"
    command -v npm >/dev/null 2>&1 || fail "npm not found in script PATH. $hint"
    if ! command -v node >/dev/null 2>&1 && ! command -v nodejs >/dev/null 2>&1; then
        fail "node/nodejs not found in script PATH. $hint"
    fi
    command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg not found in script PATH. $hint"
    command -v ffprobe >/dev/null 2>&1 || fail "ffprobe not found in script PATH. Install the ffmpeg package and ensure both binaries are visible."
}

install_pm2() {
    if command -v pm2 >/dev/null 2>&1; then
        echo "PM2 already installed."
        return
    fi

    echo "Installing PM2..."
    # 先以当前用户全局装(nvm/用户级 node 的全局前缀本就可写,且 sudo 环境里
    # 往往根本没有 npm);权限不足再退 sudo。
    if ! npm install -g pm2 ${NPM_REGISTRY:+--registry=${NPM_REGISTRY}}; then
        $SUDO npm install -g pm2 ${NPM_REGISTRY:+--registry=${NPM_REGISTRY}}
    fi
    # 装好但全局 bin 不在 PATH(自定义 npm prefix)时补进来
    if ! command -v pm2 >/dev/null 2>&1; then
        NPM_GLOBAL_BIN="$(npm prefix -g 2>/dev/null)/bin"
        [ -x "$NPM_GLOBAL_BIN/pm2" ] && export PATH="$PATH:$NPM_GLOBAL_BIN"
    fi
    need_command pm2 "npm global bin directory is not on PATH, or PM2 installation failed."
}

# nginx 配置根(默认 /etc/nginx;测试与非常规安装可用 DORAMI_NGINX_ETC_DIR 整体改指)
NGINX_ETC_DIR="${DORAMI_NGINX_ETC_DIR:-/etc/nginx}"

resolve_nginx_site_file() {
    if [ -d "$NGINX_ETC_DIR/sites-available" ] && [ -d "$NGINX_ETC_DIR/sites-enabled" ]; then
        NGINX_SITE_FILE="$NGINX_ETC_DIR/sites-available/${NGINX_SITE_NAME}"
        NGINX_SITE_ENABLED_FILE="$NGINX_ETC_DIR/sites-enabled/${NGINX_SITE_NAME}"
    else
        NGINX_SITE_FILE="$NGINX_ETC_DIR/conf.d/${NGINX_SITE_NAME}.conf"
        NGINX_SITE_ENABLED_FILE="$NGINX_SITE_FILE"
    fi
    NGINX_DEFAULT_SITE_FILE="$NGINX_ETC_DIR/sites-enabled/default"
}

# 站点配置正文渲染到 stdout(不落盘;先写 release 候选 <release>/nginx/site.conf,再由变更集流程落在线路径)
render_nginx_site_config() {
    local backend_host="$1"
    local backend_port="$2"
    local backend_upstream="http://${backend_host}:${backend_port}"
    local ssl_enabled="false"
    local hsts_header=""
    # Exact paths: a missing worker/manifest must be 404, never the SPA HTML.
    # expires preserves inherited security headers (unlike location add_header).
    local pwa_locations='    location = /sw.js {
        default_type application/javascript;
        expires -1;
        try_files $uri =404;
    }
    location = /manifest.webmanifest {
        types { application/manifest+json webmanifest; }
        expires -1;
        try_files $uri =404;
    }'

    if truthy "$NGINX_ENABLE_SSL"; then
        ssl_enabled="true"
        if truthy "$NGINX_ENABLE_HSTS"; then
            hsts_header='    add_header Strict-Transport-Security "max-age=15552000" always;'
        fi
    fi

    # 三种形态共用的 location 集合(/api 反代 + /mcp Host 改写 + SPA 入口禁缓存 + PWA 精确路径 + try_files)
    local common_locations
    common_locations="$(cat <<EOF
    location /api/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        # 关闭代理缓冲:默认缓冲下超过内存缓冲(~64KB)的响应要落 proxy_temp 临时
        # 目录,源码装 nginx 的 worker(nobody)对该目录无写权限时会静默截断大响应
        # (上游 200、浏览器 Failed to fetch);直通转发同时也是 /mcp SSE 流的正确形态。
        proxy_buffering off;
        proxy_pass ${backend_upstream};
    }

    location /mcp {
        proxy_http_version 1.1;
        # MCP python SDK 的 DNS-rebinding 防护只认 localhost 形态的 Host,
        # 经域名/EIP 反代进来会被 421 Invalid Host header 拒绝(2026-08-19 内网
        # bot 接入实锤);服务端对服务端的可信反代在边缘改写 Host 即可,与
        # proxy_pass 同源取值。/api 仍透传 \$host,不受影响。
        proxy_set_header Host ${backend_host}:${backend_port};
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        # 关闭代理缓冲:默认缓冲下超过内存缓冲(~64KB)的响应要落 proxy_temp 临时
        # 目录,源码装 nginx 的 worker(nobody)对该目录无写权限时会静默截断大响应
        # (上游 200、浏览器 Failed to fetch);直通转发同时也是 /mcp SSE 流的正确形态。
        proxy_buffering off;
        proxy_pass ${backend_upstream};
    }

    # SPA 入口禁启发式缓存:资产文件名带内容哈希天然免疫,但 index.html 若被浏览器
    # 启发式缓存,部署后用户会继续引用旧 bundle——「推了修复却没生效」的经典成因。
    # 用 expires 而非 add_header,避免 location 级 add_header 清空 server 级安全头继承。
    location = /index.html {
        expires -1;
    }

${pwa_locations}
    location / {
        try_files \$uri \$uri/ /index.html;
    }
EOF
)"
    local ssl_block
    ssl_block="$(cat <<EOF
    ssl_certificate ${NGINX_SSL_CERT_FILE};
    ssl_certificate_key ${NGINX_SSL_KEY_FILE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
EOF
)"

    if [ "$ssl_enabled" = "true" ]; then
        if truthy "$NGINX_SSL_REDIRECT"; then
            # 非 443 的 TLS 端口跳转要带端口,否则用户从 HTTP 入口落到不存在的 443(站点链路门同样按此核对)
            local redirect_port=""
            [ "${NGINX_SSL_LISTEN_PORT:-443}" = "443" ] || redirect_port=":${NGINX_SSL_LISTEN_PORT}"
            cat <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

    location / {
        return 301 https://\$host${redirect_port}\$request_uri;
    }
}

server {
    listen ${NGINX_SSL_LISTEN_PORT} ssl${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

${ssl_block}
${hsts_header}

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

${common_locations}
}
EOF
        else
            cat <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    listen ${NGINX_SSL_LISTEN_PORT} ssl${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

${ssl_block}

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

${common_locations}
}
EOF
        fi
    else
        cat <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

${common_locations}
}
EOF
    fi
}

check_nginx_ssl_inputs() {
    if truthy "$NGINX_ENABLE_SSL"; then
        if [ "$NGINX_SERVER_NAME" = "_" ] || [ -z "$NGINX_SERVER_NAME" ]; then
            fail "NGINX_ENABLE_SSL=true requires NGINX_SERVER_NAME to be your HTTPS domain."
        fi
        NGINX_SSL_CERT_FILE="${NGINX_SSL_CERT_FILE:-$NGINX_ETC_DIR/ssl/${NGINX_SERVER_NAME}.pem}"
        NGINX_SSL_KEY_FILE="${NGINX_SSL_KEY_FILE:-$NGINX_ETC_DIR/ssl/${NGINX_SERVER_NAME}.key}"
        $SUDO test -f "$NGINX_SSL_CERT_FILE" || fail "SSL certificate file not found: $NGINX_SSL_CERT_FILE"
        $SUDO test -f "$NGINX_SSL_KEY_FILE" || fail "SSL private key file not found: $NGINX_SSL_KEY_FILE"
    fi
}

resolve_nginx_main_conf() {
    # 源码装的 nginx 主配置不在 /etc/nginx:从 nginx -V 的 --conf-path 推导,
    # 探不到再退回常见默认位置。
    NGINX_MAIN_CONF="$("$NGINX_BIN" -V 2>&1 | tr ' ' '\n' | sed -n 's/^--conf-path=//p')"
    if [ -z "$NGINX_MAIN_CONF" ] || ! $SUDO test -f "$NGINX_MAIN_CONF"; then
        local candidate
        for candidate in "$NGINX_ETC_DIR/nginx.conf" /usr/local/nginx/conf/nginx.conf; do
            if $SUDO test -f "$candidate"; then
                NGINX_MAIN_CONF="$candidate"
                break
            fi
        done
    fi
    [ -n "$NGINX_MAIN_CONF" ] || fail "cannot locate the main nginx.conf (nginx -V has no --conf-path and no default candidate exists)"
}

ensure_site_included() {
    # 包管理器装的 nginx 自带 include /etc/nginx/conf.d/*.conf;源码装的默认
    # 什么都不 include,站点文件写了也不生效。用 nginx -T(实际生效配置)复核,
    # 缺失则备份主配置后往 http 块里插一行 include。
    if $SUDO "$NGINX_BIN" -T 2>/dev/null | grep -qF "configuration file ${NGINX_SITE_FILE}"; then
        return
    fi

    resolve_nginx_main_conf
    echo "Main nginx config ($NGINX_MAIN_CONF) does not include ${NGINX_SITE_FILE}; adding include..."
    if ! $SUDO grep -qE "include[[:space:]]+${NGINX_SITE_FILE}[[:space:]]*;" "$NGINX_MAIN_CONF"; then
        $SUDO cp "$NGINX_MAIN_CONF" "${NGINX_MAIN_CONF}.dorami-bak"
        # 在 http { 之后插一行 include(python 就地改写,GNU / BSD 通用)
        $SUDO python3 - "$NGINX_MAIN_CONF" "$NGINX_SITE_FILE" <<'PY'
import re, sys
path, site = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
new = re.sub(r"^(\s*)http\s*{", lambda m: f"{m.group(0)}\n    include {site};", text, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(new)
PY
        echo "Backed up original config to ${NGINX_MAIN_CONF}.dorami-bak"
    fi

    $SUDO "$NGINX_BIN" -T 2>/dev/null | grep -qF "configuration file ${NGINX_SITE_FILE}" \
        || fail "自动插入 include 后站点文件仍未生效。请手动在 ${NGINX_MAIN_CONF} 的 http { } 块内加入一行:  include ${NGINX_SITE_FILE};  然后重跑本脚本(原配置已备份为 ${NGINX_MAIN_CONF}.dorami-bak)。"
}

validate_nginx_config() {
    local backend_host="$1"
    local backend_port="$2"
    local backend_upstream="http://${backend_host}:${backend_port}"
    local nginx_dump

    echo "Validating Nginx site config..."
    $SUDO grep -F "root ${NGINX_HTML_DIR};" "$NGINX_SITE_FILE" >/dev/null \
        || fail "Nginx site root is not ${NGINX_HTML_DIR} in ${NGINX_SITE_FILE}"
    $SUDO grep -F "location /api/" "$NGINX_SITE_FILE" >/dev/null \
        || fail "Nginx site config does not define location /api/"
    $SUDO grep -F "proxy_pass ${backend_upstream};" "$NGINX_SITE_FILE" >/dev/null \
        || fail "Nginx /api proxy does not point to ${backend_upstream}"
    if truthy "$NGINX_ENABLE_SSL"; then
        $SUDO grep -F "listen ${NGINX_SSL_LISTEN_PORT} ssl" "$NGINX_SITE_FILE" >/dev/null \
            || fail "Nginx SSL listener is not configured on port ${NGINX_SSL_LISTEN_PORT}"
        $SUDO grep -F "ssl_certificate ${NGINX_SSL_CERT_FILE};" "$NGINX_SITE_FILE" >/dev/null \
            || fail "Nginx SSL certificate path is not ${NGINX_SSL_CERT_FILE}"
        $SUDO grep -F "ssl_certificate_key ${NGINX_SSL_KEY_FILE};" "$NGINX_SITE_FILE" >/dev/null \
            || fail "Nginx SSL key path is not ${NGINX_SSL_KEY_FILE}"
        if truthy "$NGINX_SSL_REDIRECT"; then
            $SUDO grep -F 'return 301 https://$host' "$NGINX_SITE_FILE" >/dev/null \
                || fail "Nginx HTTP to HTTPS redirect is not configured"
        fi
    fi

    if ! nginx_dump="$($SUDO "$NGINX_BIN" -T 2>&1)"; then
        echo "$nginx_dump" >&2
        fail "nginx -T failed"
    fi
    grep -F "root ${NGINX_HTML_DIR};" <<<"$nginx_dump" >/dev/null \
        || fail "Enabled Nginx config does not include root ${NGINX_HTML_DIR}"
    grep -F "proxy_pass ${backend_upstream};" <<<"$nginx_dump" >/dev/null \
        || fail "Enabled Nginx config does not include proxy_pass ${backend_upstream}"
    if truthy "$NGINX_ENABLE_SSL"; then
        grep -F "ssl_certificate ${NGINX_SSL_CERT_FILE};" <<<"$nginx_dump" >/dev/null \
            || fail "Enabled Nginx config does not include ssl_certificate ${NGINX_SSL_CERT_FILE}"
        grep -F "ssl_certificate_key ${NGINX_SSL_KEY_FILE};" <<<"$nginx_dump" >/dev/null \
            || fail "Enabled Nginx config does not include ssl_certificate_key ${NGINX_SSL_KEY_FILE}"
    fi

    $SUDO "$NGINX_BIN" -t
}

warn_cookie_secure_if_needed() {
    if ! truthy "$NGINX_ENABLE_SSL"; then
        return
    fi

    local cookie_secure
    cookie_secure="$(ini_get auth cookie_secure false)"
    if ! truthy "$cookie_secure"; then
        echo "WARNING: NGINX_ENABLE_SSL=true but [auth] cookie_secure is not true in $CONFIG_FILE."
        echo "         Set cookie_secure = true after confirming HTTPS access is stable."
    fi
}

# ── 站点参数(现场采样与 --status 也要用,先于动作分派读入;ini 缺失时只有 deploy 动作报错)──
if [ -f "$CONFIG_FILE" ]; then
    NGINX_HTML_DIR="${NGINX_HTML_DIR:-$(ini_get nginx html_dir /var/www/my_site)}"
    # 可选:dist 复制到宿主目录(sudo 归属、o+rX)再让 html_dir 指过去——仓库在 /root 之类 nginx worker 穿不过的位置时用
    NGINX_RELEASES_DIR="${NGINX_RELEASES_DIR:-$(ini_get nginx releases_dir "")}"
    NGINX_SITE_NAME="${NGINX_SITE_NAME:-$(ini_get nginx site_name dorami)}"
    NGINX_SERVER_NAME="${NGINX_SERVER_NAME:-$(ini_get nginx server_name _)}"
    NGINX_LISTEN_PORT="${NGINX_LISTEN_PORT:-$(ini_get nginx listen_port 80)}"
    NGINX_LISTEN_OPTIONS="${NGINX_LISTEN_OPTIONS:-$(ini_get nginx listen_options default_server)}"
    NGINX_DISABLE_DEFAULT_SITE="${NGINX_DISABLE_DEFAULT_SITE:-$(ini_get nginx disable_default_site true)}"
    NGINX_ENABLE_SSL="${NGINX_ENABLE_SSL:-$(ini_get nginx enable_ssl false)}"
    NGINX_SSL_LISTEN_PORT="${NGINX_SSL_LISTEN_PORT:-$(ini_get nginx ssl_listen_port 443)}"
    NGINX_SSL_REDIRECT="${NGINX_SSL_REDIRECT:-$(ini_get nginx ssl_redirect true)}"
    NGINX_SSL_CERT_FILE="${NGINX_SSL_CERT_FILE:-$(ini_get nginx ssl_cert_file "")}"
    NGINX_SSL_KEY_FILE="${NGINX_SSL_KEY_FILE:-$(ini_get nginx ssl_key_file "")}"
    NGINX_ENABLE_HSTS="${NGINX_ENABLE_HSTS:-$(ini_get nginx enable_hsts false)}"
    BACKEND_PROXY_HOST="${BACKEND_PROXY_HOST:-$(ini_get nginx backend_proxy_host 127.0.0.1)}"
    SERVER_PORT="$(ini_get server port 8088)"
    BACKEND_PROXY_PORT="${BACKEND_PROXY_PORT:-$(ini_get nginx backend_proxy_port "$SERVER_PORT")}"
    DB_URL="$(ini_get storage database_url "sqlite:///data/cms_data.db")"
    case "$DB_URL" in
        sqlite:///*) BM_DB_PATH="$(bm_realpath "${DB_URL#sqlite:///}")" ;;
        *) BM_DB_PATH="" ;;
    esac
else
    NGINX_HTML_DIR="${NGINX_HTML_DIR:-}"; BACKEND_PROXY_HOST="${BACKEND_PROXY_HOST:-127.0.0.1}"; BACKEND_PROXY_PORT="${BACKEND_PROXY_PORT:-8088}"
    NGINX_SERVER_NAME="${NGINX_SERVER_NAME:-_}"; NGINX_LISTEN_PORT="${NGINX_LISTEN_PORT:-80}"; NGINX_ENABLE_SSL="${NGINX_ENABLE_SSL:-false}"
    NGINX_SSL_LISTEN_PORT="${NGINX_SSL_LISTEN_PORT:-443}"; NGINX_SSL_REDIRECT="${NGINX_SSL_REDIRECT:-true}"; NGINX_RELEASES_DIR="${NGINX_RELEASES_DIR:-}"
    DB_URL=""; BM_DB_PATH=""
fi
export NGINX_HTML_DIR BACKEND_PROXY_HOST BACKEND_PROXY_PORT BM_DB_PATH NGINX_SERVER_NAME NGINX_LISTEN_PORT NGINX_ENABLE_SSL \
    NGINX_SSL_LISTEN_PORT NGINX_SSL_REDIRECT NGINX_RELEASES_DIR

# ── 动作分派 ──
case "$BM_ACTION" in
    status)
        bm_status
        exit 0 ;;
    rollback)
        # 只转发到稳定恢复入口(树外固化执行体),本脚本不参与回滚逻辑(§4.2)
        if [ ! -x "$BM_ENTRY" ]; then
            bm_fail "$BM_RC_NO_TARGET" "没有回滚入口 $BM_ENTRY:本机从未用本形态部署过(真首装或尚未收养)。先 ./deploy.sh --status 查看;旧形态安装请 ./deploy.sh --adopt"
        fi
        exec "$BM_ENTRY" --rollback ${BM_PASS_ARGS[@]+"${BM_PASS_ARGS[@]}"} ;;
esac

# 锁先于一切(§4.2):与 Docker 路径同一把;抢不到 exit 4
export DORAMI_DEPLOY_LOCK_BUSY_RC="$BM_RC_LOCK"
acquire_deploy_lock
bm_install_traps

case "$BM_ACTION" in
    discard)
        bm_sample_running
        bm_discard_txn
        exit 0 ;;
    adopt)
        declare -F bm_adopt_main >/dev/null || bm_fail "$BM_RC_USAGE" "--adopt 尚未装配"
        [ -f "$CONFIG_FILE" ] || fail "config file not found: $CONFIG_FILE. Create it from config/production.example.ini before deploying."
        need_command uv "uv is assumed to be configured on this server."
        install_system_packages; install_pm2
        NGINX_BIN="$(command -v nginx)"
        check_nginx_ssl_inputs; resolve_nginx_site_file
        bm_adopt_main
        exit 0 ;;
esac

# ── 正向部署:解析目标(tag 模式在 checkout 前经钩子做能力检查 / 未收口事务分派 / 收养)──
export DORAMI_DEPLOY_PRE_EXEC_CHECK=bm_pre_exec_check
if [ -n "$BM_CODE" ]; then
    bm_resolve_code "$BM_CODE"
else
    resolve_deploy_ref ${BM_DEPLOY_ARGS[@]+"${BM_DEPLOY_ARGS[@]}"}
fi
bm_pre_deploy_checks
BM_ORCHESTRATOR_SHA="$(git rev-parse HEAD)"

echo "=================================================="
echo "  Dorami production deploy (bare-metal) — ${DORAMI_BUILD_REF}"
echo "=================================================="
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
    fail "config file not found: $CONFIG_FILE. Create it from config/production.example.ini before deploying."
fi

need_command uv "uv is assumed to be configured on this server."

echo "[1/7] Installing system dependencies..."
install_system_packages
install_pm2

# sudo 的 secure_path 往往不含手装 nginx 的目录,后续 sudo 调用一律走绝对路径
NGINX_BIN="$(command -v nginx)"
echo "Using nginx binary: $NGINX_BIN"

echo "[2/7] Validating production config..."
warn_cookie_secure_if_needed
check_nginx_ssl_inputs
resolve_nginx_site_file
# 既有部署证据在本脚本创建任何目录 / 事务之前采样一次(首装门用它,§4.1;自己建的目录不能当证据)
bm_sample_running
bm_snapshot_evidence
# 磁盘预算(release 材料按完整体量算;DORAMI_DEPLOY_MIN_FREE_GB,默认 2)
python3 - "$BM_REPO" "${DORAMI_DEPLOY_MIN_FREE_GB:-2}" <<'PY' || fail "磁盘不足(DORAMI_DEPLOY_MIN_FREE_GB)"
import shutil, sys
free = shutil.disk_usage(sys.argv[1]).free / 1024 ** 3
need = float(sys.argv[2])
print(f"    可用磁盘 {free:.1f} GB(要求 ≥ {need:g} GB)")
sys.exit(0 if free >= need else 1)
PY
PODCAST_ARTIFACT_ROOT="${DORAMI_PODCAST_ARTIFACT_ROOT_DIR:-$(ini_get podcast_artifacts root_dir data/podcast-artifacts)}"
mkdir -p logs data "$PODCAST_ARTIFACT_ROOT"
case "$DB_URL" in
    sqlite:///*) mkdir -p "$(dirname "${DB_URL#sqlite:///}")" ;;
esac

# ── 只读预检:身份与回滚点(§4.1)──
echo "    身份与回滚点..."
bm_determine_prev

# ── 开事务(§4.3):release 目录 → controller → in-progress → 恢复入口;此后任何宿主改动都有发现入口 ──
TXN_ID="$(bm_txn_id "${DORAMI_BUILD_SHA:0:7}")"
RELEASE="$BM_RELEASES_DIR/$TXN_ID"
# dirty 固化的排除集合里由有效配置(ini + 与 config.py 同语义的环境覆盖)生成的项:可变存储的相对根(§4.4)
DB_DIR_FOR_EXCLUDE=""
case "$DB_URL" in sqlite:///*) DB_DIR_FOR_EXCLUDE="$(dirname "${DB_URL#sqlite:///}")" ;; esac
BM_EXTRA_EXCLUDES="$(python3 - "$BM_REPO" "$(ini_get media media_dir data/media)" \
    "${DORAMI_PODCAST_ARTIFACT_ROOT_DIR:-$(ini_get podcast_artifacts root_dir data/podcast-artifacts)}" \
    "${DORAMI_BACKUP_LOCAL_DIR:-$(ini_get backup local_dir data/backups)}" \
    "$DB_DIR_FOR_EXCLUDE" <<'PY'
import os, sys
repo = os.path.realpath(sys.argv[1])
roots = []
for raw in sys.argv[2:]:
    raw = (raw or "").strip()
    if not raw:
        continue
    p = os.path.expanduser(raw)
    if os.path.isabs(p):
        # 绝对路径但落在工作树内(如 DORAMI_PODCAST_ARTIFACT_ROOT_DIR=<repo>/podcast-store)同样是可变存储根
        rp = os.path.realpath(p)
        if rp == repo or not rp.startswith(repo + os.sep):
            continue
        rel = os.path.relpath(rp, repo)
    else:
        rel = os.path.normpath(raw)
    first = rel.split(os.sep)[0]
    if first and first not in (".", "..") and first not in roots:
        roots.append(first)
print(" ".join(roots))
PY
)"
export BM_EXTRA_EXCLUDES
bm_freeze_worktree "$TXN_ID"   # dirty --here 固化成快照(BM_CODE_SHA / BM_DIRTY / BM_PIN_REF)
BM_TXN_KIND="deploy"; BM_TXN_MODE="$DORAMI_DEPLOY_MODE"
BM_TXN_TARGET_JSON="$(python3 -c 'import json, sys; print(json.dumps({"ref": sys.argv[1], "code_sha": sys.argv[2], "head_sha": sys.argv[3], "dirty": sys.argv[4] == "true", "release": sys.argv[5], "venv": None, "dist": sys.argv[5] + "/dist", "pin_ref": sys.argv[6] or None}))' \
    "$DORAMI_BUILD_REF" "$BM_CODE_SHA" "$(git rev-parse HEAD)" "$BM_DIRTY" "$RELEASE" "${BM_PIN_REF:-}")"
BM_TXN_PREV_JSON="$BM_PREV_JSON"
BM_TXN_CAPS_JSON="$(python3 -c 'import json, sys; print(json.dumps({"rollback": sys.argv[1] == "true", "db_restore": True, "reproducible": True}))' "$BM_CAP_ROLLBACK")"
BM_TXN_SITE_JSON="$(python3 - "$NGINX_HTML_DIR" "$NGINX_SITE_FILE" "$NGINX_SITE_ENABLED_FILE" "$NGINX_DEFAULT_SITE_FILE" "$NGINX_SERVER_NAME" \
    "$NGINX_LISTEN_PORT" "$NGINX_ENABLE_SSL" "$NGINX_SSL_LISTEN_PORT" "$NGINX_SSL_REDIRECT" "$BACKEND_PROXY_HOST" "$BACKEND_PROXY_PORT" "$APP_NAME" "$NGINX_BIN" "$CONFIG_FILE" "$NGINX_RELEASES_DIR" <<'PY'
import json, sys
k = ["html_dir", "site_file", "enabled_file", "default_site_file", "server_name", "listen_port", "enable_ssl", "ssl_listen_port",
     "ssl_redirect", "backend_host", "backend_port", "app_name", "nginx_bin", "config_file", "releases_dir"]
print(json.dumps(dict(zip(k, sys.argv[1:]))))
PY
)"
BM_TXN_DB_JSON='{"target": null, "snapshot": null, "snapshot_at": null, "rescue_snapshot": null, "heads_before": [], "plan": null}'
bm_txn_open "$TXN_ID" "$RELEASE"

echo "[3/7] 代码副本 / venv / 挂点(只准备材料,不改在线服务)..."
bm_stage_intent code_archived
bm_archive_code "$BM_CODE_SHA" "$RELEASE"
bm_stage_done code_archived

bm_stage_intent venv_ready
bm_prepare_venv "$RELEASE"
bm_mount_points "$RELEASE" "$BM_VENV_DIR" "$CONFIG_FILE"
deploy_json_set "$BM_IN_PROGRESS" target.venv "$BM_VENV_DIR" || fail "记录 venv 失败"
bm_stage_done venv_ready

echo "    目标上下文检查(requires-python / 路径探针 / 迁移计划 / 首装门)..."
bm_check_requires_python "$RELEASE/app" "$BM_VENV_DIR"
# 路径基准 = last-success **持久化**的探针结果(paths.mutable),不用当前 ini 重算(§4.7;codex R1 P1-02);
# 旧 manifest 没有 paths 时无法证明历史布局 → 只能显式重设基准
BASELINE_PROBE=""; PATHS_FORCED_REBASE=0
# 基准来自 last-success 本身,与本次有没有回滚点(prev)无关:身份冲突用 --no-rollback-guarantee 放行时,存储布局照样要核
if [ -f "$BM_LAST_SUCCESS" ]; then
    BASELINE_PROBE="$(deploy_json_get "$BM_LAST_SUCCESS" paths "")"
    if [ -z "$BASELINE_PROBE" ] || [ "$BASELINE_PROBE" = "null" ]; then
        if [ "${DORAMI_DEPLOY_ACCEPT_PATH_CHANGE:-0}" = 1 ] && [ "$BM_NO_ROLLBACK_GUARANTEE" = 1 ]; then
            echo "    ⚠️  last-success 没有持久化的路径基准(paths):按显式重设基准处理(prev=null,没有跨存储布局的回滚保证)"
            BASELINE_PROBE=""; PATHS_FORCED_REBASE=1
        else
            bm_fail "$BM_RC_PATH_PROBE" "last-success 没有持久化的路径基准(paths 字段缺失或损坏),无法证明历史存储布局;核对 $BM_LAST_SUCCESS,或显式重设基准:DORAMI_DEPLOY_ACCEPT_PATH_CHANGE=1 ./deploy.sh … --no-rollback-guarantee"
        fi
    fi
fi
BM_BASELINE_DB_TARGET=""
[ -n "$BASELINE_PROBE" ] && BM_BASELINE_DB_TARGET="$(deploy_json_get "$BM_LAST_SUCCESS" db.target "")"
export BM_BASELINE_DB_TARGET
bm_check_paths "$RELEASE" "$RELEASE/app" "$BM_VENV_DIR" "$CONFIG_FILE" "$BASELINE_PROBE"
if [ "${BM_PATHS_REBASED:-0}" = 1 ] || [ "$PATHS_FORCED_REBASE" = 1 ]; then
    # 重设存储基准:本次没有回滚点(prev=null / rollback=false),新布局成为之后部署的基准
    deploy_json_set "$BM_IN_PROGRESS" prev null json && deploy_json_set "$BM_IN_PROGRESS" capabilities.rollback false json \
        || fail "记录重设基准失败"
    BM_PREV_JSON="null"; BM_CAP_ROLLBACK=false
    bm_persist_paths "$BM_PROBE_JSON" "${BM_PATHS_REBASED_FROM:-$(deploy_json_get "$BM_LAST_SUCCESS" paths '{}')}"
else
    bm_persist_paths "$BM_PROBE_JSON"
fi
if [ "$BM_DB_IS_SQLITE" != 1 ]; then
    # 非 SQLite(§4.1 / codex R1 P2-03):显式放弃库恢复能力后完整继续——不做快照 / 计划 / 恢复,迁移与 taxonomy 照常在目标上下文执行
    [ "$BM_NO_ROLLBACK_GUARANTEE" = 1 ] \
        || bm_fail "$BM_RC_IDENTITY" "数据库不是 SQLite(${BM_DB_BACKEND:-unknown};快照 / 恢复协议只覆盖 SQLite):确认放弃库恢复能力可加 --no-rollback-guarantee 继续"
    echo "    ⚠️  数据库不是 SQLite(${BM_DB_BACKEND}):--no-rollback-guarantee 显式继续,capabilities.db_restore=false(回滚不处置库,迁移照常)"
    deploy_json_set "$BM_IN_PROGRESS" capabilities.db_restore false json \
        && deploy_json_set "$BM_IN_PROGRESS" db.target null json \
        && deploy_json_set "$BM_IN_PROGRESS" db.backend "$BM_DB_BACKEND" \
        && deploy_json_set "$BM_IN_PROGRESS" db.url_summary "$BM_DB_URL_SUMMARY" \
        && deploy_json_set "$BM_IN_PROGRESS" db.plan "{\"status\": \"n/a\", \"detail\": \"非 SQLite 库(${BM_DB_BACKEND}):不做计划 / 快照 / 恢复\", \"pending_count\": 0}" json \
        || fail "记录非 SQLite 库信息失败"
    PLAN_STATUS="n/a"
else
    deploy_json_set "$BM_IN_PROGRESS" db.target "$BM_DB_TARGET" && deploy_json_set "$BM_IN_PROGRESS" db.backend sqlite || fail "记录 DB 目标失败"
    PLAN_JSON="$(bm_db_plan "$RELEASE/app" "$BM_VENV_DIR" "$CONFIG_FILE" "$BM_DB_TARGET")" || fail "迁移计划执行失败"
    PLAN_STATUS="$(printf '%s' "$PLAN_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')"
    PLAN_PENDING="$(printf '%s' "$PLAN_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["pending_count"])')"
    PLAN_DETAIL="$(printf '%s' "$PLAN_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["detail"])')"
    deploy_json_set "$BM_IN_PROGRESS" db.plan "$PLAN_JSON" json || fail "记录迁移计划失败"
    deploy_json_set "$BM_IN_PROGRESS" db.heads_before "$(printf '%s' "$PLAN_JSON" | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)["current_heads"]))')" json || true
    case "$PLAN_STATUS" in
        compatible) echo "    迁移计划:compatible,待执行 ${PLAN_PENDING} 个" ;;
        legacy_adoption_required) echo "    迁移计划:有业务表无 alembic_version,ensure_migrated 会对齐基线并收养(待执行 ${PLAN_PENDING})" ;;
        fresh)
            bm_fresh_gate fresh
            deploy_json_set "$BM_IN_PROGRESS" capabilities.rollback "$BM_CAP_ROLLBACK" json || true ;;
        incompatible)
            bm_fail "$BM_RC_STEP" "数据库与目标代码的迁移图不兼容(${PLAN_DETAIL});先按 docs/deploy-baremetal.md「回滚」恢复对应快照再重试" ;;
        *)
            bm_fail "$BM_RC_STEP" "迁移计划失败:${PLAN_DETAIL}" ;;
    esac
fi

echo "[4/7] Building frontend(在 release 副本上构建)..."
bm_stage_intent dist_built
bm_build_dist "$RELEASE" "$TXN_ID"
deploy_json_set "$BM_IN_PROGRESS" target.dist "$BM_DIST_DIR" || fail "记录 dist 失败"
bm_stage_done dist_built

echo "[5/7] Configuring Nginx(候选 → 变更集 → 落盘 → 校验)..."
bm_stage_intent nginx_prepared          # 首次宿主写入 intent(§4.3):此后事务不再能自动归档
bm_nginx_prepare "$RELEASE" "$BACKEND_PROXY_HOST" "$BACKEND_PROXY_PORT"
bm_stage_done nginx_prepared

echo "[6/7] 切换(快照 → 迁移 → taxonomy → 停旧进程 → 切链接 → 起新进程 → pm2 save → nginx reload)..."
bm_stage_intent db_snapshotted
if [ "$BM_DB_IS_SQLITE" = 1 ]; then
    bm_db_snapshot "$TXN_ID" "$BM_DB_TARGET"
else
    echo "    DB 快照:非 SQLite 库(${BM_DB_BACKEND}),不做"
fi
bm_stage_done db_snapshotted

bm_stage_intent db_migrated
bm_db_migrate "$RELEASE/app" "$BM_VENV_DIR" "$CONFIG_FILE"
bm_stage_done db_migrated

bm_stage_intent process_stopped
bm_pm2_stop
bm_stage_done process_stopped

bm_stage_intent links_switched
bm_switch_links "$RELEASE/app" "$BM_DIST_DIR"
bm_stage_done links_switched

bm_stage_intent process_started
bm_pm2_start "$RELEASE" "$DORAMI_BUILD_REF" "$BM_CODE_SHA" "$CONFIG_FILE"
bm_stage_done process_started

ensure_nginx_running_or_reload

echo "[7/7] 两级健康门..."
bm_stage_intent health_ok
EXPECT_VERSION="$(grep -o '__version__ = "[^"]*"' "$RELEASE/app/src/version.py" | head -1 | sed 's/.*"\(.*\)"/\1/')"
if bm_health_gates "$BM_DIST_DIR" "$EXPECT_VERSION" "$DORAMI_BUILD_REF" "$BM_CODE_SHA"; then
    bm_stage_done health_ok
    bm_stage_intent promoted
    bm_txn_promote
    bm_cleanup
    echo ""
    if [ "${DORAMI_DEPLOY_MODE}" = "tag" ]; then
        echo "Deploy complete. 发布版 ${DORAMI_BUILD_REF}(${BM_CODE_SHA:0:7});release $RELEASE"
    else
        echo "Deploy complete. ⚠️  非发布版:${DORAMI_BUILD_REF}(${BM_CODE_SHA:0:7});release $RELEASE"
    fi
    exit 0
fi

bm_alert_health_failed "$DORAMI_BUILD_REF" "$BM_CODE_SHA" "$BM_GATE_REASON"
exit 1
