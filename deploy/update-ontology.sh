#!/usr/bin/env bash
# Fetch the latest ontology files from the configured ARI repo/branch and replace
# the local runtime copies that have changed. The app reloads when any *.owl in
# the ontology's folder changes (workspace.base()).
#
# The ontology is a set of modules: the disease file (GITHUB_ONTOLOGY_PATH)
# owl:imports its siblings, and the app resolves each import from the folder, so
# it fails to load when one is missing. This fetches every .owl and .xml file in
# the disease file's folder (that includes catalog-v001.xml: publishing formats
# the file with ROBOT, app/owl_format.py, which resolves the imports through it).
# The disease file is installed last, so an import is never missing when it reloads.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"  # this checkout: the live app or the staging copy
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
REMOTE_PATH="${GITHUB_ONTOLOGY_PATH:-ontologies/ari_disease.owl}"
LOCAL_PATH="${ARI_ONTOLOGY_FILE:-$APP_DIR/ontologies/ari_disease.owl}"
TOKEN="${GITHUB_SERVICE_TOKEN:-}"

REMOTE_DIR="$(dirname "$REMOTE_PATH")"
LOCAL_DIR="$(dirname "$LOCAL_PATH")"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch() {  # fetch <repo path> <output file>
"$(dirname "$APP_DIR")/venv/bin/python" - "$OWNER" "$REPO" "$BRANCH" "$1" "$TOKEN" "$2" <<'PY'
import sys
import urllib.parse
import urllib.request

owner, repo, branch, path, token, out = sys.argv[1:]
encoded_path = urllib.parse.quote(path)
encoded_ref = urllib.parse.quote(branch)
url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_ref}"
headers = {
    "Accept": "application/vnd.github.raw+json",
    "User-Agent": "ari-metadata-manager-ontology-refresh",
}
if token:
    headers["Authorization"] = f"Bearer {token}"
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=120) as response:
    data = response.read()
with open(out, "wb") as fh:
    fh.write(data)
PY
}

# Replace atomically: the app reloads the file when its mtime changes, so it must
# never see a half-written ontology. No restart: the reload picks it up on the
# next request, without dropping curators' in-memory work.
install_if_changed() {  # install_if_changed <fetched file> <local path> <remote path>
  if [ -f "$2" ] && cmp -s "$1" "$2"; then
    echo "Already up to date: $OWNER/$REPO:$BRANCH:$3"
    return
  fi
  install -m 0644 "$1" "$2.new"
  mv -f "$2.new" "$2"
  echo "Updated $2 from $OWNER/$REPO:$BRANCH:$3"
}

list_dir() {  # list_dir <repo dir>: the .owl and .xml file names in it
"$(dirname "$APP_DIR")/venv/bin/python" - "$OWNER" "$REPO" "$BRANCH" "$1" "$TOKEN" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

owner, repo, branch, path, token = sys.argv[1:]
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
with urllib.request.urlopen(req, timeout=120) as response:
    entries = json.load(response)
for entry in entries:
    if entry["type"] == "file" and entry["name"].endswith((".owl", ".xml")):
        print(entry["name"])
PY
}

mkdir -p "$LOCAL_DIR"
DISEASE_NAME="$(basename "$REMOTE_PATH")"
NAMES="$(list_dir "$REMOTE_DIR")"
if ! grep -qxF "$DISEASE_NAME" <<<"$NAMES"; then
  echo "ERROR: $REMOTE_PATH is not in $OWNER/$REPO:$BRANCH" >&2
  exit 1
fi

# Fetch everything before installing anything, so a failed fetch leaves the
# local set as it was.
while IFS= read -r name; do
  fetch "$REMOTE_DIR/$name" "$TMP_DIR/$name"
done <<<"$NAMES"

while IFS= read -r name; do
  [ "$name" = "$DISEASE_NAME" ] && continue
  install_if_changed "$TMP_DIR/$name" "$LOCAL_DIR/$name" "$REMOTE_DIR/$name"
done <<<"$NAMES"
install_if_changed "$TMP_DIR/$DISEASE_NAME" "$LOCAL_PATH" "$REMOTE_PATH"
