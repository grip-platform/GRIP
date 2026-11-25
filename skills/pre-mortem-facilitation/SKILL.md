---
name: pre-mortem-facilitation
description: Facilitate pre-mortem exercises to identify failure modes before they happen. Use when planning major initiatives or when team confidence is high (hubris check).
status: Active
effort: medium
version: 1.0.0
triggers:
  - /pre-mortem
  - "what could kill us"
  - "failure modes"
  - "imagine we failed"
  - "how could this go wrong"
integrates:
  - asking-users
  - creating-wizards
  - hitl
  - strat-ops
  - strategic-gap-analysis
---

# Pre-Mortem Facilitation

Identify failure modes BEFORE they happen.

## Origin

Pre-mortem technique from Gary Klein (psychologist). Instead of post-mortem analysis after failure, imagine failure has already occurred and work backwards.

**Key Insight**: Prospective hindsight increases ability to identify reasons for future outcomes by 30% (Klein, 1989).

## When to Use

- Before major strategic decisions
- When launching new initiatives
- When team has high confidence (hubris check)
- Quarterly risk assessment
- After completing gap analysis (complement to 7-Fit)

## Protocol

### Step 1: Set the Scene

```text
"It's [6 months from now]. [Your Company] has failed.
The company has shut down.

Take 5 minutes. Write independently.
What happened? Be specific."
```

**Rules**:
- Write independently (no discussion until all complete)
- Be specific (not "we failed" but "cashflow ran out because...")
- No blame filtering ("V>> failed to..." is allowed - it's pre-mortem)
- First person: "Looking back, what killed us was..."

### Step 2: Collect Responses

Each team member submits their failure narrative.

**Solo Mode** - Write from multiple perspectives:
- As yourself
- As your most skeptical advisor
- As a competitor watching you fail

**Team Mode** - Each member writes independently, then share.

### Step 3: Cluster Failure Modes

Group similar failure modes into clusters:

| Cluster | Failure Modes | Frequency |
|---------|---------------|-----------|
| Financial | Cashflow, pricing, client concentration | 3/5 |
| Capacity | Burnout, bottleneck, delegation failure | 2/5 |
| Market | Positioning unclear, wrong clients | 2/5 |
| Team | Belief erosion, key person leaves | 1/5 |

### Step 4: Prioritise

Score each cluster:

```
Risk Score = Likelihood (1-5) × Impact (1-5)
```

| Cluster | Likelihood | Impact | Score | Priority |
|---------|------------|--------|-------|----------|
| Financial | 3 | 5 | 15 | HIGH |
| Capacity | 4 | 4 | 16 | CRITICAL |
| Market | 2 | 3 | 6 | LOW |
| Team | 2 | 5 | 10 | MEDIUM |

Focus on highest scores first.

### Step 5: De-Risk

For each high-priority cluster, create specific actions:

```text
FAILURE MODE: V>> Burnout (Capacity cluster, Score: 16)

De-Risking Actions:
1. [Immediate] Set max hours/week boundary
2. [This week] Identify 3 tasks to delegate to a teammate
3. [This month] Document processes for team replication
4. [Ongoing] Weekly capacity check-in

Owner: V>>
Review: Feb 24, 2026
```

### Step 6: Document

Store in `~/.claude/strat-ops/pre-mortems/{date}-{topic}.md`

---

## AskUserQuestion Flow

### Mode Selection

```text
Question: "Running pre-mortem. Solo or team?"
Header: "Mode"
Options:
- "Solo" - I'll write from multiple perspectives
- "Team async" - Each person writes separately
- "Team live" - Facilitate real-time session
```

### Solo Perspective Selection

```text
Question: "Which perspective first?"
Header: "Perspective"
Options:
- "As yourself" - Your natural concerns
- "As skeptic" - Your most critical advisor's view
- "As competitor" - Watching from outside
```

### Narrative Collection

```text
Question: "Ready to write your failure narrative?"
Header: "Write"
Options:
- "Start writing" - I'll share my narrative
- "Need prompt" - Give me the scenario again
- "Already written" - I'll paste it
```

---

## Output Format

```markdown
# Pre-Mortem: {Topic}

**Date**: 2026-01-24
**Participants**: V>>, A>>, K>>
**Time Horizon**: 6 months

## Failure Narratives

### V>>
> "{narrative}"

### A>>
> "{narrative}"

## Clustered Failure Modes

| Cluster | Failure Modes | Frequency | Score |
|---------|---------------|-----------|-------|
| {cluster} | {modes} | {freq} | {score} |

## De-Risking Actions

### {Cluster 1} (Score: {X})

| Action | Owner | Due | Status |
|--------|-------|-----|--------|
| {action} | {sig} | {date} | PENDING |

## Review Date

{date}

---

*Facilitated by GRIP - pre-mortem-facilitation skill*
```

---

---

## Duration

| Mode | Time |
|------|------|
| Solo (3 perspectives) | 30-45 minutes |
| Team (5 members) | 60-90 minutes |
| Clustering + de-risking | 30-45 minutes |
| **Total** | **1.5-2.5 hours** |

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~1,200 |
| Per perspective | ~300 |
| Clustering | ~400 |
| De-risking | ~500 |
| Documentation | ~300 |
| **Total** | **~2,700** |

---

## Related Skills

- `strategic-gap-analysis` - 7-Fit assessment (complementary)
- `red-teaming-strategic-decisions` - Assumption stress-testing
- `decision-locking` - Lock decisions after de-risking
- `strat-ops` - Strategy mode router

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial creation (promoted from strat-ops/modes) |

---


