#!/usr/bin/env bash
#
# GRIP Deployment Script
# Secure deployment with mandatory security verification.
#
# Usage: ./deploy-grip.sh [--verify] [--deploy] [--dry-run] [--self-test] [--fix]
#                         [--mode local|vps|container|managed]
#

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$CLAUDE_DIR/lib"

# Configuration defaults
RETENTION_DAYS="${GRIP_INSTANCE_RETENTION_DAYS:-30}"
AUDIT_RETENTION_DAYS=90
DRY_RUN=false
SELF_TEST=false
FIX_MODE=false

# Source shared logging functions
source "${CLAUDE_DIR}/lib/logging.sh"

# Export for sourced scripts
export CLAUDE_DIR DRY_RUN SELF_TEST FIX_MODE RETENTION_DAYS
export RED GREEN YELLOW BLUE NC
export -f log_pass log_fail log_warn log_info log_dry

# ============================================================================
# CONFIGURATION
# ============================================================================

load_config() {
    local config="$CLAUDE_DIR/config/deployment.yaml"

    if [[ -f "$config" ]]; then
        if command -v yq &>/dev/null; then
            RETENTION_DAYS=$(yq -r '.deployment.instance_retention_days // 30' "$config" 2>/dev/null)
            AUDIT_RETENTION_DAYS=$(yq -r '.deployment.audit_log_retention_days // 90' "$config" 2>/dev/null)
        else
            log_info "yq not installed - using default config values"
        fi
    fi

    # Environment overrides config file
    RETENTION_DAYS="${GRIP_INSTANCE_RETENTION_DAYS:-$RETENTION_DAYS}"
}

# ============================================================================
# DEPLOYMENT MODE
# ============================================================================

detect_deployment_mode() {
    if [[ -n "${GRIP_DETECTED_MODE:-}" ]]; then
        echo "$GRIP_DETECTED_MODE"
    elif [[ -n "${GRIP_DEPLOYMENT_TOKEN:-}" ]]; then
        echo "managed"
    elif [[ -f "/.dockerenv" ]] || grep -q docker /proc/1/cgroup 2>/dev/null; then
        echo "container"
    elif [[ -n "${SSH_CONNECTION:-}" ]]; then
        echo "vps"
    else
        echo "local"
    fi
}

export -f detect_deployment_mode

# ============================================================================
# SOURCE LIBRARIES
# ============================================================================

source_libraries() {
    if [[ -f "$LIB_DIR/deploy-verify.sh" ]]; then
        # shellcheck source=../lib/deploy-verify.sh
        source "$LIB_DIR/deploy-verify.sh"
    else
        log_fail "Missing library: $LIB_DIR/deploy-verify.sh"
        exit 1
    fi

    if [[ -f "$LIB_DIR/deploy-actions.sh" ]]; then
        # shellcheck source=../lib/deploy-actions.sh
        source "$LIB_DIR/deploy-actions.sh"
    else
        log_fail "Missing library: $LIB_DIR/deploy-actions.sh"
        exit 1
    fi
}

# ============================================================================
# USAGE
# ============================================================================

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --verify      Run security verification only
  --deploy      Run full deployment with security checks
  --dry-run     Show what would happen without making changes
  --self-test   Run verification without sudo requirements
  --fix         Auto-correct common permission issues
  --mode MODE   Override deployment mode (local|vps|container|managed)
  -h, --help    Show this help message

Configuration:
  Config file: \$CLAUDE_DIR/config/deployment.yaml
  Instance retention: $RETENTION_DAYS days

Environment Variables:
  GRIP_DEPLOYMENT_TOKEN         Set for FDE-managed deployments
  GRIP_INSTANCE_RETENTION_DAYS  Override retention (default: 30)
  CLAUDE_DIR                    Override ~/.claude location

Examples:
  $0 --verify                   # Verify security rules
  $0 --self-test                # Verify without sudo
  $0 --verify --fix             # Verify and fix issues
  $0 --deploy --dry-run         # Preview deployment
  $0 --deploy                   # Full deployment

EOF
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    local action="verify"
    local mode_override=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --verify)
                action="verify"
                shift
                ;;
            --deploy)
                action="deploy"
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --self-test)
                SELF_TEST=true
                shift
                ;;
            --fix)
                FIX_MODE=true
                shift
                ;;
            --mode)
                mode_override="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    # Load configuration
    load_config

    # Override detection if specified
    if [[ -n "$mode_override" ]]; then
        export GRIP_DETECTED_MODE="$mode_override"
    fi

    # Export flags for sourced scripts
    export DRY_RUN SELF_TEST FIX_MODE

    # Source libraries
    source_libraries

    # Execute action
    case "$action" in
        verify)
            run_security_verification
            ;;
        deploy)
            run_deployment
            ;;
    esac
}

main "$@"
