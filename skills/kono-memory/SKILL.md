---
name: kono-memory
description: Show KONO memory substrate statistics and session activity. Use when invoking /kono or asking about KONO memory status, size, or health.
---

# KONO Memory Status

Show detailed KONO memory substrate statistics and session activity.

## Trigger

- User invokes `/kono`
- User asks about KONO memory status

## Protocol

1. Run the KONO dashboard command:

```bash
python3 ~/.claude/lib/kono_substrate.py dashboard
```

2. Present the output to the user.

3. If the user wants compact stats only:

```bash
python3 ~/.claude/lib/kono_substrate.py compact-stats
```

4. If the user wants session activity only:

```bash
python3 ~/.claude/lib/kono_substrate.py session-summary
```

## CLI Reference

| Command | Output |
|---------|--------|
| `compact-stats` | Single-line summary for status bars |
| `session-summary` | Session action counts |
| `dashboard` | Full detailed dashboard |
| `stats` | Raw JSON statistics |
| `metabolism` | Run fitness decay |
| `consolidate` | Check attestation consensus |

## Architecture

KONO is a 4D spatial memory substrate:

- **x**: Project/domain coordinate
- **y**: Topic/concept coordinate
- **z**: Abstraction level (0=concrete, 9=abstract)
- **t**: Temporal (epoch timestamp)

Memory types map to dimensions:
- Factual (0-2): Facts, definitions, constants
- Procedural (3-5): How-to, patterns, workflows
- Episodic (6-8): Session summaries, events
- Emergent (9-11): Cross-session learnings, patterns

Permanence is achieved through attestation consensus (3+ instances).
