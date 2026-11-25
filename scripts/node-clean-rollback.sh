#!/usr/bin/env bash

##############################################################################
# Node Process Rollback Script
#
# Emergency restart utility for processes killed by node-clean.sh
# Reads checkpoint JSON and offers to restart each terminated process
##############################################################################

set -euo pipefail

# Show usage
show_usage() {
  cat <<EOF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Node Process Rollback - Emergency Restart Utility
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage: node-clean-rollback.sh <checkpoint-file>

DESCRIPTION:
  Reads a checkpoint created by node-clean.sh and offers to restart
  each process that was killed.

EXAMPLES:
  node-clean-rollback.sh ~/.claude/checkpoints/node-clean-1736952000.json

OPTIONS:
  --list-checkpoints    Show all available checkpoints
  --help, -h           Show this help

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
}

# List available checkpoints
list_checkpoints() {
  local checkpoint_dir="$HOME/.claude/checkpoints"

  if [[ ! -d "$checkpoint_dir" ]]; then
    echo "❌ No checkpoints directory found: $checkpoint_dir"
    exit 1
  fi

  local checkpoints=$(find "$checkpoint_dir" -name "node-clean-*.json" -type f | sort -r)

  if [[ -z "$checkpoints" ]]; then
    echo "ℹ️  No checkpoints found."
    exit 0
  fi

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Available Checkpoints:"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  while IFS= read -r checkpoint; do
    local timestamp=$(jq -r '.timestamp' "$checkpoint" 2>/dev/null || echo "Unknown")
    local count=$(jq '.processes | length' "$checkpoint" 2>/dev/null || echo "?")
    echo "📁 $checkpoint"
    echo "   Time: $timestamp"
    echo "   Processes: $count"
    echo ""
  done <<< "$checkpoints"
}

# Infer restart command from process info
infer_restart_command() {
  local cwd=$1
  local command=$2

  # Try to extract common patterns
  if echo "$command" | grep -q "next dev"; then
    echo "cd '$cwd' && npm run dev"
  elif echo "$command" | grep -q "nodemon"; then
    echo "cd '$cwd' && npm run dev"
  elif echo "$command" | grep -q "vite"; then
    echo "cd '$cwd' && npm run dev"
  elif echo "$command" | grep -q "react-scripts"; then
    echo "cd '$cwd' && npm start"
  else
    # Generic node command
    echo "cd '$cwd' && node $(basename $command)"
  fi
}

# Restart a process
restart_process() {
  local process=$1

  local pid=$(echo "$process" | jq -r '.pid')
  local name=$(echo "$process" | jq -r '.name')
  local command=$(echo "$process" | jq -r '.command')
  local cwd=$(echo "$process" | jq -r '.cwd')
  local port=$(echo "$process" | jq -r '.port')

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "PROCESS RESTART"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Original PID: $pid"
  echo "Name:         $name"
  echo "Command:      $command"
  echo "Path:         $cwd"
  echo "Port:         ${port:-N/A}"
  echo ""

  # Check if port is still in use
  if [[ "$port" != "null" ]] && lsof -i ":$port" > /dev/null 2>&1; then
    echo "⚠️  WARNING: Port $port is already in use!"
    echo ""
    lsof -i ":$port"
    echo ""
    read -r -p "Continue anyway? [y/N] " port_override
    if [[ ! "$port_override" =~ ^[Yy]$ ]]; then
      echo "⏭️  Skipped"
      return 1
    fi
  fi

  # Infer restart command
  local restart_cmd=$(infer_restart_command "$cwd" "$command")

  echo "Suggested restart command:"
  echo "  $restart_cmd"
  echo ""
  echo "Options:"
  echo "  [1] Run suggested command in background"
  echo "  [2] Edit command and run"
  echo "  [N] Skip this process"
  echo ""
  read -r -p "Choice: " choice

  case "$choice" in
    1)
      echo "🚀 Starting process in background..."
      eval "$restart_cmd" > /dev/null 2>&1 &
      local new_pid=$!
      echo "✅ Process started with PID: $new_pid"
      sleep 2
      if ps -p "$new_pid" > /dev/null 2>&1; then
        echo "✅ Process is running"
      else
        echo "❌ Process failed to start (check logs)"
      fi
      ;;
    2)
      echo ""
      echo "Enter custom restart command:"
      read -r custom_cmd
      echo "🚀 Running: $custom_cmd"
      eval "$custom_cmd" > /dev/null 2>&1 &
      local new_pid=$!
      echo "✅ Process started with PID: $new_pid"
      ;;
    *)
      echo "⏭️  Skipped"
      ;;
  esac
}

# Main execution
main() {
  if [[ $# -eq 0 ]]; then
    show_usage
    exit 1
  fi

  case "$1" in
    --list-checkpoints)
      list_checkpoints
      exit 0
      ;;
    --help|-h)
      show_usage
      exit 0
      ;;
  esac

  local checkpoint_file=$1

  if [[ ! -f "$checkpoint_file" ]]; then
    echo "❌ Checkpoint file not found: $checkpoint_file"
    exit 1
  fi

  # Validate JSON
  if ! jq empty "$checkpoint_file" 2>/dev/null; then
    echo "❌ Invalid checkpoint file (not valid JSON)"
    exit 1
  fi

  # Read checkpoint
  local timestamp=$(jq -r '.timestamp' "$checkpoint_file")
  local processes=$(jq '.processes' "$checkpoint_file")
  local count=$(echo "$processes" | jq 'length')

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔄 NODE PROCESS ROLLBACK"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Checkpoint: $checkpoint_file"
  echo "Timestamp:  $timestamp"
  echo "Processes:  $count"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [[ $count -eq 0 ]]; then
    echo "ℹ️  No processes to restart."
    exit 0
  fi

  # Iterate through processes
  local index=0
  local restarted=0
  local skipped=0

  while [[ $index -lt $count ]]; do
    local process=$(echo "$processes" | jq ".[$index]")

    restart_process "$process"
    local result=$?

    if [[ $result -eq 0 ]]; then
      restarted=$((restarted + 1))
    else
      skipped=$((skipped + 1))
    fi

    index=$((index + 1))
  done

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📊 ROLLBACK SUMMARY"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "✅ Restarted: $restarted"
  echo "⏭️  Skipped:   $skipped"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "💡 Tip: Use 'ps aux | grep node' to verify running processes"
  echo ""
}

main "$@"
