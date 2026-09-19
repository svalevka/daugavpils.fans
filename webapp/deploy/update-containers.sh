#!/usr/bin/env bash
# Automated container security updates (GitHub issue #82).
#
# Pulls the latest base images (nginx:alpine, python:3.12-slim via review-app build),
# recreates containers if updated, prunes stale images, and checks health.
#
# Runs daily at an off-peak time via daugavpils-fans-update-containers.timer.
# Configurable via environment variables for testing without touching production paths.
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/daugavpils-fans}"
DOCKER_COMPOSE_FILE="${DOCKER_COMPOSE_FILE:-$BASE_DIR/docker-compose.yml}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
CURL_BIN="${CURL_BIN:-curl}"
MAIL_BIN="${MAIL_BIN:-mail}"
HEALTHCHECK_URL_SITE="${HEALTHCHECK_URL_SITE:-https://daugavpils.fans}"
HEALTHCHECK_URL_REVIEW="${HEALTHCHECK_URL_REVIEW:-https://review.daugavpils.fans}"
MAINTAINER_EMAIL="${MAINTAINER_EMAIL:-}"
HEARTBEAT_URL="${HEARTBEAT_URL:-}"

if [ ! -f "$DOCKER_COMPOSE_FILE" ]; then
    echo "ERROR: Docker compose file not found at $DOCKER_COMPOSE_FILE" >&2
    exit 1
fi

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting automated container updates..."

# 1. Pull and update nginx
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Pulling nginx and recreating if updated..."
"$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" pull nginx
"$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" up -d nginx

# 2. Rebuild and update review-app with fresh base image
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Building review-app with --pull and recreating..."
"$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" build --pull review-app
"$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" up -d review-app

# 3. Prune dangling and old image layers to free disk space
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Pruning unused images..."
"$DOCKER_BIN" image prune -f

# 4. Health check
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Performing health checks..."
health_failed=0

check_health() {
    local url="$1"
    local name="$2"
    if [ -n "$url" ]; then
        local http_code
        http_code=$("$CURL_BIN" -k -s -o /dev/null -w "%{http_code}" -m 15 --retry 2 "$url" || echo "000")
        if [[ "$http_code" =~ ^(200|301|302)$ ]]; then
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Health check passed for $name ($url): HTTP $http_code"
        else
            echo "ERROR: Health check failed for $name ($url): HTTP $http_code" >&2
            health_failed=1
        fi
    fi
}

check_health "$HEALTHCHECK_URL_SITE" "Site"
check_health "$HEALTHCHECK_URL_REVIEW" "Review App"

if [ "$health_failed" -ne 0 ]; then
    echo "ALERT: Container health checks failed after update!" >&2
    if [ -n "$MAINTAINER_EMAIL" ] && (command -v "$MAIL_BIN" >/dev/null 2>&1 || [ -x "$MAIL_BIN" ]); then
        echo "Container health checks failed after update at $(date -u)" | "$MAIL_BIN" -s "ALERT: Container update health check failure on $(hostname)" "$MAINTAINER_EMAIL" || true
    fi
    exit 1
fi

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Container updates and health checks completed successfully."

# Optional heartbeat ping for monitoring (e.g. Healthchecks.io)
if [ -n "$HEARTBEAT_URL" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Sending heartbeat ping..."
    "$CURL_BIN" -fsS --retry 3 --connect-timeout 10 "$HEARTBEAT_URL" >/dev/null 2>&1 || true
fi
