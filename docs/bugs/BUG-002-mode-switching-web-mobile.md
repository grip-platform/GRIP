# BUG-002: Mode Switching Unavailable on Web/Mobile

**Severity**: High
**Surface**: Claude Web, Claude Mobile (iOS/Android)
**Discovered**: 2026-02-09 (GRIP monetisation brainstorm session)
**Status**: Open

## Description

GRIP's mode system (`/mode code`, `/mode brainstorm`, etc.) cannot be activated on Claude Web or Mobile. The operator types "switch to code mode" or "enter code mode" and the AI acknowledges the request but has no mechanism to actually load the mode's skills, enforce its rules, or change interaction patterns.

The AI said "Code mode active" — which violates the anti-false-confidence rule — because it had no way to verify or enforce mode state.

## Root Cause

Mode switching depends on filesystem operations:

```bash
bash ~/.claude/lib/mode-loader.sh code
```

This shell script:

1. Reads mode YAML from `~/.claude/modes/definitions/{name}-mode.yaml`
2. Resolves skill dependencies
3. Writes active mode to `~/.claude/.active-modes`
4. Outputs skill list for context loading

On Claude Web/Mobile:

- No `~/.claude` directory exists
- No bash access to run `mode-loader.sh`
- No filesystem to persist `.active-modes`
- No mechanism to load mode YAML definitions
- Skills are only available if the MCP server exposes them

The MCP server exposes the 21 UI tools but does NOT expose mode management, skill loading, or mode state.

## Impact

- 25 modes are completely unavailable on Web/Mobile
- Managed GRIP clients on Web/Mobile get no mode benefits
- The "multi-platform" claim in pitch documents is partially false
- Brainstorm Mode worked in the 9 Feb session only because the AI happened to follow brainstorm-like patterns, not because mode rules were enforced
- Foundation PARAMOUNT skills (asking-users, context-gate, etc.) are not mechanically loaded

## Evidence

Operator asked to enter code mode. AI said "Code mode active." AI then admitted this was false — no mode was actually loaded. The AI was manually adhering to user preference rules, not operating under mode enforcement.

## Fix Plan

See `docs/bugs/FIX-PLAN-001-002.md`
