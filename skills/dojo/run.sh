#!/usr/bin/env bash
# =============================================================================
# Self-verification for skills/dojo/SKILL.md
#
# This script used to live INSIDE SKILL.md, which was a bash/markdown polyglot:
# a shebang on line 1 with the whole document wrapped in a `: <<'MARKDOWN'`
# heredoc. That cost the skill its identity. Claude Code's loader needs `---`
# on line 1 to read the frontmatter, so with a shebang sitting there it fell
# back to the directory name and advertised this skill's description to the
# model as the literal string "#!/usr/bin/env bash".
#
# SKILL.md is now plain markdown the loader can read; the script lives here.
# Run: bash skills/dojo/run.sh
# =============================================================================

# ---------------------------------------------------------------------------
# Executable helper for the /dojo skill (SKILL.md alongside is pure prose).
# Mints the graduation sigil and logs a completion line through the canonical,
# already-built primitives. No new state, no new lib.
# Sourced/run by the skill runtime; safe to run standalone.
# ---------------------------------------------------------------------------
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
COMPLETIONS_LOG="${CLAUDE_DIR}/logs/dojo-completions.jsonl"

# dojo_log_completion <lesson> — append ONE anonymous finish-rate line.
# This is a by-design append-only LOG (rules/lifecycle-closed-or-expired.md),
# a finish-rate SAMPLE — never read as a "current work" queue.
dojo_log_completion() {
  local lesson="${1:?lesson id required}"
  mkdir -p "$(dirname "$COMPLETIONS_LOG")"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"lesson":"%s","ts":"%s"}\n' "$lesson" "$ts" >> "$COMPLETIONS_LOG"
}

# dojo_mint_graduation_sigil — mint the earned sigil via the canonical sigil
# primitive. Earned-only is enforced by the sigil lib's verify= contract; the
# dojo passes a verifier that the graduation gate (all 3 lessons observed) owns.
dojo_mint_graduation_sigil() {
  local gen="${GRIP_GEN:-0}"
  python3 "${CLAUDE_DIR}/lib/sigil.py" mint \
    --kind sprint --ref "dojo-graduation" --gen "$gen" 2>/dev/null \
    || echo "[dojo] sigil mint skipped (sigil.py unavailable)"
}

# Dispatch when run directly (the skill runtime calls these functions by name).
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
  case "${1:-}" in
    log-completion) dojo_log_completion "${2:?lesson id required}" ;;
    mint-graduation) dojo_mint_graduation_sigil ;;
    *) echo "usage: run.sh {log-completion <lesson>|mint-graduation}" >&2; exit 2 ;;
  esac
fi
