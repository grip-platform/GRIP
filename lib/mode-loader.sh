#!/usr/bin/env bash
# Mode Loader Library for GRIP
# Version: 3.0.0 | Gen: 476 | Per-session mode isolation

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
# grip-paths.sh defines grip_wave_position_path, which activate_mode calls
# since GRIP#8534. Source it from beside THIS file first: a hermetic test, or
# any caller whose CLAUDE_DIR points at a tree other than this checkout, has no
# lib/grip-paths.sh under CLAUDE_DIR, and under `set -u` the first call then
# dies with "command not found" (exit 127; tests/test_mode_activation_5242.py,
# three tests red on main from 2026-10-18 02:22 UTC). CLAUDE_DIR stays the
# fallback for a caller that copied this one file somewhere else.
_GRIP_MODE_LOADER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_GRIP_MODE_LOADER_DIR/grip-paths.sh" ]]; then
    # shellcheck source=/dev/null
    source "$_GRIP_MODE_LOADER_DIR/grip-paths.sh"
elif [[ -f "$CLAUDE_DIR/lib/grip-paths.sh" ]]; then
    # shellcheck source=/dev/null
    source "$CLAUDE_DIR/lib/grip-paths.sh"
fi
MODES_DIR="$CLAUDE_DIR/modes"
DEFINITIONS_DIR="$MODES_DIR/definitions"
MAX_MODES=${GRIP_MAX_MODES:-30}
MAX_MODE_BUDGET=${GRIP_MAX_MODE_BUDGET:-50000}

# --- Per-Session Mode Isolation ---
# Each Claude Code session gets its own mode state file keyed on the CC PID.
# Process tree: Claude Code (node) -> shell -> bash (mode-loader.sh)
# So CC PID = grandparent of this script's PID.
_resolve_cc_pid() {
    if [[ -n "${GRIP_CC_PID:-}" ]]; then
        echo "$GRIP_CC_PID"
        return 0
    fi
    local cc_pid=""
    if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]]; then
        # On Windows this shell's parent is claude.exe, a native program, so $PPID is 1,
        # or a fresh shell's PID in every tool call. Claude Code gives every process it
        # starts its own PID as CLAUDE_PID, which stays the same for the whole session
        # (GRIP#8743). Digits only; with no key the global files below are used.
        if [[ "${CLAUDE_PID:-}" =~ ^[0-9]+$ ]]; then
            cc_pid="$CLAUDE_PID"
        fi
    else
        cc_pid=$(ps -o ppid= -p $PPID 2>/dev/null | tr -d ' ')
    fi
    if [[ -n "$cc_pid" ]] && [[ "$cc_pid" != "1" ]]; then
        echo "$cc_pid"
    fi
}

# Resolve session-scoped mode files. Falls back to global if no CC PID found.
_GRIP_TMP="${GRIP_TMP_DIR:-${TMPDIR:-/tmp}}"
_CC_PID=$(_resolve_cc_pid)
if [[ -n "$_CC_PID" ]]; then
    ACTIVE_MODE_FILE="${_GRIP_TMP}/grip-session-mode-${_CC_PID}"
    ACTIVE_MODES_FILE="${_GRIP_TMP}/grip-session-modes-${_CC_PID}"
else
    # Fallback: global files (CLI usage outside Claude Code)
    ACTIVE_MODE_FILE="$CLAUDE_DIR/.active-mode"
    ACTIVE_MODES_FILE="$CLAUDE_DIR/.active-modes"
fi

# Broly trigger state, scoped exactly like the mode files above.
if [[ -n "$_CC_PID" ]]; then
    BROLY_TRIGGER_FILE="${_GRIP_TMP}/grip-session-broly-${_CC_PID}"
else
    BROLY_TRIGGER_FILE="$CLAUDE_DIR/.broly-trigger"
fi

# Resolve how Broly fires: "auto" (mode protocols decide) or "manual" (only an
# explicit /broly). Precedence: env override, then session state, then auto.
#
# The default is deliberately "auto". full-power-stop-override.md says a
# capability that startles gets an operator-owned brake, never a default-off
# gate that downgrades it for everyone -- so this is opt-in and anyone who
# never touches it sees byte-identical behaviour.
get_broly_trigger() {
    local v="${GRIP_BROLY_TRIGGER:-}"
    if [[ -z "$v" ]] && [[ -f "$BROLY_TRIGGER_FILE" ]]; then
        v=$(cat "$BROLY_TRIGGER_FILE" 2>/dev/null)
    fi
    case "$v" in
        manual) echo "manual" ;;
        *)      echo "auto" ;;
    esac
}

# Name where the effective value comes from. Precedence is env > session
# state > default, so without this a reader cannot tell why status disagrees
# with what they just set.
broly_trigger_source() {
    if [[ -n "${GRIP_BROLY_TRIGGER:-}" ]]
    then
        echo "GRIP_BROLY_TRIGGER env var"
    elif [[ -f "$BROLY_TRIGGER_FILE" ]]
    then
        echo "session state"
    else
        echo "default"
    fi
}

# A set that cannot take effect must not print a success message. Before this,
# `broly auto` under GRIP_BROLY_TRIGGER=manual printed "Broly trigger: auto"
# and status still said manual -- the command reported success while having no
# effect, which is worse than failing. AGORA council, PR #6379.
# $3 names what actually happened to the state file. The auto branch REMOVES
# it (absence is the default) while manual writes it, so one shared "was
# written" is false half the time -- the same operator-facing imprecision this
# function exists to correct. AGORA council suggestion 2, PR #6379.
_broly_report_set() {
    local want="$1" ok_msg="$2" state_verb="$3"
    local effective
    effective=$(get_broly_trigger)
    if [[ "$effective" != "$want" ]]
    then
        echo "Broly trigger NOT changed to '$want'." >&2
        echo "  GRIP_BROLY_TRIGGER=${GRIP_BROLY_TRIGGER:-} outranks session state," >&2
        echo "  so the effective trigger is still '$effective'." >&2
        echo "  Session state was ${state_verb} and takes effect after:" >&2
        echo "    unset GRIP_BROLY_TRIGGER" >&2
        return 1
    fi
    echo "$ok_msg"
    if [[ "$want" == "manual" ]]
    then
        echo "State: $BROLY_TRIGGER_FILE"
    fi
    return 0
}

set_broly_trigger() {
    local want="${1:-}"
    case "$want" in
        auto)
            rm -f "$BROLY_TRIGGER_FILE"
            _broly_report_set "auto" "Broly trigger: auto (mode protocols decide; this is the default)" "cleared"
            ;;
        manual)
            printf "manual\n" > "$BROLY_TRIGGER_FILE"
            _broly_report_set "manual" "Broly trigger: manual -- fan-out ONLY on an explicit /broly." "written"
            ;;
        status|"")
            echo "Broly trigger: $(get_broly_trigger)  [source: $(broly_trigger_source)]"
            ;;
        *)
            echo "Unknown Broly trigger '$want'. Use: auto | manual | status" >&2
            return 1
            ;;
    esac
}

# Auto-migrate .active-mode -> .active-modes on first multi-mode access
migrate_mode_state() {
    if [[ -f "$ACTIVE_MODE_FILE" ]] && [[ ! -f "$ACTIVE_MODES_FILE" ]]; then
        cp "$ACTIVE_MODE_FILE" "$ACTIVE_MODES_FILE"
    fi
}

# Get all active modes (newline-separated), with .active-mode fallback
get_active_modes() {
    if [[ -f "$ACTIVE_MODES_FILE" ]]; then
        cat "$ACTIVE_MODES_FILE"
    elif [[ -f "$ACTIVE_MODE_FILE" ]]; then
        cat "$ACTIVE_MODE_FILE"
    fi
}

# Broly power level priority (index = priority, higher index = higher level)
BROLY_LEVELS=("thread" "fork" "cluster" "pipeline" "mesh")

# Extract broly config from a mode YAML
extract_broly_config() {
    local yaml_file="$1"

    if command -v yq &>/dev/null; then
        local enabled
        enabled=$(yq -r '.broly.enabled // false' "$yaml_file" 2>/dev/null)
        if [[ "$enabled" != "true" ]]; then
            echo "disabled"
            return 0
        fi
        local level
        level=$(yq -r '.broly.default_level // "thread"' "$yaml_file" 2>/dev/null)
        local pattern
        pattern=$(yq -r '.broly.team_pattern // "none"' "$yaml_file" 2>/dev/null)
        local hint
        hint=$(yq -r '.broly.reasoning_hint // "cot"' "$yaml_file" 2>/dev/null)
        echo "$level:$pattern:$hint"
    else
        # sed fallback
        local enabled
        enabled=$(sed -n '/^broly:/,/^[a-z]/{ s/^ *enabled: *//p; }' "$yaml_file" 2>/dev/null | head -1 | tr -d ' ')
        if [[ "$enabled" != "true" ]]; then
            echo "disabled"
            return 0
        fi
        local level
        level=$(sed -n '/^broly:/,/^[a-z]/{ s/^ *default_level: *//p; }' "$yaml_file" 2>/dev/null | head -1 | tr -d ' ')
        local pattern
        pattern=$(sed -n '/^broly:/,/^[a-z]/{ s/^ *team_pattern: *//p; }' "$yaml_file" 2>/dev/null | head -1 | tr -d ' ')
        local hint
        hint=$(sed -n '/^broly:/,/^[a-z]/{ s/^ *reasoning_hint: *//p; }' "$yaml_file" 2>/dev/null | head -1 | tr -d ' ')
        [[ -z "$level" ]] && level="thread"
        [[ -z "$pattern" || "$pattern" == "null" ]] && pattern="none"
        [[ -z "$hint" ]] && hint="cot"
        echo "$level:$pattern:$hint"
    fi
}

# Get broly level priority index (higher = more powerful)
_broly_level_priority() {
    local level="$1"
    local i=0
    for l in "${BROLY_LEVELS[@]}"; do
        if [[ "$l" == "$level" ]]; then
            echo "$i"
            return 0
        fi
        i=$((i + 1))
    done
    echo "0"
}

# Extract skills.always list from a mode YAML without yq
extract_always_skills() {
    local yaml_file="$1"
    if command -v yq &>/dev/null; then
        yq -r '.skills.always // [] | .[]' "$yaml_file" 2>/dev/null
    else
        sed -n '/^  always:/,/^  [a-z]/p' "$yaml_file" 2>/dev/null | sed -n 's/^ *- //p' | sed 's/ *#.*//'
    fi
}

# Check if mode is hidden
is_mode_hidden() {
    local yaml_file="$1"
    local hidden="false"

    if command -v yq &>/dev/null; then
        hidden=$(yq -r '.hidden // false' "$yaml_file" 2>/dev/null)
    else
        hidden=$(grep -m1 '^hidden:' "$yaml_file" 2>/dev/null | sed 's/^hidden: *//' | tr -d ' ' || echo "false")
    fi

    [[ "$hidden" == "true" ]]
}

# List org-specific modes
list_org_modes() {
    local org="${GRIP_ORG:-default}"
    [ "$org" = "default" ] && return
    local dir="$CLAUDE_DIR/organizations/$org/modes"
    [ -d "$dir" ] || return
    for f in "$dir"/*.yaml "$dir"/*.yml; do
        [ -f "$f" ] && basename "$f" .yaml | sed 's/-mode$//'
    done
}

# List all available modes
list_modes() {
    echo "Available GRIP Modes:"
    echo "─────────────────────"

    local active_modes_content=""
    active_modes_content=$(get_active_modes)

    for yaml_file in "$DEFINITIONS_DIR"/*.yaml; do
        if [[ -f "$yaml_file" ]]; then
            # Skip hidden modes
            if is_mode_hidden "$yaml_file"; then
                continue
            fi

            local name
            name=$(basename "$yaml_file" .yaml | sed 's/-mode$//')
            local desc=""

            # Extract description from YAML (simple grep, no yq dependency)
            if command -v yq &>/dev/null; then
                desc=$(yq -r '.description // "No description"' "$yaml_file" 2>/dev/null)
            else
                desc=$(grep -m1 '^description:' "$yaml_file" 2>/dev/null | sed 's/^description: *//' | tr -d '"' || echo "No description")
            fi

            # Check if active (multi-mode aware)
            local active=""
            if [[ -n "$active_modes_content" ]] && echo "$active_modes_content" | grep -qx "$name"; then
                active=" [ACTIVE]"
            fi

            if [[ -n "$active" ]]; then
                printf "  \033[32m%-12s %s%s\033[0m\n" "$name" "$desc" "$active"
            else
                printf "  %-12s %s\n" "$name" "$desc"
            fi
        fi
    done

    echo ""
    echo "Usage: /mode <name> [name2] [name3] | /mode off | /mode status"
}

# Get current active mode (backward compat: returns first mode)
get_active_mode() {
    local modes
    modes=$(get_active_modes)
    if [[ -n "$modes" ]]; then
        echo "$modes" | head -1
    else
        echo ""
    fi
}

# Load mode definition and return skills
# Strip the tilde/quote decoration mode YAMLs carry ('token_budget: ~2500') down to a
# number bash arithmetic can take. Measured 2026-09-01: 30 of 31 definitions use the
# tilde form, and $((x + '~2500')) parses ~ as bitwise NOT, yielding -2501 — so the
# yq-present pre-check path ADDED NEGATIVES for 30 of 31 modes. Non-numeric or empty
# input falls back to 3000, the same neutral estimate the no-file path uses (GRIP#5242).
_sanitise_budget() {
    local v="${1//[^0-9]/}"
    echo "${v:-3000}"
}

# Print each mode's prompt_injection payload. This IS the activation fix for GRIP#5242:
# /mode runs this script as a tool call, and the model reads tool stdout — verified
# against live session transcripts — so printing the protocol here is what finally
# delivers it. A mode without the key (server-mode.yaml) emits nothing and that is
# legal, never an error. Prefers the venv python (PyYAML) with a stdlib fallback
# inside mode_prompt.py itself, so absence of the venv degrades, not breaks.
_emit_mode_prompts() {
    local _py="$CLAUDE_DIR/venv/bin/python"
    [[ -x "$_py" ]] || _py="python3"
    local _mp="$CLAUDE_DIR/lib/mode_prompt.py"
    [[ -f "$_mp" ]] || _mp="$(dirname "${BASH_SOURCE[0]}")/mode_prompt.py"
    [[ -f "$_mp" ]] || return 0
    local _m _payload
    for _m in "$@"; do
        _payload=$("$_py" "$_mp" "$DEFINITIONS_DIR/${_m}-mode.yaml" 2>/dev/null)
        if [[ -n "$_payload" ]]; then
            echo ""
            echo "--- MODE PROTOCOL ($_m) — follow this for the rest of the session ---"
            echo "$_payload"
        fi
    done
}

load_mode_definition() {
    local mode_name="$1"
    local yaml_file="$DEFINITIONS_DIR/${mode_name}-mode.yaml"

    if [[ ! -f "$yaml_file" ]]; then
        echo "ERROR: Mode '$mode_name' not found" >&2
        return 1
    fi

    if command -v yq &>/dev/null; then
        yq -o=json "$yaml_file"
    else
        # No yq: real JSON via python-yaml, or a LOUD failure. The old fallback printed
        # "YAML_FILE:<path>" at rc=0 — a degradation dressed as success (GRIP#5242).
        # Zero in-tree callers existed at fix time, so nothing depends on the old shape.
        local _py="$CLAUDE_DIR/venv/bin/python"
        [[ -x "$_py" ]] || _py="python3"
        "$_py" - "$yaml_file" <<'PYJSON'
import json, sys
try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: neither yq nor python-yaml available; cannot emit JSON" + chr(10))
    raise SystemExit(1)
with open(sys.argv[1], encoding="utf-8") as fh:
    print(json.dumps(yaml.safe_load(fh)))
PYJSON
    fi
}

# Semantic mode matching fallback. The GRIP folder and the typed name reach Python as
# arguments, never inside its source (GRIP#9333): a Windows path holds "\U", which
# Python reads as an escape, so the program did not compile and the loader stopped
# under set -e with no message and no list; and a typed name pasted into the source
# runs as code. The fallback has loaded nothing since GRIP#836 removed
# lib/skill-indexer.py. Whether to point it at lib/skill_indexer.py or remove it is
# open on GRIP#9333; a match activates the mode, so reviving it needs the
# hidden-mode gate too.
semantic_match_mode() {
    python3 - "$CLAUDE_DIR" "$1" 2>/dev/null <<'PY'
import sys
from pathlib import Path
import importlib.util

claude_dir, query = sys.argv[1], sys.argv[2]
lib_path = Path(claude_dir) / 'lib' / 'skill-indexer.py'
if not lib_path.exists():
    sys.exit(0)

spec = importlib.util.spec_from_file_location('skill_indexer', lib_path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    indexer = module.SkillIndexer()
    results = indexer.search(query, concept_type='concept_mode', limit=1)
    if results and results[0].get('rrf_score', 0) > 0.005:
        meta = results[0].get('metadata', {})
        print(meta.get('name', ''))
    indexer.close()
except Exception:
    pass
PY
}

# Activate one or more modes (max MAX_MODES)
# Record a mode switch event to metrics.jsonl
_record_mode_switch() {
    local from_mode="${1:-none}"
    local to_mode="${2:-none}"
    local metrics_file="$CLAUDE_DIR/metrics.jsonl"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '{"event":"mode_switch","from_mode":"%s","to_mode":"%s","ts":"%s"}\n' \
        "$from_mode" "$to_mode" "$ts" >> "$metrics_file" 2>/dev/null
}

activate_mode() {
    local -a resolved_modes=()

    # Auto mode: use LLM inference to select modes from remaining args
    if [[ "${1:-}" == "auto" ]] && [[ $# -gt 1 ]]; then
        shift
        local auto_prompt="$*"
        local auto_result
        auto_result=$(python3 "$CLAUDE_DIR/lib/auto_mode_selector.py" "$auto_prompt" 2>/dev/null)
        if [[ -n "$auto_result" ]]; then
            echo "[AUTO MODE — LLM Inference]"
            echo "$auto_result"
            echo ""
            # Extract mode names from output (first line: "Modes: x + y + z (~budget)")
            local auto_modes
            auto_modes=$(echo "$auto_result" | head -1 | sed 's/Modes: //' | sed 's/ (~.*//' | tr '+' ' ' | tr -s ' ')
            if [[ -n "$auto_modes" ]]; then
                # Recurse with inferred modes
                activate_mode $auto_modes
                return $?
            fi
        fi
        echo "WARNING: Auto mode inference failed, falling back to manual"
    fi

    for mode_name in "$@"; do
        # Check org-specific modes directory first
        local yaml_file=""
        local org="${GRIP_ORG:-default}"
        if [ "$org" != "default" ]; then
            local org_mode_dir="$CLAUDE_DIR/organizations/$org/modes"
            for candidate in "$org_mode_dir/${mode_name}-mode.yaml" "$org_mode_dir/${mode_name}.yaml"; do
                if [ -f "$candidate" ]; then
                    yaml_file="$candidate"
                    break
                fi
            done
        fi
        [ -z "$yaml_file" ] && yaml_file="$DEFINITIONS_DIR/${mode_name}-mode.yaml"

        if [[ ! -f "$yaml_file" ]]; then
            local semantic_match
            # A fallback that fails must not stop the loader: the not-found message
            # and the mode list still follow (GRIP#9333).
            semantic_match=$(semantic_match_mode "$mode_name") || semantic_match=""

            if [[ -n "$semantic_match" ]] && [[ -f "$DEFINITIONS_DIR/${semantic_match}-mode.yaml" ]]; then
                echo "[SEMANTIC MATCH: '$mode_name' -> '$semantic_match']"
                mode_name="$semantic_match"
            else
                echo "ERROR: Mode '$mode_name' not found"
                echo "Available modes:"
                # Filter hidden modes here exactly as list_modes does. The raw find used to
                # print EVERY definition, so any typo revealed the hidden-for-security set
                # (flow, server, oracle, rsi observed) that the list command deliberately
                # withholds — the error path was a disclosure oracle (GRIP#5242 adjacent
                # defect 2). Same is_mode_hidden gate, so the two surfaces cannot diverge.
                local _err_yaml
                for _err_yaml in "$DEFINITIONS_DIR"/*.yaml; do
                    [[ -f "$_err_yaml" ]] || continue
                    is_mode_hidden "$_err_yaml" && continue
                    basename "$_err_yaml" .yaml | sed 's/-mode$//' | sed 's/^/  /'
                done
                return 1
            fi
        fi

        resolved_modes+=("$mode_name")
    done

    if [[ ${#resolved_modes[@]} -gt $MAX_MODES ]]; then
        echo "ERROR: Maximum $MAX_MODES modes allowed. Got ${#resolved_modes[@]}: ${resolved_modes[*]}"
        return 1
    fi

    # Dynamic budget check: sum mode budgets, warn if exceeding ceiling
    local estimated_budget=0
    for _check_mode in "${resolved_modes[@]}"; do
        local _check_yaml="$DEFINITIONS_DIR/${_check_mode}-mode.yaml"
        if [[ -f "$_check_yaml" ]]; then
            local _mbud=""
            if command -v yq &>/dev/null; then
                _mbud=$(yq -r '.token_budget // 3000' "$_check_yaml" 2>/dev/null)
            else
                # Same extraction the Budget display line already uses successfully.
                _mbud=$(grep -m1 '^token_budget:' "$_check_yaml" 2>/dev/null | cut -d: -f2)
            fi
            estimated_budget=$((estimated_budget + $(_sanitise_budget "$_mbud")))
        else
            estimated_budget=$((estimated_budget + 3000))
        fi
    done
    if [[ $estimated_budget -gt $MAX_MODE_BUDGET ]]; then
        echo "WARNING: Combined mode budget ~${estimated_budget} exceeds ceiling ${MAX_MODE_BUDGET}. Context may be constrained."
    fi

    # Capture the OUTGOING mode BEFORE overwriting state. This read used to sit after
    # the writes below, so it read back the mode just written and every activation was
    # logged as {"from_mode": X, "to_mode": X} — the transition metric recorded no
    # transitions (GRIP#5242 adjacent defect 5; observed live in metrics.jsonl). The
    # deactivation path already read-before-delete and was correct.
    local from_mode="none"
    if [[ -f "$ACTIVE_MODE_FILE" ]]; then
        from_mode=$(cat "$ACTIVE_MODE_FILE" 2>/dev/null || echo "none")
    fi

    printf "%s\n" "${resolved_modes[@]}" > "$ACTIVE_MODES_FILE"
    echo "${resolved_modes[0]}" > "$ACTIVE_MODE_FILE"

    local mode_list
    mode_list=$(printf "%s" "${resolved_modes[*]}" | tr ' ' '+')
    echo "[MODE ACTIVATED: $mode_list]"
    _emit_mode_prompts "${resolved_modes[@]}"
    echo ""

    _record_mode_switch "$from_mode" "$mode_list"

    local total_budget=0
    for mode_name in "${resolved_modes[@]}"; do
        local yaml_file="$DEFINITIONS_DIR/${mode_name}-mode.yaml"
        local always_skills="" optional_skills="" budget=""

        always_skills=$(extract_always_skills "$yaml_file" | paste -sd',' -)
        if command -v yq &>/dev/null; then
            optional_skills=$(yq -r '.skills.optional // [] | join(", ")' "$yaml_file" 2>/dev/null)
            budget=$(yq -r '.token_budget // "0"' "$yaml_file" 2>/dev/null)
        else
            optional_skills=$(sed -n '/^  optional:/,/^[a-z]/p' "$yaml_file" 2>/dev/null | grep '^ *- ' | sed 's/^ *- //' | sed 's/ *#.*//' | paste -sd',' -)
            budget=$(grep -m1 '^token_budget:' "$yaml_file" 2>/dev/null | sed 's/^token_budget: *//' | tr -d '~' || echo "0")
        fi

        echo "[$mode_name]"
        echo "  Always: $always_skills"
        [[ -n "$optional_skills" ]] && echo "  Optional: $optional_skills"
        echo "  Budget: $budget"

        local budget_num
        budget_num=$(echo "$budget" | tr -d '~' | tr -dc '0-9')
        total_budget=$((total_budget + ${budget_num:-0}))
    done

    if [[ ${#resolved_modes[@]} -gt 1 ]]; then
        echo ""
        echo "Combined budget: ~$total_budget"
    fi

    # Broly status output
    local highest_broly_level=""
    local highest_broly_priority=-1
    local highest_broly_pattern=""
    local highest_broly_hint=""

    for mode_name in "${resolved_modes[@]}"; do
        local yaml_file="$DEFINITIONS_DIR/${mode_name}-mode.yaml"
        local broly_config
        broly_config=$(extract_broly_config "$yaml_file")

        if [[ "$broly_config" != "disabled" ]]; then
            local b_level b_pattern b_hint
            b_level="${broly_config%%:*}"
            local rest="${broly_config#*:}"
            b_pattern="${rest%%:*}"
            b_hint="${rest#*:}"
            local priority
            priority=$(_broly_level_priority "$b_level")

            if [[ "$priority" -gt "$highest_broly_priority" ]]; then
                highest_broly_priority=$priority
                highest_broly_level="$b_level"
                highest_broly_pattern="$b_pattern"
                highest_broly_hint="$b_hint"
            fi
        fi
    done

    # Multi-mode broly escalation: 2+ modes with broly -> escalate one level above highest
    local broly_enabled_count=0
    for mode_name in "${resolved_modes[@]}"; do
        local yaml_file="$DEFINITIONS_DIR/${mode_name}-mode.yaml"
        local broly_config
        broly_config=$(extract_broly_config "$yaml_file")
        [[ "$broly_config" != "disabled" ]] && broly_enabled_count=$((broly_enabled_count + 1))
    done

    if [[ "$broly_enabled_count" -ge 2 && -n "$highest_broly_level" ]]; then
        # Special combination: code + architect -> mesh with council pattern, got reasoning
        local has_code=false has_architect=false
        for mode_name in "${resolved_modes[@]}"; do
            [[ "$mode_name" == "code" ]] && has_code=true
            [[ "$mode_name" == "architect" ]] && has_architect=true
        done

        if [[ "$has_code" == "true" && "$has_architect" == "true" ]]; then
            highest_broly_level="mesh"
            highest_broly_pattern="council"
            highest_broly_hint="got"
        else
            # Generic: escalate one level above highest individual
            local next_idx=$((highest_broly_priority + 1))
            if [[ "$next_idx" -lt ${#BROLY_LEVELS[@]} ]]; then
                highest_broly_level="${BROLY_LEVELS[$next_idx]}"
            fi
            # mesh is max, can't go higher
        fi
    fi

    if [[ -n "$highest_broly_level" ]]; then
        echo ""
        local pattern_info=""
        if [[ "$highest_broly_pattern" != "none" && -n "$highest_broly_pattern" ]]; then
            pattern_info=" ($highest_broly_pattern, $highest_broly_hint)"
        else
            pattern_info=" ($highest_broly_hint)"
        fi
        local broly_trigger
        broly_trigger=$(get_broly_trigger)
        if [[ "$broly_trigger" == "manual" ]]; then
            echo "Broly: $highest_broly_level (MANUAL -- /broly only)$pattern_info"
            echo "  OVERRIDE: the mode protocols above say Broly is AUTO-ACTIVE."
            echo "  That is overridden for this session. Do NOT fan out to agents"
            echo "  without an explicit /broly from the operator."
        else
            echo "Broly: $highest_broly_level (auto-activate)$pattern_info"
        fi
    fi

    # Wave enforcement: check modes against active wave state
    local wave_file
    wave_file="$(grip_wave_position_path)"
    if [[ -f "$wave_file" ]]; then
        local wave_output
        wave_output=$(python3 "$CLAUDE_DIR/lib/wave_enforcer.py" check "${resolved_modes[@]}" 2>>"$HOME/.claude/logs/wave-enforce.log")
        if [[ -n "$wave_output" ]]; then
            echo ""
            echo "$wave_output"
        fi
    fi

    echo ""
    echo "Foundation (PARAMOUNT) skills remain active."
}

# Deactivate all modes
deactivate_mode() {
    local had_modes=false

    if [[ -f "$ACTIVE_MODES_FILE" ]]; then
        local prev_modes
        prev_modes=$(cat "$ACTIVE_MODES_FILE" | paste -sd'+' -)
        _record_mode_switch "$prev_modes" "none"
        rm -f "$ACTIVE_MODES_FILE"
        rm -f "$ACTIVE_MODE_FILE"
        echo "[MODE DEACTIVATED: $prev_modes]"
        had_modes=true
    elif [[ -f "$ACTIVE_MODE_FILE" ]]; then
        local prev_mode
        prev_mode=$(cat "$ACTIVE_MODE_FILE")
        _record_mode_switch "$prev_mode" "none"
        rm -f "$ACTIVE_MODE_FILE"
        echo "[MODE DEACTIVATED: $prev_mode]"
        had_modes=true
    fi

    if [[ "$had_modes" == "true" ]]; then
        echo ""
        echo "Foundation (PARAMOUNT) skills remain active."
    else
        echo "No active mode to deactivate."
    fi
}

# Show current mode status (multi-mode aware)
mode_status() {
    local modes
    modes=$(get_active_modes)

    if [[ -z "$modes" ]]; then
        echo "No mode currently active."
        echo "Foundation (PARAMOUNT) skills are always available."
    else
        local mode_count
        mode_count=$(echo "$modes" | wc -l | tr -d ' ')
        local mode_list
        mode_list=$(echo "$modes" | paste -sd'+' -)
        echo "Active Mode(s): $mode_list ($mode_count active)"
        echo ""

        while IFS= read -r active_mode; do
            local yaml_file="$DEFINITIONS_DIR/${active_mode}-mode.yaml"
            if [[ -f "$yaml_file" ]]; then
                local desc="" always_skills="" optional_skills="" budget=""

                if command -v yq &>/dev/null; then
                    desc=$(yq -r '.description // "N/A"' "$yaml_file")
                    always_skills=$(yq -r '.skills.always // [] | join(", ")' "$yaml_file")
                    optional_skills=$(yq -r '.skills.optional // [] | join(", ")' "$yaml_file")
                    budget=$(yq -r '.token_budget // "N/A"' "$yaml_file")
                else
                    desc=$(grep -m1 '^description:' "$yaml_file" 2>/dev/null | sed 's/^description: *//' | tr -d '"' || echo "N/A")
                    always_skills=$(extract_always_skills "$yaml_file" | paste -sd',' -)
                    optional_skills=$(sed -n '/^  optional:/,/^[a-z]/p' "$yaml_file" 2>/dev/null | grep '^ *- ' | sed 's/^ *- //' | sed 's/ *#.*//' | paste -sd',' -)
                    budget=$(grep -m1 '^token_budget:' "$yaml_file" 2>/dev/null | sed 's/^token_budget: *//' || echo "N/A")
                fi

                echo "[$active_mode] $desc"
                echo "  Always: $always_skills"
                [[ -n "$optional_skills" ]] && echo "  Optional: $optional_skills"
                echo "  Budget: $budget"
                echo ""
            fi
        done <<< "$modes"
    fi
}

# Main dispatcher
main() {
    local cmd="${1:-}"
    shift || true

    case "$cmd" in
        list)
            list_modes
            ;;
        status)
            mode_status
            ;;
        off)
            deactivate_mode
            ;;
        get)
            get_active_mode
            ;;
        broly)
            set_broly_trigger "${1:-status}"
            ;;
        load)
            load_mode_definition "${1:-}"
            ;;
        "")
            list_modes
            ;;
        *)
            # Pass all remaining args for multi-mode support
            activate_mode "$cmd" "$@"
            ;;
    esac
}

# Run if executed directly
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    main "$@"
fi
