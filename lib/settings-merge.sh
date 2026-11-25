#!/usr/bin/env bash
#
# GRIP Settings Merge — Single source of truth for settings.json generation
#
# Merges settings.reference.json with existing settings.json.
# Preserves: permissions, language, model (user-managed fields)
# Overwrites: hooks, env, statusLine, alwaysThinkingEnabled (GRIP-managed)
#
# Usage:
#   source "$CLAUDE_DIR/lib/settings-merge.sh"
#   merge_settings_from_reference "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR/config/settings.reference.json"
#

# Guard against double-sourcing
if [[ -n "${_GRIP_SETTINGS_MERGE_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_GRIP_SETTINGS_MERGE_LOADED=1

# Where THIS file lives, captured at source time. The Python backend that does
# the merge ships beside this script, so that is the copy this script must run.
# Resolving it through $CLAUDE_DIR instead made the merge depend on an
# environment variable any caller can repoint: the test suite sets CLAUDE_DIR to
# a hermetic temp dir, and every merge test then failed with "Canonical settings
# merge missing" against a directory that was never meant to hold lib/.
# cd and pwd are bash builtins, so this resolves under a restricted PATH.
# dirname is NOT a builtin: using it here broke the jq-missing test, whose
# whole point is that the merge works with only bash and python3 on PATH.
_GRIP_SETTINGS_MERGE_DIR="$(cd "${BASH_SOURCE[0]%/*}" 2>/dev/null && pwd)"

merge_settings_from_reference() {
    local settings_file="$1"
    local reference_file="$2"
    local _tmp_files=()
    trap 'rm -f ${_tmp_files[@]+"${_tmp_files[@]}"} 2>/dev/null' RETURN

    if [[ ! -f "$reference_file" ]]; then
        echo "[WARN] Reference settings not found: $reference_file" >&2
        return 1
    fi

    # Canonical smart merge via Python. There is deliberately no jq/copy
    # fallback: a degraded merge can erase mixed-group local hooks or install a
    # stale hook tree. Missing runtime support is safer as a visible failure.
    local PYTHON
    PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
    local smart_merge="${_GRIP_SETTINGS_MERGE_DIR:-}/settings_merge.py"
    # Fall back to the install path only if the sibling copy is absent, so an
    # unusual layout still resolves the way it always did.
    if [[ ! -f "$smart_merge" ]]; then
        smart_merge="${CLAUDE_DIR:-$HOME/.claude}/lib/settings_merge.py"
    fi

    if [[ -z "$PYTHON" ]]; then
        echo "[ERROR] Python is required for the canonical settings merge" >&2
        return 1
    fi
    if [[ ! -f "$smart_merge" ]]; then
        echo "[ERROR] Canonical settings merge missing: $smart_merge" >&2
        return 1
    fi
    if ! "$PYTHON" "$smart_merge" \
        --settings "$settings_file" \
        --reference "$reference_file" \
        --backup; then
        echo "[ERROR] Canonical settings merge failed; settings were not replaced" >&2
        return 1
    fi
    return 0
}
