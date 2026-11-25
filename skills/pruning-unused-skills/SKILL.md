---
name: pruning-unused-skills
description: Detects and prunes auto-generated skills with zero usage, preventing skill bloat from unchecked RSI generation. Use when running RSI cycles or when skill count exceeds useful threshold.
status: Active
effort: medium
auto_generated: true
generation_date: 2026-02-08T23:46:40Z
source_pattern: meta-unused_skills
version: 2.0.0
triggers:
  - ./optim.sh optimize detects unused_skills
  - skill count exceeds 100
  - /learn full RSI loop
integrates:
  - self-improvement-engine
  - skill-creation-best-practices
---

# Pruning Unused Skills

**Purpose:** Prevent skill bloat by detecting and pruning auto-generated skills that have zero usage after sufficient observation.

**Severity:** medium
**Token Impact:** 500-2000 tokens wasted per unused skill loaded into context

---

## Problem

The RSI pipeline (optim.sh) auto-generates skills when patterns hit the 3x YSH threshold. However, there is no feedback loop verifying these skills are actually used. Over time, this creates bloat:

- Skills directory grows unbounded
- CLAUDE.md skill registry expands
- Context window wastes tokens loading unused skill descriptions
- Signal-to-noise ratio degrades in skill discovery

### Metrics

From RSI analysis: 94 skill events tracked, but many generated skills show 0.00 usage rate. The system creates skills faster than they are consumed.

### Anti-Pattern

```text
# RSI cycle runs
./optim.sh detect          # Finds 7 patterns
./optim.sh auto-generate   # Generates 5 skills
# Next cycle...
./optim.sh detect          # Finds 6 patterns
./optim.sh auto-generate   # Generates 4 more skills
# Skills pile up, none are referenced in actual sessions
```

**Impact:** Each unused skill costs ~500 tokens when listed in skill catalogs and ~1000-2000 tokens if loaded. With 20 unused skills, that is 10k-40k tokens of waste per session.

---

## Solution

Implement a three-stage lifecycle for auto-generated skills: **Probation, Active, Pruned**.

### Stage 1: Probation (0-5 sessions)

New auto-generated skills enter probation:
- Tracked in metrics.jsonl with `skill_generated` event
- Must be triggered at least once within 5 sessions to graduate
- Probation skills are available but not promoted in catalogs

### Stage 2: Active (graduated)

Skills triggered at least once during probation become permanent:
- Full catalog listing
- Normal skill lifecycle applies

### Stage 3: Pruned (unused after probation)

Skills with zero triggers after 5 sessions:
- Move to `skills/.archive/` (not deleted, recoverable)
- Remove from CLAUDE.md registry
- Log `skill_pruned` event to metrics.jsonl

### Pruning Workflow

```text
- [ ] Run usage audit: check metrics.jsonl for skill trigger counts
- [ ] Identify skills with 0 triggers and age > 5 sessions
- [ ] Move to skills/.archive/ with timestamp
- [ ] Remove from CLAUDE.md skill registry
- [ ] Log pruning event to metrics.jsonl
```

---

## Detection

### Usage Audit Command

```bash
# Count skill triggers from metrics
cat ~/.claude/metrics.jsonl | jq -s '
  [.[] | select(.event == "skills_triggered") | .skills[]] |
  group_by(.) | map({skill: .[0], count: length}) |
  sort_by(.count)
'
```

### Identifying Candidates

```bash
# List all auto-generated skills
rg -l "auto_generated: true" ~/.claude/skills/*/SKILL.md

# Cross-reference with usage counts to find zero-trigger skills
```

### Thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| Trigger count = 0 after 5 sessions | Prune candidate | Move to .archive |
| Trigger count = 1-2 after 10 sessions | Low usage warning | Review for relevance |
| Trigger count > 3 | Healthy | No action |
| Total skill count > 120 | Bloat warning | Run full prune audit |

---

## Examples

### Before (Bloat)

```text
skills/ (96 directories)
├── auto-skill-1/    # 0 triggers in 8 sessions
├── auto-skill-2/    # 0 triggers in 12 sessions
├── auto-skill-3/    # 0 triggers in 6 sessions
├── markdown-expert/ # 47 triggers - healthy
├── concise-comm/    # 31 triggers - healthy
└── ...
```

### After (Pruned)

```text
skills/ (93 directories)
├── markdown-expert/ # 47 triggers
├── concise-comm/    # 31 triggers
└── ...

skills/.archive/
├── auto-skill-1/    # Pruned 2026-02-09, 0 triggers
├── auto-skill-2/    # Pruned 2026-02-09, 0 triggers
└── auto-skill-3/    # Pruned 2026-02-09, 0 triggers
```

---

## Implementation

This skill integrates with:

1. **self-improvement-engine** - `./optim.sh optimize` detects unused_skills meta-inefficiency
2. **metrics.jsonl** - Source of truth for skill trigger counts
3. **skill-creation-best-practices** - Gate B validates quality before generation

### Future: optim.sh Integration

Add `prune` subcommand to optim.sh:

```bash
./optim.sh prune          # Audit and prune unused skills
./optim.sh prune --dry-run # Show what would be pruned
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-02-09 | Rewritten with full content per skill-creation-best-practices |
| 1.0.0 | 2026-02-08 | Auto-generated stub from RSI meta-optimization |
