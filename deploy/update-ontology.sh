#!/usr/bin/env bash
# Fetch the latest ontology file from the configured ARI repo/branch and replace
# the local runtime ontology when it has changed. The app reloads it by mtime.
# An ontology that owl:imports the UBERON slice (ari_uberon.owl, beside it in
# the data repo) gets the slice too: the app resolves the import from the
# ontology's folder, and fails to load without it.
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
REMOTE_PATH="${GITHUB_ONTOLOGY_PATH:-ontologies/ari_t1d.owl}"
LOCAL_PATH="${ARI_ONTOLOGY_FILE:-$APP_DIR/ontologies/ari_t1d.owl}"
TOKEN="${GITHUB_SERVICE_TOKEN:-}"

SLICE_IRI="http://www.aurint.org/ontologies/ari_uberon"
SLICE_REMOTE_PATH="$(dirname "$REMOTE_PATH")/ari_uberon.owl"
SLICE_LOCAL_PATH="$(dirname "$LOCAL_PATH")/ari_uberon.owl"

TMP="$(mktemp)"
SLICE_TMP="$(mktemp)"
cleanup() { rm -f "$TMP" "$SLICE_TMP"; }
trap cleanup EXIT

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

fetch "$REMOTE_PATH" "$TMP"
mkdir -p "$(dirname "$LOCAL_PATH")"

# The slice goes in first, so the ontology that imports it never loads without it.
if grep -qF "rdf:resource=\"$SLICE_IRI\"" "$TMP"; then
  fetch "$SLICE_REMOTE_PATH" "$SLICE_TMP"
  install_if_changed "$SLICE_TMP" "$SLICE_LOCAL_PATH" "$SLICE_REMOTE_PATH"
fi
install_if_changed "$TMP" "$LOCAL_PATH" "$REMOTE_PATH"
