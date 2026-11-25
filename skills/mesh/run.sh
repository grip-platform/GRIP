#!/usr/bin/env bash
# =============================================================================
# Self-verification for skills/mesh/SKILL.md
#
# This script used to live INSIDE SKILL.md, which was a bash/markdown polyglot:
# a shebang on line 1 with the whole document wrapped in a `: <<'MARKDOWN'`
# heredoc. That cost the skill its identity. Claude Code's loader needs `---`
# on line 1 to read the frontmatter, so with a shebang sitting there it fell
# back to the directory name and advertised this skill's description to the
# model as the literal string "#!/usr/bin/env bash".
#
# SKILL.md is now plain markdown the loader can read; the script lives here.
# Run: bash skills/mesh/run.sh
# =============================================================================

set -euo pipefail

MESH_DIR="${HOME}/.grip-session-mesh"
# Resolve the document under verification: the SKILL.md next to this script.
SKILL_FILE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/SKILL.md"
PASS=0; FAIL=0; CHECKS=""

check() {
    local name="$1" result="$2"
    if [ "$result" = "ok" ]; then
        PASS=$((PASS + 1)); CHECKS="${CHECKS}  [PASS] ${name}\n"
    else
        FAIL=$((FAIL + 1)); CHECKS="${CHECKS}  [FAIL] ${name}: ${result}\n"
    fi
}

SELF_HASH=$(shasum -a 256 "$SKILL_FILE" | cut -d' ' -f1)
[ ${#SELF_HASH} -eq 64 ] && check "self-hash" "ok" || check "self-hash" "could not compute"
# INVERTED 2026-08-18: was `head -1 == shebang`, which required the defect.
[ "$(head -1 "$SKILL_FILE")" = "---" ] && check "loader-readable (--- on line 1)" "ok" || check "loader-readable (--- on line 1)" "line 1 is not ---"
DELIMS=$(grep -c '^---$' "$SKILL_FILE" 2>/dev/null) || DELIMS=0
[ "$DELIMS" -ge 2 ] && check "YAML frontmatter" "ok" || check "YAML frontmatter" "missing"
{ [ -d "$MESH_DIR" ] || mkdir -p "$MESH_DIR" 2>/dev/null; } && check "mesh data dir" "ok" || check "mesh data dir" "cannot create"
curl -sf http://127.0.0.1:9474/health > /dev/null 2>&1 && check "bus :9474" "ok" || check "bus :9474" "not running (npm start --prefix server)"

echo "====================================================="
echo "  /mesh — Session Mesh Self-Verification"
echo "  SHA-256: ${SELF_HASH}"
printf "$CHECKS"
echo "  Result: ${PASS} passed, ${FAIL} failed"
echo "====================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
