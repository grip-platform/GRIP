#!/usr/bin/env bash
# meet-absorb.sh — Absorb Google Meet Gemini transcripts into GRIP
#
# Uses `gog` CLI (https://gogcli.sh) to search Drive for Gemini notes,
# export them as text, and save to GRIP's data directory.
#
# Usage:
#   ./scripts/meet-absorb.sh                  # Absorb latest transcript
#   ./scripts/meet-absorb.sh --all            # Absorb all unprocessed transcripts
#   ./scripts/meet-absorb.sh --search "topic" # Search for specific meeting
#   ./scripts/meet-absorb.sh --list           # List available transcripts
#   ./scripts/meet-absorb.sh --watch          # Poll every 60s for new transcripts

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
TRANSCRIPT_DIR="${CLAUDE_DIR}/data/meet-transcripts"
PROCESSED_LOG="${TRANSCRIPT_DIR}/.processed.jsonl"
GOG_BIN="${GOG_BIN:-$(command -v gog 2>/dev/null || echo "")}"


# Colours (if terminal)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; NC=''
fi

log() { echo -e "${GREEN}[meet-absorb]${NC} $*"; }
warn() { echo -e "${YELLOW}[meet-absorb]${NC} $*" >&2; }
err() { echo -e "${RED}[meet-absorb]${NC} $*" >&2; }

# --dwd flag: delegate to the DWD Python CLI (domain-wide impersonation).
# Must be the first argument to bypass gog pre-flight checks.
if [ "${1:-}" = "--dwd" ]; then
    shift
    exec python3 "$(dirname "$0")/meet-absorb-dwd.py" "$@"
fi

# Pre-flight checks
if [ -z "$GOG_BIN" ]; then
    err "gog CLI not found. Install from https://gogcli.sh (or use --dwd for domain-wide delegation)"
    exit 1
fi

mkdir -p "$TRANSCRIPT_DIR"
touch "$PROCESSED_LOG"

# Check gog auth
if ! "$GOG_BIN" auth list 2>/dev/null | grep -q "drive"; then
    err "No gog Drive auth. Run: gog auth add <email> --services drive"
    exit 1
fi

# Account selection: multi-account stores require explicit account.
# Precedence: caller env > ~/.claude/.env (awk-parsed, no sourcing) > first authed account with drive scope.
if [ -z "${GOG_ACCOUNT:-}" ] && [ -f "${CLAUDE_DIR}/.env" ]; then
    GOG_ACCOUNT=$(awk -F= '/^GOG_ACCOUNT=/{sub(/^GOG_ACCOUNT=/,""); gsub(/"/,""); print; exit}' "${CLAUDE_DIR}/.env" 2>/dev/null)
fi
if [ -z "${GOG_ACCOUNT:-}" ]; then
    GOG_ACCOUNT=$("$GOG_BIN" auth list --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('accounts') or []:
    if 'drive' in (a.get('services') or []):
        print(a['email']); break
" 2>/dev/null || echo "")
fi
if [ -z "${GOG_ACCOUNT:-}" ]; then
    err "Could not resolve GOG_ACCOUNT. Set it in ~/.claude/.env or the caller env."
    exit 1
fi
export GOG_ACCOUNT
log "Using account: $GOG_ACCOUNT"

# --- Functions ---

list_transcripts() {
    "$GOG_BIN" drive search "Notes by Gemini" --json 2>/dev/null | \
        python3 -c "
import json, sys
data = json.load(sys.stdin)
files = data.get('files') or []
for f in files:
    name = f.get('name', '')
    doc_id = f.get('id', '')
    size = f.get('size', '0')
    modified = f.get('modifiedTime', '')[:10]
    print(f'{modified}  {size:>8}B  {doc_id[:12]}...  {name}')
print(f'\n{len(files)} transcript(s) found')
"
}

is_processed() {
    local doc_id="$1"
    grep -q "\"id\":\"${doc_id}\"" "$PROCESSED_LOG" 2>/dev/null
}

absorb_transcript() {
    local doc_id="$1"
    local name="$2"

    if is_processed "$doc_id"; then
        log "Already processed: $name"
        return 0
    fi

    log "Exporting: $name"

    # Export as text via gog
    local output
    output=$("$GOG_BIN" docs export "$doc_id" --format txt --json 2>/dev/null)
    local export_path
    export_path=$(echo "$output" | python3 -c "import json,sys; print(json.load(sys.stdin).get('path',''))" 2>/dev/null || echo "")

    if [ -z "$export_path" ] || [ ! -f "$export_path" ]; then
        # Fallback: check default gog download location
        local gog_dir="$HOME/Library/Application Support/gogcli/drive-downloads"
        export_path=$(ls -1t "$gog_dir"/*"$doc_id"* 2>/dev/null | head -1)
    fi

    if [ -z "$export_path" ] || [ ! -f "$export_path" ]; then
        err "Export failed for $doc_id"
        return 1
    fi

    # Sanitise filename: date + meeting title
    local safe_name
    safe_name=$(echo "$name" | sed 's/ - Notes by Gemini//' | sed 's/[^a-zA-Z0-9._-]/_/g' | sed 's/__*/_/g')
    local dest="${TRANSCRIPT_DIR}/${safe_name}.txt"

    cp "$export_path" "$dest"
    log "Saved: $dest ($(wc -c < "$dest" | tr -d ' ') bytes)"

    # Record as processed
    python3 -c "
import json, datetime
entry = {
    'id': '$doc_id',
    'name': $(python3 -c "import json; print(json.dumps('$name'))"),
    'absorbed_at': datetime.datetime.now().isoformat(),
    'path': '$dest'
}
print(json.dumps(entry))
" >> "$PROCESSED_LOG"

    return 0
}

absorb_latest() {
    local search_query="${1:-Notes by Gemini}"
    local transcripts
    transcripts=$("$GOG_BIN" drive search "$search_query" --json 2>/dev/null)

    local count
    count=$(echo "$transcripts" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('files') or []))")

    if [ "$count" = "0" ]; then
        warn "No transcripts found"
        return 0
    fi

    # Process the latest (first result, sorted by modified time)
    local doc_id name
    doc_id=$(echo "$transcripts" | python3 -c "import json,sys; print(json.load(sys.stdin)['files'][0]['id'])")
    name=$(echo "$transcripts" | python3 -c "import json,sys; print(json.load(sys.stdin)['files'][0]['name'])")

    absorb_transcript "$doc_id" "$name"
}

absorb_all() {
    local transcripts
    transcripts=$("$GOG_BIN" drive search "Notes by Gemini" --json 2>/dev/null)

    echo "$transcripts" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for f in data.get('files') or []:
    print(f['id'] + '\t' + f['name'])
" | while IFS=$'\t' read -r doc_id name; do
        absorb_transcript "$doc_id" "$name" || true
    done
}

watch_mode() {
    local interval="${1:-60}"
    log "Watching for new transcripts every ${interval}s (Ctrl+C to stop)"
    while true; do
        absorb_latest
        sleep "$interval"
    done
}

# --- Main ---

case "${1:-latest}" in
    --list|-l)
        list_transcripts
        ;;
    --all|-a)
        absorb_all
        ;;
    --search|-s)
        shift
        absorb_latest "$*"
        ;;
    --watch|-w)
        watch_mode "${2:-60}"
        ;;
    --help|-h)
        echo "Usage: meet-absorb.sh [--list|--all|--search QUERY|--watch [INTERVAL]|--help]"
        echo ""
        echo "  (default)    Absorb the latest Gemini transcript"
        echo "  --list       List available transcripts on Drive"
        echo "  --all        Absorb all unprocessed transcripts"
        echo "  --search Q   Search for transcripts matching Q"
        echo "  --watch [N]  Poll every N seconds (default: 60)"
        echo "  --help       Show this help"
        ;;
    *)
        absorb_latest
        ;;
esac
