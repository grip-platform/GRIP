# GRIP Glossary

Terminology used throughout GRIP documentation.

## Core Terms

### Gate

A deterministic enforcement mechanism that blocks actions based on conditions. Gates run outside Claude's weights and cannot be argued with or bypassed through prompting.

```yaml
- id: dependency-guardian
  condition: path contains "node_modules"
  action: deny
```

### Gen (Generation)

A sequential session number tracking GRIP instance lineage. Each new session increments the generation number.

```text
Gen 1 → Gen 2 → Gen 3 (branch) → Gen 4
                    ↓
                  Gen 3.1 (sibling)
```

### Instance

A unique identifier for a GRIP session. Instances have lineage (parent-child relationships) and can be resumed.

```text
Instance: a5e3570c
```

### PARAMOUNT

The highest priority level for GRIP skills and rules. PARAMOUNT items override all other considerations and are loaded automatically.

Examples: `context-gate`, `asking-users`, `forging-skills`

### RSI (Recursive Self-Improvement)

The ability for the agent to propose improvements to its own constraints. GRIP allows RSI for safety gates (proposing new gates) but restricts it for code generation (flow mode locked down by default).

### Signature

A user identity shorthand for team environments. Format: `{LETTER}>>` (e.g., `J>>`, `M>>`, `V>>`).

```text
J>>: refactor the auth module
```

### Mode

An operating context that adjusts GRIP's behavior for specific domains.

| Mode | Purpose |
|------|---------|
| `code` | Software development (default) |
| `strategy` | Business planning |
| `legal` | Contract drafting |
| `marketing` | Content creation |

## Technical Terms

### Hook

Code that runs before or after tool calls. GRIP uses PreToolUse hooks for gate enforcement.

```text
PreToolUse → Gate Evaluation → Allow/Deny → Tool Execution
```

### Skill

A passive reference file defining protocols and best practices. Skills are markdown files loaded as context.

Location: `~/.claude/skills/{skill-name}/SKILL.md`

### Agent

An active subprocess with isolated context that executes tasks autonomously. Agents implement protocols defined in skills.

Examples: `context-refresh`, `pr-workflow`, `history-mining`

### Context Window

The total tokens Claude can process in a session (approximately 200k). GRIP monitors usage and halts at 80%.

### Resurrection

The process of continuing a previous session with inherited context. GRIP serializes instance state and reloads it in new sessions.

```bash
grip resume latest    # Resurrect most recent session
grip resume gen:5     # Resurrect specific generation
```

### gg++

Shell alias for `grip fresh latest 12000`. Launches a fresh GRIP session with maximum context injection (12,000 tokens compressed from the previous session). Progressive hierarchy: `gg` (2k) < `gg+` (8k) < `gg++` (12k).

See [Launch Methods](LAUNCH-METHODS.md) for details.

### Semantic Context

Injected background information that helps Claude understand previous work without re-reading files.

## Interaction Terms

### Direct Mode

Interaction style with no preamble, action-first execution, minimal explanation. Default for experienced users.

### Confirm-First Mode

Interaction style that halts and confirms strategy before writing code. Useful for reviewing approaches before implementation.

### Explanatory Mode

Interaction style that explains reasoning and confirms understanding before proceeding.

## Safety Terms

### Confidence Gate

A gate that halts execution when the agent's confidence drops below a threshold (default 70%). Requires user confirmation to proceed.

### Context Gate

A gate that halts execution when context window usage exceeds 80%. Prevents context exhaustion and loss of early conversation context.

### Dependency Guardian

A gate that blocks reading from dependency folders (`node_modules`, `venv`, etc.) to prevent 50k+ token waste.

### Destructive Git Gate

A gate that blocks dangerous git operations (force push, hard reset) without explicit user approval.

## File Locations

| Term | Path |
|------|------|
| GRIP root | `~/.claude/` |
| Skills | `~/.claude/skills/` |
| Agents | `~/.claude/agents/` |
| Rules | `~/.claude/rules/` |
| Teams | `~/.claude/teams/` |
| Projects | `~/.claude/projects/` |
| Instance index | `~/.claude/projects/{project}/grip/index.json` |
