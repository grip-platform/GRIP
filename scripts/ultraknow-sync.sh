#!/usr/bin/env bash
# ULTRAKNOW periodic sync (2026-06-09, V>> /goal ULTRAKNOW).
#
# Two halves, both idempotent:
#   1. KNOWLEDGE: HAL-absorbed memory -> KONO insight feed -> /recall-able.
#      (lib/hal_memory_indexer.py --sync is content-hash deduped; running it
#      twice adds nothing. kono_auto_capture dedupes on fingerprint/id.)
#   2. BACKUP/ACCESS: ~/.claude/drafts/*.md -> Google Drive "GRIP-drafts"
#      folder via gog CLI (incremental: an mtime ledger skips unchanged
#      files; only new/modified drafts upload).
#
# Cron-safe: no prompts (--no-input), bounded output, exits 0 on partial
# Drive failure so the KONO half is never blocked by network issues.
set -u

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
VENV_PY="$CLAUDE_DIR/venv/bin/python"
DRAFTS_DIR="$CLAUDE_DIR/drafts"
LEDGER="$CLAUDE_DIR/state/ultraknow-drive-ledger.txt"   # "<mtime> <path>" lines
LOG_PREFIX="[ultraknow-sync]"
DRIVE_FOLDER_ID="1sDr7nlci0x3RfVWBI9OdiQWsbRFOwFJo"      # GRIP-drafts (licensing@codetonight.co.za)
GOG_BIN="$(command -v gog || echo /opt/homebrew/bin/gog)"

echo "$LOG_PREFIX start $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ---- Half 1: HAL memory -> KONO --------------------------------------------
if [ -x "$VENV_PY" ]; then
  PYTHONPATH="$CLAUDE_DIR" "$VENV_PY" "$CLAUDE_DIR/lib/hal_memory_indexer.py" --sync 2>&1 | tail -1
  PYTHONPATH="$CLAUDE_DIR" "$VENV_PY" "$CLAUDE_DIR/lib/kono_auto_capture.py" 2>&1 | tail -1
else
  echo "$LOG_PREFIX SKIP kono half: venv python missing at $VENV_PY"
fi

# ---- Half 2: drafts -> Google Drive (incremental) ---------------------------
if [ ! -x "$GOG_BIN" ]; then
  echo "$LOG_PREFIX SKIP drive half: gog CLI not found"
  exit 0
fi
mkdir -p "$(dirname "$LEDGER")"
touch "$LEDGER"

uploaded=0
failed=0
while IFS= read -r -d '' f; do
  mtime="$(stat -f %m -- "$f" 2>/dev/null || stat -c %Y -- "$f" 2>/dev/null)"
  key="$mtime $f"
  if grep -qxF -- "$key" "$LEDGER"; then
    continue  # unchanged since last successful upload
  fi
  if "$GOG_BIN" drive upload "$f" --parent="$DRIVE_FOLDER_ID" --no-input --json >/dev/null 2>&1; then
    # Drop any stale ledger line for this path, then record the new mtime.
    grep -vF -- " $f" "$LEDGER" > "$LEDGER.tmp" && mv "$LEDGER.tmp" "$LEDGER"
    printf '%s\n' "$key" >> "$LEDGER"
    uploaded=$((uploaded + 1))
  else
    failed=$((failed + 1))
  fi
done < <(find "$DRAFTS_DIR" -maxdepth 1 -name '*.md' -type f -print0 2>/dev/null)

echo "$LOG_PREFIX drive: uploaded=$uploaded failed=$failed ledger=$(wc -l < "$LEDGER" | tr -d ' ')"
echo "$LOG_PREFIX done $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
exit 0
