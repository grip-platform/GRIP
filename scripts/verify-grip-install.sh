#!/usr/bin/env bash
# GRIP Installation Verification Script
# Run during 1-on-1 dev sessions to verify GRIP setup health.
# Cross-platform: macOS (zsh), Linux (bash), Windows (Git Bash).

set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
PASS=0
FAIL=0
INFO=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
info() { echo "[INFO] $1"; INFO=$((INFO + 1)); }

echo "GRIP Installation Verification"
echo "==============================="
echo ""

# 1. settings.json exists
if [ -f "${CLAUDE_DIR}/settings.json" ]; then
    pass "settings.json exists"
else
    fail "settings.json missing"
fi

# 2. PreToolUse hooks configured
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [ -n "$PYTHON_CMD" ] && [ -f "${CLAUDE_DIR}/settings.json" ]; then
    HOOK_COUNT=$($PYTHON_CMD -c "
import json, sys
try:
    d = json.load(open('${CLAUDE_DIR}/settings.json'))
    hooks = d.get('hooks', {}).get('PreToolUse', [])
    print(len(hooks))
except Exception:
    print(0)
" 2>/dev/null)
    if [ "${HOOK_COUNT:-0}" -ge 5 ]; then
        pass "PreToolUse hooks: ${HOOK_COUNT} configured"
    else
        fail "PreToolUse hooks: only ${HOOK_COUNT:-0} configured (need >= 5)"
    fi
else
    fail "PreToolUse hooks: cannot check (no Python or settings.json)"
fi

# 3. Hook files exist
HOOK_FILES=(
    "hooks/context-gate-hook.py"
    "hooks/skill-usage-tracker.py"
    "lib/memory-guard.sh"
    "hooks/cc-gate-hook.py"
    "hooks/quality-pretool-hook.py"
    "hooks/grip-first-retrieval-hook.py"
)
HOOKS_FOUND=0
HOOKS_TOTAL=${#HOOK_FILES[@]}
for hf in "${HOOK_FILES[@]}"; do
    if [ -f "${CLAUDE_DIR}/${hf}" ]; then
        HOOKS_FOUND=$((HOOKS_FOUND + 1))
    fi
done
if [ "$HOOKS_FOUND" -eq "$HOOKS_TOTAL" ]; then
    pass "Hook files: ${HOOKS_FOUND}/${HOOKS_TOTAL} present"
else
    fail "Hook files: ${HOOKS_FOUND}/${HOOKS_TOTAL} present"
fi

# 4. quality-pretool-hook.py functional (check quality_fast import)
if [ -n "$PYTHON_CMD" ]; then
    IMPORT_RESULT=$($PYTHON_CMD -c "
import sys, os
sys.path.insert(0, os.path.join('${CLAUDE_DIR}', 'lib'))
from quality_fast import check_all
print('ok')
" 2>&1)
    if [ "$IMPORT_RESULT" = "ok" ]; then
        pass "quality_fast import: OK"
    else
        fail "quality_fast import: ${IMPORT_RESULT}"
    fi
else
    fail "quality_fast import: no Python available"
fi

# 5. git-crypt status (informational)
if command -v git-crypt >/dev/null 2>&1; then
    GC_COUNT=$(cd "${CLAUDE_DIR}" && git-crypt status -e 2>/dev/null | wc -l | tr -d ' ') || GC_COUNT=0
    if [ "${GC_COUNT:-0}" -gt 0 ]; then
        info "git-crypt: ${GC_COUNT} encrypted files (check keyring status manually)"
    else
        info "git-crypt: no encrypted files detected"
    fi
else
    info "git-crypt: not installed (optional)"
fi

# 6. /learn pipeline (learning_detector scan)
if [ -n "$PYTHON_CMD" ]; then
    LEARN_EXIT=0
    if [ -f "${CLAUDE_DIR}/venv/bin/activate" ]; then
        (source "${CLAUDE_DIR}/venv/bin/activate" && $PYTHON_CMD "${CLAUDE_DIR}/lib/learning_detector.py" scan >/dev/null 2>&1) || LEARN_EXIT=$?
    else
        $PYTHON_CMD "${CLAUDE_DIR}/lib/learning_detector.py" scan >/dev/null 2>&1 || LEARN_EXIT=$?
    fi
    if [ "$LEARN_EXIT" -eq 0 ]; then
        pass "/learn pipeline: exit 0"
    else
        fail "/learn pipeline: exit code ${LEARN_EXIT}"
    fi
else
    fail "/learn pipeline: no Python available"
fi

# 7. Fork sync health (compare local HEAD with upstream main)
if command -v gh >/dev/null 2>&1; then
    LOCAL_SHA=$(cd "${CLAUDE_DIR}" && git rev-parse HEAD 2>/dev/null | head -c 7)
    UPSTREAM_SHA=$(gh api repos/grip-platform/GRIP/commits/main --jq '.sha' 2>/dev/null | head -c 7)
    if [ -n "$LOCAL_SHA" ] && [ -n "$UPSTREAM_SHA" ]; then
        if [ "$LOCAL_SHA" = "$UPSTREAM_SHA" ]; then
            pass "Fork sync: HEAD matches upstream (${LOCAL_SHA})"
        else
            info "Fork sync: local ${LOCAL_SHA} vs upstream ${UPSTREAM_SHA} (may need sync)"
        fi
    else
        info "Fork sync: could not compare (gh CLI or network issue)"
    fi
else
    info "Fork sync: gh CLI not installed"
fi

# 8. Claude Code version
if command -v claude >/dev/null 2>&1; then
    CC_VERSION=$(claude --version 2>/dev/null | head -1)
    pass "Claude Code: ${CC_VERSION}"
else
    fail "Claude Code: not found in PATH"
fi

# 9. Python version
if [ -n "$PYTHON_CMD" ]; then
    PY_VERSION=$($PYTHON_CMD --version 2>&1 | head -1)
    pass "Python: ${PY_VERSION}"
else
    fail "Python: not found (need >= 3.9)"
fi

# 10. No settings.local.json (Claude Code reads settings.json only)
if [ ! -f "${CLAUDE_DIR}/settings.local.json" ]; then
    pass "No settings.local.json (correct)"
else
    fail "settings.local.json exists (Claude Code ignores this file)"
fi

echo ""
TOTAL=$((PASS + FAIL))
echo "Score: ${PASS}/${TOTAL} checks passed"
[ "$INFO" -gt 0 ] && echo "       ${INFO} informational items"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
