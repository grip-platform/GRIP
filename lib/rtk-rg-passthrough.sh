#!/usr/bin/env bash
# rtk-rg-passthrough.sh
#
# PreToolUse Bash wrapper that runs BEFORE `rtk hook claude` and short-circuits
# when the user's command starts with `rg ` (ripgrep). Without this wrapper RTK
# 0.38.0 unconditionally rewrites `rg <args>` to `rtk grep <args>`, which
# silently breaks ripgrep-only flags (--type, --no-heading, -uu, --type-add)
# AND ripgrep's default recursion (BSD grep needs -r explicitly).
#
# Origin: GitHub issue grip-platform/GRIP#2565.
#
# Mechanism: read the JSON event from stdin, peek at .tool_input.command,
# and if it starts with `rg ` or equals `rg`, emit an empty `{}` and exit 0.
# Claude Code interprets an empty hook response as "no decision, no rewrite",
# so the original `rg ...` invocation runs verbatim. For all other commands we
# replay the JSON to `rtk hook claude` on stdin and let RTK take its turn.
#
# This script is stdlib-only (bash + jq if available, else POSIX shell parsing)
# so it has zero install footprint. Cross-platform safe (macOS/Linux/Git Bash).

set -e

# Capture stdin once — it is consumed by either the passthrough OR rtk.
INPUT="$(cat)"

# Extract the command field. Prefer jq when available; otherwise fall back to a
# small awk-based parser. Both paths only need to recognise the leading token.
if command -v jq >/dev/null 2>&1; then
  CMD="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null || true)"
else
  # Minimal fallback: pull the first quoted "command" value.
  CMD="$(printf '%s' "$INPUT" | awk -F'"command":[[:space:]]*"' 'NF>1{print $2; exit}' | awk -F'"' '{print $1}')"
fi

# Strip leading whitespace so "  rg foo" still matches.
CMD_TRIMMED="${CMD#"${CMD%%[![:space:]]*}"}"

# Decide whether `rg` or `git grep` is the command in ANY pipeline segment.
# RTK 0.38.0 only inspects the leading token: it rewrites `rg`->`rtk grep`
# (breaking ripgrep-only flags) and DENIES anything containing `grep` -
# including `git grep`, a distinct index-aware tool ripgrep cannot replace.
# So a `cd /tmp/wt && rg ...` (the dominant worktree-session pattern, since
# the shell cwd resets every call) fell through and broke. We short-circuit
# (emit `{}`) for rg / git-grep in command position, even after `cd ... &&`,
# env-assignments, or a pipe. Bare `grep` is deliberately NOT matched:
# steering content-search to `rg` is bash-safety policy, not a bug - GRIP#1804
# fixes the deadlock at its root (unreliable rg passthrough) rather than
# blanket-disabling the steer. Operator-splitting is intentionally naive; a
# misparse only flips passthrough<->defer, and both directions are fail-safe.
# Origin: GitHub issue grip-platform/GRIP#1804 (extends #2565).
_rtk_passthrough_match() {
  local cmd="$1" seg normalised
  normalised="$(printf '%s' "$cmd" | awk '{gsub(/&&|\|\||;|\||&/,"\n"); print}')"
  while IFS= read -r seg; do
    seg="${seg#"${seg%%[![:space:]]*}"}"
    while [[ "$seg" =~ ^[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+(.*)$ ]]; do
      seg="${BASH_REMATCH[1]}"
      seg="${seg#"${seg%%[![:space:]]*}"}"
    done
    case "$seg" in
      "rg "*|"rg"|"git grep "*|"git grep") return 0 ;;
    esac
  done <<EOF
$normalised
EOF
  return 1
}

if _rtk_passthrough_match "$CMD_TRIMMED"; then
  # Emit empty JSON so Claude Code applies no rewrite (verbatim rg/git-grep).
  printf '{}\n'
  exit 0
fi

# Not an rg / git-grep command — defer to RTK if it is installed.
# Graceful degrade: when `rtk` is not on PATH (a fresh GRIP install before
# `brew install rtk`, or a team machine mid-bootstrap), emit empty JSON so
# Claude Code applies no rewrite and the original command runs verbatim — never
# crash every single Bash tool call with "rtk: command not found". The unified
# installer (scripts/install.sh ensure_rtk) installs rtk; this guard keeps the
# hook safe in the window before that lands. Origin: blackbook 2026-06-24.
if ! command -v rtk >/dev/null 2>&1; then
  printf '{}\n'
  exit 0
fi
printf '%s' "$INPUT" | rtk hook claude "$@"
