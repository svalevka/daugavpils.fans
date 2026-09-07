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

REPO_URL="${REPO_URL:-git@github.com:svalevka/daugavpils.fans.git}"
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
PYTHON_BIN="${PYTHON_BIN:-python3}"

for link in "$CURRENT_LINK" "$CURRENT_CHECKOUT_LINK"; do
  if [ "$(dirname "$link")" != "$(dirname "$CHECKOUT_ROOT")" ]; then
    echo "$link and CHECKOUT_ROOT must be direct siblings under the same directory" >&2
    exit 1
  fi
done

# Atomic, dependency-free overlap guard: `mkdir` either succeeds exactly
# once or fails, with no race window, on every platform - unlike `flock`,
# which isn't even installed by default everywhere this might run.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another run is already in progress ($LOCK_DIR exists) - exiting"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [ -d "$REPO_DIR" ]; then
  git -C "$REPO_DIR" fetch "$REMOTE" "$BRANCH"
else
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

LATEST_SHA="$(git -C "$REPO_DIR" rev-parse "$REMOTE/$BRANCH")"

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$LATEST_SHA" ]; then
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

# Prune worktrees beyond KEEP_CHECKOUTS, oldest (by creation, i.e. mtime -
# each is written exactly once and never touched again) first.
mapfile -t all_checkouts < <(ls -1dt "$CHECKOUT_ROOT"/*/ 2>/dev/null | sed 's:/$::')
if [ "${#all_checkouts[@]}" -gt "$KEEP_CHECKOUTS" ]; then
  for old in "${all_checkouts[@]:$KEEP_CHECKOUTS}"; do
    echo "pruning old checkout $old"
    git -C "$REPO_DIR" worktree remove --force "$old" 2>/dev/null || rm -rf "$old"
  done
fi
git -C "$REPO_DIR" worktree prune

echo "deployed $LATEST_SHA"
