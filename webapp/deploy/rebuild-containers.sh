#!/usr/bin/env bash
# Privileged container rebuild and reload triggered by sync-and-deploy.sh (GitHub issue #83).
#
# Runs as root via systemd (daugavpils-fans-rebuild.path / daugavpils-fans-rebuild.service)
# to execute privileged Docker commands without granting the sync user docker or sudo access.
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/daugavpils-fans}"
DOCKER_COMPOSE_FILE="${DOCKER_COMPOSE_FILE:-$BASE_DIR/docker-compose.yml}"
REBUILD_MARKER_FILE="${REBUILD_MARKER_FILE:-$BASE_DIR/.rebuild-needed}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
LIVE_NGINX_CONF="${LIVE_NGINX_CONF:-$BASE_DIR/nginx/daugavpils.conf}"

if [ ! -f "$REBUILD_MARKER_FILE" ]; then
  exit 0
fi

TARGET_SHA="$(cat "$REBUILD_MARKER_FILE" 2>/dev/null || echo "unknown")"
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Container rebuild/reload triggered for commit $TARGET_SHA..."

# 1. Validate compose config
if [ -f "$DOCKER_COMPOSE_FILE" ]; then
  if ! "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" config >/dev/null 2>&1; then
    echo "ERROR: docker compose config validation failed" >&2
    exit 1
  fi
fi

# 2. Validate nginx config if running and reload
if [ -f "$LIVE_NGINX_CONF" ] && [ -f "$DOCKER_COMPOSE_FILE" ]; then
  if "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" ps --services --filter "status=running" 2>/dev/null | grep -q "^nginx$"; then
    if ! "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" exec -T nginx nginx -t >/dev/null 2>&1; then
      echo "ERROR: nginx -t validation failed" >&2
      exit 1
    fi
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Reloading nginx configuration..."
    "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" exec -T nginx nginx -s reload || true
  fi
fi

# 3. Rebuild and restart review-app if defined in compose
if [ -f "$DOCKER_COMPOSE_FILE" ]; then
  if "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" config --services 2>/dev/null | grep -q "^review-app$"; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Rebuilding and updating review-app..."
    "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" build review-app
    "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" up -d review-app
  fi
  # Apply any other compose updates (e.g. if compose file itself changed)
  "$DOCKER_BIN" compose -f "$DOCKER_COMPOSE_FILE" up -d || true
fi

# 4. Remove marker file
rm -f "$REBUILD_MARKER_FILE"
echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Container rebuild/reload complete for commit $TARGET_SHA."
