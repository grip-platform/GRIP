#!/usr/bin/env bash
# grip-cloud-bootstrap.sh -- make a cloud clone of GRIP behave like an installed GRIP.
#
# On a GRIP machine, ~/.claude IS the GRIP repository, and every launcher,
# hook and skill path assumes that. In a Claude Code cloud session the repo is
# cloned somewhere else (e.g. ~/GRIP) and ~/.claude is the harness's
# own directory, so none of GRIP's skills, commands or agents load and the MCP
# launchers fail ("grip-apps dir not found at ~/.claude/mcp-servers/grip-apps").
#
# This script bridges the two by symlinking the clone's GRIP directories into
# ~/.claude. It is deliberately conservative:
#   * it runs only in a cloud session ($CLAUDE_CODE_REMOTE=true), or when
#     forced with GRIP_CLOUD_BOOTSTRAP_FORCE=1;
#   * it does nothing when ~/.claude already IS the repository (a GRIP machine);
#   * it never overwrites a working entry: an existing entry is left alone
#     (only a dangling link, whose target is gone, is replaced),
#     and a directory that already exists in ~/.claude gets the repo's missing
#     children linked into it one by one;
#   * it is idempotent: a second run changes nothing.
#
# Usage: bash scripts/grip-cloud-bootstrap.sh [REPO_DIR]
#   REPO_DIR defaults to $CLAUDE_PROJECT_DIR, else the repo containing this script.
# Also usable as the cloud environment's setup script.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${GRIP_CLOUD_BOOTSTRAP_FORCE:-}" != "1" ]; then
  exit 0
fi

REPO="${1:-${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
TARGET="${HOME}/.claude"

REPO_REAL="$(cd "$REPO" && pwd -P)"
mkdir -p "$TARGET"
TARGET_REAL="$(cd "$TARGET" && pwd -P)"

# A GRIP machine: ~/.claude is the repository itself. Nothing to bridge.
if [ "$REPO_REAL" = "$TARGET_REAL" ]; then
  exit 0
fi
# Refuse anything that is not a GRIP checkout, so a wrong argument links nothing.
if [ ! -d "$REPO_REAL/skills" ] || [ ! -f "$REPO_REAL/CLAUDE.md" ]; then
  echo "grip-cloud-bootstrap: $REPO_REAL is not a GRIP checkout; nothing linked" >&2
  exit 0
fi

# The directories GRIP's skills, commands, agents, hooks and MCP launchers
# resolve under ~/.claude.
DIRS=(skills commands agents rules lib hooks mcp-servers modes output-styles scripts bin config)

linked=0
link_one() {  # link_one SOURCE DEST: create DEST -> SOURCE unless DEST exists
  # A dangling link (its target is gone, e.g. a clone that moved) is the only
  # thing ever replaced: it points nowhere, so nothing working is lost.
  if [ -L "$2" ] && [ ! -e "$2" ]; then
    rm -f "$2"
  fi
  if [ ! -e "$2" ] && [ ! -L "$2" ]; then
    ln -s "$1" "$2"
    linked=$((linked + 1))
  fi
}

for d in "${DIRS[@]}"; do
  src="$REPO_REAL/$d"
  [ -d "$src" ] || continue
  dest="$TARGET/$d"
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    # Already a real directory (the harness keeps its own skills there):
    # add the repo's entries it does not have, one by one.
    for child in "$src"/* "$src"/.[!.]*; do
      [ -e "$child" ] || continue
      link_one "$child" "$dest/$(basename "$child")"
    done
  else
    link_one "$src" "$dest"
  fi
done

echo "grip-cloud-bootstrap: linked $linked entr$([ "$linked" = 1 ] && echo y || echo ies) from $REPO_REAL into $TARGET" >&2
