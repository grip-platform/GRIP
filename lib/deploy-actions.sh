#!/usr/bin/env bash
#
# GRIP Deployment Actions Library
# Deployment setup and configuration actions.
#
# Sourced by: scripts/deploy-grip.sh
#

# Ensure CLAUDE_DIR is set
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# ============================================================================
# AUDIT LOGGING SETUP
# ============================================================================

setup_audit_logging() {
    local log_dir="$CLAUDE_DIR/logs"

    if ${DRY_RUN:-false}; then
        log_dry "Create audit log at $log_dir/audit.log"
        log_dry "Set permissions to 600"
        log_dry "Attempt to set append-only flag (requires sudo)"
        return 0
    fi

    log_info "Setting up audit logging..."

    mkdir -p "$log_dir"

    # Create audit log
    touch "$log_dir/audit.log"
    chmod 600 "$log_dir/audit.log"

    # Attempt append-only flag with graceful degradation
    if [[ "${SELF_TEST:-false}" == "true" ]]; then
        log_info "Self-test mode: Skipping append-only flag (requires sudo)"
    elif command -v chattr &>/dev/null; then
        if sudo -n chattr +a "$log_dir/audit.log" 2>/dev/null; then
            log_pass "Audit log set to append-only"
        else
            log_warn "Could not set append-only flag (requires passwordless sudo)"
            log_info "Run manually: sudo chattr +a $log_dir/audit.log"
        fi
    elif [[ "$(uname)" == "Darwin" ]]; then
        # macOS uses different approach - append-only not available the same way
        log_info "macOS: append-only flag not available (use Time Machine for audit log protection)"
    fi

    log_pass "Audit logging configured at $log_dir/audit.log"
}

# ============================================================================
# INSTANCE FILE SECURITY
# ============================================================================

secure_instance_files() {
    if ${DRY_RUN:-false}; then
        log_dry "Set all instance .json files to 600"
        log_dry "Set all project directories to 700"
        return 0
    fi

    log_info "Securing instance files..."

    # Set restrictive permissions on all instance files
    find "$CLAUDE_DIR/projects" -type f -name "*.json" -exec chmod 600 {} \; 2>/dev/null || true
    find "$CLAUDE_DIR/projects" -type d -exec chmod 700 {} \; 2>/dev/null || true

    log_pass "Instance file permissions secured"
}

# ============================================================================
# MODE-SPECIFIC SETUP
# ============================================================================

run_mode_specific_setup() {
    local mode="$1"

    case "$mode" in
        container)
            log_info "Container deployment: Using orchestrator-managed secrets"
            ;;
        vps)
            log_info "VPS deployment: Verify SSH hardening and firewall manually"
            ;;
        managed)
            log_info "FDE-managed deployment: Token validated"
            ;;
        local)
            log_info "Local deployment: Development mode"
            ;;
    esac
}

# ============================================================================
# MAIN DEPLOYMENT RUNNER
# ============================================================================

run_deployment() {
    echo ""
    if ${DRY_RUN:-false}; then
        echo "GRIP Deployment (DRY RUN)"
        echo "========================="
    else
        echo "GRIP Deployment"
        echo "==============="
    fi
    echo ""

    local mode
    mode=$(detect_deployment_mode)
    log_info "Deployment mode: $mode"
    log_info "Instance retention: ${RETENTION_DAYS:-30} days"

    if ${DRY_RUN:-false}; then
        echo ""
        log_info "DRY RUN - No changes will be made"
        echo ""
    fi

    # Security verification first
    if ! run_security_verification; then
        log_fail "Security verification failed. Fix issues before deploying."
        exit 1
    fi

    echo ""
    log_info "Running deployment tasks..."

    # Setup audit logging
    setup_audit_logging

    # Secure instance files
    secure_instance_files

    # Mode-specific setup
    run_mode_specific_setup "$mode"

    echo ""
    log_pass "Deployment complete"
    echo ""
    echo "Next steps:"
    echo "  1. Verify firewall rules (Rule 4)"
    echo "  2. Configure log shipping for audit logs"
    echo "  3. Test with: claude --version"
    echo ""
}
