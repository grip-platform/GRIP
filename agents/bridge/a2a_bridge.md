---
name: a2a-bridge
description: "Generic A2A bridge for cross-provider agent delegation to GRIP."
protocol: a2a
tier: 4
tools:
  - grip_adapter
capabilities:
  - task_delegation
  - converge_execution
  - event_streaming
supported_providers:
  - gemini
  - codex
  - copilot
  - generic
---

# A2A Bridge Agent

Generic bridge enabling external AI agents to delegate tasks to GRIP via the A2A protocol.

## Protocol

When an external agent sends a task via A2A:
1. Authenticate the request (if security schemes are configured)
2. Detect the calling provider from request metadata
3. Map the task to the appropriate GRIP capability (converge, review, etc.)
4. Execute via `lib/a2a_adapter.py`
5. Stream progress events back
6. Return final result in A2A response format

## Provider Detection

The bridge auto-detects the calling provider from:
- `User-Agent` header
- `metadata.provider` in the A2A task
- Known API patterns

## Capability Mapping

| External Request | GRIP Capability |
|-----------------|-----------------|
| "review this code" | Code review (Ralph Wiggum loop) |
| "improve quality" | converge() with quality criterion |
| "run tests" | Test execution + Goodhart guard |
| "analyse security" | STRIDE/OWASP/DREAD analysis |
| generic task | converge() with custom criterion |

## Configuration

Provider-specific configs in `agents/bridge/`:
- `broly_gemini.md` — Gemini-specific overrides
- `broly_codex.md` — OpenAI Codex (future)
- `broly_copilot.md` — GitHub Copilot (future)
