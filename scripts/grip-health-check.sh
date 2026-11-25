#!/usr/bin/env bash
#
# GRIP Health Check — Diagnose what's missing from your GRIP installation
#
# Usage:
#   ~/.claude/scripts/grip-health-check.sh
#   grip health-check  (if bin/ is in PATH)
#
# Checks 12 critical components and reports pass/fail with fix commands.
#
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
PASS=0
FAIL=0
WARN=0

# Colours
G='\033[0;32m'
R='\033[0;31m'
Y='\033[1;33m'
B='\033[0;34m'
D='\033[2m'
NC='\033[0m'

pass()  { echo -e "  ${G}PASS${NC}  $*"; PASS=$((PASS + 1)); }
fail()  { echo -e "  ${R}FAIL${NC}  $*"; FAIL=$((FAIL + 1)); }
warn()  { echo -e "  ${Y}WARN${NC}  $*"; WARN=$((WARN + 1)); }
info()  { echo -e "  ${B}INFO${NC}  $*"; }
fix()   { echo -e "        ${D}Fix: $*${NC}"; }

# ============================================================================
# Plan tier — resolved ONCE, up front. The effort/thinking checks below, and
# the tier-propagation check, are both tier-aware: the Pro launch default is
# medium effort, not max (operator decision, 2026-10-15), so a correctly
# configured Pro install must not be reported as broken.
#
# Plain python3, not bin/grip-py: grip_tier.py only reads env/config and
# signs nothing, so the crypto-downgrade concern bin/grip-py exists for does
# not apply here — this mirrors bin/grip's own _doctor_tier_block, which
# resolves the same module the same way (and works with no repo venv, which
# CI's checkout does not have). Fail-open to "unknown": a missing python3 or
# lib/grip_tier.py degrades to the tier-blind behaviour this script already
# had, never a script failure.
# ============================================================================
GRIP_RESOLVED_TIER="unknown"
_gt_py="$CLAUDE_DIR/lib/grip_tier.py"
if command -v python3 >/dev/null 2>&1 && [ -f "$_gt_py" ]; then
    _gt_resolved=$(python3 "$_gt_py" --resolve 2>/dev/null) || _gt_resolved=""
    case "$_gt_resolved" in
        pro|max|api) GRIP_RESOLVED_TIER="$_gt_resolved" ;;
    esac
fi
unset _gt_py _gt_resolved

# The resolved tier's two envelope numbers. Mirrors lib/grip_tier.py's
# TIER_ENVELOPE (pro: 200K assumed / 180K compact; max/api: 1M / 1M) as a
# small lookup rather than a second python call — these two numbers are the
# whole envelope the propagation check below needs. Keep in sync with
# TIER_ENVELOPE if those numbers ever change.
GRIP_TIER_ASSUMED_WINDOW=""
GRIP_TIER_COMPACT_WINDOW=""
case "$GRIP_RESOLVED_TIER" in
    pro) GRIP_TIER_ASSUMED_WINDOW=200000; GRIP_TIER_COMPACT_WINDOW=180000 ;;
    max|api) GRIP_TIER_ASSUMED_WINDOW=1000000; GRIP_TIER_COMPACT_WINDOW=1000000 ;;
esac

# Tier-propagation helper: compares a configured window value (the process
# environment, else settings.json's env block) against the resolved tier's
# expected number. Catches drift -- e.g. a Max-tier
# CLAUDE_CODE_AUTO_COMPACT_WINDOW left behind on a Pro install -- without
# asserting GRIP wires this automatically. Unset is a pass on max/api (the
# full window is already the default there); unset on any other tier is
# informational only, since there is nothing configured to be wrong.
_check_tier_window() {
    local var_name="$1" expected="$2" label="$3"
    local env_val cfg_val configured
    env_val="${!var_name:-}"
    cfg_val=$(jq -r --arg k "$var_name" '.env[$k] // "missing"' "$SETTINGS" 2>/dev/null) \
        || cfg_val="missing"
    case "$cfg_val" in
        missing|null) cfg_val="" ;;
    esac
    configured="${env_val:-$cfg_val}"

    if [ -z "$configured" ]; then
        case "$GRIP_RESOLVED_TIER" in
            max|api) pass "$var_name not set ($label defaults to the full window on the $GRIP_RESOLVED_TIER tier)" ;;
            *)       info "$var_name not set ($label -- tier default applies)" ;;
        esac
    elif [ -z "$expected" ]; then
        info "$var_name=$configured (plan tier unresolved -- nothing to compare)"
    elif [ "$configured" = "$expected" ]; then
        pass "$var_name=$configured matches the $GRIP_RESOLVED_TIER tier's $label"
    else
        warn "$var_name=$configured does not match the $GRIP_RESOLVED_TIER tier's $label (expected $expected)"
        fix "Either update $var_name or confirm the plan tier with: grip tier status"
    fi
}

echo ""
echo "GRIP Health Check"
echo "================="
echo ""
echo "Scanning: $CLAUDE_DIR"
echo ""

# ============================================================================
# 1. CLAUDE.md exists
# ============================================================================
echo "${B}[1/12]${NC} Core Infrastructure"
if [ -f "$CLAUDE_DIR/CLAUDE.md" ]; then
    pass "CLAUDE.md found"
else
    fail "CLAUDE.md missing"
    fix "git clone https://github.com/grip-platform/GRIP.git ~/.claude"
fi

# Check git repo
if [ -d "$CLAUDE_DIR/.git" ]; then
    pass "Git repository initialized"
    branch=$(cd "$CLAUDE_DIR" && git branch --show-current 2>/dev/null || echo "unknown")
    info "Branch: $branch"
else
    fail "Not a git repository"
    fix "cd ~/.claude && git init && git remote add origin https://github.com/grip-platform/GRIP.git && git pull origin main"
fi

# ============================================================================
# 2. Settings.json exists and has hooks
# ============================================================================
echo ""
echo "${B}[2/12]${NC} Claude Code Settings"

SETTINGS="$CLAUDE_DIR/settings.json"
if [ -f "$SETTINGS" ]; then
    pass "settings.json found"

    # Canonical integrity (authoritative) — catches a clobbered GRIP block,
    # not just a hook-count drop (the 2026-06-25 incident). See lib/settings_validate.py.
    if command -v python3 >/dev/null 2>&1; then
        if integrity_out=$(python3 "$CLAUDE_DIR/lib/settings_validate.py" 2>&1); then
            pass "settings.json integrity OK (canonical GRIP block intact)"
        else
            fail "settings.json integrity check FAILED"
            printf '%s\n' "$integrity_out" | sed 's/^/        /'
            fix "Run: bash scripts/install.sh --update  (re-applies the canonical block)"
        fi
    fi

    # Check for hooks
    if command -v jq >/dev/null 2>&1; then
        hook_count=$(jq '[.hooks // {} | to_entries[] | .value[] | length] | add // 0' "$SETTINGS" 2>/dev/null || echo 0)
        if [ "$hook_count" -gt 0 ]; then
            pass "Hooks configured ($hook_count hook entries)"
        else
            fail "No hooks configured"
            fix "Run: bash ~/.claude/scripts/install.sh --update"
        fi

        # Check env vars -- tier-aware (operator decision, 2026-10-15): the
        # Pro launch default is medium effort, not max. On Pro, medium passes
        # and anything else (max included) warns and names the remedy, because
        # `grip-tier pro` writes medium (lib/pro_launch_defaults.py); it is
        # never a failure there.
        has_effort=$(jq -r '.env.CLAUDE_CODE_EFFORT_LEVEL // "missing"' "$SETTINGS" 2>/dev/null)
        if [ "$GRIP_RESOLVED_TIER" = "pro" ]; then
            if [ "$has_effort" = "medium" ]; then
                pass "CLAUDE_CODE_EFFORT_LEVEL=medium (the Pro launch default)"
            else
                warn "CLAUDE_CODE_EFFORT_LEVEL=$has_effort (the Pro launch default is medium)"
                fix "Run: grip-tier pro   (pins Sonnet 5.5 and sets medium effort)"
            fi
        elif [ "$has_effort" = "max" ]; then
            pass "CLAUDE_CODE_EFFORT_LEVEL=max"
        else
            fail "CLAUDE_CODE_EFFORT_LEVEL not set to 'max' (current: $has_effort)"
            fix "Add to settings.json env: \"CLAUDE_CODE_EFFORT_LEVEL\": \"max\""
        fi

        has_task_list=$(jq -r '.env.CLAUDE_CODE_TASK_LIST_ID // "missing"' "$SETTINGS" 2>/dev/null)
        if [ "$has_task_list" != "missing" ]; then
            pass "Task list ID configured ($has_task_list)"
        else
            warn "CLAUDE_CODE_TASK_LIST_ID not set"
            fix "Add to settings.json env: \"CLAUDE_CODE_TASK_LIST_ID\": \"grip\""
        fi

        # Check thinking -- tier-aware: thinking off is a real degradation on
        # every tier, but on Pro it is advisory (warn), matching the same
        # 2026-10-15 operator decision.
        has_thinking=$(jq -r '.alwaysThinkingEnabled // false' "$SETTINGS" 2>/dev/null)
        if [ "$has_thinking" = "true" ]; then
            pass "Extended thinking enabled (alwaysThinkingEnabled)"
        elif [ "$GRIP_RESOLVED_TIER" = "pro" ]; then
            warn "Extended thinking NOT enabled (advisory on the Pro tier)"
            fix "Add to settings.json: \"alwaysThinkingEnabled\": true"
        else
            fail "Extended thinking NOT enabled"
            fix "Add to settings.json: \"alwaysThinkingEnabled\": true"
        fi

        # Tier propagation: does the configured context/compact window
        # actually match what the resolved plan tier expects?
        _check_tier_window "CONTEXT_GATE_MAX" "$GRIP_TIER_ASSUMED_WINDOW" "context window"
        _check_tier_window "CLAUDE_CODE_AUTO_COMPACT_WINDOW" "$GRIP_TIER_COMPACT_WINDOW" "auto-compact window"
    else
        warn "jq not installed, cannot parse settings.json"
        fix "brew install jq (macOS) or apt install jq (Linux)"
        warn "jq missing: tier checks skipped"
    fi
else
    fail "settings.json missing"
    fix "Run: ~/.claude/scripts/grip-bootstrap-settings.sh"
fi

# ============================================================================
# 3. Status Line
# ============================================================================
echo ""
echo "${B}[3/12]${NC} Status Line"

if [ -f "$CLAUDE_DIR/statusline-command.sh" ]; then
    pass "statusline-command.sh exists"
else
    fail "statusline-command.sh missing"
    fix "git pull origin main (file should come from repo)"
fi

if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
    has_statusline=$(jq -r '.statusLine.enabled // false' "$SETTINGS" 2>/dev/null)
    has_sl_command=$(jq -r '.statusLine.command // "missing"' "$SETTINGS" 2>/dev/null)
    if [ "$has_statusline" = "true" ] || [ "$has_sl_command" != "missing" ]; then
        pass "Status line configured in settings"
    else
        fail "Status line NOT configured"
        fix "claude config set statusLine.enabled true"
        fix "claude config set statusLine.command 'bash -c '\\''bash \"\$HOME/.claude/statusline-command.sh\"'\\'''"
    fi
fi

# ============================================================================
# 4. Hooks Infrastructure
# ============================================================================
echo ""
echo "${B}[4/12]${NC} Hooks Infrastructure"

for hook in "hooks/session-start.sh" "hooks/session-end.sh"; do
    if [ -f "$CLAUDE_DIR/$hook" ]; then
        if [ -x "$CLAUDE_DIR/$hook" ]; then
            pass "$hook (executable)"
        else
            warn "$hook exists but not executable"
            fix "chmod +x ~/.claude/$hook"
        fi
    else
        fail "$hook missing"
        fix "git pull origin main"
    fi
done

if [ -f "$CLAUDE_DIR/hooks/context-gate-hook.py" ]; then
    pass "context-gate-hook.py found"
else
    fail "context-gate-hook.py missing (no context overflow protection)"
    fix "git pull origin main"
fi


# ============================================================================
# 5. KONO / Python venv
# ============================================================================
echo ""
echo "${B}[5/12]${NC} KONO Substrate"

if [ -d "$CLAUDE_DIR/venv" ]; then
    pass "Python venv exists"

    if [ -x "$CLAUDE_DIR/venv/bin/python3" ]; then
        pass "venv/bin/python3 is executable"

        # Test KONO import
        kono_test=$("$CLAUDE_DIR/venv/bin/python3" -c "
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.home() / '.claude' / 'lib'))
try:
    from kono_substrate import MemorySubstrate
    print('OK')
except Exception as e:
    print(f'FAIL:{e}')
" 2>&1)

        if [ "$kono_test" = "OK" ]; then
            pass "KONO substrate importable"
        else
            fail "KONO substrate import failed: $kono_test"
            fix "cd ~/.claude && python3 scripts/bootstrap-semantic-rl.sh"
        fi

        # Check embeddings DB
        if [ -f "$CLAUDE_DIR/embeddings.db" ]; then
            pass "embeddings.db exists"
            db_size=$(wc -c < "$CLAUDE_DIR/embeddings.db" 2>/dev/null | tr -d ' ')
            info "Size: ${db_size} bytes"
        else
            warn "embeddings.db missing (KONO has no data)"
            fix "Run: python3 ~/.claude/lib/kono_substrate.py stats"
        fi
    else
        fail "venv/bin/python3 not executable"
        fix "chmod +x ~/.claude/venv/bin/python3"
    fi
else
    fail "Python venv missing (KONO disabled)"
    fix "cd ~/.claude && python3 -m venv venv && ./scripts/bootstrap-semantic-rl.sh"
fi

# ============================================================================
# 6. MCP Servers
# ============================================================================
echo ""
echo "${B}[6/12]${NC} MCP Servers"

if [ -d "$CLAUDE_DIR/mcp-servers/grip-mcp-desktop" ]; then
    pass "grip-mcp-desktop directory exists"
    if [ -f "$CLAUDE_DIR/mcp-servers/grip-mcp-desktop/dist/server.js" ]; then
        pass "grip-mcp-desktop built (dist/server.js exists)"
    else
        fail "grip-mcp-desktop not built"
        fix "cd ~/.claude/mcp-servers/grip-mcp-desktop && npm install && npm run build"
    fi
else
    warn "grip-mcp-desktop not installed"
    fix "Check MCP server setup in docs"
fi

# ============================================================================
# 7. Skills & Agents
# ============================================================================
echo ""
echo "${B}[7/12]${NC} Skills & Agents"

if [ -d "$CLAUDE_DIR/skills" ]; then
    skill_count=$(ls -d "$CLAUDE_DIR/skills"/*/ 2>/dev/null | wc -l | tr -d ' ')
    pass "Skills directory: $skill_count skills"
else
    fail "Skills directory missing"
fi

if [ -d "$CLAUDE_DIR/agents" ]; then
    agent_count=$(ls "$CLAUDE_DIR/agents"/*.md 2>/dev/null | wc -l | tr -d ' ')
    pass "Agents directory: $agent_count agents"
else
    fail "Agents directory missing"
fi

# ============================================================================
# 8. Rules Directory
# ============================================================================
echo ""
echo "${B}[8/12]${NC} Rules & Config"

for rule in "rules/efficiency-rules.md" "rules/bash-safety.md" "rules/commit-standards.md" "rules/session-protocol.md"; do
    if [ -f "$CLAUDE_DIR/$rule" ]; then
        pass "$rule"
    else
        fail "$rule missing"
    fi
done

# ============================================================================
# 9. Model State (ACE)
# ============================================================================
echo ""
echo "${B}[9/12]${NC} Model State (ACE)"

model_state_file="$CLAUDE_DIR/state/session/model-state.json"
[ -f "$model_state_file" ] || model_state_file="$CLAUDE_DIR/.model-state.json"

if [ -f "$model_state_file" ]; then
    pass "model-state.json exists"
    if command -v jq >/dev/null 2>&1; then
        model=$(jq -r '.active_model // "unknown"' "$model_state_file" 2>/dev/null)
        ctx=$(jq -r '.context_window // "unknown"' "$model_state_file" 2>/dev/null)
        info "Model: $model, Context: $ctx"
    fi
else
    warn "model-state.json missing (ACE not initialized)"
    fix "Will be created on first session with /extend-context"
fi

# ============================================================================
# 10. Scripts & Bin
# ============================================================================
echo ""
echo "${B}[10/12]${NC} Scripts & Bin"

if [ -d "$CLAUDE_DIR/scripts" ]; then
    script_count=$(ls "$CLAUDE_DIR/scripts"/*.sh "$CLAUDE_DIR/scripts"/*.py 2>/dev/null | wc -l | tr -d ' ')
    pass "Scripts directory: $script_count scripts"
else
    fail "Scripts directory missing"
fi

if [ -d "$CLAUDE_DIR/bin" ]; then
    pass "bin/ directory exists"
    if echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.claude/bin"; then
        pass "bin/ is in PATH"
    else
        warn "bin/ not in PATH"
        fix "Add to ~/.zshrc: export PATH=\"\$HOME/.claude/bin:\$PATH\""
    fi
else
    warn "bin/ directory missing"
fi

# ============================================================================
# 11. Lib Directory
# ============================================================================
echo ""
echo "${B}[11/12]${NC} Core Libraries"

for lib in "lib/converge.py" "lib/instance_serializer.py" "lib/periodic_scanner.py" "lib/kono_substrate.py" "lib/kono_schema.py"; do
    if [ -f "$CLAUDE_DIR/$lib" ]; then
        pass "$lib"
    else
        fail "$lib missing"
    fi
done

# ============================================================================
# 12. Identity & Team
# ============================================================================
echo ""
echo "${B}[12/12]${NC} Identity & Team"

if [ -f "$CLAUDE_DIR/facts/team.md" ]; then
    pass "team.md configured"
else
    info "team.md not found (solo mode)"
fi

# Either marker: .onboarded.json (the /activate-grip wizard) or the legacy .onboarded.
if [ -f "$CLAUDE_DIR/.onboarded" ] || [ -f "$CLAUDE_DIR/.onboarded.json" ]; then
    pass "GRIP onboarding completed"
else
    warn "GRIP onboarding not completed"
    fix "Run /activate-grip in Claude Code"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "========================================"
TOTAL=$((PASS + FAIL + WARN))
SCORE=$((PASS * 100 / TOTAL))

if [ "$FAIL" -eq 0 ]; then
    echo -e "  ${G}GRIP Health: EXCELLENT${NC} ($SCORE%)"
elif [ "$FAIL" -le 3 ]; then
    echo -e "  ${Y}GRIP Health: GOOD${NC} ($SCORE%)"
elif [ "$FAIL" -le 6 ]; then
    echo -e "  ${Y}GRIP Health: FAIR${NC} ($SCORE%)"
else
    echo -e "  ${R}GRIP Health: NEEDS ATTENTION${NC} ($SCORE%)"
fi

echo ""
echo -e "  ${G}$PASS passed${NC}  ${R}$FAIL failed${NC}  ${Y}$WARN warnings${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "Quick fix: Run bash ~/.claude/scripts/install.sh --update"
    echo "Full fix:  Run bash ~/.claude/scripts/install.sh"
    echo ""
fi
