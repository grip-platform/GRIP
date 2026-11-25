#!/usr/bin/env bash
# verify-litellm-fallback.sh — read-only check for Bug B fix
#
# Prints OK if ~/.litellm/config.yaml has the Opus → Sonnet descent
# fallback edge, MISSING otherwise. Exit 0 = present, 1 = absent,
# 2 = config file missing.
#
# Reference: docs/litellm-fallback-bug-b.md (Hypothesis H369)

set -euo pipefail

CONFIG="${LITELLM_CONFIG:-$HOME/.litellm/config.yaml}"

if [[ ! -f "$CONFIG" ]]; then
  echo "MISSING: $CONFIG does not exist"
  exit 2
fi

# Look for the descent edge: claude-opus-4-7 → claude-sonnet-4-6.
# Match the YAML line shape exactly to avoid false positives in
# unrelated comments or model_list entries.
if rg -q '"claude-opus-4-7"\s*:\s*\[\s*"claude-sonnet-4-6"\s*\]' "$CONFIG"; then
  echo "OK: descent fallback (claude-opus-4-7 -> claude-sonnet-4-6) is configured in $CONFIG"
  exit 0
fi

echo "MISSING: descent fallback (claude-opus-4-7 -> claude-sonnet-4-6) is NOT configured in $CONFIG"
echo "Apply the patch from docs/litellm-fallback-bug-b.md"
exit 1
