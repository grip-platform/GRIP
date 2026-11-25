#!/usr/bin/env bash
#
# memory-guard.sh — Backup-on-write and integrity checks for MEMORY.md
#
# Called as a PreToolUse hook when Edit/Write targets MEMORY.md.
# Reads tool input from stdin (JSON with tool_input field).
#
# Protection:
#   1. Backup rotation (keeps last 3 backups)
#   2. Structure validation (GRIP-BRIEF markers, line bounds)
#   3. Warns on potential corruption vectors
#

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# Read hook input from stdin with cat, never $(</dev/stdin). Under Git Bash on
# Windows, /dev/stdin is a /proc link that cannot be opened when the parent is
# a native process such as Claude Code, so that form failed on every call
# ("/dev/stdin: No such file or directory", exit 1) and this hook never backed
# MEMORY.md up on Windows. hooks/project-cwd-hint.sh reads its input the same way.
INPUT=$(cat)

# Cheap pre-filter: most edits never mention MEMORY.md, so skip finding Python
# for them. The exact check on the parsed target follows below.
[[ "$INPUT" == *"MEMORY.md"* ]] || exit 0

# Find a Python that actually runs. A bare python3 is the Microsoft Store stub
# on some Windows machines and absent where only `python` exists, and in both
# cases the parse below printed nothing, so the hook silently did nothing.
# resolve_venv_python (scripts/lib/venv-utils.sh) tries GRIP's venv first and,
# when there is none, a python3 or python on PATH, and it returns only an
# interpreter that answers. PY stays empty when no Python runs at all, or when
# that library cannot be loaded.
VENV_UTILS="$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/venv-utils.sh"
PY=""
# The library is syntax-checked, then sourced inside the `if` test, on purpose.
# Under set -e a syntax error in a sourced file exits 2, Claude Code treats
# exit 2 from this hook as a blocking error, and this hook must never block an
# edit. The `if` alone is enough on bash 4+, but macOS ships /bin/bash 3.2,
# which aborts the whole script on a parse error in a sourced file even inside
# an `if` (measured 2026-10-15: tests/test_windows_compat.py's broken-library
# case exited 2 here while passing on the CI runners). `bash -n` parses the
# file in a child shell first, so a broken library costs one warning, never
# an edit.
# shellcheck source=../scripts/lib/venv-utils.sh
if [ -f "$VENV_UTILS" ] && bash -n "$VENV_UTILS" 2>/dev/null && source "$VENV_UTILS"; then
    PY=$(resolve_venv_python "$CLAUDE_DIR/venv") || PY=""
fi
if [ -z "$PY" ]; then
    echo "[MEMORY-GUARD] No working Python (or scripts/lib/venv-utils.sh failed to load), so MEMORY.md was not backed up." >&2
    exit 0
fi

# Check if this edit targets MEMORY.md
TOOL_INPUT=$(echo "$INPUT" | "$PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {})
    fp = ti.get('file_path', '') if isinstance(ti, dict) else ''
    print(fp)
except Exception:
    print('')
" 2>/dev/null || echo "")

# Only act on MEMORY.md edits
if [[ "$TOOL_INPUT" != *"MEMORY.md"* ]]; then
    exit 0
fi

# Use the exact file path from the tool input
MEMORY_PATH="$TOOL_INPUT"

# Verify it exists
[ -f "$MEMORY_PATH" ] || exit 0

BACKUP_DIR="$(dirname "$MEMORY_PATH")"

# --- 1. Backup rotation (keep last 3) ---
if [ -f "${BACKUP_DIR}/MEMORY.md.bak.2" ]; then
    mv -f "${BACKUP_DIR}/MEMORY.md.bak.2" "${BACKUP_DIR}/MEMORY.md.bak.3" 2>/dev/null || true
fi
if [ -f "${BACKUP_DIR}/MEMORY.md.bak.1" ]; then
    mv -f "${BACKUP_DIR}/MEMORY.md.bak.1" "${BACKUP_DIR}/MEMORY.md.bak.2" 2>/dev/null || true
fi
if [ -f "${BACKUP_DIR}/MEMORY.md.bak" ]; then
    mv -f "${BACKUP_DIR}/MEMORY.md.bak" "${BACKUP_DIR}/MEMORY.md.bak.1" 2>/dev/null || true
fi
cp -f "$MEMORY_PATH" "${BACKUP_DIR}/MEMORY.md.bak" 2>/dev/null || true

# --- 2. Structure validation ---
WARNINGS=""

# Check GRIP-BRIEF markers
if ! grep -q "GRIP-BRIEF-START" "$MEMORY_PATH" 2>/dev/null; then
    WARNINGS="${WARNINGS}Missing GRIP-BRIEF-START marker. "
fi
if ! grep -q "GRIP-BRIEF-END" "$MEMORY_PATH" 2>/dev/null; then
    WARNINGS="${WARNINGS}Missing GRIP-BRIEF-END marker. "
fi

# Check CHARACTER budget — Claude Code truncates MEMORY.md on session load at
# ~24.4 KiB of CHARACTERS (24.4*1024 = 24986; observed CC 2.x, 2026-06-05: a
# 46,304-char file reported as "45.2KB"). Long multi-line entries blow this
# CHARACTER budget while staying under any line count, so chars — not lines —
# is the dimension that causes silent index truncation. Keep this number in
# sync with _HARNESS_INDEX_CHAR_BUDGET in lib/grip_doctor.py.
HARNESS_CHAR_BUDGET=24986
# Approaching band derived from the budget (85%) — NOT a separate literal, so a
# future budget change can never desync the two thresholds (AGORA #3426 r1).
HARNESS_CHAR_APPROACH=$(( HARNESS_CHAR_BUDGET * 85 / 100 ))
CHAR_COUNT=$(wc -m < "$MEMORY_PATH" 2>/dev/null | tr -d ' ')
if [ "${CHAR_COUNT:-0}" -gt "$HARNESS_CHAR_BUDGET" ]; then
    WARNINGS="${WARNINGS}MEMORY.md at ${CHAR_COUNT} chars OVER the ${HARNESS_CHAR_BUDGET}-char load budget — it is being SILENTLY TRUNCATED on session load; shorten entries to one line (< ~200 chars) and move detail into linked topic files. "
elif [ "${CHAR_COUNT:-0}" -gt "$HARNESS_CHAR_APPROACH" ]; then
    WARNINGS="${WARNINGS}MEMORY.md at ${CHAR_COUNT} chars (approaching ${HARNESS_CHAR_BUDGET}-char load budget). "
fi

# --- 3. Output ---
if [ -n "$WARNINGS" ]; then
    echo "[MEMORY-GUARD] Backup created. Warnings: ${WARNINGS}" >&2
fi

# Always allow the edit (this is a safety net, not a gate)
exit 0
