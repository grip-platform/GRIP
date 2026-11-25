# Permission Bypass Assessment

Security audit of `--dangerously-skip-permissions` usage in `~/.claude/bin/grip`.

## Architectural Decision: KEEP ALL BYPASS PATHS

GRIP's security model relies on PreToolUse gates (context-gate-hook.py) as the enforcement
layer, NOT Claude Code's built-in permission prompts. The bypass flag enables full autonomy
while gates provide deterministic, mechanical enforcement that cannot be overridden.

**Rationale**: `--dangerously-skip-permissions` removes Claude Code's interactive permission
prompts. GRIP replaces these with gate-based enforcement that:
- Runs before every tool call (PreToolUse hook)
- Returns `permissionDecision: "deny"` to mechanically block operations
- Cannot be argued around or socially engineered
- Covers: dependency reads, destructive git, secrets, env vars, database dumps, network binding, SSH, deployment, code writes, context limits

This is a deliberate architectural choice: gates are superior to interactive prompts for
autonomous operation.

## Path Analysis

### 1. Line 373: `cmd_bootstrap_partial` (Upgrade wizard)

```bash
exec claude --dangerously-skip-permissions
```

**Context**: Called during GRIP upgrade when partial infrastructure exists.
**Verdict**: KEEP - Gates enforce during bootstrap. Wizard uses AskUserQuestion for user consent.

### 2. Line 389: `cmd_onboard` (Onboarding with system prompt)

```bash
exec claude --dangerously-skip-permissions --system-prompt "$(cat "$system_prompt")"
```

**Context**: First-time onboarding with custom system prompt.
**Verdict**: KEEP - System prompt path now validated via `validate_prompt_path()` (symlink protection added in Phase 2). Gates enforce during onboarding.

### 3. Line 445: `cmd_resume` (Session resume)

```bash
exec claude --dangerously-skip-permissions --resume "$session_uuid"
```

**Context**: Resuming a previous session.
**Verdict**: KEEP - Session resume needs seamless continuation. Gates enforce regardless.

### 4. Line 489: `cmd_fresh` (Fresh session fallback)

```bash
exec claude --dangerously-skip-permissions
```

**Context**: Starting a fresh session when reference resolution fails.
**Verdict**: KEEP - Gates enforce. No context doesn't mean no protection.

### 5. Line 525: `cmd_fresh` (Fresh with inherited context)

```bash
exec claude --dangerously-skip-permissions
```

**Context**: Starting fresh session with context from previous generation.
**Verdict**: KEEP - Context is from trusted GRIP infrastructure. Gates enforce.

### 6. Line 1606: Flow mode exec

```bash
exec claude --dangerously-skip-permissions "$@"
```

**Context**: Flow mode - trust-based bypass earned through onboarding journey.
**Verdict**: KEEP - Explicit opt-in feature with its own gate check (`check_flow_mode`).

## Summary

| Path | Line | Verdict | Rationale |
|------|------|---------|-----------|
| Upgrade wizard | 373 | KEEP | Gates enforce during bootstrap |
| Onboarding | 389 | KEEP | Symlink-protected prompt path + gates |
| Session resume | 445 | KEEP | Seamless continuation + gates |
| Fresh (no ctx) | 489 | KEEP | Gates enforce regardless |
| Fresh (with ctx) | 525 | KEEP | Trusted context + gates |
| Flow mode | 1606 | KEEP | Explicit opt-in + gates |

## Security Guarantee

All 10 enforcement gates in `context-gate-hook.py` run regardless of the bypass flag:
- Gate C: Remote Execution Verification
- Gate 0: Always Allow (whitelist)
- Gate 1: Dependency Guardian
- Gate 2/2a: Destructive Git / Git Push
- Gate 3: Secrets Detection
- Gate A: Environment Variable Protection
- Gate B: Database Exposure Prevention
- Gate D: Network Binding Protection
- Gate E: Deploy Gate
- Gate F: SSH Blocking
- Gate G: Steering Protection
- Gate 4-9: Read Cache, Tool Substitution, Bash Lint, Temp Script, Code Write, Context Gate

The bypass flag only affects Claude Code's built-in prompts, not GRIP's gate system.
