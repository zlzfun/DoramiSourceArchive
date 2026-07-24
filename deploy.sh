#!/bin/bash
# 裸机一键部署(intranet 分支专用路径):uv 装依赖 + PM2 托管后端 + 现场构建前端
# + 生成并校验宿主 Nginx 站点配置。
#
# 背景:main 分支的生产推荐路径是 Docker(deploy-docker.sh),本脚本曾于 v3.15.1
# 退役删除;内网机器 Docker 版本过低/不可用,故在 intranet 分支复活并适配现状:
#   - RAG 依赖分离(v3.16):[rag] enabled 且嵌入形态时装 rag-embedded extra;
#   - RAG 双形态(v3.17):远程形态(chroma_url 有值)不装重依赖,但内网无 docker
#     需自行保证 chroma/TEI 服务可达;
#   - [server]/[nginx] 节在本分支的 production.example.ini 中保留(main 上已删)。
# 受限网络:uv 走环境变量 UV_DEFAULT_INDEX=<内网 PyPI 镜像>;npm 走 NPM_REGISTRY=<内网 npm 镜像>。
set -euo pipefail

# Always run from the project root, no matter where the command is invoked.
cd "$(dirname "$0")"

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

APP_NAME="${PM2_APP_NAME:-dorami-backend-v2}"
VENV_DIR="${VENV_DIR:-venv}"
CONFIG_FILE="${DORAMI_CONFIG_FILE:-$(pwd)/config/production.ini}"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

fail() {
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

truthy() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

install_system_packages() {
    # 逐个探测,只装缺失的;包管理器装不上(内网源没有该包)不再直接打断——
    # 允许手动安装的二进制通过后面的复核。
    local missing=()
    command -v nginx >/dev/null 2>&1 || missing+=(nginx)
    command -v npm >/dev/null 2>&1 || missing+=(npm)
    { command -v node >/dev/null 2>&1 || command -v nodejs >/dev/null 2>&1; } || missing+=(nodejs)

    if [ ${#missing[@]} -eq 0 ]; then
        echo "System packages already installed."
        return
    fi

    echo "Installing missing packages: ${missing[*]} ..."
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update || true
        $SUDO apt-get install -y "${missing[@]}" || echo "WARNING: apt-get install failed; will re-check for manually installed binaries."
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y "${missing[@]}" || echo "WARNING: dnf install failed (内网源可能没有这些包); will re-check for manually installed binaries."
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y "${missing[@]}" || echo "WARNING: yum install failed (内网源可能没有这些包); will re-check for manually installed binaries."
    else
        echo "WARNING: no supported package manager found; expecting manually installed binaries."
    fi

    # 安装尝试后逐一复核。找不到时的常见原因:手动安装的命令只在交互 shell 的
    # PATH 里(nvm 写在 ~/.bashrc / 源码装在自定义目录)。脚本已自动追加
    # /usr/sbin:/usr/local/sbin:/usr/local/bin:/usr/local/nginx/sbin 与 nvm 目录,
    # 仍找不到就 `export PATH="$PATH:<安装目录>"` 后重跑本脚本。
    local hint="内网源没有该包时请手动安装,并确保命令对非交互 shell 可见(见脚本头部 PATH 注释)。"
    command -v nginx >/dev/null 2>&1 || fail "nginx not found in script PATH. $hint"
    command -v npm >/dev/null 2>&1 || fail "npm not found in script PATH. $hint"
    if ! command -v node >/dev/null 2>&1 && ! command -v nodejs >/dev/null 2>&1; then
        fail "node/nodejs not found in script PATH. $hint"
    fi
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

resolve_nginx_site_file() {
    if [ -d /etc/nginx/sites-available ] && [ -d /etc/nginx/sites-enabled ]; then
        NGINX_SITE_FILE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
        NGINX_SITE_ENABLED_FILE="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
    else
        NGINX_SITE_FILE="/etc/nginx/conf.d/${NGINX_SITE_NAME}.conf"
        NGINX_SITE_ENABLED_FILE="$NGINX_SITE_FILE"
    fi
}

write_nginx_site_config() {
    local backend_host="$1"
    local backend_port="$2"
    local backend_upstream="http://${backend_host}:${backend_port}"
    local ssl_enabled="false"
    local hsts_header=""

    if truthy "$NGINX_ENABLE_SSL"; then
        ssl_enabled="true"
        if [ "$NGINX_SERVER_NAME" = "_" ] || [ -z "$NGINX_SERVER_NAME" ]; then
            fail "NGINX_ENABLE_SSL=true requires NGINX_SERVER_NAME to be your HTTPS domain."
        fi
        NGINX_SSL_CERT_FILE="${NGINX_SSL_CERT_FILE:-/etc/nginx/ssl/${NGINX_SERVER_NAME}.pem}"
        NGINX_SSL_KEY_FILE="${NGINX_SSL_KEY_FILE:-/etc/nginx/ssl/${NGINX_SERVER_NAME}.key}"
        $SUDO test -f "$NGINX_SSL_CERT_FILE" || fail "SSL certificate file not found: $NGINX_SSL_CERT_FILE"
        $SUDO test -f "$NGINX_SSL_KEY_FILE" || fail "SSL private key file not found: $NGINX_SSL_KEY_FILE"
        if truthy "$NGINX_ENABLE_HSTS"; then
            hsts_header='    add_header Strict-Transport-Security "max-age=15552000" always;'
        fi
    fi

    resolve_nginx_site_file
    echo "Writing Nginx site config: $NGINX_SITE_FILE"

    $SUDO mkdir -p "$(dirname "$NGINX_SITE_FILE")"

    if [ "$ssl_enabled" = "true" ]; then
        if truthy "$NGINX_SSL_REDIRECT"; then
            $SUDO tee "$NGINX_SITE_FILE" >/dev/null <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};
    return 301 https://\$host\$request_uri;
}

server {
    listen ${NGINX_SSL_LISTEN_PORT} ssl${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

    ssl_certificate ${NGINX_SSL_CERT_FILE};
    ssl_certificate_key ${NGINX_SSL_KEY_FILE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
${hsts_header}

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

    location /api/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location /mcp {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF
        else
            $SUDO tee "$NGINX_SITE_FILE" >/dev/null <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    listen ${NGINX_SSL_LISTEN_PORT} ssl${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

    ssl_certificate ${NGINX_SSL_CERT_FILE};
    ssl_certificate_key ${NGINX_SSL_KEY_FILE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

    location /api/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location /mcp {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF
        fi
    else
        $SUDO tee "$NGINX_SITE_FILE" >/dev/null <<EOF
server {
    listen ${NGINX_LISTEN_PORT}${NGINX_LISTEN_OPTIONS:+ ${NGINX_LISTEN_OPTIONS}};
    server_name ${NGINX_SERVER_NAME};

    root ${NGINX_HTML_DIR};
    index index.html;
    client_max_body_size 100m;

    location /api/ {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location /mcp {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_pass ${backend_upstream};
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF
    fi

    if [ "$NGINX_SITE_ENABLED_FILE" != "$NGINX_SITE_FILE" ]; then
        $SUDO ln -sfn "$NGINX_SITE_FILE" "$NGINX_SITE_ENABLED_FILE"
        if truthy "$NGINX_DISABLE_DEFAULT_SITE" && [ -e /etc/nginx/sites-enabled/default ]; then
            echo "Disabling default Nginx site: /etc/nginx/sites-enabled/default"
            $SUDO rm -f /etc/nginx/sites-enabled/default
        fi
    fi

    ensure_site_included
}

resolve_nginx_main_conf() {
    # 源码装的 nginx 主配置不在 /etc/nginx:从 nginx -V 的 --conf-path 推导,
    # 探不到再退回常见默认位置。
    NGINX_MAIN_CONF="$("$NGINX_BIN" -V 2>&1 | tr ' ' '\n' | sed -n 's/^--conf-path=//p')"
    if [ -z "$NGINX_MAIN_CONF" ] || ! $SUDO test -f "$NGINX_MAIN_CONF"; then
        local candidate
        for candidate in /etc/nginx/nginx.conf /usr/local/nginx/conf/nginx.conf; do
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
        $SUDO sed -i "s|^\([[:space:]]*\)http[[:space:]]*{|&\n    include ${NGINX_SITE_FILE};|" "$NGINX_MAIN_CONF"
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
            $SUDO grep -F 'return 301 https://$host$request_uri;' "$NGINX_SITE_FILE" >/dev/null \
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

ensure_nginx_running_or_reload() {
    # 手动安装的 nginx 可能没有 systemd 单元;已在跑就 reload,没在跑则
    # 优先 systemd 起,退回直接执行二进制(sudo 全程用绝对路径,绕开 secure_path)。
    if pgrep -x nginx >/dev/null 2>&1; then
        $SUDO "$NGINX_BIN" -s reload
        return
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^nginx\.service'; then
        $SUDO systemctl start nginx
    elif command -v service >/dev/null 2>&1 && service nginx status >/dev/null 2>&1; then
        $SUDO service nginx start
    else
        $SUDO "$NGINX_BIN"
    fi
}

# RAG 部署形态判定(v3.17 双形态):
#   - enabled=false → 不装重依赖、不查模型;
#   - enabled=true 且 chroma_url 为空 → 嵌入形态:进程内推理,需 rag-embedded extra
#     (torch 科学计算栈 ~1GB)+ 本地模型目录([models] 两个路径必须存在);
#   - enabled=true 且 chroma_url 有值 → 远程形态:重依赖在 chroma/TEI 服务侧,
#     本机不装;内网无 docker 时这两个服务需自行部署,这里只提醒不拦截。
resolve_rag_mode() {
    local rag_enabled="${DORAMI_RAG_ENABLED:-$(ini_get rag enabled false)}"
    local chroma_url="${DORAMI_RAG_CHROMA_URL:-$(ini_get rag chroma_url "")}"
    if ! truthy "$rag_enabled"; then
        RAG_MODE="off"
    elif [ -n "$chroma_url" ]; then
        RAG_MODE="remote"
    else
        RAG_MODE="embedded"
    fi
}

validate_models_if_needed() {
    case "$RAG_MODE" in
        off)
            echo "RAG is disabled; skipping model directory checks."
            ;;
        remote)
            echo "RAG remote mode (chroma_url set): make sure the chroma/TEI services are reachable from this host."
            ;;
        embedded)
            local embedding_model
            local reranker_model
            embedding_model="$(ini_get models embedding_model /opt/dorami/models/bge-m3)"
            reranker_model="$(ini_get models reranker_model /opt/dorami/models/bge-reranker-v2-m3)"

            [ -d "$embedding_model" ] || fail "RAG embedded mode but embedding model directory is missing: $embedding_model"
            [ -d "$reranker_model" ] || fail "RAG embedded mode but reranker model directory is missing: $reranker_model"
            ;;
    esac
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

echo "=================================================="
echo "  Dorami production deploy (bare-metal, intranet)"
echo "=================================================="
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
    fail "config file not found: $CONFIG_FILE. Create it from config/production.example.ini before deploying."
fi

need_command uv "uv is assumed to be configured on this server."

NGINX_HTML_DIR="${NGINX_HTML_DIR:-$(ini_get nginx html_dir /var/www/my_site)}"
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

resolve_rag_mode
echo "RAG mode: $RAG_MODE"

echo "[1/7] Installing system dependencies..."
install_system_packages
install_pm2

# sudo 的 secure_path 往往不含手装 nginx 的目录,后续 sudo 调用一律走绝对路径
NGINX_BIN="$(command -v nginx)"
echo "Using nginx binary: $NGINX_BIN"

echo "[2/7] Validating production config..."
validate_models_if_needed
warn_cookie_secure_if_needed

echo "[3/7] Installing backend dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    uv venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
# 嵌入形态才装 rag-embedded extra(sentence-transformers/torch ~1GB);
# 远程/关闭形态保持瘦身安装(chromadb 客户端在核心依赖里,双模共用)。
if [ "$RAG_MODE" = "embedded" ]; then
    uv pip install -e ".[rag-embedded]"
else
    uv pip install -e .
fi

# Playwright 浏览器:rss_openai_news 节点用 headless Chromium 渲染 openai.com 正文页
# (绕过其 Cloudflare 挑战)。Python 包已由上面的 uv 装好,但浏览器二进制需单独下载——
# playwright 不会在首次启动时自动下载,缺浏览器时该节点只优雅降级为 RSS 摘要、不影响其余节点。
# 内网 reader 部署(runtime role=reader 或不跑采集)用不到该节点,下载失败仅警告不阻断。
# 三种情形自适应(任何失败都不阻断部署,set -euo pipefail 下已逐一兜底):
#   1) 已显式指定 PLAYWRIGHT_CHROMIUM_EXECUTABLE → 尊重之,跳过下载;
#   2) playwright 能为当前 OS 下载自带浏览器 → 用它,并装 Linux 系统依赖;
#   3) OS 过新/不受支持或内网下载不通 → 自动探测系统已装的 chromium/chrome,
#      export 给后续 pm2 reload/start --update-env(配合 ecosystem.config.js
#      的透传送进后端)。都没有就提示装一个后重跑部署。
echo "    Provisioning Playwright Chromium (for the OpenAI News render node)..."
if [ -n "${PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" ]; then
    echo "    使用预设的系统 Chromium: $PLAYWRIGHT_CHROMIUM_EXECUTABLE"
elif "$VENV_DIR/bin/playwright" install chromium; then
    $SUDO "$VENV_DIR/bin/playwright" install-deps chromium \
        || echo "    ⚠️  playwright install-deps 失败或不适用;若 OpenAI News 渲染异常请手动装 Chromium 系统依赖。"
else
    SYS_CHROMIUM="$(command -v chromium || command -v chromium-browser || command -v google-chrome || command -v google-chrome-stable || true)"
    if [ -n "$SYS_CHROMIUM" ]; then
        export PLAYWRIGHT_CHROMIUM_EXECUTABLE="$SYS_CHROMIUM"
        echo "    ⚠️  Playwright 自带浏览器装不上(OS 不受支持或网络不通),已自动改用系统 Chromium: $SYS_CHROMIUM"
    else
        echo "    ⚠️  Playwright 自带浏览器装不上,且未发现系统 Chromium → OpenAI News 将降级为 RSS 摘要。"
        echo "        修复:装一个 Chromium 后重跑 ./deploy.sh,例如  sudo apt-get install -y chromium  (或 snap install chromium / 装 Google Chrome)"
    fi
fi

mkdir -p logs data

# SQLite 只会创建库文件、不会创建父目录:全新 clone 没有 data/,迁移会直接
# "unable to open database file"。从 ini 解析库路径并确保父目录存在
# (sqlite:///relative 与 sqlite:////absolute 两种形式都覆盖;非 sqlite URL 跳过)。
DB_URL="$(ini_get storage database_url "sqlite:///data/cms_data.db")"
case "$DB_URL" in
    sqlite:///*)
        DB_PATH="${DB_URL#sqlite:///}"
        mkdir -p "$(dirname "$DB_PATH")"
        ;;
esac

# 数据库迁移(schema 变更走 Alembic):ensure_migrated 对「有表无版本」的
# 老库先 stamp 基线再 upgrade,避免裸 `alembic upgrade` 对已存在的表重跑建表而失败;
# 全新库则从零建到最新。指向生产库(DORAMI_CONFIG_FILE),失败即终止部署(set -e)。
echo "    Applying database migrations (alembic upgrade head)..."
DORAMI_CONFIG_FILE="$CONFIG_FILE" PYTHONPATH=src "$VENV_DIR/bin/python" -c \
    "from config import settings; from storage.migrations import ensure_migrated; ensure_migrated(settings.storage.database_url)"

echo "[4/7] Building frontend..."
cd frontend
npm install --verbose --no-audit --no-fund --replace-registry-host=always ${NPM_REGISTRY:+--registry=${NPM_REGISTRY}}
npm run build
cd ..

echo "[5/7] Configuring Nginx..."
write_nginx_site_config "$BACKEND_PROXY_HOST" "$BACKEND_PROXY_PORT"
validate_nginx_config "$BACKEND_PROXY_HOST" "$BACKEND_PROXY_PORT"

echo "[6/7] Publishing frontend assets to Nginx directory..."
$SUDO mkdir -p "$NGINX_HTML_DIR"
$SUDO rm -rf "${NGINX_HTML_DIR:?}/"*
$SUDO cp -r frontend/dist/* "$NGINX_HTML_DIR"/
$SUDO chmod -R 755 "$NGINX_HTML_DIR"

# nginx worker(源码装默认 nobody)必须能逐级穿越 html_dir 的每个父目录,
# 任何一级缺 others 的 x 位(如 /var/www 是 700)都会 stat Permission denied →
# try_files 内部重定向循环 → 500。只补穿越位 o+x,不动读写等其它权限。
dir="$NGINX_HTML_DIR"
while [ "$dir" != "/" ] && [ -n "$dir" ]; do
    perms="$($SUDO stat -c '%A' "$dir" 2>/dev/null || echo "")"
    case "$perms" in
        "") ;;            # stat 不到就跳过
        *x|*t) ;;         # others 已有穿越位
        *)
            echo "Adding o+x to $dir (nginx worker needs directory traversal)"
            $SUDO chmod o+x "$dir"
            ;;
    esac
    dir="$(dirname "$dir")"
done

echo "[7/7] Reloading backend and Nginx..."
export DORAMI_CONFIG_FILE="$CONFIG_FILE"

if pm2 describe "$APP_NAME" >/dev/null 2>&1; then
    pm2 reload ecosystem.config.js --only "$APP_NAME" --update-env
else
    pm2 start ecosystem.config.js --update-env
fi

ensure_nginx_running_or_reload

echo ""
echo "Deploy complete."
