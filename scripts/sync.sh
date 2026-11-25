#!/usr/bin/env bash
#
# DEPRECATED: Use 'bash scripts/install.sh --update' instead
# This script will be removed in GRIP v7.0
#
# For backward compatibility, this redirects to install.sh --update.

echo "[WARN] DEPRECATED: sync.sh is superseded by install.sh --update"
echo "[WARN] This script will be removed in GRIP v7.0"
echo ""

# Backward compatibility: CI/CD pipelines calling this script will be
# automatically redirected to install.sh --update. No action required.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" --update "$@"
