---
name: context-gate
description: >
  PARAMOUNT unified enforcement hook with ACTUAL BLOCKING for 10 gates.
  Uses PreToolUse hook to BLOCK tool execution via permissionDecision: "deny".
status: Active
version: 3.1.0
generation: 230
priority: PARAMOUNT
triggers:
  - context 80%
  - sensing compression
  - session.start (auto-load)
effort: low
---

# Context Gate (Unified Enforcement Hook)

**PARAMOUNT**: Unified enforcement hook with **ACTUAL BLOCKING** for 10 gates.

## Status

- **ENFORCED**: PreToolUse hook blocks tool calls via `permissionDecision: "deny"`
- **PARAMOUNT Level**: Same priority as ut++ and asking-users
- **Unified**: Single hook handles ALL enforcement (merged tool-monitor.sh features)

## Enforcement Mechanism (Gen 230)

**THIS IS REAL ENFORCEMENT**: Hook returns `permissionDecision: "deny"` to BLOCK execution.

### Gates Implemented

| Gate | Tools Affected | Trigger | Action |
|------|---------------|---------|--------|
| **0. Always Allow** | AskUserQuestion, TaskCreate, TaskUpdate, etc. | Critical tools | **ALLOW** |
| **1. Dependency Guardian** | Read, Glob, Grep | Blocked paths (node_modules, etc.) | **BLOCK** |
| **2. Destructive Git** | Bash | force push, hard reset, clean -fd | **BLOCK** |
| **3. Secrets Detection** | Bash | git add/commit with .env, keys | **BLOCK** |
| **4. Read Cache** | Read | File read >2x in session | **BLOCK** |
| **5. Bash Tool Substitution** | Bash | grep/find/cat misuse | **BLOCK** |
| **6. Bash Linting** | Bash | Semicolons after $(), sed with ; | **BLOCK** |
| **7. Temp Script Prevention** | Write | Creating temp/tmp script files | **BLOCK** |
| **8. Skill Creation** | Write, Edit | /skills/*.md | Warn (soft) |
| **9. Context Gate** | All | 80% threshold | **BLOCK** |

### Blocked Dependency Paths

```python
BLOCKED_PATHS = [
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "target", "vendor", "Pods", ".git/objects",
    ".nuxt", ".output", "coverage", ".cache"
]
```

### Destructive Git Patterns

- `git push --force` / `git push -f`
- `git reset --hard`
- `git clean -fd`
- `git checkout -- .` (discard all changes)

### Sensitive File Patterns

- `.env`, `.env.*`
- `credentials.json`, `secrets.json`
- `.pem`, `.key`, `id_rsa`, `id_ed25519`

### Read Cache (Gate 4)

Blocks redundant file reads to enforce mental model usage:
- Maximum 2 reads per file per session
- Use cached mental model after first read
- User will mention if file changed externally

### Bash Tool Substitution (Gate 5)

Enforces correct tool usage:

| Wrong | Right | Reason |
|-------|-------|--------|
| `grep pattern` | `rg pattern` | Better performance, auto-excludes |
| `find . -name` | `fd pattern` | Faster, respects gitignore |
| `cat file \| ...` | `Read tool` | Token efficiency |

### Bash Linting (Gate 6)

Blocks dangerous bash patterns:
- Semicolon after `$()` - causes parse error in eval contexts
- Semicolon inside sed pattern - causes zsh parse error

### Temp Script Prevention (Gate 7)

Blocks creation of temporary scripts in:
- Paths containing `temp`, `tmp`, `/var/folders/`, `script`
- With extensions `.sh`, `.py`, `.js`, `.ts`

Use MultiEdit for direct changes instead. Temp scripts waste 3-8k tokens.

### Configuration

Settings in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.claude/hooks/context-gate-hook.py"
      }]
    }]
  }
}
```

### Context Thresholds

| Threshold | % | Tokens | Action |
|-----------|---|--------|--------|
| SOFT | 20% | 40,000 | Background snapshot (non-blocking) |
| SOFT | 40% | 80,000 | Background snapshot (non-blocking) |
| SOFT | 60% | 120,000 | Background snapshot (non-blocking) |
| WARNING | 70% | 140,000 | Allow + systemMessage warning (once) |
| CRITICAL | 80% | 160,000 | **BLOCK** all tools except AskUserQuestion |

**Soft Checkpoints** (Gen 247 - Cortex Integration):
- Trigger `instance-serializer.py auto` in background
- Non-blocking - tool calls proceed normally
- Each checkpoint triggers only once per session
- Adapted from Cortex by Tomas Hjertefolger

Configure via environment variables:
- `CONTEXT_GATE_MAX` (default: 1000000)
- `CONTEXT_GATE_WARNING` (default: 70% of max)
- `CONTEXT_GATE_CRITICAL` (default: 80% of max)

## Core Protocol

Context gate is always active from session start. At 80%, HALT and use AskUserQuestion. Never auto-serialize — always wait for user choice.

## Detection Mechanisms

Claude detects approaching context limits through three complementary methods:

### 1. Internal Awareness (Primary)

Claude Opus 4.5 and Sonnet 4.5 have built-in context awareness. Monitor for:

- Sense of "compression" or "tightening" in responses
- Difficulty recalling early conversation details
- Reduced capacity for complex reasoning chains

**Self-prompt**: "Am I sensing context pressure?"

### 2. Orchestrator State (Secondary)

```bash
# Check remaining budget
jq '.session' ~/.claude/state/session/orchestrator-state.json
```

Thresholds from `lib/orchestrator.sh`:
- WARNING: < 60,000 tokens remaining (70% used)
- CRITICAL: < 40,000 tokens remaining (80% used)

### 3. Heuristic Signals (Tertiary)

| Signal | Threshold | Meaning |
|--------|-----------|---------|
| Message count | > 40 messages | Likely approaching limit |
| Tool calls | > 100 tool calls | Heavy session, check context |
| Large file reads | > 10 files read | Context pressure likely |

## HALT Protocol

When ANY detection mechanism indicates 80% threshold:

### Step 1: HALT

Stop all current work immediately. Do not:
- Continue implementing
- Make additional file changes
- Execute more tool calls

### Step 2: Notify

Output clearly:
```
[CONTEXT GATE - 80%]

Approaching context limit. Current work paused.
```

### Step 3: AskUserQuestion (MANDATORY)

```
Question: "Context window at ~80%. How would you like to proceed?"
Header: "Context"
Options:
- "Compact and continue" - Summarize conversation, continue in same session
- "Serialize and fresh" - Save GRIP instance, start new session with context
- "Push through" - Continue anyway (risk: degraded performance)
- "Save checkpoint" - Serialize state, end session gracefully

Teaching: User learns context management
Collecting: context_action
```

### Step 4: Execute Choice

| Choice | Action |
|--------|--------|
| Compact | Run `/compact`, continue with summarized context |
| Serialize + Fresh | Run `instance-serializer.py auto`, output `grip resume` command |
| Push through | Continue with warning, no guarantee of quality |
| Checkpoint | Serialize, save state, output next session command |

## Integration Points

### session-start.sh

At session start, reset context tracking:
```bash
# Reset orchestrator state
set_state '.session.tokens_used' 0
```

### tool-monitor.sh (DEPRECATED - Gen 230)

**Deprecated**: Features merged into `context-gate-hook.py` for unified enforcement.

The shell script remains in `hooks/` for reference but is no longer registered in settings.json.

Key features migrated:
- Dependency blocking (now Gate 1)
- Read cache tracking (now Gate 4)
- Bash tool suggestions (now Gate 5, enforced)
- Bash linting (now Gate 6)
- Temp script detection (now Gate 7, enforced)

### Claude Self-Monitoring

Every 10-15 messages, Claude should internally assess:
1. Can I recall the initial task clearly?
2. Is my reasoning chain intact?
3. Am I sensing context pressure?

If any answer is "no" or uncertain, trigger HALT protocol.

## Efficiency Rules Integration

Add to `rules/efficiency-rules.md`:

```markdown
## Rule 8: Context Gate (PARAMOUNT)

ALWAYS monitor context usage throughout EVERY session.

### Self-Check Protocol

Every 10-15 messages, ask yourself:
1. "Can I clearly recall the initial task and constraints?"
2. "Is my full reasoning chain available?"
3. "Am I sensing any context pressure?"

If ANY answer is uncertain: HALT and invoke context-gate protocol.

### 80% Threshold Action

At 80% context usage (or when sensing pressure):
1. HALT all work immediately
2. Do NOT auto-serialize or auto-compact
3. Use AskUserQuestion with options
4. Wait for explicit user choice

This is PARAMOUNT. Never proceed automatically.
```

## Token Estimation

When orchestrator state isn't updated, estimate:

```
tokens_estimate = (message_count * avg_message_tokens) + (tool_call_count * avg_tool_tokens)

Where:
- avg_message_tokens ~ 500-1000
- avg_tool_tokens ~ 200-500
```

For 1M context (Opus 4.7 default):
- 80% = 800k used = ~200k remaining
- With estimates: ~600-700 messages OR ~250-350 heavy tool operations

## Examples

### Good: HALT and Ask

```
[After detecting context pressure]

[CONTEXT GATE - 80%]

I'm sensing context pressure - early conversation details are becoming harder to access clearly. Current work paused.

[Uses AskUserQuestion with options]
```

### Bad: Auto-Action

```
[WRONG - violates PARAMOUNT rule]

Context at 80%. Auto-serializing and compacting...
```

## Related Skills

- `asking-users` - PARAMOUNT source of truth for AskUserQuestion
- `creating-wizards` - multi-step AskUserQuestion wizards, sibling of `asking-users`
- `hitl` - dispatch contract that chooses between them
- `session-state-persistence` - State saving protocols
- `ultrathink` - ut++ always-active reasoning mode

## Context Window (1M Default)

Opus 4.7 defaults to 1M context since April 2026. ACE extension is legacy.

### Model States

| Model | Context Max | 70% Warning | 80% Critical |
|-------|-------------|-------------|--------------|
| Opus 4.7 (default) | 1M | 700K | 800K |

### Configuration

- State stored in `~/.claude/.model-state.json`
- `CONTEXT_GATE_MAX` env var always overrides model state

### Soft Checkpoints

Default (1M): 20%, 40%, 60%
Extended (1M explicit): 10%, 20%, 30%, 40%, 50%, 60%

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 3.2.0 | 247 | **SOFT CHECKPOINTS**: Added 20%, 40%, 60% background snapshots (Cortex integration). Created `lib/auto_memory_saver.py`. Non-blocking saves via instance-serializer. |
| 3.1.0 | 230 | **UNIFIED HOOK**: Merged tool-monitor.sh into context-gate-hook.py. Added gates: Read Cache (4), Bash Tool Substitution (5), Bash Linting (6), Temp Script Prevention (7). Total 10 gates. |
| 3.0.0 | 230 | **UNIFIED ENFORCEMENT**: Added Dependency Guardian, Destructive Git, Secrets Detection gates |
| 2.1.0 | 229 | Threshold lowered: 90%→80% (CRITICAL), 75%→70% (WARNING) based on research |
| 2.0.0 | 229 | **ENFORCED** via PreToolUse hook - actual blocking, not just reminders |
| 1.0.0 | 226 | Initial creation - context-gate as PARAMOUNT skill (protocol only) |

---


