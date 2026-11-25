#!/usr/bin/env bash
# =============================================================================
# Self-verification for skills/closeout/SKILL.md
#
# This script used to live INSIDE SKILL.md, which was a bash/markdown polyglot:
# a shebang on line 1 with the whole document wrapped in a `: <<'MARKDOWN'`
# heredoc. That cost the skill its identity. Claude Code's loader needs `---`
# on line 1 to read the frontmatter, so with a shebang sitting there it fell
# back to the directory name and advertised this skill's description to the
# model as the literal string "#!/usr/bin/env bash".
#
# SKILL.md is now plain markdown the loader can read; the script lives here.
# Run: bash skills/closeout/run.sh
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
_hash_file() {
    # Try multiple hash commands (cross-platform)
    # Note: sha256sum on Windows Git Bash prefixes with \ for paths with backslashes
    local h=""
    h=$(shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1)
    [ -z "$h" ] && h=$(sha256sum "$1" 2>/dev/null | cut -d' ' -f1)
    [ -z "$h" ] && h=$(cmd.exe /c "certutil -hashfile \"$(cygpath -w "$1" 2>/dev/null || echo "$1")\" SHA256" 2>/dev/null \
        | sed -n '2p' | tr -d ' ')
    # Strip leading backslash (sha256sum quirk on Windows paths)
    h="${h#\\}"
    echo "$h"
}
SELF_HASH=$(_hash_file "$SKILL_FILE")
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

# --- Check 4: Integrated dependencies exist ---
DEPS="closeout.py grip_save.py"
MISSING=""
for dep in $DEPS; do
    if [ ! -f "${CLAUDE_DIR}/lib/${dep}" ]; then
        MISSING="${MISSING} ${dep}"
    fi
done
# Check rsi/lock.py separately (nested path)
if [ ! -f "${CLAUDE_DIR}/lib/rsi/lock.py" ]; then
    MISSING="${MISSING} rsi/lock.py"
fi
if [ -z "$MISSING" ]; then
    check "integrated dependencies (3/3)" "ok"
else
    check "integrated dependencies" "missing:${MISSING}"
fi

# --- Check 5: All 8 closeout steps documented ---
STEP_COUNT=$(grep -c '^### Step [0-9]' "$SKILL_FILE" 2>/dev/null) || STEP_COUNT=0
if [ "$STEP_COUNT" -ge 8 ]; then
    check "closeout steps documented (8)" "ok"
else
    check "closeout steps documented" "expected >= 8, found ${STEP_COUNT}"
fi

# --- Check 6: Protected branch safety gate documented ---
if grep -q 'protected.*branch.*HALT\|HALT.*AskUserQuestion' "$SKILL_FILE" 2>/dev/null; then
    check "protected branch safety gate" "ok"
else
    check "protected branch safety gate" "not documented"
fi

# --- Check 7: No AI attribution in file ---
# Split strings so this grep line does not match itself
_ATTR1="Generated with Clau""de"
_ATTR2="Co-Authored-By: Clau""de"
if grep -qi "${_ATTR1}\|${_ATTR2}" "$SKILL_FILE" 2>/dev/null; then
    check "no AI attribution" "found AI attribution in file"
else
    check "no AI attribution" "ok"
fi

# --- Check 8: Hash consistency (compute again, must match) ---
SELF_HASH_2=$(_hash_file "$SKILL_FILE")
if [ "$SELF_HASH" = "$SELF_HASH_2" ]; then
    check "hash consistency (idempotent)" "ok"
else
    check "hash consistency" "hash changed between reads"
fi

# --- Output report ---
echo "================================================"
echo "  CLOSEOUT SKILL.MD — Self-Verification Report"
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
