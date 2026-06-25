#!/usr/bin/env bash
# Pull the latest ARI Metadata Manager app code and restart the app when code
# changes. Ontology data is refreshed separately by update-ontology.sh.
set -euo pipefail
REPO=/opt/ari/ari-metadata-manager
cd "$REPO"
BRANCH="$(grep -E '^APP_REPO_BRANCH=' .env 2>/dev/null | cut -d= -f2-)"
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
