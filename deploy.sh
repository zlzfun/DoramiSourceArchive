#!/bin/bash
set -euo pipefail

# Always run from the project root, no matter where the command is invoked.
cd "$(dirname "$0")"

APP_NAME="${PM2_APP_NAME:-dorami-backend-v2}"
VENV_DIR="${VENV_DIR:-venv}"
CONFIG_FILE="${DORAMI_CONFIG_FILE:-$(pwd)/config/production.ini}"
NGINX_HTML_DIR="${NGINX_HTML_DIR:-/var/www/my_site}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-dorami}"
NGINX_SERVER_NAME="${NGINX_SERVER_NAME:-_}"
NGINX_LISTEN_PORT="${NGINX_LISTEN_PORT:-80}"
NGINX_LISTEN_OPTIONS="${NGINX_LISTEN_OPTIONS:-default_server}"
NGINX_DISABLE_DEFAULT_SITE="${NGINX_DISABLE_DEFAULT_SITE:-true}"
BACKEND_PROXY_HOST="${BACKEND_PROXY_HOST:-127.0.0.1}"

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

    resolve_nginx_site_file
    echo "Writing Nginx site config: $NGINX_SITE_FILE"

    $SUDO mkdir -p "$(dirname "$NGINX_SITE_FILE")"
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

    if ! nginx_dump="$($SUDO nginx -T 2>&1)"; then
        echo "$nginx_dump" >&2
        fail "nginx -T failed"
    fi
    grep -F "root ${NGINX_HTML_DIR};" <<<"$nginx_dump" >/dev/null \
        || fail "Enabled Nginx config does not include root ${NGINX_HTML_DIR}"
    grep -F "proxy_pass ${backend_upstream};" <<<"$nginx_dump" >/dev/null \
        || fail "Enabled Nginx config does not include proxy_pass ${backend_upstream}"

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

echo "=================================================="
echo "  Dorami production deploy"
echo "=================================================="
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
    fail "config file not found: $CONFIG_FILE. Create it from config/production.example.ini before deploying."
fi

need_command uv "uv is assumed to be configured on this server."

SERVER_PORT="$(ini_get server port 8088)"
BACKEND_PROXY_PORT="${BACKEND_PROXY_PORT:-$SERVER_PORT}"

echo "[1/7] Installing system dependencies..."
install_system_packages
install_pm2

echo "[2/7] Validating production config..."
validate_models_if_needed

echo "[3/7] Installing backend dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    uv venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
uv pip install -e .

mkdir -p logs

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
