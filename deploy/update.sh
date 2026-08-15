#!/usr/bin/env bash
# Pull the latest ARI Metadata Manager app code and restart the app when code
# changes. Ontology data is refreshed separately by update-ontology.sh.
set -euo pipefail
REPO=/opt/ari/ari-metadata-manager
cd "$REPO"
# sed exits 0 when the key is absent (unlike grep, whose exit 1 under
# `set -euo pipefail` would kill the script before the fetch ever runs);
# `|| true` also covers a missing .env file.
BRANCH="$(sed -n 's/^APP_REPO_BRANCH=//p' .env 2>/dev/null || true)"
BRANCH="${BRANCH:-main}"
before="$(git rev-parse HEAD 2>/dev/null || echo none)"
git fetch --quiet origin "$BRANCH"

# A hard reset discards uncommitted changes to tracked files. The runtime ontology
# (ontologies/ari_t1d.owl) can be edited in place by not-signed-in writes, so any
# local modifications are stashed first rather than silently destroyed. Recover
# with `git stash list` / `git stash apply` on the server. `--include-untracked`
# also captures new untracked (non-ignored) files; ignored files like .env are
# left untouched (a reset never touches them either).
if [ -n "$(git status --porcelain)" ]; then
  git stash push --include-untracked \
    -m "pre-update autostash $(date +%Y%m%d_%H%M%S)" >/dev/null 2>&1 || true
  echo "WARNING: local changes present before update; stashed (git stash list) instead of discarding." >&2
fi

git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/$BRANCH"
after="$(git rev-parse HEAD)"
if [ "$before" != "$after" ]; then
  sudo systemctl restart ari-mm           # only restart when the branch actually changed
  echo "Updated to origin/$BRANCH @ $(git rev-parse --short HEAD); restarted."
else
  echo "Already up to date (@ $(git rev-parse --short HEAD)); no restart."
fi
