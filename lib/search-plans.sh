#!/usr/bin/env bash
# Search plans using semantic search
# Usage: ./search-plans.sh "query" [--limit N]

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

if [[ -z "${1:-}" ]]; then
    echo "Usage: search-plans.sh <query> [--limit N]"
    exit 1
fi

python3 "$CLAUDE_DIR/lib/plan_registry.py" search "$@"
