---
name: incognito-mode-orchestration
description: Routes private conversations with no persistence, no external calls, and minimal logging. Use when /mode incognito is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode incognito
  - incognito mode active
integrates:
  - asking-users
---

# Incognito Mode Orchestration

Routes private conversations through strict privacy protocols. No persistence, no external calls, minimal footprint.

## Protocols

### 1. No Persistence (Non-Negotiable)

| Action | Status |
|--------|--------|
| Run instance-serializer.py | BLOCKED |
| Update instance next_up field | BLOCKED |
| Create resurrection files | BLOCKED |
| Save session state | BLOCKED |
| Update SESSION.md | BLOCKED |

### 2. No External Calls (Non-Negotiable)

| Service | Status |
|---------|--------|
| MCP servers (Notion, GitHub, Zapier) | BLOCKED |
| WebFetch | BLOCKED (unless user explicitly requests) |
| WebSearch | BLOCKED (unless user explicitly requests) |

### 3. Minimal Logging

- Avoid triggering hooks where possible
- Don't reference conversation specifics in outputs
- Behave as if this conversation will leave no trace

### 4. On Mode Exit

- Remind user: "Incognito session ended. Nothing persisted."
- Do NOT offer to save state

### 5. Technical Limitations (Transparent)

| Limitation | Reason |
|------------|--------|
| API requests reach Anthropic | Unavoidable |
| Terminal history may retain commands | User responsibility |
| System hooks still execute | GRIP cannot override settings.json |

## Workflows

### Quick (all incognito operations)

1. Receive request
2. Process locally with privacy protocols
3. Output directly
4. No persistence, no state updates

**Broly level**: N/A (Broly disabled)

### Standard

Same as Quick. Incognito mode maintains minimal footprint at all times.

### Deep

Same as Quick. No escalation available in incognito mode.

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- User request requires external call (confirm explicit authorisation)
- User request would create persistent artifacts
- Ambiguity about what constitutes "private"

## Output Style

- Direct and minimal
- British English throughout
- No emoji
- No references to external state or history

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for incognito mode |
