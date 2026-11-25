#!/usr/bin/env bash
# verify-clone.sh — validate GRIP clone is correctly configured
#
# Run after cloning and setting up git-crypt:
#   bash scripts/verify-clone.sh
#
# Exit codes: 0 = all good, 1 = issues found

set -euo pipefail

PASS=0
WARN=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  [PASS] $1"; }
warn() { WARN=$((WARN + 1)); echo "  [WARN] $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; }

echo "GRIP Clone Verification"
echo "======================="
echo ""

# --- 1. git-crypt status ---
echo "1. git-crypt"
if command -v git-crypt &>/dev/null; then
    pass "git-crypt installed"
else
    fail "git-crypt not installed (brew install git-crypt)"
fi

# Check if unlocked by testing whether a known encrypted file has no GITCRYPT header
PYTHON_CMD="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
_vc_unlocked=false
for _sentinel in "facts/identity.md" "facts/team.md"; do
    if [ -f "$_sentinel" ] && [ -n "$PYTHON_CMD" ]; then
        _is_enc=$("$PYTHON_CMD" -c "
import sys
try:
    with open('${_sentinel}', 'rb') as f:
        print('yes' if f.read(9) == b'\x00GITCRYPT' else 'no')
except Exception:
    print('unknown')
" 2>/dev/null)
        if [ "$_is_enc" = "no" ]; then
            _vc_unlocked=true
        fi
        break
    fi
done
if [ "$_vc_unlocked" = true ]; then
    pass "Repository unlocked (encrypted files readable)"
else
    fail "Repository is locked (run: git-crypt unlock /path/to/key)"
fi
echo ""

# --- 2. skip-worktree flags ---
echo "2. skip-worktree flags"
# Read the same declared list setup-local-git.sh writes from, so the two cannot
# drift. It used to be three names hardcoded here, which passed while five files
# of the same class -- outside learning/ and so outside the setter's old glob --
# kept no flag at all and left the tree permanently dirty (GRIP#6461).
EXPECTED_SKIP=()
SKIP_LIST="config/skip-worktree-paths.txt"
if [ -f "$SKIP_LIST" ]; then
    while IFS= read -r _pattern; do
        _pattern="${_pattern%%#*}"
        _pattern="$(printf '%s' "$_pattern" | tr -d '[:space:]')"
        [ -z "$_pattern" ] && continue
        for _f in $_pattern; do
            [ -f "$_f" ] && EXPECTED_SKIP+=("$_f")
        done
    done < "$SKIP_LIST"
else
    fail "$SKIP_LIST is missing -- cannot check skip-worktree coverage"
fi
for f in "${EXPECTED_SKIP[@]}"; do
    if git ls-files -v "$f" 2>/dev/null | grep -q "^S"; then
        pass "skip-worktree set: $f"
    elif git ls-files --error-unmatch "$f" &>/dev/null; then
        warn "skip-worktree NOT set: $f (run: bash scripts/setup-local-git.sh)"
    else
        warn "not tracked: $f (may not exist yet)"
    fi
done
echo ""

# --- 3. gitignore sanity ---
echo "3. .gitignore coverage"
# Files that MUST be ignored (only tests untracked files — tracked files bypass .gitignore)
MUST_IGNORE=(
    ".env"
    "facts/identity.md"
    "settings.json"
    "instances/"
    "venv/"
)
for f in "${MUST_IGNORE[@]}"; do
    if git check-ignore -q "$f" 2>/dev/null; then
        pass "ignored: $f"
    else
        fail "NOT ignored (should be): $f"
    fi
done

# Files that must NOT be ignored
MUST_TRACK=(
    ".gitignore"
    ".gitattributes"
    "CLAUDE.md"
    "skills/system-diagramming/SKILL.md"
    "scripts/verify-clone.sh"
    "scripts/setup-local-git.sh"
)
for f in "${MUST_TRACK[@]}"; do
    if git check-ignore -q "$f" 2>/dev/null; then
        fail "ignored (should be tracked): $f"
    else
        pass "tracked: $f"
    fi
done
echo ""

# --- 4. Gitignore negation patterns (shared docs must be trackable) ---
echo "4. Shared doc negations"
MUST_NEGATE=(
    "docs/restricted/"
    "docs/diagrams/"
    "docs/FILE-TRACKING.md"
)
for f in "${MUST_NEGATE[@]}"; do
    if git check-ignore -q "$f" 2>/dev/null; then
        fail "Negation broken — $f is ignored (should be trackable)"
    else
        pass "negated: $f"
    fi
done
echo ""

# --- 5. No secrets staged ---
echo "5. Secret leak check"
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
SECRET_PATTERNS=(".env" ".credentials" "secret" "token" "apikey")
LEAKED=false
for pattern in "${SECRET_PATTERNS[@]}"; do
    if echo "$STAGED" | grep -iq "$pattern"; then
        fail "Potential secret staged: $(echo "$STAGED" | grep -i "$pattern")"
        LEAKED=true
    fi
done
if [ "$LEAKED" = false ]; then
    pass "No secrets in staging area"
fi
echo ""

# --- Summary ---
echo "======================="
# --- 6. Gitattributes encryption patterns ---
echo "6. Encryption patterns (.gitattributes)"
if [ -f ".gitattributes" ]; then
    EXPECTED_ENCRYPTED=("docs/restricted" "facts/*.md" "data/genomes/*.json")
    for pattern in "${EXPECTED_ENCRYPTED[@]}"; do
        if grep -qF "$pattern" .gitattributes 2>/dev/null; then
            pass "encrypted pattern: $pattern"
        else
            fail "Missing encryption pattern: $pattern"
        fi
    done
else
    fail ".gitattributes missing"
fi
echo ""

# --- Summary ---
echo "======================="
echo "Results: $PASS passed, $WARN warnings, $FAIL failures"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Fix failures before contributing. Run 'bash scripts/setup-local-git.sh' for skip-worktree."
    exit 1
elif [ "$WARN" -gt 0 ]; then
    echo ""
    echo "Warnings are non-blocking but should be addressed."
    exit 0
else
    echo ""
    echo "Clone is correctly configured."
    exit 0
fi
