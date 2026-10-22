#!/usr/bin/env bash
# Pull the latest ARI Metadata Manager app code and restart the app when code
# changes. Ontology data is refreshed separately by update-ontology.sh.
set -euo pipefail
# The checkout this script lives in, so the staging copy (STAGING.md) updates
# itself and never the live app.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# sed exits 0 when the key is absent (unlike grep, whose exit 1 under
# `set -euo pipefail` would kill the script before the fetch ever runs);
# `|| true` also covers a missing .env file.
BRANCH="$(sed -n 's/^APP_REPO_BRANCH=//p' .env 2>/dev/null || true)"
BRANCH="${BRANCH:-main}"
PORT="$(sed -n 's/^PORT=//p' .env 2>/dev/null || true)"
PORT="${PORT:-8001}"
SERVICE="$(sed -n 's/^APP_SERVICE=//p' .env 2>/dev/null || true)"
SERVICE="${SERVICE:-ari-mm}"
git fetch --quiet origin "$BRANCH"

# A stash entry's content: the tree it recorded, plus its untracked files' tree
# when it has one. Two entries with the same content hold the same changes.
stash_content() {
  git rev-parse -q --verify "$1^{tree}" || return 0
  git rev-parse -q --verify "$1^3^{tree}" || true
}

# A hard reset discards uncommitted changes to tracked files, so any are stashed
# first rather than silently destroyed. Recover with `git stash list` /
# `git stash apply` on the server. `--include-untracked` also captures new
# untracked (non-ignored) files; ignored files like .env and the runtime
# ontology are left untouched (a reset never touches them either).
if [ -n "$(git status --porcelain)" ]; then
  echo "WARNING: local changes present before update; stashing them (git stash list) instead of discarding:" >&2
  git status --porcelain >&2
  before="$(git rev-parse -q --verify refs/stash || true)"
  git stash push --include-untracked \
    -m "pre-update autostash $(date +%Y%m%d_%H%M%S)" >/dev/null 2>&1 || true
  after="$(git rev-parse -q --verify refs/stash || true)"
  # Something that rewrites tracked files on every run would otherwise add one
  # identical entry per run, indefinitely. update-mappings.sh fetching over the
  # tracked mapping files did exactly that, an entry every ten minutes, with the
  # warning above as the only trace. An entry identical to the one before it
  # holds nothing new, so it is dropped and the repetition is reported as the
  # fault it is.
  if [ -n "$before" ] && [ "$after" != "$before" ] \
     && [ "$(stash_content 'stash@{0}')" = "$(stash_content 'stash@{1}')" ]; then
    git stash drop --quiet 'stash@{0}'
    echo "ERROR: the previous run already stashed exactly these changes (now stash@{0}); not stashing them again. Something on this server rewrites tracked files on every run: find and stop it." >&2
  fi
fi

git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/$BRANCH"

# The runtime ontology is not tracked here; update-ontology.sh owns it. Running
# it now means a restart below never starts the app on a missing or stale file.
"$REPO/deploy/update-ontology.sh"

# Compare against the commit the RUNNING app was started from, not the HEAD this
# script found. Comparing with HEAD lost every deferred restart: the deferral
# left HEAD at the new commit, so the next run saw nothing to do and the app
# kept serving the old code (and, before base() reloaded by mtime, the old
# ontology) until someone restarted it by hand.
head="$(git rev-parse --short HEAD)"
health="$(curl -fsS --max-time 5 "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
running="$(printf '%s' "$health" | sed -n 's/.*"version":[[:space:]]*"[^"(]*(\([0-9a-f]*\),.*/\1/p')"
if [ "$running" = "$head" ]; then
  echo "Already running $head; no restart."
  exit 0
fi

# Do not restart out from under a curator who has unpublished work in flight.
# A restart drops the in-memory per-user services; the working copies on disk
# survive it, but the curator loses their in-progress request and their review
# session's unsaved tail. This defers the restart to the next timer tick, so an
# idle window is found within ten minutes rather than the deploy interrupting
# someone mid-edit. Set ARI_FORCE_RESTART=1 to override for an urgent fix.
if [ "${ARI_FORCE_RESTART:-0}" != "1" ]; then
  # worlds_in_memory > 0 means at least one curator has edited since the last
  # restart. jq is not assumed; this reads the one field with sed.
  worlds="$(printf '%s' "$health" | sed -n 's/.*"worlds_in_memory":[[:space:]]*\([0-9]*\).*/\1/p')"
  if [ -n "$worlds" ] && [ "$worlds" -gt 0 ]; then
    echo "Running ${running:-unknown}, code is at $head, but $worlds curator working" \
         "cop(ies) are live — deferring the restart to the next run." >&2
    exit 0
  fi
fi

sudo systemctl restart "$SERVICE"
echo "Updated to origin/$BRANCH @ $head (was ${running:-not running}); restarted."
