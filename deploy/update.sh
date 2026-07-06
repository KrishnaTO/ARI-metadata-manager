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
git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/$BRANCH"
after="$(git rev-parse HEAD)"
if [ "$before" != "$after" ]; then
  sudo systemctl restart ari-mm           # only restart when the branch actually changed
  echo "Updated to origin/$BRANCH @ $(git rev-parse --short HEAD); restarted."
else
  echo "Already up to date (@ $(git rev-parse --short HEAD)); no restart."
fi
