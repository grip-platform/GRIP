# GRIP Skill Analytics

Smart skill analytics for tracking skill usage patterns.

**Key insight:** Track what actually happens with skill loading to improve over time.

## Architecture

```
+----------------------------------------------------------------+
|                    Skill System                                 |
|                                                                 |
|   skills directory: ~/.claude/skills                            |
|                    |                                            |
|                    v                                            |
|   <available_skills>  ----------------------------------->     |
|   - efficiency-rules                                            |
|   - design-principles                                           |
|   - github                                                      |
|   - ...                        Claude decides what to read      |
+----------------------------------------------------------------+
                                 |
                                 v
+----------------------------------------------------------------+
|                  Skill Analytics                                |
|                                                                 |
|   tool_result hook                                              |
|         |                                                       |
|         v                                                       |
|   "Read skills/github/SKILL.md" --> recordSkillRead("github")  |
|                                                                 |
|   MetricsTracker                                                |
|   +----------------------------------------------------------+ |
|   | Per-skill stats:                                         | |
|   |   - timesAvailable: sessions where skill was listed      | |
|   |   - timesRead: times Claude actually read SKILL.md       | |
|   |   - timesManuallyRequested: FN signal from user          | |
|   |   - readRate: timesRead / timesAvailable                 | |
|   |   - usefulness: readRate adjusted for FN penalty         | |
|   +----------------------------------------------------------+ |
+----------------------------------------------------------------+
```

## How It Works

### Tracking

1. **Session start**: Note which skills are available
2. **During session**: Hook catches all file reads
3. **Skill read detected**: When path matches `*/skills/*/SKILL.md`
4. **Session end**: Compute metrics and save

### Metrics

| Metric | Meaning |
|--------|---------|
| `timesAvailable` | How many sessions had this skill listed |
| `timesRead` | How many times Claude actually read it |
| `timesManuallyRequested` | Times user explicitly asked for it (FN) |
| `readRate` | `timesRead / timesAvailable` |
| `usefulness` | readRate with FN penalty |

### Confusion Matrix (per-skill)

```
                    Skill Actually Needed
                    Yes              No
Skill       Yes     TP (read)        FP (available, not read)
Available   No      FN (manual req)  TN (not available, not needed)
```

- **TP**: Skill was available AND Claude read it
- **FP**: Skill was available BUT Claude didn't read it
- **TN**: Skill wasn't available AND wasn't needed (hard to measure)
- **FN**: User manually requested a skill (strongest learning signal)

## Analytics Commands

| Action | Description |
|--------|-------------|
| `stats` | Overview of all skill metrics |
| `skill` | Detailed stats for one skill |
| `confusion` | Explain the confusion matrix |
| `recommend` | Suggestions based on data |
| `manual_request` | Record that you needed a skill (FN signal) |

## Learning Over Time

The metrics file persists across sessions:

```json
{
  "skillStats": {
    "github": {
      "timesAvailable": 45,
      "timesRead": 12,
      "timesManuallyRequested": 2,
      "readRate": 0.267,
      "usefulness": 0.245
    }
  },
  "sessions": {
    "total": 50,
    "withReads": 35
  },
  "recentSessions": [...],
  "lastUpdated": 1706832000000
}
```

### Actionable Insights

1. **High read rate (>70%)** -> Consider making paramount
2. **High manual request rate** -> Improve skill description or triggers
3. **Low read rate (<20%)** -> Maybe too specialized, consider gating

## Paramount Skills

To make skills always load (paramount), update their `SKILL.md`:

```yaml
---
name: efficiency-rules
description: GRIP efficiency rules - PARAMOUNT, always apply
---
```

## Design Principle

This follows the principle: **observe and learn, don't control**.

Instead of trying to predict which skills Claude needs (which is hard), we:
1. Let the native skill system work
2. Track what actually happens
3. Provide data to improve over time
4. Let the human decide what to make paramount

The FN signal (manual requests) is the strongest learning signal -- when you explicitly ask for a skill, that's ground truth that we missed it.

---

*Part of GRIP Skill Analytics.*
