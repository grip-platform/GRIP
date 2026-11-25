#!/usr/bin/env bash
# GRIP Setup - macOS/Linux launcher
# Usage: ./grip-setup.sh [--check|--fix] [--platform windows|macos|linux]
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/grip-setup.py" "$@"
