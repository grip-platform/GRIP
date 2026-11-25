#!/usr/bin/env bash
# openai-key.sh -- the one bash reader of the per-token OpenAI key. Source it.
#
# GPT runs on the ChatGPT subscription, never on the per-token key
# (rules/gpt-on-the-subscription-never-credits.md). A launcher that hands the
# key to a harness lets that harness bill OpenAI per token, so the read is
# gated on the same opt-in bin/grip-codex-ladder's paid rung uses.
#
#   grip_openai_api_key          prints the key and returns 0, only when
#                                GRIP_CODEX_ALLOW_PAID is exactly "1";
#                                otherwise prints nothing and returns 1.
#
# The caller passes a keychain reader so this file stays free of platform
# branches: grip_openai_api_key <reader>, where <reader> is a function or
# command taking the service name (bin/grip-opencode has _kv_get).

grip_openai_api_key() {
  local reader="${1:-}"
  if [[ "${GRIP_CODEX_ALLOW_PAID:-}" != "1" ]]; then
    return 1
  fi
  if [[ -z "$reader" ]]; then
    return 1
  fi
  "$reader" grip-openai
}
