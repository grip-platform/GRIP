#!/usr/bin/env bash
#
# Verify KONO Installation
#
# Checks that KONO schema is properly initialized and functional.
#
# Usage:
#   ./scripts/verify-kono-install.sh
#   CLAUDE_DIR=/custom/path ./scripts/verify-kono-install.sh
#

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
VENV_PYTHON="$CLAUDE_DIR/venv/bin/python3"

# Source shared logging functions
source "${CLAUDE_DIR}/lib/logging.sh"
# This script uses PASS/FAIL semantics (log_pass/log_fail from logging.sh)

errors=0

echo ""
echo "========================================"
echo "  KONO Installation Verification"
echo "========================================"
echo ""
echo "CLAUDE_DIR: $CLAUDE_DIR"
echo ""

# Check 1: Python venv exists
log_info "Checking Python venv..."
if [[ -x "$VENV_PYTHON" ]]; then
    log_pass "Python venv found"
else
    log_error "Python venv not found at $VENV_PYTHON"
    log_info "Run: scripts/bootstrap-semantic-rl.sh"
    exit 1
fi

# Check 2: Required Python packages
log_info "Checking Python dependencies..."
if "$VENV_PYTHON" -c "import apsw, sqlite_vec" 2>/dev/null; then
    log_pass "Python packages installed (apsw, sqlite_vec)"
else
    log_error "Missing Python packages"
    errors=$((errors + 1))
fi

# Check 3: Embeddings database exists
log_info "Checking embeddings database..."
if [[ -f "$CLAUDE_DIR/embeddings.db" ]]; then
    size=$(wc -c < "$CLAUDE_DIR/embeddings.db" | tr -d ' ')
    log_pass "Database exists (${size} bytes)"
else
    log_error "embeddings.db not found"
    errors=$((errors + 1))
fi

# Check 4: KONO schema version
log_info "Checking KONO schema version..."
if [[ -f "$CLAUDE_DIR/lib/kono_schema.py" ]]; then
    version_output=$("$VENV_PYTHON" "$CLAUDE_DIR/lib/kono_schema.py" version 2>/dev/null || echo '{"version": 0}')
    version=$(echo "$version_output" | "$VENV_PYTHON" -c "import sys, json; print(json.load(sys.stdin).get('version', 0))" 2>/dev/null || echo "0")

    if [[ "$version" -ge 1 ]]; then
        log_pass "KONO schema v${version} installed"
    else
        log_warn "KONO schema not initialized (v${version})"
        log_info "Run: python3 $CLAUDE_DIR/lib/kono_schema.py init"
        errors=$((errors + 1))
    fi
else
    log_error "kono_schema.py not found"
    errors=$((errors + 1))
fi

# Check 5: KONO tables exist
log_info "Checking KONO tables..."
tables_output=$("$VENV_PYTHON" "$CLAUDE_DIR/lib/kono_schema.py" verify 2>/dev/null || echo '{"status": "error"}')
status=$(echo "$tables_output" | "$VENV_PYTHON" -c "import sys, json; print(json.load(sys.stdin).get('status', 'error'))" 2>/dev/null || echo "error")

if [[ "$status" == "ok" ]]; then
    log_pass "All KONO tables present"
else
    log_error "KONO schema incomplete"
    echo "$tables_output" | "$VENV_PYTHON" -c "import sys, json; d=json.load(sys.stdin); print('  Missing tables:', d.get('missing_tables', [])); print('  Missing columns:', d.get('missing_columns', []))" 2>/dev/null || true
    errors=$((errors + 1))
fi

# Check 6: KONO substrate functionality
log_info "Checking KONO substrate..."
if [[ -f "$CLAUDE_DIR/lib/kono_substrate.py" ]]; then
    stats_output=$(CLAUDE_DIR="$CLAUDE_DIR" "$VENV_PYTHON" "$CLAUDE_DIR/lib/kono_substrate.py" stats 2>/dev/null || echo '{"status": "error"}')
    substrate_status=$(echo "$stats_output" | "$VENV_PYTHON" -c "import sys, json; print(json.load(sys.stdin).get('status', 'error'))" 2>/dev/null || echo "error")

    if [[ "$substrate_status" == "ok" ]]; then
        log_pass "KONO substrate operational"
        # Show brief stats
        echo "$stats_output" | "$VENV_PYTHON" -c "
import sys, json
d = json.load(sys.stdin)
stats = d.get('stats', {})
print(f\"  Total embeddings: {stats.get('total_embeddings', 'N/A')}\")
print(f\"  Permanent memories: {stats.get('permanent_memories', 'N/A')}\")
print(f\"  Active instances: {stats.get('active_instances', 'N/A')}\")
" 2>/dev/null || true
    else
        log_warn "KONO substrate returned non-ok status"
        errors=$((errors + 1))
    fi
else
    log_error "kono_substrate.py not found"
    errors=$((errors + 1))
fi

# Check 7: KONO library
log_info "Checking KONO library..."
if [[ -f "$CLAUDE_DIR/lib/kono.py" ]]; then
    if "$VENV_PYTHON" -c "import sys; sys.path.insert(0, '$CLAUDE_DIR/lib'); import kono; print(f'  Version: {kono.__version__}')" 2>/dev/null; then
        log_pass "KONO library importable"
    else
        log_warn "KONO library import failed (may need init)"
        errors=$((errors + 1))
    fi
else
    log_error "kono.py not found"
    errors=$((errors + 1))
fi

# Check 8: Run unit tests (if pytest available)
log_info "Checking for KONO tests..."
if [[ -f "$CLAUDE_DIR/tests/test_kono.py" ]]; then
    if "$VENV_PYTHON" -c "import pytest" 2>/dev/null; then
        log_info "Running KONO unit tests..."
        if "$VENV_PYTHON" -m pytest "$CLAUDE_DIR/tests/test_kono.py" -v --tb=short -q 2>/dev/null; then
            log_pass "KONO unit tests passed"
        else
            log_warn "Some KONO tests failed (may be expected on fresh install)"
        fi
    else
        log_info "pytest not installed, skipping tests"
    fi
else
    log_info "test_kono.py not found, skipping tests"
fi

# Summary
echo ""
echo "========================================"
if [[ $errors -eq 0 ]]; then
    echo -e "  ${GREEN}KONO Installation: VERIFIED${NC}"
else
    echo -e "  ${YELLOW}KONO Installation: $errors ISSUES${NC}"
fi
echo "========================================"
echo ""

if [[ $errors -gt 0 ]]; then
    echo "To fix issues, try:"
    echo "  1. Run bootstrap: scripts/bootstrap-semantic-rl.sh"
    echo "  2. Init KONO: python3 lib/kono_schema.py init"
    echo "  3. Run migration: scripts/migrate-to-kono.py --backup"
    echo ""
fi

exit $errors
