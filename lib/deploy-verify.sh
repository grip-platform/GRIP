#!/usr/bin/env bash
#
# GRIP Deployment Verification Library
# Security verification functions for 5 rules.
#
# Sourced by: scripts/deploy-grip.sh
#

# Ensure CLAUDE_DIR is set
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# ============================================================================
# SEARCH HELPER (rg with grep fallback)
# ============================================================================

_search() {
    # Usage: _search [rg_args...] <pattern> <path>
    # Falls back to grep if rg not available
    local args=()
    local pattern=""
    local path=""
    local type_filter=""
    local ignore_case=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --type)
                type_filter="$2"
                shift 2
                ;;
            -i)
                ignore_case="-i"
                shift
                ;;
            --glob)
                # Skip glob args for grep fallback
                shift 2
                ;;
            *)
                if [[ -z "$pattern" ]]; then
                    pattern="$1"
                else
                    path="$1"
                fi
                shift
                ;;
        esac
    done

    if command -v rg &>/dev/null; then
        # Use ripgrep
        local rg_args=()
        [[ -n "$ignore_case" ]] && rg_args+=(-i)
        [[ -n "$type_filter" ]] && rg_args+=(--type "$type_filter")
        rg "${rg_args[@]}" "$pattern" "$path" 2>/dev/null
    else
        # Fallback to grep
        local grep_args=(-r)
        [[ -n "$ignore_case" ]] && grep_args+=(-i)
        if [[ -n "$type_filter" ]]; then
            case "$type_filter" in
                py) grep_args+=(--include="*.py") ;;
                sh) grep_args+=(--include="*.sh") ;;
                js) grep_args+=(--include="*.js") ;;
            esac
        fi
        grep "${grep_args[@]}" "$pattern" "$path" 2>/dev/null
    fi
}

# ============================================================================
# RULE 1: No Localhost Trust
# ============================================================================

verify_rule_1_localhost() {
    local failures=0

    # Check for localhost trust patterns in Python files
    if _search -i --type py "127.0.0.1|localhost" "$CLAUDE_DIR/lib" 2>/dev/null | grep -qi "trust\|allow\|skip.*auth"; then
        log_fail "Rule 1: Found localhost trust patterns"
        failures=$((failures + 1))
    fi

    # Check for IP-based auth bypass
    if _search -i "remote_addr.*127\|request\.ip.*local" "$CLAUDE_DIR/lib" 2>/dev/null | grep -qi "if\|auth"; then
        log_fail "Rule 1: Found IP-based auth bypass"
        failures=$((failures + 1))
    fi

    if [[ $failures -eq 0 ]]; then
        log_pass "Rule 1: No localhost trust patterns"
        return 0
    fi
    return 1
}

# ============================================================================
# RULE 2: Secrets Encryption
# ============================================================================

verify_rule_2_secrets() {
    local failures=0

    # Check for plaintext API keys (sk-, pk_ patterns)
    if _search -i "(sk-[a-zA-Z0-9]{20,}|pk_[a-zA-Z0-9]{20,})" "$CLAUDE_DIR" \
        --glob '!*.md' --glob '!*.template' --glob '!*.example' 2>/dev/null; then
        log_fail "Rule 2: Found plaintext API keys"
        failures=$((failures + 1))
    fi

    # Check for hardcoded passwords (exclude commented lines and placeholders)
    if _search -i "password\s*=\s*['\"][^'\"]+['\"]" "$CLAUDE_DIR/lib" 2>/dev/null | \
        grep -v "^#" | grep -v "your-" | grep -v "example" | grep -v "template"; then
        log_fail "Rule 2: Found hardcoded passwords"
        failures=$((failures + 1))
    fi

    if _search -i "password\s*=\s*['\"][^'\"]+['\"]" "$CLAUDE_DIR/scripts" 2>/dev/null | \
        grep -v "^#" | grep -v "your-" | grep -v "example" | grep -v "template"; then
        log_fail "Rule 2: Found hardcoded passwords in scripts"
        failures=$((failures + 1))
    fi

    # Check .env not in git
    if [[ -d "$CLAUDE_DIR/.git" ]]; then
        if git -C "$CLAUDE_DIR" ls-files --error-unmatch .env 2>/dev/null; then
            log_fail "Rule 2: .env is tracked in git"
            failures=$((failures + 1))
        fi
    fi

    # Check .env permissions if exists
    if [[ -f "$CLAUDE_DIR/.env" ]]; then
        local perms
        perms=$(stat -f %Lp "$CLAUDE_DIR/.env" 2>/dev/null || stat -c %a "$CLAUDE_DIR/.env" 2>/dev/null)
        if [[ "$perms" != "600" ]]; then
            log_warn "Rule 2: .env permissions are $perms (should be 600)"
            if [[ "${FIX_MODE:-false}" == "true" ]]; then
                chmod 600 "$CLAUDE_DIR/.env"
                log_info "Rule 2: Fixed .env permissions"
            fi
        fi
    fi

    if [[ $failures -eq 0 ]]; then
        log_pass "Rule 2: No plaintext secrets found"
        return 0
    fi
    return 1
}

# ============================================================================
# RULE 3: Instance File Protection
# ============================================================================

verify_rule_3_instances() {
    local failures=0

    # Check for world-readable instance files
    local world_readable
    world_readable=$(find "$CLAUDE_DIR/projects" -name "*.json" -perm -004 2>/dev/null | head -5)
    if [[ -n "$world_readable" ]]; then
        log_warn "Rule 3: World-readable instance files found"
        echo "$world_readable" | head -3
        if [[ "${FIX_MODE:-false}" == "true" ]]; then
            find "$CLAUDE_DIR/projects" -name "*.json" -exec chmod 600 {} \; 2>/dev/null || true
            log_info "Rule 3: Fixed instance file permissions"
        fi
    fi

    # Check for HTTP routes to instances (would need to check web server config)
    if [[ -f "/etc/nginx/sites-enabled/grip" ]] || [[ -f "/etc/caddy/Caddyfile" ]]; then
        if _search -i "location.*/instance|proxy.*instance" /etc/nginx/sites-enabled/ 2>/dev/null || \
           _search -i "location.*/instance|proxy.*instance" /etc/caddy/ 2>/dev/null; then
            log_fail "Rule 3: Found HTTP routes to instance files"
            failures=$((failures + 1))
        fi
    fi

    if [[ $failures -eq 0 ]]; then
        log_pass "Rule 3: Instance files protected"
        return 0
    fi
    return 1
}

# ============================================================================
# RULE 4: Network Egress Control
# ============================================================================

verify_rule_4_egress() {
    local mode
    mode=$(detect_deployment_mode)

    case "$mode" in
        container)
            log_info "Rule 4: Container mode - egress controlled by orchestrator"
            ;;
        vps)
            # Check if ufw or iptables has egress rules
            if [[ "${SELF_TEST:-false}" == "true" ]]; then
                log_info "Rule 4: Self-test mode - skipping sudo-required checks"
            elif command -v ufw &>/dev/null && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then
                log_pass "Rule 4: UFW firewall active"
            elif command -v iptables &>/dev/null && sudo -n iptables -L OUTPUT 2>/dev/null | grep -q "DROP"; then
                log_pass "Rule 4: iptables egress rules configured"
            else
                log_warn "Rule 4: No egress firewall detected (configure UFW or iptables)"
            fi
            ;;
        managed)
            log_pass "Rule 4: Managed deployment - egress controlled by FDE"
            ;;
        local)
            # macOS firewall detection
            if [[ "$(uname)" == "Darwin" ]]; then
                if [[ "${SELF_TEST:-false}" == "true" ]]; then
                    log_info "Rule 4: Self-test mode - skipping firewall checks"
                else
                    local fw_status
                    fw_status=$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null || echo "")
                    if echo "$fw_status" | grep -qi "enabled"; then
                        log_pass "Rule 4: macOS Application Firewall enabled"
                    else
                        log_info "Rule 4: macOS Application Firewall disabled (local dev mode)"
                    fi

                    # Also check pf if available
                    if sudo -n pfctl -s info 2>/dev/null | grep -q "Status: Enabled"; then
                        log_pass "Rule 4: macOS pf firewall enabled"
                    fi
                fi
            else
                log_info "Rule 4: Local mode - egress not restricted"
            fi
            ;;
    esac

    return 0
}

# ============================================================================
# RULE 5: Audit Logging
# ============================================================================

verify_rule_5_audit() {
    local failures=0

    # Check if audit log directory exists
    local audit_log="/var/log/grip/audit.log"
    local alt_audit_log="$CLAUDE_DIR/logs/audit.log"

    if [[ -f "$audit_log" ]] || [[ -f "$alt_audit_log" ]]; then
        log_pass "Rule 5: Audit log exists"
    else
        if [[ "${FIX_MODE:-false}" == "true" ]]; then
            mkdir -p "$CLAUDE_DIR/logs"
            touch "$CLAUDE_DIR/logs/audit.log"
            chmod 600 "$CLAUDE_DIR/logs/audit.log"
            log_info "Rule 5: Created audit log at $CLAUDE_DIR/logs/audit.log"
        else
            log_warn "Rule 5: Audit log not found (use --fix to create)"
        fi
    fi

    return 0
}

# ============================================================================
# MAIN VERIFICATION RUNNER
# ============================================================================

run_security_verification() {
    echo ""
    echo "GRIP Security Verification"
    echo "=========================="
    echo ""

    local mode
    mode=$(detect_deployment_mode)
    log_info "Deployment mode: $mode"

    if [[ "${SELF_TEST:-false}" == "true" ]]; then
        log_info "Running in self-test mode (no sudo required)"
    fi

    if [[ "${FIX_MODE:-false}" == "true" ]]; then
        log_info "Fix mode enabled - will auto-correct issues"
    fi

    echo ""

    local passed=0
    local failed=0

    if verify_rule_1_localhost; then ((passed++)) || true; else ((failed++)) || true; fi
    if verify_rule_2_secrets; then ((passed++)) || true; else ((failed++)) || true; fi
    if verify_rule_3_instances; then ((passed++)) || true; else ((failed++)) || true; fi
    if verify_rule_4_egress; then ((passed++)) || true; else ((failed++)) || true; fi
    if verify_rule_5_audit; then ((passed++)) || true; else ((failed++)) || true; fi

    echo ""
    echo "=========================="

    if [[ $failed -eq 0 ]]; then
        echo -e "${GREEN}Security gate: PASSED${NC} ($passed/5 rules)"
        return 0
    else
        echo -e "${RED}Security gate: FAILED${NC} ($failed/5 rules failed)"
        return 1
    fi
}
