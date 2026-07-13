#!/usr/bin/env bash
# Fetch the latest mapping TSV files from the configured ARI repo/branch and
# restart the app only when any local runtime mapping changes.
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
TOKEN="${GITHUB_SERVICE_TOKEN:-}"

# Default mapping files if not configured. Format: remote_path:local_path
DEFAULT_MAPPINGS="mappings/ari.equivalencies.tsv:$APP_DIR/mappings/ari.equivalencies.tsv
mappings/ari.mis_curated_synonyms.tsv:$APP_DIR/mappings/ari.mis_curated_synonyms.tsv
mappings/ari.predicted.sssom.tsv:$APP_DIR/mappings/ari.predicted.sssom.tsv
mappings/ari.sssom.tsv:$APP_DIR/mappings/ari.sssom.tsv
mappings/ari.synonym_blocklist.tsv:$APP_DIR/mappings/ari.synonym_blocklist.tsv"

MAPPING_CONFIG="${ARI_MAPPING_FILES:-$DEFAULT_MAPPINGS}"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

any_changed=0

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
    install -m 0644 "$TMP" "$local_path"
    echo "Updated mapping: $OWNER/$REPO:$BRANCH:$remote_path -> $local_path"
    any_changed=1
  fi
done <<EOF
$MAPPING_CONFIG
EOF

if [ "$any_changed" -eq 1 ]; then
  sudo systemctl restart ari-mm
  echo "One or more mappings changed; restarted ari-mm."
else
  echo "No mapping changes; ari-mm not restarted."
fi