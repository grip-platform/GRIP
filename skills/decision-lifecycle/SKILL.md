---
name: decision-lifecycle
description: Record, track, resolve, and audit decisions across their full lifecycle. Use when recording decisions, checking outcomes, running checkpoint reviews, or voiding stale entries.
status: Active
effort: high
version: 1.0.0
created: 2026-03-06
triggers:
  - /decision
  - /decisions
  - /resolve-decision
  - /void-decision
  - /decision-checkpoint
  - "record this decision"
  - "what decisions are open"
  - "resolve decision"
  - "void decision"
integrates:
  - decision-mode
  - decision-locking
  - asking-users
  - creating-wizards
  - hitl
---

# Decision Lifecycle

Operational lifecycle management for decisions — from recording through resolution.

## Purpose

Close the gap between *making* decisions (decision-mode) and *locking* them (decision-locking). This skill handles:

- Recording decisions with metadata
- Tracking predicted vs actual outcomes
- Running checkpoint reviews
- Resolving/voiding stale entries
- Surfacing unresolved decisions

## State File

Store at `~/.claude/state/decisions.json`:

```json
{
  "decisions": [
    {
      "id": "1ac6a4cd",
      "created": "2026-03-05T02:42:00Z",
      "decision": "Should we expand?",
      "source": "user",
      "confidence": null,
      "stakes": "strategic",
      "status": "open",
      "outcome": null,
      "resolved_at": null,
      "notes": null
    }
  ]
}
```

## Decision Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | 8-char hex, first 8 of UUID |
| `created` | ISO8601 | yes | When recorded |
| `decision` | string | yes | The decision statement |
| `source` | enum | yes | `user`, `automated`, `copilot` |
| `confidence` | 0.0-1.0 | no | Predicted confidence (automated only) |
| `stakes` | enum | no | `trivial`, `tactical`, `strategic` |
| `status` | enum | yes | `open`, `resolved`, `voided` |
| `outcome` | enum | no | `success`, `fail`, `deferred`, `partial` |
| `resolved_at` | ISO8601 | no | When resolved/voided |
| `notes` | string | no | Resolution rationale |

---

## Commands

### /decision record

Record a new decision:

```text
/decision record "Should we expand the team?"
```

Prompt for metadata:
- Stakes: trivial / tactical / strategic
- Confidence (if automated): 0.0-1.0

### /decision list

Show open decisions:

```text
/decision list
/decision list --all        # Include resolved/voided
/decision list --stale 7d   # Open > 7 days
```

Output format:

```
Open Decisions (3)

| ID       | Age  | Decision                  | Stakes    |
|----------|------|---------------------------|-----------|
| 1ac6a4cd | 5h   | Should we expand?         | strategic |
| 186f9301 | 2h   | Should we expand?         | strategic |
| f343604b | 5h   | (empty)                   | -         |
```

### /decision resolve

Resolve with outcome:

```text
/decision resolve 1ac6a4cd --outcome deferred --notes "Capacity constraints"
```

Outcomes: `success`, `fail`, `deferred`, `partial`

### /decision void

Void invalid/test entries:

```text
/decision void f343604b --reason "Empty decision body"
```

### /decision checkpoint

Run checkpoint review (what Copilot does):

```text
/decision checkpoint
```

Actions:
1. List all open decisions with age
2. Flag stale (>24h for tactical, >7d for strategic)
3. Compare predictions vs outcomes for resolved
4. Surface decisions needing attention

---

## Checkpoint Review Protocol

Run daily or on-demand. Produces:

### 1. Unresolved Decisions

| ID | Age | Decision | Stakes | Action Needed |
|----|-----|----------|--------|---------------|
| ... | ... | ... | ... | Resolve/Void/Defer |

### 2. Prediction Accuracy

For automated decisions with confidence scores:

| ID | Predicted | Actual | Delta |
|----|-----------|--------|-------|
| ... | 0.85 | success | +0.15 |

Track calibration over time.

### 3. Patterns

- Duplicate decisions (same text, multiple entries)
- Stale open decisions (exceeding threshold)
- Low-confidence predictions that succeeded
- High-confidence predictions that failed

---

## Integration

### With decision-mode

After decision-mode completes analysis:
```
-> decision-lifecycle records decision
-> Tracks until resolved
```

### With decision-locking

After decision-locking finalises:
```
-> decision-lifecycle marks resolved with outcome
-> Links to locked decision document
```

### With Copilot

Copilot calls checkpoint review:
```
-> decision-lifecycle returns structured data
-> Copilot formats for Slack
```

---

## Auto-Void Rules

Void automatically when:

- Decision body is empty
- Decision body is test placeholder ("test", "asdf", etc.)
- Duplicate of existing open decision (keep oldest)

---

## Example: Full Lifecycle

```
# 1. User makes strategic decision
User: "Should we expand the team?"
→ /decision record "Should we expand the team?"
→ ID: 1ac6a4cd, stakes: strategic, status: open

# 2. Hours pass, Copilot runs checkpoint
/decision checkpoint
→ Unresolved: 1ac6a4cd (5h old, strategic)
→ Action needed: Resolve or defer

# 3. User responds to checkpoint
User: "No expand. No scale."
→ /decision resolve 1ac6a4cd --outcome deferred --notes "Capacity constraints at 72% sprint commitment"
→ Status: resolved

# 4. Void malformed entries
/decision void f343604b --reason "Empty decision body"
→ Status: voided

# 5. Next checkpoint shows clean state
/decision checkpoint
→ No unresolved decisions
→ Calibration: 1 deferred (no prediction to verify)
```

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~600 |
| State read | ~200 |
| Checkpoint report | ~400 |
| **Total** | **~1200** |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-06 | Initial creation — fills lifecycle gap |
