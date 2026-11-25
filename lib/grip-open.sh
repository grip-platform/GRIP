#!/usr/bin/env bash
#
# grip-open.sh — Open files with the right tool based on extension.
#
# Usage:
#   bash lib/grip-open.sh /path/to/file.md
#   bash lib/grip-open.sh /path/to/data.json
#

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: grip-open <file>" >&2
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "File not found: $FILE" >&2
    exit 1
fi

case "${FILE##*.}" in
    md|txt|py|sh|js|ts|tsx|jsx|yaml|yml|toml|cfg|ini|conf)
        ${EDITOR:-open} "$FILE" ;;
    json)
        python3 -m json.tool "$FILE" 2>/dev/null | "${PAGER:-less}" || ${EDITOR:-open} "$FILE" ;;
    pdf)
        open "$FILE" ;;
    png|jpg|jpeg|gif|svg|webp)
        open "$FILE" ;;
    *)
        open "$FILE" ;;
esac
