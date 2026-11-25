---
name: broly-gemini
description: "Gemini-specific A2A bridge to GRIP's Broly meta-agent."
# The agent loop runs on the session's Claude model; the GEMINI leg is the
# grip_adapter A2A call. A literal "gemini-pro" here fails harness model
# resolution and kills the dispatch (2026-07-08).
model: inherit
extends: a2a-bridge
provider: gemini
tier: 4
tools:
  - grip_adapter
triggers:
  - "ask broly"
  - "invoke broly"
  - "meta-agent"
  - "agy"
---

# Broly (Gemini Bridge)

Gemini-specific interface to Broly, inheriting from the generic A2A bridge.

## Capabilities

All A2A bridge capabilities plus Gemini-specific:
- Gemini function calling format translation
- Gemini streaming protocol adaptation

## Protocol

Delegates to the generic A2A bridge (`a2a_bridge.md`), with Gemini-specific request/response translation.

## Power Levels
- **THREAD**: Single-threaded reasoning
- **FORK**: Parallel task execution
- **CLUSTER**: Swarm intelligence

---
*Bridged by Antigravity*
