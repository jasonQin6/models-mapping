#!/usr/bin/env bash
# watch_opencode.sh — Poll opencode go.mdx on GitHub for changes.
# If changed, trigger Codex CLI to run the models-mapping skill.
#
# Usage:
#   ./watch_opencode.sh              # check once, trigger if changed
#   ./watch_opencode.sh --force      # ignore hash, always trigger
#   ./watch_opencode.sh --dry-run    # check only, don't trigger codex
#
# Schedule via launchd (daily at 09:00):
#   cp scripts/com.axonhub.watch-opencode.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.axonhub.watch-opencode.plist

set -euo pipefail

REPO="anomalyco/opencode"
BRANCH="dev"
FILE_PATH="packages/web/src/content/docs/zh-cn/go.mdx"
RAW_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/${FILE_PATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HASH_FILE="$SKILL_DIR/references/opencode-source.hash"
LOG_FILE="$SKILL_DIR/references/watch.log"

FORCE=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=true ;;
    --dry-run) DRY_RUN=true ;;
    *)         echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$msg"
  echo "$msg" >> "$LOG_FILE"
}

# --- Fetch raw .mdx content and compute hash ---
fetch_remote_hash() {
  local tmpfile
  tmpfile=$(mktemp)
  trap "rm -f '$tmpfile'" RETURN

  if ! curl -sL --max-time 30 -o "$tmpfile" "$RAW_URL"; then
    log "ERROR: curl failed to fetch $RAW_URL"
    return 1
  fi

  if [[ ! -s "$tmpfile" ]]; then
    log "ERROR: empty response from $RAW_URL"
    return 1
  fi

  shasum -a 256 "$tmpfile" | awk '{print $1}'
}

# --- Compare with stored hash ---
read_stored_hash() {
  if [[ -f "$HASH_FILE" ]]; then
    cat "$HASH_FILE"
  else
    echo ""
  fi
}

store_hash() {
  mkdir -p "$(dirname "$HASH_FILE")"
  echo "$1" > "$HASH_FILE"
}

# --- Trigger Codex CLI ---
trigger_codex() {
  log "TRIGGER: opencode go.mdx changed, invoking codex exec"

  local prompt="Run the models-mapping skill: execute python3 scripts/fetch_data.py --skip hash to force-refresh both opencode and arena data, then run python3 scripts/compute_mapping.py --stdout to produce the updated mapping CSV."

  if $DRY_RUN; then
    log "DRY-RUN: would run: codex exec \"$prompt\""
    return 0
  fi

  cd "$SKILL_DIR"
  codex exec "$prompt" 2>&1 | while IFS= read -r line; do
    log "  codex: $line"
  done

  local exit_code=${PIPESTATUS[0]}
  if [[ $exit_code -eq 0 ]]; then
    log "SUCCESS: codex exec completed"
  else
    log "ERROR: codex exec exited with code $exit_code"
    return 1
  fi
}

# --- Main ---
main() {
  log "--- watch_opencode.sh start ---"

  local remote_hash
  remote_hash=$(fetch_remote_hash) || exit 1
  log "Remote hash: ${remote_hash:0:16}..."

  local stored_hash
  stored_hash=$(read_stored_hash)

  if $FORCE; then
    log "FORCE mode: skipping hash comparison"
  elif [[ "$remote_hash" == "$stored_hash" ]]; then
    log "No changes detected (hash match: ${remote_hash:0:16}...)"
    exit 0
  else
    log "Change detected: stored=${stored_hash:0:16}... remote=${remote_hash:0:16}..."
  fi

  trigger_codex
  store_hash "$remote_hash"
  log "--- watch_opencode.sh done ---"
}

main
