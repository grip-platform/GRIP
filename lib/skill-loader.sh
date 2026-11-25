#!/usr/bin/env bash
#
# Skill Loader Library
# Scans, registers, and activates skills from ~/.claude/skills/
#
# PURPOSE:
#   Skills exist as SKILL.md files but have no loading mechanism.
#   This library provides:
#   1. Skill discovery and indexing
#   2. Metadata extraction from YAML frontmatter
#   3. Trigger-based activation
#   4. On-demand loading to minimize token usage
#
# USAGE:
#   source ~/.claude/lib/skill-loader.sh
#
#   # Index all skills
#   index_all_skills
#
#   # Load specific skill
#   load_skill "context-refresh"
#
#   # Check if skill should activate
#   should_activate_skill "figma-to-code" "implement this Figma design"
#
# VERSION: 1.0.0
# DATE: 2025-11-27
#

set -euo pipefail

# ============================================================================
# DEPENDENCIES
# ============================================================================

# Source shared YAML utilities
_SKILL_LOADER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_SKILL_LOADER_DIR/yaml-utils.sh"

# ============================================================================
# CONSTANTS
# ============================================================================

# Simple assignment (bash 3.x compatible, safe when re-sourced)
SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
SKILLS_INDEX="${SKILLS_INDEX:-$HOME/.claude/skills-index.json}"
SKILLS_CACHE_DIR="${SKILLS_CACHE_DIR:-$HOME/.claude/.cache/skills}"

# ============================================================================
# LOGGING (Compatible with optim.sh)
# ============================================================================

_skill_log_info() {
    echo "[SKILL-LOADER][INFO] $*" >&2
}

_skill_log_warn() {
    echo "[SKILL-LOADER][WARN] $*" >&2
}

_skill_log_error() {
    echo "[SKILL-LOADER][ERROR] $*" >&2
}

_skill_log_success() {
    echo "[SKILL-LOADER][SUCCESS] $*" >&2
}

# ============================================================================
# YAML FRONTMATTER PARSING
# ============================================================================

# Parse YAML frontmatter from a SKILL.md file and convert to JSON
# Uses shared extract_frontmatter from yaml-utils.sh
# Returns: JSON object of frontmatter fields
parse_skill_frontmatter() {
    local skill_file="$1"

    if [[ ! -f "$skill_file" ]]; then
        echo "{}"
        return 1
    fi

    # Extract content between --- markers using shared utility
    local frontmatter
    frontmatter=$(extract_frontmatter "$skill_file")

    if [[ -z "$frontmatter" ]]; then
        echo "{}"
        return 0
    fi

    # Convert YAML to JSON (simple key: value parsing)
    local json="{"
    local first=true

    while IFS=: read -r key value; do
        # Skip empty lines and comments
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue

        # Skip YAML list items ("  - item"). They carry no colon, so the read
        # above leaves the whole line in $key and emits it as a JSON KEY --
        # which breaks the document outright when the item contains a quote
        # (skills/cowork-bootstrap carries `- '"/grip" command'`).
        [[ "$key" =~ ^[[:space:]]*- ]] && continue

        # Trim with parameter expansion, never xargs: xargs parses shell
        # quoting and exits 1 on an unbalanced apostrophe, which under
        # `set -e` (line 30) aborts the entire index build. Also avoids two
        # subshells per frontmatter line.
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        value="${value#\"}"
        value="${value%\"}"

        # Escape the key for JSON (backslash first). Keys are always strings.
        key="${key//\\/\\\\}"
        key="${key//\"/\\\"}"

        # Handle arrays (simple detection)
        if [[ "$value" =~ ^\[ ]] && printf '%s' "$value" | jq -e . >/dev/null 2>&1; then
            # Valid JSON array - keep as-is. The jq check is load-bearing: a
            # YAML flow array with unquoted elements ([grip, child, warm] in
            # skills/voice-chatting) is NOT valid JSON, and emitting it raw
            # invalidated the whole index. Anything that fails falls through
            # to the string branch -- preserved verbatim as text rather than
            # silently breaking the document.
            :
        elif [[ "$value" =~ ^[0-9]+$ ]]; then
            # Numeric value - no quotes
            :
        elif [[ "$value" == "true" || "$value" == "false" ]]; then
            # Boolean - no quotes
            :
        else
            # String value - escape, THEN quote. Escaping before the type
            # detection above would turn ["a"] into [\"a\"] and break the
            # raw-array branch.
            value="${value//\\/\\\\}"
            value="${value//\"/\\\"}"
            value="\"$value\""
        fi

        if [[ "$first" == "true" ]]; then
            first=false
        else
            json+=","
        fi

        json+="\"$key\":$value"
    done <<< "$frontmatter"

    json+="}"

    echo "$json"
}

# Extract specific field from frontmatter
get_frontmatter_field() {
    local skill_file="$1"
    local field="$2"

    parse_skill_frontmatter "$skill_file" | jq -r ".$field // empty"
}

# ============================================================================
# SKILL DISCOVERY
# ============================================================================

# Find all skill directories
discover_skills() {
    if [[ ! -d "$SKILLS_DIR" ]]; then
        _skill_log_warn "Skills directory not found: $SKILLS_DIR"
        return 1
    fi

    # Find all SKILL.md files, excluding archives.
    #
    # Archived skills are not indexable by construction: this loop rebuilds the
    # path as "$SKILLS_DIR/$name/SKILL.md" from a bare basename, which is wrong
    # for anything nested. Measured 2026-09-16 on the live tree: all 76
    # duplicate basenames were a live skill shadowed by its own archived copy
    # (0 live-vs-live), and all 12 unresolvable names were archived-only --
    # which is exactly `.meta.errors`. Consumers that need archived content read
    # it BY PATH and never consult this index; bin/grip:1394-1395 resolves the
    # authenticating-with-claude system prompt straight from skills/_archived/.
    #
    # `.archive` is already invisible to fd's hidden-file skip; it is named here
    # so the behaviour does not silently depend on that default.
    fd -t f "SKILL.md" "$SKILLS_DIR" \
        --exclude '_archived' --exclude '.archive' 2>/dev/null | \
        xargs -I{} dirname {} | \
        xargs -I{} basename {}
}

# Count total skills
count_skills() {
    discover_skills 2>/dev/null | wc -l | tr -d ' '
}

# Check if skill exists
skill_exists() {
    local skill_name="$1"
    local skill_file="$SKILLS_DIR/$skill_name/SKILL.md"

    [[ -f "$skill_file" ]]
}

# Get skill file path
get_skill_path() {
    local skill_name="$1"
    echo "$SKILLS_DIR/$skill_name/SKILL.md"
}

# ============================================================================
# SKILL INDEXING
# ============================================================================

# Build index of all skills with metadata
index_all_skills() {
    _skill_log_info "Indexing skills from: $SKILLS_DIR"

    local index="{\"skills\":{},\"meta\":{}}"
    local skill_count=0
    local error_count=0
    local collision_count=0

    while IFS= read -r skill_name; do
        [[ -z "$skill_name" ]] && continue

        local skill_file="$SKILLS_DIR/$skill_name/SKILL.md"

        if [[ -f "$skill_file" ]]; then
            # Extract metadata
            local metadata
            metadata=$(parse_skill_frontmatter "$skill_file")

            # Add file path and name
            metadata=$(echo "$metadata" | jq \
                --arg name "$skill_name" \
                --arg path "$skill_file" \
                '. + {skill_name: $name, file_path: $path}')

            # A name collision is silent data loss, so say so. discover_skills
            # reduces every path to its basename, and the loop then rebuilds
            # the path as "$SKILLS_DIR/$skill_name/SKILL.md" -- so a nested
            # <group>/<name>/SKILL.md collapses to <name> and this loop reads
            # the TOP-LEVEL <name> instead. The nested skill's content is never
            # indexed at all. Measured 2026-09-16: 382 adds produced 316 unique
            # keys, so 66 skills went missing without a word (GRIP#6019).
            # The check must run BEFORE the assignment below: once
            # `.skills[$name] = $meta` has executed the previous entry is
            # already gone, so `has($name)` afterwards is true for every skill
            # and distinguishes nothing.
            if printf '%s' "$index" | jq -e --arg name "$skill_name" \
                    '.skills | has($name)' >/dev/null 2>&1; then
                _skill_log_warn "duplicate skill name, overwriting: $skill_name"
                collision_count=$((collision_count + 1))
            fi

            # Add to index
            index=$(echo "$index" | jq \
                --arg name "$skill_name" \
                --argjson meta "$metadata" \
                '.skills[$name] = $meta')

            skill_count=$((skill_count + 1))
        else
            _skill_log_warn "SKILL.md not found for: $skill_name"
            error_count=$((error_count + 1))
        fi
    done < <(discover_skills)

    # Add meta information
    index=$(echo "$index" | jq \
        --arg count "$skill_count" \
        --arg errors "$error_count" \
        --arg collisions "$collision_count" \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '.meta = {
            total_skills: ($count | tonumber),
            stored_skills: (.skills | length),
            collisions: ($collisions | tonumber),
            errors: ($errors | tonumber),
            indexed_at: $timestamp
        }')

    # Save to index file, atomically. A plain `> "$SKILLS_INDEX"` truncates the
    # target to 0 bytes BEFORE the pipeline runs, so a build that is killed or
    # fails validation leaves an empty index -- which every later session then
    # correctly reads as stale, rebuilds, and fails again. That is how this
    # index reached 0 bytes and could not recover (GRIP#6013). Writing to a
    # temp file and renaming means a failed build leaves the previous index
    # intact, so the worst case is a stale index rather than no index at all.
    local _tmp="${SKILLS_INDEX}.$$.tmp"
    if ! echo "$index" | jq '.' > "$_tmp" 2>/dev/null; then
        rm -f "$_tmp"
        _skill_log_error "Index failed validation; kept the previous $SKILLS_INDEX"
        return 1
    fi

    # The rename can fail too -- a read-only directory, a full disk, or a
    # cross-device target. Left unchecked under `set -e` that aborts the caller
    # with the scratch file still on disk and nothing logged, so the one thing
    # an operator needs to know (the index is stale, and why) is exactly what
    # they would not be told. Handled explicitly, and the scratch file is
    # removed on every path that does not consume it.
    if ! mv -f "$_tmp" "$SKILLS_INDEX"; then
        rm -f "$_tmp"
        _skill_log_error "Could not install $SKILLS_INDEX; kept the previous index"
        return 1
    fi

    _skill_log_success "Indexed $skill_count skills ($error_count errors)"
    echo "$index"
}

# Get skill metadata from index
get_skill_metadata() {
    local skill_name="$1"

    if [[ ! -f "$SKILLS_INDEX" ]]; then
        index_all_skills > /dev/null
    fi

    jq -r ".skills[\"$skill_name\"] // empty" "$SKILLS_INDEX"
}

# List all indexed skills
list_skills() {
    if [[ ! -f "$SKILLS_INDEX" ]]; then
        index_all_skills > /dev/null
    fi

    jq -r '.skills | keys[]' "$SKILLS_INDEX"
}

# ============================================================================
# SKILL LOADING
# ============================================================================

# Load skill content (full SKILL.md)
load_skill() {
    local skill_name="$1"
    local skill_file
    skill_file=$(get_skill_path "$skill_name")

    if [[ ! -f "$skill_file" ]]; then
        _skill_log_error "Skill not found: $skill_name"
        return 1
    fi

    _skill_log_info "Loading skill: $skill_name"

    # Return content
    cat "$skill_file"
}

# Load skill summary (frontmatter + first section only)
load_skill_summary() {
    local skill_name="$1"
    local skill_file
    skill_file=$(get_skill_path "$skill_name")

    if [[ ! -f "$skill_file" ]]; then
        _skill_log_error "Skill not found: $skill_name"
        return 1
    fi

    # Extract frontmatter + first section (up to second ## header)
    awk '
        /^---$/ { if(fm) fm=0; else fm=1; print; next }
        fm { print; next }
        /^## / { if(seen_header) exit; seen_header=1 }
        { print }
    ' "$skill_file"
}

# Load multiple skills
load_skills() {
    local skills=("$@")

    for skill in "${skills[@]}"; do
        echo "=== SKILL: $skill ==="
        load_skill "$skill"
        echo ""
    done
}

# ============================================================================
# TRIGGER DETECTION
# ============================================================================

# Check if user input matches skill triggers
should_activate_skill() {
    local skill_name="$1"
    local user_input="$2"

    local metadata
    metadata=$(get_skill_metadata "$skill_name")

    if [[ -z "$metadata" ]]; then
        return 1
    fi

    # Get command trigger
    local command
    command=$(echo "$metadata" | jq -r '.command // empty')

    # Check if user input contains command
    if [[ -n "$command" && "$user_input" =~ $command ]]; then
        return 0
    fi

    # Check common trigger patterns based on skill name
    case "$skill_name" in
        context-refresh)
            [[ "$user_input" =~ (refresh|understand|context|session.start) ]] && return 0
            ;;
        figma-to-code)
            [[ "$user_input" =~ (figma|design|implement.*design|ui.*from) ]] && return 0
            ;;
        mobile-responsive-ui)
            [[ "$user_input" =~ (mobile|responsive|viewport|css|html|ui) ]] && return 0
            ;;
        pr-automation)
            [[ "$user_input" =~ (create.*pr|pull.*request|pr.*create) ]] && return 0
            ;;
        yagni-principle)
            [[ "$user_input" =~ (future|maybe|might.*need|just.*in.*case) ]] && return 0
            ;;
        *)
            # Generic: check if skill name appears in input
            [[ "$user_input" =~ $skill_name ]] && return 0
            ;;
    esac

    return 1
}

# Find all skills that should activate for given input
find_matching_skills() {
    local user_input="$1"
    local matches=()

    while IFS= read -r skill_name; do
        if should_activate_skill "$skill_name" "$user_input"; then
            matches+=("$skill_name")
        fi
    done < <(list_skills)

    printf '%s\n' "${matches[@]}"
}

# ============================================================================
# SKILL COMMANDS
# ============================================================================

# Get command for skill
get_skill_command() {
    local skill_name="$1"
    local metadata
    metadata=$(get_skill_metadata "$skill_name")

    echo "$metadata" | jq -r '.command // empty'
}

# Find skill by command
find_skill_by_command() {
    local command="$1"

    if [[ ! -f "$SKILLS_INDEX" ]]; then
        index_all_skills > /dev/null
    fi

    jq -r ".skills | to_entries[] | select(.value.command == \"$command\") | .key" "$SKILLS_INDEX"
}

# ============================================================================
# CACHE MANAGEMENT
# ============================================================================

# Cache loaded skill (to avoid re-reading)
cache_skill() {
    local skill_name="$1"
    local content="$2"

    mkdir -p "$SKILLS_CACHE_DIR"

    local cache_file="$SKILLS_CACHE_DIR/${skill_name}.cache"
    echo "$content" > "$cache_file"

    _skill_log_info "Cached skill: $skill_name"
}

# Get cached skill
get_cached_skill() {
    local skill_name="$1"
    local cache_file="$SKILLS_CACHE_DIR/${skill_name}.cache"

    if [[ -f "$cache_file" ]]; then
        cat "$cache_file"
        return 0
    fi

    return 1
}

# Clear skill cache
clear_skill_cache() {
    if [[ -d "$SKILLS_CACHE_DIR" ]] && [[ -n "$SKILLS_CACHE_DIR" ]]; then
        rm -rf "${SKILLS_CACHE_DIR:?}"/*
        _skill_log_info "Cleared skill cache"
    fi
}

# ============================================================================
# DIAGNOSTICS
# ============================================================================

# Print skill information
diagnose_skill() {
    local skill_name="$1"

    echo "=== Skill Diagnosis: $skill_name ==="
    echo ""

    if ! skill_exists "$skill_name"; then
        echo "Status: NOT FOUND"
        echo "Expected path: $(get_skill_path "$skill_name")"
        return 1
    fi

    local skill_file
    skill_file=$(get_skill_path "$skill_name")
    echo "Path: $skill_file"
    echo "Size: $(wc -c < "$skill_file" | tr -d ' ') bytes"
    echo "Lines: $(wc -l < "$skill_file" | tr -d ' ')"
    echo ""

    echo "Frontmatter:"
    parse_skill_frontmatter "$skill_file" | jq '.'
    echo ""

    echo "Sections:"
    rg '^## ' "$skill_file" 2>/dev/null || echo "(none found)"
    echo ""

    echo "=== End Diagnosis ==="
}

# Print all skills summary
diagnose_all_skills() {
    echo "=== Skills Summary ==="
    echo ""
    echo "Skills directory: $SKILLS_DIR"
    echo "Index file: $SKILLS_INDEX"
    echo ""

    # Index if needed
    if [[ ! -f "$SKILLS_INDEX" ]]; then
        index_all_skills > /dev/null
    fi

    echo "Total skills: $(count_skills)"
    echo ""

    echo "Skill list:"
    while IFS= read -r skill; do
        local command
        command=$(get_skill_command "$skill")
        if [[ -n "$command" ]]; then
            printf "  %-30s %s\n" "$skill" "$command"
        else
            printf "  %-30s (no command)\n" "$skill"
        fi
    done < <(list_skills)

    echo ""
    echo "=== End Summary ==="
}

# ============================================================================
# EXPORTS
# ============================================================================

export -f parse_skill_frontmatter
export -f get_frontmatter_field
export -f discover_skills
export -f count_skills
export -f skill_exists
export -f get_skill_path
export -f index_all_skills
export -f get_skill_metadata
export -f list_skills
export -f load_skill
export -f load_skill_summary
export -f load_skills
export -f should_activate_skill
export -f find_matching_skills
export -f get_skill_command
export -f find_skill_by_command
export -f cache_skill
export -f get_cached_skill
export -f clear_skill_cache
export -f diagnose_skill
export -f diagnose_all_skills

# ============================================================================
# MAIN (if run directly)
# ============================================================================

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-help}" in
        index|--index|-i)
            index_all_skills
            ;;
        list|--list|-l)
            list_skills
            ;;
        load|--load)
            load_skill "${2:-}"
            ;;
        summary|--summary)
            load_skill_summary "${2:-}"
            ;;
        diagnose|--diagnose|-d)
            if [[ -n "${2:-}" ]]; then
                diagnose_skill "$2"
            else
                diagnose_all_skills
            fi
            ;;
        match|--match|-m)
            find_matching_skills "${2:-}"
            ;;
        help|--help|-h)
            cat << 'EOF'
Skill Loader Library - Scan, register, and activate skills

USAGE:
    ./skill-loader.sh [command] [args]

COMMANDS:
    index               Build index of all skills
    list                List all indexed skills
    load <skill>        Load full skill content
    summary <skill>     Load skill summary (frontmatter + first section)
    diagnose [skill]    Diagnose skill(s)
    match <input>       Find skills matching input
    help                Show this help message

AS LIBRARY:
    source ~/.claude/lib/skill-loader.sh

    # Index all skills
    index_all_skills

    # Load a skill
    content=$(load_skill "context-refresh")

    # Find matching skills
    matches=$(find_matching_skills "implement this Figma design")

EOF
            ;;
        *)
            echo "Unknown command: $1"
            echo "Run '$0 help' for usage"
            exit 1
            ;;
    esac
fi
