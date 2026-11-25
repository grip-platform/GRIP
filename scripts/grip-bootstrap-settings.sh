#!/usr/bin/env bash
#
# DEPRECATED: Settings are now configured by install.sh via settings.reference.json
# This script will be removed in GRIP v7.0
#
# Run 'bash scripts/install.sh --update' to refresh settings.

echo "[WARN] DEPRECATED: grip-bootstrap-settings.sh is superseded by install.sh"
echo "[WARN] Settings are now generated from config/settings.reference.json"
echo "[WARN] Run 'bash scripts/install.sh --update' to refresh settings"
echo ""

# Backward compatibility: CI/CD pipelines calling this script will be
# automatically redirected to install.sh --update. No action required.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" --update "$@"
