#!/usr/bin/env bash
#
# Autonomous Learning Engine - Bash Interface
# Wrapper for learning-detector.py
#
# USAGE:
#   source ~/.claude/lib/learning.sh
#   detect_learning "message text" [novelty_score]
#   process_learning "message text" [novelty_score] [project_path]
#   list_pending_skills
#   approve_skill "candidate_id"
#   reject_skill "candidate_id" ["reason"]
#
# INTEGRATION:
#   Sourced by hooks/startup/init.sh at session start. Prompts reach the
#   detector through hooks/learning-hook.py (UserPromptSubmit), which runs
#   lib/learning_detector.py itself. monitor_learning() below has had no
#   caller since hooks/tool-monitor.sh was merged into context-gate-hook.py.
#
# SOURCE: Gen 50, Dialectical Learning Preplan
# DATE: 2025-12-21

set -euo pipefail

[[ -z "${CLAUDE_DIR:-}" ]] && readonly CLAUDE_DIR="$HOME/.claude"
[[ -z "${LIB_DIR:-}" ]] && readonly LIB_DIR="$CLAUDE_DIR/lib"
[[ -z "${LEARNING_DETECTOR:-}" ]] && readonly LEARNING_DETECTOR="$LIB_DIR/learning-detector.py"
source "${CLAUDE_DIR}/lib/portable-sed.sh"

# ============================================================================
# LOGGING
# ============================================================================

# Internal logging function for learning events
_learning_log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    echo "[$timestamp] [$level] [LEARNING] $message" >&2
}

# ============================================================================
# CORE FUNCTIONS
# ============================================================================

# Check if learning detector is available
learning_check_deps() {
    if [[ ! -f "$LEARNING_DETECTOR" ]]; then
        return 1
    fi
    if ! command -v python3 &>/dev/null; then
        return 1
    fi
    return 0
}

# Quick learning detection (returns JSON)
detect_learning() {
    local message="$1"
    local novelty_score="${2:-0.0}"

    learning_check_deps || return 1

    python3 "$LEARNING_DETECTOR" detect "$message" --novelty "$novelty_score" 2>/dev/null
}

# Full learning processing pipeline (detect -> evaluate -> propose)
process_learning() {
    local message="$1"
    local novelty_score="${2:-0.0}"
    local project_path="${3:-$(pwd)}"

    learning_check_deps || return 1

    python3 "$LEARNING_DETECTOR" process "$message" \
        --novelty "$novelty_score" \
        --project "$project_path" 2>/dev/null
}

# List pending skill candidates
list_pending_skills() {
    learning_check_deps || return 1

    python3 "$LEARNING_DETECTOR" list 2>/dev/null
}

# Approve a skill candidate
approve_skill() {
    local candidate_id="$1"

    learning_check_deps || return 1

    python3 "$LEARNING_DETECTOR" approve "$candidate_id" 2>/dev/null

    # Regenerate always-loaded rules from approved insights
    python3 "$LEARNING_DETECTOR" generate-rules 2>/dev/null || true
}

# Reject a skill candidate
reject_skill() {
    local candidate_id="$1"
    local reason="${2:-}"

    learning_check_deps || return 1

    if [[ -n "$reason" ]]; then
        python3 "$LEARNING_DETECTOR" reject "$candidate_id" --reason "$reason" 2>/dev/null
    else
        python3 "$LEARNING_DETECTOR" reject "$candidate_id" 2>/dev/null
    fi
}

# Initialize learning directories
init_learning() {
    learning_check_deps || return 1

    python3 "$LEARNING_DETECTOR" init 2>/dev/null
}

# ============================================================================
# HOOK INTEGRATION
# ============================================================================

# Check for pending skills at session start
check_pending_learning() {
    learning_check_deps || return 0

    local result
    result=$(python3 "$LEARNING_DETECTOR" list 2>/dev/null) || return 0

    local count
    count=$(echo "$result" | jq -r '.count // 0' 2>/dev/null) || count=0

    if [[ "$count" -gt 0 ]]; then
        echo "[GRIP LEARNING] $count pending skill candidate(s) await approval"
        echo "Run 'python3 ~/.claude/lib/learning_detector.py list' to review"
        return 0
    fi
}

# Process message for learning during user prompt
# Gen 148: Added embedding_succeeded check to detect blind novelty scoring
monitor_learning() {
    local message="$1"
    local novelty_score="${2:-0.0}"
    local project_path="${3:-$(pwd)}"

    learning_check_deps || return 0

    # Only process if novelty is notable or message is substantial
    if (( $(echo "$novelty_score < 0.3" | bc -l 2>/dev/null || echo 0) )) && [[ ${#message} -lt 100 ]]; then
        return 0
    fi

    # Process through learning detector
    local result
    result=$(process_learning "$message" "$novelty_score" "$project_path" 2>/dev/null) || return 0

    # Gen 148: Check if embedding actually succeeded
    local embedding_succeeded
    embedding_succeeded=$(echo "$result" | jq -r '.embedding_succeeded // false' 2>/dev/null) || embedding_succeeded="false"
    if [[ "$embedding_succeeded" == "false" ]]; then
        _learning_log "WARN" "Embedding failed - novelty scoring blind for this message"
    fi

    # Gen 153: Check coherence gate results
    local coherence_passed
    coherence_passed=$(echo "$result" | jq -r '.coherence.coherence_passed // true' 2>/dev/null) || coherence_passed="true"
    if [[ "$coherence_passed" == "false" ]]; then
        local coherence_score
        coherence_score=$(echo "$result" | jq -r '.coherence.coherence_score // 0' 2>/dev/null) || coherence_score="0"
        _learning_log "INFO" "Coherence gate: text rejected (score=$coherence_score)"
    fi

    # Check if learning event was detected
    local is_learning
    is_learning=$(echo "$result" | jq -r '.learning_event.is_learning_event // false' 2>/dev/null) || is_learning="false"

    if [[ "$is_learning" == "true" ]]; then
        local action
        action=$(echo "$result" | jq -r '.action_taken // "none"' 2>/dev/null)

        case "$action" in
            skill_candidate_created)
                # Extract notification and output it
                local notification
                notification=$(echo "$result" | jq -r '.notification // empty' 2>/dev/null)
                if [[ -n "$notification" ]]; then
                    echo ""
                    echo "$notification"
                fi
                ;;
            flag_for_infrastructure)
                echo "[GRIP LEARNING] Infrastructure improvement detected - review recommended"
                ;;
            document_project)
                # Project-specific learning, no action needed
                ;;
        esac
    fi

    return 0
}

# ============================================================================
# SKILL GENERATION
# ============================================================================

# Quality validation for generated skill
validate_skill_quality() {
    local skill_file="$1"
    local score=0

    [[ ! -f "$skill_file" ]] && echo "0" && return 1

    local content
    content=$(cat "$skill_file")

    # Check frontmatter (30 points)
    if echo "$content" | rg -q '^---'; then
        score=$((score + 5))
    fi
    if echo "$content" | rg -q '^name:'; then
        score=$((score + 5))
    fi
    if echo "$content" | rg -q '^description:'; then
        score=$((score + 10))
    fi
    if echo "$content" | rg -q '^status: Active'; then
        score=$((score + 5))
    fi
    if echo "$content" | rg -q '^triggers:'; then
        score=$((score + 5))
    fi

    # Check body structure (40 points)
    local section_count
    section_count=$(echo "$content" | rg -c '^## ' || echo "0")
    if [[ "$section_count" -ge 3 ]]; then
        score=$((score + 20))
    fi

    # Check for examples (10 points)
    if echo "$content" | rg -qi 'example'; then
        score=$((score + 10))
    fi

    # Check no unfilled placeholders (10 points)
    if ! echo "$content" | rg -q '\{\{[A-Z_]+\}\}'; then
        score=$((score + 10))
    fi

    # Check line count < 500 (10 points)
    local line_count
    line_count=$(echo "$content" | wc -l | tr -d ' ')
    if [[ "$line_count" -lt 500 ]]; then
        score=$((score + 10))
    fi

    echo "$score"
}

# Register skill in SKILLS.md (markdown category map)
register_skill_in_docs() {
    local skill_name="$1"
    # $2 (command) and $3 (budget) are intentionally unused: the category map
    # only needs the skill id; those columns were dropped (R0 -- dead data that
    # nothing read). $4 is the description.
    local description="$4"

    local skills_file="$CLAUDE_DIR/docs/SKILLS.md"
    [[ ! -f "$skills_file" ]] && return 1

    # Already listed as a `- `name`` markdown item?
    if rg -q "^- \`${skill_name}\`" "$skills_file"; then
        _learning_log "INFO" "Skill already registered: $skill_name"
        return 0
    fi

    local entry="- \`${skill_name}\` — ${description}"

    # Insert under the autonomous-learning entry (improvement category). Match on
    # `.*autonomous-learning` so no literal backtick appears in the sed pattern.
    # Fall back to an Auto-registered section, then to EOF.
    if rg -q "^- .*autonomous-learning" "$skills_file"; then
        portable_sed_i "/^- .*autonomous-learning/a\\
${entry}
" "$skills_file" 2>/dev/null || printf '%s\n' "$entry" >> "$skills_file"
    else
        rg -q "^## auto-registered" "$skills_file" || printf '\n## auto-registered\n\n' >> "$skills_file"
        printf '%s\n' "$entry" >> "$skills_file"
    fi

    _learning_log "INFO" "Registered skill in SKILLS.md: $skill_name"
}

# Generate skill from approved candidate
generate_skill_from_candidate() {
    local candidate_id="$1"

    learning_check_deps || return 1

    # Get candidate data
    local candidate_file="$CLAUDE_DIR/learning/approved/$candidate_id.json"
    if [[ ! -f "$candidate_file" ]]; then
        echo "ERROR: Approved candidate not found: $candidate_id"
        return 1
    fi

    local candidate
    candidate=$(cat "$candidate_file")

    # Extract fields from candidate
    local skill_name description full_content learning_score triggers_json
    skill_name=$(echo "$candidate" | jq -r '.skill_name')
    description=$(echo "$candidate" | jq -r '.description')
    full_content=$(echo "$candidate" | jq -r '.full_content')
    learning_score=$(echo "$candidate" | jq -r '.learning_score')
    triggers_json=$(echo "$candidate" | jq -r '.triggers')

    # Derive trigger list
    local trigger_list=""
    if echo "$triggers_json" | jq -r '.teaching_moment' | rg -q 'true'; then
        trigger_list="${trigger_list}teaching moment, "
    fi
    if echo "$triggers_json" | jq -r '.new_term' | rg -q 'true'; then
        trigger_list="${trigger_list}new term coined, "
    fi
    if echo "$triggers_json" | jq -r '.generalisation' | rg -q 'true'; then
        trigger_list="${trigger_list}generalisation pattern, "
    fi
    trigger_list="${trigger_list%%, }"  # Remove trailing comma

    # Check template exists
    local template_file="$CLAUDE_DIR/templates/skills/SKILL.template.md"
    if [[ ! -f "$template_file" ]]; then
        echo "ERROR: Skill template not found: $template_file"
        return 1
    fi

    # Create skill directory
    local skill_dir="$CLAUDE_DIR/skills/$skill_name"
    mkdir -p "$skill_dir"

    # Get current date and GRIP generation
    local current_date
    current_date=$(date -u +%Y-%m-%d)
    local grip_gen="${GRIP_GEN:-0}"

    # Generate skill from template
    local skill_file="$skill_dir/SKILL.md"
    local template_content
    template_content=$(cat "$template_file")

    # Derive title (capitalize skill name)
    local title
    title=$(echo "$skill_name" | sed 's/-/ /g' | awk '{for(i=1;i<=NF;i++)$i=toupper(substr($i,1,1))tolower(substr($i,2))}1')

    # Replace placeholders
    template_content="${template_content//\{\{SKILL_NAME\}\}/$skill_name}"
    template_content="${template_content//\{\{TITLE\}\}/$title}"
    template_content="${template_content//\{\{DESCRIPTION\}\}/$description}"
    template_content="${template_content//\{\{DESCRIPTION_LONG\}\}/$full_content}"
    template_content="${template_content//\{\{DATE\}\}/$current_date}"
    template_content="${template_content//\{\{GEN\}\}/$grip_gen}"
    template_content="${template_content//\{\{PATTERN_NAME\}\}/learning-detection}"
    template_content="${template_content//\{\{TRIGGER_PHRASE\}\}/$trigger_list}"
    template_content="${template_content//\{\{TRIGGER_1\}\}/$trigger_list}"
    template_content="${template_content//\{\{TRIGGER_2\}\}/manual invocation}"
    template_content="${template_content//\{\{LEARNING_SCORE\}\}/$learning_score}"

    # Fill protocol section
    local protocol="Apply this learning when the trigger conditions are met."
    template_content="${template_content//\{\{PROTOCOL\}\}/$protocol}"

    # Fill example sections with generic content
    template_content="${template_content//\{\{EXAMPLE_BEFORE\}\}/# Before applying this pattern}"
    template_content="${template_content//\{\{EXAMPLE_AFTER\}\}/# After applying this pattern}"
    template_content="${template_content//\{\{VALIDATION_CHECK_1\}\}/Pattern applied correctly}"
    template_content="${template_content//\{\{VALIDATION_CHECK_2\}\}/No anti-pattern violations}"
    template_content="${template_content//\{\{TOKEN_LOAD\}\}/300}"
    template_content="${template_content//\{\{TOKEN_PER_USE\}\}/100}"

    # Write skill file
    echo "$template_content" > "$skill_file"

    # Validate quality
    local quality_score
    quality_score=$(validate_skill_quality "$skill_file")

    if [[ "$quality_score" -lt 70 ]]; then
        echo "WARNING: Skill quality score ($quality_score) below threshold (70)"
        echo "Skill created but may need manual refinement: $skill_file"
    else
        echo "Skill quality: $quality_score/100 (PASS)"
    fi

    # Register in SKILLS.md
    register_skill_in_docs "$skill_name" "-" "~300" "$description"

    # Log success
    _learning_log "INFO" "Generated skill: $skill_name (quality: $quality_score)"

    # Auto-log improvement for heatmap tracking
    if [[ -f "$CLAUDE_DIR/lib/improvement_analyzer.py" ]]; then
        python3 "$CLAUDE_DIR/lib/improvement_analyzer.py" log \
            skill_created \
            "skills/$skill_name" \
            "Auto-generated from learning detection (score: $learning_score)" \
            auto 2>/dev/null || true
    fi

    echo "SUCCESS: Skill generated at $skill_file"
    echo "Quality score: $quality_score/100"

    return 0
}

# ============================================================================
# EXPORTS
# ============================================================================

export -f learning_check_deps detect_learning process_learning
export -f list_pending_skills approve_skill reject_skill init_learning
export -f check_pending_learning monitor_learning generate_skill_from_candidate
