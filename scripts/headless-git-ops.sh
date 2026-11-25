#!/usr/bin/env bash
# Headless workflow execution via Claude Code
# Usage: headless-git-ops.sh <workflow> [--repo <path>]
set -euo pipefail

WORKFLOW="${1:?Usage: headless-git-ops.sh <workflow> [--repo <path>]}"
REPO="${3:-$(pwd)}"

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
HISTORY_FILE="$CLAUDE_DIR/data/aide/history.jsonl"

echo "[headless] Running workflow: $WORKFLOW on $REPO"

claude -p "Run the workflow '${WORKFLOW}' on repo at ${REPO}. \
Use /mode git-ops. Execute all steps, log results to data/aide/history.jsonl. \
If any step requires human decision, output ESCALATE: <reason> and exit 1." \
  --allowedTools "Read,Bash,Grep,Glob"

echo "[headless] Workflow complete: $WORKFLOW"
