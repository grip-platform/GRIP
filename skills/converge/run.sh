#!/usr/bin/env bash
# =============================================================================
# Self-verification for skills/converge/SKILL.md
#
# This script used to live INSIDE SKILL.md, which was a bash/markdown polyglot:
# a shebang on line 1 with the whole document wrapped in a `: <<'MARKDOWN'`
# heredoc. That cost the skill its identity. Claude Code's loader needs `---`
# on line 1 to read the frontmatter, so with a shebang sitting there it fell
# back to the directory name and advertised this skill's description to the
# model as the literal string "#!/usr/bin/env bash".
#
# SKILL.md is now plain markdown the loader can read; the script lives here.
# Run: bash skills/converge/run.sh
# =============================================================================

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
# Resolve the document under verification: the SKILL.md next to this script.
SKILL_FILE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/SKILL.md"
PASS=0
FAIL=0
CHECKS=""

check() {
    local name="$1"
    local result="$2"
    if [ "$result" = "ok" ]; then
        PASS=$((PASS + 1))
        CHECKS="${CHECKS}  [PASS] ${name}\n"
    else
        FAIL=$((FAIL + 1))
        CHECKS="${CHECKS}  [FAIL] ${name}: ${result}\n"
    fi
}

# --- Check 1: Self-hash computation ---
SELF_HASH=$(shasum -a 256 "$SKILL_FILE" | cut -d' ' -f1)
if [ -n "$SELF_HASH" ] && [ ${#SELF_HASH} -eq 64 ]; then
    check "self-hash (sha256)" "ok"
else
    check "self-hash (sha256)" "could not compute hash"
fi

# --- Check 2: Loader-readable markdown (frontmatter opens line 1) ---
# INVERTED 2026-08-18. This check used to REQUIRE a shebang on line 1 — the very
# defect it now guards against. Claude Code's loader reads frontmatter only when
# `---` is the first line; with a shebang there the skill advertised itself to
# the model as the literal "#!/usr/bin/env bash". It fails loudly if the
# bash/markdown polyglot form ever comes back.
FIRST_LINE=$(head -1 "$SKILL_FILE")
HAS_MARKDOWN=$(grep -c '^# ' "$SKILL_FILE" 2>/dev/null) || HAS_MARKDOWN=0
if [ "$FIRST_LINE" = "---" ] && [ "$HAS_MARKDOWN" -gt 0 ]; then
    check "loader-readable markdown (--- on line 1)" "ok"
else
    check "loader-readable markdown (--- on line 1)" "line 1 is '${FIRST_LINE}', '# ' headers: ${HAS_MARKDOWN}"
fi

# --- Check 3: YAML frontmatter present ---
FRONTMATTER_DELIMS=$(grep -c '^---$' "$SKILL_FILE" 2>/dev/null) || FRONTMATTER_DELIMS=0
if [ "$FRONTMATTER_DELIMS" -ge 2 ]; then
    check "YAML frontmatter" "ok"
else
    check "YAML frontmatter" "expected >= 2 '---' delimiters, found ${FRONTMATTER_DELIMS}"
fi

# --- Check 4: Integrated libraries exist ---
LIBS="converge.py converge_combinators.py converge_inherit.py converge_metrics.py converge_dna.py"
MISSING=""
for lib in $LIBS; do
    if [ ! -f "${CLAUDE_DIR}/lib/${lib}" ]; then
        MISSING="${MISSING} ${lib}"
    fi
done
if [ -z "$MISSING" ]; then
    check "integrated libraries (5/5)" "ok"
else
    check "integrated libraries" "missing:${MISSING}"
fi

# --- Check 5: Scoring rubric has exactly 6 task types ---
TASK_TYPES=$(grep -E '^\| (code|explanation|design|analysis|creative|general) \|' "$SKILL_FILE" | head -20 | wc -l | tr -d ' ')
if [ "$TASK_TYPES" -ge 6 ]; then
    check "scoring rubric task types (6)" "ok"
else
    check "scoring rubric task types" "expected >= 6, found ${TASK_TYPES}"
fi

# --- Check 6: Hash consistency (compute again, must match) ---
SELF_HASH_2=$(shasum -a 256 "$SKILL_FILE" | cut -d' ' -f1)
if [ "$SELF_HASH" = "$SELF_HASH_2" ]; then
    check "hash consistency (idempotent)" "ok"
else
    check "hash consistency" "hash changed between reads"
fi

# --- Output report ---
echo "================================================"
echo "  CONVERGE SKILL.MD — Self-Verification Report"
echo "================================================"
echo ""
echo "  File:    $(basename "$SKILL_FILE")"
echo "  SHA-256: ${SELF_HASH}"
echo "  Lines:   $(wc -l < "$SKILL_FILE" | tr -d ' ')"
echo ""
printf "$CHECKS"
echo ""
echo "  Result: ${PASS} passed, ${FAIL} failed"
echo "================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
