#!/usr/bin/env bash
# Fetch the latest mapping TSV files from the configured ARI repo/branch and
# replace the local copies when they have changed.
#
# No restart: the app reads these files from disk on every request that needs
# them, so a new file is picked up immediately. Restarting used to throw away the
# loaded reference indexes (the next review-page visitor waited ~5 s for them)
# and every curator's in-memory working copy, without the deferral update.sh
# applies while curators have work in progress.
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
TOKEN="${GITHUB_SERVICE_TOKEN:-}"

# Default mapping files if not configured: the mapping files the app reads from the
# ARI repo. The synonym blocklist is the predictor's: without it, prediction refuses
# to run rather than bring back the synonym matches it exists to stop. The symptom
# mapping links each Symptom to its shared symptom class (symptomConcept): without
# it, adding or renaming a symptom is refused.
#
# They are written to ari-mappings/ (gitignored), which is where the app reads
# them (app/config.py MAPPINGS_RUNTIME_DIR). Writing them over the tracked copies
# under mappings/ made update.sh stash and reset them on every tick, and this
# script then saw them "change" and restart the app every ten minutes.
DEFAULT_MAPPINGS="mappings/ari.equivalencies.tsv:$APP_DIR/ari-mappings/ari.equivalencies.tsv
mappings/ari.sssom.tsv:$APP_DIR/ari-mappings/ari.sssom.tsv
mappings/ari.synonym_blocklist.tsv:$APP_DIR/ari-mappings/ari.synonym_blocklist.tsv
notebook/symptom-mapping/symptom_mapping.tsv:$APP_DIR/ari-mappings/symptom_mapping.tsv"

MAPPING_CONFIG="${ARI_MAPPING_FILES:-$DEFAULT_MAPPINGS}"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

fetch_file() {
  local remote_path="$1"
  local owner="$2"
  local repo="$3"
  local branch="$4"
  local token="$5"
  local out="$6"

  local encoded_path encoded_ref url
  encoded_path="$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$remote_path'''))")"
  encoded_ref="$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$branch'''))")"
  url="https://api.github.com/repos/${owner}/${repo}/contents/${encoded_path}?ref=${encoded_ref}"

  local headers=(
    "-H" "Accept: application/vnd.github.raw+json"
    "-H" "User-Agent: ari-metadata-manager-mapping-refresh"
  )
  if [ -n "$token" ]; then
    headers+=("-H" "Authorization: Bearer $token")
  fi

  curl -fsSL --max-time 120 "${headers[@]}" "$url" -o "$out" 2>/dev/null
}

# Iterate over each mapping file specification
while IFS= read -r spec; do
  [ -z "$spec" ] && continue

  remote_path="${spec%%:*}"
  local_path="${spec##*:}"

  if [ -z "$remote_path" ] || [ -z "$local_path" ]; then
    echo "WARNING: skipping invalid mapping spec: $spec" >&2
    continue
  fi

  mkdir -p "$(dirname "$local_path")"

  if ! fetch_file "$remote_path" "$OWNER" "$REPO" "$BRANCH" "$TOKEN" "$TMP"; then
    echo "WARNING: failed to fetch $OWNER/$REPO:$BRANCH:$remote_path — skipping" >&2
    continue
  fi

  if [ -f "$local_path" ] && cmp -s "$TMP" "$local_path"; then
    echo "Mapping already up to date: $OWNER/$REPO:$BRANCH:$remote_path -> $local_path"
  else
    # Replace atomically: the running app may read the file at any moment, and
    # must never see it half-written.
    install -m 0644 "$TMP" "$local_path.new"
    mv -f "$local_path.new" "$local_path"
    echo "Updated mapping: $OWNER/$REPO:$BRANCH:$remote_path -> $local_path"
  fi
done <<EOF
$MAPPING_CONFIG
EOF