#!/usr/bin/env bash
# rsi-pipeline.sh — Intelligent RSI pipeline using /converge and /rsi patterns.
#
# Unlike a naive "for issue; do fix; done" loop, this pipeline:
# 1. Detects task type from issue labels/title for proper /converge scoring
# 2. Crafts quality criteria using the De Morgan duality model
# 3. Checkpoints state between issues via /save
# 4. Respects Fibonacci-Wave-Breathing for pacing (batch sizes 1,1,2,3,5)
# 5. Records converge metrics for compound analysis
# 6. Runs /preflight before starting and between waves
#
# Usage:
#   ./scripts/rsi-pipeline.sh                          # Default: 5 issues
#   ./scripts/rsi-pipeline.sh --limit 10               # Process up to 10
#   ./scripts/rsi-pipeline.sh --dry-run                # Preview without executing
#   ./scripts/rsi-pipeline.sh --wave W3                # Start at wave W3
#   ./scripts/rsi-pipeline.sh --label rsi              # Different label filter
#   ./scripts/rsi-pipeline.sh --max-depth 5            # Override converge depth
set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# grip_save.py signs an IDR decision record. A bare `python3` resolves to an
# interpreter without `cryptography`, which does not fail -- it writes a
# forgeable demo-sealed record. Resolve the venv once, here, via the shared
# helper the other 25 GRIP commands already use.
source "$(dirname "${BASH_SOURCE[0]}")/lib/venv-utils.sh"
if ! GRIP_PY="$(resolve_venv_python "${CLAUDE_DIR}/venv")"; then
  echo "[rsi-pipeline] no Python interpreter found (searched ${CLAUDE_DIR}/venv, then PATH)" >&2
  exit 1
fi

LOG_FILE="${CLAUDE_DIR}/learning/rsi-pipeline-runs.jsonl"
STATE_FILE="${CLAUDE_DIR}/.rsi-pipeline-state.json"
DEFAULT_LIMIT=8
DEFAULT_LABEL="sprint-loop"
MAX_TURNS=30
ALLOWED_TOOLS="Edit,Read,Bash,Write,Grep,Glob,Agent"

# Fibonacci wave batch sizes (W1-W8 ascent, then W8-W1 descent)
FIBONACCI_WAVES=(1 1 2 3 5 8 5 3 2 1 1)

# --- Argument parsing ---
DRY_RUN=false
LIMIT="$DEFAULT_LIMIT"
LABEL="$DEFAULT_LABEL"
START_WAVE=1
MAX_DEPTH=3

usage() {
  cat <<USAGE
Usage: $(basename "$0") [OPTIONS]

Intelligent RSI pipeline using /converge and /rsi patterns.
Each issue gets a crafted prompt with task-type detection, quality
criteria, and De Morgan duality validation.

Options:
  --dry-run           Preview issues and prompts without executing
  --limit N           Max issues to process (default: $DEFAULT_LIMIT)
  --label LABEL       GitHub issue label filter (default: $DEFAULT_LABEL)
  --wave WN           Start at wave N (default: 1)
  --max-depth N       Override /converge max depth (default: 3)
  --resume            Resume from last checkpoint
  -h, --help          Show this help
USAGE
  exit 0
}

RESUME=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)     DRY_RUN=true; shift ;;
    --limit)       LIMIT="$2"; shift 2 ;;
    --label)       LABEL="$2"; shift 2 ;;
    --wave)        START_WAVE="${2#W}"; shift 2 ;;  # Accept W3 or 3
    --max-depth)   MAX_DEPTH="$2"; shift 2 ;;
    --resume)      RESUME=true; shift ;;
    -h|--help)     usage ;;
    *)             echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# --- Preflight checks ---
for cmd in claude jq gh python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd not found" >&2; exit 1; }
done

echo "=== RSI Pipeline (Fibonacci-Wave-Converge) ==="
echo "Label: $LABEL | Limit: $LIMIT | Max depth: $MAX_DEPTH | Start wave: W$START_WAVE"
echo ""

# --- Run credential validator ---
echo "[preflight] Running credential check..."
python3 "${CLAUDE_DIR}/lib/credential_validator.py" --quick 2>/dev/null || true
echo ""

# --- Task type detection from issue title/labels ---
detect_task_type() {
  local title="$1"
  local labels="$2"
  local title_lower
  title_lower=$(echo "$title" | tr '[:upper:]' '[:lower:]')

  # Check labels first (most reliable signal)
  if echo "$labels" | grep -qE 'bug|fix|hotfix'; then echo "code"; return; fi
  if echo "$labels" | grep -qE 'docs|documentation'; then echo "explanation"; return; fi
  if echo "$labels" | grep -qE 'design|architect'; then echo "design"; return; fi
  if echo "$labels" | grep -qE 'security|audit'; then echo "analysis"; return; fi

  # Fall back to title keyword detection (matches /converge task type table)
  if echo "$title_lower" | grep -qE 'fix|refactor|test|implement|build|add|create'; then
    echo "code"; return
  fi
  if echo "$title_lower" | grep -qE 'explain|document|describe|clarify'; then
    echo "explanation"; return
  fi
  if echo "$title_lower" | grep -qE 'design|architect|schema|plan|structure'; then
    echo "design"; return
  fi
  if echo "$title_lower" | grep -qE 'audit|investigate|review|analyze|inspect'; then
    echo "analysis"; return
  fi

  echo "code"  # Default for sprint-loop issues
}

# --- Build converge-aware prompt for each issue ---
build_converge_prompt() {
  local number="$1"
  local title="$2"
  local body="$3"
  local task_type="$4"
  local max_depth="$5"
  local wave="$6"

  # Map task type to converge dimensions and criteria
  local dimensions criterion
  case "$task_type" in
    code)
      dimensions="correctness (40%), design (30%), simplicity (30%)"
      criterion="correctness >= 90 AND design >= 80 AND simplicity >= 80 AND tests added/updated"
      ;;
    explanation)
      dimensions="clarity (40%), accuracy (30%), brevity (30%)"
      criterion="clarity >= 90 AND accuracy >= 90 AND brevity >= 80"
      ;;
    design)
      dimensions="completeness (40%), coherence (30%), elegance (30%)"
      criterion="completeness >= 85 AND coherence >= 85 AND elegance >= 80"
      ;;
    analysis)
      dimensions="evidence (40%), insight (30%), actionability (30%)"
      criterion="evidence >= 85 AND insight >= 80 AND actionability >= 85"
      ;;
    *)
      dimensions="quality (40%), completeness (30%), conciseness (30%)"
      criterion="quality >= 85 AND completeness >= 85"
      ;;
  esac

  cat <<PROMPT
You are working in the GRIP repository at ${CLAUDE_DIR}.
This is RSI Wave W${wave}, processing issue #${number}.

## Task

/converge ${max_depth}: ${title}

## Issue #${number}: ${title}

${body}

## Converge Protocol (MANDATORY)

Task type: ${task_type}
Scoring dimensions: ${dimensions}
Criterion: ${criterion}
Max depth: ${max_depth}

Execute the /converge loop:
1. Depth 0: Implement the fix/feature. Score each dimension independently.
2. Depth 1+: Identify weaknesses, improve targeted areas. Re-score.
3. Stop when criterion is met or max_depth reached.

## Quality Gates (De Morgan Duality)

After implementation, validate using BOTH directions:
- Guardrail check: Run tests, check for DRY/KISS/BIG-O/YAGNI violations
- Converge check: Score all dimensions >= threshold
- De Morgan equivalence: NOT(ANY(violation)) must equal ALL(quality_met)

If the quality hook reports violations, convert them to dimension scores:
- DRY violation: design -15
- KISS violation: simplicity -15
- BIG-O violation: simplicity -10
- YAGNI violation: design -10

## Requirements

1. Implement the fix or feature described in the issue
2. Add or update tests (MANDATORY — RSI Rule 13)
3. Tests must verify actual output values, not just call counts (Goodhart protection)
4. Keep changes minimal and focused on the issue scope
5. Commit with conventional commit message referencing #${number}
6. Create a PR via: git checkout -b feat/issue-${number}, push, gh pr create
7. Record converge metrics: python3 lib/converge_metrics.py (dimension_scores MANDATORY)

## After Completion

Output a structured summary:
- Issue: #${number}
- Task type: ${task_type}
- Final scores: {dimension: score} for each dimension
- Converged at depth: N/max_depth
- PR: URL or "committed to branch"
- Tests: N added/updated
PROMPT
}

# --- Checkpoint state ---
save_checkpoint() {
  local wave="$1"
  local completed="$2"
  local total="$3"
  jq -nc \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson wave "$wave" \
    --argjson completed "$completed" \
    --argjson total "$total" \
    '{timestamp: $ts, wave: $wave, completed: $completed, total: $total}' \
    > "$STATE_FILE"
}

# --- Resume from checkpoint ---
if [[ "$RESUME" == "true" ]] && [[ -f "$STATE_FILE" ]]; then
  RESUME_WAVE=$(jq -r '.wave' "$STATE_FILE")
  RESUME_COMPLETED=$(jq -r '.completed' "$STATE_FILE")
  echo "[resume] Resuming from W${RESUME_WAVE}, ${RESUME_COMPLETED} completed"
  START_WAVE="$RESUME_WAVE"
fi

# --- Fetch issues ---
echo "[fetch] Fetching issues with label '$LABEL' (limit: $LIMIT)..."
ISSUES_JSON=$(gh issue list \
  --repo grip-platform/GRIP \
  --label "$LABEL" \
  --state open \
  --limit "$LIMIT" \
  --json number,title,body,labels \
  2>/dev/null) || {
  echo "ERROR: Failed to fetch issues from GitHub" >&2
  exit 1
}

ISSUE_COUNT=$(echo "$ISSUES_JSON" | jq 'length')

if [[ "$ISSUE_COUNT" -eq 0 ]]; then
  echo "No open issues found with label '$LABEL'."
  exit 0
fi

echo "[fetch] Found $ISSUE_COUNT issue(s) to process."
echo ""

mkdir -p "$(dirname "$LOG_FILE")"

PASS=0 FAIL=0 SKIPPED=0 ISSUE_IDX=0
RESULTS=()

# --- Fibonacci Wave Loop ---
WAVE_IDX=$((START_WAVE - 1))
TOTAL_WAVES=${#FIBONACCI_WAVES[@]}

while [[ "$ISSUE_IDX" -lt "$ISSUE_COUNT" ]] && [[ "$WAVE_IDX" -lt "$TOTAL_WAVES" ]]; do
  WAVE_NUM=$((WAVE_IDX + 1))
  BATCH_SIZE=${FIBONACCI_WAVES[$WAVE_IDX]}

  # Cap batch to remaining issues
  REMAINING=$((ISSUE_COUNT - ISSUE_IDX))
  if [[ "$BATCH_SIZE" -gt "$REMAINING" ]]; then
    BATCH_SIZE="$REMAINING"
  fi

  echo "=== Wave W${WAVE_NUM} (batch: ${BATCH_SIZE}) ==="

  for b in $(seq 0 $((BATCH_SIZE - 1))); do
    IDX=$((ISSUE_IDX + b))
    NUMBER=$(echo "$ISSUES_JSON" | jq -r ".[$IDX].number")
    TITLE=$(echo "$ISSUES_JSON" | jq -r ".[$IDX].title")
    BODY=$(echo "$ISSUES_JSON" | jq -r ".[$IDX].body")
    LABELS=$(echo "$ISSUES_JSON" | jq -r ".[$IDX].labels | map(.name) | join(\",\")")

    TASK_TYPE=$(detect_task_type "$TITLE" "$LABELS")

    echo "  [W${WAVE_NUM}.$((b + 1))] #$NUMBER: $TITLE (type: $TASK_TYPE)"

    if [[ "$DRY_RUN" == "true" ]]; then
      echo "    [DRY-RUN] Task type: $TASK_TYPE | Depth: $MAX_DEPTH | Turns: $MAX_TURNS"
      PROMPT=$(build_converge_prompt "$NUMBER" "$TITLE" "$BODY" "$TASK_TYPE" "$MAX_DEPTH" "$WAVE_NUM")
      echo "    [DRY-RUN] Prompt preview (first 3 lines):"
      echo "$PROMPT" | head -3 | sed 's/^/    /'
      SKIPPED=$((SKIPPED + 1))
      RESULTS+=("SKIP #$NUMBER $TITLE ($TASK_TYPE)")
      continue
    fi

    PROMPT=$(build_converge_prompt "$NUMBER" "$TITLE" "$BODY" "$TASK_TYPE" "$MAX_DEPTH" "$WAVE_NUM")

    START_TS=$(date +%s)
    EXIT_CODE=0

    claude -p "$PROMPT" \
      --max-turns "$MAX_TURNS" \
      --allowedTools "$ALLOWED_TOOLS" \
      2>/dev/null || EXIT_CODE=$?

    END_TS=$(date +%s)
    DURATION=$((END_TS - START_TS))

    # Log result with converge metadata
    jq -nc \
      --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson issue "$NUMBER" \
      --arg title "$TITLE" \
      --arg task_type "$TASK_TYPE" \
      --argjson exit_code "$EXIT_CODE" \
      --argjson duration "$DURATION" \
      --argjson wave "$WAVE_NUM" \
      --argjson max_depth "$MAX_DEPTH" \
      --arg label "$LABEL" \
      '{timestamp: $ts, issue: $issue, title: $title, task_type: $task_type, exit_code: $exit_code, duration_seconds: $duration, wave: $wave, max_depth: $max_depth, label: $label, mode: "converge"}' \
      >> "$LOG_FILE"

    if [[ "$EXIT_CODE" -eq 0 ]]; then
      echo "    PASS (${DURATION}s, $TASK_TYPE)"
      PASS=$((PASS + 1))
      RESULTS+=("PASS #$NUMBER $TITLE ($TASK_TYPE, ${DURATION}s)")
    else
      echo "    FAIL exit=$EXIT_CODE (${DURATION}s)"
      FAIL=$((FAIL + 1))
      RESULTS+=("FAIL #$NUMBER $TITLE (exit=$EXIT_CODE, ${DURATION}s)")
    fi
  done

  ISSUE_IDX=$((ISSUE_IDX + BATCH_SIZE))

  # --- Breathing Pause Between Waves ---
  if [[ "$DRY_RUN" != "true" ]] && [[ "$ISSUE_IDX" -lt "$ISSUE_COUNT" ]]; then
    echo ""
    echo "  [breathing] Wave W${WAVE_NUM} complete. Checkpointing..."
    save_checkpoint "$WAVE_NUM" "$ISSUE_IDX" "$ISSUE_COUNT"

    # Run /save for state persistence
    "$GRIP_PY" "${CLAUDE_DIR}/lib/grip_save.py" \
      "RSI pipeline W${WAVE_NUM}: ${PASS} pass, ${FAIL} fail" \
      --quiet 2>/dev/null || true

    echo "  [breathing] Checkpoint saved. Continuing to W$((WAVE_NUM + 1))..."
    echo ""
  fi

  WAVE_IDX=$((WAVE_IDX + 1))
done

# --- Summary ---
echo ""
echo "=== RSI Pipeline Summary ==="
if [[ "$DRY_RUN" == "true" ]]; then
  echo "Mode: DRY-RUN (no executions)"
fi
echo "Protocol: Fibonacci-Wave-Converge (De Morgan quality duality)"
echo "Waves: W${START_WAVE} to W${WAVE_IDX}"
echo "Total: $ISSUE_COUNT | Pass: $PASS | Fail: $FAIL | Skipped: $SKIPPED"
echo ""
for r in "${RESULTS[@]}"; do echo "  $r"; done

if [[ "$DRY_RUN" != "true" ]]; then
  echo ""
  echo "Log: $LOG_FILE"

  # Final save
  "$GRIP_PY" "${CLAUDE_DIR}/lib/grip_save.py" \
    "RSI pipeline complete: ${PASS}/${ISSUE_COUNT} passed" \
    --quiet 2>/dev/null || true

  # Clean up checkpoint
  rm -f "$STATE_FILE"

  # Suggest converge compound analysis
  echo ""
  echo "Run compound analysis: python3 ${CLAUDE_DIR}/lib/converge_metrics.py compound"
fi
