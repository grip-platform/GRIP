#!/usr/bin/env bash
# scripts/claude-audit.sh — GRIP-aware Claude Code infrastructure audit.
#
# Reports the actual loaded footprint of a GRIP-equipped Claude Code install:
# CLAUDE.md size, hook chains, plugins, skills count, MCP servers, and
# 7-day cache-aware token usage from session JSONLs.
#
# v1 — fixes three structural bugs from v0:
# 1. v0 read MCPs from settings.json. GRIP puts MCPs in ~/.claude/.mcp.json
#    per rules/mcp-org-isolation.md (PARAMOUNT). v1 reads the right file.
# 2. v0 used `grep -P` (Perl regex). macOS BSD grep does not support it,
#    so the token section died with "grep: invalid option -- P" + awk
#    division-by-zero. v1 uses Python for parsing.
# 3. v0 reported "Avg input: 19 tokens" by summing input_tokens alone.
#    Claude Code caches aggressively (cache_creation + cache_read fields),
#    so input_tokens is just the uncached *delta* (often 5-50). True
#    context loaded per call is input + cache_creation + cache_read,
#    typically 50-200k. v1 reports all four numbers and a TRUE-input total.
#
# Origin: /audit deliberation 2026-05-03 (V>>'s audit-script review).
#
# Usage:
#   bash ~/.claude/scripts/claude-audit.sh                  # human-readable
#   bash ~/.claude/scripts/claude-audit.sh --json           # machine-readable
#   CLAUDE_DIR=/path bash ~/.claude/scripts/claude-audit.sh # override target

set -uo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"
JSON_MODE=0
[ "${1:-}" = "--json" ] && JSON_MODE=1

# ── Section: CLAUDE.md size ─────────────────────────────────────────────

print_md_size() {
  echo "=== CLAUDE.md size ==="
  if [ -f "$CLAUDE_DIR/CLAUDE.md" ]; then
    printf "user-level:    %s words\n" "$(wc -w < "$CLAUDE_DIR/CLAUDE.md" | tr -d ' ')"
  fi
  if [ -f "$PROJECT_DIR/.claude/CLAUDE.md" ] \
     && [ "$PROJECT_DIR/.claude/CLAUDE.md" != "$CLAUDE_DIR/.claude/CLAUDE.md" ]; then
    printf "project-level: %s words\n" "$(wc -w < "$PROJECT_DIR/.claude/CLAUDE.md" | tr -d ' ')"
  fi
  if [ -f "$CLAUDE_DIR/.claude/CLAUDE.md" ]; then
    printf "checked-in:    %s words\n" "$(wc -w < "$CLAUDE_DIR/.claude/CLAUDE.md" | tr -d ' ')"
  fi
  echo "Note: word-count alone misses @-included files (rules/, learned-insights, etc)"
}

# ── Section: hooks + UserPromptSubmit summary ───────────────────────────

print_hooks() {
  echo "=== Active hook events ==="
  jq -r '.hooks // {} | keys | .[]' "$CLAUDE_DIR/settings.json" 2>/dev/null | tr '\n' ' '
  echo
  echo
  echo "=== UserPromptSubmit hooks (the per-prompt cost) ==="
  jq -r '
    [.hooks.UserPromptSubmit[]?.hooks[]?] as $h
    | "count: \($h | length)"
    + "  total_timeout_ms: \([$h[].timeout // 0] | add)"
    + "  async: \([$h[] | select(.async == true)] | length)"
    + "  blocking: \([$h[] | select(.async != true)] | length)"
  ' "$CLAUDE_DIR/settings.json" 2>/dev/null
}

# ── Section: plugins (real source) ──────────────────────────────────────

print_plugins() {
  echo "=== Installed plugins (~/.claude/plugins/installed_plugins.json) ==="
  jq -r '.plugins | keys[]' "$CLAUDE_DIR/plugins/installed_plugins.json" 2>/dev/null \
    || echo "(no plugins manifest found)"
}

# ── Section: skills (count, with note) ──────────────────────────────────

print_skills() {
  echo "=== Skills installed ==="
  local count
  count=$(ls "$CLAUDE_DIR/skills/" 2>/dev/null | wc -l | tr -d ' ')
  echo "$count skills installed (loaded on-demand, not all active per session)"
  echo "Note: GRIP design — breadth, not always-on. Skills route via prompt-routing."
}

# ── Section: MCPs (real source) ─────────────────────────────────────────

print_mcps() {
  echo "=== Connected MCPs (~/.claude/.mcp.json) ==="
  jq -r '.mcpServers | keys[]' "$CLAUDE_DIR/.mcp.json" 2>/dev/null | tr '\n' ' '
  echo
}

# ── Section: token usage (cache-aware) ──────────────────────────────────

print_tokens() {
  echo "=== Token usage last 7 days (cache-aware — the headline metric) ==="
  python3 - "$CLAUDE_DIR" "$JSON_MODE" <<'PYEOF'
import json
import os
import sys
import time
from pathlib import Path

claude_dir = Path(sys.argv[1])
json_mode = sys.argv[2] == "1"
projects = claude_dir / "projects"
cutoff = time.time() - 7 * 86400

stats = {
    "messages": 0,
    "input_tokens": 0,
    "cache_creation": 0,
    "cache_read": 0,
    "output_tokens": 0,
}


def is_real_session(path: Path) -> bool:
    s = str(path)
    return "pytest" not in s and "/test" not in s


def accumulate(usage: dict) -> None:
    stats["messages"] += 1
    stats["input_tokens"] += usage.get("input_tokens", 0) or 0
    stats["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
    stats["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
    stats["output_tokens"] += usage.get("output_tokens", 0) or 0


def scan_jsonl(path: Path) -> None:
    try:
        for line in path.read_text(errors="replace").splitlines():
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (entry.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                accumulate(usage)
    except OSError:
        return


for jsonl in projects.rglob("*.jsonl"):
    try:
        if jsonl.stat().st_mtime < cutoff:
            continue
    except OSError:
        continue
    if not is_real_session(jsonl):
        continue
    scan_jsonl(jsonl)


m = stats["messages"]
if m == 0:
    if json_mode:
        print(json.dumps({"messages": 0, "note": "no usage entries"}))
    else:
        print("no usage entries found")
    sys.exit(0)

true_input = stats["input_tokens"] + stats["cache_creation"] + stats["cache_read"]

if json_mode:
    out = {
        **stats,
        "true_input_tokens": true_input,
        "averages": {
            "input_delta_per_msg": stats["input_tokens"] // m,
            "cache_creation_per_msg": stats["cache_creation"] // m,
            "cache_read_per_msg": stats["cache_read"] // m,
            "true_input_per_msg": true_input // m,
            "output_per_msg": stats["output_tokens"] // m,
        },
    }
    print(json.dumps(out))
else:
    print(f"messages:                {m:>15,}")
    print(
        f"input_tokens delta:      {stats['input_tokens']:>15,}  "
        f"(avg {stats['input_tokens']//m:>10,} — uncached new content only)"
    )
    print(
        f"cache_creation:          {stats['cache_creation']:>15,}  "
        f"(avg {stats['cache_creation']//m:>10,} — newly cached this turn)"
    )
    print(
        f"cache_read:              {stats['cache_read']:>15,}  "
        f"(avg {stats['cache_read']//m:>10,} — pulled from cache)"
    )
    print(
        f"TRUE input per call:     {true_input:>15,}  "
        f"(avg {true_input//m:>10,} — sum of above; the real loaded context)"
    )
    print(
        f"output_tokens:           {stats['output_tokens']:>15,}  "
        f"(avg {stats['output_tokens']//m:>10,})"
    )
PYEOF
}

# ── Section: GRIP-specific footprint ────────────────────────────────────

print_grip_footprint() {
  echo "=== GRIP-specific footprint ==="
  if [ -d "$CLAUDE_DIR/rules" ]; then
    local rules_words
    rules_words=$(find "$CLAUDE_DIR/rules" -name '*.md' -type f 2>/dev/null \
      | xargs wc -w 2>/dev/null | tail -1 | awk '{print $1}')
    printf "rules/ directory:        %s words across %s files\n" \
      "${rules_words:-0}" \
      "$(find "$CLAUDE_DIR/rules" -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' ')"
  fi
  if [ -d "$CLAUDE_DIR/agents" ]; then
    printf "agents/ directory:       %s agent files\n" \
      "$(ls "$CLAUDE_DIR/agents/" 2>/dev/null | wc -l | tr -d ' ')"
  fi
  if [ -d "$CLAUDE_DIR/hooks" ]; then
    printf "hooks/ directory:        %s hook files\n" \
      "$(ls "$CLAUDE_DIR/hooks/" 2>/dev/null | wc -l | tr -d ' ')"
  fi
}

# ── Main ────────────────────────────────────────────────────────────────

main() {
  if [ "$JSON_MODE" -eq 1 ]; then
    # JSON mode: emit token usage only (the section worth machine-parsing)
    print_tokens
    return 0
  fi
  print_md_size; echo
  print_hooks; echo
  print_plugins; echo
  print_skills; echo
  print_mcps; echo
  print_tokens; echo
  print_grip_footprint; echo
  echo "=== Audit complete ==="
}

main
