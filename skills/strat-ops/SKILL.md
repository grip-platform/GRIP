---
name: strat-ops
description: Strategy operations mode - detects strategic context and routes to appropriate strategy skill/mode. Covers gap analysis, red-teaming, pre-mortems, and decision locking.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /strat-ops
  - "analyse our position"
  - "strategic assessment"
  - "stress test this assumption"
  - "what could kill us"
  - "lock this decision"
  - "gap analysis"
  - "pre-mortem"
  - "assume X is wrong"
integrates:
  - strategic-gap-analysis
  - red-teaming-strategic-decisions
  - asking-users
  - creating-wizards
  - hitl
---

# Strat-Ops: Strategy Operations Mode

Meta-skill that detects strategic context and routes to the appropriate strategy mode.

## Philosophy

Strategy work is different from coding work. It requires:
- Structured frameworks (not free-form discussion)
- Explicit assumption surfacing
- Team input aggregation
- Decision documentation

Strat-ops mode recognises when you're doing strategy and activates the right tools.

## Strategy Modes

| Mode | Trigger Patterns | Skill/Protocol |
|------|------------------|----------------|
| **Gap Analysis** | "assess position", "gap analysis", "7-fit", "business health" | `strategic-gap-analysis` |
| **Red Team** | "stress test", "challenge assumption", "what if X is wrong" | `red-teaming-strategic-decisions` |
| **Pre-Mortem** | "what could kill us", "failure modes", "imagine we failed" | `modes/pre-mortem.md` |
| **Decision Lock** | "lock this decision", "finalise", "document decision" | `modes/decision-lock.md` |

## Detection Protocol

When strategic language detected:

```text
[STRAT-OPS DETECTED]

I'm sensing strategic context. Available modes:

1. Gap Analysis - Assess current position across 7 fits
2. Red Team - Stress-test assumptions adversarially
3. Pre-Mortem - Identify failure modes before they happen
4. Decision Lock - Document and finalise a decision

Which mode fits your intent?
```

### Trigger Patterns

**Gap Analysis Mode**:
- "assess our strategic position"
- "where are we weak"
- "gap analysis"
- "5-fit", "7-fit"
- "business health check"
- `/strategic-gap`, `/7-fit`

**Red Team Mode**:
- "stress test this"
- "challenge our assumptions"
- "what if we're wrong about X"
- "devil's advocate"
- "assume the opposite"
- `/red-team`

**Pre-Mortem Mode**:
- "what could kill us"
- "imagine we failed"
- "failure modes"
- "how could this go wrong"
- "pre-mortem"

**Decision Lock Mode**:
- "lock this decision"
- "finalise the choice"
- "document this decision"
- "we've decided X"
- "decision made"

## Mode Routing

### Automatic Routing

If trigger is unambiguous, route directly:

```text
User: "Let's do a gap analysis"
→ Invoke strategic-gap-analysis skill
```

### Clarification Routing

If trigger is ambiguous, use AskUserQuestion:

```text
User: "Let's think strategically about this"

Question: "What type of strategic work?"
Header: "Mode"
Options:
- "Gap Analysis" - Assess current position
- "Red Team" - Challenge assumptions
- "Pre-Mortem" - Identify failure modes
- "Decision Lock" - Finalise a decision
```

## Mode Protocols

### Gap Analysis (via strategic-gap-analysis)

Full 49-question assessment across 7 fits:
1. Positioning Fit
2. Delivery Fit
3. Client Fit
4. Channel Fit
5. Model Fit
6. Team Fit (critical - V>>'s "again" fear)
7. Founder Fit

Modes: Founder Solo, Team Individual, Comparison

See `~/.claude/skills/strategic-gap-analysis/SKILL.md`

### Red Team (via red-teaming-strategic-decisions)

Adversarial assumption testing:
1. Surface assumptions
2. Score confidence (1-10)
3. Steel-man counter-positions
4. Identify kill shots
5. De-risk or pivot

See `~/.claude/skills/red-teaming-strategic-decisions/SKILL.md`

### Pre-Mortem (via pre-mortem-facilitation)

See `~/.claude/skills/pre-mortem-facilitation/SKILL.md`

Failure mode identification:
1. "It's [future date]. We failed. What happened?"
2. Each team member writes independently
3. Cluster failure modes
4. Prioritise by likelihood × impact
5. Create de-risking actions

### Decision Lock (via decision-locking)

See `~/.claude/skills/decision-locking/SKILL.md`

Decision documentation:
1. State the decision clearly
2. List alternatives considered
3. Document rationale
4. Record who was involved
5. Set review date
6. Create accountability

## Creating New Strategy Skills

Use templates in `templates/` to create organisation-specific strategy skills:

- `templates/gap-analysis-template.md` - Custom gap analysis frameworks
- `templates/red-team-template.md` - Custom red-team protocols
- `templates/pre-mortem-template.md` - Custom pre-mortem frameworks

## GRIP Integration

```grip
; strat-ops mode detection
context.strategy⟿ @skills/strat-ops/SKILL.md
strat-ops.detect⟿ trigger.patterns
strat-ops.route⟿ mode.specific ∨ AskUserQuestion
strat-ops.modes⟿ gap-analysis ⫶ red-team ⫶ pre-mortem ⫶ decision-lock
```

## Storage

Strategy artifacts stored in:

```
~/.claude/strat-ops/
├── assessments/           # Gap analysis results
│   └── {date}/{sig}.json
├── red-teams/             # Red team exercise results
│   └── {date}-{topic}.md
├── pre-mortems/           # Pre-mortem results
│   └── {date}-{topic}.md
└── decisions/             # Locked decisions
    └── {date}-{topic}.md
```

## Token Budget

| Component | Tokens |
|-----------|--------|
| Mode detection | ~200 |
| Mode routing | ~150 |
| Mode-specific (varies) | 3k-8k |

## Related Skills

- `strategic-gap-analysis` - 7-Fit assessment
- `red-teaming-strategic-decisions` - Assumption testing
- `asking-users` - PARAMOUNT for all strategic interactions
- `leading-organisation` - Founder operating system

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial creation |

---


