#!/usr/bin/env bash
# Pull-based redeploy of the Site to the primary domain (see ADR-0001 and
# the GitHub issue tracker's #8/#10 work).
#
# GitHub Actions never gets inbound access to this box - its SSH stays
# allowlisted to a single IP, deliberately not widened to GitHub's runner
# ranges (see webapp/deploy/README.md). So instead of an Action pushing a
# deploy in, this script runs here on a timer and pulls: it fetches
# `main`, and if it has moved since the last run, builds the Site from an
# isolated `git worktree` (never touching a tree something else might be
# mid-read of) and atomically re-points a `current` symlink at it.
#
# `current` is served straight out of the worktree - there is no separate
# "copy into the docroot" step. An earlier version rsynced the build output
# into a directory Docker bind-mounts into the nginx container
# (docker-compose.yml's old `./dist:/usr/share/nginx/html:ro`); that rsync
# was not atomic (a request mid-rsync could see a mix of old/new/deleted
# files), and Docker only resolves a bind mount's *source path* once at
# container start, so replacing that directory wholesale isn't a safe fix
# either. The actual fix: mount the whole `site/` parent directory once
# (`./site:/site:ro`) and have nginx's `root` (see nginx/daugavpils.conf)
# traverse a `current` symlink *inside* it - Docker doesn't cache the
# target of an internal symlink, so nginx re-resolves it fresh on every
# request, and `ln -sfn` is a single atomic syscall.
#
# `current-checkout` (see GitHub issue #14) is the same idea for
# review_app: it needs read access to the live `bands/**/*.yaml` tree,
# not the built HTML `current` points at, so it gets its own symlink,
# pointing at the worktree root instead of webapp/dist - re-pointed
# atomically in the same step, from the same successful build, so the two
# symlinks can never disagree about which commit is "live."
#
# Redeploys on *any* change to main, not only approved-proposal ones -
# this box has no local media tree (same limitation the GitHub Pages CI
# mirror already has), so the build runs with SITE_SKIP_LOCAL_VALIDATION=1,
# trusting archive.org's already-published checksums rather than
# re-verifying local files. The maintainer's existing manual
# validate.py -> build.py -> rsync path (full local media recheck) still
# works unchanged as an on-demand, fully-verified alternative; this script
# just stops it being the *only* way the primary domain gets updated.
#
# All paths below are configurable via environment variables so this
# script can be exercised against scratch directories in tests
# (test_sync_and_deploy.py) without touching real server state; the
# defaults are cherry's actual layout (see README.md).
set -euo pipefail

# HTTPS, not SSH: this repo is public, so a plain clone/fetch needs no
# auth at all this way - no deploy key to generate or rotate on this box.
REPO_URL="${REPO_URL:-https://github.com/svalevka/daugavpils.fans.git}"
REPO_DIR="${REPO_DIR:-/opt/daugavpils-fans/repo}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
# CHECKOUT_ROOT and CURRENT_LINK must be direct siblings under the same
# parent directory (that parent is what docker-compose.yml bind-mounts as
# a whole into the nginx container - see this file's header comment) so
# that CURRENT_LINK can be a *relative* symlink: relative is what lets the
# same symlink resolve correctly both here on the host
# (/opt/daugavpils-fans/site/current) and inside the container
# (/site/current), since a host-absolute target wouldn't exist in the
# container's mount namespace at all.
CHECKOUT_ROOT="${CHECKOUT_ROOT:-/opt/daugavpils-fans/site/checkouts}"
CURRENT_LINK="${CURRENT_LINK:-/opt/daugavpils-fans/site/current}"
CURRENT_CHECKOUT_LINK="${CURRENT_CHECKOUT_LINK:-/opt/daugavpils-fans/site/current-checkout}"
STATE_FILE="${STATE_FILE:-/opt/daugavpils-fans/.last-deployed-sha}"
LOCK_DIR="${LOCK_DIR:-/opt/daugavpils-fans/.sync-and-deploy.lock.d}"
KEEP_CHECKOUTS="${KEEP_CHECKOUTS:-3}"
DEPLOY_BASE_DIR="$(dirname "$STATE_FILE")"
LIVE_COMPOSE_FILE="${LIVE_COMPOSE_FILE:-$DEPLOY_BASE_DIR/docker-compose.yml}"
LIVE_NGINX_DIR="${LIVE_NGINX_DIR:-$DEPLOY_BASE_DIR/nginx}"
LIVE_NGINX_CONF="${LIVE_NGINX_CONF:-$LIVE_NGINX_DIR/daugavpils.conf}"

if [ "${1:-}" = "--check-drift" ]; then
  drift_found=0
  target_dir="${WORKTREE_DIR:-}"
  if [ -z "$target_dir" ] || [ ! -d "$target_dir" ]; then
    if [ -d "$REPO_DIR" ]; then
      target_dir="$REPO_DIR"
    elif [ -L "$CURRENT_CHECKOUT_LINK" ]; then
      target_dir="$(dirname "$CHECKOUT_ROOT")/$(readlink "$CURRENT_CHECKOUT_LINK")"
    fi
  fi

  if [ -n "$target_dir" ] && [ -d "$target_dir" ]; then
    repo_compose="$target_dir/webapp/deploy/docker-compose.yml"
    repo_nginx="$target_dir/webapp/deploy/nginx/daugavpils.conf"

    if [ -f "$repo_compose" ]; then
      if [ ! -f "$LIVE_COMPOSE_FILE" ]; then
        echo "DRIFT: $LIVE_COMPOSE_FILE is missing" >&2
        drift_found=1
      elif ! cmp -s "$repo_compose" "$LIVE_COMPOSE_FILE"; then
        echo "DRIFT: $LIVE_COMPOSE_FILE differs from repo $repo_compose" >&2
        drift_found=1
      fi
    fi

    if [ -f "$repo_nginx" ]; then
      if [ ! -f "$LIVE_NGINX_CONF" ]; then
        echo "DRIFT: $LIVE_NGINX_CONF is missing" >&2
        drift_found=1
      elif ! cmp -s "$repo_nginx" "$LIVE_NGINX_CONF"; then
        echo "DRIFT: $LIVE_NGINX_CONF differs from repo $repo_nginx" >&2
        drift_found=1
      fi
    fi
  fi

  if [ "$drift_found" -eq 1 ]; then
    exit 1
  else
    echo "OK: live deployment configuration matches repo"
    exit 0
  fi
fi

for link in "$CURRENT_LINK" "$CURRENT_CHECKOUT_LINK"; do
  if [ "$(dirname "$link")" != "$(dirname "$CHECKOUT_ROOT")" ]; then
    echo "$link and CHECKOUT_ROOT must be direct siblings under the same directory" >&2
    exit 1
  fi
done

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_DIR/pid"
    return 0
  fi

  local is_stale=0
  if [ -f "$LOCK_DIR/pid" ]; then
    local lock_pid
    lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
      echo "stale lock detected: PID $lock_pid is no longer running - reclaiming lock"
      is_stale=1
    fi
  else
    local dir_mtime now_ts
    dir_mtime="$(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0)"
    now_ts="$(date +%s)"
    if [ "$dir_mtime" -gt 0 ] && [ $((now_ts - dir_mtime)) -ge 900 ]; then
      echo "stale lock detected: lock directory older than 15 minutes - reclaiming lock"
      is_stale=1
    fi
  fi

  if [ "$is_stale" -eq 1 ]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo "$$" > "$LOCK_DIR/pid"
      return 0
    fi
  fi

  echo "another run is already in progress ($LOCK_DIR exists) - exiting"
  exit 0
}

acquire_lock
trap 'rm -rf "$LOCK_DIR" 2>/dev/null || true' EXIT

if [ -d "$REPO_DIR" ]; then
  git -C "$REPO_DIR" fetch "$REMOTE" "$BRANCH"
else
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

LATEST_SHA="$(git -C "$REPO_DIR" rev-parse "$REMOTE/$BRANCH")"

PREVIOUS_SHA="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [ -n "$PREVIOUS_SHA" ] && [ "$PREVIOUS_SHA" = "$LATEST_SHA" ]; then
  echo "already deployed $LATEST_SHA - nothing to do"
  exit 0
fi

echo "deploying $LATEST_SHA"

mkdir -p "$CHECKOUT_ROOT"
WORKTREE_DIR="$CHECKOUT_ROOT/$LATEST_SHA"
if [ ! -d "$WORKTREE_DIR" ]; then
  git -C "$REPO_DIR" worktree add --detach "$WORKTREE_DIR" "$LATEST_SHA"
fi

# `set -e` means a failing build (e.g. tools/validate.py or the
# archive.org media-published check inside webapp/build.py) aborts here,
# before CURRENT_LINK/STATE_FILE are ever touched - a broken commit never
# reaches the live site, it just leaves this worktree unlinked for the
# next successful run's pruning to eventually clean up.
(
  cd "$WORKTREE_DIR"
  SITE_SKIP_LOCAL_VALIDATION=1 "$PYTHON_BIN" webapp/build.py
)

# Atomic on POSIX (ln -sfn is a single rename(2) under the hood):
# CURRENT_LINK always resolves to either the fully-old or the fully-new
# checkout, never a half-written one. Relative target - see the
# CHECKOUT_ROOT/CURRENT_LINK sibling contract above.
ln -sfn "$(basename "$CHECKOUT_ROOT")/$LATEST_SHA/webapp/dist" "$CURRENT_LINK"
ln -sfn "$(basename "$CHECKOUT_ROOT")/$LATEST_SHA" "$CURRENT_CHECKOUT_LINK"

echo "$LATEST_SHA" > "$STATE_FILE"

# Sync live deployment configuration (nginx/daugavpils.conf, docker-compose.yml) if changed or drifted (GitHub issue #79)
sync_deployment_config() {
  local new_compose="$WORKTREE_DIR/webapp/deploy/docker-compose.yml"
  local new_nginx="$WORKTREE_DIR/webapp/deploy/nginx/daugavpils.conf"

  if [ ! -f "$new_compose" ] && [ ! -f "$new_nginx" ]; then
    return 0
  fi

  local config_needs_sync=0
  if [ -n "$PREVIOUS_SHA" ]; then
    if git -C "$REPO_DIR" diff --name-only "$PREVIOUS_SHA" "$LATEST_SHA" | grep -qE '^(webapp/deploy/nginx/|webapp/deploy/docker-compose\.yml)'; then
      config_needs_sync=1
    fi
  fi

  if [ -f "$new_compose" ]; then
    if [ ! -f "$LIVE_COMPOSE_FILE" ] || ! cmp -s "$new_compose" "$LIVE_COMPOSE_FILE"; then
      config_needs_sync=1
    fi
  fi

  if [ -f "$new_nginx" ]; then
    if [ ! -f "$LIVE_NGINX_CONF" ] || ! cmp -s "$new_nginx" "$LIVE_NGINX_CONF"; then
      config_needs_sync=1
    fi
  fi

  if [ "$config_needs_sync" -eq 0 ]; then
    echo "deployment configuration unchanged - nothing to sync"
    return 0
  fi

  echo "deployment configuration changed or drifted - syncing"

  local compose_backup=""
  local nginx_backup=""
  if [ -f "$LIVE_COMPOSE_FILE" ]; then
    compose_backup="$(mktemp "${LIVE_COMPOSE_FILE}.bak.XXXXXX")"
    cp "$LIVE_COMPOSE_FILE" "$compose_backup"
  fi
  if [ -f "$LIVE_NGINX_CONF" ]; then
    nginx_backup="$(mktemp "${LIVE_NGINX_CONF}.bak.XXXXXX")"
    cp "$LIVE_NGINX_CONF" "$nginx_backup"
  fi

  # Copy new files into place
  if [ -f "$new_compose" ]; then
    mkdir -p "$(dirname "$LIVE_COMPOSE_FILE")"
    cp "$new_compose" "$LIVE_COMPOSE_FILE"
  fi
  if [ -f "$new_nginx" ]; then
    mkdir -p "$(dirname "$LIVE_NGINX_CONF")"
    cp "$new_nginx" "$LIVE_NGINX_CONF"
  fi

  # Validate
  local validation_failed=0
  if command -v docker >/dev/null 2>&1; then
    if [ -f "$LIVE_COMPOSE_FILE" ]; then
      if ! docker compose -f "$LIVE_COMPOSE_FILE" config >/dev/null 2>&1; then
        echo "ERROR: docker compose config validation failed" >&2
        validation_failed=1
      fi
    fi

    if [ "$validation_failed" -eq 0 ] && [ -f "$LIVE_NGINX_CONF" ]; then
      if docker compose -f "$LIVE_COMPOSE_FILE" ps --services --filter "status=running" 2>/dev/null | grep -q "^nginx$"; then
        if ! docker compose -f "$LIVE_COMPOSE_FILE" exec -T nginx nginx -t >/dev/null 2>&1; then
          echo "ERROR: nginx -t validation failed" >&2
          validation_failed=1
        fi
      fi
    fi
  fi

  if [ "$validation_failed" -eq 1 ]; then
    echo "ALERT: deployment config validation failed! Rolling back to previous files" >&2
    if [ -n "$compose_backup" ]; then
      mv -f "$compose_backup" "$LIVE_COMPOSE_FILE"
    else
      rm -f "$LIVE_COMPOSE_FILE"
    fi
    if [ -n "$nginx_backup" ]; then
      mv -f "$nginx_backup" "$LIVE_NGINX_CONF"
    else
      rm -f "$LIVE_NGINX_CONF"
    fi
    return 1
  fi

  # Reload / restart only on successful validation
  if command -v docker >/dev/null 2>&1 && [ -f "$LIVE_COMPOSE_FILE" ]; then
    if docker compose -f "$LIVE_COMPOSE_FILE" ps --services --filter "status=running" 2>/dev/null | grep -q "^nginx$"; then
      echo "reloading nginx configuration"
      docker compose -f "$LIVE_COMPOSE_FILE" exec -T nginx nginx -s reload || true
    fi
    echo "applying compose service updates"
    docker compose -f "$LIVE_COMPOSE_FILE" up -d || true
  fi

  [ -n "$compose_backup" ] && rm -f "$compose_backup"
  [ -n "$nginx_backup" ] && rm -f "$nginx_backup"

  echo "deployment configuration successfully validated and applied"
  return 0
}

sync_deployment_config || true

# If review_app code changed since the last deploy,
# rebuild and restart review-app so server changes go live without manual intervention.
if [ -n "$PREVIOUS_SHA" ]; then
  if git -C "$REPO_DIR" diff --name-only "$PREVIOUS_SHA" "$LATEST_SHA" | grep -qE '^review_app/'; then
    echo "review_app code or deployment configuration changed ($PREVIOUS_SHA..$LATEST_SHA)"
    COMPOSE_FILE="${COMPOSE_FILE:-$LIVE_COMPOSE_FILE}"
    AUTO_REBUILD_CONTAINERS="${AUTO_REBUILD_CONTAINERS:-1}"
    if [ "$AUTO_REBUILD_CONTAINERS" = "1" ] && command -v docker >/dev/null 2>&1 && [ -f "$COMPOSE_FILE" ]; then
      echo "rebuilding and restarting review-app container"
      docker compose -f "$COMPOSE_FILE" build review-app
      docker compose -f "$COMPOSE_FILE" up -d review-app
    fi
  fi
fi

# Prune worktrees beyond KEEP_CHECKOUTS, oldest (by creation, i.e. mtime -
# each is written exactly once and never touched again) first.
mapfile -t all_checkouts < <(ls -1dt "$CHECKOUT_ROOT"/*/ 2>/dev/null | sed 's:/$::')
if [ "${#all_checkouts[@]}" -gt "$KEEP_CHECKOUTS" ]; then
  for old in "${all_checkouts[@]:$KEEP_CHECKOUTS}"; do
    echo "pruning old checkout $old"
    git -C "$REPO_DIR" worktree remove --force "$old" 2>/dev/null || rm -rf "$old" 2>/dev/null || true
  done
fi
git -C "$REPO_DIR" worktree prune 2>/dev/null || true

if [ -n "${HEARTBEAT_URL:-}" ]; then
  curl -fsS -m 10 --retry 2 "$HEARTBEAT_URL" >/dev/null 2>&1 || echo "WARNING: failed to ping HEARTBEAT_URL" >&2
fi

echo "deployed $LATEST_SHA"
