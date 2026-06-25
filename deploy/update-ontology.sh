#!/usr/bin/env bash
# Fetch the latest ontology file from the configured ARI repo/branch and restart
# the app only when the local runtime ontology changes.
set -euo pipefail

APP_DIR=/opt/ari/ari-metadata-manager
ENV_FILE="$APP_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

OWNER="${GITHUB_OWNER:-KrishnaTO}"
REPO="${GITHUB_REPO:-ARI}"
BRANCH="${GITHUB_BASE_BRANCH:-main}"
REMOTE_PATH="${GITHUB_ONTOLOGY_PATH:-ontologies/ari_t1d.owl}"
LOCAL_PATH="${ARI_ONTOLOGY_FILE:-$APP_DIR/ontologies/ari_t1d.owl}"
TOKEN="${GITHUB_SERVICE_TOKEN:-}"

TMP="$(mktemp)"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

"/opt/ari/venv/bin/python" - "$OWNER" "$REPO" "$BRANCH" "$REMOTE_PATH" "$TOKEN" "$TMP" <<'PY'
import base64
import json
import sys
import urllib.parse
import urllib.request

owner, repo, branch, path, token, out = sys.argv[1:]
encoded_path = urllib.parse.quote(path)
encoded_ref = urllib.parse.quote(branch)
url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_ref}"
headers = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "ari-metadata-manager-ontology-refresh",
}
if token:
    headers["Authorization"] = f"Bearer {token}"
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=60) as response:
    payload = json.loads(response.read().decode("utf-8"))
if payload.get("encoding") != "base64" or "content" not in payload:
    raise SystemExit(f"GitHub response for {path}@{branch} did not contain base64 file content")
data = base64.b64decode(payload["content"])
with open(out, "wb") as fh:
    fh.write(data)
PY

mkdir -p "$(dirname "$LOCAL_PATH")"
if [ -f "$LOCAL_PATH" ] && cmp -s "$TMP" "$LOCAL_PATH"; then
  echo "Ontology already up to date: $OWNER/$REPO:$BRANCH:$REMOTE_PATH"
  exit 0
fi

install -m 0644 "$TMP" "$LOCAL_PATH"
sudo systemctl restart ari-mm
echo "Updated ontology from $OWNER/$REPO:$BRANCH:$REMOTE_PATH -> $LOCAL_PATH; restarted ari-mm."