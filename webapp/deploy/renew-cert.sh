#!/usr/bin/env bash
# Automated Let's Encrypt TLS certificate renewal (see GitHub issue #46).
#
# Runs certbot via Docker using the Cloudflare DNS-01 plugin.
# When a new certificate is issued (detected by comparing the certificate's
# checksum before and after the certbot run), nginx is reloaded gracefully
# without dropping connections.
#
# Configurable via environment variables for testing without touching production paths.
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/daugavpils-fans}"
CERTBOT_DIR="${CERTBOT_DIR:-$BASE_DIR/certbot}"
CLOUDFLARE_INI="${CLOUDFLARE_INI:-$BASE_DIR/cloudflare.ini}"
DOCKER_COMPOSE_FILE="${DOCKER_COMPOSE_FILE:-$BASE_DIR/docker-compose.yml}"
CERT_FILE="${CERT_FILE:-$CERTBOT_DIR/conf/live/daugavpils.fans/fullchain.pem}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
HEARTBEAT_URL="${HEARTBEAT_URL:-}"

# Verify credentials exist before proceeding
if [ ! -f "$CLOUDFLARE_INI" ]; then
    echo "ERROR: Cloudflare credentials not found at $CLOUDFLARE_INI" >&2
    exit 1
fi

# Function to get file hash portably (Linux / macOS)
get_hash() {
    local target="$1"
    if [ -f "$target" ]; then
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum "$target" | awk '{print $1}'
        elif command -v shasum >/dev/null 2>&1; then
            shasum -a 256 "$target" | awk '{print $1}'
        else
            cksum "$target" | awk '{print $1}'
        fi
    else
        echo ""
    fi
}

BEFORE_HASH=$(get_hash "$CERT_FILE")

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running certbot renewal check..."
"$DOCKER_BIN" run --rm \
    -v "$CERTBOT_DIR/conf:/etc/letsencrypt" \
    -v "$CLOUDFLARE_INI:/cloudflare.ini:ro" \
    certbot/dns-cloudflare renew "$@"

AFTER_HASH=$(get_hash "$CERT_FILE")

# Check if certificate changed
if [ -n "$AFTER_HASH" ] && [ "$BEFORE_HASH" != "$AFTER_HASH" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Certificate renewed ($BEFORE_HASH -> $AFTER_HASH). Reloading nginx..."
    "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" exec -T nginx nginx -s reload
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Nginx reloaded successfully."
else
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] No certificate renewal needed or dry-run executed. Nginx reload skipped."
fi

# Optional heartbeat ping for monitoring (e.g. Healthchecks.io)
if [ -n "$HEARTBEAT_URL" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Sending heartbeat ping..."
    curl -fsS --retry 3 --connect-timeout 10 "$HEARTBEAT_URL" >/dev/null 2>&1 || true
fi
