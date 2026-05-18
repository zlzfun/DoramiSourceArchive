#!/bin/bash
set -euo pipefail

# Always run from the project root, no matter where the command is invoked.
cd "$(dirname "$0")"

APP_NAME="${PM2_APP_NAME:-dorami-backend-v2}"
VENV_DIR="${VENV_DIR:-venv}"
CONFIG_FILE="${DORAMI_CONFIG_FILE:-$(pwd)/config/production.ini}"
NGINX_HTML_DIR="${NGINX_HTML_DIR:-/var/www/my_site}"

echo "=================================================="
echo "  Dorami production deploy"
echo "=================================================="
echo ""

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: config file not found: $CONFIG_FILE"
    echo "Create it from config/production.example.ini before deploying."
    exit 1
fi

echo "[1/4] Installing backend dependencies..."
if [ ! -d "$VENV_DIR" ]; then
    uv venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
uv sync --active --no-dev --frozen --no-install-project

mkdir -p logs

echo "[2/4] Building frontend..."
cd frontend
npm install --verbose --no-audit --no-fund --replace-registry-host=always
npm run build
cd ..

echo "[3/4] Publishing frontend assets to Nginx directory..."
sudo mkdir -p "$NGINX_HTML_DIR"
sudo rm -rf "${NGINX_HTML_DIR:?}/"*
sudo cp -r frontend/dist/* "$NGINX_HTML_DIR"/
sudo chmod -R 755 "$NGINX_HTML_DIR"

echo "[4/4] Reloading backend and Nginx..."
export DORAMI_CONFIG_FILE="$CONFIG_FILE"

if pm2 describe "$APP_NAME" >/dev/null 2>&1; then
    pm2 reload ecosystem.config.js --only "$APP_NAME" --update-env
else
    pm2 start ecosystem.config.js --update-env
fi

sudo nginx -s reload

echo ""
echo "Deploy complete."
