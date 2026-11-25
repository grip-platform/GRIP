# GRIP Memory Guide

How to use KONO, instance JSON, and auto-memory efficiently.

## Memory Hierarchy

GRIP has three memory layers. Always consult them in this order to avoid wasting tokens:

| Priority | Layer | What It Provides | Token Cost |
|----------|-------|------------------|------------|
| 1 | **MEMORY.md** | Injected at session start. Stable patterns, learnings, preferences | 0 (already loaded) |
| 2 | **Instance JSON** | Structured task state from previous session (`next_up`) | ~200 (one file read) |
| 3 | **KONO** | Semantic recall across all sessions. Cross-project patterns | ~500 per query |

Never jump to KONO when MEMORY.md already has the answer. Never spawn an Explore agent when the git log (injected at session start) has the context.

## Layer 1: Auto-Memory (MEMORY.md)

**Location**: `~/.claude/projects/{project-id}/memory/MEMORY.md`

Loaded automatically into every session's system prompt. Zero-cost access.

### What Goes Here

- Stable patterns confirmed across multiple sessions
- Key architectural decisions and file paths
- Solutions to recurring problems
- User workflow preferences

### What Does NOT Go Here

- Session-specific context (current task, in-progress work)
- Speculative or unverified conclusions
- Anything already in CLAUDE.md

### Usage

MEMORY.md is capped at 200 lines in the system prompt. Keep it concise.

```
# Good: concise, actionable
### Zustand v5 Infinite Loop
- Selectors returning new references cause re-renders
- Fix: module-level constant as fallback

# Bad: verbose, narrative
### Zustand v5 Infinite Loop
I discovered that when using Zustand v5 with React 19, there's an
interesting interaction where the useSyncExternalStore hook...
```

For detailed notes, create topic files (e.g., `memory/debugging.md`) and link from MEMORY.md.

### Updating Memory

The agent updates MEMORY.md automatically when it encounters reusable learnings. You can also instruct it directly:

- "Remember that we always use bun instead of npm"
- "Forget the Railway deployment notes"

## Layer 2: Instance JSON

**Location**: `~/.claude/projects/{project-id}/grip/{instance-sha}.json`

Each session serializes its state on exit. The critical field is `next_up`:

```json
{
  "next_up": {
    "completed": ["Add auth middleware", "Write tests"],
    "remaining": ["Deploy to staging", "Update docs"],
    "blockers": ["Waiting for API key from ops"],
    "notes": "Auth uses JWT. Test creds: user@test.com / test123"
  }
}
```

### Resuming Sessions

| Command | What Happens |
|---------|--------------|
| `grip resume latest` | Loads the most recent instance JSON for this project. Agent sees exact task lists. |
| `grip resume gen:N` | Loads a specific generation's instance. |
| `grip fresh gen:N` | Fresh session, but KONO injects episodic memories from generation N semantically. |

### How It Works

1. **Session end**: Instance serializer writes `next_up` to JSON + stores episodic memory in KONO
2. **Next session start**: Resurrection hook loads the instance, exports `next_up` as context
3. **Agent acknowledges**: "I remember. Instance a5e3570c, Gen 293, 47 messages."

### Checking Instance State Manually

```bash
# List all instances for current project
python3 ~/.claude/lib/instance_serializer.py list

# View a specific instance
python3 ~/.claude/lib/instance_serializer.py info <instance_id>
```

## Layer 3: KONO (Semantic Memory)

**Location**: `~/.claude/embeddings.db` (SQLite with vector extensions)

KONO stores, retrieves, and evolves memories using vector embeddings and spatial coordinates. It operates automatically at four trigger points.

### Automatic Recall (Zero Intervention)

| Trigger | When | What Happens |
|---------|------|--------------|
| Session start | Always | Recalls project-relevant memories (5 results) |
| Resurrection | On `grip resume` | Recalls instance-specific memories (5 results) |
| User prompt | Messages >= 80 chars | Recalls query-relevant memories (3 results, deduplicated) |
| Context refresh | `/refresh-context` | Comprehensive recall (10 results, spatial expansion) |

Recalled memories appear in the agent's context as:

```
[KONO RECALL]
- (factual, 0.82, permanent) Zustand v5 selectors must be stable...
- (procedural, 0.45) electron-vite renderer root defaults to src/renderer...
```

### Memory Lifecycle

```
Store --> Fitness decays daily (5%) --> Memory fades
  ^                                        |
  |                                        v
Attest <-- Recall surfaces it <-- User prompt matches
  |
  v
Fitness increases --> Survives --> 3 attestations = PERMANENT
```

Useful memories get recalled, recalled memories get attested, attested memories survive. Unused memories fade naturally.

### Manual Commands

```bash
# Quick status
python3 ~/.claude/lib/kono_substrate.py compact-stats

# Full dashboard
python3 ~/.claude/lib/kono_substrate.py dashboard

# Manual recall
python3 ~/.claude/lib/kono_substrate.py recall "zustand selector"

# Run fitness decay
python3 ~/.claude/lib/kono_substrate.py metabolism

# Promote memories with 3+ attestations to permanent
python3 ~/.claude/lib/kono_substrate.py consolidate
```

### Memory Dimensions

| Dimension | Examples |
|-----------|----------|
| **Factual** (0-2) | API references, definitions, constants |
| **Procedural** (3-5) | Workflows, plans, how-tos |
| **Episodic** (6-8) | Session summaries, checkpoints |
| **Emergent** (9-11) | Cross-session patterns, insights |

## Efficiency Tips

### Do

- Check MEMORY.md first (already in system prompt, zero cost)
- Use `grip resume latest` to get exact task state from the previous session
- Let KONO recall happen automatically (it triggers on every substantive prompt)
- Keep MEMORY.md under 200 lines with links to detailed topic files

### Don't

- Spawn an Explore agent to find "what happened last session" (read the git log injected at startup)
- Manually query KONO for something MEMORY.md already covers
- Store session-specific state in MEMORY.md (that's what instance JSON is for)
- Re-read files the agent has already read this session (trust the mental model)

### Token Cost Comparison

| Action | Tokens |
|--------|--------|
| Read from MEMORY.md (already loaded) | 0 |
| Read instance JSON | ~200 |
| KONO automatic recall | ~500 |
| `/remind-yourself` (history search) | 5k-20k |
| Explore agent for context | 50k-75k |

## Quick Reference

| I Want To... | Do This |
|-------------|---------|
| See what I worked on last session | Read the git log (already injected) or `grip resume latest` |
| Store a reusable learning | Tell the agent: "Remember that X" |
| Find a past solution | `/remind-yourself "zustand infinite loop"` |
| Check KONO health | `python3 ~/.claude/lib/kono_substrate.py dashboard` |
| Resume exact task state | `grip resume latest` |
| Start fresh with semantic context | `grip fresh gen:N` |
| See all project instances | `python3 ~/.claude/lib/instance_serializer.py list` |

## Further Reading

- [KONO Usage Guide](KONO-USAGE-GUIDE.md) - Full KONO CLI reference and architecture
- [Memory Architecture](MEMORY-ARCHITECTURE.md) - 4-layer technical architecture
- [Session State Architecture](SESSION-STATE-ARCHITECTURE.md) - Instance JSON and branch merging
