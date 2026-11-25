#!/usr/bin/env bash
# =============================================================================
# Self-verification for skills/mem/SKILL.md
#
# This script used to live INSIDE SKILL.md, which was a bash/markdown polyglot:
# a shebang on line 1 with the whole document wrapped in a `: <<'MARKDOWN'`
# heredoc. That cost the skill its identity. Claude Code's loader needs `---`
# on line 1 to read the frontmatter, so with a shebang sitting there it fell
# back to the directory name and advertised this skill's description to the
# model as the literal string "#!/usr/bin/env bash".
#
# SKILL.md is now plain markdown the loader can read; the script lives here.
# Run: bash skills/mem/run.sh
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

SELF_HASH=$(shasum -a 256 "$SKILL_FILE" | cut -d' ' -f1)
[ -n "$SELF_HASH" ] && check "self-hash" "ok" || check "self-hash" "failed"

[ -f "${CLAUDE_DIR}/lib/hal_mem.py" ] && check "hal_mem.py shim" "ok" || check "hal_mem.py shim" "missing"
[ -f "${CLAUDE_DIR}/tests/test_hal_mem.py" ] && check "tests/test_hal_mem.py" "ok" || check "tests" "missing"

if python3 -c "from lib.hal_mem import probe, is_fixed_point; s=probe(); print(s.pressure_level)" 2>/dev/null | grep -qE "green|yellow|red"; then
    check "probe() returns valid state" "ok"
else
    check "probe() returns valid state" "failed"
fi

echo "====================================================="
echo "  MEM SKILL.MD — Self-Verification Report"
echo "====================================================="
printf "$CHECKS"
echo "  Result: ${PASS} passed, ${FAIL} failed"
echo "====================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
