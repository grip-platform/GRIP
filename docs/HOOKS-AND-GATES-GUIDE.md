# Hooks and Safety Gates Guide

How GRIP intercepts tool calls, enforces safety gates, and runs lifecycle hooks.

> **Authority note (2026-07-30):** this guide contains historical gate examples.
> The executable source of truth is `config/hooks.registry.json`, with the
> current generated inventory in `hooks/GENERATED-HOOKS.md`. Do not copy the
> older response JSON examples below into a hook implementation.

## What Are Hooks?

Hooks are scripts that Claude Code executes at specific lifecycle events. GRIP uses hooks for two purposes:

1. **Safety gates** - intercept and block dangerous tool calls before they execute
2. **Lifecycle automation** - run scripts at session start, on user prompts, and at session end

The key insight: hooks run **outside Claude's weights**. A Python gate script cannot be persuaded or argued with. Condition matches, action blocked.

## How Safety Gates Work

Every time the agent calls a tool (Read, Write, Bash, Edit, etc.), a PreToolUse hook fires **before** the tool executes:

```
Agent plans tool call
       |
       v
PreToolUse hook fires
       |
       v
context-gate-hook.py evaluates 10 gates
       |
       +-- All gates pass --> Tool executes normally
       |
       +-- Any gate denies --> Tool blocked, reason shown to agent
```

The agent sees the denial reason and must adjust its approach. It cannot retry the same blocked action.

## The 10 Default Gates

| # | Gate | Tools Affected | What It Blocks |
|---|------|---------------|----------------|
| 0 | Always Allow | AskUserQuestion, Task tools, Plan tools | Nothing (whitelist) |
| 1 | Dependency Guardian | Read, Glob, Grep | Reading node_modules, venv, dist, etc. |
| 2 | Destructive Git | Bash | `git push --force`, `git reset --hard`, `git clean -f` |
| 3 | Secrets Detection | Bash | Committing .env, .pem, credentials.json |
| 4 | Read Cache | Read | Re-reading files already read this session |
| 5 | Bash Tool Substitution | Bash | Using grep instead of rg, find instead of fd |
| 6 | Bash Linting | Bash | Dangerous bash patterns (semicolons after `$()`) |
| 7 | Temp Script Prevention | Write | Creating throwaway scripts instead of direct edits |
| 8 | Skill Creation | Write, Edit | Skill files missing required sections |
| 9 | Context Gate | All | Context window usage exceeding 80% |

### Gate Examples

**Dependency Guardian** blocks reading dependency folders (saves 50k+ tokens):

```
Agent: Read /project/node_modules/express/index.js
Gate:  DENY - "Cannot read from node_modules. Use rg --glob '!node_modules/*' instead."
```

**Destructive Git** blocks force pushes:

```
Agent: Bash "git push --force origin main"
Gate:  DENY - "Destructive git operation blocked. User must explicitly approve."
```

**Read Cache** prevents redundant file reads:

```
Agent: Read /src/app.ts  (already read 3 messages ago)
Gate:  DENY - "File already read this session. Use your cached mental model."
```

**Context Gate** halts work at 80% context usage:

```
Agent: (any tool call when context > 80%)
Gate:  DENY - "Context at 80%. Halting for user decision."
Agent: Presents options: compact, fresh session, or push through
```

## Remote Execution Gates

Additional gates for when GRIP runs in remote/deployed contexts:

| Gate | What It Blocks |
|------|---------------|
| **Env Var Protection** | Reading `$API_KEY`, `$SECRET`, `$TOKEN` patterns |
| **Database Exposure** | `pg_dump`, `mysqldump`, `SELECT * FROM users` |
| **Remote Verification** | Unverified remote execution contexts |
| **Network Binding** | Binding to 0.0.0.0 without authentication |
| **Secrets in Output** | Redacts API keys, tokens, private keys from tool output |

## Lifecycle Hooks

Beyond safety gates, GRIP hooks automate the session lifecycle:

### Session Start

```
session-start.sh
    |
    +-- init.sh        Set up constants, paths, logging
    +-- grip.sh        Auto-resurrect previous session, KONO recall, mode restore
    +-- checks.sh      Detect state files, plans, learning candidates
    +-- output.sh      Emit compact status line to agent
```

**What happens automatically:**
1. Register session in GRIP registry
2. Auto-resurrect previous instance (if exists)
3. Recall project-relevant memories from KONO
4. Restore active mode
5. Inject instance `next_up` state
6. Output compact status line

### User Prompt (UserPromptSubmit)

Fires on every user message:

| Hook | What It Does |
|------|-------------|
| `recall-hook.py` | KONO semantic recall for messages >= 80 chars |
| `learning-hook.py` | Detects reusable patterns for autonomous learning |

### Session End

Fires when the session terminates:

| Hook | What It Does |
|------|-------------|
| `session-end.sh` | Serializes instance state, runs KONO metabolism and consolidation |

## Hook Configuration

Hooks are registered in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "python3 $HOME/.claude/hooks/context-gate-hook.py"
      }]
    }],
    "UserPromptSubmit": [{
      "hooks": [
        { "command": "python3 $HOME/.claude/hooks/recall-hook.py" },
        { "command": "python3 $HOME/.claude/hooks/learning-hook.py" }
      ]
    }]
  }
}
```

### Hook Response Format

All PreToolUse hooks return JSON:

```json
{"decision": "allow"}
```

```json
{"decision": "deny", "reason": "Explanation shown to the agent"}
```

```json
{"decision": "allow", "systemMessage": "Context injected into agent without blocking"}
```

The `systemMessage` field lets hooks inject information (like forge signals or recall results) without blocking the tool call.

## Subagent Gate Enforcement (Fractal Gates)

Hooks fire at ALL depths in Claude Code — both the main session (depth 0) and
subagents (depth 1). Subagents cannot spawn sub-subagents, so depth is capped at 1.

### How It Works

When a hook fires inside a subagent, the stdin JSON includes two additional fields:

- `agent_id`: Unique identifier for the subagent (e.g. `"a113d7f56afb"`)
- `agent_type`: Agent name (e.g. `"Explore"`, `"direct-implementation"`)

These fields are `null`/absent for main session tool calls.

### Enforcement Layers at Depth 1

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| User-level hooks (settings.json) | All 46+ hooks fire for subagent tool calls | All subagents |
| Agent-scoped hooks (frontmatter) | Hooks defined in agent `.md` frontmatter | That agent only |
| Skills injection (frontmatter) | `skills:` field injects GRIP knowledge | That agent only |
| additionalContext injection | PreToolUse returns compact gate rules | All subagents |

### additionalContext Injection

The `context-gate-hook.py` detects subagent context via `agent_id` and injects
compact gate rules (~30 tokens) via the `additionalContext` response field on
every allowed tool call. This works for ALL subagents including built-in agents
(Explore, Plan, general-purpose) that cannot be modified via frontmatter.

### SubagentStart/SubagentStop Telemetry

Lifecycle events are logged to `data/subagent-lifecycle.jsonl` via
`hooks/subagent-lifecycle-hook.py`. The sentinel hook also records `agent_id`
and `agent_type` on all events for depth-aware audit trails.

### Verified Empirically

Canary test (2026-03-28) confirmed:

- PreToolUse fires for subagent Read calls (sentinel shows `agent_id`)
- Dependency Guardian blocks `node_modules` reads at depth 1
- Tool Substitution blocks bash `grep` at depth 1
- SubagentStart/SubagentStop fire and log correctly

## Customizing Gates

### Add Custom Gates via YAML

Create `~/.grip/gates.yaml`:

```yaml
version: "2.0"

gates:
  - id: no-drop-database
    name: "Database Protection"
    condition:
      type: command_pattern
      patterns:
        - "DROP\\s+DATABASE"
        - "DELETE\\s+FROM.*WHERE\\s+1=1"
    action: deny
    reason: "Database destructive operation blocked."

  - id: strict-confidence
    condition:
      type: confidence_threshold
      threshold: 0.8
```

### Add Custom Gates via Python

Edit `context-gate-hook.py`:

```python
def check_my_gate(tool_name: str, tool_input: dict) -> tuple[str, str]:
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if "dangerous_thing" in command:
            return "deny", "Custom gate: blocked dangerous_thing"
    return "allow", ""
```

Add to the gate chain:

```python
gates = [
    check_always_allow,
    check_dependency_guardian,
    # ... existing gates ...
    check_my_gate,
    check_context_gate,  # context gate always runs last
]
```

## Testing Gates

Each gate has must-block and must-allow test cases:

```yaml
tests:
  must_block:
    - { tool: Bash, command: "git push --force" }
  must_allow:
    - { tool: Bash, command: "git push origin main" }
```

Test manually:

```bash
# Test a specific tool call against all gates
echo '{"tool_name": "Read", "tool_input": {"file_path": "/project/node_modules/x.js"}}' | \
  python3 ~/.claude/hooks/context-gate-hook.py

# Expected: {"decision": "deny", "reason": "Dependency Guardian: ..."}
```

Run full gate verification:

```bash
grip-gates verify           # All gates
grip-gates test gate-name   # Specific gate
```

## Audit Trail

All gate decisions are logged to `~/.grip/gates-audit.jsonl`:

```json
{"ts": "2026-02-01T17:00:00Z", "gate": "dependency-guardian", "decision": "deny", "tool": "Read"}
{"ts": "2026-02-01T17:00:01Z", "gate": "destructive-git", "decision": "deny", "tool": "Bash"}
```

## Key Principles

| Principle | What It Means |
|-----------|--------------|
| **User authority** | User defines gates. Agent cannot override. |
| **Mechanical enforcement** | Python script, not trained values. Cannot be persuaded. |
| **Deterministic** | Same input always produces same output. |
| **Testable** | Every gate has must-block/must-allow cases. |
| **Customizable** | YAML config, no code changes needed for basic gates. |
| **Auditable** | Every decision logged with timestamp and context. |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Hooks not running | settings.json misconfigured | Verify hook paths use `$HOME` |
| "command not found" | Python not in PATH | Use full path: `/usr/bin/python3` |
| Gate not triggering | Regex pattern mismatch | Test manually with echo pipe |
| False positive | Pattern too broad | Narrow the regex or add exceptions |
| Hook timeout | Script takes too long | Check for network calls or slow I/O |

## Further Reading

- [Hooks Architecture](HOOKS-ARCHITECTURE.md) - Technical implementation details
- [Safety Gates](SAFETY-GATES.md) - Design philosophy and remote execution gates
- [Security Hardening Guide](SECURITY-HARDENING-GUIDE.md) - Full deployment security
