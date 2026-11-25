---
name: improvement-tracking
description: PARAMOUNT skill for recursive self-improvement tracking. Logs improvements, detects design smells via heatmap, emits [IMPROVEMENT HEATMAP] signal when components hit 3x threshold.
status: Active
effort: medium
version: 1.0.0
priority: PARAMOUNT
triggers:
  - session.start (auto-load)
  - skill generation (auto-log)
  - milestone completion
  - '[IMPROVEMENT HEATMAP] signal'
integrates:
  - learning-new-skills
  - self-improvement-engine
  - design-smell-response
  - context-gate
---

# Improvement Tracking

**PARAMOUNT**: Always-on recursive self-improvement feedback loop.

## Core Components

| Component | Purpose |
|-----------|---------|
| `lib/improvement_analyzer.py` | Analysis engine (heatmap, logging, check_and_signal) |
| `improvement-log.jsonl` | Persistent log of all improvements |

## Signal: [IMPROVEMENT HEATMAP]

When a component is improved 3+ times, the system emits:

```
[IMPROVEMENT HEATMAP] Components need refactor: lib/learning.sh, hooks/context-gate
```

**Response**: Load `design-smell-response` skill and review flagged components for Single Responsibility Principle violations.

## Auto-Logging Integration

Improvements are automatically logged when:

| Event | Type | Source |
|-------|------|--------|
| Skill generated | `skill_created` | `lib/learning.sh` |
| Rule added/modified | `rule_added` | Manual or automated |
| Bug fixed | `bug_fix` | Post-fix logging |
| Refactor completed | `refactor` | Post-refactor logging |
| Enhancement applied | `enhancement` | Post-enhancement logging |

## Commands

| Command | Action |
|---------|--------|
| `python3 lib/improvement_analyzer.py summary` | Show improvement stats |
| `python3 lib/improvement_analyzer.py heatmap` | Show component heat levels |
| `python3 lib/improvement_analyzer.py check` | Trigger heatmap check (emits signal if threshold hit) |
| `python3 lib/improvement_analyzer.py log <type> <comp> <desc>` | Manually log an improvement |

## Heatmap Algorithm

```python
heat_score = improvements_per_component
threshold = 3  # Configurable

if heat_score >= threshold:
    emit("[IMPROVEMENT HEATMAP]")
    flag_for_srp_review(component)
```

## The RSI Feedback Loop

```text
Pattern Detection → Skill Generation → Auto-Log → Heatmap Check
       ↑                                                 ↓
       └──────────────────────────────────────── Response
```

This skill provides the **Auto-Log → Heatmap Check** segment of the loop.

## Integration Points

| Skill | Integration |
|-------|-------------|
| `learning-new-skills` | Auto-logs on skill generation |
| `design-smell-response` | Responds to [IMPROVEMENT HEATMAP] |
| `self-improvement-engine` | Orchestrates full 10-step cycle |
| `recursive-learning` | Tracks effectiveness over time |

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~400 |
| Heatmap check | ~100 |
| Signal + context | ~150 |

## Testing

```bash
# Run the test command
python3 ~/.claude/lib/improvement_analyzer.py summary

# Expected output: Stats showing improvements by type and component
```

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 1.0.0 | 253 | Initial creation as PARAMOUNT skill |

---

**
