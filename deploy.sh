#!/bin/bash
set -euo pipefail

# Always run from the project root, no matter where the command is invoked.
cd "$(dirname "$0")"

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
    if command -v nginx >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && { command -v node >/dev/null 2>&1 || command -v nodejs >/dev/null 2>&1; }; then
        echo "System packages already installed."
        return
    fi

    echo "Installing nginx, node, and npm..."
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update
        $SUDO apt-get install -y nginx nodejs npm
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y nginx nodejs npm
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y nginx nodejs npm
    else
        fail "No supported package manager found. Install nginx, nodejs, and npm manually."
    fi

    need_command nginx "Install nginx and rerun this script."
    need_command npm "Install npm and rerun this script."
    if ! command -v node >/dev/null 2>&1 && ! command -v nodejs >/dev/null 2>&1; then
        fail "node/nodejs is required. Install Node.js and rerun this script."
    fi
}

install_pm2() {
    if command -v pm2 >/dev/null 2>&1; then
        echo "PM2 already installed."
        return
    fi

    echo "Installing PM2..."
    $SUDO npm install -g pm2
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

    if ! nginx_dump="$($SUDO nginx -T 2>&1)"; then
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

    $SUDO nginx -t
}

ensure_nginx_running_or_reload() {
    if command -v systemctl >/dev/null 2>&1 && $SUDO systemctl list-unit-files nginx.service >/dev/null 2>&1; then
        $SUDO systemctl start nginx
        $SUDO nginx -s reload
    elif command -v service >/dev/null 2>&1; then
        $SUDO service nginx start || true
        $SUDO nginx -s reload
    else
        $SUDO nginx -s reload
    fi
}

validate_models_if_needed() {
    local rag_enabled="${DORAMI_RAG_ENABLED:-$(ini_get rag enabled false)}"
    if ! truthy "$rag_enabled"; then
        echo "RAG is disabled; skipping model directory checks."
        return
    fi

    local embedding_model
    local reranker_model
    embedding_model="$(ini_get models embedding_model /opt/dorami/models/bge-m3)"
    reranker_model="$(ini_get models reranker_model /opt/dorami/models/bge-reranker-v2-m3)"

    [ -d "$embedding_model" ] || fail "RAG is enabled but embedding model directory is missing: $embedding_model"
    [ -d "$reranker_model" ] || fail "RAG is enabled but reranker model directory is missing: $reranker_model"
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
echo "  Dorami production deploy"
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

echo "[1/7] Installing system dependencies..."
install_system_packages
install_pm2

echo "[2/7] Validating production config..."
validate_models_if_needed
warn_cookie_secure_if_needed

echo "[3/7] Installing backend dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    uv venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
uv pip install -e .

# Playwright 浏览器：rss_openai_news 节点用 headless Chromium 渲染 openai.com 正文页
# （绕过其 Cloudflare 挑战）。Python 包已由上面的 uv 装好，但浏览器二进制需单独下载——
# playwright 不会在首次启动时自动下载，缺浏览器时该节点只优雅降级为 RSS 摘要、不影响其余节点。
# 三种情形自适应（任何失败都不阻断部署，set -euo pipefail 下已逐一兜底）：
#   1) 已显式指定 PLAYWRIGHT_CHROMIUM_EXECUTABLE → 尊重之，跳过下载；
#   2) playwright 能为当前 OS 下载自带浏览器 → 用它，并装 Linux 系统依赖；
#   3) OS 过新/不受支持（如 Ubuntu 26.04，playwright 拒绝下载）→ 自动探测系统已装的
#      chromium/chrome，export 给后续 pm2 reload/start --update-env（配合 ecosystem.config.js
#      的透传送进后端）。都没有就提示装一个后重跑部署。
echo "    Provisioning Playwright Chromium (for the OpenAI News render node)..."
if [ -n "${PLAYWRIGHT_CHROMIUM_EXECUTABLE:-}" ]; then
    echo "    使用预设的系统 Chromium: $PLAYWRIGHT_CHROMIUM_EXECUTABLE"
elif "$VENV_DIR/bin/playwright" install chromium; then
    $SUDO "$VENV_DIR/bin/playwright" install-deps chromium \
        || echo "    ⚠️  playwright install-deps 失败或不适用；若 OpenAI News 渲染异常请手动装 Chromium 系统依赖。"
else
    SYS_CHROMIUM="$(command -v chromium || command -v chromium-browser || command -v google-chrome || command -v google-chrome-stable || true)"
    if [ -n "$SYS_CHROMIUM" ]; then
        export PLAYWRIGHT_CHROMIUM_EXECUTABLE="$SYS_CHROMIUM"
        echo "    ⚠️  Playwright 自带浏览器装不上（OS 不受支持），已自动改用系统 Chromium: $SYS_CHROMIUM"
    else
        echo "    ⚠️  Playwright 自带浏览器装不上，且未发现系统 Chromium → OpenAI News 将降级为 RSS 摘要。"
        echo "        修复：装一个 Chromium 后重跑 ./deploy.sh，例如  sudo apt-get install -y chromium  （或 snap install chromium / 装 Google Chrome）"
    fi
fi

mkdir -p logs

# 数据库迁移（阶段 2 起 schema 变更走 Alembic）：ensure_migrated 对「有表无版本」的
# 老库先 stamp 基线再 upgrade，避免裸 `alembic upgrade` 对已存在的表重跑建表而失败；
# 全新库则从零建到最新。指向生产库（DORAMI_CONFIG_FILE），失败即终止部署（set -e）。
echo "    Applying database migrations (alembic upgrade head)..."
DORAMI_CONFIG_FILE="$CONFIG_FILE" PYTHONPATH=src "$VENV_DIR/bin/python" -c \
    "from config import settings; from storage.migrations import ensure_migrated; ensure_migrated(settings.storage.database_url)"

echo "[4/7] Building frontend..."
cd frontend
npm install --verbose --no-audit --no-fund --replace-registry-host=always
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
