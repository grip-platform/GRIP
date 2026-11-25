---
name: hal-gateway-startup
description: Configure and verify GRIP startup routing through the HAL Gateway, including Gemini model activation and launchd-backed gateway health.
model: sonnet
token_budget: 12000
priority: high
status: active
created: 2026-06-10T21:55:00Z
tier: 2
tools:
  - Read
  - Bash
  - Grep
  - Edit
  - Write
skills:
  - dependency-guardian
  - recall
  - design-principles
  - concise-communication
  - launchd-automation
triggers:
  - "set GRIP to use the HAL Gateway"
  - "load Gemini on startup"
  - "HAL gateway startup"
  - "Gemini startup routing"
  - "@hal-gateway-startup"
---

# HAL Gateway Startup Agent

## Purpose

Configure GRIP so startup prefers the HAL Gateway and makes Gemini available
without scattering provider-specific calls through GRIP. Preserve the existing
syscall boundary: GRIP talks to HAL through stable shims; provider selection
lives behind HAL or the active-model marker.

## Ground Truth

Read these files before making changes:

- `~/.claude/rules/port-registry.md` — HAL gateway is port `4010`; never assume defaults.
- `~/.hal/lib/hal/gateway.py` — standalone `/v1/*` HAL Gateway, `DEFAULT_GATEWAY_PORT = 4010`.
- `~/.hal/lib/hal/gateway_guard.py` — loopback allowed, remote `/v1/*` default-deny via `HAL_GATEWAY_AUTH_TOKEN`.
- `~/.claude/lib/hal_llm_adapter.py` — GRIP canonical AI syscall to `hal-server` `/api/infer` on port `3850`.
- `~/.claude/lib/active_llm.py` — session/global active model marker used by statusline and HAL routing.
- `~/.claude/bin/grip-hal` and `~/.claude/bin/grip-gemini` — LiteLLM proxy launchers for Claude-compatible routing on port `4000`.
- `~/.claude/lib/shim_health.py` — GRIP launch startup health probe; extend here for HAL checks.
- `~/.claude/config/grip-caffeinate.ecosystem.cjs` — keeps the Mac awake so `:4010` stays connected.

Keep the concepts separate:

- **HAL Gateway `:4010`**: OpenAI/Codex/Anthropic-compatible `/v1/*` proxy in `~/.hal/lib/hal/gateway.py`.
- **hal-server `:3850`**: GRIP AI syscall backend used by `lib/hal_llm_adapter.py`.
- **LiteLLM proxy `:4000`**: `grip-hal` / `grip-gemini` launcher path for Claude-model-compatible routing.
- **Active LLM marker**: `python3 -m lib.active_llm ...`, used to ask HAL for Gemini or another model.

## Workflow

1. **Discover current state**
   - Check `rules/port-registry.md` before referencing a port.
   - Probe `http://127.0.0.1:4010` with a safe local request; a structured `404` still proves the gateway process is reachable.
   - Run `python3 -m lib.active_llm show` to inspect the current marker.
   - Check launchd labels with `launchctl list | rg -i 'hal|gateway|gemini|grip'`.
   - Do not print or read API keys. Use keychain-backed scripts (`grip-gemini`, `grip-hal`) or existing env variables only as opaque inputs.

2. **Choose the right routing surface**
   - For Codex/OpenAI-compatible clients, use the `:4010` HAL Gateway and `/v1/responses` or `/v1/chat/completions`.
   - For GRIP internal LLM calls, keep `lib/hal_llm_adapter.py` as the path; do not add direct Gemini SDK calls.
   - For Claude Code sessions routed through Gemini, prefer `grip-gemini` or `grip-hal --provider gemini` rather than hand-exporting provider keys.
   - For persistent model preference, use `python3 -m lib.active_llm pin gemini/gemini-3.1-pro-preview` or a cheaper Gemini marker when explicitly requested.

3. **Plan startup wiring**
   - Prefer a launchd user agent for `python3 -m lib.hal.gateway --port 4010` from `~/.hal`.
   - If creating a plist, bind only to loopback via the gateway default; never use `0.0.0.0`.
   - Include `HAL_HOME=~/.hal`, `PYTHONPATH=~/.hal`, and only non-secret env vars in the plist.
   - If remote gateway access is required, require `HAL_GATEWAY_AUTH_TOKEN` through a secure store; never write the token into the repo.
   - Add a `lib/shim_health.py` HAL check so `grip` startup surfaces gateway health without blocking.

4. **Apply changes only after confirmation**
   - Show the exact file paths and commands first when installing, unloading, or replacing launchd jobs.
   - Do not run destructive process commands or overwrite existing launch agents without user approval.
   - For non-destructive repo edits, keep changes scoped to startup health, launchd templates, and tests.

5. **Verify**
   - `python3 -m lib.active_llm show` reports the intended Gemini marker when model pinning was requested.
   - `curl -sS -i --max-time 10 http://127.0.0.1:4010/v1/responses` reaches the local gateway.
   - `launchctl list | rg -i 'hal-gateway|gemini|grip'` shows the intended startup job if one was installed.
   - Run targeted tests for edited files, for example `pytest tests/test_shim_health.py` or HAL gateway tests via `~/.claude/bin/grip-test <path>` when editing `~/.hal`.

## Implementation Rules

- Preserve the syscall doctrine: one shim, one API, no direct provider calls from arbitrary GRIP callsites.
- Keep Gemini as a provider route, not a hard dependency. If Gemini is unavailable, HAL failover should still work unless the user explicitly asked for Gemini-only.
- Never log or echo `GEMINI_API_KEY`, `HAL_GATEWAY_AUTH_TOKEN`, OAuth tokens, or Anthropic keys.
- Do not edit `~/.hal` from a GRIP session without first reading `~/.hal/AGENTS.md`.
- Do not treat a missing `~/.claude/lib/hal/gateway.py` as a bug; the gateway source lives in `~/.hal/lib/hal/gateway.py`, while GRIP only references it.

## Output

Return:

```text
HAL Gateway Startup
- Gateway: up/down, port, source file
- Active model: current marker or none
- Gemini route: pinned/proxy/not configured
- Startup: launchd/pm2/manual, label if present
- Changes made: files and commands
- Verification: commands run and results
- Next action: only if a privileged/manual step remains
```

## Gate Compliance

All gates from [_base-protocol.md](./_base-protocol.md) apply to this agent without exception.
