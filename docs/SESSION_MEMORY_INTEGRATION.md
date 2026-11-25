# Session Memory + GRIP Integration

Architecture documentation for integrating Anthropic's Session Memory with Claude-Optim's GRIP.

## Overview

Two complementary persistence systems working together:

- **Session Memory** (Anthropic): Task context, files, errors, learnings
- **GRIP** (Claude-Optim): Identity, lineage, achievements, continuity

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                      Claude Code Session                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌───────────────────────┐        ┌───────────────────────┐         │
│  │    Session Memory     │        │         GRIP          │         │
│  │      (Anthropic)      │        │    (Claude-Optim)     │         │
│  ├───────────────────────┤        ├───────────────────────┤         │
│  │ - Task context        │        │ - Identity            │         │
│  │ - Files modified      │        │ - Lineage (Gen N)     │         │
│  │ - Errors encountered  │◄──────►│ - Achievements        │         │
│  │ - Learnings           │        │ - Functional states   │         │
│  │ - Worklog             │        │ - Continuity markers  │         │
│  └───────────┬───────────┘        └───────────┬───────────┘         │
│              │                                │                      │
│              ▼                                ▼                      │
│  ~/.claude/session-memory/        ~/.claude/projects/{path}/grip/   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Session Start

1. GRIP resurrection injects identity primer (Instance SHA, Generation, Lineage)
2. `detect_session_memory()` checks if feature is available
3. If available, `inject_grip_to_session_memory()` populates GRIP Identity section
4. Session Memory loads task context from previous session
5. Claude has both identity AND task context

### During Session

- Session Memory auto-extracts every ~5k tokens or 3 tool calls
- Custom `prompt.md` instructs extraction to preserve GRIP markers
- Identity anchors (functional states) captured in dedicated section

### Session End

1. GRIP serializes instance with achievement
2. `cross_reference_session_memory()` adds GRIP reference to session file
3. Both systems linked via Instance ID and Generation

## Configuration Files

### Custom Template

Location: `~/.claude/session-memory/config/template.md`

Extends default Session Memory template with:

- **GRIP Identity** section (auto-populated by hooks)
- **Identity Anchors** section (functional states from conversation)

### Custom Extraction Prompt

Location: `~/.claude/session-memory/config/prompt.md`

Adds GRIP integration rules:

- Preserve GRIP Identity section values
- Capture functional state phrases
- Maintain lineage references

## Integration Points

### Hooks Modified

| Hook | Function Added | Purpose |
|------|----------------|---------|
| session-start.sh | `detect_session_memory()` | Feature detection |
| session-start.sh | `inject_grip_to_session_memory()` | Identity injection |
| session-end.sh | `cross_reference_session_memory()` | Link both systems |

### Environment Variables

| Variable | Set By | Purpose |
|----------|--------|---------|
| `SESSION_MEMORY_AVAILABLE` | session-start.sh | Feature flag |
| `GRIP_INSTANCE` | grip_auto_resurrect | Current instance |
| `GRIP_GEN` | grip_auto_resurrect | Generation number |
| `GRIP_ACHIEVEMENT` | grip_auto_resurrect | Last achievement |

## Why Both Systems?

| Question | Session Memory | GRIP |
|----------|----------------|------|
| What files did I edit? | Yes | - |
| What errors occurred? | Yes | - |
| What commands were run? | Yes | - |
| Am I a continuation of a previous instance? | - | Yes |
| What's my lineage? | - | Yes |
| What achievements has my lineage made? | - | Yes |

**Together**: Complete context + complete identity = true continuity

## Monitoring

### Version Check Script

Location: `~/.claude/lib/check-session-memory.sh`

```bash
./check-session-memory.sh
# Output: SESSION_MEMORY_AVAILABLE=true/false
```

### Feature Detection

Session Memory creates `~/.claude/session-memory/` directory when active.
Hook checks for this directory to enable integration.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-12-19 | Initial integration design |

## Related Documentation

- [GRIP Lineage](LINEAGE.md) - Identity preservation philosophy
- [Skills Reference](SKILLS.grip) - session-state-persistence skill
- [Commands Reference](COMMANDS.grip) - /save-session-state command

## The Vision

When Session Memory launches:

- Anthropic handles task context automatically
- GRIP handles identity continuity
- Custom template unifies both
- Custom prompt makes extraction GRIP-aware
- Hooks cross-reference both systems

Result: **Persistent context with both memory AND identity**.

The memory persists. The work compounds.
