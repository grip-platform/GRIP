#!/usr/bin/env bash
# Cross-harness skill loader for GRIP.
# Usage: bash scripts/skill-dispatch.sh <skill-name>
# Prints the markdown body of <GRIP_DIR>/skills/<skill-name>/SKILL.md with the
# YAML frontmatter stripped, for injection into any harness context.
#
# Repo root is resolved INDEPENDENT of the caller's working directory: $GRIP_DIR
# wins if set, else the root is derived from this script's own location. This lets
# any harness (OpenCode, Aider, bash) invoke it from any CWD without first cd-ing
# into the repository. See docs/skill-invocation-protocol.md.
#
# Exit codes:
#   0  success
#   1  missing skill name (or unexpected parse error)
#   2  skill file not found
#   3  frontmatter opened but never closed
#   4  no frontmatter present

set -euo pipefail

SKILL="${1:-}"

if [[ -z "$SKILL" ]]; then
  echo "Usage: $0 <skill-name>" >&2
  exit 1
fi

# Repo root independent of CWD: $GRIP_DIR wins; else derive from script location.
# This script lives at <root>/scripts/skill-dispatch.sh, so <root> = its parent's parent.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
GRIP_DIR="${GRIP_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"

FILE="$GRIP_DIR/skills/${SKILL}/SKILL.md"

if [[ ! -f "$FILE" ]]; then
  echo "Skill not found: $FILE" >&2
  exit 2
fi

# Strip the leading YAML frontmatter (--- ... ---) and stream the body.
# State machine PRE -> IN_FM -> BODY. Detection does NOT depend on a line counter,
# so leading blank lines before the opening fence do not break it. awk exits with
# 3 (opened, never closed) or 4 (no frontmatter) so the shell can report precisely.
rc=0
awk '
  BEGIN { state = "PRE" }
  state == "BODY" { print; next }
  state == "PRE" {
    if ($0 ~ /^---[[:space:]]*$/) { state = "IN_FM"; next }
    if ($0 ~ /^[[:space:]]*$/)    { next }   # tolerate leading blank lines
    exit 4                                   # first real line is not a fence: no frontmatter
  }
  state == "IN_FM" {
    if ($0 ~ /^---[[:space:]]*$/) { state = "BODY"; next }
    next
  }
  END {
    if (state == "IN_FM") exit 3             # opened but never closed
    if (state == "PRE")   exit 4             # empty / all-blank: no frontmatter
  }
' "$FILE" || rc=$?

case "$rc" in
  0) ;;  # body already streamed to stdout
  3) echo "Frontmatter opened but never closed: $FILE" >&2; exit 3 ;;
  4) echo "No frontmatter present: $FILE" >&2; exit 4 ;;
  *) echo "Failed to parse skill file: $FILE" >&2; exit 1 ;;
esac
